import requests
import json
import pandas as pd
from datetime import datetime, timedelta
import os
import sys
import time
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ==========================================
# CONFIGURATION - Loaded from environment
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Load .env file
def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value

load_env()

# Import name mappings
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, get_audit_date

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_ORG = os.environ.get('GITHUB_ORG', 'Pvragon')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
START_DATE_DT = pd.to_datetime(START_DATE_STR).tz_localize('America/Los_Angeles')

# Rolling window mode: by default fetch only last ROLLING_DAYS. FULL_REBUILD falls back
# to START_DATE (used after changing name_mappings/rules).
FULL_REBUILD = os.environ.get('FULL_REBUILD', 'false').lower() in ('true', '1', 'yes')
ROLLING_DAYS = int(os.environ.get('ROLLING_DAYS', '7'))
if FULL_REBUILD:
    FETCH_START_DT = START_DATE_DT
    print(f"[MODE] FULL_REBUILD — fetching commits from {START_DATE_STR}")
else:
    FETCH_START_DT = pd.Timestamp.now(tz='America/Los_Angeles') - timedelta(days=ROLLING_DAYS)
    print(f"[MODE] Rolling {ROLLING_DAYS}-day window — fetching commits from {FETCH_START_DT.strftime('%Y-%m-%d')}")

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def get_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=0.5)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    return session

session = get_session()

def get_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

def fetch_repos():
    print(f"[1/4] Fetching Repositories for org: {GITHUB_ORG}...")
    repos = []
    page = 1
    recent_count = 0
    while True:
        url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"
        resp = session.get(url, headers=get_headers(), params={"page": page, "per_page": 100}, timeout=30)
        if resp.status_code != 200: break
        data = resp.json()
        if not data: break
        for r in data:
            updated_at = pd.to_datetime(r['updated_at'])
            if updated_at.tzinfo is None:
                updated_at = updated_at.tz_localize('UTC')
            updated_at = updated_at.tz_convert('America/Los_Angeles')
            
            if updated_at >= FETCH_START_DT:
                repos.append({"full_name": r['full_name'], "name": r['name'], "owner": GITHUB_ORG})
                recent_count += 1
        page += 1
    print(f"  Found {len(repos)} organization repositories updated since {START_DATE_STR}.")
    return repos

def fetch_user_repos(username):
    """Fetch repositories for a specific user updated since target date."""
    print(f"  Fetching repos for user: {username}...")
    repos = []
    page = 1
    try:
        while True:
            url = f"https://api.github.com/users/{username}/repos"
            resp = session.get(url, headers=get_headers(), params={"page": page, "per_page": 100, "sort": "updated"}, timeout=20)
            if resp.status_code != 200: break
            data = resp.json()
            if not data: break
            
            for r in data:
                updated_at = pd.to_datetime(r['updated_at'])
                if updated_at.tzinfo is None:
                    updated_at = updated_at.tz_localize('UTC')
                updated_at = updated_at.tz_convert('America/Los_Angeles')
                
                if updated_at >= FETCH_START_DT:
                    repos.append({"full_name": r['full_name'], "name": r['name'], "owner": username})
                else: 
                    # Since we sort by updated desc, we can stop if we hit an old repo
                    # but only if page=1 and we have enough data. For safety, let's just break for the user.
                    break 
            
            if len(data) < 100: break
            page += 1
    except Exception as e:
        print(f"    - Error for {username}: {e}")
    return repos

def fetch_detailed_commits(repos):
    print(f"[2/4] Fetching Detailed Commits (Timestamps) for {len(repos)} repositories...")
    all_commits = []
    since_iso = FETCH_START_DT.isoformat()

    # Deduplicate repos by full_name
    seen_repos = set()
    unique_repos = []
    for r in repos:
        if r['full_name'] not in seen_repos:
            seen_repos.add(r['full_name'])
            unique_repos.append(r)

    for repo in unique_repos:
        full_name = repo['full_name']
        print(f"  - {full_name}...")
        page = 1
        while True:
            try:
                url = f"https://api.github.com/repos/{full_name}/commits"
                params = {"since": since_iso, "page": page, "per_page": 100}
                resp = session.get(url, headers=get_headers(), params=params, timeout=20)
                
                if resp.status_code != 200:
                    break
                
                commits = resp.json()
                if not commits: break
                
                for c in commits:
                    commit_data = c.get('commit', {})
                    author_data = commit_data.get('author', {})
                    author_date_raw = author_data.get('date')
                    if not author_date_raw: continue
                    
                    ts_utc = pd.to_datetime(author_date_raw)
                    ts_pst = ts_utc.tz_convert('America/Los_Angeles')
                    
                    author_name = author_data.get('name', 'Unknown')
                    github_login = c.get('author', {}).get('login') if c.get('author') else None
                    identifier = github_login or author_name
                    
                    all_commits.append({
                        "Identifier": identifier,
                        "Date": get_audit_date(ts_pst),
                        "Time": ts_pst.strftime('%I:%M %p'),
                        "timestamp": ts_pst,
                        "Repo": full_name,
                        "Hash": c.get('sha', '')[:10],
                        "Message": commit_data.get('message', '')[:100]
                    })
                
                if len(commits) < 100: break
                page += 1
                time.sleep(0.05)
            except Exception as e:
                print(f"      [Timeout/Error: skipping {full_name}]")
                break
            
    return all_commits

def process_and_upload_commits(commits):
    print("[3/4] Processing Commits...")
    if not commits:
        print("  No commits found.")
        return
        
    df = pd.DataFrame(commits)
    df['Name'] = df['Identifier'].apply(map_name)
    df = df[~df['Name'].apply(should_exclude)]
    df = df.sort_values(by='timestamp', ascending=False)
    
    df['Platform'] = "GitHub"
    df['Event Type'] = "Code Commit"
    df['Quantity'] = 1
    
    final_df = df[['Name', 'Date', 'Time', 'Platform', 'Repo', 'Event Type', 'Quantity', 'Hash', 'Message']]
    
    print(f"[4/4] Uploading {len(final_df)} commits to Google Sheet (Github_Commits)...")
    
    # Auth
    creds = None
    if os.path.exists('token.json'): creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
             secrets = [f for f in os.listdir('.') if 'client_secret' in f or 'credentials.json' in f]
             if not secrets: return
             flow = InstalledAppFlow.from_client_secrets_file(secrets[0], SCOPES)
             creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token: token.write(creds.to_json())

    gc = gspread.authorize(creds)
    try:
        sh = gc.open_by_key(SHEET_ID)
        tn = "Github_Commits"
        try: 
            ws = sh.worksheet(tn)
            existing_data = ws.get_all_records()
            df_old = pd.DataFrame(existing_data)
        except: 
            ws = sh.add_worksheet(tn, 10000, 10)
            df_old = pd.DataFrame(columns=['Name', 'Date', 'Time', 'Platform', 'Repo', 'Event Type', 'Quantity', 'Hash', 'Message'])
        
        # 1. Standardize columns
        STANDARD_COLS = ['Name', 'Date', 'Time', 'Platform', 'Repo', 'Event Type', 'Quantity', 'Hash', 'Message']
        for col in STANDARD_COLS:
            if col not in df_old.columns:
                df_old[col] = ''
        
        # 2. Merge by Hash. Rolling window: historical commit hashes naturally stay because
        # the fresh fetch only returns last N days; concat + dedup keeps historical + fresh.
        # FULL_REBUILD: overwrite entirely so excluded users/rules take effect.
        if FULL_REBUILD:
            combined = final_df[STANDARD_COLS]
            print(f"  [MERGE] FULL_REBUILD — overwriting with {len(final_df)} fresh rows")
        elif not df_old.empty:
            combined = pd.concat([df_old[STANDARD_COLS], final_df[STANDARD_COLS]]).drop_duplicates(subset=['Hash'], keep='last')
            print(f"  [MERGE] Rolling {ROLLING_DAYS}d — merged by Hash: {len(combined)} total rows")
        else:
            combined = final_df[STANDARD_COLS]

        # 3. Sort
        combined['sort_dt'] = pd.to_datetime(combined['Date'], format='%m/%d/%y', errors='coerce')
        combined = combined.sort_values(by=['sort_dt'], ascending=[False])
        
        # 4. BULLETPROOF CLEANING: Convert every value to a native Python type
        final_upload_df = combined[STANDARD_COLS].copy()
        rows_to_upload = []
        rows_to_upload.append(final_upload_df.columns.tolist()) # Add Header
        
        for _, row in final_upload_df.iterrows():
            clean_row = []
            for col in STANDARD_COLS:
                val = row[col]
                # Special handling for Quantity to ensure it's a native int
                if col == 'Quantity':
                    try:
                        clean_row.append(int(float(val)) if pd.notnull(val) and str(val) != '' else 1)
                    except:
                        clean_row.append(1)
                else:
                    # Convert everything else to string, handle nulls
                    clean_row.append(str(val) if pd.notnull(val) else "")
            rows_to_upload.append(clean_row)

        # 5. SAFETY CHECK: Only clear and update if we have actual data rows (more than just header)
        if len(rows_to_upload) > 1:
            print(f"  [CLEAN] Data sanitized. Uploading {len(rows_to_upload)-1} rows...")
            ws.clear()
            ws.update(values=rows_to_upload, range_name='A1')
            print(f"  [SUCCESS] Uploaded {len(rows_to_upload)-1} merged commits.")
        else:
            print("  [SKIP] No new or existing data to upload. Sheet preserved.")
            
    except Exception as e:
        print(f"  [ERROR] {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    from name_mappings import GITHUB_TEAM_HANDLES
    
    all_handles = GITHUB_TEAM_HANDLES

    all_repos = fetch_repos() # Org repos
    
    print(f"Scanning personal repos for {len(all_handles)} team members...")
    for handle in all_handles:
        user_repos = fetch_user_repos(handle)
        all_repos.extend(user_repos)

    commits = fetch_detailed_commits(all_repos)
    process_and_upload_commits(commits)

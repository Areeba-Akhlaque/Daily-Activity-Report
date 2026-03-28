import requests
import json
import pandas as pd
from datetime import datetime
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
# GitHub API uses ISO 8601 strings.
START_DATE_DT = datetime.strptime(START_DATE_STR, "%Y-%m-%d")
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

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
    while True:
        url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"
        resp = requests.get(url, headers=get_headers(), params={"page": page, "per_page": 100})
        if resp.status_code != 200:
            print(f"  Error fetching repos: {resp.status_code} {resp.text}")
            break
        data = resp.json()
        if not data: break
        repos.extend(data)
        page += 1
    print(f"  Found {len(repos)} repositories.")
    return repos

def fetch_events_for_repos(repos):
    print(f"[2/4] Fetching Events for {len(repos)} repositories...")
    all_events = []
    
    # Event mapping
    EVENT_MAP = {
        # PushEvent intentionally excluded - covered by Github_Commits tab
        "PullRequestEvent": "PR Opened/Closed",
        "PullRequestReviewEvent": "PR Reviewed", 
        "PullRequestReviewCommentEvent": "PR Comment Posted",
        "IssueCommentEvent": "Issue/PR Comment Posted",
        "IssuesEvent": "Issue Opened/Closed",
        "CreateEvent": "Branch/Tag Created",
        "DeleteEvent": "Branch/Tag Deleted"
    }

    last_event_time = {} # (User, Type) -> Last TS

    for repo in repos:
        repo_name = repo['name']
        print(f"  Fetching events for: {repo_name}...")
        page = 1
        repo_active = True
        while repo_active:
            url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/events"
            try:
                resp = requests.get(url, headers=get_headers(), params={"page": page, "per_page": 100}, timeout=20)
                if resp.status_code != 200: break
                events = resp.json()
            except Exception as e:
                print(f"    Timeout or error for {repo_name}: {e}")
                break
            
            if not events: break
            
            for ev in events:
                # Parse and convert to PST
                ts_utc = pd.to_datetime(ev['created_at'])
                ts_pst = ts_utc.tz_convert('America/Los_Angeles')
                
                if ts_pst < pd.to_datetime(START_DATE_STR).tz_localize('America/Los_Angeles'):
                    repo_active = False
                    break
                
                # Extract details
                actor = ev.get('actor', {}).get('login', 'Unknown')
                etype = ev.get('type')
                readable_type = EVENT_MAP.get(etype)
                
                if readable_type:
                    # INTEGRITY FIX: 60s cooldown per User/Type
                    key = (actor, readable_type)
                    ts_unix = ts_pst.timestamp()
                    if key in last_event_time:
                        if abs(ts_unix - last_event_time[key]) < 60:
                            continue
                    last_event_time[key] = ts_unix

                    all_events.append({
                        "User": actor,
                        "Date": get_audit_date(ts_pst), 
                        "timestamp": ts_pst,
                        "Event Type": readable_type
                    })
            
            page += 1
            if page > 10: break # GitHub usually only keeps 300 events or 90 days.
            time.sleep(0.1) # Small delay to be nice to API
            
    return all_events

def fetch_historical_search():
    """Use Search API to find PR/Issue events before the Event Buffer (approx 30 days).
    NOTE: Commits are intentionally excluded here — they are tracked in Github_Commits tab.
    Adding commits here would double-count and inflate numbers.
    """
    print("[1.5/4] Searching for historical PRs and Issues (Backfill)...")
    from name_mappings import GITHUB_TEAM_HANDLES
    
    historical_events = []
    since = START_DATE_STR  # '2026-01-01'
    
    for handle in GITHUB_TEAM_HANDLES:
        print(f"    Scanning {handle}...")
        # 1. PRs Created
        q_pr = f"author:{handle} org:{GITHUB_ORG} type:pr created:>={since}"
        r_pr = requests.get("https://api.github.com/search/issues", headers=get_headers(), params={"q": q_pr, "per_page": 100})
        if r_pr.status_code == 200:
            for item in r_pr.json().get('items', []):
                dt = pd.to_datetime(item['created_at']).tz_convert('America/Los_Angeles')
                historical_events.append({
                    "User": handle, "Date": get_audit_date(dt), "timestamp": dt, "Event Type": "PR Opened/Closed"
                })
        
        # 2. Issues Created
        q_is = f"author:{handle} org:{GITHUB_ORG} type:issue created:>={since}"
        r_is = requests.get("https://api.github.com/search/issues", headers=get_headers(), params={"q": q_is, "per_page": 100})
        if r_is.status_code == 200:
            for item in r_is.json().get('items', []):
                dt = pd.to_datetime(item['created_at']).tz_convert('America/Los_Angeles')
                historical_events.append({
                    "User": handle, "Date": get_audit_date(dt), "timestamp": dt, "Event Type": "Issue Opened/Closed"
                })

        time.sleep(2)  # Search API rate limits are stricter (30 calls/min)
    
    return historical_events

def process_and_upload(events):
    print("[3/4] Processing data...")
    if not events:
        print("  No events found.")
        return
        
    df = pd.DataFrame(events)
    # Map names
    df['Name'] = df['User'].apply(map_name)
    
    # Filter exclusions
    df = df[~df['Name'].apply(should_exclude)]
    
    # Aggregate
    summary = df.groupby(['Name', 'Date', 'Event Type']).size().reset_index(name='Quantity')
    summary['Platform'] = "GitHub"
    final_df = summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
    print(f"[4/4] Uploading {len(final_df)} aggregate rows to Google Sheet...")
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
        tn = "Github_Activity"
        try: 
            ws = sh.worksheet(tn)
            existing_data = ws.get_all_records()
            df_old = pd.DataFrame(existing_data)
        except: 
            ws = sh.add_worksheet(tn, 5000, 10)
            df_old = pd.DataFrame(columns=['Name', 'Date', 'Platform', 'Event Type', 'Quantity'])
        
        # IMPORTANT: Strip old 'Code Pushed' rows from existing data.
        # These were wrongly added in a previous run. Commits live in Github_Commits tab.
        ALLOWED_EVENT_TYPES = ['PR Opened/Closed', 'PR Reviewed', 'PR Comment Posted',
                               'Issue/PR Comment Posted', 'Issue Opened/Closed',
                               'Branch/Tag Created', 'Branch/Tag Deleted']
        
        # Merge
        if not df_old.empty:
            # Remove any old Code Pushed rows first
            df_old_clean = df_old[df_old['Event Type'].isin(ALLOWED_EVENT_TYPES)]
            combined = pd.concat([df_old_clean, final_df]).drop_duplicates(
                subset=['Name', 'Date', 'Event Type'], keep='last'
            )
        else:
            combined = final_df

        # Sort
        combined['sort_dt'] = pd.to_datetime(combined['Date'], format='%m/%d/%y')
        combined = combined.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
        # 3. BULLETPROOF CLEANING: Convert every value to a native Python type
        final_merged = combined[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']].copy()
        rows_to_upload = []
        rows_to_upload.append(final_merged.columns.tolist()) # Add Header
        
        for _, row in final_merged.iterrows():
            clean_row = []
            for col in final_merged.columns:
                val = row[col]
                if col == 'Quantity':
                    try:
                        clean_row.append(int(float(val)) if pd.notnull(val) and str(val) != '' else 1)
                    except:
                        clean_row.append(1)
                else:
                    clean_row.append(str(val) if pd.notnull(val) else "")
            rows_to_upload.append(clean_row)

        print(f"  [CLEAN] Data sanitized for JSON upload.")
        ws.clear()
        ws.update(values=rows_to_upload, range_name='A1')
        print(f"  [SUCCESS] Uploaded {len(rows_to_upload)-1} merged aggregate rows.")
    except Exception as e: 
        print(f"  [ERROR] {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    repos = fetch_repos()
    events = fetch_events_for_repos(repos)
    
    # Always attempt search backfill to capture historical PRs/Issues
    history = fetch_historical_search()
    events.extend(history)
    
    process_and_upload(events)

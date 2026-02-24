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
START_DATE_DT = pd.to_datetime(START_DATE_STR).tz_localize('America/Los_Angeles')

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

def fetch_detailed_commits(repos):
    print(f"[2/4] Fetching Detailed Commits (Timestamps) for {len(repos)} repositories...")
    all_commits = []
    
    # We look for commits since START_DATE
    since_iso = START_DATE_DT.isoformat()

    for repo in repos:
        repo_name = repo['name']
        print(f"  Fetching commits for: {repo_name}...")
        page = 1
        while True:
            url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/commits"
            params = {"since": since_iso, "page": page, "per_page": 100}
            resp = requests.get(url, headers=get_headers(), params=params)
            
            if resp.status_code != 200:
                break
            
            commits = resp.json()
            if not commits:
                break
            
            for c in commits:
                commit_data = c.get('commit', {})
                author_data = commit_data.get('author', {})
                
                # This is the "Actual" time the dev committed on their local machine
                author_date_raw = author_data.get('date')
                if not author_date_raw: continue
                
                ts_utc = pd.to_datetime(author_date_raw)
                ts_pst = ts_utc.tz_convert('America/Los_Angeles')
                
                author_name = author_data.get('name', 'Unknown')
                # Also check GitHub handle if available
                github_login = c.get('author', {}).get('login') if c.get('author') else None
                
                # We use specific handle or name
                identifier = github_login or author_name
                
                all_commits.append({
                    "Identifier": identifier,
                    "Date": get_audit_date(ts_pst),
                    "timestamp": ts_pst,
                    "Repo": repo_name,
                    "Message": commit_data.get('message', '')[:100]
                })
            
            if len(commits) < 100: break
            page += 1
            time.sleep(0.1)
            
    return all_commits

def process_and_upload_commits(commits):
    print("[3/4] Processing Commits...")
    if not commits:
        print("  No commits found.")
        return
        
    df = pd.DataFrame(commits)
    # Map names using our sophisticated name_mappings.py
    df['Name'] = df['Identifier'].apply(map_name)
    
    # Filter exclusions
    df = df[~df['Name'].apply(should_exclude)]
    
    # Aggregate by Name, Date, Message (to avoid counting identical commits if they appear somehow)
    summary = df.groupby(['Name', 'Date', 'Repo', 'Message']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Name'], ascending=[False, True])
    
    summary['Platform'] = "GitHub"
    summary['Event Type'] = "Code Commit"
    
    final_df = summary[['Name', 'Date', 'Platform', 'Repo', 'Event Type', 'Message']]
    
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
            ws.clear()
        except: 
            ws = sh.add_worksheet(tn, 5000, 10)
        
        ws.update(values=[final_df.columns.values.tolist()] + final_df.values.tolist(), range_name='A1')
        print(f"  [SUCCESS] Uploaded {len(final_df)} commits to Github_Commits tab.")
    except Exception as e: 
        print(f"  [ERROR] {e}")

if __name__ == "__main__":
    repos = fetch_repos()
    commits = fetch_detailed_commits(repos)
    process_and_upload_commits(commits)

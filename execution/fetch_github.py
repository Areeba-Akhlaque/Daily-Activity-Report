import requests
import json
import pandas as pd
from datetime import datetime
import os
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

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_ORG = os.environ.get('GITHUB_ORG', 'Pvragon')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
# GitHub API uses ISO 8601 strings.
START_DATE_DT = datetime.strptime(START_DATE_STR, "%Y-%m-%d")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

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
        "PushEvent": "Code Pushed",
        "PullRequestEvent": "PR Opened/Closed",
        "PullRequestReviewCommentEvent": "PR Comment Posted",
        "IssueCommentEvent": "Issue/PR Comment Posted",
        "IssuesEvent": "Issue Opened/Closed",
        "CreateEvent": "Branch/Tag Created",
        "DeleteEvent": "Branch/Tag Deleted"
    }

    for repo in repos:
        repo_name = repo['name']
        # Skip repos that haven't been updated since 2026
        updated_at = datetime.strptime(repo['updated_at'], "%Y-%m-%dT%H:%M:%SZ")
        if updated_at < START_DATE_DT:
            continue
            
        print(f"  Fetching events for: {repo_name}...")
        page = 1
        repo_active = True
        while repo_active:
            url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/events"
            resp = requests.get(url, headers=get_headers(), params={"page": page, "per_page": 100})
            if resp.status_code != 200: break
            events = resp.json()
            if not events: break
            
            for ev in events:
                created_dt = datetime.strptime(ev['created_at'], "%Y-%m-%dT%H:%M:%SZ")
                if created_dt < START_DATE_DT:
                    repo_active = False
                    break
                
                # Extract details
                actor = ev.get('actor', {}).get('login', 'Unknown')
                etype = ev.get('type')
                readable_type = EVENT_MAP.get(etype)
                
                if readable_type:
                    all_events.append({
                        "User": actor,
                        "Date": created_dt.strftime('%m/%d/%y'),
                        "timestamp": created_dt,
                        "Event Type": readable_type
                    })
            
            page += 1
            if page > 10: break # GitHub usually only keeps 300 events or 90 days.
            time.sleep(0.1) # Small delay to be nice to API
            
    return all_events

def process_and_upload(events):
    print("[3/4] Processing data...")
    if not events:
        print("  No events found.")
        return
        
    df = pd.DataFrame(events)
    # Aggregate
    summary = df.groupby(['User', 'Date', 'Event Type']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    
    summary['Platform'] = "GitHub"
    final_df = summary[['User', 'Date', 'Platform', 'Event Type', 'Quantity']]
    # Rename 'User' to 'Name' for consistency
    final_df = final_df.rename(columns={'User': 'Name'})
    
    print(f"[4/4] Uploading {len(final_df)} rows to Google Sheet...")
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
        try: ws = sh.worksheet(tn); ws.clear()
        except: ws = sh.add_worksheet(tn, 2000, 10)
        
        ws.update(values=[final_df.columns.values.tolist()], range_name='A1')
        ws.append_rows(final_df.values.tolist())
        print(f"  [SUCCESS] Uploaded {len(final_df)} aggregate rows.")
    except Exception as e: print(f"  [ERROR] {e}")

if __name__ == "__main__":
    repos = fetch_repos()
    events = fetch_events_for_repos(repos)
    process_and_upload(events)

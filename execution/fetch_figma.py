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

FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN', '')
FIGMA_TEAM_ID = os.environ.get('FIGMA_TEAM_ID', '')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
START_DATE_DT = datetime.strptime(START_DATE_STR, "%Y-%m-%d")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def get_headers():
    return {
        "X-Figma-Token": FIGMA_TOKEN
    }

def fetch_projects():
    print(f"[1/4] Fetching Projects for team: {FIGMA_TEAM_ID}...")
    url = f"https://api.figma.com/v1/teams/{FIGMA_TEAM_ID}/projects"
    resp = requests.get(url, headers=get_headers())
    if resp.status_code != 200:
        print(f"  Error fetching projects: {resp.status_code} {resp.text}")
        return []
    data = resp.json().get('projects', [])
    print(f"  Found {len(data)} projects.")
    return data

def fetch_files_for_projects(projects):
    print(f"[2/4] Fetching Files, Comments & Versions...")
    all_events = []
    
    for proj in projects:
        pid = proj['id']
        pname = proj['name']
        print(f"  Checking project: {pname}...")
        
        url = f"https://api.figma.com/v1/projects/{pid}/files"
        resp = requests.get(url, headers=get_headers())
        if resp.status_code != 200: continue
        
        files = resp.json().get('files', [])
        for f in files:
            fkey = f['key']
            fname = f['name']
            
            # 1. Fetch comments
            print(f"    Fetching activity for: {fname}...")
            c_url = f"https://api.figma.com/v1/files/{fkey}/comments"
            c_resp = requests.get(c_url, headers=get_headers())
            if c_resp.status_code == 200:
                comments = c_resp.json().get('comments', [])
                for c in comments:
                    if 'created_at' not in c: continue
                    created_dt = pd.to_datetime(c['created_at']).tz_localize(None)
                    if created_dt >= START_DATE_DT:
                        user_name = c.get('user', {}).get('handle', 'Unknown')
                        all_events.append({
                            "Name": user_name, "Date": created_dt.strftime('%m/%d/%y'),
                            "Event Type": "Comment Posted", "Platform": "Figma"
                        })

            # 2. Fetch versions (as "File Updated" events)
            v_url = f"https://api.figma.com/v1/files/{fkey}/versions"
            v_resp = requests.get(v_url, headers=get_headers())
            if v_resp.status_code == 200:
                versions = v_resp.json().get('versions', [])
                for v in versions:
                    if 'created_at' not in v: continue
                    v_dt = pd.to_datetime(v['created_at']).tz_localize(None)
                    if v_dt >= START_DATE_DT:
                        user_name = v.get('user', {}).get('handle', 'Unknown')
                        # Filter out system-level autosaves labeled as 'Figma'
                        if user_name.lower() != 'figma':
                            all_events.append({
                                "Name": user_name, "Date": v_dt.strftime('%m/%d/%y'),
                                "Event Type": "File Updated", "Platform": "Figma"
                            })
            
            time.sleep(1) # More generous rate limit for versions + comments
            
    return all_events

def process_and_upload(events):
    print("[3/4] Processing data...")
    if not events:
        print("  No Figma events (comments) found since 2026.")
        return
        
    df = pd.DataFrame(events)
    # Aggregate
    summary = df.groupby(['Name', 'Date', 'Event Type', 'Platform']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    
    final_df = summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
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
        tn = "Figma_Activity"
        try: ws = sh.worksheet(tn); ws.clear()
        except: ws = sh.add_worksheet(tn, 1000, 10)
        
        ws.update(values=[final_df.columns.values.tolist()], range_name='A1')
        ws.append_rows(final_df.values.tolist())
        print(f"  [SUCCESS] Uploaded {len(final_df)} aggregate rows.")
    except Exception as e: print(f"  [ERROR] {e}")

if __name__ == "__main__":
    projects = fetch_projects()
    events = fetch_files_for_projects(projects)
    process_and_upload(events)

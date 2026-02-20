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

FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN', '')
FIGMA_TEAM_ID = os.environ.get('FIGMA_TEAM_ID', '')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
START_DATE_DT = pd.to_datetime(START_DATE_STR).tz_localize('America/Los_Angeles')
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

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
                    created_dt = pd.to_datetime(c['created_at']).tz_convert('America/Los_Angeles')
                    if created_dt >= START_DATE_DT:
                        user_name = c.get('user', {}).get('handle', 'Unknown')
                        all_events.append({
                            "Name": user_name, "Date": created_dt.strftime('%m/%d/%y'),
                            "timestamp": created_dt,
                            "Event Type": "Comment Posted", "Platform": "Figma"
                        })
                        pass

            # 2. Fetch versions (as "File Edited" events) - Paginated to find all users
            v_page_token = None
            all_versions = []
            while True:
                v_url = f"https://api.figma.com/v1/files/{fkey}/versions"
                v_params = {'page_token': v_page_token} if v_page_token else {}
                v_resp = requests.get(v_url, headers=get_headers(), params=v_params)
                if v_resp.status_code != 200: break
                
                v_data = v_resp.json()
                batch = v_data.get('versions', [])
                if not batch: break
                
                all_versions.extend(batch)
                
                # Check if we should stop (oldest in batch is before START_DATE_DT)
                oldest_in_batch_dt = pd.to_datetime(batch[-1]['created_at']).tz_convert('America/Los_Angeles')
                if oldest_in_batch_dt < START_DATE_DT:
                    break
                
                v_page_token = v_data.get('pagination', {}).get('next_page_token')
                if not v_page_token: break
                time.sleep(0.5)

            if all_versions:
                # Process Creation (Oldest Version across all pages)
                oldest_v = all_versions[-1]
                if 'created_at' in oldest_v:
                     v_dt = pd.to_datetime(oldest_v['created_at']).tz_convert('America/Los_Angeles')
                     if v_dt >= START_DATE_DT:
                         user = oldest_v.get('user', {}).get('handle', 'Unknown')
                         if user.lower() != 'figma':
                             all_events.append({
                                 "Name": user, "Date": get_audit_date(v_dt),
                                 "timestamp": v_dt,
                                 "Event Type": "File Created", "Platform": "Figma"
                             })

                # Process Edits (All versions except oldest)
                seen_v_hashes = set() # Dedup if pagination overlaps
                for v in all_versions[:-1]:
                    if 'created_at' not in v or not v.get('id'): continue
                    if v['id'] in seen_v_hashes: continue
                    seen_v_hashes.add(v['id'])
                    
                    v_dt = pd.to_datetime(v['created_at']).tz_convert('America/Los_Angeles')
                    
                    if v_dt >= START_DATE_DT:
                        user = v.get('user', {}).get('handle', 'Unknown')
                        if user.lower() != 'figma':
                             all_events.append({
                                 "Name": user, "Date": get_audit_date(v_dt),
                                 "timestamp": v_dt,
                                 "Event Type": "File Edited", "Platform": "Figma"
                             })
                    else:
                        break # We are going backwards in time
            time.sleep(1) # More generous rate limit for versions + comments
            
    return all_events

def process_and_upload(events):
    print("[3/4] Processing data...")
    if not events:
        print("  No Figma events (comments) found since 2026.")
        return
        
    df = pd.DataFrame(events)
    print(f"  Raw users found: {df['Name'].unique().tolist()}")
    # Map names
    df['Name'] = df['Name'].apply(map_name)
    
    # Filter exclusions
    df = df[~df['Name'].apply(should_exclude)]
    
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

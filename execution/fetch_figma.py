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

def get_with_retry(url, headers, params=None):
    """Fetch with automated retry for 429 errors."""
    for i in range(3):
        r = requests.get(url, headers=headers, params=params)
        if r.status_code == 429:
            wait = (i + 1) * 5
            print(f"      - [429] Rate limit hit. Waiting {wait}s...")
            time.sleep(wait)
            continue
        return r
    return r

def get_headers():
    return {
        "X-Figma-Token": FIGMA_TOKEN
    }

def fetch_all_activity():
    print(f"[{get_audit_date(datetime.now())}*** STARTING FIGMA FETCH")
    all_events = []
    
    # 1. Get Projects
    print("[1/4] Fetching Projects for team: " + FIGMA_TEAM_ID + "...")
    p_url = f"https://api.figma.com/v1/teams/{FIGMA_TEAM_ID}/projects"
    p_resp = get_with_retry(p_url, get_headers())
    if p_resp.status_code != 200:
        print(f"  [ERROR*** Projects API: {p_resp.status_code}")
        return []
        
    projects = p_resp.json().get('projects', [])
    print(f"  Found {len(projects)} projects.")
    
    for p in projects:
        p_id = p['id']
        p_name = p['name']
        print(f"  Checking project: {p_name}...")
        
        # Get Files in Project
        f_url = f"https://api.figma.com/v1/projects/{p_id}/files"
        f_resp = get_with_retry(f_url, get_headers())
        if f_resp.status_code != 200: continue
        
        files = f_resp.json().get('files', [])
        for f in files:
            fkey = f['key']
            fname = f['name']
            
            # 1. Fetch comments
            print(f"    Fetching activity for: {fname}...")
            c_url = f"https://api.figma.com/v1/files/{fkey}/comments"
            c_resp = get_with_retry(c_url, get_headers())
            file_commentators = set()
            if c_resp.status_code == 200:
                comments = c_resp.json().get('comments', [])
                for c in comments:
                    if 'created_at' not in c: continue
                    created_dt = pd.to_datetime(c['created_at']).tz_convert('America/Los_Angeles')
                    if created_dt >= START_DATE_DT:
                        user_name = c.get('user', {}).get('handle', 'Unknown')
                        file_commentators.add(user_name)
                        all_events.append({
                            "Name": user_name, "Date": created_dt.strftime('%m/%d/%y'),
                            "timestamp": created_dt,
                            "Event Type": "Comment Posted", "Platform": "Figma"
                        })
                if file_commentators:
                    print(f"      - Found commentators: {list(file_commentators)}")

            # 2. Fetch versions (as "File Edited" events) - Paginated
            v_page_token = None
            v_page_count = 0
            all_versions = []
            while v_page_count < 10: 
                v_url = f"https://api.figma.com/v1/files/{fkey}/versions"
                v_params = {'page_size': 100}
                if v_page_token: v_params['page_token'] = v_page_token
                
                v_resp = get_with_retry(v_url, get_headers(), params=v_params)
                if v_resp.status_code != 200: 
                    print(f"      - Error fetching versions (Page {v_page_count+1}): {v_resp.status_code}")
                    break
                
                v_data = v_resp.json()
                batch = v_data.get('versions', [])
                if not batch: break
                
                all_versions.extend(batch)
                v_page_count += 1
                
                oldest_in_batch_dt = pd.to_datetime(batch[-1]['created_at']).tz_convert('America/Los_Angeles')
                if oldest_in_batch_dt < START_DATE_DT:
                    break
                
                v_page_token = v_data.get('pagination', {}).get('next_page_token')
                if not v_page_token: break
                time.sleep(1.0) # Slower for GH Actions

            if all_versions:
                # Diagnostics: List all unique users found in this file
                file_users = set(v.get('user', {}).get('handle', 'Unknown') for v in all_versions)
                print(f"      - Found {len(all_versions)} versions. Authors: {list(file_users)}")

                all_versions_sorted = sorted(all_versions, key=lambda x: x['created_at'])
                oldest_v = all_versions_sorted[0]
                
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

                seen_v_stamps = set() 
                for v in all_versions:
                    if 'created_at' not in v: continue
                    if v.get('id') == oldest_v.get('id'): continue 
                    v_dt = pd.to_datetime(v['created_at']).tz_convert('America/Los_Angeles')
                    if v_dt >= START_DATE_DT:
                        user = v.get('user', {}).get('handle', 'Unknown')
                        v_key = (user, v_dt.strftime('%Y-%m-%d %H:%M'))
                        if v_key in seen_v_stamps: continue
                        seen_v_stamps.add(v_key)
                        
                        if user.lower() != 'figma':
                             all_events.append({
                                 "Name": user, "Date": get_audit_date(v_dt),
                                 "timestamp": v_dt,
                                 "Event Type": "File Edited", "Platform": "Figma"
                             })
            time.sleep(1.5) 
    return all_events

def process_and_upload(events):
    print("[3/4] Processing data...")
    if not events:
        print("  No NEW Figma events found.")
        return
        
    df_new = pd.DataFrame(events)
    # Map names
    df_new['Name'] = df_new['Name'].apply(map_name)
    # Filter exclusions
    df_new = df_new[~df_new['Name'].apply(should_exclude)]
    
    # Simple aggregation for the new batch
    summary_new = df_new.groupby(['Name', 'Date', 'Event Type', 'Platform']).size().reset_index(name='Quantity')

    # Auth for Google Sheets
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
    sh = gc.open_by_key(SHEET_ID)
    ws_name = "Figma_Activity"
    try: 
        ws = sh.worksheet(ws_name)
        existing_data = ws.get_all_records()
        df_old = pd.DataFrame(existing_data)
    except: 
        ws = sh.add_worksheet(ws_name, 5000, 10)
        df_old = pd.DataFrame(columns=['Name', 'Date', 'Platform', 'Event Type', 'Quantity'])

    final_summary = summary_new

    # Sort final
    final_summary['sort_dt'] = pd.to_datetime(final_summary['Date'], format='%m/%d/%y')
    final_summary = final_summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    final_df = final_summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
    print(f"[4/4] Uploading {len(final_df)} merged rows to Google Sheet...")
    ws.clear()
    ws.update(values=[final_df.columns.values.tolist()] + final_df.values.tolist(), range_name='A1')
    print("  [SUCCESS*** Figma activity synced.")

if __name__ == "__main__":
    events = fetch_all_activity()
    process_and_upload(events)

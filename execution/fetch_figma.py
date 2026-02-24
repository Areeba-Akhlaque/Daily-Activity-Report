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

            # 2. Fetch versions (as "File Edited" events) - Paginated to find all users
            # 2. Fetch versions (as "File Edited" events) - Paginated to find all users
            v_page_token = None
            v_page_count = 0
            all_versions = []
            while v_page_count < 10: # Fetch up to 1000 versions (10 pages of 100)
                v_url = f"https://api.figma.com/v1/files/{fkey}/versions"
                v_params = {'page_size': 100}
                if v_page_token: v_params['page_token'] = v_page_token
                
                v_resp = requests.get(v_url, headers=get_headers(), params=v_params)
                if v_resp.status_code != 200: 
                    print(f"      - Error fetching versions (Page {v_page_count+1}): {v_resp.status_code}")
                    break
                
                v_data = v_resp.json()
                batch = v_data.get('versions', [])
                if not batch: break
                
                all_versions.extend(batch)
                v_page_count += 1
                
                # Check if the oldest in this batch is before START_DATE_DT
                # Figma versions are returned newest first.
                oldest_in_batch_dt = pd.to_datetime(batch[-1]['created_at']).tz_convert('America/Los_Angeles')
                
                if oldest_in_batch_dt < START_DATE_DT:
                    # We've reached past our target date
                    break
                
                v_page_token = v_data.get('pagination', {}).get('next_page_token')
                if not v_page_token: break
                time.sleep(0.5)

            if all_versions:
                # Diagnostics: List all unique users found in this file
                file_users = set(v.get('user', {}).get('handle', 'Unknown') for v in all_versions)
                print(f"      - Found {len(all_versions)} versions. Authors: {list(file_users)}")

                # Process Creation (Oldest Version across all pages)
                # Note: We sort by time descending, so last item is oldest
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

                # Process Edits (Any version after creation within range)
                seen_v_stamps = set() 
                for v in all_versions:
                    if 'created_at' not in v: continue
                    if v.get('id') == oldest_v.get('id'): continue 
                    
                    v_dt = pd.to_datetime(v['created_at']).tz_convert('America/Los_Angeles')
                    
                    if v_dt >= START_DATE_DT:
                        user = v.get('user', {}).get('handle', 'Unknown')
                        # Deduplicate multiple hits on same minute/user to avoid noise
                        v_key = (user, v_dt.strftime('%Y-%m-%d %H:%M'))
                        if v_key in seen_v_stamps: continue
                        seen_v_stamps.add(v_key)
                        
                        if user.lower() != 'figma':
                             all_events.append({
                                 "Name": user, "Date": get_audit_date(v_dt),
                                 "timestamp": v_dt,
                                 "Event Type": "File Edited", "Platform": "Figma"
                             })
            time.sleep(1) 
            
    return all_events

def process_and_upload(events):
    print("[3/4] Processing data...")
    if not events:
        print("  No Figma events found since 2026.")
        return
        
    df = pd.DataFrame(events)
    raw_handles = df['Name'].unique().tolist()
    print(f"  Raw handles detected: {raw_handles}")
    
    # Map names
    df['Name'] = df['Name'].apply(map_name)
    
    # Detailed Trace: Who was found and who was mapped?
    for handle in raw_handles:
        mapped = map_name(handle)
        excluded = should_exclude(mapped)
        print(f"    - Trace: '{handle}' -> mapped to '{mapped}' (Excluded: {excluded})")

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

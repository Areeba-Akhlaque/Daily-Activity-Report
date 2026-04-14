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

FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN', '')
FIGMA_TEAM_ID = os.environ.get('FIGMA_TEAM_ID', '')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
START_DATE_DT = pd.to_datetime(START_DATE_STR).tz_localize('America/Los_Angeles')

# Rolling window mode: by default fetch only last ROLLING_DAYS; FULL_REBUILD=true
# falls back to fetching from START_DATE (used after changing name_mappings/rules).
FULL_REBUILD = os.environ.get('FULL_REBUILD', 'false').lower() in ('true', '1', 'yes')
ROLLING_DAYS = int(os.environ.get('ROLLING_DAYS', '7'))
if FULL_REBUILD:
    FETCH_START_DT = START_DATE_DT
    print(f"[MODE] FULL_REBUILD — fetching from {START_DATE_STR}")
else:
    FETCH_START_DT = pd.Timestamp.now(tz='America/Los_Angeles') - timedelta(days=ROLLING_DAYS)
    print(f"[MODE] Rolling {ROLLING_DAYS}-day window — fetching from {FETCH_START_DT.strftime('%Y-%m-%d')}")
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
    print(f"[{get_audit_date(datetime.now())}] *** STARTING FIGMA FETCH")
    all_events = []
    
    # 1. Get Projects
    print("[1/4] Fetching Projects for team: " + FIGMA_TEAM_ID + "...")
    p_url = f"https://api.figma.com/v1/teams/{FIGMA_TEAM_ID}/projects"
    p_resp = get_with_retry(p_url, get_headers())
    if p_resp.status_code != 200:
        print(f"  [ERROR] Projects API: {p_resp.status_code}")
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
                    if created_dt >= FETCH_START_DT:
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
                if oldest_in_batch_dt < FETCH_START_DT:
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
                     if v_dt >= FETCH_START_DT:
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
                    if v_dt >= FETCH_START_DT:
                        user = v.get('user', {}).get('handle', 'Unknown')
                        v_key = (user, v_dt.strftime('%Y-%m-%d %H:') + f'{(v_dt.minute // 15) * 15:02d}')
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

    # Merge new data with existing sheet data.
    # FULL_REBUILD: fresh fetch is authoritative for everything → overwrite sheet entirely.
    # Rolling:      fresh fetch is authoritative ONLY for the last N days → preserve
    #               rows older than the window untouched, drop old rows within the window
    #               to avoid duplicates, then append the fresh window rows.
    if FULL_REBUILD:
        combined = summary_new
        print(f"  [MERGE] FULL_REBUILD — overwriting with {len(summary_new)} fresh rows")
    elif not df_old.empty:
        for col in ['Name', 'Date', 'Platform', 'Event Type', 'Quantity']:
            if col not in df_old.columns:
                df_old[col] = ''
        df_old = df_old[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']].copy()
        df_old['_dt'] = pd.to_datetime(df_old['Date'], format='%m/%d/%y', errors='coerce')
        window_start_naive = FETCH_START_DT.tz_localize(None).normalize()
        historical = df_old[df_old['_dt'].notna() & (df_old['_dt'] < window_start_naive)]
        historical = historical.drop(columns=['_dt'])
        combined = pd.concat([historical, summary_new], ignore_index=True)
        print(f"  [MERGE] Rolling {ROLLING_DAYS}d — {len(historical)} historical + {len(summary_new)} fresh = {len(combined)}")
    else:
        combined = summary_new
        print(f"  [MERGE] No existing sheet — writing {len(summary_new)} fresh rows")
    
    # Ensure Quantity is int
    combined['Quantity'] = pd.to_numeric(combined['Quantity'], errors='coerce').fillna(1).astype(int)

    # 3. BULLETPROOF CLEANING: Convert every value to a native Python type
    final_df = combined[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']].copy()
    rows_to_upload = []
    rows_to_upload.append(final_df.columns.tolist()) # Add Header
    
    for _, row in final_df.iterrows():
        clean_row = []
        for col in final_df.columns:
            val = row[col]
            if col == 'Quantity':
                try:
                    clean_row.append(int(float(val)) if pd.notnull(val) and str(val) != '' else 1)
                except:
                    clean_row.append(1)
            else:
                clean_row.append(str(val) if pd.notnull(val) else "")
        rows_to_upload.append(clean_row)

    # 4. SAFETY CHECK: Only clear and update if we have actual data rows (more than just header)
    if len(rows_to_upload) > 1:
        print(f"  [CLEAN] Data sanitized. Uploading {len(rows_to_upload)-1} Figma rows...")
        ws.clear()
        ws.update(values=rows_to_upload, range_name='A1')
        print("  [SUCCESS] Figma activity synced.")
    else:
        print("  [SKIP] No new or existing Figma data to upload. Sheet preserved.")

def save_figma_cache(events):
    """Save raw timestamped Figma events for Activity Time Analysis to avoid double API calls.

    Rolling window mode: merges freshly-fetched last-N-days events with existing cache
    (keeps pre-window historical events so Activity Time Analysis stays complete).
    FULL_REBUILD mode: overwrites cache with the full fetch.
    """
    try:
        cache_rows_new = [
            {'name': e.get('Name', ''), 'timestamp': e['timestamp'].isoformat(), 'app': 'Figma'}
            for e in events if e.get('timestamp') and e.get('Name')
        ]
        cache_path = os.path.join(ROOT_DIR, 'dashboard', 'figma_events_cache.json')
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        if FULL_REBUILD:
            final_rows = cache_rows_new
        else:
            existing = []
            if os.path.exists(cache_path):
                try:
                    with open(cache_path) as f:
                        existing = json.load(f) or []
                except Exception:
                    existing = []
            window_start_iso = FETCH_START_DT.isoformat()
            preserved = [r for r in existing if r.get('timestamp', '') < window_start_iso]
            merged = preserved + cache_rows_new
            seen = set()
            final_rows = []
            for r in merged:
                key = (r.get('name', ''), r.get('timestamp', ''))
                if key in seen:
                    continue
                seen.add(key)
                final_rows.append(r)
            print(f"  [CACHE] Preserved {len(preserved)} historical + {len(cache_rows_new)} fresh = {len(final_rows)} total")

        with open(cache_path, 'w') as f:
            json.dump(final_rows, f)
        print(f"  [CACHE] Saved {len(final_rows)} Figma events to cache")
    except Exception as ce:
        print(f"  [CACHE WARN] {ce}")

if __name__ == "__main__":
    events = fetch_all_activity()
    save_figma_cache(events)
    process_and_upload(events)

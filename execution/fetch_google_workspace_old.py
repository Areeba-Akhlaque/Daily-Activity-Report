import requests
import json
import pandas as pd
from datetime import datetime, timezone
import os
import sys
import time
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from concurrent.futures import ThreadPoolExecutor, as_completed

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
from name_mappings import map_name, should_exclude, NAME_MAP

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01') + "T00:00:00Z"
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

def get_creds():
    creds = None
    token_path = os.path.join(SCRIPT_DIR, 'token.json') # Ensure absolute path to token
    if not os.path.exists(token_path):
         # Try parent directory if running from execution
         token_path = os.path.join(ROOT_DIR, 'token.json')
         
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing token...")
            try:
                creds.refresh(Request())
            except Exception:
                print("Refresh failed. Re-initiating login...")
                creds = None
        
        if not creds:
            print("Initiating new login flow...")
            # Look for credentials.json in current or parent dirs
            secrets = [f for f in os.listdir(SCRIPT_DIR) if 'client_secret' in f or 'credentials.json' in f.lower()]
            if not secrets:
                 secrets = [os.path.join(ROOT_DIR, f) for f in os.listdir(ROOT_DIR) if 'client_secret' in f or 'credentials.json' in f.lower()]
            
            if not secrets:
                # Last resort fallback
                params_creds = os.path.join(ROOT_DIR, 'credentials.json')
                if os.path.exists(params_creds):
                    secrets = [params_creds]
                else: 
                    print("ERROR: No credentials.json found.")
                    return None
            
            # Use run_local_server for interactive login
            flow = InstalledAppFlow.from_client_secrets_file(secrets[0], SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    return creds

def fetch_window(url, creds, start_dt, end_dt, application_name):
    # Ensure token is valid before request
    if creds.expired:
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"    [Token Refresh Failed] {e}")
            return []

    headers = {"Authorization": f"Bearer {creds.token}"}

    start_str = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    events_in_window = []
    
    # We fetch ALL events (params['eventName'] is purposefully omitted to allow 'delivery' + others)
    # and filter locally.
    params = {
        "startTime": start_str,
        "endTime": end_str,
        "maxResults": 1000
    }
    
    try:
        while True:
            # Check expiry inside pagination loop too
            if creds.expired:
                 creds.refresh(Request())
                 headers = {"Authorization": f"Bearer {creds.token}"}

            resp = requests.get(url, headers=headers, params=params, timeout=60) # Increased timeout
            
            if resp.status_code == 401:
                # 401 Unauthorized - Token might be invalid despite check
                print("    [401] Token invalid, refreshing and retrying...")
                creds.refresh(Request())
                headers = {"Authorization": f"Bearer {creds.token}"}
                resp = requests.get(url, headers=headers, params=params, timeout=60)

            # Retry logic for 500/503 errors
            if resp.status_code in [500, 502, 503, 504]:
                print(f"    [{resp.status_code}] Google Backend Error. Retrying in 5s...")
                time.sleep(5)
                resp = requests.get(url, headers=headers, params=params, timeout=60)
            
            if resp.status_code != 200:
                print(f"    [Error {resp.status_code}] {resp.text}")
                break
            
            data = resp.json()
            items = data.get('items', [])
            for item in items:
                actor_email = item.get('actor', {}).get('email', '')
                if not actor_email: continue
                
                timestamp = item.get('id', {}).get('time', '')
                
                events = item.get('events', [])
                for ev in events:
                    event_name = ev.get('name', '')
                    keep_event = False
                    mapped_type = ""
                    
                    if application_name == 'gmail':
                        # Documentation: eventName is 'delivery', type is in params
                        # But we also assume explicit 'send' might imply old behavior
                        
                        if event_name == 'send':
                            keep_event = True
                            mapped_type = "Gmail Send"
                        
                        elif event_name == 'delivery':
                            # Parse parameters for mail_event_type
                            # Possible values: 1=Sent, 2=Received
                            params_list = ev.get('parameters', [])
                            mail_event_type = None
                            
                            for p in params_list:
                                # Docs say parameter is 'event_info.mail_event_type' inside 'event_info'?
                                # Or a parameter named 'mail_event_type'?
                                # We check robustly.
                                p_name = p.get('name', '')
                                if 'mail_event_type' in p_name: # catch "event_info.mail_event_type"
                                    try:
                                        mail_event_type = int(p.get('intValue', -1))
                                    except:
                                        pass
                                
                                # Sometimes it's nested in a parameter named 'event_info'?
                                # API structure usually: "parameters": [{"name": "event_info", "messageValue": {...}}]?
                                # But let's rely on flattened names if available.
                                # If API returns flattened names (checked docs): 
                                # "name": "event_info.mail_event_type", "intValue": "1"
                                
                            if mail_event_type == 1:
                                keep_event = True
                                mapped_type = "Gmail Send"
                            # elif mail_event_type == 2:
                            #     keep_event = True
                            #     mapped_type = "Gmail Receive"
                            
                    elif application_name == 'drive':
                        # Local Filter: Content edits
                        if event_name in ['edit', 'create']:
                            keep_event = True
                            mapped_type = f"Drive {event_name.capitalize()}"

                    if keep_event:
                        display_name = map_name(actor_email)
                        try:
                             dt = pd.to_datetime(timestamp)
                        except:
                             continue
                        
                        events_in_window.append({
                            "Name": display_name,
                            "Date": dt.strftime('%m/%d/%y'),
                            "timestamp_dt": dt,
                            "Platform": "Google Workspace",
                            "Event Type": mapped_type,
                            "Quantity": 1
                        })

            next_token = data.get('nextPageToken')
            if not next_token: break
            params['pageToken'] = next_token
    except Exception as e:
        print(f"    [Window Exception] {e}")

    return events_in_window

def fetch_audit_logs(creds, application_name):
    print(f"Starting optimized parallel fetch for: {application_name}")
    all_events = []
    
    start_dt = pd.to_datetime(START_DATE_STR)
    now_dt = datetime.now(timezone.utc)
    
    windows = []
    curr = start_dt
    while curr < now_dt:
        nxt = curr + pd.Timedelta(days=30)
        windows.append((curr, nxt if nxt < now_dt else now_dt))
        curr = nxt

    # Determine targets (Users)
    targets = []
    if application_name == 'gmail':
        print("  [Info] Fetching Gmail logs for each user individually (skipping 'all' due to API limitations)...")
        for email in NAME_MAP.keys():
            if '@pvragon.com' in email and not should_exclude(email):
                targets.append(email)
    else:
        # Drive works with 'all'
        targets = ['all']

    tasks = []
    # Reduce workers to 1 (Sequential) to prevent 500 Errors
    with ThreadPoolExecutor(max_workers=1) as executor:
        for target_user in targets:
            print(f"    - Fetching: {target_user}")
            url = f"https://admin.googleapis.com/admin/reports/v1/activity/users/{target_user}/applications/{application_name}"
            for w in windows:
                # Pass 'creds' object instead of fixed headers for token refresh
                tasks.append(executor.submit(fetch_window, url, creds, w[0], w[1], application_name))
        
        for f in as_completed(tasks):
            all_events.extend(f.result())
            
    return all_events

def process_and_upload(events):
    print(f"[3/4] Processing and Uploading {len(events)} events...")
    if not events:
        print("  No audit events found.")
        # We still upload empty if needed, or return? 
        # Better to not wipe sheet if empty?
        # But if validly 0 events (e.g. no activity), we should clear?
        # User wants to SEE data.
        return

    df = pd.DataFrame(events)
    # Map names
    df['Name'] = df['Name'].apply(map_name)
    
    # Filter exclusions
    df = df[~df['Name'].apply(should_exclude)]
    
    # Aggregate daily
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    
    final_df = summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
    # Ensure SHEET_ID is correct
    creds = get_creds() # Refresh creds for gspread too
    gc = gspread.authorize(creds)
    try:
        sh = gc.open_by_key(SHEET_ID)
        tab_name = "GoogleWorkspace_Activity"
        try:
            ws = sh.worksheet(tab_name)
            ws.clear()
        except:
            ws = sh.add_worksheet(title=tab_name, rows=2000, cols=10)
        
        ws.update(values=[final_df.columns.values.tolist()], range_name='A1')
        ws.append_rows(final_df.values.tolist())
        print(f"  [SUCCESS] Uploaded {len(final_df)} aggregate rows.")
    except Exception as e:
        print(f"  [ERROR] Upload: {e}")

if __name__ == "__main__":
    creds = get_creds()
    if creds:
        # print("Testing token refresh capability...")
        # if creds.expired and creds.refresh_token:
        #     creds.refresh(Request())
        
        print(f"Authentication Successful. Valid until: {creds.expiry}")
        
        # Gmail first (Prioritize fix verification)
        gmail_events = fetch_audit_logs(creds, 'gmail')
        
        # Drive next
        drive_events = fetch_audit_logs(creds, 'drive')
        
        process_and_upload(drive_events + gmail_events)

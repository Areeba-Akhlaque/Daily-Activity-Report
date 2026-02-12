import os
import sys
import time
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==========================================
# UNIFIED GOOGLE WORKSPACE FETCH SCRIPT
# ==========================================
# Combines:
# 1. Robust Auth (Interactive + Refresh)
# 2. Official Google API Client (Better handling)
# 3. Correct Event Logic (Delivery + Mail Event Type)
# 4. Sequential/Safe Fetching to avoid 500 Errors
# ==========================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Load helper modules
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, NAME_MAP

# Configuration
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

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

def get_creds():
    """Get valid credentials, refreshing or prompting as needed."""
    creds = None
    token_path = os.path.join(SCRIPT_DIR, 'token.json')
    if not os.path.exists(token_path):
        token_path = os.path.join(ROOT_DIR, 'token.json')

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            print("[Auth] Token file corrupt/invalid.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[Auth] Refreshing expired token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Auth] Refresh failed: {e}")
                creds = None

        if not creds:
            print("[Auth] Initiating new login flow...")
            secrets = [f for f in os.listdir(SCRIPT_DIR) if 'client_secret' in f or 'credentials.json' in f.lower()]
            if not secrets:
                 secrets = [os.path.join(ROOT_DIR, f) for f in os.listdir(ROOT_DIR) if 'client_secret' in f or 'credentials.json' in f.lower()]
            
            if not secrets:
                # Last resort
                params_creds = os.path.join(ROOT_DIR, 'credentials.json')
                if os.path.exists(params_creds):
                    secrets = [params_creds]
            
            if not secrets:
                print("[Auth] ERROR: No credentials.json found.")
                return None
            
            flow = InstalledAppFlow.from_client_secrets_file(secrets[0], SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save valid token
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    return creds

def fetch_logs_in_windows(service, user_key, application_name, start_dt, end_dt):
    """Fetch logs respecting 30-day API limit."""
    all_events = []
    
    # Generate 30-day windows
    windows = []
    curr = start_dt
    while curr < end_dt:
        nxt = curr + timedelta(days=29) # Safe margin < 30
        windows.append((curr, nxt if nxt < end_dt else end_dt))
        curr = nxt
        
    for w_start, w_end in windows:
        # print(f"    - Window: {w_start.strftime('%m-%d')} to {w_end.strftime('%m-%d')}")
        start_str = w_start.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        end_str = w_end.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        
        events = fetch_logs_for_user(service, user_key, application_name, start_str, end_str)
        all_events.extend(events)
        
    return all_events

def fetch_logs_for_user(service, user_key, application_name, start_time, end_time):
    """Fetch logs for a specific user using official client (single window)."""
    events = []
    page_token = None
    
    params = {
        'userKey': user_key,
        'applicationName': application_name,
        'startTime': start_time,
        'endTime': end_time,
        'maxResults': 1000
    }
    
    while True:
        try:
            if page_token:
                params['pageToken'] = page_token
                
            response = service.activities().list(**params).execute()
            items = response.get('items', [])
            
            for item in items:
                actor_email = item.get('actor', {}).get('email', '')
                timestamp = item.get('id', {}).get('time', '')
                
                for ev in item.get('events', []):
                    event_name = ev.get('name', '')
                    keep = False
                    mapped_type = ""
                    
                    if application_name == 'gmail':
                        if event_name == 'send':
                            keep = True
                            mapped_type = "Gmail Send"
                        # Explicitly excluding 'delivery' events as they are passive/received
                        # and user wants strictly 'Send' activity only.
                                
                    elif application_name == 'drive':
                         if event_name in ['edit', 'create', 'upload']:
                             keep = True
                             mapped_type = f"Drive {event_name.capitalize()}"
                    
                    if keep and actor_email:
                        try:
                            dt = pd.to_datetime(timestamp)
                            events.append({
                                "Name": map_name(actor_email),
                                "Date": dt.strftime('%m/%d/%y'),
                                "timestamp_dt": dt,
                                "Platform": "Google Workspace",
                                "Event Type": mapped_type,
                                "Quantity": 1
                            })
                        except: pass

            page_token = response.get('nextPageToken')
            if not page_token:
                break
                
        except HttpError as e:
            if e.resp.status in [403, 429, 500, 502, 503]:
                error_body = e.content.decode('utf-8') if e.content else "No content"
                print(f"    [Retry] API Error {e.resp.status}. Detail: {error_body}. Waiting 10s...")
                time.sleep(10)
                continue
            else:
                # print(f"    [Error] {e}") # specific window error shouldn't crash all
                break
        except Exception as e:
            # print(f"    [Error] {e}")
            break
            
    return events

def fetch_all_google_workspace(creds):
    print("="*60)
    print("STARTING UNIFIED GOOGLE WORKSPACE FETCH")
    print("="*60)
    
    service = build('admin', 'reports_v1', credentials=creds)
    all_events = []
    
    start_dt = pd.to_datetime(START_DATE_STR).replace(tzinfo=timezone.utc)
    end_dt = datetime.now(timezone.utc)
    
    # 1. GMAIL FETCH (Unified 'all' scan)
    print(f"\\n[Gmail] Scanning 'all' users ({start_dt.strftime('%Y-%m-%d')} - {end_dt.strftime('%Y-%m-%d')})...")
    try:
        # Fetching for 'all' users is more robust and mirrors generate_activity_time.py success
        events = fetch_logs_in_windows(service, 'all', 'gmail', start_dt, end_dt)
        print(f"  Found {len(events)} Gmail events.")
        all_events.extend(events)
    except Exception as e:
        print(f"  [Gmail Error] {e}")
            
    # 2. DRIVE FETCH 
    print(f"\n[Drive] Scanning 'all' users...")
    try:
        events = fetch_logs_in_windows(service, 'all', 'drive', start_dt, end_dt)
        print(f"  Found {len(events)} Drive events.")
        all_events.extend(events)
    except Exception as e:
        print(f"  [Drive Error] {e}")

    return all_events

def process_and_upload(events):
    print(f"\n[Upload] Processing {len(events)} total events...")
    if not events:
        print("  No events to upload.")
        return

    df = pd.DataFrame(events)
    # Ensure mapping is clean
    df['Name'] = df['Name'].apply(map_name)
    df = df[~df['Name'].apply(should_exclude)]
    
    # Aggregate
    if df.empty:
        print("  No events left after mapping/exclusion.")
        return
        
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    
    final_df = summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
    # Upload
    creds = get_creds()
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
        print(f"  [SUCCESS] Uploaded {len(final_df)} aggregate rows to '{tab_name}'.")
    except Exception as e:
        print(f"  [ERROR] Upload failed: {e}")

if __name__ == "__main__":
    creds = get_creds()
    if creds:
        events = fetch_all_google_workspace(creds)
        process_and_upload(events)

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
from name_mappings import map_name, should_exclude

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01') + "T00:00:00Z"
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

# Domains to EXCLUDE from Gmail Received count (auto-generated/marketing emails)
EXCLUDED_SENDER_DOMAINS = [
    'github.com',
    'mail.instagram.com',
    'mg.upwork.com',
    'shopify.com',
    'att.net',
    'notifications3.mailchimp.com',
    'bf05x.hubspotemail.net',
    'notifications2.mailchimp.com',
    'cioeu109333.lovable.dev',
    'eu-west-1.amazonses.com',
    'mail128-67.atl41.mandrillapp.com',
    'notifications4.mailchimp.com',
    'triplewhale.com',
    'geopod-ismtpd-0',
    'sailthru.com',
    'gsemail.gainsightapp.com',
    'geopod-ismtpd-canary-0',
    # Partial matches (will check if domain ends with these)
    'mailchimp.com',
    'hubspotemail.net',
    'amazonses.com',
    'mandrillapp.com',
]

def get_creds():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing token...")
            creds.refresh(Request())
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        else:
            print("No valid credentials found. Please ensure token.json is correct.")
            return None
    return creds

from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_window(url, headers, start_dt, end_dt, application_name):
    start_str = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"  [Parallel] Scanning {application_name}: {start_str} to {end_str}")
    
    events_in_window = []
    params = {
        "startTime": start_str,
        "endTime": end_str,
        "maxResults": 1000
    }
    
    # CRITICAL FIX FROM USER GUIDE: Use eventName='send' directly for Gmail
    if application_name == 'gmail':
        params['eventName'] = 'send'
    
    try:
        while True:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                print(f"    [Error {resp.status_code}] {resp.text}")
                break
            
            data = resp.json()
            items = data.get('items', [])
            for item in items:
                actor_email = item.get('actor', {}).get('email', '')
                if not actor_email: continue
                
                if not actor_email: continue
                
                timestamp = item.get('id', {}).get('time', '')
                
                # If we are strictly fetching 'send' events (as per guide), 
                # we don't need to iterate through item['events'] to filter.
                # The API already filtered for us via eventName='send'.
                # However, the structure is still:
                events = item.get('events', [])
                for ev in events:
                    # Double check event name just in case, or just take the valid one
                    event_name = ev.get('name', '')
                    
                    keep_event = False
                    mapped_event = f"{application_name.capitalize()} {event_name}"
                    
                    if application_name == 'drive':
                        # Strict Filter: Only content modifications
                        if event_name in ['edit', 'create']:
                            keep_event = True
                    
                    elif application_name == 'gmail':
                        # Guide Method: We specifically requested eventName='send'.
                        # So any event returned IS a send event.
                        # We label it nicely.
                        if event_name == 'send':
                           keep_event = True
                           mapped_event = "Gmail Send"

                    if keep_event:
                        dt = pd.to_datetime(timestamp)
                        
                        # Apply mapping immediately to ensure consistency
                        display_name = map_name(actor_email)
                        
                        events_in_window.append({
                            "Name": display_name,
                            "Date": dt.strftime('%m/%d/%y'),
                            "timestamp_dt": dt,
                            "Platform": "Google Workspace",
                            "Event Type": mapped_event,
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
    url = f"https://admin.googleapis.com/admin/reports/v1/activity/users/all/applications/{application_name}"
    headers = {"Authorization": f"Bearer {creds.token}"}
    
    start_dt = pd.to_datetime(START_DATE_STR)
    now_dt = datetime.now(timezone.utc)
    
    windows = []
    curr = start_dt
    while curr < now_dt:
        nxt = curr + pd.Timedelta(days=30)
        windows.append((curr, nxt if nxt < now_dt else now_dt))
        curr = nxt

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_window, url, headers, w[0], w[1], application_name) for w in windows]
        for f in as_completed(futures):
            all_events.extend(f.result())
            
    return all_events

def process_and_upload(events):
    print("[3/4] Processing and Uploading...")
    if not events:
        print("  No audit events found.")
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
        print(f"  [SUCCESS] Uploaded {len(final_df)} aggregate rows.")
    except Exception as e:
        print(f"  [ERROR] Upload: {e}")

if __name__ == "__main__":
    creds = get_creds()
    if creds:
        drive_events = fetch_audit_logs(creds, 'drive')
        gmail_events = fetch_audit_logs(creds, 'gmail')
        process_and_upload(drive_events + gmail_events)

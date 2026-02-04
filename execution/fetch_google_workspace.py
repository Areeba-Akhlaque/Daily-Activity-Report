import requests
import json
import pandas as pd
from datetime import datetime, timezone
import os
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

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
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

def fetch_audit_logs(creds, application_name):
    print(f"Fetching audit logs for: {application_name}...")
    all_events = []
    url = f"https://admin.googleapis.com/admin/reports/v1/activity/users/all/applications/{application_name}"
    headers = {"Authorization": f"Bearer {creds.token}"}
    
    # Define time windows (30 days max each)
    start_dt = pd.to_datetime(START_DATE_STR)
    now_dt = datetime.now(timezone.utc)
    
    current_start = start_dt
    while current_start < now_dt:
        current_end = current_start + pd.Timedelta(days=30)
        if current_end > now_dt:
            current_end = now_dt
            
        start_str = current_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        end_str = current_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        print(f"  Scanning window: {start_str} to {end_str}")
        
        params = {
            "startTime": start_str,
            "endTime": end_str,
            "maxResults": 1000
        }
        
        try:
            while True:
                resp = requests.get(url, headers=headers, params=params)
                if resp.status_code != 200:
                    print(f"    Error {resp.status_code}: {resp.text}")
                    break
                
                data = resp.json()
                items = data.get('items', [])
                for item in items:
                    actor = item.get('actor', {})
                    actor_email = actor.get('email', '')
                    caller_type = actor.get('callerType', '')
                    
                    # Skip system-triggered events (no human actor)
                    if caller_type == 'KEY' or not actor_email:
                        continue
                    
                    timestamp = item.get('id', {}).get('time', '')
                    events = item.get('events', [])
                    
                    for ev in events:
                        event_name = ev.get('name', '')
                        
                        # Filtering based on user requirements
                        keep_event = False
                        mapped_event = f"{application_name.capitalize()} {event_name}"
                        
                        if application_name == 'drive':
                            if event_name in ['edit', 'create', 'upload', 'rename']:
                                keep_event = True
                        elif application_name == 'gmail':
                            if event_name == 'delivery':
                                # Extract sender domain from message parameters
                                sender_domain = ''
                                for p in ev.get('parameters', []):
                                    if p.get('name') == 'message_info':
                                        msg_params = p.get('messageValue', {}).get('parameter', [])
                                        for mp in msg_params:
                                            if mp.get('name') == 'rfc2822_message_id':
                                                mid = mp.get('value', '')
                                                if '@' in mid:
                                                    sender_domain = mid.split('@')[-1].rstrip('>')
                                                break
                                        break
                                
                                # Check if sender domain should be excluded
                                is_excluded = False
                                for excl in EXCLUDED_SENDER_DOMAINS:
                                    if sender_domain == excl or sender_domain.endswith('.' + excl) or sender_domain.endswith(excl):
                                        is_excluded = True
                                        break
                                
                                if not is_excluded:
                                    keep_event = True
                                    mapped_event = "Gmail Received"
                            elif event_name == 'send':
                                keep_event = True
                                mapped_event = "Gmail Send"
                        
                        if keep_event:
                            dt = pd.to_datetime(timestamp)
                            all_events.append({
                                "Name": actor_email,
                                "Date": dt.strftime('%m/%d/%y'),
                                "timestamp_dt": dt,
                                "Platform": "Google Workspace",
                                "Event Type": mapped_event,
                                "Quantity": 1
                            })
                            
                if len(all_events) % 500 == 0 and items:
                    print(f"    Current total events found: {len(all_events)}...")
                
                next_token = data.get('nextPageToken')
                if not next_token:
                    break
                params['pageToken'] = next_token
        except Exception as e:
            print(f"    Exception: {e}")
            break
            
        current_start = current_end
        
    return all_events

def process_and_upload(events):
    print("[3/4] Processing and Uploading...")
    if not events:
        print("  No audit events found.")
        return

    df = pd.DataFrame(events)
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

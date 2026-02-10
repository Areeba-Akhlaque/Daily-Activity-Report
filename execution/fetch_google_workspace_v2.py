import requests
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import os
import sys
import time
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ==========================================
# CONFIGURATION
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

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

sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2025-11-01')
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

def get_creds():
    creds = None
    token_path = os.path.join(ROOT_DIR, 'token.json')
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing token...")
            creds.refresh(Request())
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
        else:
            print("No valid credentials found.")
            return None
    return creds

def fetch_gmail_send_events():
    """RAW DISCOVERY MODE: Fetch and print ALL events from last 7 days"""
    print("="*60)
    print("[DISCOVERY MODE] Fetching ALL Gmail events (Last 7 Days)")
    print("="*60)
    
    creds = get_creds()
    if not creds: return []
    
    service = build('admin', 'reports_v1', credentials=creds)
    
    # Just check last 7 days to be fast
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=7)
    
    start_time = start_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    end_time = end_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    
    all_events = []
    unique_event_names = set()
    
    try:
        response = service.activities().list(
            userKey='all',
            applicationName='gmail',
            startTime=start_time,
            endTime=end_time,
            maxResults=100
        ).execute()
        
        activities = response.get('items', [])
        print(f"Total Raw Activities Found: {len(activities)}")
        
        for activity in activities:
            actor = activity.get('actor', {}).get('email', 'Unknown')
            events = activity.get('events', [])
            for event in events:
                name = event.get('name')
                
                if name == 'delivery':
                    params = event.get('parameters', [])
                    # Convert list of dicts to a simpler dict for viewing
                    param_dict = {p['name']: p.get('value') or p.get('multiValue') for p in params}
                    
                    if len(all_events) < 5:  # Inspect first 5 delivery events
                        print("\n" + "-"*40)
                        print(f"Actor: {actor}")
                        print(f"Event: {name}")
                        print(f"Parameters: {json.dumps(param_dict, indent=2)}")
                
                unique_event_names.add(name)
                all_events.append({"Name": actor, "Event": name})
                
    except Exception as e:
        print(f"[ERROR] {e}")
        
    print("\n" + "="*60)
    print("UNIQUE EVENT NAMES FOUND IN YOUR ACCOUNT:")
    for name in unique_event_names:
        print(f"- {name}")
    print("="*60)
    
    return all_events

def fetch_drive_events():
    """Fetch Drive events"""
    print("[Drive] Fetching edit/create events...")
    
    creds = get_creds()
    if not creds:
        return []
    
    service = build('admin', 'reports_v1', credentials=creds)
    
    start_date = datetime.strptime(START_DATE_STR, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)
    
    start_time = start_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    end_time = end_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    
    all_events = []
    next_page_token = None
    
    try:
        while True:
            response = service.activities().list(
                userKey='all',
                applicationName='drive',
                startTime=start_time,
                endTime=end_time,
                maxResults=1000,
                pageToken=next_page_token
            ).execute()
            
            activities = response.get('items', [])
            
            for activity in activities:
                actor_email = activity.get('actor', {}).get('email', '').lower()
                timestamp = activity.get('id', {}).get('time', '')
                events = activity.get('events', [])
                
                for event in events:
                    event_name = event.get('name', '')
                    
                    if event_name in ['edit', 'create', 'upload', 'rename']:
                        dt = pd.to_datetime(timestamp)
                        all_events.append({
                            "Name": map_name(actor_email),
                            "Date": dt.strftime('%m/%d/%y'),
                            "Platform": "Google Workspace",
                            "Event Type": f"Drive {event_name.capitalize()}",
                            "Quantity": 1
                        })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
                
    except Exception as e:
        print(f"  [ERROR] {e}")
    
    print(f"[Drive] Total events found: {len(all_events)}")
    return all_events

def process_and_upload(events):
    print("[Upload] Processing and uploading...")
    if not events:
        print("  No events found.")
        return

    df = pd.DataFrame(events)
    df['Name'] = df['Name'].apply(map_name)
    df = df[~df['Name'].apply(should_exclude)]
    
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
    print("=" * 60)
    print("GOOGLE WORKSPACE AUDIT - Using Guide's Exact Method")
    print("=" * 60)
    
    gmail_events = fetch_gmail_send_events()
    drive_events = fetch_drive_events()
    
    all_events = gmail_events + drive_events
    process_and_upload(all_events)
    
    print("\nDone!")

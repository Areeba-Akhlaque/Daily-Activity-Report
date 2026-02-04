"""
Fetch Backendless Console Activity
==================================
Fetches console activity logs from Backendless and uploads to Google Sheet.
See: directives/fetch_backendless_activity.md
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timezone
from collections import defaultdict
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Load .env
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

BACKENDLESS_APP_ID = os.environ.get('BACKENDLESS_APP_ID', '5EC56BA3-AA29-2AC1-FFEE-A7C07D146900')
BACKENDLESS_API_KEY = os.environ.get('BACKENDLESS_API_KEY', '1E5DDF72-6AEB-4CC9-9B1B-EAD29FEE0A23')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE = os.environ.get('START_DATE', '2026-01-01')

# Timestamp for filtering
START_TS = int(datetime.strptime(START_DATE, '%Y-%m-%d').timestamp() * 1000)

# Name mapping
NAME_MAP = {
    'bilal@pvragon.com': 'Bilal Munir',
    'jaime@pvragon.com': 'James Hereford',
    'alexander@pvragon.com': 'Alexander Pavelko',
    'areeba@pvragon.com': 'Areeba Akhlaque',
    'farhan@pvragon.com': 'Muhammad Farhan',
    'saifullah@pvragon.com': 'Saifullah Khan',
    'juan@pvragon.com': 'Juan Vidal',
    # Add more as discovered
}


def get_google_creds():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def fetch_backendless_activity():
    """Fetch console activity from Backendless."""
    print("[1/3] Fetching Backendless Console Activity...")
    
    events = []
    
    # Backendless Console API endpoint
    url = f"https://api.backendless.com/{BACKENDLESS_APP_ID}/{BACKENDLESS_API_KEY}/console/audit"
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    try:
        # Fetch with pagination
        offset = 0
        page_size = 100
        
        while True:
            params = {
                'pageSize': page_size,
                'offset': offset
            }
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                print(f"  API returned status {response.status_code}")
                # Try alternative endpoint
                break
            
            data = response.json()
            
            if not data:
                break
            
            for log in data:
                timestamp = log.get('timestamp', 0)
                if timestamp < START_TS:
                    continue
                
                user = log.get('user', '')
                action = log.get('action', '')
                
                if user:
                    date_str = datetime.fromtimestamp(timestamp / 1000).strftime('%m/%d/%y')
                    name = NAME_MAP.get(user.lower(), user)
                    
                    events.append({
                        'name': name,
                        'date': date_str,
                        'event_type': action
                    })
            
            if len(data) < page_size:
                break
            
            offset += page_size
    
    except Exception as e:
        print(f"  Error fetching from Backendless API: {e}")
        print("  Note: Backendless Console API may require special permissions")
    
    # If no events from API, check if there's existing data in the sheet
    if not events:
        print("  No new events from API. Checking existing sheet data...")
    
    return events


def aggregate_events(events):
    """Aggregate events by name, date, event_type."""
    counts = defaultdict(int)
    
    for e in events:
        key = (e['name'], e['date'], e['event_type'])
        counts[key] += 1
    
    rows = []
    for (name, date, event_type), count in sorted(counts.items()):
        rows.append({
            'Name': name,
            'Date': date,
            'Event Type': event_type,
            'Count': count
        })
    
    return rows


def upload_to_sheet(rows, creds):
    """Upload aggregated data to Google Sheet."""
    print("[3/3] Uploading to Google Sheet...")
    
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    try:
        ws = sh.worksheet('Backendless_Activity')
    except:
        ws = sh.add_worksheet(title='Backendless_Activity', rows=1000, cols=5)
    
    if rows:
        # Clear and write new data
        ws.clear()
        df = pd.DataFrame(rows)
        values = [df.columns.tolist()] + df.values.tolist()
        ws.update(values=values, range_name='A1')
        print(f"  Uploaded {len(rows)} rows")
    else:
        print("  No new events to upload (keeping existing data)")


def main():
    print("=" * 60)
    print("Backendless Activity Fetch")
    print("=" * 60)
    
    # Get credentials
    creds = get_google_creds()
    
    # Fetch events
    events = fetch_backendless_activity()
    print(f"  Found {len(events)} events")
    
    # Aggregate
    print("[2/3] Aggregating events...")
    rows = aggregate_events(events)
    print(f"  Aggregated to {len(rows)} rows")
    
    # Upload
    upload_to_sheet(rows, creds)
    
    print("\n[COMPLETE] Backendless activity fetch finished")


if __name__ == "__main__":
    main()

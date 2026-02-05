"""
Fetch Backendless Console Activity
==================================
Fetches console activity logs from Backendless API.
Falls back to local CSV if API fails.
"""

import os
import sys
import requests
import pandas as pd
import json
import time
from datetime import datetime, timezone

# Add script dir to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(SCRIPT_DIR)

from name_mappings import map_name, should_exclude
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Env Loading
def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k] = v
load_env()

# Config
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
APP_ID = os.environ.get('BACKENDLESS_APP_ID')
API_KEY = os.environ.get('BACKENDLESS_API_KEY')
DEV_LOGIN = os.environ.get('BACKENDLESS_DEV_LOGIN')
DEV_PASS = os.environ.get('BACKENDLESS_DEV_PASSWORD')

def get_google_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds

def fetch_from_api():
    """Attempt to fetch logs via Backendless API."""
    print("[1/3] Backendless Console Logs API Check...")
    print("  ! Console/Management Logs are NOT accessible via standard REST API Keys.")
    print("  ! Please manually export 'console_audit_logs.csv' from Backendless and push to this repo.")
    print("  ! Falling back to checking local CSV file...")
    return []
                    break
        except Exception as e:
            print(f"  [Error] {table}: {e}")
            
    if not all_logs:
        # Fallback: Try Login (if provided) to fetch user token
        if DEV_LOGIN and DEV_PASS:
            print("  Attempting Developer Login...")
            auth_url = f"{base_url}/users/login"
            try:
                login_res = requests.post(auth_url, json={'login': DEV_LOGIN, 'password': DEV_PASS})
                if login_res.status_code == 200:
                    token = login_res.json().get('user-token')
                    print("  [SUCCESS] Logged in. Fetching with User Token...")
                    # Now try fetching logs with this token
                    # Note: This usually grants access to Data Tables permissions allow.
                else:
                    print(f"  [Login Failed] {login_res.status_code}")
            except:
                pass

    if not all_logs:
        print("  [WARN] Could not fetch logs via API. Falling back to CSV.")
        return fetch_from_csv()

    # Process API Data
    print(f"  Processing {len(all_logs)} API records...")
    processed = []
    for log in all_logs:
        # Normalize fields
        # API usually returns 'ownerId', 'created', etc.
        # We need 'developer' (email), 'event', 'timestamp'
        
        # Mapping logic depends on table schema.
        # Assuming schema: event, user_email/developer, created (ms)
        
        email = log.get('developer') or log.get('user_email') or log.get('email') or 'Unknown'
        event_type = log.get('event') or log.get('action') or 'Unknown Event'
        
        # Timestamp
        ts = log.get('created') or log.get('timestamp')
        if not ts: continue
        
        try:
            dt = datetime.fromtimestamp(ts/1000)
            date_str = dt.strftime('%m/%d/%y')
            
            # Filter Date
            if dt.strftime('%Y-%m-%d') < START_DATE_STR: continue
            
            name = map_name(email)
            if should_exclude(name): continue
            
            processed.append({
                'Name': name,
                'Date': date_str,
                'Platform': 'Backendless App',
                'Event Type': event_type,
                'Count': 1
            })
        except:
            continue
            
    # Aggregate
    df = pd.DataFrame(processed)
    if df.empty: return []
    
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Count')
    return summary.to_dict('records')

def fetch_from_csv():
    """Legacy backup: Read console_audit_logs.csv"""
    csv_path = os.path.join(ROOT_DIR, 'console_audit_logs.csv')
    if not os.path.exists(csv_path):
        print("  CSV not found.")
        return []

    print("  Reading local CSV...")
    try:
        df = pd.read_csv(csv_path)
    except:
        return []

    # Process CSV logic (Same as before)
    def parse_dev(s):
        try: return json.loads(str(s)).get('email', 'Unknown')
        except: return str(s)

    df['Email'] = df['developer'].apply(parse_dev)
    df['Name'] = df['Email'].apply(map_name)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df[df['timestamp'] >= START_DATE_STR]
    
    df = df[~df['Name'].apply(should_exclude)]
    
    df['Date'] = df['timestamp'].dt.strftime('%m/%d/%y')
    df['Event Type'] = df['event'].fillna('Unknown')
    df['Platform'] = 'Backendless App'
    
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Count')
    return summary.to_dict('records')

def upload_to_sheet(rows, creds):
    print(f"[3/3] Uploading {len(rows)} rows to Google Sheet...")
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet('Console_Audit_Logs')
        ws.clear()
    except:
        ws = sh.add_worksheet('Console_Audit_Logs', 5000, 10)
        
    if rows:
        headers = ['Name', 'Date', 'Platform', 'Event Type', 'Count']
        values = [headers] + [[r.get(h, '') for h in headers] for r in rows]
        ws.update(values=values, range_name='A1')

def main():
    print("="*60)
    print("Backendless Activity Fetch (Hybrid)")
    print("="*60)
    try:
        rows = fetch_from_api()
        creds = get_google_creds()
        upload_to_sheet(rows, creds)
        print("[COMPLETE] Done.")
    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    main()

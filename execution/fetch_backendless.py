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
import json
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

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE = os.environ.get('START_DATE', '2026-01-01')

# Backendless Console credentials
CONSOLE_HOST = os.environ.get('BACKENDLESS_CONSOLE_HOST', 'https://console.okridecare.com')
APP_ID = os.environ.get('BACKENDLESS_APP_ID', 'DF9C4AEE-7CAC-4014-8293-8D706579495A')
DEV_LOGIN = os.environ.get('BACKENDLESS_DEV_LOGIN', '')
DEV_PASSWORD = os.environ.get('BACKENDLESS_DEV_PASSWORD', '')

# Import name mappings
try:
    from name_mappings import map_name, should_exclude
except ImportError:
    sys.path.insert(0, SCRIPT_DIR)
    from name_mappings import map_name, should_exclude

def get_google_creds():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds




def fetch_and_process_csv():
    """Load from console_audit_logs.csv if it exists."""
    csv_path = os.path.join(ROOT_DIR, 'console_audit_logs.csv')
    
    if not os.path.exists(csv_path):
        print(f"  CSV not found: {csv_path}")
        return []
    
    df = pd.read_csv(csv_path)
    print(f"  Loaded {len(df)} records from CSV")
    
    # Parse developer column to get email
    def get_email(dev_str):
        try:
            if pd.isna(dev_str):
                return 'Unknown'
            dev = json.loads(str(dev_str))
            return dev.get('email', 'Unknown')
        except:
            return 'Unknown'
    
    df['Email'] = df['developer'].apply(get_email)
    df['Name'] = df['Email'].apply(map_name)
    
    # Filter exclusions
    df = df[~df['Name'].apply(should_exclude)]
    df = df[~df['Email'].apply(should_exclude)]
    
    # Filter for start date
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df[df['timestamp'] >= START_DATE]
    
    # Map columns
    df['Date'] = df['timestamp'].dt.strftime('%m/%d/%y')
    df['Event Type'] = df['event'].fillna('Unknown')
    df['Platform'] = 'Backendless App'
    
    # Aggregate
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Count')
    
    # Sort by date DESCENDING (newest first)
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Name'], ascending=[False, True])
    summary = summary.drop(columns=['sort_dt'])
    
    return summary[['Name', 'Date', 'Platform', 'Event Type', 'Count']].to_dict('records')


def upload_to_sheet(rows, creds):
    """Upload aggregated data to Google Sheet."""
    print("[3/3] Uploading to Google Sheet...")
    
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    try:
        ws = sh.worksheet('Console_Audit_Logs')
        ws.clear()
    except:
        ws = sh.add_worksheet(title='Console_Audit_Logs', rows=5000, cols=10)
    
    if rows:
        headers = ['Name', 'Date', 'Platform', 'Event Type', 'Count']
        values = [headers] + [[r[h] for h in headers] for r in rows]
        ws.update(values=values, range_name='A1')
        print(f"  Uploaded {len(rows)} rows (sorted by newest date first)")
    else:
        print("  No data to upload")


def main():
    print("=" * 60)
    print("Backendless Activity Fetch")
    print("=" * 60)
    
    # Get credentials
    creds = get_google_creds()
    
    # Fetch and process data
    print("[1/3] Loading Backendless audit data...")
    rows = fetch_and_process_csv()
    print(f"  Processed {len(rows)} aggregated rows")
    
    if rows:
        dates = sorted(set(r['Date'] for r in rows))
        print(f"  Date range: {dates[0]} to {dates[-1]}")
        names = sorted(set(r['Name'] for r in rows))
        print(f"  Team members: {names}")
    
    # Upload
    upload_to_sheet(rows, creds)
    
    print("\n[COMPLETE] Backendless activity fetch finished")


if __name__ == "__main__":
    main()

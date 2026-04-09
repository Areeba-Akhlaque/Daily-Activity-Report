"""
Pvragon Dashboard Data Refresh Script
=====================================
This script fetches all activity data and exports it to dashboard/data.json
Run this on a schedule (e.g., every hour) to keep the dashboard updated.
"""

import requests
import json
import pandas as pd
import pytz
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
import time

# Set Timezone to PST
PST = pytz.timezone('America/Los_Angeles')
now_pst = datetime.now(timezone.utc).astimezone(PST)

print(f"=== Dashboard Data Refresh - {now_pst.strftime('%Y-%m-%d %H:%M:%S %Z')} ===")

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
# Load .env explicitly
env_path = os.path.join(ROOT_DIR, '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
DASHBOARD_DIR = os.path.join(ROOT_DIR, 'dashboard')

# Ensure dashboard directory exists
os.makedirs(DASHBOARD_DIR, exist_ok=True)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/drive',
]

# Auth
print("[1/5] Authenticating...")
creds = Credentials.from_authorized_user_file(os.path.join(ROOT_DIR, 'token.json'), SCOPES)
if not creds.valid and creds.refresh_token:
    creds.refresh(Request())
    with open(os.path.join(ROOT_DIR, 'token.json'), 'w') as f:
        f.write(creds.to_json())

gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

# Fetch Daily Audit Report
print("[2/5] Fetching Daily Audit Report...")
try:
    ws1 = sh.worksheet('Daily Audit')
    data1 = ws1.get_all_records()
    print(f"  Loaded {len(data1)} rows")
except Exception as e:
    print(f"  Error: {e}")
    data1 = []

# Fetch Activity Time Analysis
print("[3/5] Fetching Activity Time Analysis...")
try:
    ws2 = sh.worksheet('Activity Time Analysis')
    data2 = ws2.get_all_records()
    print(f"  Loaded {len(data2)} rows")
except Exception as e:
    print(f"  Error: {e}")
    data2 = []

# Fetch Event Type References
print("[4/5] Fetching Event Type References...")
try:
    ws3 = sh.worksheet('Event Type References')
    data3 = ws3.get_all_records()
    print(f"  Loaded {len(data3)} records")
except Exception as e:
    print(f"  Error: {e}")
    data3 = []

# Fetch System Architecture
print("[5/5] Fetching System Architecture...")
try:
    ws4 = sh.worksheet('System Architecture')
    data4 = ws4.get_all_records()
    print(f"  Loaded {len(data4)} records")
except Exception as e:
    print(f"  Error: {e}")
    data4 = []

# Build dashboard data
dashboard_data = {
    'dailyAudit': data1,
    'timeAnalysis': data2,
    'eventReferences': data3,
    'systemArchitecture': data4,
    'lastUpdated': now_pst.strftime('%Y-%m-%d %H:%M:%S %Z'),
    'googleSheetUrl': f"https://docs.google.com/spreadsheets/d/{SHEET_ID}",
    'stats': {
        'totalAuditRows': len(data1),
        'totalTimeRows': len(data2),
        'totalEventRefs': len(data3),
        'uniqueMembers': len(set(r.get('Team Member', '') for r in data1)),
        'platforms': list(set(r.get('Platform', '') for r in data1 if r.get('Platform')))
    }
}

# Save to JSON
output_path = os.path.join(DASHBOARD_DIR, 'data.json')
with open(output_path, 'w') as f:
    json.dump(dashboard_data, f, indent=2)

print(f"\n[SUCCESS] Dashboard data saved to: {output_path}")
print(f"  Total audit records: {len(data1)}")
print(f"  Total time records: {len(data2)}")
print(f"  Last updated: {dashboard_data['lastUpdated']}")

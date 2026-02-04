"""
Pvragon Dashboard Data Refresh Script
=====================================
This script fetches all activity data and exports it to dashboard/data.json
Run this on a schedule (e.g., every hour) to keep the dashboard updated.
"""

import requests
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
import time

print(f"=== Dashboard Data Refresh - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SHEET_ID = '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo'
DASHBOARD_DIR = os.path.join(ROOT_DIR, 'dashboard')

# Ensure dashboard directory exists
os.makedirs(DASHBOARD_DIR, exist_ok=True)

# Auth
print("[1/3] Authenticating...")
creds = Credentials.from_authorized_user_file(os.path.join(ROOT_DIR, 'token.json'))
if not creds.valid and creds.refresh_token:
    creds.refresh(Request())
    with open(os.path.join(ROOT_DIR, 'token.json'), 'w') as f:
        f.write(creds.to_json())

gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

# Fetch Daily Audit Report
print("[2/3] Fetching Daily Audit Report...")
try:
    ws1 = sh.worksheet('Daily Audit')
    data1 = ws1.get_all_records()
    print(f"  Loaded {len(data1)} rows")
except Exception as e:
    print(f"  Error: {e}")
    data1 = []

# Fetch Activity Time Analysis
print("[3/3] Fetching Activity Time Analysis...")
try:
    ws2 = sh.worksheet('Activity Time Analysis')
    data2 = ws2.get_all_records()
    print(f"  Loaded {len(data2)} rows")
except Exception as e:
    print(f"  Error: {e}")
    data2 = []

# Build dashboard data
dashboard_data = {
    'dailyAudit': data1,
    'timeAnalysis': data2,
    'lastUpdated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'stats': {
        'totalAuditRows': len(data1),
        'totalTimeRows': len(data2),
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

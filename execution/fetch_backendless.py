import requests
import json
import pandas as pd
from datetime import datetime
import os
import sys
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import re
import time

# Add current directory to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude

# Config
# RECIPE: Target https://develop.backendless.com/console/home/login
# Note: 'console.backendless.com' is often an alias for 'develop.backendless.com'
LOGIN_URL = "https://develop.backendless.com/console/home/login"

APP_ID = os.environ.get('BACKENDLESS_APP_ID')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')
DEV_LOGIN = os.environ.get('BACKENDLESS_DEV_LOGIN')
DEV_PASSWORD = os.environ.get('BACKENDLESS_DEV_PASSWORD')

def get_google_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds

def clean_developer_email(dev_raw):
    """
    Robust parsing to retrieve Email from Developer column.
    Strategies:
    1. Check if dict (API valid)
    2. Check if JSON string (API raw)
    3. Regex search for email pattern
    """
    if not dev_raw: return "Unknown"
    
    val = str(dev_raw)
    
    # 1. Try JSON/Dict
    try:
        if isinstance(dev_raw, dict):
            return dev_raw.get('email', val)
        if isinstance(dev_raw, str) and ('{' in dev_raw):
            data = json.loads(dev_raw)
            if 'email' in data: return data['email']
    except: pass

    # 2. Regex
    try:
        match = re.search(r'[\w\.-]+@[\w\.-]+', val)
        if match: return match.group(0)
    except: pass
    
    return val

def fetch_logs_internal_api():
    if not DEV_LOGIN or not DEV_PASSWORD:
        print("[API] Credentials Missing (LOGIN/PASSWORD).")
        return []

    s = requests.Session()
    print(f"[API] Logging in to {LOGIN_URL}...")
    try:
        # 1. Login
        login_payload = {'login': DEV_LOGIN, 'password': DEV_PASSWORD}
        res = s.post(LOGIN_URL, json=login_payload)
        
        if res.status_code != 200:
            print(f"[API] Login Failed: {res.status_code} - {res.text}")
            return []
            
        # 2. Capture Auth Key
        auth_key = res.headers.get('auth-key')
        if not auth_key:
            try: auth_key = res.json().get('authKey')
            except: pass
            
        if not auth_key:
            print("[API] FATAL: Login successful but no auth-key found in headers/body.")
            print(f"Headers: {res.headers}")
            return []
            
        print(f"[API] Auth Key Captured: {auth_key[:10]}...")
        
        # 3. Fetch Audit Logs
        # URL: https://develop.backendless.com/{APP_ID}/console/security/audit-logs
        audit_url = f"https://develop.backendless.com/{APP_ID}/console/security/audit-logs"
        headers = {'auth-key': auth_key}
        
        print(f"[API] Fetching Logs from {audit_url}...")
        log_res = s.get(audit_url, headers=headers)
        
        if log_res.status_code != 200:
            print(f"[API] Log Validated Failed: {log_res.status_code} - {log_res.text}")
            return []
            
        data = log_res.json()
        if isinstance(data, list): return data
        if isinstance(data, dict): return data.get('data', [])
        return []
        
    except Exception as e:
        print(f"[API] Exception: {e}")
        return []

def main():
    print("="*60)
    print("Backendless Activity Fetch (Python Recipe)")
    print("="*60)
    
    logs = fetch_logs_internal_api()
    source = 'API'
    
    if not logs:
        print("[WARN] API returned no logs. Checking local CSV...")
        csv_path = os.path.join(ROOT_DIR, 'console_audit_logs.csv')
        if os.path.exists(csv_path):
             try:
                 logs = pd.read_csv(csv_path).to_dict('records')
                 source = 'CSV'
                 print(f"[CSV] Loaded {len(logs)} rows.")
             except: pass
    
    if not logs:
        print("[ERROR] No data found.")
        return # Exit gracefully

    # Process
    print(f"Processing {len(logs)} logs from {source}...")
    processed = []
    
    for log in logs:
        try:
            # Developer
            dev_raw = log.get('developer')
            email = clean_developer_email(dev_raw)
            name = map_name(email)
            if should_exclude(name): continue
            
            # Timestamp (ms -> date)
            ts = log.get('created') or log.get('timestamp')
            if not ts: continue
            
            ts = float(ts)
            if ts > 9999999999: ts = ts / 1000.0
            dt = datetime.fromtimestamp(ts)
            
            date_str = dt.strftime('%m/%d/%y') # For Sheet Match
            if date_str < '01/01/26': continue # Start Date filter
            
            # Event
            event = log.get('action') or log.get('event') or 'Unknown'
            
            processed.append({
                'Name': name,
                'Date': date_str,
                'Platform': 'Backendless App',
                'Event Type': event,
                'Count': 1
            })
        except: continue
        
    if not processed:
        print("No relevant logs found after processing.")
        return

    # Aggregate
    df = pd.DataFrame(processed)
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Count')
    rows = summary.to_dict('records')
    
    # Upload
    print(f"[Sheet] Uploading {len(rows)} summarized rows...")
    creds = get_google_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    # Update 'Console_Audit_Logs'
    try: ws = sh.worksheet('Console_Audit_Logs')
    except: ws = sh.add_worksheet('Console_Audit_Logs', 5000, 10)
    
    ws.clear()
    headers = ['Name', 'Date', 'Platform', 'Event Type', 'Count']
    values = [headers] + [[r[h] for h in headers] for r in rows]
    ws.update(values=values, range_name='A1')
    print("[SUCCESS] Done.")

if __name__ == "__main__":
    main()

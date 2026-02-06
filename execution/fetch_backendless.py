
import requests
import json
import pandas as pd
from datetime import datetime, timezone
import os
import sys
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Import name mappings
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude

# Config
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
APP_ID = os.environ.get('BACKENDLESS_APP_ID')
API_KEY = os.environ.get('BACKENDLESS_API_KEY')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

def get_google_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds

def fetch_raw_api_logs():
    """Attempt login and fetch raw audit logs."""
    dev_login = os.environ.get('BACKENDLESS_DEV_LOGIN')
    dev_password = os.environ.get('BACKENDLESS_DEV_PASSWORD')
    
    if not dev_login or not dev_password:
        print("  [API] Developer Credentials not set. Skipping login.")
        return []
        
    print(f"  [API] Attempting login as {dev_login}...")
    session = requests.Session()
    try:
        # 1. Login
        # Use custom domain or fallback to API
        custom = os.environ.get('BACKENDLESS_API_URL', 'https://api.backendless.com').rstrip('/')
        # Usually console login is at api.backendless.com/console/home/login
        # Try generic first
        login_url = "https://api.backendless.com/console/home/login"
        
        login_res = session.post(login_url, json={'login': dev_login, 'password': dev_password}, headers={"Content-Type": "application/json"})
        if login_res.status_code != 200:
            print(f"  [API] Login Failed: {login_res.status_code} - {login_res.text}")
            return []
            
        auth_key = login_res.json().get('authKey')
        print("  [API] Login Success. Fetching logs...")
        
        # 2. Fetch Logs
        log_url = f"https://api.backendless.com/console/application/{APP_ID}/audit/log"
        # Increase size? pageSize=1000? Not sure if supported.
        res = session.get(log_url, headers={"auth-key": auth_key})
        
        if res.status_code == 200:
            data = res.json()
            # Struct check
            if isinstance(data, dict) and 'data' in data: return data['data']
            if isinstance(data, list): return data
            return []
        else:
            print(f"  [API] Fetch Error: {res.status_code} - {res.text}")
            return []
    except Exception as e:
        print(f"  [API] Exception: {e}")
        return []

def fetch_raw_csv_logs():
    """Read local CSV."""
    csv_path = os.path.join(ROOT_DIR, 'console_audit_logs.csv')
    if not os.path.exists(csv_path):
        return []
    print("  [CSV] Reading local file...")
    try:
        return pd.read_csv(csv_path).to_dict('records')
    except:
        return []

def process_logs(logs, source_type='API'):
    """Convert raw logs (API or CSV dicts) to Summary format."""
    print(f"  Processing {len(logs)} records from {source_type}...")
    processed = []
    
    for log in logs:
        # EXTRACT FIELDS
        email = 'Unknown'
        event = 'Unknown'
        timestamp = 0
        
        try:
            # API Format usually: {'developer': '...', 'event': '...', 'created': 173...}
            # CSV Format usually: {'developer': '{"email":...}', 'event': '...', 'timestamp': 173...}
            
            # Email
            if 'developer' in log:
                dev = log['developer']
                if source_type == 'CSV':
                    # CSV stores it as JSON string often
                    try: 
                         if isinstance(dev, str) and '{' in dev: dev = json.loads(dev).get('email')
                    except: pass
                email = dev if isinstance(dev, str) else str(dev)
            else:
                email = log.get('email', 'Unknown')
                
            # Event
            event = log.get('event') or log.get('action') or 'Unknown'
            
            # Timestamp
            timestamp = log.get('created') or log.get('timestamp')
            
            if not timestamp: continue
            
            # Convert to Date
            ts_val = float(timestamp) / 1000.0 if float(timestamp) > 9999999999 else float(timestamp) # Handle ms vs s
            dt = datetime.fromtimestamp(ts_val)
            
            if dt.strftime('%Y-%m-%d') < START_DATE_STR: continue
            
            # Map Name
            name = map_name(email)
            if should_exclude(name): continue
            
            processed.append({
                'Name': name,
                'Date': dt.strftime('%m/%d/%y'),
                'Platform': 'Backendless App',
                'Event Type': event,
                'Count': 1
            })
            
        except Exception as e:
            continue
            
    if not processed: return []
    
    # Aggregate
    df = pd.DataFrame(processed)
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Count')
    return summary.to_dict('records')

def upload_to_sheet(rows, creds):
    print(f"[Sheet] Uploading {len(rows)} rows...")
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
    print("Backendless Activity Fetch")
    print("="*60)
    
    try:
        # 1. Try API
        logs = fetch_raw_api_logs()
        source = 'API'
        
        # 2. Fallback to CSV
        if not logs:
            print("  [INFO] No API logs found. Trying CSV...")
            logs = fetch_raw_csv_logs()
            source = 'CSV'
            
        if not logs:
            print("  [WARN] No data found from any source.")
            return

        # 3. Process
        summary = process_logs(logs, source)
        
        # 4. Upload
        creds = get_google_creds()
        upload_to_sheet(summary, creds)
        print("[COMPLETE] Done.")
        
    except Exception as e:
        print(f"[ERROR] Main Loop: {e}")
        # Dont fail hard to allow other scripts to run? 
        # Actually workflow steps run isolated, but we should exit 1 if critical?
        # User saw "IndentationError" which implies hard crash.
        # We catch here so we print error clean.
        sys.exit(1)

if __name__ == "__main__":
    main()

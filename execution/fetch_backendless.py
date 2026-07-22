import requests
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import os
import sys
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import re

# Add current directory to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, get_audit_date

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

BASE_URL = os.environ.get('BACKENDLESS_API_URL', "https://develop.backendless.com")
# Remove trailing slash if present
if BASE_URL.endswith('/'): BASE_URL = BASE_URL[:-1]

LOGIN_URL = f"{BASE_URL}/console/home/login"

APP_ID = os.environ.get('BACKENDLESS_APP_ID')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
DEV_LOGIN = os.environ.get('BACKENDLESS_DEV_LOGIN')
DEV_PASSWORD = os.environ.get('BACKENDLESS_DEV_PASSWORD')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

# Rolling window mode: by default only process events from last ROLLING_DAYS.
# FULL_REBUILD falls back to START_DATE (used after changing name_mappings/rules).
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
FULL_REBUILD = os.environ.get('FULL_REBUILD', 'false').lower() in ('true', '1', 'yes')
ROLLING_DAYS = int(os.environ.get('ROLLING_DAYS', '7'))
if FULL_REBUILD:
    FETCH_START_DT = pd.to_datetime(START_DATE_STR).tz_localize('UTC')
    print(f"[MODE] FULL_REBUILD — processing Backendless from {START_DATE_STR}")
else:
    FETCH_START_DT = pd.Timestamp.now(tz='UTC') - timedelta(days=ROLLING_DAYS)
    print(f"[MODE] Rolling {ROLLING_DAYS}-day window — processing Backendless from {FETCH_START_DT.strftime('%Y-%m-%d')}")

def get_google_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        # Save refreshed token to ensure persistence of correct scopes
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
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
        audit_url = f"{BASE_URL}/{APP_ID}/console/security/audit-logs"
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

import subprocess

def fetch_logs_node_wrapper():
    """Fetch logs by invoking the Node.js script with --json flag."""
    print("[API] invoking fetch_backendless_node.js --json...")
    node_script = os.path.join(SCRIPT_DIR, 'fetch_backendless_node.js')
    
    if not os.path.exists(node_script):
        print(f"[API] Error: Node script not found at {node_script}")
        return []
        
    try:
        # Check if node is available
        result = subprocess.run(
            ['node', node_script, '--json'], 
            cwd=SCRIPT_DIR, 
            capture_output=True, 
            text=True,
            encoding='utf-8'
        )
        
        if result.returncode != 0:
            print(f"[API] Node script failed: {result.stderr}")
            return []
            
        output = result.stdout.strip()
        if not output:
            return []
            
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return []
    except Exception as e:
        print(f"[API] Error running Node script: {e}")
        return []

def main():
    print("="*60)
    print("Backendless Activity Fetch (Node.js SDK via Python)")
    print("="*60)
    
    # Priority: Node.js (Official SDK)
    logs = fetch_logs_node_wrapper()

    # Fallback to Python Internal API (Legacy)
    if not logs:
        print("[WARN] Node fetch failed/empty. Trying direct Python API...")
        logs = fetch_logs_internal_api()
    
    if not logs:
        print("[ERROR] No data found via API. (CSV fallback DISABLED).")
        return # Exit gracefully

    # Sort by timestamp to apply cooldown
    logs.sort(key=lambda x: x.get('created') or x.get('timestamp') or 0)
    
    processed = []
    
    last_event_time = {} # (Name, Event) -> Last TS

    for log in logs:
        try:
            # Developer
            dev_raw = log.get('developer')
            email = clean_developer_email(dev_raw)
            name = map_name(email)
            if should_exclude(name, email): continue
            
            # Timestamp (ms -> date)
            ts_raw = log.get('created') or log.get('timestamp')
            if not ts_raw: continue
            
            ts = ts_raw / 1000.0 if ts_raw > 9999999999 else ts_raw
            
            # UTC filter via rolling window
            dt_utc = datetime.fromtimestamp(ts, timezone.utc)
            if pd.Timestamp(dt_utc) < FETCH_START_DT: continue
            
            event = log.get('action') or log.get('event') or 'Unknown'
            
            # INTEGRITY FIX: 60-second cooldown per (User, Event) to filter auto-save noise
            key = (name, event)
            if key in last_event_time:
                if abs(ts - last_event_time[key]) < 60:
                    continue
            last_event_time[key] = ts

            dt_pst = pd.to_datetime(ts, unit='s').tz_localize('UTC').tz_convert('America/Los_Angeles')
            date_str = get_audit_date(dt_pst)
            
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
    
    # Sort: Date (Newest), then Name (A-Z)
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Name'], ascending=[False, True])
    summary = summary.drop(columns=['sort_dt'])
    
    rows = summary.to_dict('records')

    # Upload
    print(f"[Sheet] Uploading {len(rows)} summarized rows...")
    creds = get_google_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    # Update 'Console_Audit_Logs' with rolling merge
    try:
        ws = sh.worksheet('Console_Audit_Logs')
        existing_data = ws.get_all_records()
        df_old = pd.DataFrame(existing_data)
    except:
        ws = sh.add_worksheet('Console_Audit_Logs', 5000, 10)
        df_old = pd.DataFrame(columns=['Name', 'Date', 'Platform', 'Event Type', 'Count'])

    summary_out = summary[['Name', 'Date', 'Platform', 'Event Type', 'Count']].copy()

    if FULL_REBUILD:
        combined = summary_out
        print(f"  [MERGE] FULL_REBUILD — overwriting with {len(summary_out)} fresh rows")
    elif not df_old.empty:
        for col in ['Name', 'Date', 'Platform', 'Event Type', 'Count']:
            if col not in df_old.columns:
                df_old[col] = ''
        df_old = df_old[['Name', 'Date', 'Platform', 'Event Type', 'Count']].copy()
        df_old['_dt'] = pd.to_datetime(df_old['Date'], format='%m/%d/%y', errors='coerce')
        window_start_naive = FETCH_START_DT.tz_convert('America/Los_Angeles').tz_localize(None).normalize()
        historical = df_old[df_old['_dt'].notna() & (df_old['_dt'] < window_start_naive)]
        historical = historical.drop(columns=['_dt'])
        combined = pd.concat([historical, summary_out], ignore_index=True)
        # Fresh rows must WIN over stale sheet rows for the same key. Without this,
        # any event the fetch reports for a date OLDER than the window start (e.g.
        # GitHub's historical PR/Issue search) lands in BOTH `historical` and the fresh
        # frame, silently duplicating that person/day/event-type — 85 such rows had
        # accumulated across 54 dates before this guard existed.
        combined = combined.drop_duplicates(
            subset=['Name', 'Date', 'Platform', 'Event Type'], keep='last')
        print(f"  [MERGE] Rolling {ROLLING_DAYS}d — {len(historical)} historical + {len(summary_out)} fresh = {len(combined)}")
    else:
        combined = summary_out
        print(f"  [MERGE] No existing sheet — writing {len(summary_out)} fresh rows")

    combined['Count'] = pd.to_numeric(combined['Count'], errors='coerce').fillna(1).astype(int)
    # Sort by date desc so newest rows sit at the top of the sheet.
    combined['_sort_dt'] = pd.to_datetime(combined['Date'], format='%m/%d/%y', errors='coerce')
    combined = combined.sort_values(by=['_sort_dt', 'Count'], ascending=[False, False]).drop(columns=['_sort_dt'])

    ws.clear()
    headers = ['Name', 'Date', 'Platform', 'Event Type', 'Count']
    values = [headers]
    for _, row in combined.iterrows():
        clean_row = []
        for col in headers:
            val = row[col]
            if col == 'Count':
                try:
                    clean_row.append(int(float(val)) if pd.notnull(val) and str(val) != '' else 1)
                except:
                    clean_row.append(1)
            else:
                clean_row.append(str(val) if pd.notnull(val) else "")
        values.append(clean_row)
    ws.update(values=values, range_name='A1')
    print(f"[SUCCESS] Uploaded {len(values)-1} rows.")

    # Save cache so generate_activity_time.py reads it instead of re-fetching.
    # Rolling window: merge fresh last-N-days events with preserved historical cache.
    cache_events_new = []
    for log in logs:
        try:
            dev_raw = log.get('developer')
            email = clean_developer_email(dev_raw)
            ts = log.get('created') or log.get('timestamp')
            if not ts: continue
            if ts > 9999999999: ts = ts / 1000.0
            dt_utc = pd.to_datetime(ts, unit='s').tz_localize('UTC')
            if dt_utc < FETCH_START_DT: continue
            dt_pst = dt_utc.tz_convert('America/Los_Angeles')
            name = map_name(email)
            if should_exclude(name): continue
            cache_events_new.append({'name': name, 'timestamp': dt_pst.isoformat(), 'app': 'Backendless'})
        except: continue

    try:
        import json as _json
        cache_path = os.path.join(ROOT_DIR, 'dashboard', 'backendless_events_cache.json')
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        if FULL_REBUILD:
            final_rows = cache_events_new
        else:
            existing = []
            if os.path.exists(cache_path):
                try:
                    with open(cache_path) as f:
                        existing = _json.load(f) or []
                except Exception:
                    existing = []
            window_start_pst = FETCH_START_DT.tz_convert('America/Los_Angeles')
            preserved = []
            for r in existing:
                ts_raw = r.get('timestamp', '')
                if not ts_raw: continue
                try:
                    r_dt = pd.to_datetime(ts_raw)
                    if r_dt.tz is None:
                        r_dt = r_dt.tz_localize('America/Los_Angeles')
                    if r_dt < window_start_pst:
                        preserved.append(r)
                except Exception:
                    continue
            merged = preserved + cache_events_new
            seen = set()
            final_rows = []
            for r in merged:
                key = (r.get('name', ''), r.get('timestamp', ''))
                if key in seen: continue
                seen.add(key)
                final_rows.append(r)
            print(f"  [CACHE] Preserved {len(preserved)} historical + {len(cache_events_new)} fresh = {len(final_rows)} total")

        with open(cache_path, 'w') as f:
            _json.dump(final_rows, f)
        print(f"  [CACHE] Saved {len(final_rows)} Backendless events to cache")
    except Exception as ce:
        print(f"  [CACHE WARN] {ce}")

def fetch_backendless_events_raw():
    """Wrapper for activity time analysis. Saves a cache to avoid re-fetching in generate_activity_time."""
    logs = fetch_logs_node_wrapper()
    if not logs:
        logs = fetch_logs_internal_api()

    events = []
    if not logs: return []

    from name_mappings import get_audit_date, map_name, should_exclude

    for log in logs:
        try:
            dev_raw = log.get('developer')
            email = clean_developer_email(dev_raw)
            ts = log.get('created') or log.get('timestamp')
            if not ts: continue
            if ts > 9999999999: ts = ts / 1000.0

            dt_utc = pd.to_datetime(ts, unit='s').tz_localize('UTC')
            if dt_utc < FETCH_START_DT: continue

            dt_pst = dt_utc.tz_convert('America/Los_Angeles')
            name = map_name(email)
            if should_exclude(name): continue
            events.append({'raw_name': email, 'name': name, 'timestamp': dt_pst, 'app': 'Backendless'})
        except: continue

    # Save cache so generate_activity_time.py doesn't re-fetch the API
    try:
        cache_rows = [
            {'name': e['name'], 'timestamp': e['timestamp'].isoformat(), 'app': 'Backendless'}
            for e in events
        ]
        cache_path = os.path.join(ROOT_DIR, 'dashboard', 'backendless_events_cache.json')
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump(cache_rows, f)
        print(f"  [CACHE] Saved {len(cache_rows)} Backendless events to cache")
    except Exception as ce:
        print(f"  [CACHE WARN] {ce}")

    return events

if __name__ == "__main__":
    main()

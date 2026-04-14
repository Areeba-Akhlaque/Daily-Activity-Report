import os
import sys
import time
import pandas as pd
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==========================================
# UNIFIED GOOGLE WORKSPACE FETCH SCRIPT
# ==========================================
# Combines:
# 1. Robust Auth (Interactive + Refresh)
# 2. Official Google API Client (Better handling)
# 3. Correct Event Logic (Delivery + Mail Event Type)
# 4. Sequential/Safe Fetching to avoid 500 Errors
# ==========================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Load helper modules
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, get_audit_date, STRICT_TEAM_GMAIL

# Configuration
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/gmail.send'
]

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

# Load AFTER load_env() so .env values are picked up in local runs
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')

# Rolling window mode: by default fetch only last ROLLING_DAYS; FULL_REBUILD=true
# falls back to fetching from START_DATE (used after changing name_mappings/rules).
FULL_REBUILD = os.environ.get('FULL_REBUILD', 'false').lower() in ('true', '1', 'yes')
ROLLING_DAYS = int(os.environ.get('ROLLING_DAYS', '7'))
if FULL_REBUILD:
    FETCH_START_DT = pd.to_datetime(START_DATE_STR).replace(tzinfo=timezone.utc)
    print(f"[MODE] FULL_REBUILD — fetching Google Workspace from {START_DATE_STR}")
else:
    FETCH_START_DT = datetime.now(timezone.utc) - timedelta(days=ROLLING_DAYS)
    print(f"[MODE] Rolling {ROLLING_DAYS}-day window — fetching Google Workspace from {FETCH_START_DT.strftime('%Y-%m-%d')}")

def get_creds():
    """Get valid credentials, refreshing or prompting as needed."""
    creds = None
    token_path = os.path.join(SCRIPT_DIR, 'token.json')
    if not os.path.exists(token_path):
        token_path = os.path.join(ROOT_DIR, 'token.json')

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            print("[Auth] Token file corrupt/invalid.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[Auth] Refreshing expired token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Auth] Refresh failed: {e}")
                creds = None

        if not creds:
            print("[Auth] Initiating new login flow...")
            secrets = [f for f in os.listdir(SCRIPT_DIR) if 'client_secret' in f or 'credentials.json' in f.lower()]
            if not secrets:
                 secrets = [os.path.join(ROOT_DIR, f) for f in os.listdir(ROOT_DIR) if 'client_secret' in f or 'credentials.json' in f.lower()]
            
            if not secrets:
                # Last resort
                params_creds = os.path.join(ROOT_DIR, 'credentials.json')
                if os.path.exists(params_creds):
                    secrets = [params_creds]
            
            if not secrets:
                print("[Auth] ERROR: No credentials.json found.")
                return None
            
            flow = InstalledAppFlow.from_client_secrets_file(secrets[0], SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save valid token
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    return creds

def fetch_logs_in_windows(service, user_key, application_name, start_dt, end_dt):
    """Fetch logs respecting 30-day API limit."""
    all_events = []
    processed_ids = set()
    
    # Generate 30-day windows
    windows = []
    curr = start_dt
    while curr < end_dt:
        nxt = curr + timedelta(days=29) # Safe margin < 30
        windows.append((curr, nxt if nxt < end_dt else end_dt))
        curr = nxt
        
    for w_start, w_end in windows:
        # print(f"    - Window: {w_start.strftime('%m-%d')} to {w_end.strftime('%m-%d')}")
        start_str = w_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        end_str = w_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        events = fetch_logs_for_user(service, user_key, application_name, start_str, end_str, processed_ids)
        all_events.extend(events)
        
    return all_events

def fetch_logs_for_user(service, user_key, application_name, start_time, end_time, processed_ids=None):
    """Fetch logs for a specific user using official client (single window)."""
    events = []
    if processed_ids is None:
        processed_ids = set()
    page_token = None
    
    params = {
        'userKey': user_key,
        'applicationName': application_name,
        'startTime': start_time,
        'endTime': end_time,
        'maxResults': 1000
    }
    
    if application_name == 'gmail':
        params['eventName'] = 'delivery'
        params['filters'] = 'event_info.mail_event_type==1'
    
    while True:
        try:
            if page_token:
                params['pageToken'] = page_token
                
            response = service.activities().list(**params).execute()
            items = response.get('items', [])

            for item in items:
                actor_email = item.get('actor', {}).get('email', '')
                timestamp = item.get('id', {}).get('time', '')
                
                for ev in item.get('events', []):
                    event_name = ev.get('name', '')
                    keep = False
                    mapped_type = ""
                    effective_email = actor_email

                    if application_name == 'gmail':
                        # The API filter filters by mail_event_type==1 (Message Sent)
                        
                        # 1. Map name to check strict inclusion
                        mapped_nm = map_name(actor_email)
                        if mapped_nm not in STRICT_TEAM_GMAIL:
                             continue

                        # 2. FILTER: Exclude auto-generated emails
                        is_auto = False
                        params_list = ev.get('parameters', [])
                        for p in params_list:
                            if p.get('name') == 'message_info' and 'messageValue' in p:
                                inner_params = p['messageValue'].get('parameter', [])
                                for ip in inner_params:
                                    # Common flags for automated emails
                                    if ip.get('name') in ['is_auto_response', 'auto_reply'] and ip.get('boolValue') is True:
                                        is_auto = True
                                        break
                        
                        if is_auto:
                            continue

                        keep = True
                        mapped_type = "Gmail Send"

                    elif application_name == 'drive':
                         if event_name == 'edit':
                             keep = True
                             mapped_type = "Drive Edit"
                    
                    
                    if keep and effective_email:
                        # SAFEGUARD: Skip IDs starting with /v/ or missing @ symbols that look like system ids
                        if effective_email.startswith('/v/') or ('@' not in effective_email and len(effective_email) > 15):
                             continue
                        # DEDUPLICATION
                        uniq_id = item.get('id', {}).get('uniqueQualifier')
                        if not uniq_id:
                             # Fallback composite key
                             msg_id = next((p['value'] for p in ev.get('parameters', []) if p['name'] == 'message_id'), 'no_msg_id')
                             uniq_id = f"{timestamp}_{actor_email}_{event_name}_{msg_id}"
                        
                        if uniq_id in processed_ids:
                            continue
                        processed_ids.add(uniq_id)

                        try:
                            # PST TIMEZONE CONVERSION
                            # Ensure timestamp is parsed as UTC aware
                            if str(timestamp).endswith('Z'):
                                ts_str = str(timestamp).replace('Z', '+00:00')
                            else:
                                ts_str = str(timestamp)
                                
                            dt_utc = pd.to_datetime(ts_str)
                            if dt_utc.tz is None:
                                dt_utc = dt_utc.tz_localize('UTC')
                                
                            dt_pst = dt_utc.tz_convert('America/Los_Angeles')
                            
                            events.append({
                                "Name": map_name(effective_email),
                                "Date": get_audit_date(dt_pst),
                                "timestamp_dt": dt_pst,
                                "Platform": "Google Workspace",
                                "Event Type": mapped_type,
                                "Quantity": 1
                            })
                        except Exception as e:
                            # print(f"Date error: {e}")
                            pass

            page_token = response.get('nextPageToken')
            if not page_token:
                break
                
        except HttpError as e:
            if e.resp.status in [403, 429, 500, 502, 503]:
                error_body = e.content.decode('utf-8') if e.content else "No content"
                print(f"    [Retry] API Error {e.resp.status}. Detail: {error_body}. Waiting 10s...")
                time.sleep(10)
                continue
            else:
                # print(f"    [Error] {e}") # specific window error shouldn't crash all
                break
        except Exception as e:
            # print(f"    [Error] {e}")
            break
            
    return events

def fetch_all_google_workspace(creds):
    print("="*60)
    print("STARTING UNIFIED GOOGLE WORKSPACE FETCH")
    print("="*60)
    
    service = build('admin', 'reports_v1', credentials=creds)
    all_events = []

    start_dt = FETCH_START_DT
    end_dt = datetime.now(timezone.utc)
    
    # 1. GMAIL FETCH (Unified 'all' scan)
    print(f"\\n[Gmail] Scanning 'all' users ({start_dt.strftime('%Y-%m-%d')} - {end_dt.strftime('%Y-%m-%d')})...")
    try:
        # Fetching for 'all' users is more robust and mirrors generate_activity_time.py success
        events = fetch_logs_in_windows(service, 'all', 'gmail', start_dt, end_dt)
        print(f"  Found {len(events)} Gmail events.")
        all_events.extend(events)
    except Exception as e:
        print(f"  [Gmail Error] {e}")
            
    # 2. DRIVE FETCH 
    print(f"\n[Drive] Scanning 'all' users...")
    try:
        events = fetch_logs_in_windows(service, 'all', 'drive', start_dt, end_dt)
        print(f"  Found {len(events)} Drive events.")
        all_events.extend(events)
    except Exception as e:
        print(f"  [Drive Error] {e}")


    return all_events

def process_and_upload(events):
    print(f"\n[Upload] Processing {len(events)} total events...")
    if not events:
        print("  No events to upload.")
        return

    df = pd.DataFrame(events)
    # Mapping is already done in fetch_logs_for_user, but we double check here
    df['Name'] = df['Name'].apply(map_name)
    df = df[~df['Name'].apply(should_exclude)]
    
    # Aggregate
    if df.empty:
        print("  No events left after mapping/exclusion.")
        return

    # DEBUG: Print Per-User Breakdown
    print("\n=== GMAIL ACTIVITY SUMMARY (Per User) ===")
    try:
        # Filter for Gmail events only for clarity
        gmail_df = df[df['Platform'] == 'Google Workspace']
        if not gmail_df.empty:
             summary_table = gmail_df.groupby(['Name', 'Event Type']).size().unstack(fill_value=0)
             print(summary_table.to_string())
        else:
             print("No Google Workspace events found.")
    except Exception as e:
        print(f"Error printing summary: {e}")
    print("=========================================\n")
    
    # === 15-MINUTE WINDOW DEDUPLICATION (Occurrence-Based Counting) ===
    # Instead of counting each individual file operation (e.g., 200 file copies = 200 events),
    # group events within 15-minute windows per user per event type.
    # This means bulk operations (mass copy, drag-drop uploads) count as 1 occurrence.
    # Max possible per user per event type per day: ~96 events.
    if 'timestamp_dt' in df.columns:
        original_count = len(df)
        df['_window_15m'] = df['timestamp_dt'].dt.tz_convert('UTC').dt.floor('15min')
        df = df.drop_duplicates(subset=['Name', 'Event Type', '_window_15m'])
        df = df.drop(columns=['_window_15m'])
        deduped_count = len(df)
        if original_count != deduped_count:
            print(f"  15-min window dedup: {original_count} → {deduped_count} events (removed {original_count - deduped_count} bulk duplicates)")
    
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    
    final_df = summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
    # Save raw timestamped events cache for Activity Time Analysis
    # (prevents generate_activity_time.py from re-fetching Google Workspace APIs a second time)
    # Rolling mode: merge fresh window events with existing cache rows older than the window
    # so Activity Time Analysis keeps historical first/last/break calculations intact.
    if 'timestamp_dt' in df.columns:
        try:
            import json
            cache_rows_new = [
                {'name': row['Name'], 'timestamp': row['timestamp_dt'].isoformat(), 'app': f"GW:{row['Event Type'].replace(' ', '')}"}
                for _, row in df.iterrows()
            ]
            cache_path = os.path.join(ROOT_DIR, 'dashboard', 'gworkspace_events_cache.json')
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)

            if FULL_REBUILD:
                final_rows = cache_rows_new
            else:
                existing = []
                if os.path.exists(cache_path):
                    try:
                        with open(cache_path) as f:
                            existing = json.load(f) or []
                    except Exception:
                        existing = []
                # Parse each existing row's timestamp to a tz-aware dt for safe comparison
                # (direct ISO string compare would be unsafe across different offsets).
                preserved = []
                for r in existing:
                    ts_raw = r.get('timestamp', '')
                    if not ts_raw:
                        continue
                    try:
                        r_dt = pd.to_datetime(ts_raw)
                        if r_dt.tz is None:
                            r_dt = r_dt.tz_localize('UTC')
                        if r_dt < FETCH_START_DT:
                            preserved.append(r)
                    except Exception:
                        continue
                merged = preserved + cache_rows_new
                seen = set()
                final_rows = []
                for r in merged:
                    key = (r.get('name', ''), r.get('timestamp', ''), r.get('app', ''))
                    if key in seen:
                        continue
                    seen.add(key)
                    final_rows.append(r)
                print(f"  [CACHE] Preserved {len(preserved)} historical + {len(cache_rows_new)} fresh = {len(final_rows)} total")

            with open(cache_path, 'w') as f:
                json.dump(final_rows, f)
            print(f"  [CACHE] Saved {len(final_rows)} Google Workspace events to cache")
        except Exception as ce:
            print(f"  [CACHE WARN] {ce}")

    # Upload — rolling-window aware
    # FULL_REBUILD: clear sheet, write fresh fetch entirely.
    # Rolling:      read existing sheet, keep rows older than the window, merge with
    #               fresh window rows, write combined. This preserves historical data
    #               while eliminating duplicates for dates inside the window.
    creds = get_creds()
    gc = gspread.authorize(creds)
    try:
        sh = gc.open_by_key(SHEET_ID)
        tab_name = "GoogleWorkspace_Activity"

        df_old = pd.DataFrame(columns=['Name', 'Date', 'Platform', 'Event Type', 'Quantity'])
        try:
            ws = sh.worksheet(tab_name)
            if not FULL_REBUILD:
                existing_data = ws.get_all_records()
                df_old = pd.DataFrame(existing_data) if existing_data else df_old
        except Exception:
            ws = sh.add_worksheet(title=tab_name, rows=2000, cols=10)

        if FULL_REBUILD or df_old.empty:
            combined = final_df
            if FULL_REBUILD:
                print(f"  [MERGE] FULL_REBUILD — overwriting with {len(final_df)} fresh rows")
            else:
                print(f"  [MERGE] No existing sheet data — writing {len(final_df)} fresh rows")
        else:
            for col in ['Name', 'Date', 'Platform', 'Event Type', 'Quantity']:
                if col not in df_old.columns:
                    df_old[col] = ''
            df_old = df_old[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']].copy()
            df_old['_dt'] = pd.to_datetime(df_old['Date'], format='%m/%d/%y', errors='coerce')
            window_start_naive = FETCH_START_DT.replace(tzinfo=None).date()
            historical = df_old[df_old['_dt'].notna() & (df_old['_dt'].dt.date < window_start_naive)]
            historical = historical.drop(columns=['_dt'])
            combined = pd.concat([historical, final_df], ignore_index=True)
            print(f"  [MERGE] Rolling {ROLLING_DAYS}d — {len(historical)} historical + {len(final_df)} fresh = {len(combined)}")

        combined['Quantity'] = pd.to_numeric(combined['Quantity'], errors='coerce').fillna(1).astype(int)
        rows_to_upload = [combined.columns.values.tolist()] + combined.values.tolist()

        ws.clear()
        ws.update(values=rows_to_upload, range_name='A1')
        print(f"  [SUCCESS] Uploaded {len(combined)} aggregate rows to '{tab_name}'.")
    except Exception as e:
        print(f"  [ERROR] Upload failed: {e}")

if __name__ == "__main__":
    creds = get_creds()
    if creds:
        events = fetch_all_google_workspace(creds)
        process_and_upload(events)

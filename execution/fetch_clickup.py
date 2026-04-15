import requests
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import os
import sys
import time
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ==========================================
# CONFIGURATION - Loaded from environment
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

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

# Import name mappings
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, get_audit_date

CLICKUP_API_KEY = os.environ.get('CLICKUP_API_KEY', '')
WORKSPACE_ID = os.environ.get('CLICKUP_WORKSPACE_ID', '9011906822')
TEAM_ID = os.environ.get('CLICKUP_TEAM_ID', '9011906822')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')

# Rolling window mode: by default only fetch events from (now - ROLLING_DAYS).
# FULL_REBUILD=true falls back to START_DATE (used after changing name_mappings/rules).
FULL_REBUILD = os.environ.get('FULL_REBUILD', 'false').lower() in ('true', '1', 'yes')
ROLLING_DAYS = int(os.environ.get('ROLLING_DAYS', '7'))
if FULL_REBUILD:
    FETCH_START_DT = datetime.strptime(START_DATE_STR, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    print(f"[MODE] FULL_REBUILD — fetching ClickUp from {START_DATE_STR}")
else:
    FETCH_START_DT = datetime.now(timezone.utc) - timedelta(days=ROLLING_DAYS)
    print(f"[MODE] Rolling {ROLLING_DAYS}-day window — fetching ClickUp from {FETCH_START_DT.strftime('%Y-%m-%d')}")
FETCH_START_TS_MS = int(FETCH_START_DT.timestamp() * 1000)
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

USER_CACHE = {}

def get_headers_v2():
    return {"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"}

def get_headers_v3():
    return {"Authorization": CLICKUP_API_KEY, "Accept": "application/json"}

def fetch_users():
    print("[1/4] Fetching Workspace Members...")
    try:
        resp = requests.get(f"https://api.clickup.com/api/v2/team/{TEAM_ID}", headers=get_headers_v2())
        if resp.status_code == 200:
            for m in resp.json().get('team', {}).get('members', []):
                u = m.get('user', {})
                USER_CACHE[str(u.get('id'))] = u.get('username')
            print(f"  Cached {len(USER_CACHE)} users.")
        else:
            print(f"  Failed: {resp.status_code}")
    except Exception as e: print(f"  Error: {e}")

def fetch_task_activity():
    print("[2/4] Fetching Task State-based Events (Created, Updated, Completed)...")
    events = []
    task_ids = set()
    page = 0
    has_more = True
    
    while has_more:
        try:
            url = f"https://api.clickup.com/api/v2/team/{TEAM_ID}/task"
            params = {
                "date_updated_gt": FETCH_START_TS_MS,
                "page": page,
                "subtasks": "true",
                "include_closed": "true",
                "include_deleted": "true"
            }
            resp = requests.get(url, headers=get_headers_v2(), params=params)
            if resp.status_code != 200: break
            tasks = resp.json().get('tasks', [])
            if not tasks: break
            
            print(f"  Processing Tasks Page {page} ({len(tasks)} tasks)...")
            last_event_time = {} # (User, Type) -> Last TS

            for t in tasks:
                tid = t.get('id')
                task_ids.add(tid)
                
                # Helper for cooldown
                def add_event(uid, ts, etype):
                    if not uid or not ts: return
                    ts_unix = int(ts) / 1000.0
                    key = (uid, etype)
                    if key in last_event_time:
                        if abs(ts_unix - last_event_time[key]) < 60:
                            return
                    last_event_time[key] = ts_unix
                    events.append({"user_id": uid, "timestamp": ts, "event_type": etype})

                # 1. task created
                d_c = int(t.get('date_created') or 0)
                if d_c >= FETCH_START_TS_MS:
                    uid = str(t.get('creator', {}).get('id', ''))
                    add_event(uid, d_c, "task created")
                
                # 2. task completed
                d_done = t.get('date_done') or t.get('date_closed')
                if d_done:
                    d_d_int = int(d_done)
                    if d_d_int >= FETCH_START_TS_MS:
                        assignees = t.get('assignees', [])
                        uid = str(assignees[0].get('id')) if assignees else str(t.get('creator', {}).get('id', ''))
                        add_event(uid, d_d_int, "task completed")
                
                # 3. task updated (based on last update)
                d_u = int(t.get('date_updated') or 0)
                if d_u >= FETCH_START_TS_MS:
                    # Attribution on 'updated' is noisy in List API (often guesses creator/assignee).
                    # We only count it if it's NOT a creation/completion event.
                    if abs(d_u - d_c) > 5000: # not the same as creation
                        assignees = t.get('assignees', [])
                        uid = str(assignees[0].get('id')) if assignees else str(t.get('creator', {}).get('id', ''))
                        add_event(uid, d_u, "task updated")

                # 4. Comments (These are always attributed correctly)
                # Note: We still fetch comments as they are reliable user-attributed logs.
                # However, fetching individual task comments is slow. We'll do it selectively.
                # In this loop we only fetched tasks updated in 2026, so it's a good subset.
                
            page += 1
            if page > 100: break
        except Exception as e:
            print(f"  Error: {e}"); break
            
    return events, list(task_ids)

from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_comments_for_task(tid):
    try:
        url = f"https://api.clickup.com/api/v2/task/{tid}/comment"
        resp = requests.get(url, headers=get_headers_v2(), timeout=10)
        if resp.status_code == 200:
            comments = resp.json().get('comments', [])
            task_events = []
            for c in comments:
                c_date = int(c.get('date', 0))
                if c_date >= FETCH_START_TS_MS:
                    uid = str(c.get('user', {}).get('id', ''))
                    task_events.append({"user_id": uid, "timestamp": c_date, "event_type": "Comment Posted"})
            return task_events
    except:
        pass
    return []

def fetch_comments_for_active_tasks(task_ids):
    print(f"  Fetching comments for {len(task_ids)} relevant tasks (Parallel)...")
    events = []
    
    # Use ThreadPool to fetch comments in parallel (max 10 threads to avoid hitting rate limits too hard)
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_tid = {executor.submit(fetch_comments_for_task, tid): tid for tid in task_ids}
        
        count = 0
        for future in as_completed(future_to_tid):
            result = future.result()
            if result:
                events.extend(result)
            
            count += 1
            if count % 100 == 0:
                print(f"    Checked comments for {count}/{len(task_ids)} tasks...")
                
    return events

def _fetch_messages_for_channel(cid, event_type):
    """Fetch all messages (and replies) for a single channel/DM channel."""
    events = []
    msg_cursor = ""
    while True:
        params = {"cursor": msg_cursor} if msg_cursor else {}
        m_resp = None
        for attempt in range(4):  # up to 3 retries on 429
            try:
                m_resp = requests.get(
                    f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}/chat/channels/{cid}/messages",
                    headers=get_headers_v3(), params=params, timeout=15
                )
                if m_resp.status_code == 429:
                    wait = (attempt + 1) * 10  # 10s, 20s, 30s
                    # print(f"    [Chat] 429 for channel {cid}, retry in {wait}s...")
                    time.sleep(wait)
                    continue
                break  # success or non-retryable error
            except Exception as e:
                print(f"    [Chat] Message fetch error for channel {cid}: {e}")
                break

        if m_resp is None or m_resp.status_code != 200:
            if m_resp is not None and m_resp.status_code != 429:
                pass  # silently skip — channel may be inaccessible
            break
        resp_body = m_resp.json()
        m_data = resp_body.get('data', [])
        if not m_data:
            break

        stop_channel = False
        for m in m_data:
            m_ts = int(m.get('date', 0))
            if m_ts < FETCH_START_TS_MS:
                stop_channel = True
                break
            uid = str(m.get('user_id') or m.get('user', {}).get('id', ''))
            if uid:
                events.append({"user_id": uid, "timestamp": m_ts, "event_type": event_type})

            # Fetch thread replies
            if m.get('replies_count', 0) > 0:
                mid = m.get('id')
                try:
                    r_resp = requests.get(
                        f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}/chat/messages/{mid}/replies",
                        headers=get_headers_v3(), timeout=15
                    )
                    if r_resp.status_code == 200:
                        for r in r_resp.json().get('data', []):
                            r_ts = int(r.get('date', 0))
                            if r_ts >= FETCH_START_TS_MS:
                                r_uid = str(r.get('user_id') or r.get('user', {}).get('id', ''))
                                if r_uid:
                                    events.append({"user_id": r_uid, "timestamp": r_ts, "event_type": event_type})
                except Exception:
                    pass

        if stop_channel:
            break
        msg_cursor = resp_body.get('next_cursor') or ''
        if not msg_cursor:
            break
    return events


def _collect_channels(channel_type_filter=None):
    """
    Page through the ClickUp v3 channels endpoint.
    channel_type_filter: None (all), 'CHANNEL' (public), 'DIRECT' (DMs), etc.
    Returns list of channel dicts.
    """
    channels = []
    cursor = ""
    while True:
        params = {}
        if cursor:
            params['cursor'] = cursor
        if channel_type_filter:
            params['type'] = channel_type_filter

        try:
            resp = requests.get(
                f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}/chat/channels",
                headers=get_headers_v3(), params=params, timeout=15
            )
        except Exception as e:
            print(f"  [Chat] Channel list error (filter={channel_type_filter}): {e}")
            break

        if resp.status_code != 200:
            print(f"  [Chat] Channel list HTTP {resp.status_code} (filter={channel_type_filter}): {resp.text[:200]}")
            break

        data = resp.json()
        batch = data.get('data', []) or data.get('channels', [])
        channels.extend(batch)

        cursor = data.get('next_cursor') or data.get('nextCursor') or ''
        if not cursor:
            break
    return channels


def fetch_chat_activity():
    print("[3/4] Fetching Chat Activity (Channels & Direct Messages)...")
    events = []

    # --- Step A: Collect all channels (unfiltered) ---
    all_channels = _collect_channels(channel_type_filter=None)
    print(f"  [Chat] Unfiltered channel list: {len(all_channels)} items")

    # --- Step B: Explicitly fetch DM channels too ---
    # ClickUp v3 changed its API so DMs may not appear in the unfiltered listing.
    # We fetch with explicit type filters to guarantee coverage.
    for dm_type in ('DIRECT', 'direct', 'dm', 'DM'):
        dm_channels = _collect_channels(channel_type_filter=dm_type)
        if dm_channels:
            print(f"  [Chat] Found {len(dm_channels)} DM channels via type='{dm_type}'")
            all_channels.extend(dm_channels)
            break  # Only one filter will work; stop on first success

    # Deduplicate by channel ID
    seen_ids = set()
    unique_channels = []
    for c in all_channels:
        cid = c.get('id')
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            unique_channels.append(c)

    print(f"  [Chat] Total unique channels to scan: {len(unique_channels)}")

    # Summarise what types we found (for diagnostics)
    type_counts = {}
    for c in unique_channels:
        t = str(c.get('type', 'unknown')).upper()
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"  [Chat] Channel types found: {type_counts}")

    # --- Step C: Fetch messages for each channel ---
    for c in unique_channels:
        cid = c.get('id')
        ctype = str(c.get('type', '')).upper()

        # Classify: anything that isn't a public/group channel is a DM
        if ctype in ('CHANNEL', 'PUBLIC', 'GROUP', 'SPACE'):
            event_type = "Channels messages"
        else:
            event_type = "Direct chats messages"

        ch_events = _fetch_messages_for_channel(cid, event_type)
        events.extend(ch_events)

    dm_count = sum(1 for e in events if e['event_type'] == 'Direct chats messages')
    ch_count = sum(1 for e in events if e['event_type'] == 'Channels messages')
    print(f"  [Chat] Total events: {len(events)} ({ch_count} channel msgs, {dm_count} DMs)")
    return events

def process_and_upload(events):
    print("[4/4] Processing and Uploading...")
    if not events: print("  No events found."); return

    df = pd.DataFrame(events)
    df['Raw Name'] = df['user_id'].apply(lambda x: USER_CACHE.get(str(x), f"User {x}"))
    df['Name'] = df['Raw Name'].apply(map_name)

    # Filter out unidentified generic users and non-core team members
    df = df[~df['Name'].str.startswith('User')]
    df = df[~df['Name'].apply(should_exclude)]
    # Convert ms timestamp to PST and apply Audit Date logic
    df['dt_pst'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('America/Los_Angeles')
    df['Date'] = df['dt_pst'].apply(get_audit_date)

    # Save raw timestamped events cache for Activity Time Analysis.
    # Rolling window: merge fresh last-N-days events with preserved pre-window historical cache.
    # FULL_REBUILD: overwrite entirely.
    try:
        cache_rows_new = [
            {'name': row['Name'], 'timestamp': row['dt_pst'].isoformat(), 'app': 'ClickUp'}
            for _, row in df.iterrows()
        ]
        cache_path = os.path.join(ROOT_DIR, 'dashboard', 'clickup_events_cache.json')
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
            window_start_dt = pd.Timestamp(FETCH_START_DT).tz_convert('America/Los_Angeles')
            preserved = []
            for r in existing:
                ts_raw = r.get('timestamp', '')
                if not ts_raw:
                    continue
                try:
                    r_dt = pd.to_datetime(ts_raw)
                    if r_dt.tz is None:
                        r_dt = r_dt.tz_localize('America/Los_Angeles')
                    if r_dt < window_start_dt:
                        preserved.append(r)
                except Exception:
                    continue
            merged = preserved + cache_rows_new
            seen = set()
            final_rows = []
            for r in merged:
                key = (r.get('name', ''), r.get('timestamp', ''))
                if key in seen:
                    continue
                seen.add(key)
                final_rows.append(r)
            print(f"  [CACHE] Preserved {len(preserved)} historical + {len(cache_rows_new)} fresh = {len(final_rows)} total")

        with open(cache_path, 'w') as f:
            json.dump(final_rows, f)
        print(f"  [CACHE] Saved {len(final_rows)} ClickUp events to cache")
    except Exception as ce:
        print(f"  [CACHE WARN] {ce}")
    
    summary = df.groupby(['Name', 'Date', 'event_type']).size().reset_index(name='Quantity')
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Quantity'], ascending=[False, False])
    
    summary['Platform'] = "ClickUp"
    summary['Event Type'] = summary['event_type']
    final_df = summary[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]
    
    # Upload
    creds = None
    if os.path.exists('token.json'): creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
             secrets = [f for f in os.listdir('.') if 'client_secret' in f or 'credentials.json' in f]
             if not secrets: return
             flow = InstalledAppFlow.from_client_secrets_file(secrets[0], SCOPES)
             creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token: token.write(creds.to_json())

    gc = gspread.authorize(creds)
    try:
        sh = gc.open_by_key(SHEET_ID)
        tn = "Clickup_Activity"
        try:
            ws = sh.worksheet(tn)
            existing_records = ws.get_all_records()
            df_old = pd.DataFrame(existing_records)
        except:
            ws = sh.add_worksheet(tn, 1000, 20)
            df_old = pd.DataFrame(columns=['Name', 'Date', 'Platform', 'Event Type', 'Quantity'])

        # Rolling merge: keep historical rows older than window, append fresh window rows.
        if FULL_REBUILD:
            combined = final_df
            print(f"  [MERGE] FULL_REBUILD — overwriting with {len(final_df)} fresh rows")
        elif not df_old.empty:
            for col in ['Name', 'Date', 'Platform', 'Event Type', 'Quantity']:
                if col not in df_old.columns:
                    df_old[col] = ''
            df_old = df_old[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']].copy()
            df_old['_dt'] = pd.to_datetime(df_old['Date'], format='%m/%d/%y', errors='coerce')
            window_start_naive = pd.Timestamp(FETCH_START_DT).tz_convert('America/Los_Angeles').tz_localize(None).normalize()
            historical = df_old[df_old['_dt'].notna() & (df_old['_dt'] < window_start_naive)]
            historical = historical.drop(columns=['_dt'])
            combined = pd.concat([historical, final_df], ignore_index=True)
            print(f"  [MERGE] Rolling {ROLLING_DAYS}d — {len(historical)} historical + {len(final_df)} fresh = {len(combined)}")
        else:
            combined = final_df
            print(f"  [MERGE] No existing sheet — writing {len(final_df)} fresh rows")

        combined['Quantity'] = pd.to_numeric(combined['Quantity'], errors='coerce').fillna(1).astype(int)
        # Sort by date desc so newest rows sit at the top of the sheet.
        combined['_sort_dt'] = pd.to_datetime(combined['Date'], format='%m/%d/%y', errors='coerce')
        combined = combined.sort_values(by=['_sort_dt', 'Quantity'], ascending=[False, False]).drop(columns=['_sort_dt'])
        combined = combined[['Name', 'Date', 'Platform', 'Event Type', 'Quantity']]

        rows_to_upload = [combined.columns.tolist()]
        for _, row in combined.iterrows():
            clean_row = []
            for col in combined.columns:
                val = row[col]
                if col == 'Quantity':
                    try:
                        clean_row.append(int(float(val)) if pd.notnull(val) and str(val) != '' else 1)
                    except:
                        clean_row.append(1)
                else:
                    clean_row.append(str(val) if pd.notnull(val) else "")
            rows_to_upload.append(clean_row)

        if len(rows_to_upload) > 1:
            ws.clear()
            ws.update(values=rows_to_upload, range_name='A1')
            print(f"  [SUCCESS] Uploaded {len(rows_to_upload)-1} aggregate rows.")
            print("  Event breakdown:")
            print(df['event_type'].value_counts().to_string())
        else:
            print("  [SKIP] No data to upload. Sheet preserved.")
    except Exception as e: print(f"  [ERROR] {e}")

if __name__ == "__main__":
    fetch_users()
    tasks, task_ids = fetch_task_activity()
    comm_events = fetch_comments_for_active_tasks(task_ids)
    chats = fetch_chat_activity()
    process_and_upload(tasks + comm_events + chats)

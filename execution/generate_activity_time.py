"""
Activity Time Analysis Generator
================================
Calculates work patterns from actual activity timestamps.
Uses Google Workspace (Drive, Gmail) and GitHub event timestamps.

Output Columns:
- Team Member
- Date
- First Activity (PST)
- Last Activity (PST)
- Active Window (Hours)
- Longest Break (Minutes)
- Total Events
"""

import os
import sys
import json
import time
import pandas as pd
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import pytz

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DASHBOARD_DIR = os.path.join(ROOT_DIR, 'dashboard')

# Import name mappings
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, STRICT_TEAM_GMAIL

# Load .env FIRST so os.environ.get() calls below pick up the right values
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
PST = pytz.timezone('America/Los_Angeles')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/gmail.send'
]


def get_creds():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def _jsonsafe(v):
    """Coerce a pandas/numpy scalar to a native JSON-serializable value for gspread."""
    if v is None:
        return ''
    if isinstance(v, (str, int, float)):
        return v
    item = getattr(v, 'item', None)  # numpy scalar -> python scalar
    if callable(item):
        try:
            return v.item()
        except Exception:
            pass
    return str(v)


def _get_or_create_ws(sh, title, rows=5000, cols=10):
    """Return the worksheet, creating it only if it genuinely doesn't exist."""
    try:
        return sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def _write_with_retry(ws, values, attempts=4):
    """Write a worksheet with backoff, WITHOUT clearing first.

    The old pattern was clear() then update(): a transient 429/5xx on the write
    left the tab cleared-but-empty, which silently zeroed every 'Active Hours' cell
    in the daily email. Here we overwrite from A1 (so a failed write keeps the last
    good data) and only afterwards trim any stale trailing rows left over from a
    previous, longer run. update() overwrites the whole header+data range each time,
    so this is idempotent and the tab can never be emptied by a write failure.
    """
    last_err = None
    n = len(values)
    for i in range(attempts):
        try:
            ws.update(values=values, range_name='A1')
            # Remove leftover rows from a previous run that had MORE rows than this one.
            try:
                grid_rows = ws.row_count
                if grid_rows > n:
                    ws.batch_clear([f'A{n + 1}:H{grid_rows}'])
            except Exception as trim_err:
                print(f'  [Sheet] trailing-row trim skipped: {trim_err}')
            return True
        except Exception as e:
            last_err = e
            wait = (i + 1) * 5
            print(f'  [Sheet] write attempt {i + 1}/{attempts} failed: {e} — retrying in {wait}s')
            time.sleep(wait)
    print(f'  [ERROR] Activity Time Analysis write failed after {attempts} attempts: {last_err}')
    raise last_err if last_err is not None else RuntimeError('write failed')


def _load_cache(cache_filename, label):
    """
    Load a timestamped-events cache written by a fetch_*.py script.
    Returns list of dicts with 'name' (str) and 'timestamp' (tz-aware PST datetime).
    Returns None if the cache file doesn't exist (signals caller to fall back).
    """
    cache_path = os.path.join(DASHBOARD_DIR, cache_filename)
    if not os.path.exists(cache_path):
        print(f'  [{label}] No cache file found at {cache_path} — will fall back.')
        return None
    try:
        with open(cache_path, 'r') as f:
            rows = json.load(f)
        events = []
        for r in rows:
            try:
                dt = pd.to_datetime(r['timestamp']).tz_convert(PST)
                events.append({'raw_name': r.get('name', ''), 'name': r.get('name', ''), 'timestamp': dt, 'app': r.get('app', label), 'event_type': r.get('event_type', '')})
            except Exception:
                continue
        print(f'  [{label}] Loaded {len(events)} events from cache.')
        return events
    except Exception as e:
        print(f'  [{label}] Cache load error: {e} — will fall back.')
        return None


def fetch_google_workspace_events(creds):
    """
    Load Google Workspace events from cache written by fetch_google_workspace.py.
    Falls back to direct API fetch if cache is missing.
    """
    print('[1/5] Loading Google Workspace events...')
    cached = _load_cache('gworkspace_events_cache.json', 'GWorkspace')
    if cached is not None:
        return cached

    # Fallback: re-fetch directly (only runs if fetch_google_workspace.py didn't run first)
    print('  [GWorkspace] Falling back to direct API fetch...')
    import requests as req
    events = []
    headers = {'Authorization': f'Bearer {creds.token}'}

    for app in ['drive', 'gmail']:
        start_dt = datetime.strptime(START_DATE, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        current_start = start_dt
        processed_ids = set()

        while current_start < now_dt:
            current_end = min(current_start + timedelta(days=30), now_dt)
            params = {
                'startTime': current_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'endTime': current_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'maxResults': 1000
            }
            if app == 'gmail':
                params['eventName'] = 'delivery'
                params['filters'] = 'event_info.mail_event_type==1'
            url = f'https://admin.googleapis.com/admin/reports/v1/activity/users/all/applications/{app}'

            while True:
                try:
                    resp = req.get(url, headers=headers, params=params)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    for item in data.get('items', []):
                        actor = item.get('actor', {})
                        email = actor.get('email', '')
                        if not email or actor.get('callerType') == 'KEY':
                            continue
                        if email.startswith('/v/') or ('@' not in email and len(email) > 15):
                            continue
                        uniq_id = item.get('id', {}).get('uniqueQualifier') or f"{item.get('id',{}).get('time','')}_{email}_{app}"
                        if uniq_id in processed_ids:
                            continue
                        processed_ids.add(uniq_id)
                        ts = item.get('id', {}).get('time', '')
                        if not ts:
                            continue
                        keep = False
                        evs = item.get('events', [])
                        if app == 'gmail':
                            mapped_nm = map_name(email)
                            if mapped_nm not in STRICT_TEAM_GMAIL:
                                continue
                            if not any(ip.get('name') in ['is_auto_response', 'auto_reply'] and ip.get('boolValue') is True
                                       for ev in evs for p in ev.get('parameters', [])
                                       if p.get('name') == 'message_info'
                                       for ip in p.get('messageValue', {}).get('parameter', [])):
                                keep = True
                        elif app == 'drive':
                            keep = any(e.get('name') == 'edit' for e in evs)
                        if keep:
                            try:
                                dt = pd.to_datetime(ts).tz_convert(PST)
                                events.append({'raw_name': email, 'name': map_name(email), 'timestamp': dt, 'app': f"GW:{app.capitalize()}"})
                            except Exception:
                                pass
                    if not data.get('nextPageToken'):
                        break
                    params['pageToken'] = data['nextPageToken']
                except Exception as e:
                    print(f'  Warning: {e}')
                    break
            current_start = current_end

    # 15-min window dedup
    if events:
        seen_windows = set()
        deduped = []
        for e in sorted(events, key=lambda x: x['timestamp']):
            wk = (e['raw_name'], e['app'], e['timestamp'].tz_convert('UTC').floor('15min'))
            if wk not in seen_windows:
                seen_windows.add(wk)
                deduped.append(e)
        events = deduped

    print(f'  Google Workspace (fallback): {len(events)} events')
    return events


def fetch_github_events(creds):
    """Read GitHub commit timestamps from the Github_Commits sheet (written by fetch_github_commits.py)."""
    print('[2/5] Loading GitHub commits from sheet...')
    events = []
    try:
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.worksheet('Github_Commits')
        data = ws.get_all_records()
        for r in data:
            dt_str = f"{r.get('Date','')} {r.get('Time','')}"
            try:
                dt = PST.localize(datetime.strptime(dt_str.strip(), '%m/%d/%y %I:%M %p'))
                name = map_name(r.get('Name', ''))
                if not should_exclude(name):
                    events.append({'raw_name': r.get('Name', ''), 'name': name, 'timestamp': dt, 'app': 'GitHub Commit'})
            except Exception:
                continue
    except Exception as e:
        print(f'  GitHub Sheet Fetch Error: {e}')
    print(f'  GitHub: {len(events)} commit events')
    return events


def fetch_backendless_events_wrapped():
    """Load Backendless events from cache written by fetch_backendless.py."""
    print('[3/5] Loading Backendless events...')
    cached = _load_cache('backendless_events_cache.json', 'Backendless')
    if cached is not None:
        return cached

    # Fallback: re-fetch via module
    print('  [Backendless] Falling back to direct API fetch...')
    try:
        import fetch_backendless
        return fetch_backendless.fetch_backendless_events_raw()
    except Exception as e:
        print(f'  Backendless Fetch Error: {e}')
        return []


def fetch_clickup_events_wrapped():
    """Load ClickUp events from cache written by fetch_clickup.py."""
    print('[4/5] Loading ClickUp events...')
    cached = _load_cache('clickup_events_cache.json', 'ClickUp')
    if cached is not None:
        return cached

    # Fallback: re-fetch via module
    print('  [ClickUp] Falling back to direct API fetch...')
    try:
        import fetch_clickup
        fetch_clickup.fetch_users()
        tasks, tids = fetch_clickup.fetch_task_activity()
        comments = fetch_clickup.fetch_comments_for_active_tasks(tids)
        chats = fetch_clickup.fetch_chat_activity()
        processed = []
        for e in tasks + comments + chats:
            uid = e.get('user_id')
            raw_n = fetch_clickup.USER_CACHE.get(str(uid), f"User {uid}")
            ts = e.get('timestamp')
            try:
                dt = pd.to_datetime(ts, unit='ms').tz_localize('UTC').tz_convert(PST)
                name = map_name(raw_n)
                if not should_exclude(name):
                    processed.append({'raw_name': raw_n, 'name': name, 'timestamp': dt, 'app': 'ClickUp'})
            except Exception:
                pass
        print(f'  ClickUp (fallback): {len(processed)} events')
        return processed
    except Exception as e:
        print(f'  ClickUp Fetch Error: {e}')
        return []


def fetch_figma_events_wrapped():
    """Load Figma events from cache written by fetch_figma.py."""
    print('[5/5] Loading Figma events...')
    cached = _load_cache('figma_events_cache.json', 'Figma')
    if cached is not None:
        return cached

    # Fallback: re-fetch via module
    print('  [Figma] Falling back to direct API fetch...')
    try:
        import fetch_figma
        raw_events = fetch_figma.fetch_all_activity()
        processed = []
        for e in raw_events:
            dt = e.get('timestamp')
            name = e.get('Name', '')
            if dt and name and not should_exclude(map_name(name)):
                processed.append({'raw_name': name, 'name': map_name(name), 'timestamp': dt, 'app': 'Figma'})
        print(f'  Figma (fallback): {len(processed)} events')
        return processed
    except Exception as e:
        print(f'  Figma Fetch Error: {e}')
        return []


def generate_activity_time_analysis(creds):
    """Generate Activity Time Analysis from actual event timestamps."""
    print('=' * 60)
    print('ACTIVITY TIME ANALYSIS GENERATOR')
    print(f'Started: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 60)
    
    # Fetch events
    gw_events = fetch_google_workspace_events(creds)
    gh_events = fetch_github_events(creds)
    bl_events = fetch_backendless_events_wrapped()
    cu_events = fetch_clickup_events_wrapped()
    fi_events = fetch_figma_events_wrapped()
    
    all_events = gw_events + gh_events + bl_events + cu_events + fi_events
    
    if not all_events:
        print('[SKIP] No events found')
        return
    
    # Map names — use pre-mapped 'name' from cache if available, else derive from raw_name
    for e in all_events:
        if not e.get('name'):
            e['name'] = map_name(e.get('raw_name', ''))

    # Filter exclusions
    all_events = [e for e in all_events if not should_exclude(e['name'])]
    print(f'After filtering and mapping: {len(all_events)} events')
    
    if not all_events:
        print('[SKIP] No events after filtering')
        return
    
    # Build DataFrame
    df = pd.DataFrame(all_events)
    # Apply 7PM Rolling Window Logic
    from name_mappings import get_audit_date
    df['date'] = df['timestamp'].apply(get_audit_date)
    
    # Group by NAME + DATE and calculate metrics
    results = []
    
    SESSION_GAP_MINUTES = 120 # 2-hour threshold for identifying meaningful breaks
    BUFFER_MINUTES = 30       # 30-minute total buffer (15m before/15m after) for each session
    
    for (name, date), group in df.groupby(['name', 'date']):
        # Get all unique timestamps for the user on this date, sorted
        times = sorted(group['timestamp'].unique())
        
        if not times:
            continue
            
        sessions = []
        current_session = [times[0]]
        
        # 1. Group events into distinct sessions (Active -> Break -> Active)
        for i in range(1, len(times)):
            t_prev = times[i-1]
            t_curr = times[i]
            gap = (t_curr - t_prev).total_seconds() / 60.0
            
            if gap > SESSION_GAP_MINUTES:
                # Gap is too long -> finalize current session and start new one
                sessions.append(current_session)
                current_session = [t_curr]
            else:
                current_session.append(t_curr)
        
        sessions.append(current_session)
        
        # 2. Calculate Total Active Time by summing session durations + buffers
        total_work_seconds = 0
        for session in sessions:
            s_start = session[0]
            s_end = session[-1]
            
            # Raw duration from first click to last click
            raw_duration_sec = (s_end - s_start).total_seconds()
            
            # Add the productivity buffer (setup + wrap-up)
            # This ensures even a single event gets 30 mins credit
            session_total_sec = raw_duration_sec + (BUFFER_MINUTES * 60)
            
            total_work_seconds += session_total_sec

        active_duration_hours = total_work_seconds / 3600.0
        
        # Calculate longest break (same as before)
        longest_gap = 0
        if len(times) > 1:
            for i in range(1, len(times)):
                gap = (times[i] - times[i-1]).total_seconds() / 60
                if gap > longest_gap:
                    longest_gap = gap
        
        first = times[0]
        last = times[-1]
        
        results.append({
            'Team Member': name,
            'Date': date,
            'First Activity (PST)': first.strftime('%I:%M %p'),
            'Last Activity (PST)': last.strftime('%I:%M %p'),
            'Active Window (Hours)': round(active_duration_hours, 1), # Now Duration
            'Longest Break (Minutes)': int(longest_gap),
            'Total Events': len(times),
            'Platform Distribution': ", ".join(group['app'].unique() if 'app' in group.columns else group['Platform'].unique() if 'Platform' in group.columns else ['Unknown'])
        })
    
    result_df = pd.DataFrame(results)

    # NEVER blank the tab on an empty result. If grouping produced no rows (e.g.
    # caches missing on this runner), leave whatever is already in the sheet so the
    # daily email keeps showing the last known good data instead of all-zeros.
    if result_df.empty:
        print('[WARN] Activity Time Analysis produced 0 rows — leaving existing tab untouched.')
        return

    # Sort by date (newest first), then by name
    result_df['sort_dt'] = pd.to_datetime(result_df['Date'], format='%m/%d/%y')
    result_df = result_df.sort_values(by=['sort_dt', 'Team Member'], ascending=[False, True])
    result_df = result_df.drop(columns=['sort_dt'])

    print(f'Final rows: {len(result_df)}')

    # Build a JSON-safe payload (native str/int/float only — no numpy types that
    # could make the gspread write raise mid-flight).
    header = result_df.columns.tolist()
    values = [header]
    for rec in result_df.to_dict('records'):
        values.append([_jsonsafe(rec[c]) for c in header])

    # Upload to Google Sheets with retry so a transient API error can't leave the
    # tab cleared-but-empty (which silently zeroed the daily email's Active Hours).
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = _get_or_create_ws(sh, 'Activity Time Analysis')
    _write_with_retry(ws, values)

    print(f'\n[SUCCESS] Activity Time Analysis updated: {len(result_df)} rows')
    
    # === NEW: Generate Hourly Data for Dashboard ===
    print("Generating Hourly Data JSON for Dashboard...")
    try:
        hourly_records = []
        for e in all_events:
            dt = e['timestamp'].astimezone(PST)
            hourly_records.append({
                'member': e['name'],
                'date': dt.strftime('%m/%d/%y'),
                'hour': int(dt.strftime('%H')), # 0-23
                'platform': e.get('app', 'Unknown'),
                'type': e.get('event_type') or e.get('Action') or 'Unknown'
            })
        
        df_hourly = pd.DataFrame(hourly_records)
        # Aggregate by hour
        hourly_counts = df_hourly.groupby(['member', 'date', 'hour', 'platform', 'type']).size().reset_index(name='count')
        
        dashboard_dir = os.path.join(ROOT_DIR, 'dashboard')
        os.makedirs(dashboard_dir, exist_ok=True)
        hourly_path = os.path.join(dashboard_dir, 'hourly_data.json')
        
        hourly_counts.to_json(hourly_path, orient='records')
        print(f"[SUCCESS] Hourly data saved to {hourly_path} ({len(hourly_counts)} records)")
    except Exception as e:
        print(f"[ERROR] Failed to generate hourly data: {e}")

    print('=' * 60)


def main():
    creds = get_creds()
    generate_activity_time_analysis(creds)


if __name__ == "__main__":
    main()

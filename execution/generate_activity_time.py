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
import requests
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import pytz

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Import name mappings
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, STRICT_TEAM_GMAIL
import fetch_clickup
import fetch_figma
import fetch_backendless

SHEET_ID = '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo'
PST = pytz.timezone('America/Los_Angeles')
START_DATE = '2026-01-01'

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


def get_creds():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def fetch_google_workspace_events(creds):
    """Fetch events from Google Workspace (Drive, Gmail) with timestamps."""
    print('[1/5] Fetching Google Workspace events...')
    events = []
    headers = {'Authorization': f'Bearer {creds.token}'}
    
    for app in ['drive', 'gmail']:
        start_dt = datetime.strptime(START_DATE, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        current_start = start_dt
        processed_ids = set()
        
        while current_start < now_dt:
            current_end = current_start + timedelta(days=30)
            if current_end > now_dt:
                current_end = now_dt
            
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
                    resp = requests.get(url, headers=headers, params=params)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    
                    items = data.get('items', [])
                    print(f"    Fetched {len(items)} items from {app}...")
                    
                    for item in items:
                        actor = item.get('actor', {})
                        email = actor.get('email', '')
                        if not email or actor.get('callerType') == 'KEY':
                            continue

                        # SAFEGUARD: Skip IDs starting with /v/ or missing @ symbols that look like system ids
                        if email.startswith('/v/') or ('@' not in email and len(email) > 15):
                             continue
                        
                        # Deduplication
                        uniq_id = item.get('id', {}).get('uniqueQualifier')
                        if not uniq_id:
                             # Fallback composite key
                             ts = item.get('id', {}).get('time', '')
                             uniq_id = f"{ts}_{email}_{app}"
                        
                        if uniq_id in processed_ids:
                            continue
                        processed_ids.add(uniq_id)
                        
                        ts = item.get('id', {}).get('time', '')
                        if ts:
                            try:
                                # Apply specific filters
                                keep = False
                                if app == 'gmail': 
                                    # 1. Map name to check strict inclusion
                                    mapped_nm = map_name(email)
                                    if mapped_nm not in STRICT_TEAM_GMAIL:
                                         continue

                                    # 2. FILTER: Exclude auto-generated emails
                                    is_auto = False
                                    events_in_item = item.get('events', [])
                                    for ev in events_in_item:
                                        params_list = ev.get('parameters', [])
                                        for p in params_list:
                                            if p.get('name') == 'message_info' and 'messageValue' in p:
                                                inner_params = p['messageValue'].get('parameter', [])
                                                for ip in inner_params:
                                                    if ip.get('name') in ['is_auto_response', 'auto_reply'] and ip.get('boolValue') is True:
                                                        is_auto = True
                                                        break
                                    if is_auto:
                                        continue
                                    keep = True # Filtered by API mail_event_type:1
                                elif app == 'drive':
                                    # We only want edits
                                    events_in_item = item.get('events', [])
                                    if any(e.get('name') == 'edit' for e in events_in_item):
                                        keep = True
                                
                                if keep:
                                    dt = pd.to_datetime(ts).tz_convert(PST)
                                    events.append({'raw_name': email, 'timestamp': dt, 'app': f"GW:{app.capitalize()}"})
                            except:
                                pass
                    
                    if not data.get('nextPageToken'):
                        break
                    params['pageToken'] = data['nextPageToken']
                except Exception as e:
                    print(f'  Warning: {e}')
                    break
            
            current_start = current_end
    
    print(f'  Google Workspace: {len(events)} events')
    return events


def fetch_github_events(creds):
    """Fetch events from GitHub_Commits worksheet for more detailed analysis."""
    print('[2/5] Fetching GitHub commits from worksheet...')
    events = []
    
    try:
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.worksheet('Github_Commits')
        data = ws.get_all_records()
        
        for r in data:
            dt_str = f"{r['Date']} {r['Time']}"
            try:
                # MM/DD/YY HH:MM AM/PM
                dt = datetime.strptime(dt_str, '%m/%d/%y %I:%M %p')
                dt = PST.localize(dt)
                events.append({
                    'raw_name': r['Name'], 
                    'timestamp': dt, 
                    'app': 'GitHub Commit'
                })
            except Exception as e:
                continue
                
    except Exception as e:
        print(f'  GitHub Worksheet Fetch Error: {e}')
    
    print(f'  GitHub: {len(events)} commit events')
    return events


def fetch_backendless_events_wrapped():
    """Fetch events from Backendless API with timestamps."""
    print('[3/5] Fetching Backendless events...')
    try:
        events = fetch_backendless.fetch_backendless_events_raw()
        print(f'  Backendless: {len(events)} events')
        return events
    except Exception as e:
        print(f'  Backendless Fetch Error: {e}')
        return []


def fetch_clickup_events_wrapped():
    """Fetch ClickUp events via fetch_clickup module."""
    print('[4/5] Fetching ClickUp events...')
    try:
        fetch_clickup.fetch_users()
        tasks, tids = fetch_clickup.fetch_task_activity()
        comments = fetch_clickup.fetch_comments_for_active_tasks(tids)
        chats = fetch_clickup.fetch_chat_activity()
        
        processed = []
        raw_events = tasks + comments + chats
        for e in raw_events:
            uid = e.get('user_id')
            raw_n = fetch_clickup.USER_CACHE.get(str(uid), f"User {uid}")
            ts = e.get('timestamp') # ms
            try:
                dt = pd.to_datetime(ts, unit='ms').tz_localize('UTC').tz_convert(PST)
                processed.append({'raw_name': raw_n, 'timestamp': dt, 'app': 'ClickUp'})
            except: pass
        print(f'  ClickUp: {len(processed)} events')
        return processed
    except Exception as e:
        print(f'  ClickUp Fetch Error: {e}')
        return []


def fetch_figma_events_wrapped():
    """Fetch Figma events via fetch_figma module."""
    print('[5/5] Fetching Figma events...')
    try:
        projects = fetch_figma.fetch_projects()
        raw_events = fetch_figma.fetch_files_for_projects(projects)
        
        processed = []
        for e in raw_events:
            # We updated fetch_figma to include 'timestamp' (PST-aware)
            dt = e.get('timestamp')
            name = e.get('Name')
            if dt and name:
                 processed.append({'raw_name': name, 'timestamp': dt, 'app': 'Figma'})
        print(f'  Figma: {len(processed)} events')
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
    
    # Map names
    for e in all_events:
        raw = e['raw_name']
        e['name'] = map_name(raw)
    
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
    
    # Sort by date (newest first), then by name
    result_df['sort_dt'] = pd.to_datetime(result_df['Date'], format='%m/%d/%y')
    result_df = result_df.sort_values(by=['sort_dt', 'Team Member'], ascending=[False, True])
    result_df = result_df.drop(columns=['sort_dt'])
    
    print(f'Final rows: {len(result_df)}')
    
    # Upload to Google Sheets
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    try:
        ws = sh.worksheet('Activity Time Analysis')
        ws.clear()
    except:
        ws = sh.add_worksheet(title='Activity Time Analysis', rows=5000, cols=10)
    
    values = [result_df.columns.tolist()] + result_df.values.tolist()
    ws.update(values=values, range_name='A1')
    
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
                'type': e.get('event_type', e.get('Action', 'Unknown'))
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

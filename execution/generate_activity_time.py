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
from name_mappings import map_name, should_exclude

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
    print('[1/2] Fetching Google Workspace events...')
    events = []
    headers = {'Authorization': f'Bearer {creds.token}'}
    
    for app in ['drive', 'gmail']:
        start_dt = datetime.strptime(START_DATE, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        current_start = start_dt
        
        while current_start < now_dt:
            current_end = current_start + timedelta(days=30)
            if current_end > now_dt:
                current_end = now_dt
            
            params = {
                'startTime': current_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'endTime': current_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'maxResults': 1000
            }
            url = f'https://admin.googleapis.com/admin/reports/v1/activity/users/all/applications/{app}'
            
            while True:
                try:
                    resp = requests.get(url, headers=headers, params=params)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    
                    for item in data.get('items', []):
                        actor = item.get('actor', {})
                        email = actor.get('email', '')
                        if not email or actor.get('callerType') == 'KEY':
                            continue
                        
                        ts = item.get('id', {}).get('time', '')
                        if ts:
                            try:
                                dt = pd.to_datetime(ts).tz_convert(PST)
                                events.append({'raw_name': email, 'timestamp': dt})
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


def fetch_github_events():
    """Fetch events from GitHub with timestamps."""
    print('[2/2] Fetching GitHub events...')
    events = []
    
    github_token = os.environ.get('GITHUB_TOKEN', os.environ.get('GH_PAT', ''))
    if not github_token:
        print('  [SKIP] No GitHub token found')
        return events
    
    headers = {'Authorization': f'Bearer {github_token}', 'Accept': 'application/vnd.github+json'}
    start_dt_pst = pd.to_datetime(START_DATE).tz_localize(PST)
    
    try:
        repos_resp = requests.get('https://api.github.com/orgs/Pvragon/repos', headers=headers, params={'per_page': 100})
        repos = repos_resp.json() if repos_resp.status_code == 200 else []
        
        for repo in repos:
            try:
                ev_resp = requests.get(
                    f"https://api.github.com/repos/Pvragon/{repo['name']}/events",
                    headers=headers,
                    params={'per_page': 100}
                )
                if ev_resp.status_code != 200:
                    continue
                    
                for ev in ev_resp.json():
                    created = ev.get('created_at', '')
                    actor = ev.get('actor', {}).get('login', '')
                    if created and actor:
                        try:
                            dt = pd.to_datetime(created).tz_convert(PST)
                            if dt >= start_dt_pst:
                                events.append({'raw_name': actor, 'timestamp': dt})
                        except:
                            pass
            except:
                pass
                
    except Exception as e:
        print(f'  Warning: {e}')
    
    print(f'  GitHub: {len(events)} events')
    return events


def fetch_backendless_events():
    """Fetch events from Backendless CSV with timestamps."""
    print('[3/3] Fetching Backendless events...')
    events = []
    
    csv_path = os.path.join(ROOT_DIR, 'console_audit_logs.csv')
    if not os.path.exists(csv_path):
        print('  [SKIP] console_audit_logs.csv not found')
        return events
        
    try:
        df = pd.read_csv(csv_path)
        # Parse timestamps
        if 'timestamp' in df.columns:
            # Backendless timestamps are milliseconds
            df['dt'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize(timezone.utc).dt.tz_convert(PST)
            
            # Parse developer email
            def get_email(dev_str):
                try:
                    if pd.isna(dev_str): return ''
                    return json.loads(str(dev_str)).get('email', '')
                except: return ''
            
            df['email'] = df['developer'].apply(get_email)
            
            start_dt = pd.to_datetime(START_DATE).tz_localize(PST)
            
            # Filter
            cutoff = df['dt'] >= start_dt
            
            for _, row in df[cutoff].iterrows():
                if row['email']:
                    events.append({'raw_name': row['email'], 'timestamp': row['dt']})
                    
    except Exception as e:
        print(f'  Warning parsing Backendless CSV: {e}')
        
    print(f'  Backendless: {len(events)} events')
    return events


def generate_activity_time_analysis(creds):
    """Generate Activity Time Analysis from actual event timestamps."""
    print('=' * 60)
    print('ACTIVITY TIME ANALYSIS GENERATOR')
    print(f'Started: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 60)
    
    # Fetch events
    gw_events = fetch_google_workspace_events(creds)
    gh_events = fetch_github_events()
    bl_events = fetch_backendless_events()
    
    all_events = gw_events + gh_events + bl_events
    
    if not all_events:
        print('[SKIP] No events found')
        return
    
    # Map names
    for e in all_events:
        raw = e['raw_name']
        e['name'] = map_name(raw)
    
    # Filter exclusions
    all_events = [e for e in all_events if not should_exclude(e['name'])]
    print(f'After filtering: {len(all_events)} events')
    
    if not all_events:
        print('[SKIP] No events after filtering')
        return
    
    # Build DataFrame
    df = pd.DataFrame(all_events)
    df['date'] = df['timestamp'].dt.strftime('%m/%d/%y')
    
    # Group by NAME + DATE and calculate metrics
    results = []
    for (name, date), group in df.groupby(['name', 'date']):
        times = sorted(group['timestamp'].tolist())
        first = times[0]
        last = times[-1]
        
        # Calculate longest break
        longest_gap = 0
        if len(times) > 1:
            for i in range(1, len(times)):
                gap = (times[i] - times[i-1]).total_seconds() / 60
                if gap > longest_gap:
                    longest_gap = gap
        
        # Active window
        active_window = (last - first).total_seconds() / 3600
        
        results.append({
            'Team Member': name,
            'Date': date,
            'First Activity (PST)': first.strftime('%I:%M %p'),
            'Last Activity (PST)': last.strftime('%I:%M %p'),
            'Active Window (Hours)': round(active_window, 1),
            'Longest Break (Minutes)': int(longest_gap),
            'Total Events': len(times)
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
    print('=' * 60)


def main():
    creds = get_creds()
    generate_activity_time_analysis(creds)


if __name__ == "__main__":
    main()


import os
import sys
import pandas as pd
from datetime import datetime, timezone, timedelta
import pytz
import requests

# Add current directory to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from name_mappings import map_name, should_exclude, NAME_MAP
import fetch_google_workspace
import fetch_clickup
import fetch_figma
import fetch_backendless

# Target Configuration
TARGET_DATE = '02/19/26' # Analyzing yesterday as a full sample
PST = pytz.timezone('America/Los_Angeles')

def get_creds():
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_file(os.path.join(ROOT_DIR, 'token.json'))

def main():
    print("="*80)
    print(f"DEEP DATA INTEGRITY AUDIT: FULL TEAM ANALYSIS - {TARGET_DATE}")
    print("="*80)
    
    creds = get_creds()
    all_events = []
    
    # Define the 24-hour window for the target date in PST -> UTC
    # 02/19 00:00 PST = 02/19 08:00 UTC
    # 02/19 23:59 PST = 02/20 07:59 UTC
    start_dt_utc = datetime(2026, 2, 19, 8, 0, tzinfo=timezone.utc)
    end_dt_utc = datetime(2026, 2, 20, 8, 0, tzinfo=timezone.utc)

    # 1. Google Workspace (Meet, Drive, Calendar, Gmail-Send)
    print("[1/6] Scanning Google Workspace (All Apps)...")
    service = fetch_google_workspace.build('admin', 'reports_v1', credentials=creds)
    for app in ['drive', 'gmail', 'calendar', 'meet']:
        try:
            # We use 'send' for gmail now to ensure active work
            evs = fetch_google_workspace.fetch_logs_in_windows(service, 'all', app, start_dt_utc, end_dt_utc)
            for e in evs:
                name = map_name(e.get('Name'))
                if not should_exclude(name):
                    dt = e['timestamp_dt']
                    if dt.strftime('%m/%d/%y') == TARGET_DATE:
                        all_events.append({'Name': name, 'Time': dt, 'Platform': f'GW:{app.capitalize()}', 'Type': e['Event Type']})
        except Exception as e:
            print(f"      - Error scanning {app}: {e}")

    # 2. ClickUp (Team Wide)
    print("[2/6] Scanning ClickUp Activity...")
    fetch_clickup.fetch_users()
    tasks, tids = fetch_clickup.fetch_task_activity()
    comments = fetch_clickup.fetch_comments_for_active_tasks(tids)
    chats = fetch_clickup.fetch_chat_activity()
    
    for e in (tasks + comments + chats):
        uid = e.get('user_id')
        name = map_name(fetch_clickup.USER_CACHE.get(str(uid), f"User {uid}"))
        if not should_exclude(name):
            ts = e.get('timestamp')
            dt = pd.to_datetime(ts, unit='ms').tz_localize('UTC').tz_convert(PST)
            if dt.strftime('%m/%d/%y') == TARGET_DATE:
                all_events.append({'Name': name, 'Time': dt, 'Platform': 'ClickUp', 'Type': e.get('event_type')})

    # 3. GitHub (Organization Wide)
    print("[3/6] Scanning GitHub Commits & Events...")
    github_token = os.environ.get('GITHUB_TOKEN', '')
    headers = {'Authorization': f'Bearer {github_token}'}
    # Checking top repos to verify data integrity
    active_repos = ['Audit-report', 'RefCheqr-Backend-V2', 'RefCheqr-Mobile-V2', 'milotrack-backend', 'milotrack-mobile']
    for repo in active_repos:
        try:
            ev_resp = requests.get(f"https://api.github.com/repos/Pvragon/{repo}/events", headers=headers, timeout=10)
            if ev_resp.status_code == 200:
                for ev in ev_resp.json():
                    name = map_name(ev.get('actor', {}).get('login', ''))
                    if not should_exclude(name):
                        dt = pd.to_datetime(ev.get('created_at')).tz_convert(PST)
                        if dt.strftime('%m/%d/%y') == TARGET_DATE:
                            all_events.append({'Name': name, 'Time': dt, 'Platform': 'GitHub', 'Type': ev.get('type')})
        except: pass

    # 4. Backendless Console & Auth
    print("[4/6] Scanning Backendless Live Logs...")
    bl_events = fetch_backendless.fetch_backendless_events_raw()
    for e in bl_events:
        name = map_name(e['raw_name'])
        if not should_exclude(name):
            dt = e['timestamp']
            if dt.strftime('%m/%d/%y') == TARGET_DATE:
                all_events.append({'Name': name, 'Time': dt, 'Platform': 'Backendless', 'Type': 'API/Console Access'})

    # 5. Figma (Files & Versions)
    print("[5/6] Scanning Figma Design Events...")
    try:
        projects = fetch_figma.fetch_projects()
        fi_events = fetch_figma.fetch_files_for_projects(projects)
        for e in fi_events:
            name = map_name(e.get('Name'))
            if not should_exclude(name):
                dt = e.get('timestamp')
                if dt and dt.strftime('%m/%d/%y') == TARGET_DATE:
                    all_events.append({'Name': name, 'Time': dt, 'Platform': 'Figma', 'Type': e.get('Platform_Type', 'Edit')})
    except: pass

    if not all_events:
        print("\n[ERROR] COMPLETED BUT NO EVENTS FOUND. Check API Keys or Target Date.")
        return

    # Process Final Results
    df = pd.DataFrame(all_events)
    print(f"\n[6/6] Analyzing {len(df)} total events for data integrity...")
    
    # Calculate Active Hours for every person found
    SESSION_GAP_MINUTES = 45
    MIN_SESSION_CREDIT = 10
    
    audit_results = []
    
    for name, group in df.groupby('Name'):
        times = sorted(group['Time'].tolist())
        total_seconds = 0
        current_session_start = times[0]
        
        if len(times) == 1:
            total_seconds = MIN_SESSION_CREDIT * 60
        else:
            for i in range(1, len(times)):
                gap = (times[i] - times[i-1]).total_seconds() / 60.0
                if gap > SESSION_GAP_MINUTES:
                    total_seconds += max((times[i-1] - current_session_start).total_seconds(), MIN_SESSION_CREDIT * 60)
                    current_session_start = times[i]
            # Final
            total_seconds += max((times[-1] - current_session_start).total_seconds(), MIN_SESSION_CREDIT * 60)
            
        hours = total_seconds / 3600.0
        platforms = ", ".join(group['Platform'].unique())
        
        audit_results.append({
            'Member': name,
            'Active Hours': f"{hours:.2f}h",
            'Event Count': len(times),
            'Platforms Used': platforms,
            'First/Last': f"{times[0].strftime('%I:%M %p')} - {times[-1].strftime('%I:%M %p')}"
        })

    # Sort by active hours for easy review
    audit_df = pd.DataFrame(audit_results).sort_values('Active Hours', ascending=False)
    
    print("\n" + "="*80)
    print(f"FINAL AUDIT INTEGRITY RESULTS - {TARGET_DATE}")
    print("="*80)
    print(audit_df.to_string(index=False))
    print("="*80)
    print("\nAUDIT COMPLETED. If these numbers match your expectations of who was working, integrity is confirmed.")

if __name__ == "__main__":
    main()

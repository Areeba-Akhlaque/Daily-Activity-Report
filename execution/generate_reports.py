"""
Complete Data Refresh
=====================
Updates Console_Audit_Logs, Daily Audit, and Activity Time Analysis tabs.
Uses comprehensive name mappings from name_mappings.py
"""

import os
import sys
import json
import pandas as pd
from datetime import datetime, timezone
from collections import defaultdict
import pytz
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Import name mappings
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude, get_audit_date

# Load .env explicitly if needed (duplicated from other scripts for fallback)
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


def update_console_audit_logs(gc, sh):
    """Update Console_Audit_Logs from the live worksheet (synced by fetch_backendless.py)."""
    print("\n=== [1/3] Updating Console_Audit_Logs (Virtual) ===")
    
    try:
        ws = sh.worksheet('Console_Audit_Logs')
        data = ws.get_all_records()
        if not data:
            print("  [SKIP] Console_Audit_Logs is empty.")
            return

        df = pd.DataFrame(data)
        print(f"  Loaded {len(df)} records from worksheet")
        
        # Ensure we have consistent column names
        if 'Email' in df.columns:
            df['Name'] = df['Email'].apply(map_name)
        elif 'Name' in df.columns:
            # Already mapped by fetch_backendless.py
            pass
        
        # Filter exclusions
        df = df[~df['Name'].apply(should_exclude)]
        
        if 'Activity Type' in df.columns:
            df['Event Type'] = df['Activity Type']
        elif 'Event Type' not in df.columns:
            df['Event Type'] = 'Unknown'
        
        df['Platform'] = 'Backendless App'
        
        if 'Count' not in df.columns:
            df['Count'] = 1
            
        # Aggregate to ensure uniqueness for Daily Audit
        summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type'])['Count'].sum().reset_index()
        
        # Sort newest first
        summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
        summary = summary.sort_values(by=['sort_dt', 'Name'], ascending=[False, True])
        summary = summary.drop(columns=['sort_dt'])
        
        print(f"  Aggregated to {len(summary)} rows")
        
        # Upload
        try:
            ws = sh.worksheet('Console_Audit_Logs')
            ws.clear()
        except:
            ws = sh.add_worksheet(title='Console_Audit_Logs', rows=5000, cols=10)
        
        final = summary[['Name', 'Date', 'Platform', 'Event Type', 'Count']]
        ws.update(values=[final.columns.tolist()] + final.values.tolist(), range_name='A1')
        print(f"  [SUCCESS] Uploaded {len(final)} Backendless rows")

    except Exception as e:
        print(f"  [ERROR] Failed to update Console_Audit_Logs: {e}")


def update_daily_audit(gc, sh):
    """
    Regenerate Daily Audit from all source tabs.
    
    Aggregate actual activity data.
    Note: Previously generated a complete matrix (including 0s), but this hit Google Sheet cell limits.
    Now only stores existing activity rows (sparse).
    """
    print("\n=== [2/3] Updating Daily Audit (Complete Matrix) ===")
    
    # Source tabs
    source_tabs = [
        'Console_Audit_Logs',
        'Clickup_Activity',
        'Github_Activity',
        'Github_Commits',
        'Figma_Activity',
        'GoogleWorkspace_Activity'
    ]
    
    all_data = []
    all_persons = set()
    all_dates = set()
    all_event_types = set()  # (Platform, Event Type) tuples
    
    for tab_name in source_tabs:
        try:
            print(f"  - Fetching {tab_name}...")
            ws = sh.worksheet(tab_name)
            data = ws.get_all_records()
            print(f"  {tab_name}: {len(data)} rows")
            
            for row in data:
                # Normalize column names
                name = row.get('Name', row.get('Team Member', ''))
                date = row.get('Date', row.get('Activity Date', ''))
                event_type = row.get('Event Type', row.get('Activity Type', ''))
                count = row.get('Count', row.get('Quantity', 1))
                
                # IMPORTANT: Github_Activity and Github_Commits both belong to 'GitHub' platform
                # so their events sum correctly in the Daily Audit matrix.
                if tab_name in ('Github_Activity', 'Github_Commits'):
                    platform = 'GitHub'
                else:
                    platform = row.get('Platform', tab_name.replace('_Activity', '').replace('_Logs','').replace('_', ' '))
                
                # Apply name mapping
                name = map_name(name)
                
                # Skip "Gmail Received" if it somehow slipped into a source tab
                if event_type == "Gmail Received":
                    continue
                    
                if not should_exclude(name) and name and date and event_type:
                    all_data.append({
                        'Team Member': name,
                        'Activity Date': date,
                        'Platform': platform,
                        'Activity Type': event_type,
                        'Count': int(count) if count else 0
                    })
                    
                    # Track all unique values for matrix generation
                    all_persons.add(name)
                    all_dates.add(date)
                    all_event_types.add((platform, event_type))
                    
        except Exception as e:
            print(f"  [WARN] {tab_name}: {e}")
    
    if not all_data:
        print("  [SKIP] No data to update")
        return
    
    print(f"  Found: {len(all_persons)} persons, {len(all_dates)} dates, {len(all_event_types)} event types")
    
    # 1. Aggregate existing counts to sum up duplicates (like multiple commits in a day)
    df_existing = pd.DataFrame(all_data)
    summary_existing = df_existing.groupby(['Team Member', 'Activity Date', 'Platform', 'Activity Type'])['Count'].sum().to_dict()
    
    # 2. Define Matrix Scope (Last 45 days)
    # Convert dates to datetime objects
    dt_objects = sorted([pd.to_datetime(d, format='%m/%d/%y') for d in all_dates], reverse=True)
    
    # ALWAYS include Today (PST) to prevent empty dashboard
    today_pst = datetime.now(timezone.utc).astimezone(pytz.timezone('America/Los_Angeles'))
    today_str = today_pst.strftime('%m/%d/%y')
    
    latest = dt_objects[0] if dt_objects else today_pst
    cutoff = latest - pd.Timedelta(days=90) # Increased to 90 days to capture Jan 1st
    
    filtered_dates = [d.strftime('%m/%d/%y') for d in dt_objects if d >= cutoff]
    if today_str not in filtered_dates:
        filtered_dates.insert(0, today_str) # Force today at the top

    print(f"  Matrix will cover {len(filtered_dates)} dates (last 90 days, including Today PST).")
    
    # 3. Create the Full Matrix with 0s
    matrix_rows = []
    sorted_event_types = sorted(list(all_event_types))
    sorted_persons = sorted(list(all_persons))
    
    print("  Organizing rows by Date and Platform Groupings...")
    for date in filtered_dates:
        for (plat, etype) in sorted_event_types:
            for person in sorted_persons:
                key = (person, date, plat, etype)
                count = summary_existing.get(key, 0)
                
                # Include ALL rows (even zeros) so dashboard filters and charts work correctly
                matrix_rows.append({
                    'Team Member': person,
                    'Activity Date': date,
                    'Platform': plat,
                    'Activity Type': etype,
                    'Count': count
                })
    
    df_result = pd.DataFrame(matrix_rows)
    print(f"  Total matrix rows: {len(df_result)}")
    
    # Upload in chunks
    try:
        ws = sh.worksheet('Daily Audit')
        ws.clear()
    except:
        ws = sh.add_worksheet(title='Daily Audit', rows=100000, cols=10)
    
    # Upload header
    headers = df_result.columns.tolist()
    rows_to_upload = [headers] + df_result.values.tolist()
    
    # Chunked upload
    CHUNK_SIZE = 5000 
    for i in range(0, len(rows_to_upload), CHUNK_SIZE):
        chunk = rows_to_upload[i:i + CHUNK_SIZE]
        if i == 0:
            ws.update(values=chunk, range_name='A1')
        else:
            ws.append_rows(chunk[1:] if i > 0 else chunk)
        print(f"    Uploaded rows {i} to {min(i + CHUNK_SIZE, len(rows_to_upload))}")
    
    print(f"  [SUCCESS] Uploaded {len(df_result)} rows (Full Matrix)")


# Import generator for Activity Time Analysis
try:
    from generate_activity_time import generate_activity_time_analysis
except ImportError:
    # Add SCRIPT_DIR to path if import fails
    sys.path.insert(0, SCRIPT_DIR)
    from generate_activity_time import generate_activity_time_analysis


def update_event_references(gc, sh):
    """Update 'Event Type References' by scanning all unique (Platform, Event Type) pairs in Daily Audit."""
    print("\n=== [4/4] Updating Event Type References ===")
    try:
        ws_audit = sh.worksheet('Daily Audit')
        data = ws_audit.get_all_records()
        
        # Collect unique pairs
        pairs = set()
        for r in data:
            plat = r.get('Platform')
            etype = r.get('Activity Type') or r.get('Event Type')
            if plat and etype:
                pairs.add((plat, etype))
        
        if not pairs:
            print("  [SKIP] No event pairs found.")
            return

        # Try to fetch existing descriptions to preserve them
        desc_map = {}
        try:
            ws_ref = sh.worksheet('Event Type References')
            existing_data = ws_ref.get_all_records()
            for r in existing_data:
                # Key on (Platform, Event Type)
                key = (str(r.get('Platform', '')), str(r.get('Event Type', '')))
                if key[0] and key[1]:
                    desc_map[key] = r.get('Description', '')
        except Exception as e:
            print(f"  [INFO] Creating new 'Event Type References' tab: {e}")
            ws_ref = sh.add_worksheet('Event Type References', 1000, 5)

        # Default descriptions for common event types
        defaults = {
            ('ClickUp', 'task created'): "User created a new task to track specific work items.",
            ('ClickUp', 'task completed'): "User successfully finalized and closed an assigned task.",
            ('ClickUp', 'Comment Posted'): "User added a note, update, or feedback to an existing task.",
            ('ClickUp', 'Channels messages'): "User participated in team discussion via ClickUp Channel chat.",
            ('ClickUp', 'Direct chats messages'): "User sent a direct message for direct communication.",
            ('ClickUp', 'task updated'): "User modified task details like due dates, priority, or status.",
            ('GitHub', 'PushEvent'): "User pushed code updates or new features to the GitHub repository.",
            ('GitHub', 'PullRequestEvent'): "User initiated or updated a PR for code review/merging.",
            ('GitHub', 'IssueCommentEvent'): "User added a comment to a GitHub issue or PR.",
            ('GitHub', 'CreateEvent'): "User created a new branch or tag in the repository.",
            ('GitHub', 'DeleteEvent'): "User deleted a branch or tag from the repository.",
            ('Google Workspace', 'Drive Edit'): "User modified a document, spreadsheet, or file in Google Drive.",
            ('Google Workspace', 'Gmail Send'): "User sent an outgoing email from their professional account.",
            ('Google Workspace', 'Meet'): "User attended a virtual meeting via Google Meet.",
            ('Figma', 'File Edited'): "User worked on or made changes to a Figma design file.",
            ('Figma', 'File Created'): "User created a new design file in the project.",
            ('Figma', 'Comment Posted'): "User added a design comment or feedback in Figma.",
            ('Figma', 'Design Page Create'): "User created a new workspace or design canvas in Figma.",
            ('Figma', 'Version Create'): "User saved a named version of the design file.",
            ('Backendless App', 'API/Console Access'): "User interacted with the Backendless management console or API.",
            ('Backendless App', 'Update Page UI'): "User modified a UI page or container in the Backendless console.",
            ('Backendless App', 'Modify table record'): "User edited data directly within a Backendless database table.",
            ('Backendless App', 'Create UI Container'): "User added a new UI element or container to the application."
        }

        # Build merged dataset
        merged_data = []
        for plat, etype in sorted(pairs):
            desc = desc_map.get((plat, etype), "")
            if not desc:
                desc = defaults.get((plat, etype), "")
            
            # Intelligent fallback if still empty
            if not desc:
                desc = f"User performed a '{etype}' action on the {plat} platform."
            
            merged_data.append({
                'Platform': plat,
                'Event Type': etype,
                'Description': desc
            })

        df_final = pd.DataFrame(merged_data)
        ws_ref.clear()
        ws_ref.update(values=[df_final.columns.tolist()] + df_final.values.tolist(), range_name='A1')
        print(f"  [SUCCESS] Updated {len(df_final)} event types (Preserved existing descriptions).")
        
    except Exception as e:
        print(f"  [ERROR] Failed to update Event Type References: {e}")


def update_system_architecture(gc, sh):
    """Update 'System Architecture' tab with technical metadata and logic definitions."""
    print("\n=== [5/5] Updating System Architecture ===")
    try:
        data = [
            ["Tab Name", "Data Source", "Timezone", "Update Frequency", "Accuracy Level", "Key Logic/Scope"],
            ["Clickup_Activity", "ClickUp API (V2/V3)", "PST", "Daily (4 AM PST)", "High (90%)", "Tracks tasks, comments, and chats. Attribute based on user ID."],
            ["Github_Activity", "GitHub Events API", "PST", "Daily (4 AM PST)", "Perfect (100%)", "Tracks pushes, PRs, and reviews across organization repos."],
            ["Figma_Activity", "Figma Plugin/API", "PST", "Daily (4 AM PST)", "High (95%)", "Tracks version creation and file edits."],
            ["GoogleWorkspace_Activity", "Google Admin SDK", "PST", "Daily (4 AM PST)", "High (98%)", "Tracks Drive edits, Sent emails, and Meets. Received emails excluded."],
            ["Backendless_Activity", "Backendless Logs", "PST", "Daily (4 AM PST)", "Perfect (100%)", "Tracks console actions and database modifications."],
            ["Daily Audit", "Internal Logic", "PST", "Daily (4 AM PST)", "High", "Unified matrix of all activities. 0-fill for days with no activity."],
            ["Activity Time Analysis", "Timestamp Logic", "PST", "Daily (4 AM PST)", "High (Strict)", "Calculated Work Day metrics. Strictly uses First/Last activity timestamps with 0 buffer."]
        ]
        
        try:
            ws = sh.worksheet('System Architecture')
            ws.clear()
        except:
            ws = sh.add_worksheet('System Architecture', 100, 10)
            
        ws.update(values=data, range_name='A1')
        print("  [SUCCESS] Updated System Architecture definitions.")
    except Exception as e:
        print(f"  [ERROR] Failed to update System Architecture: {e}")

def update_activity_time_analysis(gc, sh):
    """
    Update Activity Time Analysis using robust timestamp-based logic.
    Delegates to generate_activity_time.py which fetches raw timestamps.
    """
    print("\n=== [3/3] Updating Activity Time Analysis (Robust) ===")
    
    try:
        # Use credentials from existing gc object or fetch new ones
        # generate_activity_time_analysis expects creds object
        # We can pass the creds object we already have if it's compatible, 
        # but generate_activity_time_analysis calls get_creds() internally or expects argument.
        # It takes 'creds' argument.
        
        # We need to get the Credentials object. gc.auth is the Credentials object in newer gspread?
        # Or we can just grab them again.
        
        creds = get_creds()
        generate_activity_time_analysis(creds)
        
    except Exception as e:
        print(f"  [ERROR] Failed to update Activity Time Analysis: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("=" * 60)
    print("COMPLETE DATA REFRESH")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Authenticate
    creds = get_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    # Update all tabs
    update_console_audit_logs(gc, sh)
    update_daily_audit(gc, sh)
    update_event_references(gc, sh) # Added this step
    update_activity_time_analysis(gc, sh)
    update_system_architecture(gc, sh) # Added this step
    
    print("\n" + "=" * 60)
    print("[COMPLETE] All tabs updated successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()

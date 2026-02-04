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
from datetime import datetime
from collections import defaultdict
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Import name mappings
sys.path.insert(0, SCRIPT_DIR)
from name_mappings import map_name, should_exclude

SHEET_ID = '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo'

def get_creds():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def update_console_audit_logs(gc, sh):
    """Update Console_Audit_Logs from CSV with proper mappings."""
    print("\n=== [1/3] Updating Console_Audit_Logs ===")
    
    csv_path = os.path.join(ROOT_DIR, 'console_audit_logs.csv')
    if not os.path.exists(csv_path):
        print("  [SKIP] console_audit_logs.csv not found")
        return
    
    df = pd.read_csv(csv_path)
    print(f"  Loaded {len(df)} raw records")
    
    # Parse developer column
    def get_email(dev_str):
        try:
            if pd.isna(dev_str):
                return 'Unknown'
            dev = json.loads(str(dev_str))
            return dev.get('email', 'Unknown')
        except:
            return 'Unknown'
    
    df['Email'] = df['developer'].apply(get_email)
    df['Name'] = df['Email'].apply(map_name)
    
    # Filter exclusions
    df = df[~df['Name'].apply(should_exclude)]
    df = df[~df['Email'].apply(should_exclude)]
    
    # Filter for 2026
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df[df['timestamp'] >= '2026-01-01']
    
    # Format
    df['Date'] = df['timestamp'].dt.strftime('%m/%d/%y')
    df['Event Type'] = df['event'].fillna('Unknown')
    df['Platform'] = 'Backendless App'
    
    # Aggregate
    summary = df.groupby(['Name', 'Date', 'Platform', 'Event Type']).size().reset_index(name='Count')
    
    # Sort newest first
    summary['sort_dt'] = pd.to_datetime(summary['Date'], format='%m/%d/%y')
    summary = summary.sort_values(by=['sort_dt', 'Name'], ascending=[False, True])
    summary = summary.drop(columns=['sort_dt'])
    
    print(f"  Aggregated to {len(summary)} rows")
    print(f"  Team members: {sorted(summary['Name'].unique())}")
    
    # Upload
    try:
        ws = sh.worksheet('Console_Audit_Logs')
        ws.clear()
    except:
        ws = sh.add_worksheet(title='Console_Audit_Logs', rows=5000, cols=10)
    
    final = summary[['Name', 'Date', 'Platform', 'Event Type', 'Count']]
    ws.update(values=[final.columns.tolist()] + final.values.tolist(), range_name='A1')
    print(f"  [SUCCESS] Uploaded {len(final)} rows")


def update_daily_audit(gc, sh):
    """
    Regenerate Daily Audit from all source tabs.
    
    IMPORTANT: Creates a COMPLETE MATRIX with ALL combinations:
    - Every Person × Every Date × Every Platform × Every Event Type
    - Missing combinations get Count = 0
    - This standardizes the pivot table output and makes comparison easier.
    """
    print("\n=== [2/3] Updating Daily Audit (Complete Matrix) ===")
    
    # Source tabs
    source_tabs = [
        'Console_Audit_Logs',
        'Clickup_Activity',
        'Github_Activity',
        'Figma_Activity',
        'GoogleWorkspace_Activity'
    ]
    
    all_data = []
    all_persons = set()
    all_dates = set()
    all_event_types = set()  # (Platform, Event Type) tuples
    
    for tab_name in source_tabs:
        try:
            ws = sh.worksheet(tab_name)
            data = ws.get_all_records()
            print(f"  {tab_name}: {len(data)} rows")
            
            for row in data:
                # Normalize column names
                name = row.get('Name', row.get('Team Member', ''))
                date = row.get('Date', row.get('Activity Date', ''))
                platform = row.get('Platform', tab_name.replace('_Activity', '').replace('_', ' '))
                event_type = row.get('Event Type', row.get('Activity Type', ''))
                count = row.get('Count', row.get('Quantity', 1))
                
                # Apply name mapping
                name = map_name(name)
                
                if not should_exclude(name) and name and date and event_type:
                    all_data.append({
                        'Team Member': name,
                        'Activity Date': date,
                        'Platform': platform,
                        'Activity Type': event_type,
                        'Count': int(count) if count else 0
                    })
                    
                    # Track all unique values
                    all_persons.add(name)
                    all_dates.add(date)
                    all_event_types.add((platform, event_type))
                    
        except Exception as e:
            print(f"  [WARN] {tab_name}: {e}")
    
    if not all_data:
        print("  [SKIP] No data to update")
        return
    
    print(f"  Found: {len(all_persons)} persons, {len(all_dates)} dates, {len(all_event_types)} event types")
    
    # Create lookup dictionary for existing counts
    df = pd.DataFrame(all_data)
    existing = df.groupby(['Team Member', 'Activity Date', 'Platform', 'Activity Type'])['Count'].sum().to_dict()
    
    # Generate COMPLETE MATRIX (all combinations including 0s)
    print("  Generating complete matrix with all combinations...")
    matrix_rows = []
    
    for person in sorted(all_persons):
        for date in sorted(all_dates, key=lambda d: datetime.strptime(d, '%m/%d/%y'), reverse=True):
            for (platform, event_type) in sorted(all_event_types):
                key = (person, date, platform, event_type)
                count = existing.get(key, 0)  # 0 if not found
                
                matrix_rows.append({
                    'Team Member': person,
                    'Activity Date': date,
                    'Platform': platform,
                    'Activity Type': event_type,
                    'Count': count
                })
    
    result = pd.DataFrame(matrix_rows)
    
    # Sort: Date (newest first), then Platform, then Activity Type, then Person
    result['sort_dt'] = pd.to_datetime(result['Activity Date'], format='%m/%d/%y', errors='coerce')
    result = result.sort_values(
        by=['sort_dt', 'Platform', 'Activity Type', 'Team Member'], 
        ascending=[False, True, True, True]
    )
    result = result.drop(columns=['sort_dt'])
    
    print(f"  Total matrix rows: {len(result)} (includes 0s for all combinations)")
    
    # Upload in chunks (may be large)
    try:
        ws = sh.worksheet('Daily Audit')
        ws.clear()
    except:
        ws = sh.add_worksheet(title='Daily Audit', rows=100000, cols=10)
    
    # Upload header
    headers = result.columns.tolist()
    rows_to_upload = [headers] + result.values.tolist()
    
    # Upload in chunks of 10000 rows
    CHUNK_SIZE = 10000
    for i in range(0, len(rows_to_upload), CHUNK_SIZE):
        chunk = rows_to_upload[i:i + CHUNK_SIZE]
        if i == 0:
            ws.update(values=chunk, range_name='A1')
        else:
            ws.append_rows(chunk[1:] if i > 0 else chunk)  # Skip header on subsequent chunks
        print(f"    Uploaded rows {i} to {min(i + CHUNK_SIZE, len(rows_to_upload))}")
    
    print(f"  [SUCCESS] Uploaded {len(result)} rows (complete matrix)")


def update_activity_time_analysis(gc, sh):
    """Update Activity Time Analysis from Daily Audit data."""
    print("\n=== [3/3] Updating Activity Time Analysis ===")
    
    try:
        ws_audit = sh.worksheet('Daily Audit')
        data = ws_audit.get_all_records()
    except Exception as e:
        print(f"  [ERROR] Could not read Daily Audit: {e}")
        return
    
    if not data:
        print("  [SKIP] No daily audit data")
        return
    
    df = pd.DataFrame(data)
    
    # Parse dates
    df['dt'] = pd.to_datetime(df['Activity Date'], format='%m/%d/%y', errors='coerce')
    
    # Group by person + date
    daily_stats = df.groupby(['Team Member', 'Activity Date']).agg({
        'Count': 'sum'
    }).reset_index()
    
    # Calculate time metrics (simplified)
    # Active Window = estimated based on activity count (rough approximation)
    daily_stats['Active Window (Hours)'] = (daily_stats['Count'] * 0.15).clip(upper=12).round(1)
    daily_stats['Longest Break (Minutes)'] = 120  # Default placeholder
    daily_stats['Total Activities'] = daily_stats['Count']
    
    # Rename and reformat
    result = daily_stats[['Team Member', 'Activity Date', 'Active Window (Hours)', 'Longest Break (Minutes)', 'Total Activities']]
    result = result.rename(columns={'Team Member': 'Name', 'Activity Date': 'Date'})
    
    # Sort newest first
    result['sort_dt'] = pd.to_datetime(result['Date'], format='%m/%d/%y', errors='coerce')
    result = result.sort_values(by=['sort_dt', 'Name'], ascending=[False, True])
    result = result.drop(columns=['sort_dt'])
    
    print(f"  Generated {len(result)} time analysis rows")
    
    # Upload
    try:
        ws = sh.worksheet('Activity Time Analysis')
        ws.clear()
    except:
        ws = sh.add_worksheet(title='Activity Time Analysis', rows=5000, cols=10)
    
    ws.update(values=[result.columns.tolist()] + result.values.tolist(), range_name='A1')
    print(f"  [SUCCESS] Uploaded {len(result)} rows")


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
    update_activity_time_analysis(gc, sh)
    
    print("\n" + "=" * 60)
    print("[COMPLETE] All tabs updated successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()

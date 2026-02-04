import requests
import json
import pandas as pd
from datetime import datetime
import os
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

CLICKUP_API_KEY = os.environ.get('CLICKUP_API_KEY', '')
WORKSPACE_ID = os.environ.get('CLICKUP_WORKSPACE_ID', '9011906822')
TEAM_ID = os.environ.get('CLICKUP_TEAM_ID', '9011906822')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
START_TS_MS = int(datetime.strptime(START_DATE_STR, "%Y-%m-%d").timestamp() * 1000)
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

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
                "date_updated_gt": START_TS_MS,
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
            for t in tasks:
                tid = t.get('id')
                task_ids.add(tid)
                
                # 1. task created
                d_c = int(t.get('date_created') or 0)
                if d_c >= START_TS_MS:
                    uid = str(t.get('creator', {}).get('id', ''))
                    events.append({"user_id": uid, "timestamp": d_c, "event_type": "task created"})
                
                # 2. task completed
                d_done = t.get('date_done') or t.get('date_closed')
                if d_done:
                    d_d_int = int(d_done)
                    if d_d_int >= START_TS_MS:
                        # Best effort: Attribute to first assignee or creator
                        assignees = t.get('assignees', [])
                        uid = str(assignees[0].get('id')) if assignees else str(t.get('creator', {}).get('id', ''))
                        events.append({"user_id": uid, "timestamp": d_d_int, "event_type": "task completed"})
                
                # 3. task updated (based on last update)
                d_u = int(t.get('date_updated') or 0)
                if d_u >= START_TS_MS:
                    # Avoid double counting exact same timestamp as creation or completion?
                    # Actually, if date_updated > date_created, it's an update event.
                    if d_u > d_c and d_u != (int(d_done or 0)):
                        assignees = t.get('assignees', [])
                        uid = str(assignees[0].get('id')) if assignees else str(t.get('creator', {}).get('id', ''))
                        events.append({"user_id": uid, "timestamp": d_u, "event_type": "task updated"})

                # 4. Comments (These are always attributed correctly)
                # Note: We still fetch comments as they are reliable user-attributed logs.
                # However, fetching individual task comments is slow. We'll do it selectively.
                # In this loop we only fetched tasks updated in 2026, so it's a good subset.
                
            page += 1
            if page > 100: break
        except Exception as e:
            print(f"  Error: {e}"); break
            
    return events, list(task_ids)

def fetch_comments_for_active_tasks(task_ids):
    print(f"  Fetching comments for {len(task_ids)} relevant tasks...")
    events = []
    count = 0
    for tid in task_ids:
        try:
            url = f"https://api.clickup.com/api/v2/task/{tid}/comment"
            resp = requests.get(url, headers=get_headers_v2())
            if resp.status_code == 200:
                comments = resp.json().get('comments', [])
                for c in comments:
                    c_date = int(c.get('date', 0))
                    if c_date >= START_TS_MS:
                        uid = str(c.get('user', {}).get('id', ''))
                        events.append({"user_id": uid, "timestamp": c_date, "event_type": "Comment Posted"})
            
            count += 1
            if count % 50 == 0: print(f"    Checked comments for {count}/{len(task_ids)} tasks...")
            # Respect rate limits
            if count % 10 == 0: time.sleep(0.1)
        except: pass
    return events

def fetch_chat_activity():
    print("[3/4] Fetching Chat Activity (Channels & Direct Chats)...")
    events = []
    try:
        cursor = ""
        while True:
            url = f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}/chat/channels"
            resp = requests.get(url, headers=get_headers_v3(), params={"cursor": cursor} if cursor else {})
            if resp.status_code != 200: break
            data = resp.json()
            channels = data.get('data', []) or data.get('channels', [])
            for c in channels:
                cid = c.get('id')
                ctype = str(c.get('type')).upper()
                event_type = "Channels messages" if ctype == "CHANNEL" else "Direct chats messages"
                
                msg_cursor = ""
                while True:
                    m_resp = requests.get(f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}/chat/channels/{cid}/messages", 
                                          headers=get_headers_v3(), 
                                          params={"cursor": msg_cursor} if msg_cursor else {})
                    if m_resp.status_code != 200: break
                    m_data = m_resp.json().get('data', [])
                    if not m_data: break
                    
                    stop_channel = False
                    for m in m_data:
                        m_ts = int(m.get('date', 0))
                        if m_ts < START_TS_MS: stop_channel = True; break
                        uid = str(m.get('user_id') or m.get('user', {}).get('id', ''))
                        events.append({"user_id": uid, "timestamp": m_ts, "event_type": event_type})
                        
                        if m.get('replies_count', 0) > 0:
                            mid = m.get('id')
                            r_resp = requests.get(f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}/chat/messages/{mid}/replies", headers=get_headers_v3())
                            if r_resp.status_code == 200:
                                for r in r_resp.json().get('data', []):
                                    r_ts = int(r.get('date', 0))
                                    if r_ts >= START_TS_MS:
                                        events.append({"user_id": str(r.get('user_id') or r.get('user', {}).get('id', '')), "timestamp": r_ts, "event_type": event_type})
                    
                    if stop_channel: break
                    msg_cursor = m_resp.json().get('next_cursor')
                    if not msg_cursor: break
            
            cursor = data.get('next_cursor')
            if not cursor: break
    except: pass
    return events

def process_and_upload(events):
    print("[4/4] Processing and Uploading...")
    if not events: print("  No events found."); return
    
    df = pd.DataFrame(events)
    df['Name'] = df['user_id'].apply(lambda x: USER_CACHE.get(str(x), f"User {x}"))
    df['Date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.strftime('%m/%d/%y')
    
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
        try: ws = sh.worksheet(tn); ws.clear()
        except: ws = sh.add_worksheet(tn, 1000, 20)
        ws.update(values=[final_df.columns.values.tolist()], range_name='A1')
        ws.append_rows(final_df.values.tolist())
        print(f"  [SUCCESS] Uploaded {len(final_df)} aggregate rows.")
        print("  Event breakdown:")
        print(df['event_type'].value_counts().to_string())
    except Exception as e: print(f"  [ERROR] {e}")

if __name__ == "__main__":
    fetch_users()
    tasks, task_ids = fetch_task_activity()
    comm_events = fetch_comments_for_active_tasks(task_ids)
    chats = fetch_chat_activity()
    process_and_upload(tasks + comm_events + chats)

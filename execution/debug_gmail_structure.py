import os
import sys
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime

# FAST DISCOVERY SCRIPT to find ANY Gmail event structure
# Iterates users until it hits data.

SCOPES = ['https://www.googleapis.com/auth/admin.reports.audit.readonly']
TARGET_USERS = ['bradd@pvragon.com', 'adriane@pvragon.com', 'jaime@pvragon.com', 'james@pvragon.com'] # Likely active users

def get_creds():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    return creds

def run():
    creds = get_creds()
    service = build('admin', 'reports_v1', credentials=creds)
    now = datetime.datetime.utcnow()
    start_time = (now - datetime.timedelta(days=29)).isoformat() + 'Z'
    end_time = now.isoformat() + 'Z'
    
    print(f"Searching for ANY Gmail event ({start_time} - {end_time})...")
    
    for user in TARGET_USERS:
        print(f"Checking {user}...")
        try:
            results = service.activities().list(userKey=user, applicationName='gmail', startTime=start_time, endTime=end_time).execute()
            items = results.get('items', [])
            if items:
                print(f"FOUND {len(items)} items for {user}!")
                first = items[0]
                print(json.dumps(first, indent=2))
                return # Found it!
            else:
                print("  No events.")
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == '__main__':
    run()

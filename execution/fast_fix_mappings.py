
import os
import sys
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from datetime import datetime

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from generate_reports import update_daily_audit, update_console_audit_logs

SHEET_ID = '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo'

def get_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds

def main():
    print("Running FAST fix for mappings (Console & Daily Audit only)...")
    creds = get_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    update_console_audit_logs(gc, sh)
    update_daily_audit(gc, sh)
    
    print("Fast fix complete.")

if __name__ == "__main__":
    main()

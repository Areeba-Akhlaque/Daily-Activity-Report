"""
Standalone Drive Chart Uploader
================================
Uploads the latest chart PNG from dashboard/charts/ to Google Drive.
Runs as a separate workflow step so failures are visible in Actions logs.
"""
import os
import sys
import glob
import traceback

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k] = v
load_env()

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

DRIVE_FOLDER_ID = '1MUvSw33n-PTpUkB6QwgQuJ5fEdLDNfKi'
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/gmail.send',
]

def main():
    print("=== Drive Chart Upload ===")

    # 1. Find latest chart file committed to repo
    charts_dir = os.path.join(ROOT_DIR, 'dashboard', 'charts')
    charts = sorted(glob.glob(os.path.join(charts_dir, 'audit_chart_*.png')), reverse=True)
    if not charts:
        print("[SKIP] No chart files found in dashboard/charts/")
        return

    chart_path = charts[0]
    chart_name = os.path.basename(chart_path)
    chart_size = os.path.getsize(chart_path)
    print(f"[CHART] Found: {chart_name} ({chart_size:,} bytes)")

    # 2. Load and validate credentials
    token_path = os.path.join(ROOT_DIR, 'token.json')
    if not os.path.exists(token_path):
        print(f"[ERROR] token.json not found at {token_path}")
        sys.exit(1)

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    except Exception as e:
        print(f"[ERROR] Could not load token.json: {e}")
        sys.exit(1)

    print(f"[AUTH] valid={creds.valid} | expired={creds.expired} | has_refresh={bool(creds.refresh_token)}")
    print(f"[AUTH] scopes={creds.scopes}")

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            print("[AUTH] Token expired, refreshing...")
            try:
                creds.refresh(Request())
                print(f"[AUTH] Refresh OK. valid={creds.valid}")
            except Exception as e:
                print(f"[ERROR] Token refresh failed: {e}")
                print(traceback.format_exc())
                print("[FIX] Update GOOGLE_TOKEN secret: run execution/refresh_google_token.py locally")
                sys.exit(1)
        else:
            print("[ERROR] No valid token and no refresh token.")
            sys.exit(1)

    # 3. Check if file already exists in Drive (skip if so — idempotent)
    try:
        drive = build('drive', 'v3', credentials=creds)

        existing = drive.files().list(
            q=f"name='{chart_name}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false",
            fields='files(id, name, createdTime)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute().get('files', [])

        if existing:
            print(f"[SKIP] {chart_name} already in Drive (ID: {existing[0]['id']}) — no duplicate upload")
            return

    except Exception as e:
        print(f"[WARN] Could not check for existing file: {e}")
        # Continue anyway and try to upload

    # 4. Upload
    try:
        print(f"[DRIVE] Uploading {chart_name} to folder {DRIVE_FOLDER_ID}...")
        media = MediaFileUpload(chart_path, mimetype='image/png', resumable=False)
        result = drive.files().create(
            body={'name': chart_name, 'parents': [DRIVE_FOLDER_ID]},
            media_body=media,
            fields='id, name, webViewLink',
            supportsAllDrives=True
        ).execute()

        file_id = result.get('id')
        view_link = result.get('webViewLink', f"https://drive.google.com/file/d/{file_id}/view")
        print(f"[SUCCESS] Chart uploaded to Drive!")
        print(f"[SUCCESS] File: {chart_name}")
        print(f"[SUCCESS] ID:   {file_id}")
        print(f"[SUCCESS] Link: {view_link}")

    except Exception as e:
        print(f"[ERROR] Drive upload failed: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        if '403' in str(e) or 'insufficientPermissions' in str(e):
            print("[FIX] GOOGLE_TOKEN secret is missing drive scope.")
            print("[FIX] Run execution/refresh_google_token.py locally → update GOOGLE_TOKEN secret")
        sys.exit(1)


if __name__ == '__main__':
    main()

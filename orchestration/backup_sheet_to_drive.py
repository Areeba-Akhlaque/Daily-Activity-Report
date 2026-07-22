"""Daily snapshot of the whole workbook to Google Drive.

The Sheet is the system's permanent archive: Google Admin Reports only serves ~180
days and that floor slides forward daily, so once a row is in the Sheet it is the
ONLY copy in existence. This script keeps an off-Sheet copy so a bad write, an
accidental tab delete, or a corrupted run can always be reversed.

Every tab is exported to CSV and the set is zipped — a zip of CSVs is roughly an
order of magnitude smaller than JSON, so a year of daily snapshots stays tiny.
One file per day; re-running the same day updates that file instead of piling up
duplicates.

Self-skipping: if Drive is unreachable or the folder is not shared with this
account, it logs and exits 0 so it can never block the audit.
"""
import os
import sys
import csv
import io
import zipfile
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)


def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k] = v


load_env()

import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/drive',
]

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
# Parent supplied by the owner. Starts with 0A => a shared drive, so every Drive
# call needs supportsAllDrives / includeItemsFromAllDrives.
BACKUP_PARENT_ID = os.environ.get('DRIVE_BACKUP_PARENT_ID', '0AGXVh_HBvJKwUk9PVA')
BACKUP_FOLDER_NAME = os.environ.get('DRIVE_BACKUP_FOLDER', 'Pvragon Sheet Backups')


def get_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    if not os.path.exists(token_path):
        print('[BACKUP] token.json missing — skipping.')
        return None
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def find_or_create_folder(drive, name, parent):
    q = (f"name = '{name}' and '{parent}' in parents "
         f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    res = drive.files().list(q=q, fields='files(id,name)', supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute()
    hits = res.get('files', [])
    if hits:
        return hits[0]['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent]}
    folder = drive.files().create(body=meta, fields='id', supportsAllDrives=True).execute()
    print(f"[BACKUP] Created folder '{name}' ({folder['id']})")
    return folder['id']


def main():
    if not SHEET_ID:
        print('[BACKUP] GOOGLE_SHEET_ID not set — skipping.')
        return 0
    creds = get_creds()
    if not creds:
        return 0

    try:
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        worksheets = sh.worksheets()
    except Exception as e:
        print(f'[BACKUP] Could not open the workbook: {e}')
        return 0

    buf = io.BytesIO()
    total_rows = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for ws in worksheets:
            try:
                values = ws.get_all_values()
            except Exception as e:
                print(f'  [WARN] {ws.title}: {e}')
                continue
            out = io.StringIO()
            csv.writer(out, lineterminator='\n').writerows(values)
            z.writestr(f'{ws.title}.csv', out.getvalue())
            total_rows += len(values)
            print(f'  {ws.title}: {len(values)} rows')
    data = buf.getvalue()

    stamp = datetime.now().strftime('%Y-%m-%d')
    fname = f'pvragon_sheet_backup_{stamp}.zip'
    print(f'[BACKUP] {len(worksheets)} tabs, {total_rows} rows -> {fname} ({len(data)/1024:.0f} KB)')

    try:
        drive = build('drive', 'v3', credentials=creds)
        folder_id = find_or_create_folder(drive, BACKUP_FOLDER_NAME, BACKUP_PARENT_ID)
        media = MediaInMemoryUpload(data, mimetype='application/zip', resumable=False)
        existing = drive.files().list(
            q=f"name = '{fname}' and '{folder_id}' in parents and trashed = false",
            fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute().get('files', [])
        if existing:
            drive.files().update(fileId=existing[0]['id'], media_body=media,
                                 supportsAllDrives=True).execute()
            print(f'[BACKUP] Updated existing {fname}')
        else:
            drive.files().create(body={'name': fname, 'parents': [folder_id]},
                                 media_body=media, fields='id',
                                 supportsAllDrives=True).execute()
            print(f'[BACKUP] Uploaded {fname}')
    except Exception as e:
        print(f'[BACKUP] Drive upload failed (audit unaffected): {e}')
        return 0

    return 0


if __name__ == '__main__':
    sys.exit(main())

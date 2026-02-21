import gspread
import os
import pandas as pd
from google.oauth2.credentials import Credentials
import pytz

SHEET_ID = '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo'
ROOT_DIR = "d:/OFC_WORK/new offc/James-pvragon/Audit-report"

creds = Credentials.from_authorized_user_file(os.path.join(ROOT_DIR, 'token.json'))
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

# Inspect Bilal's events from Console_Audit_Logs (raw source for current day usually)
# Actually, the best way is to run the generation logic and print Bilal's gaps.
try:
    from name_mappings import map_name, get_audit_date
    import fetch_backendless
    import fetch_figma
    # Mocking the fetch to avoid full network hit if possible, or just read the Sheet
    ws = sh.worksheet('Daily Audit')
    audit_data = ws.get_all_records()
    df = pd.DataFrame(audit_data)
    
    # Filter for Bilal and yesterday (Feb 20)
    target_date = "02/20/26"
    bilal_df = df[(df['Team Member'] == 'Bilal Munir') & (df['Activity Date'] == target_date)]
    print(f"Bilal Munir Events for {target_date}: {len(bilal_df)}")
    print(bilal_df[['Platform', 'Activity Type', 'Count']])

    # Since Daily Audit doesn't have minutes, we need the Analysis sheet or raw logs.
    ws_time = sh.worksheet('Activity Time Analysis')
    time_df = pd.DataFrame(ws_time.get_all_records())
    bilal_time = time_df[(time_df['Team Member'] == 'Bilal Munir') & (time_df['Date'] == target_date)]
    print("\nTime Analysis Row for Bilal:")
    print(bilal_time.to_dict('records'))

except Exception as e:
    print(f"Error: {e}")

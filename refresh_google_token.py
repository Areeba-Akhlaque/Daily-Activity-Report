
"""
REFRESH GOOGLE TOKEN
====================
Run this script LOCALLY to regenerate your 'token.json' with the correct permissions.

1. Ensure 'credentials.json' is in this folder.
2. Run this script: python refresh_google_token.py
3. A browser will open. Login with your Google Workspace Admin account.
4. Copy the content of the NEW 'token.json' file.
5. Update your GitHub Secret 'GOOGLE_TOKEN' with this new content.
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Scopes needed for Audit Logs and Sheets
SCOPES = [
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def main():
    creds = None
    # Load existing token if valid (unlikely if scopes changed)
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except:
            print("Old token invalid or incompatible.")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except:
                creds = None
        
        if not creds:
            if not os.path.exists('credentials.json'):
                print("ERROR: missing 'credentials.json'. Please download it from Google Cloud Console.")
                return

            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the new token
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    print("\nSUCCESS! 'token.json' has been updated.")
    print("Now open 'token.json', copy the entire content, and update your GitHub Secret 'GOOGLE_TOKEN'.")

if __name__ == '__main__':
    main()

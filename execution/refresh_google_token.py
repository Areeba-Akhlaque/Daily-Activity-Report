import os
import os.path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# The scopes required for your reports
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/drive'  # For uploading chart images to shared Drive
]

def main():
    creds = None
    # Check if we have an existing token
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If no valid credentials, let's log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token...")
            creds.refresh(Request())
        else:
            print("Starting new login flow...")
            if not os.path.exists('credentials.json'):
                print("Error: 'credentials.json' not found in this folder!")
                return
                
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the credentials for next time
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
        print("\n" + "="*40)
        print("SUCCESS! 'token.json' has been created.")
        print("="*40)
        print("Next steps:")
        print("1. Open the 'token.json' file.")
        print("2. Copy the entire text content.")
        print("3. Go to GitHub -> Settings -> Secrets and variables -> Actions.")
        print("4. Update your 'GOOGLE_TOKEN' secret with this new text.")

if __name__ == '__main__':
    main()

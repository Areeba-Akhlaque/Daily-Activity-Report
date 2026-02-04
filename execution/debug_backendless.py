
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv('BACKENDLESS_APP_ID')
API_KEY = os.getenv('BACKENDLESS_API_KEY')

# Backendless API Base URL (US Cluster)
BASE_URL = f"https://api.backendless.com/{APP_ID}/{API_KEY}"

def check_table(table_name):
    print(f"\nChecking table: {table_name}...")
    url = f"{BASE_URL}/data/{table_name}?pageSize=1"
    try:
        res = requests.get(url)
        if res.status_code == 200:
            data = res.json()
            print(f"[SUCCESS] Table '{table_name}' accessible!")
            print(f"Sample data: {json.dumps(data, indent=2)}")
            return True
        else:
            print(f"[FAILED] {res.status_code} - {res.text}")
            return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False

def main():
    if not APP_ID or not API_KEY:
        print("Missing Credentials in .env")
        return

    print("Probing Backendless API for Logs/Audits...")
    
    # Common table names for logs
    candidates = ['Log', 'Audit', 'ConsoleLog', 'Activity', 'StartTable'] 
    
    found = False
    for table in candidates:
        if check_table(table):
            found = True
            
    if not found:
        print("\nNo standard log tables found via Data API.")
        print("It implies logs are only in Console (Management API required or unavailable via Data API).")

if __name__ == "__main__":
    main()

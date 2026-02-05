
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv('BACKENDLESS_APP_ID')
API_KEY = os.getenv('BACKENDLESS_API_KEY')
CUSTOM = os.getenv('BACKENDLESS_API_URL', 'https://api.backendless.com').rstrip('/')

BASE_URL = f"{CUSTOM}/{APP_ID}/{API_KEY}"

print(f"Checking Access: {BASE_URL}")

def check(table):
    print(f"Probing {table}...")
    try:
        # 5 second timeout
        res = requests.get(f"{BASE_URL}/data/{table}?pageSize=1", timeout=5)
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            print(f"DATA FOUND in {table}!")
            try: print(json.dumps(res.json(), indent=2)) 
            except: pass
            return True
        else:
            print(f"Error: {res.text}")
    except Exception as e:
        print(f"Exception: {e}")
    return False

check('Audit')
check('Log')
check('ConsoleAudit')

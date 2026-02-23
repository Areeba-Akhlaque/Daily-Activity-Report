import requests
import os
import json
from datetime import datetime, timezone

# Load .env manually
def load_env():
    with open('.env') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ[k] = v

load_env()

APP_ID = os.environ.get('BACKENDLESS_APP_ID')
DEV_LOGIN = os.environ.get('BACKENDLESS_DEV_LOGIN')
DEV_PASSWORD = os.environ.get('BACKENDLESS_DEV_PASSWORD')
BASE_URL = "https://console.okridecare.com"

def debug_backendless():
    s = requests.Session()
    login_url = f"{BASE_URL}/console/home/login"
    res = s.post(login_url, json={'login': DEV_LOGIN, 'password': DEV_PASSWORD})
    if res.status_code != 200:
        print("Login failed")
        return
    
    auth_key = res.json().get('authKey')
    headers = {'auth-key': auth_key}
    
    # Audit logs URL
    url = f"{BASE_URL}/{APP_ID}/console/security/audit-logs"
    res = s.get(url, headers=headers)
    if res.status_code != 200:
        print("Fetch failed")
        return
    
    logs = res.json()
    print(f"Total logs: {len(logs)}")
    if logs:
        print(f"Sample log: {logs[0]}")
        ts = logs[0].get('created') or logs[0].get('timestamp')
        if ts:
            if ts > 9999999999: ts = ts / 1000.0
            dt = datetime.fromtimestamp(ts, timezone.utc)
            print(f"Sample Date (UTC): {dt}")

if __name__ == "__main__":
    debug_backendless()

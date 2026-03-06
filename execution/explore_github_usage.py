import requests
import os
import json

def load_env():
    if os.path.exists('.env'):
        with open('.env') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value

load_env()

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_ORG = os.environ.get('GITHUB_ORG', 'Pvragon')

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def explore_github_usage():
    print(f"Exploring GitHub Usage for Org: {GITHUB_ORG}")
    
    # 1. Copilot Usage (Requires Copilot for Business/Enterprise)
    print("\n--- Checking Copilot Usage ---")
    url_copilot = f"https://api.github.com/orgs/{GITHUB_ORG}/copilot/usage"
    r = requests.get(url_copilot, headers=headers)
    print(f"Copilot Usage API Status: {r.status_code}")
    if r.status_code == 200:
        print("SUCCESS: Copilot usage data found!")
        # print(json.dumps(r.json()[:1], indent=2))
    else:
        print(f"FAILED: {r.text}")

    # 2. Codespaces Usage (Billing API)
    print("\n--- Checking Codespaces Usage (Secret/Billing) ---")
    url_codespaces = f"https://api.github.com/orgs/{GITHUB_ORG}/settings/billing/codespaces"
    r = requests.get(url_codespaces, headers=headers)
    print(f"Codespaces Billing API Status: {r.status_code}")
    if r.status_code == 200:
        print("SUCCESS: Codespaces billing data found!")
        print(json.dumps(r.json(), indent=2))
    else:
        print(f"FAILED: {r.text}")

    # 3. Actions Usage (Billing API)
    print("\n--- Checking Actions Usage (Billing API) ---")
    url_actions = f"https://api.github.com/orgs/{GITHUB_ORG}/settings/billing/actions"
    r = requests.get(url_actions, headers=headers)
    print(f"Actions Billing API Status: {r.status_code}")
    if r.status_code == 200:
        print("SUCCESS: Actions usage data found!")
        print(json.dumps(r.json(), indent=2))

if __name__ == "__main__":
    explore_github_usage()

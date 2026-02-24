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

FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN', '')
FIGMA_TEAM_ID = os.environ.get('FIGMA_TEAM_ID', '')

headers = {"X-Figma-Token": FIGMA_TOKEN}

def check_members():
    # There is no direct "member list" API for teams in the public REST API 
    # unless you are an Enterprise user.
    # However, we can try to find them by seeing who has access to files.
    
    print(f"Checking access for team: {FIGMA_TEAM_ID}")
    
    # Let's try to get all teams the user belongs to
    url = "https://api.figma.com/v2/teams" # This is a guess or v1? Figma v1/teams/team_id
    # Actually, Figma doesn't have a "list my teams" endpoint in v1.
    
    # But wait, there's a workaround. We can fetch the user's own info.
    me_url = "https://api.figma.com/v1/me"
    me_r = requests.get(me_url, headers=headers)
    if me_r.status_code == 200:
        print(f"Me: {me_r.json().get('handle')} ({me_r.json().get('email')})")

if __name__ == "__main__":
    check_members()

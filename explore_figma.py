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

def explore():
    print(f"Exploring Team: {FIGMA_TEAM_ID}")
    
    # 1. Team Projects
    url = f"https://api.figma.com/v1/teams/{FIGMA_TEAM_ID}/projects"
    r = requests.get(url, headers=headers)
    print(f"Projects API Status: {r.status_code}")
    if r.status_code == 200:
        projects = r.json().get('projects', [])
        print(f"Found {len(projects)} projects:")
        for p in projects:
            print(f"  - {p['name']} (ID: {p['id']})")
    
    # 2. Files in the first project (or all)
    if projects:
        for p in projects:
            p_url = f"https://api.figma.com/v1/projects/{p['id']}/files"
            pr = requests.get(p_url, headers=headers)
            if pr.status_code == 200:
                files = pr.json().get('files', [])
                print(f"  Project '{p['name']}' has {len(files)} files:")
                for f in files:
                    print(f"    * {f['name']} (Key: {f['key']})")

if __name__ == "__main__":
    explore()

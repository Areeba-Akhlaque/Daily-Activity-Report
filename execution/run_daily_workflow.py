"""
Daily Workflow Orchestrator
===========================
Runs the complete daily audit pipeline.
See: directives/daily_workflow.md
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

def run_script(script_name, description):
    """Run a Python script and return success status."""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    print(f"\n{'='*60}")
    print(f"[RUNNING] {description}")
    print(f"Script: {script_name}")
    print('='*60)
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=ROOT_DIR,
            capture_output=False,
            text=True
        )
        if result.returncode == 0:
            print(f"[SUCCESS] {description}")
            return True
        else:
            print(f"[FAILED] {description} (exit code: {result.returncode})")
            return False
    except Exception as e:
        print(f"[ERROR] {description}: {e}")
        return False


def load_env():
    """Load environment variables from .env file manually."""
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        print(f"Loading environment from {env_path}")
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value
                    if 'BACKENDLESS' in key:
                         print(f"Loaded {key} from .env")

def main():
    load_env()
    start_time = datetime.now()
    print("\n" + "=" * 60)
    print("  PVRAGON DAILY ACTIVITY AUDIT")
    print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = {}
    
    # Step 1: Platform Data
    print("\n[STEP 1] FETCHING PLATFORM DATA")
    try:
        # Try running Node script for Backendless first (matches GitHub workflow)
        node_script = os.path.join(SCRIPT_DIR, 'fetch_backendless_node.js')
        if os.path.exists(node_script):
            print(f"  Running Node.js fetcher: {node_script}")
            subprocess.run(['node', node_script], cwd=ROOT_DIR, check=False)
    except Exception as e:
        print(f"  [WARN] Node script failed: {e}")

    results['clickup'] = run_script('fetch_clickup.py', 'ClickUp Activity')
    results['github'] = run_script('fetch_github.py', 'GitHub Activity')
    results['google'] = run_script('fetch_google_workspace.py', 'Google Workspace Activity')
    results['figma'] = run_script('fetch_figma.py', 'Figma Activity')
    results['backendless'] = run_script('fetch_backendless.py', 'Backendless Activity')
    
    # Step 1.5: Generate Reports (Daily Audit + Activity Time Analysis)
    print("\n[STEP 1.5] GENERATING REPORTS")
    results['reports'] = run_script('generate_reports.py', 'Daily Audit & Time Analysis')
    
    # Step 2: Refresh dashboard
    print("\n[STEP 2] REFRESHING DASHBOARD")
    results['dashboard'] = run_script('refresh_dashboard.py', 'Dashboard Data Export')
    
    # Step 3: Send email summary
    print("\n[STEP 3] SENDING EMAIL SUMMARY")
    results['email'] = run_script('send_daily_email.py', 'Daily Summary Email')
    
    # Summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    print("\n" + "=" * 60)
    print("WORKFLOW SUMMARY")
    print("=" * 60)
    
    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    
    for step, success in results.items():
        status = "[OK]" if success else "[FAIL]"
        print(f"  {status} {step}")
    
    print(f"\nCompleted: {success_count}/{total_count} steps successful")
    print(f"Duration: {duration:.1f} seconds")
    print(f"Finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if success_count < total_count:
        print("\n[WARN] Some steps failed. Check logs above for details.")
        sys.exit(1)
    else:
        print("\n[SUCCESS] All steps completed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()

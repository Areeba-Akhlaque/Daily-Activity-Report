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


def main():
    start_time = datetime.now()
    print("\n" + "=" * 60)
    print("🚀 PVRAGON DAILY ACTIVITY AUDIT")
    print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = {}
    
    # Step 1: Fetch platform data
    print("\n📡 STEP 1: FETCHING PLATFORM DATA")
    results['clickup'] = run_script('fetch_clickup.py', 'ClickUp Activity')
    results['github'] = run_script('fetch_github.py', 'GitHub Activity')
    results['google'] = run_script('fetch_google_workspace.py', 'Google Workspace Activity')
    results['figma'] = run_script('fetch_figma.py', 'Figma Activity')
    results['backendless'] = run_script('fetch_backendless.py', 'Backendless Activity')
    
    # Step 2: Refresh dashboard
    print("\n📊 STEP 2: REFRESHING DASHBOARD")
    results['dashboard'] = run_script('refresh_dashboard.py', 'Dashboard Data Export')
    
    # Step 3: Send email summary
    print("\n📧 STEP 3: SENDING EMAIL SUMMARY")
    results['email'] = run_script('send_daily_email.py', 'Daily Summary Email')
    
    # Summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    print("\n" + "=" * 60)
    print("📋 WORKFLOW SUMMARY")
    print("=" * 60)
    
    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    
    for step, success in results.items():
        status = "✅" if success else "❌"
        print(f"  {status} {step}")
    
    print(f"\nCompleted: {success_count}/{total_count} steps successful")
    print(f"Duration: {duration:.1f} seconds")
    print(f"Finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    if success_count < total_count:
        print("\n⚠️ Some steps failed. Check logs above for details.")
        sys.exit(1)
    else:
        print("\n🎉 All steps completed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()

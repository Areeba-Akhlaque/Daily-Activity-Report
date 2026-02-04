# Daily Workflow Orchestration

## Goal
Run the complete daily data fetch and reporting pipeline at 7:00 PM PST.

## Schedule
- **Trigger Time**: 7:00 PM PST daily
- **Data Window**: Previous 7:00 PM PST to current 6:59 PM PST (24 hours)

## Execution Order

### Step 1: Fetch Platform Data (Parallel)
Run these scripts to update source tabs:
1. `execution/fetch_clickup.py` → Clickup_Activity tab
2. `execution/fetch_github.py` → GitHub_Activity tab
3. `execution/fetch_google_workspace.py` → GoogleWorkspace_Activity tab
4. `execution/fetch_figma.py` → Figma_Activity tab
5. `execution/fetch_backendless.py` → Backendless_Activity tab

### Step 2: Generate Reports (Sequential)
After all fetches complete:
1. `execution/generate_audit_report.py` → Daily Audit tab
2. `execution/generate_time_analysis.py` → Activity Time Analysis tab

### Step 3: Refresh Dashboard
1. `execution/refresh_dashboard.py` → dashboard/data.json
2. Commit and push to GitHub (triggers GitHub Pages deploy)

### Step 4: Send Email Summary
1. `execution/send_daily_email.py` → Email to stakeholders

## GitHub Actions Workflow
Located at: `.github/workflows/daily-audit.yml`

```yaml
name: Daily Activity Audit
on:
  schedule:
    - cron: '0 3 * * *'  # 3:00 AM UTC = 7:00 PM PST
  workflow_dispatch:  # Manual trigger

jobs:
  fetch-and-report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run daily audit
        env:
          GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}
          # ... other secrets
        run: python execution/run_daily_workflow.py
      - name: Deploy dashboard
        run: |
          git config user.name github-actions
          git config user.email github-actions@github.com
          git add dashboard/data.json
          git commit -m "Update dashboard data" || true
          git push
```

## Error Handling
- If any fetch script fails, log error and continue with others
- If report generation fails, skip email but log error
- Always attempt to send email with whatever data is available

## Monitoring
- Workflow runs visible in GitHub Actions tab
- Email confirmation indicates successful run
- Dashboard "Last Updated" shows most recent refresh

## Learnings
- (Add learnings as the workflow matures)

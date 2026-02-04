# Refresh Dashboard

## Goal
Export Google Sheet data to JSON format for the interactive dashboard.

## Inputs
- `GOOGLE_SHEET_ID` - Source Google Sheet
- `token.json` - Google OAuth credentials

## Execution Script
`execution/refresh_dashboard.py`

## Process
1. Authenticate with Google Sheets API
2. Fetch "Daily Audit" tab data
3. Fetch "Activity Time Analysis" tab data
4. Build JSON structure with:
   - dailyAudit: array of all audit rows
   - timeAnalysis: array of time analysis rows
   - lastUpdated: timestamp
   - stats: summary statistics
5. Write to `dashboard/data.json`

## Outputs
- Creates/updates `dashboard/data.json`
- Dashboard auto-loads this file for visualizations

## Dashboard Features
The dashboard displays:
- Total Activities (filterable)
- Avg Active Hours/Day
- Avg Longest Break
- Active Team Members count
- Activity Trend chart (line)
- Platform Distribution chart (doughnut)
- Team Member Activity chart (horizontal bar)
- Active Hours Distribution chart (bar)
- Team Leaderboard table

## Edge Cases
- **Sheet API Error**: Retry once, then fail with clear error
- **Empty Data**: Create JSON with empty arrays, don't fail
- **Large Dataset**: Handle 40k+ rows efficiently

## Learnings
- 2026-02-04: Dashboard reads from local JSON, not live API (for speed)
- 2026-02-04: Auto-refresh in browser every 5 minutes

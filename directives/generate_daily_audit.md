# Generate Daily Audit Report

## Goal
Combine all platform activity data into a unified "Daily Audit" report matrix in Google Sheet.

## Inputs
- `GOOGLE_SHEET_ID` - Target Google Sheet
- Data from these source tabs:
  - Backendless_Activity
  - Clickup_Activity
  - Figma_Activity
  - GitHub_Activity
  - GoogleWorkspace_Activity

## Execution Script
`execution/generate_audit_report.py`

## Process
1. Read all 5 source tabs from Google Sheet
2. Extract unique: Team Members, Dates, Event Types (with Platform)
3. Build a matrix of all combinations
4. For each combination, lookup the count from source data
5. Apply professional column headers:
   - Name → Team Member
   - Date → Activity Date
   - Event Type → Activity Type
   - Quantity → Count
6. Sort by date descending, then platform, then activity type
7. Upload to "Daily Audit" tab

## Outputs
- Updates "Daily Audit" tab with columns:
  - Team Member, Activity Date, Platform, Activity Type, Count
- Creates complete matrix (no gaps - 0 for missing combinations)

## Name Standardization
Apply unified name mapping across all platforms:
- Bilal Mughal → Bilal Munir
- aleksandar.m.tanaskovic@gmail.com → Alexander Pavelko
- jkhereford@gmail.com → James Hereford
- etc.

## Excluded Names
Remove these from the audit:
- Kelly, Kelly Hereford
- System accounts (Build, Careers, Support, etc.)
- Bots (dependabot, vercel[bot])

## Edge Cases
- **Empty Source Tab**: Use 0 counts, don't fail
- **New Team Member**: Add to matrix automatically
- **New Event Type**: Add to matrix automatically

## Learnings
- 2026-02-04: Matrix approach ensures every combination has a row
- 2026-02-04: Professional headers requested by client

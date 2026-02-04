# Fetch Backendless App Activity

## Goal
Fetch Backendless console activity logs and upload aggregated counts to the Google Sheet.

## Inputs
- `BACKENDLESS_APP_ID` - Application ID (5EC56BA3-AA29-2AC1-FFEE-A7C07D146900)
- `BACKENDLESS_API_KEY` - REST API Key
- `GOOGLE_SHEET_ID` - Target Google Sheet
- `START_DATE` - Earliest date to fetch (2026-01-01)

## Execution Script
`execution/fetch_backendless.py`

## Process
1. Authenticate with Backendless REST API
2. Fetch console activity logs with pagination
3. Extract event types:
   - Create Table
   - Deploy Cloud Code Model From Console
   - Edit Custom Email Template
   - Edit Timer
   - Invite New Developer
   - Modify table record
   - Publish UI Container
   - Rename File
   - Reset Table Owner Permission
   - Run Timer
   - Save Cloud Code Draft Model
   - Update Function Logic
   - Update Page UI
   - Update UI Page Handler Logic
4. Map developer emails to names
5. Aggregate by: Name × Date × Event Type
6. Upload to "Backendless_Activity" tab in Google Sheet

## Outputs
- Updates "Backendless_Activity" tab with columns: Name, Date, Event Type, Count
- Prints summary of console events fetched

## Name Mapping
Map Backendless users to real names:
- (Add mappings based on API response format)

## Edge Cases
- **API Auth Failure**: Check API key validity
- **Pagination**: Handle offset-based pagination
- **Empty Logs**: Log warning, continue

## Learnings
- 2026-01-18: Console logs endpoint requires admin access
- 2026-02-01: Event types vary by action performed

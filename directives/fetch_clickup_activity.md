# Fetch ClickUp Activity

## Goal
Fetch all ClickUp activity (tasks created/updated/completed, comments, chat messages) and upload aggregated counts to the Google Sheet.

## Inputs
- `CLICKUP_API_KEY` - API authentication key
- `CLICKUP_WORKSPACE_ID` - Workspace to fetch from (9011906822)
- `GOOGLE_SHEET_ID` - Target Google Sheet
- `START_DATE` - Earliest date to fetch (2026-01-01)

## Execution Script
`execution/fetch_clickup.py`

## Process
1. Fetch workspace members and cache user ID → name mapping
2. Fetch all tasks (paginated) and extract:
   - Task Created events (from `date_created`)
   - Task Updated events (from `date_updated`)
   - Task Completed events (from `date_closed`)
3. Fetch comments for each task updated since START_DATE
4. Fetch chat activity (channels and direct messages)
5. Aggregate by: Name × Date × Event Type
6. Upload to "Clickup_Activity" tab in Google Sheet

## Outputs
- Updates "Clickup_Activity" tab with columns: Name, Date, Event Type, Count
- Prints summary of events fetched per type

## Name Mapping
Apply these mappings to unify names:
- Bilal Mughal → Bilal Munir
- Kelly, Kelly Hereford → EXCLUDE

## Edge Cases
- **API Rate Limit**: 100 requests/min - script has 0.5s delays between requests
- **Empty Task List**: Log warning, continue with other data sources
- **Comment Fetch Failure**: Log error, continue (don't fail entire run)
- **Pagination**: Handle `has_more` flag properly

## Learnings
- 2026-01-15: Task history endpoint requires Business plan, switched to state-based tracking
- 2026-02-03: Comments must be fetched per-task, not via bulk endpoint
- 2026-02-03: Chat messages require separate channels/direct endpoint calls

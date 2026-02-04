# Fetch Figma Activity

## Goal
Fetch Figma file update activity and upload aggregated counts to the Google Sheet.

## Inputs
- `FIGMA_TOKEN` - Figma Personal Access Token
- `FIGMA_TEAM_ID` - Team ID (1272292028369498498)
- `GOOGLE_SHEET_ID` - Target Google Sheet
- `START_DATE` - Earliest date to fetch (2026-01-01)

## Execution Script
`execution/fetch_figma.py`

## Process
1. Fetch all projects in the team
2. For each project, fetch all files
3. For each file, get version history to track updates
4. Only count "File Updated" events (when last_modified changes)
5. Map Figma user handles to real names
6. Aggregate by: Name × Date × "File Updated"
7. Upload to "Figma_Activity" tab in Google Sheet

## Outputs
- Updates "Figma_Activity" tab with columns: Name, Date, Event Type, Count
- Prints summary of files and updates found

## Name Mapping
Map Figma handles to real names as discovered:
- (Add mappings as they are discovered from API responses)

## Edge Cases
- **API Rate Limit**: Monitor response headers, add delays if needed
- **No Version History**: Some files may not have accessible history
- **Deleted Files**: Skip, don't fail
- **Empty Projects**: Continue to next project

## Learnings
- 2026-01-20: Figma API requires team-level access for file listing
- 2026-02-01: Version history endpoint provides user + timestamp

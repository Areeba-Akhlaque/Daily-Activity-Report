# Fetch GitHub Activity

## Goal
Fetch all GitHub activity from the Pvragon organization and upload aggregated counts to the Google Sheet.

## Inputs
- `GITHUB_TOKEN` - GitHub Personal Access Token
- `GITHUB_ORG` - Organization name (Pvragon)
- `GOOGLE_SHEET_ID` - Target Google Sheet
- `START_DATE` - Earliest date to fetch (2026-01-01)

## Execution Script
`execution/fetch_github.py`

## Process
1. Fetch all repositories in the organization
2. For each repository, fetch events (paginated, max 100 per page)
3. Map event types to readable names:
   - PushEvent → Code Pushed
   - CreateEvent → Branch/Tag Created
   - DeleteEvent → Branch/Tag Deleted
   - PullRequestEvent → PR Opened/Closed
   - IssueCommentEvent → Issue/PR Comment Posted
   - IssuesEvent → Issue Opened/Closed
   - PullRequestReviewCommentEvent → PR Comment Posted
4. Aggregate by: Name × Date × Event Type
5. Upload to "GitHub_Activity" tab in Google Sheet

## Outputs
- Updates "GitHub_Activity" tab with columns: Name, Date, Event Type, Count
- Prints summary of events fetched per repository

## Name Mapping
Apply these mappings to unify GitHub usernames to real names:
- bilalmunir985-oss → Bilal Munir
- javidal10 → Juan Vidal
- jkhereford → James Hereford
- saif72437 → Saifullah Khan
- Areeba-Akhlaque → Areeba Akhlaque
- dependabot[bot], vercel[bot] → EXCLUDE

## Edge Cases
- **API Rate Limit**: 5000 requests/hour - monitor X-RateLimit-Remaining header
- **Events API Limitation**: Only returns last 90 days of events
- **Empty Repository**: Skip, don't fail
- **Bot Accounts**: Filter out dependabot[bot], vercel[bot], etc.

## Learnings
- 2026-01-20: Events API only returns recent events, not full history
- 2026-02-01: Some repos have no events, handle gracefully

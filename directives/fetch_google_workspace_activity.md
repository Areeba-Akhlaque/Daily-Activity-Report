# Fetch Google Workspace Activity

## Goal
Fetch Google Workspace activity (Drive and Gmail) from the Admin Reports API and upload aggregated counts to the Google Sheet.

## Inputs
- `token.json` - Google OAuth token with Admin SDK access
- `GOOGLE_SHEET_ID` - Target Google Sheet
- `START_DATE` - Earliest date to fetch (2026-01-01)

## Execution Script
`execution/fetch_google_workspace.py`

## Process
1. Authenticate using Google OAuth credentials
2. Fetch Drive activity reports:
   - Filter for: create, edit, upload, rename actions
   - Map to: Drive create, Drive edit, Drive upload, Drive rename
3. Fetch Gmail activity reports:
   - Count received messages
   - EXCLUDE auto-generated emails from these domains:
     - github.com, mail.instagram.com, mg.upwork.com, shopify.com
     - notifications*.mailchimp.com, hubspotemail.net, amazonses.com
     - mandrillapp.com, sailthru.com, gainsightapp.com
     - cioeu109333.lovable.dev, triplewhale.com, geopod-ismtpd-*
4. Map email addresses to names
5. Aggregate by: Name × Date × Event Type
6. Upload to "GoogleWorkspace_Activity" tab in Google Sheet

## Outputs
- Updates "GoogleWorkspace_Activity" tab with columns: Name, Date, Event Type, Count
- Prints summary of Drive and Gmail events

## Name Mapping
Map @pvragon.com emails to full names:
- adriane@pvragon.com → Adriane Barredo
- alexander@pvragon.com → Alexander Pavelko
- areeba@pvragon.com → Areeba Akhlaque
- bilal@pvragon.com → Bilal Munir
- cherry@pvragon.com → Cherry Aznar
- farhan@pvragon.com → Muhammad Farhan
- jaime@pvragon.com → James Hereford
- etc.

Also map external emails:
- aleksandar.m.tanaskovic@gmail.com → Alexander Pavelko
- jkhereford@gmail.com → James Hereford
- bilalmunir985@gmail.com → Bilal Munir
- etc.

## Excluded Accounts
Do NOT include these system/service accounts:
- build@, careers@, employees@, support@
- gcp-organization-admins@, service-admins@
- rc-eng-notifications@, softstackers@
- Kelly, Kelly Hereford

## Edge Cases
- **Token Expired**: Auto-refresh using refresh_token
- **API Date Range**: Max 30 days per request - use pagination
- **Missing Email Actor**: Skip events with no email (system events)

## Learnings
- 2026-02-03: Gmail received must exclude marketing/transactional emails
- 2026-02-04: Personal emails (gmail.com) need mapping to team members

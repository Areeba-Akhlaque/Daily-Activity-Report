# Send Daily Summary Email

## Goal
Generate and send a daily activity summary email to stakeholders.

## Inputs
- `GOOGLE_SHEET_ID` - Source data
- `EMAIL_RECIPIENTS` - Comma-separated list (areeba@pvragon.com, jaime@pvragon.com)
- `token.json` - Google OAuth credentials (includes Gmail send permission)

## Execution Script
`execution/send_daily_email.py`

## Process
1. Authenticate with Gmail API
2. Fetch summary data from Google Sheet:
   - Total activities today
   - Active team members count
   - Top performers (by event count)
   - Platform breakdown
   - Any anomalies (unusually long breaks, etc.)
3. Generate HTML email with:
   - Header with date
   - Key metrics summary
   - Top performers list
   - Alerts/anomalies section
   - Quick links to Sheet and Dashboard
4. Send email to all recipients

## Email Template
```
📊 Daily Activity Report - [DATE]

HIGHLIGHTS:
✅ [X] Active Team Members Today
📈 Total Activities: [X]
⏰ Avg Active Hours: [X]h
🏆 Top Performers: [Name1] ([X]), [Name2] ([X]), [Name3] ([X])

⚠️ ALERTS:
- [Any anomalies or notable items]

🔗 QUICK LINKS:
• Google Sheet: [SHEET_URL]
• Live Dashboard: [DASHBOARD_URL]

Best,
Pvragon Activity Bot
```

## Outputs
- Sends HTML email to all recipients
- Prints confirmation with message ID

## Links to Include
- Google Sheet: https://docs.google.com/spreadsheets/d/1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo
- Dashboard: [GitHub Pages URL - to be configured]

## Edge Cases
- **Gmail API Error**: Log error, don't crash workflow
- **Empty Data**: Send email with "No activity recorded" message
- **Rate Limit**: Gmail allows 100 emails/day - plenty for daily summary

## Learnings
- 2026-02-04: Email should be concise, mobile-friendly
- 2026-02-04: Include both Sheet and Dashboard links

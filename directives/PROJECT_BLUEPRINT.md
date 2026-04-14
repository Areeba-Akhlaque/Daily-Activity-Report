# Pvragon Activity Tracker — Project Definition

## 1. What this is
An automated daily audit system that tracks team productivity across ClickUp, GitHub, Figma, Google Workspace, and Backendless. It consolidates raw platform events into a single Google Sheet, a public web dashboard, and a daily email sent to managers.

Client-facing deliverables (in priority order):
1. **Daily email** — sent every morning with yesterday's activity summary + chart.
2. **Google Sheet** — full historical record (`Daily Audit`, `Activity Time Analysis`, `Event Type References`, per-platform tabs).
3. **Web dashboard** — https://Areeba-Akhlaque.github.io/Daily-Activity-Report/ (charts, hourly heatmap, 7-day trends).

## 2. Architecture (3 layers)
1. **Directive Layer** (`directives/*.md`) — SOPs for each fetch and each pipeline stage. Humans read these to understand intent.
2. **Orchestration Layer** (`orchestration/`) — top-level runners. `run_daily_workflow.py` sequences the pipeline, `send_daily_email.py` mails the summary, `upload_charts_to_drive.py` backs up chart PNGs, `build_hourly.py` and `refresh_dashboard.py` produce the dashboard JSON.
3. **Execution Layer** (`execution/`) — one `fetch_*.py` per platform, plus `name_mappings.py` (the single source of truth for identity/exclusion), `generate_reports.py` (builds the Daily Audit matrix), and `generate_activity_time.py` (computes session windows).

## 3. Daily pipeline
Triggered by `.github/workflows/daily-audit.yml` at **12:00 UTC = 04:00 PST** so the previous PST day is always fully captured. Sequence:

1. `fetch_clickup.py` — tasks, comments, chat channels, DMs (v2 + v3 APIs)
2. `fetch_github.py` — events (PRs, issues, reviews) + search backfill
3. `fetch_github_commits.py` — commits from org repos + team members' personal repos
4. `fetch_figma.py` — file versions + comments
5. `fetch_google_workspace.py` — Admin Reports (Drive, Gmail, Meet, Calendar)
6. `fetch_backendless.py` — console audit log via Node SDK wrapper
7. `generate_reports.py` — unifies per-platform tabs into the `Daily Audit` matrix
8. `generate_activity_time.py` — computes first event, last event, active hours, longest break per person per day (reads cached event files, not live APIs)
9. `build_hourly.py` + `refresh_dashboard.py` — writes `dashboard/data.json`, `hourly_data.json`, `charts/*.png`
10. Commit + push dashboard artifacts
11. `upload_charts_to_drive.py` — copies chart PNGs to Google Drive folder `1MUvSw33n-PTpUkB6QwgQuJ5fEdLDNfKi` (with Apps Script fallback from Gmail)
12. GitHub Pages deploy
13. `send_daily_email.py` — emails yesterday's summary to managers (can be skipped via `skip_email` input)

## 4. Rolling 7-day window (Phase 2)
Every daily run fetches **only the last `ROLLING_DAYS=7` days** from each platform, not all history from `START_DATE`. Historical rows and cache events older than the window are preserved untouched in both the Google Sheet tabs and the `dashboard/*_events_cache.json` files.

**Why:** fetching from `START_DATE=2026-01-01` grew linearly every day. On 2026-04-10 we hit the 30-minute GitHub Actions timeout. A rolling window keeps daily runtime roughly constant.

**Escape hatch:** the `full_rebuild` workflow_dispatch input sets `FULL_REBUILD=true`, which falls back to fetching from `START_DATE`. Use this after changing `name_mappings.py` exclusion rules or canonical name mappings so the old rows get reprocessed.

**Cache persistence:** `dashboard/figma_events_cache.json`, `clickup_events_cache.json`, `gworkspace_events_cache.json`, `backendless_events_cache.json` are committed to git each run so they survive across runners. Without this the rolling merge would lose everything older than 7 days.

**Invariant:** Activity Time Analysis (first event, last event, longest break) must stay 100% accurate — the client reads the daily email. The rolling window preserves all pre-window events in the caches, so session analysis is identical to what FULL_REBUILD would produce.

## 5. Identity / exclusion
`execution/name_mappings.py` owns:
- `map_name()` — canonical "Team Member" name from any platform handle/email
- `should_exclude()` — drops non-team members and excluded accounts
- `get_audit_date()` — the PST audit-day rule (events before 12:00 AM PST count as previous day)
- `GITHUB_TEAM_HANDLES` — list used by the commit search backfill

**Do not filter by repo or platform-specific criteria.** If an event maps to a team member who is not excluded, it counts. Saif's personal repos count because there is no reliable signal distinguishing "company-related" from "personal learning" — the client's rule is: if a team member did work, it counts.

## 6. Timezones
All user-visible dates and times are **America/Los_Angeles (PST/PDT)**. APIs return UTC; conversion happens in each fetch script. The "audit date" of an event is the PST calendar day it occurred on — see `get_audit_date()`.

## 7. Files you usually change
- Add/remove team members: `execution/name_mappings.py` → then run workflow with `full_rebuild=true`
- Change pipeline behavior: `orchestration/run_daily_workflow.py`
- Change email content/chart: `orchestration/send_daily_email.py`
- Change dashboard layout: `dashboard/index.html` + `refresh_dashboard.py`
- Change schedule or secrets: `.github/workflows/daily-audit.yml`

## 8. Secrets (GitHub Actions)
`GOOGLE_CREDENTIALS`, `GOOGLE_TOKEN`, `GOOGLE_SHEET_ID`, `CLICKUP_API_KEY`, `CLICKUP_WORKSPACE_ID`, `CLICKUP_TEAM_ID`, `GH_PAT`, `FIGMA_TOKEN`, `FIGMA_TEAM_ID`, `BACKENDLESS_APP_ID`, `BACKENDLESS_API_KEY`, `BACKENDLESS_DEV_LOGIN`, `BACKENDLESS_DEV_PASSWORD`, `EMAIL_USER`, `EMAIL_PASSWORD`. Email recipients are hard-coded in the workflow (`areeba@pvragon.com`, `jaime@pvragon.com`, `bradd@pvragon.com`).

## 9. Known external dependencies
- Google Apps Script "Daily Audit" project — listens for incoming audit emails and saves inline QuickChart images to the Drive folder as a redundancy path for the Python upload step.
- QuickChart.io — renders the chart PNGs the email embeds via `<img src>`.

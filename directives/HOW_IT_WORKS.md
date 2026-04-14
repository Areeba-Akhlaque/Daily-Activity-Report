# How the Project Works

This is the practical guide to how each stage of the daily audit pipeline behaves. For the high-level definition (what this project *is*, who uses it, what it delivers), see `PROJECT_BLUEPRINT.md`. For agent/automation rules, see `AGENTS.md`.

---

## Schedule & trigger

- **Cron:** `0 12 * * *` (12:00 UTC = 04:00 AM PST) — see `.github/workflows/daily-audit.yml`.
- **Why this time:** the PST day must be fully over before we audit it, so we wait until 4 AM PST (the previous day closed 4 hours earlier).
- **Manual trigger:** `workflow_dispatch` with two inputs:
  - `skip_email` — suppress the manager email (use when testing).
  - `full_rebuild` — set `FULL_REBUILD=true`, which forces every fetch script to pull from `START_DATE=2026-01-01` instead of the rolling 7-day window. Use this after editing `name_mappings.py`.

## Rolling 7-day window (Phase 2)

Every fetch script reads `ROLLING_DAYS=7` and `FULL_REBUILD` from the environment:

- **Rolling mode (default):** fetch only events from `now - 7 days` to now. Rows in the Google Sheet older than the window are preserved untouched; rows inside the window are replaced by the fresh fetch.
- **FULL_REBUILD mode:** fetch from `START_DATE` and overwrite the entire tab. Use when canonical names / exclusion rules changed.
- **Caches** (`dashboard/*_events_cache.json`) use the same rule — pre-window historical events are preserved so `generate_activity_time.py` stays accurate. They are committed to git each run so they survive across Actions runners.

## Pipeline order

`orchestration/run_daily_workflow.py` runs these sequentially. If any fetch fails, the others still run.

1. **`fetch_clickup.py`** → `Clickup_Activity` tab
   - v2 API for workspace members, tasks (created/updated/completed), and per-task comments.
   - v3 API for chat channels and direct messages (paged through all channels, classifies each as "Channels messages" vs "Direct chats messages").
   - 60-second per-(user, event-type) cooldown to suppress auto-save noise.
   - Writes raw events to `clickup_events_cache.json`.

2. **`fetch_github.py`** → `Github_Activity` tab
   - Org events endpoint per repo for the last ~90 days' worth of PRs, issues, reviews, comments, branch/tag ops. `PushEvent` is deliberately excluded — commits are covered by `fetch_github_commits.py`. Counting them here would double-count.
   - Search API backfill (`type:pr` and `type:issue`) per team handle in `GITHUB_TEAM_HANDLES` to catch anything the events endpoint drops.
   - 60-second per-(user, event-type) cooldown.

3. **`fetch_github_commits.py`** → `Github_Commits` tab
   - Fetches commits from all org repos updated within the window, plus each team member's personal repos (we treat personal-repo commits as real work because there's no reliable company-vs-personal signal — the rule is "if a team member did work, it counts").
   - Dedup by commit SHA, so rolling merge naturally preserves historical commits.

4. **`fetch_figma.py`** → `Figma_Activity` tab
   - Lists team projects → files → fetches versions (paginated) and comments.
   - Emits "File Created" for the oldest version, "File Edited" for subsequent versions, "Comment Posted" for comments.
   - 15-minute bucketing per (user, minute-quarter) on versions to suppress auto-save churn.

5. **`fetch_google_workspace.py`** → `GoogleWorkspace_Activity` tab
   - Admin Reports API. Fetches Drive, Gmail, Meet, Calendar, Login activity.
   - Gmail **sends only** are counted as team activity (received mail is noise).
   - Admin Reports has a hard **30-day per-request** window, so rolling mode does one call and FULL_REBUILD chunks the full range into 30-day windows.

6. **`fetch_backendless.py`** → `Console_Audit_Logs` tab
   - Primary path: `fetch_backendless_node.js` (official SDK, Node wrapper).
   - Fallback: direct Python login to `console.okridecare.com` + audit-logs REST endpoint.
   - Developer email parsed via `clean_developer_email()` (dict / JSON string / regex).
   - 60-second per-(user, event) cooldown.

7. **`generate_reports.py`** → `Daily Audit` + `Event Type References` + `System Architecture` tabs
   - Reads all per-platform tabs and builds the unified matrix: `Team Member × Activity Date × Platform × Activity Type × Count`.
   - Gap-fills with zeros so every combination has a row (the client requested this for transparency).
   - Re-applies `map_name()` + `should_exclude()` as a final safety net.
   - Preserves any manual "Description" edits in the `Event Type References` tab (does not overwrite).

8. **`generate_activity_time.py`** → `Activity Time Analysis` tab
   - Reads all `dashboard/*_events_cache.json` files (not live APIs — that's why caches must persist).
   - Computes per-person-per-day: **first event**, **last event**, **total active hours** (session-gap model), **longest break**.
   - This is the metric the client reads in the daily email. It must stay accurate — do not break cache persistence.

9. **`build_hourly.py`** + **`refresh_dashboard.py`** → `dashboard/data.json`, `dashboard/hourly_data.json`, `dashboard/charts/*.png`
   - Dashboard reads local JSON, not live APIs, for speed.
   - Charts are rendered via QuickChart.io (external URL) so they can be embedded in the email without Drive links.

10. **Commit + push** dashboard artifacts + caches → triggers GitHub Pages deploy.

11. **`upload_charts_to_drive.py`** → Google Drive folder `1MUvSw33n-PTpUkB6QwgQuJ5fEdLDNfKi`
    - Backs up the rendered chart PNG so the client has a historical archive in Drive.
    - A parallel redundancy path exists as a Google Apps Script ("Daily Audit" project) that listens for incoming audit emails and saves the inline QuickChart images to the same folder — this is the fallback if the Python upload step ever fails.

12. **`send_daily_email.py`** → managers (`areeba@pvragon.com`, `jaime@pvragon.com`, `bradd@pvragon.com`)
    - Reads `Daily Audit` + `Activity Time Analysis` for **yesterday only**.
    - Builds HTML summary: total activities, active members, top performers, platform breakdown, per-person active hours + longest break.
    - Embeds the chart via `<img src="https://quickchart.io/...">`.
    - Skipped entirely if `SKIP_EMAIL=true`.

## Identity & exclusion

All name mapping, exclusion logic, and the PST audit-day rule live in **`execution/name_mappings.py`**. It is the single source of truth. Three functions:

- `map_name(handle_or_email)` — returns the canonical Team Member name (e.g., `bilalmunir985-oss` → `Bilal Munir`, `aleksandar.m.tanaskovic@gmail.com` → `Alexander Pavelko`).
- `should_exclude(name, email=None)` — drops bots, system accounts (`build@`, `careers@`, `support@`, `gcp-organization-admins@`, etc.), and former team members (e.g., Kelly Hereford).
- `get_audit_date(dt_pst)` — returns the PST calendar day the event "belongs to". Events before midnight PST count as the previous day.

`GITHUB_TEAM_HANDLES` is the list the GitHub commit/search backfills iterate over.

After editing this file, trigger a workflow run with `full_rebuild=true` so historical rows are reprocessed with the new rules.

## Error handling philosophy

- Each fetch script is independent — a 500 from Figma does not block ClickUp.
- Gmail sends and Drive uploads are wrapped in `continue-on-error` in the workflow, so email failure does not block the pipeline committing dashboard data.
- 429s are retried with exponential backoff inside each fetch (`get_with_retry` in Figma, per-attempt sleeps in ClickUp chat, etc.).
- Sheet writes always go through a "read existing → merge → clear → write" pattern so a crash mid-write cannot silently delete historical rows.

## Dashboard

Served from GitHub Pages at `https://Areeba-Akhlaque.github.io/Daily-Activity-Report/`. Reads `dashboard/data.json` and `dashboard/hourly_data.json` (committed to the repo each run). Shows: total activities, avg active hours, avg longest break, active team members, 7-day trend, platform breakdown, per-person leaderboard, hourly heatmap.

## Timezones

Everything user-facing is **America/Los_Angeles (PST/PDT)**. APIs return UTC; each fetch script converts to PST before applying `get_audit_date()`. The `Date` column in every tab is formatted `MM/DD/YY` PST.

## Key terms

- **Deep Work** — productivity output (GitHub commits, Figma edits, Backendless console actions).
- **Coordination** — communication output (ClickUp chats, Gmail sends).
- **Audit Window** — the PST calendar day being reported on.
- **Active Window** — from first event to last event, minus breaks longer than the session-gap threshold.

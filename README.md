# Pvragon Activity Tracker

An automated daily audit system that consolidates team activity across five
platforms into a single, trustworthy record. Every morning it collects what each
team member did the day before, writes it to a Google Sheet, refreshes a web
dashboard, and emails a summary to management — with no manual work.

## What it produces

| Output | Description |
| --- | --- |
| **Google Sheet** | The permanent archive. One tab per source plus consolidated audit, per-person active-hours, and an internal spend tab. |
| **Web dashboard** | A public GitHub Pages site with filters, trends, a platform breakdown, a leaderboard, and an hourly heatmap. |
| **Daily email** | A morning summary to managers with an activity chart per person. |

## Sources

ClickUp · GitHub · Figma · Google Workspace (Gmail + Drive) · Backendless.
An internal-only tracker also attributes AI/software subscription spend per person
from Mercury cards; that data stays in the Sheet and the email and never reaches
the public dashboard.

## How it is organised

The code is split into three layers:

- **`directives/`** — plain-language standard operating procedures for each source.
- **`orchestration/`** — the runners that sequence the daily job, refresh the
  dashboard, send the email, and back the workbook up.
- **`execution/`** — one fetcher per platform plus the report/aggregation logic.

## Running

The pipeline runs automatically via GitHub Actions on a daily schedule
(`0 12 * * *`, i.e. 12:00 UTC). It can also be triggered manually from the Actions
tab. A rolling window keeps recent days current while preserving all history.

Python dependencies are listed in `requirements.txt`; the one Node helper's
dependencies are in `execution/package.json`.

## Configuration

All credentials are supplied as **GitHub Actions secrets** — nothing sensitive is
stored in the repository. The required secret names are:

```
GOOGLE_CREDENTIALS   GOOGLE_TOKEN        GOOGLE_SHEET_ID
CLICKUP_API_KEY      CLICKUP_WORKSPACE_ID CLICKUP_TEAM_ID
GH_PAT               FIGMA_TOKEN         FIGMA_TEAM_ID
BACKENDLESS_APP_ID   BACKENDLESS_API_KEY BACKENDLESS_DEV_LOGIN  BACKENDLESS_DEV_PASSWORD
MERCURY_API_TOKEN    EMAIL_USER          EMAIL_PASSWORD
```

The dashboard URL used in the email is read from the repository variable
`DASHBOARD_URL` (it falls back to a default if unset).

Local development uses a `.env` file and OAuth token files, all of which are
git-ignored and never committed.

## A note on the data

This system informs how people's contributions are viewed, so integrity is a first
principle: the daily run only ever adds to history, duplicates are guarded against
at the source, and the whole workbook is snapshotted to Google Drive each day.

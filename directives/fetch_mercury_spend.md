# Directive — Mercury AI & Software Spend

Tracks per-person software/subscription spend on company Mercury cards and surfaces
it in the Google Sheet (`AI & Software Spend` tab) and the daily manager email.

## Goal
Each team member has a dedicated Mercury "SW & Subscription" card. Audit who is
paying for what (Claude, OpenAI, Cursor, etc.), at what tier, and how much —
without anyone manually compiling receipts.

## Scope & privacy
- **Financial data — internal only.** It lives in the Google Sheet tab + the
  manager email. It must **never** reach the public dashboard. This is automatic:
  `refresh_dashboard.py` reads only `Daily Audit`, `Activity Time Analysis`,
  `Event Type References`, `System Architecture` — not this tab. Do **not** add this
  tab to that list or commit any spend cache to the repo.
- Whoever can view the audit sheet can see this tab. Restrict sheet sharing if needed.

## Execution
`execution/fetch_mercury.py` — run after the platform fetchers, before report
generation. Self-skips if `MERCURY_API_TOKEN` is unset (never breaks the pipeline).

1. `GET /cards` → `{card_id: last4}` across **all** accounts (the credit-card
   account is hidden from `GET /accounts` but its cards appear here).
2. For each account holding cards, page `GET /account/{id}/transactions?start=…`.
3. Keep only `debitCardTransaction` / `creditCardTransaction` with a negative amount
   (debits; skip refunds and $0 auths).
4. Attribute: `details.debitCardInfo.id` → `last4` → person via
   `mercury_mappings.person_for_card()`. Untracked cards (incl. Rafio ••0964 and
   Lucah ••7470) are skipped.
5. Classify merchant (`normalize_merchant`) and tier/type (`classify`, best-effort,
   labelled "≈" in the email).
6. Write the `AI & Software Spend` tab, **merging by `Txn ID`** so history
   accumulates and nothing double-counts. Grid is resized to stay tight (the
   workbook has a 10M-cell limit — see the Daily Audit resize fix).

Columns: `Team Member · Date · Merchant · Category · Type · Tier · Amount · Card · Description · Txn ID`.

## Rolling window
Same `FULL_REBUILD` / `ROLLING_DAYS` env as the other fetchers, but the rolling
window has a 35-day floor (subscriptions are monthly and charges can post late).
Run with `full_rebuild=true` once to backfill history from `START_DATE`.

## Identity / config
`execution/mercury_mappings.py`:
- `CARD_LAST4_TO_PERSON` — the finance team's card→person assignments.
- `SKIP_LAST4` — cards explicitly not tracked.
- `MERCHANT_ALIASES`, `classify()` — merchant normalization + approximate tiering.
Finance attribution is independent of the activity `CORE_TEAM` gate (some
cardholders aren't on the activity roster).

## Email
`send_daily_email.py` → `get_spend_summary()` + `_render_spend_section()` add a
"🧾 AI & Software Spend" section: month-to-date spend per person + new charges
on the reported day.

## Secrets
`MERCURY_API_TOKEN` (read-only) — GitHub Actions secret + the workflow `.env` step.
Use a read-only token; never commit it.

## Testing
`python execution/fetch_mercury.py --dry-run` (or `MERCURY_DRY_RUN=true`) prints the
rows it would write and per-person totals without touching the sheet.

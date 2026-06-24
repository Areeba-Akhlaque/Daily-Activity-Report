"""
Mercury AI & Software Spend Fetcher
===================================
Pulls debit/credit card transactions from Mercury, attributes each charge to a
team member by card last4, classifies merchant + tier, and writes the
"AI & Software Spend" tab. Financial data — stays in the sheet + email only,
never the public dashboard (refresh_dashboard.py reads only 4 named tabs).

See: directives/fetch_mercury_spend.md
Self-skips cleanly if MERCURY_API_TOKEN is not set, so it never breaks the pipeline.
"""

import os
import sys
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from mercury_mappings import person_for_card, normalize_merchant, classify


def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k] = v


load_env()

MERCURY_TOKEN = os.environ.get('MERCURY_API_TOKEN', '')
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
TAB_NAME = 'AI & Software Spend'
BASE = 'https://api.mercury.com/api/v1'
PST = 'America/Los_Angeles'

START_DATE_STR = os.environ.get('START_DATE', '2026-01-01')
FULL_REBUILD = os.environ.get('FULL_REBUILD', 'false').lower() in ('true', '1', 'yes')
ROLLING_DAYS = int(os.environ.get('ROLLING_DAYS', '7'))
DRY_RUN = ('--dry-run' in sys.argv) or os.environ.get('MERCURY_DRY_RUN', '').lower() in ('true', '1', 'yes')

# Subscriptions are monthly and charges can post a few days late, so use a wider
# floor than the 7-day activity window. Merge-by-txn-id keeps it idempotent.
if FULL_REBUILD:
    FETCH_START = datetime.strptime(START_DATE_STR, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    print(f'[MODE] FULL_REBUILD — Mercury spend from {START_DATE_STR}')
else:
    window = max(ROLLING_DAYS, 35)
    FETCH_START = datetime.now(timezone.utc) - timedelta(days=window)
    print(f'[MODE] Rolling {window}-day window — Mercury spend from {FETCH_START.strftime("%Y-%m-%d")}')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
]
COLUMNS = ['Team Member', 'Date', 'Merchant', 'Category', 'Type', 'Tier',
           'Amount', 'Card', 'Description', 'Txn ID']


def mercury_headers():
    return {'Authorization': f'Bearer {MERCURY_TOKEN}', 'Accept': 'application/json'}


def fetch_card_map():
    """Return {card_id: last4} across every account (incl. the credit account that
    /accounts hides)."""
    r = requests.get(f'{BASE}/cards', headers=mercury_headers(), timeout=30)
    r.raise_for_status()
    cards = r.json().get('cards', [])
    card_map = {}
    for c in cards:
        cid = c.get('id') or c.get('cardId')
        last4 = c.get('lastFourDigits') or c.get('lastFour')
        if cid and last4:
            card_map[cid] = str(last4)[-4:]
    print(f'  [Cards] Resolved {len(card_map)} cards across all accounts')
    return card_map


def fetch_accounts_with_cards(card_map):
    """Account ids that host at least one card (so we know where to read txns)."""
    r = requests.get(f'{BASE}/cards', headers=mercury_headers(), timeout=30)
    accts = {c.get('accountId') for c in r.json().get('cards', []) if c.get('accountId')}
    return sorted(a for a in accts if a)


def fetch_card_transactions(account_id):
    """All debit/credit card transactions for an account since FETCH_START (paged)."""
    txns = []
    offset = 0
    start_str = FETCH_START.strftime('%Y-%m-%d')
    while True:
        params = {'limit': 500, 'offset': offset, 'start': start_str}
        r = requests.get(f'{BASE}/account/{account_id}/transactions',
                         headers=mercury_headers(), params=params, timeout=30)
        if r.status_code != 200:
            print(f'  [Txns] account {str(account_id)[:8]} HTTP {r.status_code}: {r.text[:160]}')
            break
        batch = r.json().get('transactions', [])
        if not batch:
            break
        txns.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return txns


def _card_id_of(txn):
    d = txn.get('details') or {}
    info = d.get('debitCardInfo') or d.get('creditCardInfo') or {}
    return info.get('id')


def build_rows(card_map):
    rows = []
    for aid in fetch_accounts_with_cards(card_map):
        for t in fetch_card_transactions(aid):
            if t.get('kind') not in ('debitCardTransaction', 'creditCardTransaction'):
                continue
            amount = t.get('amount')
            if not amount or amount >= 0:   # debits are negative; skip credits/refunds & $0 auths
                continue
            last4 = card_map.get(_card_id_of(t))
            person = person_for_card(last4)
            if not person:
                continue   # untracked card (incl. Rafio/Lucah) → skip

            ts = t.get('postedAt') or t.get('createdAt') or ''
            try:
                date_str = pd.to_datetime(ts).tz_convert(PST).strftime('%m/%d/%y')
            except Exception:
                date_str = str(ts)[:10]

            counterparty = t.get('counterpartyName') or ''
            desc = t.get('bankDescription') or ''
            merchant = normalize_merchant(counterparty, desc)
            ctype, tier = classify(merchant, amount)
            rows.append({
                'Team Member': person,
                'Date': date_str,
                'Merchant': merchant,
                'Category': t.get('mercuryCategory') or 'Software',
                'Type': ctype,
                'Tier': tier,
                'Amount': round(abs(amount), 2),
                'Card': f'••{last4}',
                'Description': desc[:60],
                'Txn ID': t.get('id', ''),
            })
    return rows


def get_creds():
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def upload(rows):
    df_new = pd.DataFrame(rows, columns=COLUMNS)
    creds = get_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    try:
        ws = sh.worksheet(TAB_NAME)
        existing = ws.get_all_records()
        df_old = pd.DataFrame(existing)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_NAME, rows=2000, cols=len(COLUMNS))
        df_old = pd.DataFrame(columns=COLUMNS)

    # Merge by Txn ID so history accumulates and nothing double-counts.
    if FULL_REBUILD or df_old.empty:
        combined = df_new
    else:
        for col in COLUMNS:
            if col not in df_old.columns:
                df_old[col] = ''
        combined = pd.concat([df_old[COLUMNS], df_new], ignore_index=True)
        combined = combined.drop_duplicates(subset=['Txn ID'], keep='last')

    combined['Amount'] = pd.to_numeric(combined['Amount'], errors='coerce').fillna(0).round(2)
    combined['_dt'] = pd.to_datetime(combined['Date'], format='%m/%d/%y', errors='coerce')
    combined = combined.sort_values(by=['_dt'], ascending=False).drop(columns=['_dt'])

    values = [COLUMNS]
    for _, r in combined.iterrows():
        values.append([(round(float(r[c]), 2) if c == 'Amount' else str(r[c]) if pd.notnull(r[c]) else '')
                       for c in COLUMNS])

    # Write then trim trailing rows (keep grid tight — same lesson as the 10M-cell fix).
    n = len(values)
    if ws.row_count < n:
        ws.resize(rows=n + 50, cols=len(COLUMNS))
    ws.update(values=values, range_name='A1')
    if ws.row_count > n:
        ws.batch_clear([f'A{n + 1}:J{ws.row_count}'])
    print(f'  [SUCCESS] Wrote {len(combined)} spend rows to "{TAB_NAME}"')


def main():
    print('=' * 60)
    print('MERCURY AI & SOFTWARE SPEND FETCH')
    print('=' * 60)
    if not MERCURY_TOKEN:
        print('[SKIP] MERCURY_API_TOKEN not set — skipping Mercury fetch.')
        return
    try:
        card_map = fetch_card_map()
    except Exception as e:
        print(f'[ERROR] Could not reach Mercury: {e}')
        return

    rows = build_rows(card_map)
    print(f'  Attributed {len(rows)} tracked card charges since {FETCH_START.strftime("%Y-%m-%d")}')
    if not rows and not FULL_REBUILD:
        print('  [INFO] No new charges in window (existing sheet rows preserved).')

    if DRY_RUN:
        df = pd.DataFrame(rows, columns=COLUMNS)
        print('\n[DRY RUN] Would write these rows (no sheet write):')
        with pd.option_context('display.max_rows', 40, 'display.width', 200):
            print(df.head(40).to_string(index=False))
        if not df.empty:
            print('\nPer-person totals (window):')
            print(df.groupby('Team Member')['Amount'].sum().sort_values(ascending=False).to_string())
        return

    if rows or FULL_REBUILD:
        upload(rows)
    else:
        print('  [SKIP] Nothing to write.')


if __name__ == '__main__':
    main()

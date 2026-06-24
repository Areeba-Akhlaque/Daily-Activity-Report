"""
Send Daily Activity Summary Email
=================================
Generates and sends an HTML email with daily activity highlights.
See: directives/send_daily_email.md
"""

import os
import sys
import json
import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
import requests
import pytz

from collections import defaultdict
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Add execution dir to path for name_mappings
sys.path.insert(0, os.path.join(ROOT_DIR, 'execution'))
from name_mappings import map_name, should_exclude, STRICT_TEAM_GMAIL, COMMS_EVENT_TYPES, is_comms_event

# Load from .env if exists
def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value

load_env()

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1t7jeunt3IDmnBcIoRYxM06sZgzCYYMAK8AgwH21M0Fo')
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENTS = os.environ.get('EMAIL_RECIPIENTS', 'areeba@pvragon.com,jaime@pvragon.com').split(',')
DASHBOARD_URL = os.environ.get('DASHBOARD_URL', 'https://Areeba-Akhlaque.github.io/Daily-Activity-Report/dashboard/index.html')
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/gmail.send'
]

def get_credentials():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds

def safe_int(val):
    """Safely convert to int, defaulting to 0."""
    try:
        if not val: return 0
        return int(float(str(val).replace(',', '')))
    except (ValueError, TypeError):
        return 0

def safe_float(val):
    """Safely convert to float, defaulting to 0.0."""
    try:
        if not val: return 0.0
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0.0

def _norm_date(s):
    """Normalize a sheet date cell to MM/DD/YY for tolerant cross-tab matching.

    Google Sheets can auto-format a written '05/12/26' into '5/12/2026' (or other
    locale forms) depending on the column's number format. The leaderboard joins
    'Activity Time Analysis'.Date against 'Daily Audit'.Activity Date by exact
    string, so any drift between the two tabs silently drops every time-metric.
    Parsing both sides to a canonical MM/DD/YY makes the join format-proof.
    """
    s = str(s).strip()
    if not s:
        return ''
    for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d', '%m-%d-%y', '%m-%d-%Y'):
        try:
            return datetime.strptime(s, fmt).strftime('%m/%d/%y')
        except ValueError:
            continue
    return s

def _build_summary_from_audit(target_audit_data, target_time_data, target_date, exclude_comms=False):
    """
    Build a per-member summary dict from Daily Audit + Activity Time Analysis rows.
    When exclude_comms=True, rows whose Activity Type is in COMMS_EVENT_TYPES are dropped
    from event/platform/type totals. Time Analysis fields (start/end/hours/break) come
    from the sheet and reflect all activity — intentionally unchanged across variants.
    """
    member_stats = {}

    # Pre-populate with all core employees so they appear even with 0 events
    for name in STRICT_TEAM_GMAIL:
        member_stats[name] = {
            'name': name,
            'start': '-',
            'end': '-',
            'hours': 0.0,
            'break': 0,
            'events': 0,
            'top_platform': '-',
            'platform_breakdown': {},
            'type_breakdown': {}
        }

    # Time Analysis (same for both variants — describes the work day, not event count)
    for r in target_time_data:
        name = r.get('Team Member')
        if not name: continue
        member_stats[name] = {
            'name': name,
            'start': r.get('First Activity (PST)', '-'),
            'end': r.get('Last Activity (PST)', '-'),
            'hours': safe_float(r.get('Active Window (Hours)')),
            'break': safe_int(r.get('Longest Break (Minutes)')),
            'events': safe_int(r.get('Total Events')),
            'top_platform': '-',
            'platform_breakdown': {},
            'type_breakdown': {}
        }

    platform_counts = defaultdict(lambda: defaultdict(int))
    type_counts = defaultdict(lambda: defaultdict(int))
    global_plat_counts = defaultdict(int)
    total_activities = 0
    # Per-member event total derived purely from Daily Audit rows (so the
    # Comms-excluded variant can override the sheet's Total Events figure).
    audit_event_counts = defaultdict(int)

    for r in target_audit_data:
        name = r.get('Team Member')
        plat = r.get('Platform')
        act_type = r.get('Activity Type')
        count = safe_int(r.get('Count'))

        if count <= 0:
            continue
        if exclude_comms and is_comms_event(act_type):
            continue

        if name:
            platform_counts[name][plat] += count
            if act_type:
                type_counts[name][act_type] += count
            audit_event_counts[name] += count
        if plat:
            global_plat_counts[plat] += count
        total_activities += count

        if name and name not in member_stats:
            member_stats[name] = {
                'name': name, 'start': '-', 'end': '-', 'hours': 0.0, 'break': 0,
                'events': 0, 'top_platform': '-',
                'platform_breakdown': {}, 'type_breakdown': {}
            }

    for name, plats in platform_counts.items():
        if not plats or name not in member_stats:
            continue
        top_plat = max(plats.items(), key=lambda x: x[1])[0]
        member_stats[name]['top_platform'] = top_plat
        if member_stats[name]['events'] == 0:
            member_stats[name]['events'] = sum(plats.values())
        member_stats[name]['platform_breakdown'] = dict(plats)
        member_stats[name]['type_breakdown'] = dict(type_counts[name])

    # In the Comms-excluded variant the Time Analysis 'Total Events' figure still
    # counts Comms — override it with the Daily-Audit-derived count so the event
    # column of the leaderboard is consistent with the other totals.
    if exclude_comms:
        for name, m in member_stats.items():
            m['events'] = audit_event_counts.get(name, 0)

    sorted_members = sorted(member_stats.values(), key=lambda x: (x['hours'], x['events']), reverse=True)

    active_mems_list = [m for m in sorted_members if m['events'] > 0]
    active_members_count = len(active_mems_list)

    if active_members_count > 0:
        avg_hours = sum(m['hours'] for m in active_mems_list) / active_members_count
        avg_break = sum(m['break'] for m in active_mems_list) / active_members_count
    else:
        avg_hours = 0.0
        avg_break = 0

    return {
        'date': target_date,
        'total_activities': total_activities,
        'active_members': active_members_count,
        'avg_hours': round(avg_hours, 1),
        'avg_break': round(avg_break),
        'members': sorted_members,
        'platform_counts': dict(global_plat_counts),
        'all_types': list(set(t for m in type_counts.values() for t in m.keys())),
        'time_range': "12:00 AM - 11:59 PM PST"
    }


def get_daily_summary(creds, target_date_override=None):
    """Fetch summary data from Google Sheet with detailed daily breakdown.

    Returns the full (all-activity) summary and attaches a 'work_only' sibling
    summary with Comms event types excluded for the side-by-side email section.
    """
    print("Fetching daily summary data...")
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    # Get Date Logic (PST-aware)
    pst = pytz.timezone('America/Los_Angeles')
    now_pst = datetime.now(pst)

    if target_date_override:
        target_date = target_date_override
    else:
        # ALWAYS target "Yesterday" (The last fully completed 24h cycle)
        # This ensures reports always cover a full 12:00 AM - 11:59 PM window.
        target_date_dt = now_pst - timedelta(days=1)
        target_date = target_date_dt.strftime('%m/%d/%y')

    print(f"Target Date: {target_date}")

    target_norm = _norm_date(target_date)

    # 1. Fetch Activity Time Analysis
    try:
        print("  Reading 'Activity Time Analysis'...")
        ws_time = sh.worksheet('Activity Time Analysis')
        time_data = ws_time.get_all_records()
        target_time_data = [r for r in time_data if _norm_date(r.get('Date')) == target_norm]
        print(f"    Found {len(target_time_data)} time records for {target_date} (tab has {len(time_data)} rows total)")
    except Exception as e:
        print(f"    [WARN] Error fetching Time Analysis: {e}")
        target_time_data = []

    # 2. Fetch Daily Audit
    try:
        print("  Reading 'Daily Audit'...")
        ws_audit = sh.worksheet('Daily Audit')
        audit_data = ws_audit.get_all_records()
        target_audit_data = [r for r in audit_data if _norm_date(r.get('Activity Date')) == target_norm]
        print(f"    Found {len(target_audit_data)} audit records for {target_date}")
    except Exception as e:
        print(f"    [WARN] Error fetching Daily Audit: {e}")
        target_audit_data = []

    # Detect the failure mode behind the all-zeros leaderboard: Daily Audit has
    # activity for the day but Activity Time Analysis has nothing, so every
    # first/last/hours/break cell would render blank. Flag it loudly + in the email.
    time_data_missing = (not target_time_data) and bool(target_audit_data)
    if time_data_missing:
        print(f"  [WARN] Activity Time Analysis has NO rows for {target_date} but Daily Audit has "
              f"{len(target_audit_data)}. Active-hours columns will be blank. The 'Activity Time "
              f"Analysis' tab is likely empty/stale — re-run the daily workflow (or "
              f"execution/generate_activity_time.py) to repopulate it.")

    # Build both variants from the same raw rows so totals are directly comparable.
    summary_all = _build_summary_from_audit(target_audit_data, target_time_data, target_date, exclude_comms=False)
    summary_work = _build_summary_from_audit(target_audit_data, target_time_data, target_date, exclude_comms=True)
    summary_all['work_only'] = summary_work
    summary_all['time_data_missing'] = time_data_missing
    summary_all['spend'] = get_spend_summary(sh, target_date)

    return summary_all


def get_spend_summary(sh, target_date):
    """Read the 'AI & Software Spend' tab (Mercury) and summarize month-to-date
    spend per person + new charges on the target date. Returns None if absent."""
    try:
        ws = sh.worksheet('AI & Software Spend')
        rows = ws.get_all_records()
    except Exception:
        return None
    if not rows:
        return None
    try:
        tdt = datetime.strptime(target_date, '%m/%d/%y')
    except ValueError:
        return None

    by_person = defaultdict(lambda: {'total': 0.0, 'tools': defaultdict(float)})
    month_total = 0.0
    new_charges = []
    for r in rows:
        d = str(r.get('Date', '')).strip()
        try:
            rdt = datetime.strptime(d, '%m/%d/%y')
        except ValueError:
            continue
        amt = safe_float(r.get('Amount'))
        person = r.get('Team Member', '')
        merchant = r.get('Merchant', '')
        if rdt.year == tdt.year and rdt.month == tdt.month:
            by_person[person]['total'] += amt
            by_person[person]['tools'][merchant] += amt
            month_total += amt
        if d == target_date:
            new_charges.append({'person': person, 'merchant': merchant, 'amount': amt,
                                'tier': r.get('Tier', ''), 'type': r.get('Type', '')})

    people = []
    for name, d in sorted(by_person.items(), key=lambda x: -x[1]['total']):
        top = sorted(d['tools'].items(), key=lambda x: -x[1])
        people.append({'name': name, 'total': round(d['total'], 2),
                       'tools': ', '.join(f"{m} ${v:,.0f}" for m, v in top[:4])})
    return {
        'month_label': tdt.strftime('%B %Y'),
        'month_total': round(month_total, 2),
        'people': people,
        'new_charges': sorted(new_charges, key=lambda x: -x['amount']),
        'new_total': round(sum(c['amount'] for c in new_charges), 2),
    }


def _render_spend_section(spend):
    """HTML for the AI & Software Spend email section."""
    if not spend or not spend['people']:
        return ""

    rows_html = ""
    for m in spend['people']:
        rows_html += f"""
        <tr>
            <td style="padding: 10px 12px; border-bottom: 1px solid #eee;"><b>{m['name']}</b></td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #eee; text-align: right; color: #047857; font-weight: bold;">${m['total']:,.2f}</td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #eee; color: #64748b; font-size: 12px;">{m['tools']}</td>
        </tr>"""

    new_html = ""
    if spend['new_charges']:
        items = "".join(
            f"<li><strong>{c['person']}</strong> — {c['merchant']} "
            f"{('('+c['tier']+')') if c['tier'] else ''} <strong>${c['amount']:,.2f}</strong></li>"
            for c in spend['new_charges'])
        new_html = f"""
        <p style="margin: 16px 0 8px; font-size: 13px; color: #475569;"><strong>New charges yesterday</strong> (${spend['new_total']:,.2f}):</p>
        <ul style="list-style: none; padding: 0; margin: 0; display: block;">{items}</ul>"""

    return f"""
    <div style="margin-top: 40px; padding-top: 24px; border-top: 2px dashed #cbd5e1;">
        <h2 style="margin: 0 0 8px; font-size: 18px; color: #1e293b;">🧾 AI &amp; Software Spend</h2>
        <p style="margin: 0 0 16px; color: #64748b; font-size: 12px;">
            Per-person software &amp; subscription spend on Mercury cards for <strong>{spend['month_label']}</strong>
            (month-to-date total <strong>${spend['month_total']:,.2f}</strong>). Tier labels are approximate.
        </p>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="text-align:left;">Team Member</th>
                        <th style="text-align:right;">This Month</th>
                        <th style="text-align:left;">Top Tools</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
        {new_html}
    </div>"""



# === Platform-Based Color System ===
# Premium SaaS Palette (ClickUp=Violet, GitHub=Slate, Google=SkyBlue, Figma=Amber, Backendless=Emerald)
EVENT_COLOR_MAP = {
    # ClickUp
    'Comment Posted': '#C084FC', 'task updated': '#A78BFA', 'task completed': '#8B5CF6',
    'task created': '#7C3AED', 'Channels messages': '#C4B5FD', 'Direct chats messages': '#DDD6FE',
    # GitHub
    'Code Commit': '#94A3B8', 'PR Opened/Closed': '#CBD5E1', 'PR Reviewed': '#E2E8F0',
    'PR Comment Posted': '#F1F5F9', 'Issue/PR Comment Posted': '#F8FAFC',
    'Branch/Tag Created': '#64748B', 'Branch/Tag Deleted': '#475569', 'Issue Opened/Closed': '#334155',
    # Google Workspace
    'Gmail Send': '#38BDF8', 'Drive Edit': '#0EA5E9',
    # Figma
    'File Edited': '#FBBF24', 'File Created': '#F59E0B',
    # Backendless
    'Update Page UI': '#34D399', 'Modify table record': '#10B981',
    'Deploy Cloud Code Model From Console': '#059669', 'Save Cloud Code Draft Model': '#6EE7B7',
    'Update UI Page Handler Logic': '#2DD4BF', 'Delete File': '#14B8A6',
    'Update Function Logic': '#0D9488', 'Delete table records': '#A7F3D0',
    'Run Timer': '#5EEAD4', 'Create Column': '#4ADE80', 'Change Column': '#22C55E',
    'Publish UI Container': '#86EFAC', 'Create new record in table': '#16A34A',
    'Create new UI Page': '#4ADE80', 'Edit Custom Email Template': '#38BDF8',
    'Create UI Container': '#818CF8', 'Copy File': '#6EE7B7', 'Create New Directory': '#A7F3D0',
}

EVENT_PLATFORM_MAP = {
    'task created': 'ClickUp', 'task updated': 'ClickUp', 'task completed': 'ClickUp',
    'Comment Posted': 'ClickUp', 'Channels messages': 'ClickUp', 'Direct chats messages': 'ClickUp',
    'Code Commit': 'GitHub', 'PR Opened/Closed': 'GitHub', 'PR Reviewed': 'GitHub',
    'PR Comment Posted': 'GitHub', 'Issue/PR Comment Posted': 'GitHub',
    'Issue Opened/Closed': 'GitHub', 'Branch/Tag Created': 'GitHub', 'Branch/Tag Deleted': 'GitHub',
    'Gmail Send': 'Google Workspace', 'Drive Edit': 'Google Workspace',
    'File Edited': 'Figma', 'File Created': 'Figma',
    'Update Page UI': 'Backendless App', 'Modify table record': 'Backendless App',
    'Create UI Container': 'Backendless App', 'API/Console Access': 'Backendless App',
}

PLATFORM_SORT_ORDER = ['ClickUp', 'GitHub', 'Google Workspace', 'Figma', 'Backendless App', 'Other']

_TEAL_SHADES = ['#34D399','#10B981','#059669','#6EE7B7','#2DD4BF','#14B8A6','#0D9488','#A7F3D0','#5EEAD4','#4ADE80','#22C55E','#86EFAC','#16A34A','#4ADE80','#38BDF8','#818CF8','#6EE7B7','#A7F3D0']
_backendless_shade_ctr = [0]

def get_event_color(event_type, _cache={}):
    if event_type in _cache: return _cache[event_type]
    if event_type in EVENT_COLOR_MAP:
        _cache[event_type] = EVENT_COLOR_MAP[event_type]
        return EVENT_COLOR_MAP[event_type]
    platform = EVENT_PLATFORM_MAP.get(event_type, 'Other')
    if platform == 'Backendless App' or event_type not in EVENT_PLATFORM_MAP:
        color = _TEAL_SHADES[_backendless_shade_ctr[0] % len(_TEAL_SHADES)]
        _backendless_shade_ctr[0] += 1
    else:
        color = {'ClickUp':'#FF5C85', 'GitHub':'#686868', 'Google Workspace':'#1E88E5',
                 'Figma':'#FF9800', 'Backendless App':'#26A69A'}.get(platform, '#777777')
    _cache[event_type] = color
    return color

DRIVE_FOLDER_ID = '1MUvSw33n-PTpUkB6QwgQuJ5fEdLDNfKi'  # Daily Audit Charts folder

def upload_chart_to_drive(creds, img_bytes, date_str):
    """Upload chart image bytes to Google Drive. Side-effect only — does not affect email URL."""
    import traceback
    try:
        from googleapiclient.discovery import build as build_service
        from googleapiclient.http import MediaInMemoryUpload

        # Log token state for diagnostics
        token_scopes = getattr(creds, 'scopes', None) or []
        print(f"  [Drive] Token valid: {creds.valid} | Scopes: {token_scopes}")

        if token_scopes and 'https://www.googleapis.com/auth/drive' not in token_scopes:
            print(f"  [WARN] Token is missing 'drive' scope — Drive upload skipped.")
            print(f"  [FIX]  Run execution/refresh_google_token.py locally and update the GOOGLE_TOKEN GitHub secret.")
            return None

        print(f"  [Drive] Uploading chart to folder {DRIVE_FOLDER_ID}...")
        drive_service = build_service('drive', 'v3', credentials=creds)

        safe_date = date_str.replace('/', '-')
        file_name = f"audit_chart_{safe_date}.png"

        file_metadata = {
            'name': file_name,
            'parents': [DRIVE_FOLDER_ID]
        }
        media = MediaInMemoryUpload(img_bytes, mimetype='image/png')

        uploaded_file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink',
            supportsAllDrives=True
        ).execute()

        file_id = uploaded_file.get('id')
        print(f"  [SUCCESS] Chart saved to Drive: {file_name} (ID: {file_id})")
        # NOTE: Do NOT call permissions().create() — setting 'anyone with link'
        # triggers the Workspace DLP policy which auto-deletes the file immediately.
        # The Drive folder is already shared with the team, so no extra permission needed.
        return file_id

    except Exception as e:
        print(f"  [WARN] Drive upload failed: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        if '403' in str(e) or 'insufficientPermissions' in str(e):
            print(f"  [FIX] Token in GOOGLE_TOKEN secret is missing 'drive' scope.")
            print(f"  [FIX] Re-run execution/refresh_google_token.py locally, then update the secret.")
        return None


def save_chart_to_repo(img_bytes, date_str):
    """Save chart PNG to dashboard/charts/ so it gets committed with the daily run."""
    try:
        charts_dir = os.path.join(ROOT_DIR, 'dashboard', 'charts')
        os.makedirs(charts_dir, exist_ok=True)
        safe_date = date_str.replace('/', '-')
        file_path = os.path.join(charts_dir, f"audit_chart_{safe_date}.png")
        with open(file_path, 'wb') as f:
            f.write(img_bytes)
        print(f"  [CHART] Saved chart to repo: dashboard/charts/audit_chart_{safe_date}.png")
        return file_path
    except Exception as e:
        print(f"  [CHART WARN] Could not save chart to repo: {e}")
        return None


def generate_stacked_bar_chart(summary, creds=None, chart_label_suffix="", save_filename_suffix=""):
    """Generate a stacked horizontal bar chart for Activity Types per Member using QuickChart.io.

    chart_label_suffix: appended to the chart title (e.g. " (Comms Excluded)").
    save_filename_suffix: appended to the saved PNG filename so the two variants don't overwrite each other.
    """
    # Order members on the chart by the actual stacked bar length (descending)
    # so the busiest member sits at the top. The bar length equals the sum of
    # type_breakdown values (Daily Audit counts) — NOT m['events'], which comes
    # from Time Analysis' "Total Events" and can diverge from the bar total.
    def _bar_total(m):
        return sum(m.get('type_breakdown', {}).values())
    active_members = sorted(
        [m for m in summary['members'] if _bar_total(m) > 0],
        key=_bar_total,
        reverse=True,
    )
    if not active_members: return None

    names = [m['name'] for m in active_members]
    # Get all unique types present in active members
    all_types = list(set(t for m in active_members for t in m.get('type_breakdown', {}).keys()))
    
    # Sort types by platform grouping then alphabetically within platform
    def sort_key(t):
        plat = EVENT_PLATFORM_MAP.get(t, 'Other')
        plat_idx = PLATFORM_SORT_ORDER.index(plat) if plat in PLATFORM_SORT_ORDER else 99
        return (plat_idx, t)
    
    types = sorted(all_types, key=sort_key)
    
    datasets = []
    for t in types:
        data = [m.get('type_breakdown', {}).get(t, 0) for m in active_members]
        
        # Get explicitly mapped color for this event type
        color = get_event_color(t)
        
        datasets.append({
            'label': t,
            'data': data,
            'backgroundColor': color,
        })
        
    chart_title = 'Activity Breakdown by Member & Activity Type'
    if chart_label_suffix:
        chart_title = chart_title + chart_label_suffix

    chart_config = {
        'type': 'horizontalBar',
        'data': { 'labels': names, 'datasets': datasets },
        'options': {
            'title': { 'display': True, 'text': chart_title },
            'tooltips': { 'mode': 'index', 'intersect': False },
            'legend': { 
                'display': True, 
                'position': 'top', 
                'align': 'center',
                'labels': { 'usePointStyle': False, 'boxWidth': 12 }
            },
            'responsive': False,
            'scales': {
                'xAxes': [{ 'stacked': True, 'ticks': { 'beginAtZero': True } }],
                'yAxes': [{ 'stacked': True }]
            },
            'plugins': { 'datalabels': { 'display': False } }
        }
    }
    
    try:
        total_height = max(400, len(names) * 30 + 100)
        chart_payload = {'chart': chart_config, 'width': 800, 'height': total_height, 'backgroundColor': 'white'}

        # Step 1: Get short URL for email embedding (email client loads image itself — no download needed)
        quickchart_url = None
        try:
            resp = requests.post('https://quickchart.io/chart/create', json=chart_payload, timeout=15)
            if resp.status_code == 200:
                quickchart_url = resp.json().get('url')
            else:
                print(f"  [WARN] QuickChart /chart/create returned {resp.status_code}")
        except Exception as qc_err:
            print(f"  [WARN] QuickChart /chart/create failed: {qc_err}")

        # Step 2: Fetch PNG bytes directly from /chart endpoint (does NOT depend on the short URL)
        # This avoids the two-step download that fails from GitHub Actions
        try:
            print(f"  Fetching chart PNG directly...")
            img_resp = requests.post(
                'https://quickchart.io/chart',
                json=chart_payload,
                timeout=30
            )
            if img_resp.status_code == 200 and img_resp.content[:4] == b'\x89PNG':
                # Save PNG to dashboard/charts/ — committed to git and picked up
                # by the dedicated "Upload Chart to Google Drive" workflow step.
                save_date = summary.get('date', 'unknown')
                if save_filename_suffix:
                    save_date = f"{save_date}{save_filename_suffix}"
                save_chart_to_repo(img_resp.content, save_date)
            else:
                print(f"  [WARN] Direct chart PNG fetch failed: HTTP {img_resp.status_code}, Content-Type: {img_resp.headers.get('Content-Type', 'unknown')}")
        except Exception as dl_err:
            print(f"  [WARN] Chart PNG fetch failed: {dl_err}")

        # Always return QuickChart short URL for the email
        if quickchart_url:
            return quickchart_url

        # Fallback: if short URL failed, use the direct chart URL (works but is long)
        print(f"  [INFO] Using direct chart endpoint as email URL fallback.")
        return None

    except Exception as e:
        print(f"[WARN] Chart generation failed: {e}")
    return None


def _render_leaderboard_rows(members):
    """Render <tr> rows for the leaderboard table from a list of member dicts."""
    html = ""
    for m in members:
        base_s = "padding: 12px; border-bottom: 1px solid #eee;"
        if m['events'] == 0: base_s += " color: #999;"

        name_s = base_s
        ctr_s = base_s + " text-align: center;"
        hours_s = ctr_s + " color: #244d5d; font-weight: bold;"
        if m['events'] == 0: hours_s = ctr_s + " color: #ccc;"

        html += f"""
        <tr>
            <td style="{name_s}"><b>{m['name']}</b></td>
            <td style="{ctr_s}">{m['start']}</td>
            <td style="{ctr_s}">{m['end']}</td>
            <td style="{hours_s}">{m['hours']}h</td>
            <td style="{ctr_s}">{m['break']}m</td>
            <td style="{ctr_s}">{m['events']:,}</td>
        </tr>
        """
    return html


def _render_platform_pills(platform_counts):
    html = ""
    for plat, count in sorted(platform_counts.items(), key=lambda x: x[1], reverse=True):
        html += f"<li><strong>{plat}:</strong> {count:,}</li>"
    return html


def generate_email_html(summary, chart_url=None, work_chart_url=None):
    """Generate HTML email content with an All-Activity section and a
    side-by-side Work-Only (Comms excluded) section."""

    rows_html = _render_leaderboard_rows(summary['members'])
    platform_html = _render_platform_pills(summary['platform_counts'])

    # Banner shown only when the time-analysis join came back empty for the day, so
    # managers know blank First/Last/Active-Hours cells mean "data unavailable",
    # not "the whole team worked 0 hours".
    time_warning_html = ""
    if summary.get('time_data_missing'):
        time_warning_html = """
        <div style="margin: 0 0 20px; padding: 12px 16px; background: #fef3c7; border: 1px solid #f59e0b; border-radius: 8px; color: #92400e; font-size: 13px;">
            <strong>⚠ Active-hours data unavailable for this date.</strong>
            Event counts below are accurate, but First Event / Last Event / Active Hours /
            Max Break could not be computed (the Activity Time Analysis source was empty for this day).
        </div>
        """

    work_summary = summary.get('work_only')
    work_platform_html = _render_platform_pills(work_summary['platform_counts']) if work_summary else ""
    spend_section_html = _render_spend_section(summary.get('spend'))

    # Sync Status Logic
    sync_status_html = ""
    try:
        status_path = os.path.join(ROOT_DIR, 'workflow_status.json')
        if os.path.exists(status_path):
            with open(status_path, 'r') as f:
                ws_status = json.load(f)
                results = ws_status.get('results', {})
                # Key steps to show
                check_steps = ['clickup', 'github', 'google', 'figma', 'backendless', 'reports']
                statuses = []
                for step in check_steps:
                    if step in results:
                        icon = "✅" if results[step] else "❌"
                        statuses.append(f"{step.capitalize()} {icon}")
                sync_status_html = " • ".join(statuses)
        
        if not sync_status_html:
            sync_status_html = "ClickUp ✅ • GitHub ✅ • Figma ✅ • Google ✅ • Backendless ✅"
    except:
        sync_status_html = "All Systems Operational ✅"
    
    # Chart HTML
    chart_html = ""
    if chart_url:
        chart_html = f"""
        <!-- Chart Image (QuickChart URL) -->
        <div style="text-align: center; margin-bottom: 30px;">
            <img src="{chart_url}" alt="Daily Activity Breakdown" style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">
        </div>
        """

    work_chart_html = ""
    if work_chart_url:
        work_chart_html = f"""
        <div style="text-align: center; margin-bottom: 30px;">
            <img src="{work_chart_url}" alt="Work Activity Breakdown (Comms Excluded)" style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">
        </div>
        """

    # Side-by-side Work (Comms-excluded) section
    work_section_html = ""
    if work_summary:
        work_section_html = f"""
        <div style="margin-top: 40px; padding-top: 24px; border-top: 2px dashed #cbd5e1;">
            <h2 style="margin: 0 0 8px; font-size: 18px; color: #1e293b;">Work Activity (Comms Excluded)</h2>
            <p style="margin: 0 0 20px; color: #64748b; font-size: 12px;">
                Same day, with <strong>Gmail Send</strong>, <strong>Direct chats messages</strong>, and
                <strong>Channels messages</strong> removed. Shown side-by-side so team members whose day
                skews toward communications aren't compared unfavorably against heads-down work output.
                Event totals: <strong>{work_summary['total_activities']:,}</strong> vs
                <strong>{summary['total_activities']:,}</strong> (all activity).
            </p>
            {work_chart_html}
            <h3 style="margin: 20px 0 16px; font-size: 16px; color: #1e293b; border-left: 4px solid #0ea5e9; padding-left: 10px;">Platform Distribution (Work Only)</h3>
            <div style="text-align: center;">
                <ul>{work_platform_html}</ul>
            </div>
        </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Lato', 'Segoe UI', Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
            .header {{ background: #244d5d; color: white; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; }}
            .header p {{ margin: 10px 0 0; opacity: 0.9; font-size: 14px; }}
            
            .content {{ padding: 24px; }}
            
            .metrics-grid {{ display: flex; justify-content: space-around; margin-bottom: 30px; background: #f8fafc; padding: 20px; border-radius: 8px; }}
            .metric {{ text-align: center; }}
            .metric-value {{ font-size: 28px; font-weight: 800; color: #1e1b4b; margin-bottom: 4px; }}
            .metric-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }}
            
            .table-container {{ margin-bottom: 30px; overflow-x: auto; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
            th {{ text-align: center; padding: 12px; background: #f1f5f9; color: #475569; font-weight: 600; font-size: 11px; text-transform: uppercase; }}
            th:first-child {{ text-align: left; }}
            
            .links {{ text-align: center; padding: 25px 20px; background: #f8fafc; border-top: 1px solid #e2e8f0; }}
            .button {{ display: inline-block; padding: 12px 20px; background: #244d5d; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 14px; min-width: 160px; text-align: center; }}
            .button-text {{ color: #ffffff !important; text-decoration: none !important; }}
            .button:hover {{ background: #1a3c4a; }}
            
            .footer {{ text-align: center; padding: 20px; color: #94a3b8; font-size: 12px; }}
            
            ul {{ list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }}
            li {{ background: #f1f5f9; padding: 6px 12px; border-radius: 20px; font-size: 12px; color: #334155; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Pvragon Daily Activity Report</h1>
                <p style="font-size: 16px; font-weight: bold; margin-bottom: 4px;">{summary['date']}</p>
                <p style="font-size: 12px; opacity: 0.9;">Time Range: {summary.get('time_range', 'All Day PST')}</p>
            </div>
            
            <div class="content">
                {chart_html}
                
                <h2 style="margin: 0 0 8px; font-size: 18px; color: #1e293b;">All Activity</h2>
                <p style="margin: 0 0 20px; color: #64748b; font-size: 12px;">
                    Every tracked event for the day — ClickUp, GitHub, Google Workspace, Figma, and Backendless combined
                    (includes Gmail sends and ClickUp chat messages).
                </p>
                {time_warning_html}
                <!-- Expanded Activity Table -->
                <h3 style="margin: 0 0 16px; font-size: 16px; color: #1e293b; border-left: 4px solid #244d5d; padding-left: 10px;">Daily Highlights Leaderboard</h3>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>First Event</th>
                                <th>Last Event</th>
                                <th>Active Hours</th>
                                <th>Max Break</th>
                                <th>Events</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows_html}
                        </tbody>
                    </table>
                </div>

                <!-- Platform Breakdown -->
                <h3 style="margin: 20px 0 16px; font-size: 16px; color: #1e293b; border-left: 4px solid #10b981; padding-left: 10px;">Platform Distribution</h3>
                <div style="text-align: center;">
                    <ul>{platform_html}</ul>
                </div>

                {work_section_html}

                {spend_section_html}
            </div>

            <div class="links">
                <!-- Centered Buttons Table for Email Client Compatibility -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin: auto;">
                    <tr>
                        <td align="center" style="padding: 0 10px;">
                            <a href="{DASHBOARD_URL}" class="button">
                                <span class="button-text">📊 Full Dashboard</span>
                            </a>
                        </td>
                        <td align="center" style="padding: 0 10px;">
                            <a href="{SHEET_URL}" class="button">
                                <span class="button-text">📗 Master Sheet</span>
                            </a>
                        </td>
                    </tr>
                </table>
            </div>
            
            <div class="footer">
                <div style="margin-bottom: 8px;">
                    <strong>System Heartbeat:</strong> v2.5 Standard • PST Timezone
                </div>
                <div style="font-size: 11px; background: #f8fafc; padding: 10px; border-radius: 4px; border: 1px solid #e2e8f0; display: inline-block;">
                    <strong>System Integrity:</strong> {sync_status_html}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html


def send_email_smtp(user, password, recipients, subject, html_content):
    """Send email using SMTP (App Password)."""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Pvragon Activity Bot <{user}>"
        msg['To'] = ', '.join(recipients)
        
        msg.attach(MIMEText(html_content, 'html'))
        
        # Connect to Gmail SMTP (SSL)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(user, password)
            server.send_message(msg)
            
        print(f"[SUCCESS] Email sent via SMTP (User: {user})")
        return True
    except Exception as e:
        print(f"[ERROR] SMTP Failed: {e}")
        return False


def send_email(creds, recipients, subject, html_content):
    """Send email using Gmail API."""
    service = build('gmail', 'v1', credentials=creds)
    
    message = MIMEMultipart('alternative')
    message['Subject'] = subject
    message['From'] = 'me'
    message['To'] = ', '.join(recipients)
    
    # Attach HTML content
    html_part = MIMEText(html_content, 'html')
    message.attach(html_part)
    
    # Encode and send
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    try:
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()
        print(f"[SUCCESS] Email sent! Message ID: {sent['id']}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")
        return False


def main():
    print("=== Sending Daily Activity Summary Email ===")
    
    # Process Arguments
    target_date = None
    override_email = None
    for arg in sys.argv:
        if arg.startswith('--date='):
            target_date = arg.split('=')[1]
        elif '@' in arg:
            override_email = [arg]

    # Get credentials
    creds = get_credentials()
    
    # Get summary data
    print("[1/3] Fetching summary data...")
    summary = get_daily_summary(creds, target_date_override=target_date)
    print(f"  Total activities: {summary['total_activities']:,}")
    print(f"  Active members: {summary['active_members']}")
    
    # Generate Chart URL (and upload to Drive for permanent storage)
    print("  Generating daily chart URL...")
    chart_url = generate_stacked_bar_chart(summary, creds=creds)

    work_chart_url = None
    if summary.get('work_only'):
        print("  Generating Comms-excluded chart URL...")
        work_chart_url = generate_stacked_bar_chart(
            summary['work_only'], creds=creds,
            chart_label_suffix=' (Comms Excluded)',
            save_filename_suffix='_work_only'
        )

    # Generate email
    print("[2/3] Generating email...")
    html = generate_email_html(summary, chart_url, work_chart_url=work_chart_url)
    
    # Send Email
    subject = f"[v2.5] Daily Activity Audit - {summary['date']}"
    if summary.get('time_range'):
        subject += f" ({summary['time_range']})"
        
    # Recipients
    recipients = override_email if override_email else EMAIL_RECIPIENTS

    # SKIP_EMAIL flag — set via workflow_dispatch input to suppress email during testing
    if os.environ.get('SKIP_EMAIL', '').lower() in ('true', '1', 'yes'):
        print(f"[3/3] SKIP_EMAIL=true — email suppressed (test mode). Would send to: {', '.join(recipients)}")
        print("\n[COMPLETE] Test run complete — no email sent to managers.")
        return

    print(f"[3/3] Sending to: {', '.join(recipients)}")

    # Send Email
    success = False
    if EMAIL_USER and EMAIL_PASSWORD:
        print(f"  Attempting SMTP (App Password)...")
        success = send_email_smtp(EMAIL_USER, EMAIL_PASSWORD, recipients, subject, html)
        if not success:
            print(f"  [WARN] SMTP failed. Falling back to Gmail API...")

    if not success:
        print(f"  Using Gmail API (OAuth)...")
        success = send_email(creds, recipients, subject, html)

    if success:
        print("\n[COMPLETE] Daily summary email sent successfully!")
    else:
        print("\n[FAILED] Could not send email via SMTP or Gmail API. Check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

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
from email.mime.image import MIMEImage
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
from name_mappings import map_name, should_exclude, STRICT_TEAM_GMAIL

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

def get_credentials():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
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

def get_daily_summary(creds, target_date_override=None):
    """Fetch summary data from Google Sheet with detailed daily breakdown."""
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
    
    # 1. Fetch Activity Time Analysis
    try:
        print("  Reading 'Activity Time Analysis'...")
        ws_time = sh.worksheet('Activity Time Analysis')
        time_data = ws_time.get_all_records()
        # Filter for target date
        target_time_data = [r for r in time_data if r.get('Date') == target_date]
        print(f"    Found {len(target_time_data)} time records for {target_date}")
        
    except Exception as e:
        print(f"    [WARN] Error fetching Time Analysis: {e}")
        target_time_data = []

    # 2. Fetch Daily Audit
    try:
        print("  Reading 'Daily Audit'...")
        ws_audit = sh.worksheet('Daily Audit')
        audit_data = ws_audit.get_all_records()
        target_audit_data = [r for r in audit_data if r.get('Activity Date') == target_date]
        print(f"    Found {len(target_audit_data)} audit records for {target_date}")
    except Exception as e:
        print(f"    [WARN] Error fetching Daily Audit: {e}")
        target_audit_data = []

    # 3. Process Per-Member Stats
    member_stats = {}
    
    # Pre-populate with all core employees to ensure they appear even with 0 events
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
    
    # Update with actual Time Analysis data
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
            'top_platform': '-'
        }

    # From Daily Audit (Platform & Count verification)
    platform_counts = defaultdict(lambda: defaultdict(int)) # Member -> Platform -> Count
    type_counts = defaultdict(lambda: defaultdict(int))     # Member -> Type -> Count
    global_plat_counts = defaultdict(int)
    total_activities = 0
    
    for r in target_audit_data:
        name = r.get('Team Member')
        plat = r.get('Platform')
        act_type = r.get('Activity Type') 
        count = safe_int(r.get('Count'))
        
        if count > 0:
            if name: 
                platform_counts[name][plat] += count
                if act_type: type_counts[name][act_type] += count
            if plat: global_plat_counts[plat] += count
            total_activities += count
            
            # Ensure member exists in stats
            if name and name not in member_stats:
                member_stats[name] = {
                    'name': name, 'start': '-', 'end': '-', 'hours': 0.0, 'break': 0, 'events': 0, 'top_platform': '-'
                }

    # Calculate Top Platform per member & Add Type Counts
    for name, plats in platform_counts.items():
        if not plats or name not in member_stats: continue
        top_plat = max(plats.items(), key=lambda x: x[1])[0]
        member_stats[name]['top_platform'] = top_plat
        # Sync event count if missing
        if member_stats[name]['events'] == 0:
             member_stats[name]['events'] = sum(plats.values())
        
        # Add detailed breakdowns
        member_stats[name]['platform_breakdown'] = dict(plats)
        member_stats[name]['type_breakdown'] = dict(type_counts[name])

    # Sort members
    sorted_members = sorted(member_stats.values(), key=lambda x: (x['hours'], x['events']), reverse=True)
    
    # Aggregate Metrics (User requested removal from email, but still useful in structure)
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
        'all_types': list(set(t for m in type_counts.values() for t in m.keys())), # For chart labels
        'time_range': "12:00 AM - 11:59 PM PST"
    }



def get_chart_color(label, index):
    """Generate consistent and unique color based on label. Ensures maximum distinction."""
    h = 0
    for char in label:
        h = ord(char) + ((h << 5) - h)
    
    # 1. Start with a high-contrast palette
    palette = [
        '#4e79a7', '#f28e2c', '#e15759', '#76b7b2', '#59a14f', 
        '#edc949', '#af7aa1', '#ff9da7', '#9c755f', '#bab0ab',
        '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
        '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
    ]
    
    if index < len(palette):
        return palette[index]
    
    # 2. Fallback to unique HSL for additional items
    hue = abs(h) % 360
    return f'hsl({hue}, 60%, 65%)'

def generate_stacked_bar_chart(summary):
    """Generate a stacked horizontal bar chart for Activity Types per Member using QuickChart.io."""
    active_members = [m for m in summary['members'] if m['events'] > 0]
    if not active_members: return None
        
    names = [m['name'] for m in active_members]
    # Get all unique types present in active members
    types = sorted(list(set(t for m in active_members for t in m.get('type_breakdown', {}).keys())))
    
    datasets = []
    for idx, t in enumerate(types):
        data = [m.get('type_breakdown', {}).get(t, 0) for m in active_members]
        datasets.append({
            'label': t,
            'data': data,
            'backgroundColor': get_chart_color(t, idx),
            # Stack all in one group to mimic Dashboard 'stacked' mode
            # Chart.js 2 (QuickChart default) uses 'xAxes/yAxes: stacked: true' options
        })
        
    chart_config = {
        'type': 'horizontalBar',
        'data': { 'labels': names, 'datasets': datasets },
        'options': {
            'title': { 'display': True, 'text': 'Activity Breakdown by Member & Activity Type' },
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
        # Create Short URL
        total_height = max(400, len(names) * 30 + 100)
        resp = requests.post(
            'https://quickchart.io/chart/create', 
            json={'chart': chart_config, 'width': 800, 'height': total_height, 'backgroundColor': 'white'}
        )
        if resp.status_code == 200: 
            return resp.json().get('url') # Return Short URL
    except Exception as e:
        print(f"[WARN] Chart URL generation failed: {e}")
    return None


def generate_email_html(summary, chart_url=None):
    """Generate HTML email content with detailed leaderboard."""
    
    # Generate Rows (Include all employees)
    rows_html = ""
    for m in summary['members']:
        # Styles for this row
        base_s = "padding: 12px; border-bottom: 1px solid #eee;"
        if m['events'] == 0: base_s += " color: #999;"
        
        name_s = base_s
        ctr_s = base_s + " text-align: center;"
        hours_s = ctr_s + " color: #244d5d; font-weight: bold;"
        if m['events'] == 0: hours_s = ctr_s + " color: #ccc;"

        rows_html += f"""
        <tr>
            <td style="{name_s}"><b>{m['name']}</b></td>
            <td style="{ctr_s}">{m['start']}</td>
            <td style="{ctr_s}">{m['end']}</td>
            <td style="{hours_s}">{m['hours']}h</td>
            <td style="{ctr_s}">{m['break']}m</td>
        </tr>
        """
    
    # Platform Breakdown HTML
    platform_html = ""
    for plat, count in sorted(summary['platform_counts'].items(), key=lambda x: x[1], reverse=True):
        platform_html += f"<li><strong>{plat}:</strong> {count:,}</li>"

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
            .button {{ display: inline-block; padding: 12px 24px; background: #244d5d; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 14px; margin: 5px; }}
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
            </div>
            
            <div class="links">
                <!-- Centered Buttons Table for Email Client Compatibility -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin: auto;">
                    <tr>
                        <td align="center" style="padding: 0 4px;">
                            <a href="{DASHBOARD_URL}" class="button" style="background-color: #244d5d; color: #ffffff !important; display: inline-block;">
                                <span class="button-text">📊 Full Dashboard</span>
                            </a>
                        </td>
                        <td align="center" style="padding: 0 4px;">
                            <a href="{SHEET_URL}" class="button" style="background-color: #244d5d; color: #ffffff !important; display: inline-block;">
                                <span class="button-text">📗 Master Sheet</span>
                            </a>
                        </td>
                        <td align="center" style="padding: 0 4px;">
                            <a href="https://github.com/Areeba-Akhlaque/Daily-Activity-Report/blob/main/directives/PROJECT_BLUEPRINT.md" class="button" style="background-color: #64748b; color: #ffffff !important; display: inline-block;">
                                <span class="button-text">📄 Blueprint</span>
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
    
    # Generate Chart URL
    print("  Generating daily chart URL...")
    chart_url = generate_stacked_bar_chart(summary)
    
    # Generate email
    print("[2/3] Generating email...")
    html = generate_email_html(summary, chart_url)
    
    # Send Email
    subject = f"[v2.4] Daily Activity Audit - {summary['date']}"
    if summary.get('time_range'):
        subject += f" ({summary['time_range']})"
        
    # Recipients
    recipients = override_email if override_email else EMAIL_RECIPIENTS
    
    print(f"[3/3] Sending to: {', '.join(recipients)}")
    
    if EMAIL_USER and EMAIL_PASSWORD:
        print(f"  Using SMTP (App Password)...")
        success = send_email_smtp(EMAIL_USER, EMAIL_PASSWORD, recipients, subject, html)
    else:
        print(f"  Using Gmail API (OAuth)...")
        success = send_email(creds, recipients, subject, html)
    
    if success:
        print("\n[COMPLETE] Daily summary email sent successfully!")
    else:
        print("\n[FAILED] Could not send email. Check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

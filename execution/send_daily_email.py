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

from collections import defaultdict
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

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
EMAIL_RECIPIENTS = os.environ.get('EMAIL_RECIPIENTS', 'areeba@pvragon.com').split(',')
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

def get_daily_summary(creds):
    """Fetch summary data from Google Sheet with detailed daily breakdown."""
    print("Fetching daily summary data...")
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    # Get Date Logic (Today vs Yesterday if run early)
    today = datetime.now()
    today_str = today.strftime('%m/%d/%y')
    yesterday_str = (today - timedelta(days=1)).strftime('%m/%d/%y')
    print(f"Target Date (Today): {today_str}")
    
    # 1. Fetch Activity Time Analysis
    try:
        print("  Reading 'Activity Time Analysis'...")
        ws_time = sh.worksheet('Activity Time Analysis')
        time_data = ws_time.get_all_records()
        today_time = [r for r in time_data if r.get('Date') == today_str]
        yesterday_time = [r for r in time_data if r.get('Date') == yesterday_str]
        
        target_date = today_str if today_time else yesterday_str
        target_time_data = today_time if today_time else yesterday_time
        print(f"    Found {len(target_time_data)} time records for {target_date}")
        
    except Exception as e:
        print(f"    [WARN] Error fetching Time Analysis: {e}")
        target_date = today_str
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
    
    # From Time Analysis
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
        'all_types': list(set(t for m in type_counts.values() for t in m.keys())) # For chart labels
    }



def get_chart_color(label, index):
    """Generate consistent color based on label hash. Matches dashboard style consistently."""
    # Dashboard palette logic: simple hash
    h = 0
    for char in label:
        h = ord(char) + ((h << 5) - h)
    
    # Use a fixed palette to pick from based on hash
    palette = [
        '#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', 
        '#ec4899', '#14b8a6', '#f97316', '#06b6d4', '#84cc16'
    ]
    color = palette[abs(h) % len(palette)]
    return color

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
            'legend': { 'display': False },
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
    
    # Generate Rows
    rows_html = ""
    for m in summary['members']:
        if m['events'] == 0: continue
        rows_html += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #eee;"><b>{m['name']}</b></td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">{m['start']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">{m['end']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center; color: #6366f1; font-weight: bold;">{m['hours']}h</td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">{m['break']}m</td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">{m['events']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;"><span style="background: #eef2ff; color: #4f46e5; padding: 4px 8px; border-radius: 12px; font-size: 11px;">{m['top_platform']}</span></td>
        </tr>
        """
    
    # Platform Global
    platform_html = ''.join([f"<li>{p}: {c:,}</li>" for p, c in sorted(summary['platform_counts'].items(), key=lambda x: x[1], reverse=True)])
    
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
            .header {{ background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); color: white; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 26px; font-weight: 700; }}
            .header p {{ margin: 10px 0 0; opacity: 0.8; font-size: 14px; }}
            
            .content {{ padding: 24px; }}
            
            .metrics-grid {{ display: flex; justify-content: space-around; margin-bottom: 30px; background: #f8fafc; padding: 20px; border-radius: 8px; }}
            .metric {{ text-align: center; }}
            .metric-value {{ font-size: 28px; font-weight: 800; color: #1e1b4b; margin-bottom: 4px; }}
            .metric-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }}
            
            .table-container {{ margin-bottom: 30px; overflow-x: auto; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
            th {{ text-align: center; padding: 12px; background: #f1f5f9; color: #475569; font-weight: 600; font-size: 11px; text-transform: uppercase; }}
            th:first-child {{ text-align: left; }}
            
            .links {{ text-align: center; padding: 20px; background: #f8fafc; border-top: 1px solid #e2e8f0; }}
            .button {{ display: inline-block; padding: 12px 24px; background: #6366f1; color: white; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 14px; transition: background 0.2s; }}
            .button:hover {{ background: #4f46e5; }}
            
            .footer {{ text-align: center; padding: 20px; color: #94a3b8; font-size: 12px; }}
            
            ul {{ list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }}
            li {{ background: #f1f5f9; padding: 6px 12px; border-radius: 20px; font-size: 12px; color: #334155; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Pvragon Daily Activity Report</h1>
                <p>{summary['date']}</p>
            </div>
            
            <div class="content">
                {chart_html}
                
                <!-- Expanded Activity Table -->
                <h3 style="margin: 0 0 16px; font-size: 16px; color: #1e293b; border-left: 4px solid #6366f1; padding-left: 10px;">Daily Highlights Leaderboard</h3>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>First Event</th>
                                <th>Last Event</th>
                                <th>Duration</th>
                                <th>Max Break</th>
                                <th>Events</th>
                                <th>Top Platform</th>
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
                <a href="{DASHBOARD_URL}" class="button">📊 Open Full Dashboard</a>
                <p style="margin-top: 12px; font-size: 12px;"><a href="{SHEET_URL}" style="color: #6366f1; text-decoration: none;">View Source Spreadsheet</a></p>
            </div>
            
            <div class="footer">
                Generated automatically by Pvragon Bot • {datetime.now().strftime('%I:%M %p')}
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
    
    # Get credentials
    creds = get_credentials()
    
    # Get summary data
    print("[1/3] Fetching summary data...")
    summary = get_daily_summary(creds)
    print(f"  Total activities: {summary['total_activities']:,}")
    print(f"  Active members: {summary['active_members']}")
    
    # Generate Chart URL
    print("  Generating daily chart URL...")
    chart_url = generate_stacked_bar_chart(summary)
    
    # Generate email
    print("[2/3] Generating email...")
    html = generate_email_html(summary, chart_url)
    subject = f"📊 Daily Activity Report - {summary['date']}"
    
    # Determine recipients (allow override via CLI)
    recipients = EMAIL_RECIPIENTS
    if len(sys.argv) > 1:
        recipients = [sys.argv[1]]
        print(f"[Override] Sending test email to: {recipients[0]}")
    
    # Send email
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

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
def get_daily_summary(creds):
    """Fetch summary data from Google Sheet with detailed daily breakdown."""
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    # Get Date Logic (Today vs Yesterday if run early)
    # We prefer today's data if available, else yesterday.
    today = datetime.now()
    today_str = today.strftime('%m/%d/%y')
    yesterday_str = (today - timedelta(days=1)).strftime('%m/%d/%y')
    
    # 1. Fetch Activity Time Analysis (for Start/End/Duration)
    try:
        ws_time = sh.worksheet('Activity Time Analysis')
        time_data = ws_time.get_all_records()
        # Check both dates
        today_time = [r for r in time_data if r.get('Date') == today_str]
        yesterday_time = [r for r in time_data if r.get('Date') == yesterday_str]
        
        # Select best dataset (prefer Today if it has substantial data, else Yesterday)
        # Threshold: if > 0 records for today, use today.
        target_date = today_str if today_time else yesterday_str
        target_time_data = today_time if today_time else yesterday_time
        
    except Exception as e:
        print(f"Error fetching Time Analysis: {e}")
        target_date = today_str
        target_time_data = []

    # 2. Fetch Daily Audit (for Platform breakdown and total counts)
    try:
        ws_audit = sh.worksheet('Daily Audit')
        audit_data = ws_audit.get_all_records()
        target_audit_data = [r for r in audit_data if r.get('Activity Date') == target_date]
    except Exception as e:
        print(f"Error fetching Daily Audit: {e}")
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
            'hours': float(r.get('Active Window (Hours)', 0)),
            'break': int(r.get('Longest Break (Minutes)', 0)),
            'events': int(r.get('Total Events', 0)),
            'top_platform': '-'
        }

    # From Daily Audit (Platform & Count verification)
    platform_counts = defaultdict(lambda: defaultdict(int)) # Member -> Platform -> Count
    total_activities = 0
    
    for r in target_audit_data:
        name = r.get('Team Member')
        plat = r.get('Platform')
        count = int(r.get('Count', 0))
        if count > 0:
            platform_counts[name][plat] += count
            total_activities += count
            
            # Ensure member exists in stats (if they missed Time Analysis somehow)
            if name not in member_stats:
                member_stats[name] = {
                    'name': name, 'start': '-', 'end': '-', 'hours': 0, 'break': 0, 'events': 0, 'top_platform': '-'
                }

    # Calculate Top Platform per member
    for name, plats in platform_counts.items():
        if not plats: continue
        top_plat = max(plats.items(), key=lambda x: x[1])[0]
        member_stats[name]['top_platform'] = top_plat
        # Sync event count just in case
        if member_stats[name]['events'] == 0:
             member_stats[name]['events'] = sum(plats.values())

    # Sort members by Active Hours (desc) then Event Count
    sorted_members = sorted(member_stats.values(), key=lambda x: (x['hours'], x['events']), reverse=True)
    
    # Aggregate Metrics
    active_members_count = len([m for m in sorted_members if m['events'] > 0])
    avg_hours = sum(m['hours'] for m in sorted_members) / active_members_count if active_members_count else 0
    avg_break = sum(m['break'] for m in sorted_members) / active_members_count if active_members_count else 0
    
    # Global Platform Breakdown
    global_plat_counts = defaultdict(int)
    for r in target_audit_data:
        global_plat_counts[r.get('Platform')] += int(r.get('Count', 0))

    return {
        'date': target_date,
        'total_activities': total_activities,
        'active_members': active_members_count,
        'avg_hours': round(avg_hours, 1),
        'avg_break': round(avg_break),
        'members': sorted_members,
        'platform_counts': dict(global_plat_counts)
    }


def generate_email_html(summary):
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
                <!-- Summary Metrics -->
                <div class="metrics-grid">
                    <div class="metric">
                        <div class="metric-value">{summary['total_activities']:,}</div>
                        <div class="metric-label">Total Events</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{summary['active_members']}</div>
                        <div class="metric-label">Active Members</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{summary['avg_hours']}h</div>
                        <div class="metric-label">Avg Hours</div>
                    </div>
                </div>
                
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
    
    # Generate email
    print("[2/3] Generating email...")
    html = generate_email_html(summary)
    subject = f"📊 Daily Activity Report - {summary['date']}"
    
    # Send email
    print(f"[3/3] Sending to: {', '.join(EMAIL_RECIPIENTS)}")
    
    if EMAIL_USER and EMAIL_PASSWORD:
        print(f"  Using SMTP (App Password)...")
        success = send_email_smtp(EMAIL_USER, EMAIL_PASSWORD, EMAIL_RECIPIENTS, subject, html)
    else:
        print(f"  Using Gmail API (OAuth)...")
        success = send_email(creds, EMAIL_RECIPIENTS, subject, html)
    
    if success:
        print("\n[COMPLETE] Daily summary email sent successfully!")
    else:
        print("\n[FAILED] Could not send email. Check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

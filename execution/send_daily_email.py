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
EMAIL_RECIPIENTS = os.environ.get('EMAIL_RECIPIENTS', 'areeba@pvragon.com,jaime@pvragon.com').split(',')
EMAIL_USER = os.environ.get('EMAIL_USER', '')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')
DASHBOARD_URL = os.environ.get('DASHBOARD_URL', 'https://pvragon.github.io/activity-dashboard')
SHEET_URL = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}'


def get_credentials():
    """Get Google OAuth credentials."""
    token_path = os.path.join(ROOT_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def get_daily_summary(creds):
    """Fetch summary data from Google Sheet."""
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    
    # Get today's date string (MM/DD/YY format)
    today = datetime.now()
    today_str = today.strftime('%m/%d/%y')
    yesterday_str = (today - timedelta(days=1)).strftime('%m/%d/%y')
    
    # Fetch Daily Audit data
    ws = sh.worksheet('Daily Audit')
    data = ws.get_all_records()
    
    # Filter for today or yesterday (depending on when run)
    today_data = [r for r in data if r.get('Activity Date') in [today_str, yesterday_str]]
    
    # Calculate metrics
    total_activities = sum(int(r.get('Count', 0)) for r in today_data)
    
    # Get unique active members
    active_members = set(r.get('Team Member') for r in today_data if int(r.get('Count', 0)) > 0)
    
    # Get top performers
    member_counts = {}
    for r in today_data:
        member = r.get('Team Member', '')
        count = int(r.get('Count', 0))
        member_counts[member] = member_counts.get(member, 0) + count
    
    top_performers = sorted(member_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Get platform breakdown
    platform_counts = {}
    for r in today_data:
        platform = r.get('Platform', '')
        count = int(r.get('Count', 0))
        platform_counts[platform] = platform_counts.get(platform, 0) + count
    
    # Fetch Time Analysis for avg hours
    try:
        ws_time = sh.worksheet('Activity Time Analysis')
        time_data = ws_time.get_all_records()
        time_today = [r for r in time_data if r.get('Date') in [today_str, yesterday_str]]
        
        if time_today:
            avg_hours = sum(float(r.get('Active Window (Hours)', 0)) for r in time_today) / len(time_today)
            avg_break = sum(int(r.get('Longest Break (Minutes)', 0)) for r in time_today) / len(time_today)
        else:
            avg_hours = 0
            avg_break = 0
    except:
        avg_hours = 0
        avg_break = 0
    
    return {
        'date': yesterday_str if not today_data else today_str,
        'total_activities': total_activities,
        'active_members': len(active_members),
        'avg_hours': round(avg_hours, 1),
        'avg_break': round(avg_break),
        'top_performers': top_performers,
        'platform_counts': platform_counts
    }


def generate_email_html(summary):
    """Generate HTML email content."""
    # Format top performers
    top_performers_html = ', '.join([f"<b>{name}</b> ({count})" for name, count in summary['top_performers']])
    
    # Format platform breakdown
    platform_html = ''.join([f"<li>{p}: {c:,}</li>" for p, c in sorted(summary['platform_counts'].items(), key=lambda x: x[1], reverse=True)])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #6366f1, #818cf8); color: white; padding: 24px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; }}
            .header p {{ margin: 8px 0 0; opacity: 0.9; }}
            .content {{ padding: 24px; }}
            .metric {{ display: inline-block; width: 48%; text-align: center; padding: 16px 0; }}
            .metric-value {{ font-size: 32px; font-weight: bold; color: #6366f1; }}
            .metric-label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
            .section {{ margin: 20px 0; padding: 16px; background: #f9fafb; border-radius: 8px; }}
            .section h3 {{ margin: 0 0 12px; color: #333; font-size: 14px; text-transform: uppercase; }}
            .links {{ text-align: center; padding: 20px; background: #f0f0ff; }}
            .links a {{ display: inline-block; margin: 8px; padding: 12px 24px; background: #6366f1; color: white; text-decoration: none; border-radius: 6px; font-weight: 500; }}
            .links a:hover {{ background: #5558e3; }}
            .footer {{ text-align: center; padding: 16px; color: #999; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 Daily Activity Report</h1>
                <p>{summary['date']}</p>
            </div>
            <div class="content">
                <div style="text-align: center;">
                    <div class="metric">
                        <div class="metric-value">{summary['total_activities']:,}</div>
                        <div class="metric-label">Total Activities</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{summary['active_members']}</div>
                        <div class="metric-label">Active Members</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{summary['avg_hours']}h</div>
                        <div class="metric-label">Avg Active Hours</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value">{summary['avg_break']}m</div>
                        <div class="metric-label">Avg Longest Break</div>
                    </div>
                </div>
                
                <div class="section">
                    <h3>🏆 Top Performers</h3>
                    <p>{top_performers_html}</p>
                </div>
                
                <div class="section">
                    <h3>📱 Platform Breakdown</h3>
                    <ul>{platform_html}</ul>
                </div>
            </div>
            
            <div class="links">
                <a href="{SHEET_URL}">📄 View Full Report</a>
                <a href="{DASHBOARD_URL}">📊 Open Dashboard</a>
            </div>
            
            <div class="footer">
                Pvragon Activity Bot • Generated automatically at 7:00 PM PST
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

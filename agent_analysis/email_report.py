import os
import sys
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
import markdown

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from site_config import SITE_SHORT_NAME


EMAIL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #2c3e50; background-color: #f5f7fa; margin: 0; padding: 0; }
.container { max-width: 720px; margin: 24px auto; background: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow: hidden; }
.header { background: linear-gradient(135deg, #1a4d8c 0%, #2d6cb8 100%); color: #ffffff; padding: 28px 36px; }
.header h1 { margin: 0; font-size: 22px; font-weight: 600; letter-spacing: 0.2px; }
.header .subtitle { margin-top: 6px; font-size: 14px; opacity: 0.9; }
.content { padding: 32px 36px; }
.content h1 { font-size: 24px; color: #1a4d8c; margin-top: 0; border-bottom: 2px solid #e1e8f0; padding-bottom: 10px; }
.content h2 { font-size: 18px; color: #1a4d8c; margin-top: 28px; margin-bottom: 10px; }
.content h3 { font-size: 15px; color: #34495e; margin-top: 20px; margin-bottom: 8px; }
.content p { margin: 10px 0; font-size: 14px; }
.content ul, .content ol { padding-left: 22px; font-size: 14px; }
.content li { margin: 6px 0; }
.content strong { color: #1a4d8c; }
.content hr { border: none; border-top: 1px solid #e1e8f0; margin: 24px 0; }
.content em { color: #5a6c7d; }
.content code { background: #f1f4f8; padding: 2px 6px; border-radius: 3px; font-size: 13px; font-family: "SF Mono", Consolas, monospace; }
.footer { background: #f5f7fa; padding: 18px 36px; font-size: 12px; color: #7a8a9a; border-top: 1px solid #e1e8f0; }
"""


def build_html_email(md_text: str, report_date: str) -> str:
    html_body = markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{EMAIL_CSS}</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Weekly Analysis Report</h1>
      <div class="subtitle">{report_date}</div>
    </div>
    <div class="content">
      {html_body}
    </div>
    <div class="footer">
      Generated automatically by the {SITE_SHORT_NAME} AI analysis agent.
    </div>
  </div>
</body>
</html>"""


def send_daily_report():
    load_dotenv()

    sender_email = os.environ.get("SENDER_EMAIL")
    sender_password = os.environ.get("SENDER_PASSWORD")
    recipient_env = os.environ.get("RECIPIENT_EMAIL", "")
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))

    # Build the recipient list from RECIPIENT_EMAIL (comma-separated), deduped.
    env_recipients = [e.strip() for e in recipient_env.split(",") if e.strip()]
    seen = set()
    recipients = []
    for addr in env_recipients:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            recipients.append(addr)

    if not all([sender_email, sender_password, recipients]):
        print("Error: Missing email configuration.")
        print("Please ensure SENDER_EMAIL, SENDER_PASSWORD, and RECIPIENT_EMAIL are set in your .env file.")
        return

    current_date_str = datetime.datetime.now().strftime("%Y_%m_%d")
    report_filename = f"{current_date_str}_analysis_report.md"

    script_dir = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(script_dir, "analysis_reports", report_filename)

    if not os.path.exists(report_path):
        print(f"Error: The report for today '{report_filename}' was not found in 'analysis_reports'.")
        print("Please make sure the analysis_agent.py script has run today before running this script.")
        return

    print(f"Found today's report: {report_filename}")
    print(f"Preparing to send email to: {', '.join(recipients)}...")

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            md_text = f.read()
    except Exception as e:
        print(f"Failed to read report: {e}")
        return

    report_date = datetime.datetime.now().strftime("%B %d, %Y")
    html_body = build_html_email(md_text, report_date)

    msg = MIMEMultipart("alternative")
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"Weekly Analysis Report - {report_date}"

    msg.attach(MIMEText(md_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipients, msg.as_string())
        server.quit()
        print("Email sent successfully!")
    except smtplib.SMTPAuthenticationError as e:
        print(f"Auth failed: {e}")
    except Exception as e:
        print(f"Failed to send: {e}")


if __name__ == "__main__":
    send_daily_report()

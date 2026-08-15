"""Sends the daily match digest via Gmail SMTP (BUILD_PLAN.md item 9).

Uses a Gmail App Password (not your real Google account password) - see
GITHUB_ACTIONS_SETUP.md for how to generate one. Deliberately plain smtplib against Gmail's
own SMTP server rather than a transactional-email service like SES/SendGrid: this is a single
recipient getting at most one email a day, nowhere near Gmail's own sending limits (500/day
for a regular account) - a dedicated email API would be pure overhead for this volume.

Reads GMAIL_ADDRESS/GMAIL_APP_PASSWORD/NOTIFY_EMAIL from the environment (populated from
GitHub Actions secrets in the scheduled workflow, or from your local .env when testing by
hand) - same load_dotenv() pattern gemini_utils.py and the connector modules already use, so
running `python email_sender.py` locally picks up your .env without any extra setup.
"""
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
# Defaults to emailing yourself - set NOTIFY_EMAIL only if you want the digest to go somewhere
# other than the Gmail account that's sending it.
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL") or GMAIL_ADDRESS


def _build_html_body(matches: list[dict]) -> str:
    rows = []
    for job in matches:
        tier = job.get("match_tier", "")
        score = job.get("match_score", "")
        title = job.get("title") or "(untitled role)"
        company = job.get("company") or ""
        location = job.get("location") or "location not listed"
        url = job.get("url") or "#"
        points = ", ".join(job.get("match_points") or [])
        points_html = (
            f'<div style="color:#666;font-size:12px;margin-top:4px;">Matches: {points}</div>'
            if points else ""
        )
        rows.append(f"""
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #e5e5e5;">
                <div style="font-weight:600;font-size:15px;color:#111;">{title}</div>
                <div style="color:#555;font-size:13px;">{company} - {location}</div>
                <div style="color:#2563eb;font-size:12px;font-weight:600;margin-top:2px;">
                  {tier} match - score {score}
                </div>
                {points_html}
                <a href="{url}" style="font-size:13px;">View job posting -&gt;</a>
              </td>
            </tr>
        """)

    plural = "es" if len(matches) != 1 else ""
    return f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif;max-width:600px;">
      <h2 style="color:#111;">JobScout AI - {len(matches)} new match{plural} today</h2>
      <p style="color:#555;font-size:13px;">
        These are jobs scored for the first time by today's run - matches you've already
        been emailed about won't show up again.
      </p>
      <table style="width:100%;border-collapse:collapse;">{''.join(rows)}</table>
      <p style="color:#999;font-size:11px;margin-top:24px;">
        Sent automatically by your JobScout AI GitHub Actions workflow. To stop these emails,
        disable or delete the scheduled workflow in your fork's Actions tab.
      </p>
    </body></html>
    """


def send_digest_email(matches: list[dict], attachment_path: str | None = None):
    """Emails the given matches as an HTML digest, optionally with a PDF attached.

    Raises RuntimeError (rather than silently no-op-ing) if the Gmail credentials aren't
    configured - a scheduled job that "successfully" finds matches but never actually sends
    the email it exists to send should fail loudly, not print a warning nobody's watching for.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set (as GitHub Actions secrets, or "
            "in your local .env) to send the digest email."
        )
    if not NOTIFY_EMAIL:
        raise RuntimeError("No recipient configured - set NOTIFY_EMAIL or GMAIL_ADDRESS.")

    msg = MIMEMultipart("mixed")
    plural = "es" if len(matches) != 1 else ""
    msg["Subject"] = f"JobScout AI: {len(matches)} new match{plural} today"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_EMAIL

    msg.attach(MIMEText(_build_html_body(matches), "html"))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment_path)}"'
        msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [NOTIFY_EMAIL], msg.as_string())


if __name__ == "__main__":
    # Manual smoke test - run `python email_sender.py` locally (with GMAIL_ADDRESS/
    # GMAIL_APP_PASSWORD in your .env) to confirm sending works before trusting the scheduled
    # workflow with it. Sends one fake match to yourself, no database or API calls involved.
    test_matches = [{
        "title": "Senior Product Owner",
        "company": "Test Co",
        "location": "Remote",
        "match_tier": "Strong",
        "match_score": 92,
        "match_points": ["Agile delivery", "Stakeholder management"],
        "url": "https://example.com/job/123",
    }]
    send_digest_email(test_matches)
    print(f"Test email sent to {NOTIFY_EMAIL}.")

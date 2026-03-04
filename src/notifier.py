"""Daily application goal email reminder.

Checks how many jobs were marked 'Applied' today and sends a Gmail
notification if the count is below the daily goal.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from datetime import date
from email.mime.text import MIMEText

from src.config import AppConfig, get_db

logger = logging.getLogger(__name__)

_GMAIL_SMTP = "smtp.gmail.com"
_GMAIL_PORT = 465


def send_daily_reminder(cfg: AppConfig) -> None:
    """Check today's application count and send email if below goal.

    Reads config from env vars:
      NOTIFY_EMAIL      — recipient (and sender) Gmail address
      GMAIL_APP_PASSWORD — Gmail app password
      DAILY_GOAL        — minimum applications per day (default 15)
    """
    email = os.getenv("NOTIFY_EMAIL", "")
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not email or not app_password:
        logger.debug("[Notifier] Email not configured, skipping reminder")
        return

    daily_goal = int(os.getenv("DAILY_GOAL", "15"))

    db = get_db(cfg)
    try:
        today_count = db.count_applications_today()
    finally:
        db.close()

    logger.info(f"[Notifier] Today's applications: {today_count}/{daily_goal}")

    if today_count >= daily_goal:
        logger.info("[Notifier] Goal met, no reminder needed")
        return

    remaining = daily_goal - today_count
    today_str = date.today().strftime("%B %d, %Y")

    subject = f"Job Tracker: {remaining} more application{'s' if remaining != 1 else ''} needed today"
    body = (
        f"Daily Application Reminder — {today_str}\n"
        f"{'=' * 45}\n\n"
        f"Applied today:  {today_count}\n"
        f"Daily goal:     {daily_goal}\n"
        f"Remaining:      {remaining}\n\n"
        f"You need to apply to {remaining} more job{'s' if remaining != 1 else ''} "
        f"to hit your daily goal.\n\n"
        f"Keep going — consistency is key!\n"
    )

    _send_gmail(email, app_password, subject, body)


def _send_gmail(to_email: str, app_password: str, subject: str, body: str) -> None:
    """Send a plain-text email via Gmail SMTP."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = to_email
    msg["To"] = to_email

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(_GMAIL_SMTP, _GMAIL_PORT, context=context) as server:
            server.login(to_email, app_password)
            server.sendmail(to_email, to_email, msg.as_string())
        logger.info(f"[Notifier] Reminder email sent to {to_email}")
    except Exception as e:
        logger.error(f"[Notifier] Failed to send email: {e}")

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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import AppConfig, get_db

logger = logging.getLogger(__name__)

_GMAIL_SMTP = "smtp.gmail.com"
_GMAIL_PORT = 465

_MOTIVATIONAL_QUOTES = [
    "Every application is a step closer to the right opportunity.",
    "You miss 100% of the jobs you don't apply to.",
    "The best time to apply was yesterday. The second best time is now.",
    "Small daily progress leads to big results.",
    "Your future self will thank you for applying today.",
    "Rejection is redirection. Keep applying.",
    "The only way to guarantee failure is to stop trying.",
    "One of these applications will change everything.",
    "Discipline beats motivation. Apply even when you don't feel like it.",
    "You're not just applying for jobs — you're building momentum.",
    "Success is the sum of small efforts repeated day in and day out.",
    "Don't stop when you're tired. Stop when you're hired.",
    "Every 'no' gets you closer to a 'yes'.",
    "Consistency is what transforms average into excellence.",
    "The job search is a numbers game. Keep your numbers up.",
    "Today's effort is tomorrow's opportunity.",
    "You're one application away from a life-changing offer.",
    "Trust the process. Results follow consistency.",
    "Hard work beats talent when talent doesn't apply.",
    "Winners are just people who didn't give up.",
    "The grind is temporary. The career is forever.",
    "Apply like your dream company is hiring today — because they might be.",
    "Your next job won't find you. Go find it.",
    "Showing up every day is the hardest part. You've got this.",
    "No one ever regretted applying to too many jobs.",
    "Stay hungry. Stay applying.",
    "It only takes one 'yes' to make all the effort worth it.",
    "Progress, not perfection. Just hit send.",
    "The job market rewards the persistent, not the perfect.",
    "Today is a great day to get closer to your goal.",
]


_CONGRATS_QUOTES = [
    "You showed up and crushed it. That's what winners do.",
    "Consistency is your superpower. Another goal smashed!",
    "You're building something incredible — one application at a time.",
    "Today you chose discipline over comfort. That's how careers are made.",
    "Most people quit. You didn't. Be proud of that.",
    "Every application you sent today is a door waiting to open.",
    "You did the work. Now let the results catch up.",
    "This kind of effort doesn't go unrewarded. Keep it up!",
    "Goal crushed! Your future self is already thanking you.",
    "You're not just job hunting — you're proving what you're made of.",
    "Another day, another goal met. The streak continues!",
    "Hard days build strong careers. You nailed it today.",
    "You're in the top 1% of people who actually follow through.",
    "Rest easy tonight — you earned it.",
    "The right opportunity is getting closer. Today proved it.",
]


def _get_congrats_quote() -> str:
    """Pick a congratulations quote based on the day of the year."""
    day = date.today().timetuple().tm_yday
    return _CONGRATS_QUOTES[day % len(_CONGRATS_QUOTES)]


def _get_daily_quote() -> str:
    """Pick a motivational quote based on the day of the year (rotates daily)."""
    day = date.today().timetuple().tm_yday
    return _MOTIVATIONAL_QUOTES[day % len(_MOTIVATIONAL_QUOTES)]


def _progress_bar(current: int, goal: int, width: int = 20) -> str:
    """Generate a text-based progress bar."""
    pct = min(current / max(goal, 1), 1.0)
    filled = round(width * pct)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {current}/{goal}"


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
        logger.info("[Notifier] Goal met! Sending encouragement")
        _send_congrats(email, app_password, today_count, daily_goal)
        return

    remaining = daily_goal - today_count
    today_str = date.today().strftime("%A, %B %d, %Y")
    quote = _get_daily_quote()
    progress = _progress_bar(today_count, daily_goal)

    subject = f"Job Tracker: {remaining} more application{'s' if remaining != 1 else ''} needed today"

    # HTML email
    html = f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">

  <h2 style="color: #2c3e50; margin-bottom: 5px;">Daily Application Reminder</h2>
  <p style="color: #7f8c8d; margin-top: 0;">{today_str}</p>

  <hr style="border: none; border-top: 2px solid #3498db; margin: 15px 0;">

  <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
    <tr>
      <td style="padding: 10px 15px; background: #f8f9fa; border-radius: 8px 8px 0 0;">
        <span style="color: #7f8c8d; font-size: 13px;">Applied Today</span><br>
        <span style="font-size: 28px; font-weight: bold; color: #e74c3c;">{today_count}</span>
      </td>
      <td style="padding: 10px 15px; background: #f8f9fa; border-radius: 8px 8px 0 0;">
        <span style="color: #7f8c8d; font-size: 13px;">Daily Goal</span><br>
        <span style="font-size: 28px; font-weight: bold; color: #2c3e50;">{daily_goal}</span>
      </td>
      <td style="padding: 10px 15px; background: #f8f9fa; border-radius: 8px 8px 0 0;">
        <span style="color: #7f8c8d; font-size: 13px;">Remaining</span><br>
        <span style="font-size: 28px; font-weight: bold; color: #e67e22;">{remaining}</span>
      </td>
    </tr>
  </table>

  <div style="background: #ecf0f1; border-radius: 10px; height: 20px; margin: 15px 0; overflow: hidden;">
    <div style="background: linear-gradient(90deg, #3498db, #2ecc71); height: 100%; width: {min(today_count / max(daily_goal, 1) * 100, 100):.0f}%; border-radius: 10px; transition: width 0.3s;"></div>
  </div>
  <p style="text-align: center; color: #7f8c8d; font-size: 13px; margin-top: 5px;">
    {min(today_count / max(daily_goal, 1) * 100, 100):.0f}% complete
  </p>

  <p style="font-size: 15px; color: #2c3e50; line-height: 1.5;">
    You need to apply to <strong>{remaining} more job{'s' if remaining != 1 else ''}</strong> to hit your daily goal.
  </p>

  <div style="background: #fef9e7; border-left: 4px solid #f39c12; padding: 12px 15px; margin: 20px 0; border-radius: 0 8px 8px 0;">
    <p style="margin: 0; color: #7d6608; font-style: italic; font-size: 14px;">
      "{quote}"
    </p>
  </div>

  <hr style="border: none; border-top: 1px solid #ecf0f1; margin: 20px 0;">
  <p style="color: #bdc3c7; font-size: 11px; text-align: center;">
    Job Tracker Daily Reminder &bull; Goal: {daily_goal} applications/day
  </p>

</body>
</html>"""

    # Plain text fallback
    plain = (
        f"Daily Application Reminder — {today_str}\n"
        f"{'=' * 45}\n\n"
        f"  Progress:  {progress}\n\n"
        f"  Applied today:  {today_count}\n"
        f"  Daily goal:     {daily_goal}\n"
        f"  Remaining:      {remaining}\n\n"
        f"You need to apply to {remaining} more job{'s' if remaining != 1 else ''} "
        f"to hit your daily goal.\n\n"
        f'"{quote}"\n'
    )

    _send_gmail(email, app_password, subject, plain, html)


def _send_congrats(
    email: str, app_password: str, today_count: int, daily_goal: int,
) -> None:
    """Send a congratulations email when the daily goal is met."""
    today_str = date.today().strftime("%A, %B %d, %Y")
    quote = _get_congrats_quote()
    extra = today_count - daily_goal

    subject = f"Job Tracker: Goal reached! {today_count} applications today"

    html = f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">

  <h2 style="color: #27ae60; margin-bottom: 5px;">Daily Goal Reached!</h2>
  <p style="color: #7f8c8d; margin-top: 0;">{today_str}</p>

  <hr style="border: none; border-top: 2px solid #27ae60; margin: 15px 0;">

  <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
    <tr>
      <td style="padding: 10px 15px; background: #f8f9fa; border-radius: 8px 8px 0 0;">
        <span style="color: #7f8c8d; font-size: 13px;">Applied Today</span><br>
        <span style="font-size: 28px; font-weight: bold; color: #27ae60;">{today_count}</span>
      </td>
      <td style="padding: 10px 15px; background: #f8f9fa; border-radius: 8px 8px 0 0;">
        <span style="color: #7f8c8d; font-size: 13px;">Daily Goal</span><br>
        <span style="font-size: 28px; font-weight: bold; color: #2c3e50;">{daily_goal}</span>
      </td>
      <td style="padding: 10px 15px; background: #f8f9fa; border-radius: 8px 8px 0 0;">
        <span style="color: #7f8c8d; font-size: 13px;">Bonus</span><br>
        <span style="font-size: 28px; font-weight: bold; color: #27ae60;">+{extra}</span>
      </td>
    </tr>
  </table>

  <div style="background: #ecf0f1; border-radius: 10px; height: 20px; margin: 15px 0; overflow: hidden;">
    <div style="background: linear-gradient(90deg, #27ae60, #2ecc71); height: 100%; width: 100%; border-radius: 10px;"></div>
  </div>
  <p style="text-align: center; color: #27ae60; font-size: 14px; font-weight: bold; margin-top: 5px;">
    100% complete!
  </p>

  <p style="font-size: 15px; color: #2c3e50; line-height: 1.5;">
    You hit your daily goal of <strong>{daily_goal} applications</strong>{f" and went <strong>{extra} beyond</strong>" if extra > 0 else ""}! Great work today.
  </p>

  <div style="background: #eafaf1; border-left: 4px solid #27ae60; padding: 12px 15px; margin: 20px 0; border-radius: 0 8px 8px 0;">
    <p style="margin: 0; color: #1e8449; font-style: italic; font-size: 14px;">
      "{quote}"
    </p>
  </div>

  <hr style="border: none; border-top: 1px solid #ecf0f1; margin: 20px 0;">
  <p style="color: #bdc3c7; font-size: 11px; text-align: center;">
    Job Tracker Daily Reminder &bull; Goal: {daily_goal} applications/day
  </p>

</body>
</html>"""

    plain = (
        f"Daily Goal Reached! — {today_str}\n"
        f"{'=' * 45}\n\n"
        f"  Applied today:  {today_count}\n"
        f"  Daily goal:     {daily_goal}\n"
        f"  Bonus:          +{extra}\n\n"
        f"You hit your daily goal"
        f"{f' and went {extra} beyond' if extra > 0 else ''}! "
        f"Great work today.\n\n"
        f'"{quote}"\n'
    )

    _send_gmail(email, app_password, subject, plain, html)


def _send_gmail(
    to_email: str, app_password: str, subject: str, plain: str, html: str,
) -> None:
    """Send an HTML email with plain-text fallback via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = to_email
    msg["To"] = to_email

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(_GMAIL_SMTP, _GMAIL_PORT, context=context) as server:
            server.login(to_email, app_password)
            server.sendmail(to_email, to_email, msg.as_string())
        logger.info(f"[Notifier] Reminder email sent to {to_email}")
    except Exception as e:
        logger.error(f"[Notifier] Failed to send email: {e}")

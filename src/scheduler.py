"""Background scheduler for periodic job fetching and daily reminders."""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from src.config import AppConfig
from src.notifier import send_daily_reminder
from src.pipeline import run_pipeline

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler(cfg: AppConfig) -> BackgroundScheduler:
    """Start a background scheduler for job fetching and daily reminders."""
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.info("Scheduler already running")
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)

    # Periodic job fetching
    _scheduler.add_job(
        func=run_pipeline,
        trigger="interval",
        minutes=cfg.fetch_interval_minutes,
        args=[cfg],
        id="job_fetch",
        name="Fetch and score jobs",
        replace_existing=True,
        max_instances=1,
    )

    # Daily application goal reminder
    notify_hour = int(os.getenv("NOTIFY_HOUR", "21"))
    _scheduler.add_job(
        func=send_daily_reminder,
        trigger="cron",
        hour=notify_hour,
        args=[cfg],
        id="daily_application_check",
        name="Daily application goal reminder",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        f"Scheduler started: fetch every {cfg.fetch_interval_minutes}min, "
        f"daily reminder at {notify_hour}:00"
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None

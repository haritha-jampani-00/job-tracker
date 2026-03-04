#!/Users/harithajampani/anaconda3/bin/python
"""Standalone daily application goal check. Run via launchd/cron."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from src.config import load_config
from src.notifier import send_daily_reminder

if __name__ == "__main__":
    cfg = load_config()
    send_daily_reminder(cfg)

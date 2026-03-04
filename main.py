#!/usr/bin/env python3
"""CLI entry point for the job tracker.

Usage:
    python main.py --run-once                     # Single fetch cycle
    python main.py --loop                         # Continuous with scheduler
    python main.py --run-once --roles "ML Engineer, Data Engineer"
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from src.config import load_config
from src.pipeline import run_pipeline
from src.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Job Tracker CLI")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run a single fetch-score-export cycle and exit",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously with background scheduler",
    )
    parser.add_argument(
        "--roles",
        type=str,
        default=None,
        help="Override roles (comma-separated, e.g., 'ML Engineer, Data Engineer')",
    )
    args = parser.parse_args()

    if not args.run_once and not args.loop:
        parser.print_help()
        print("\nUse --run-once for a single run or --loop for continuous mode.")
        sys.exit(1)

    cfg = load_config()

    if args.roles:
        cfg.search.roles = [r.strip() for r in args.roles.split(",") if r.strip()]

    logger.info(f"Roles: {cfg.search.roles}")

    if args.loop:
        logger.info("Starting in loop mode (Ctrl+C to stop)...")
        result = run_pipeline(cfg)
        logger.info(f"Initial run: {result}")

        start_scheduler(cfg)

        def handle_signal(sig: int, frame) -> None:
            logger.info("Shutting down...")
            stop_scheduler()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        while True:
            time.sleep(60)
    else:
        result = run_pipeline(cfg)
        roles = ", ".join(result.get("roles", []))
        print(
            f"\nDone! Roles: [{roles}]\n"
            f"  Found {result['jobs_found']} jobs, "
            f"added {result['jobs_added']} new, "
            f"exported {result['jobs_exported']} total to Excel."
        )


if __name__ == "__main__":
    main()

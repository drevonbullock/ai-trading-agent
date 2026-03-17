"""
scheduler.py
APScheduler-based scheduler for the AI Trading Agent.
Runs run_scan() at :00 and :30 every hour, 7 days a week.
Weekdays (Mon–Fri): all markets — crypto, forex, stocks, commodities.
Weekends (Sat–Sun): crypto and commodities only (forex/stocks are closed).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "logs", "scheduler.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)

# ── APScheduler imports ────────────────────────────────────────────────────────

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:
    log.error("APScheduler not installed. Run: pip install apscheduler")
    sys.exit(1)

# ── Project imports ────────────────────────────────────────────────────────────

from main import run_scan
from alerts.telegram_bot import send_message


# ── Scheduled job ─────────────────────────────────────────────────────────────

def _scan_job() -> None:
    """Wrapper called by APScheduler — catches all exceptions so the scheduler stays alive."""
    log.info("Scheduled scan triggered.")

    # Determine which markets to scan based on day of week (UTC).
    # weekday() returns 5 for Saturday, 6 for Sunday.
    weekday = datetime.utcnow().weekday()
    if weekday >= 5:  # weekend
        markets = ["crypto", "commodities"]
        log.info("Weekend mode — scanning crypto and commodities only (forex/stocks closed).")
    else:
        markets = ["crypto", "forex", "stocks", "commodities"]
        log.info("Weekday mode — scanning all markets.")

    try:
        run_scan(markets=markets)
    except Exception as exc:
        log.error("run_scan() raised an unhandled exception: %s", exc, exc_info=True)
        try:
            send_message(f"⚠️ *Scheduler error* — scan failed:\n`{exc}`")
        except Exception:
            pass  # Don't let alert failure kill the scheduler either


# ── Scheduler setup ────────────────────────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    """
    Create and configure the APScheduler instance.

    Schedule: twice per hour at :00 and :30, Monday–Friday only (48 scans/day).
    Timezone: UTC (markets use UTC for session boundaries).
    """
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        _scan_job,
        trigger=CronTrigger(
            minute="0,30",           # top of hour and half-past, every day
            timezone="UTC",
        ),
        id="market_scan",
        name="30-minute market scan",
        max_instances=1,             # prevent overlapping runs
        coalesce=True,               # skip missed fires instead of stacking
        misfire_grace_time=120,      # tolerate up to 2-min delay before skipping
    )

    return scheduler


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("AI Trading Agent Scheduler starting up")
    log.info("Schedule: :00 and :30 every hour, 7 days (UTC) — 48 scans/day")
    log.info("Weekdays: all markets | Weekends: crypto + commodities only")
    log.info("=" * 60)

    # ── Startup Telegram notification ─────────────────────────────────────────
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        send_message(
            f"🤖 <b>AI Trading Agent online</b>\n"
            f"Scheduler started at {now_str}\n"
            f"Scanning at :00 and :30 — 7 days/week (48×/day)\n"
            f"Weekdays: all markets | Weekends: crypto + commodities only",
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("Startup Telegram notification failed: %s", exc)

    # ── Run one scan immediately on startup ───────────────────────────────────
    log.info("Running immediate scan on startup ...")
    _scan_job()

    # ── Start blocking scheduler ──────────────────────────────────────────────
    scheduler = build_scheduler()

    try:
        scheduler.start()
        log.info("Scheduler is live. Scans run at :00 and :30 every hour, 7 days/week.")
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped by user.")
        try:
            send_message("🛑 <b>AI Trading Agent</b> — scheduler stopped.", parse_mode="HTML")
        except Exception:
            pass

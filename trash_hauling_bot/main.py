"""
Trash Hauling Lead Generation & Scheduling Bot — Orchestrator

Wires together the three sub-agents and starts the Telegram bot + scheduler.

Usage:
    # From the repo root:
    cd trash_hauling_bot
    python main.py

    # One-time Facebook login (do this before the first scraper run):
    python -m agents.scraper --login
"""

import asyncio
import logging
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Load .env before importing anything that reads config
load_dotenv(Path(__file__).parent / ".env")

from agents.calendar_sync import CalendarSyncAgent
from agents.outreach import OutreachAgent
from agents.scraper import ScraperAgent
from config import config
from integrations.telegram_bot import TrashHaulingBot
from utils.audit import AuditLogger

Path(config.data_dir).mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{config.data_dir}/bot.log"),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    missing = config.validate()
    if missing:
        logger.error("Missing required config: %s", ", ".join(missing))
        logger.error("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    audit = AuditLogger(config.audit_log_file)
    scraper = ScraperAgent(audit)
    outreach = OutreachAgent(audit)
    cal_sync = CalendarSyncAgent(audit)
    bot = TrashHaulingBot(scraper, outreach, cal_sync, audit)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scraper.run,
        "interval",
        minutes=config.scraper_interval_minutes,
        id="fb_scraper",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cal_sync.run,
        "interval",
        minutes=config.calendar_sync_interval_minutes,
        id="calendar_sync",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cal_sync.mark_stale_leads,
        "interval",
        hours=24,
        id="lead_aging",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    audit.log("orchestrator", "startup", {
        "scraper_interval_min": config.scraper_interval_minutes,
        "cal_sync_interval_min": config.calendar_sync_interval_minutes,
    })
    logger.info("Scheduler started — scraper every %dm, calendar sync every %dm",
                config.scraper_interval_minutes, config.calendar_sync_interval_minutes)

    try:
        await bot.start()
        await asyncio.Event().wait()   # block until interrupted
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested")
    finally:
        scheduler.shutdown(wait=False)
        await bot.stop()
        audit.log("orchestrator", "shutdown")
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())

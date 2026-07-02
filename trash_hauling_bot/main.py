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

# Env is loaded by config.py (consolidated parent .env at ../.env), so no
# explicit load_dotenv call here. config.py runs first via the import below.
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
        logger.error("Fill in the consolidated parent .env (Claude-openclaw\\.env) with your keys.")
        sys.exit(1)

    if not config.authorized_chat_ids and not config.dry_run:
        logger.warning(
            "TRASH_BOT_CHAT_IDS is empty — bot will accept commands from ANY chat that "
            "finds the bot token. Set TRASH_BOT_CHAT_IDS in .env to lock it down."
        )
    if not config.team_chat_ids:
        logger.warning(
            "TRASH_BOT_TEAM_CHAT_IDS is empty — /schedule and /cancel will not send team notifications."
        )

    audit = AuditLogger(config.audit_log_file)
    scraper = ScraperAgent(audit)
    outreach = OutreachAgent(audit)
    cal_sync = CalendarSyncAgent(audit)
    bot = TrashHaulingBot(scraper, outreach, cal_sync, audit)

    async def _scraper_job() -> None:
        count = await scraper.run()
        if count > 0:
            await bot.notify_team(
                f"{count} new lead(s) found — use /leads new to review."
            )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _scraper_job,
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

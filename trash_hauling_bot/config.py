
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_HERE = Path(__file__).parent
# Consolidated env: both bots now share Claude-openclaw\.env at the parent dir.
# Key names are namespaced (TRASH_BOT_*, FB_*, etc.) so the bots can't cross-load.
load_dotenv(_HERE.parent / ".env")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _list_env(key: str, default: str) -> List[str]:
    val = os.getenv(key, default)
    return [x.strip() for x in val.split(",") if x.strip()]


def _int_list_env(key: str) -> List[int]:
    result = []
    for x in os.getenv(key, "").split(","):
        x = x.strip()
        if x:
            try:
                result.append(int(x))
            except ValueError:
                pass
    return result


@dataclass
class Config:
    # Telegram — dedicated bot separate from ClawBot
    bot_token: str = field(default_factory=lambda: os.getenv("TRASH_BOT_TOKEN", ""))
    authorized_chat_ids: List[int] = field(default_factory=lambda: _int_list_env("TRASH_BOT_CHAT_IDS"))
    team_chat_ids: List[int] = field(default_factory=lambda: _int_list_env("TRASH_BOT_TEAM_CHAT_IDS"))

    # Google — service account JSON placed at this path
    google_credentials_file: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CREDENTIALS_FILE", "data/google_credentials.json")
    )
    google_sheet_id: str = field(default_factory=lambda: os.getenv("GOOGLE_SHEET_ID", ""))
    google_calendar_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CALENDAR_ID", "primary"))

    # Claude API — used for outreach message generation; falls back to template if unset
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    # Facebook Marketplace scraping
    fb_search_location: str = field(default_factory=lambda: os.getenv("FB_SEARCH_LOCATION", ""))
    fb_search_radius: int = field(default_factory=lambda: _int_env("FB_SEARCH_RADIUS_MILES", 50))
    fb_search_keywords: List[str] = field(
        default_factory=lambda: _list_env(
            "FB_SEARCH_KEYWORDS",
            "trash hauling,junk removal,cleanout,debris removal,haul away",
        )
    )
    fb_profile_dir: str = field(default_factory=lambda: os.getenv("FB_PROFILE_DIR", "data/fb_profile"))

    # Scheduler intervals
    scraper_interval_minutes: int = field(default_factory=lambda: _int_env("SCRAPER_INTERVAL_MINUTES", 30))
    calendar_sync_interval_minutes: int = field(default_factory=lambda: _int_env("CALENDAR_SYNC_INTERVAL_MINUTES", 5))
    lead_stale_days: int = field(default_factory=lambda: _int_env("LEAD_STALE_DAYS", 7))

    # Dry-run mode — set DRY_RUN=true to run without real Google credentials or FB session
    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "false").lower() == "true")

    # Paths
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "data"))
    audit_log_file: str = field(default_factory=lambda: os.getenv("AUDIT_LOG_FILE", "data/audit.log"))
    pending_queue_file: str = field(
        default_factory=lambda: os.path.join(os.getenv("DATA_DIR", "data"), "pending_outreach.json")
    )

    def validate(self) -> List[str]:
        missing = []
        if not self.bot_token:
            missing.append("TRASH_BOT_TOKEN")
        if self.dry_run:
            return missing  # Google credentials and FB location not required in dry-run
        if not self.google_sheet_id:
            missing.append("GOOGLE_SHEET_ID")
        if not self.fb_search_location:
            missing.append("FB_SEARCH_LOCATION")
        if not Path(self.google_credentials_file).exists():
            missing.append(f"GOOGLE_CREDENTIALS_FILE ({self.google_credentials_file} not found)")
        return missing


config = Config()

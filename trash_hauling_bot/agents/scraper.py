"""
Sub-Agent 1 — Facebook Marketplace Scraper

Monitors FB Marketplace for trash hauling / junk removal listings, scores
them by urgency and job size, deduplicates, and writes new leads to Sheets.

First-time setup (run once to save a login session):
    python -m trash_hauling_bot.agents.scraper --login
"""

import asyncio
import logging
import random
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from config import config
from integrations.sheets import SheetsClient
from utils.audit import AuditLogger
from utils.dedup import DedupStore
from utils.sanitize import extract_phone, sanitize_text, validate_fb_url

logger = logging.getLogger(__name__)

_URGENCY_KEYWORDS = ["asap", "urgent", "today", "immediately", "right away", "emergency", "quick", "same day"]
_SIZE_LARGE = ["full house", "entire house", "estate", "4 bedroom", "3 bedroom", "garage full", "tons of", "whole"]
_SIZE_MEDIUM = ["room", "2 bedroom", "shed", "basement", "apartment", "office"]
_SIZE_SMALL = ["few items", "small load", "quick", "couple items", "single item", "one piece"]

_DEMAND_SIGNALS = [
    "need help", "need someone", "need a hauler", "need a company", "need a service",
    "need it hauled", "need it removed", "need it picked up", "need it gone",
    "need to get rid", "need picked up", "need removed", "need hauled", "need pickup",
    "looking for", "looking to hire", "want to hire", "trying to find",
    "anyone available", "does anyone", "can someone", "can anyone",
    "willing to pay", "how much will", "what would it cost", "get a quote",
    "need a quote", "estimate for", "help me clean", "help me get rid",
    "in need of", "who can", "any recommendations", "who does junk removal",
    "who hauls", "i need someone", "we need someone", "hiring someone",
    "looking for a hauler", "need a hauler", "need hauling",
]
_SUPPLY_SIGNALS = [
    "we offer", "we provide", "we are a", "our team", "we specialize",
    "our service", "free estimates", "free estimate", "licensed and insured",
    "licensed & insured", "serving the dfw", "serving dallas", "we service",
    "give us a call", "call us today", "contact us for", "book online",
    "we remove", "we haul junk", "we pick up", "we clean out",
    "book now", "schedule online", "our prices", "starting at $",
]
_CONTAINER_SIGNALS = [
    "dumpster rental", "roll-off", "rolloff", "container rental",
    "drop-off container", "rent a dumpster", "dumpster for rent",
    "portable toilet", "porta potty",
]
_SELLING_SIGNALS = [
    "for sale", "obo", "or best offer", "asking price",
    "pickup only", "must pick up", "price firm", "make an offer", "make offer",
    "free to whoever", "free to a good home", "taking offers", "open to offers",
    "will sell", "priced to sell", "reduced to", "willing to sell",
    "i'm selling", "im selling", "we're selling", "we are selling",
    "items for sale", "furniture for sale", "appliances for sale",
]
# The demand phrases above ("need someone", "can anyone", "looking for", etc.) are
# generic "wanting" language that also shows up in unrelated posts (pet rehoming,
# moving help, etc.). Require the post to also name a junk/hauling-relevant topic
# so demand phrasing alone can't pass the filter.
_TOPICAL_SIGNALS = [
    "junk", "trash", "hauling", "haul away", "haul off", "debris", "cleanout",
    "clean out", "clear out", "clearing out",
    "yard waste", "construction debris", "yard debris", "estate",
    "hoarder", "storage unit", "renovation", "demo debris", "demolition",
    "tree limbs", "branches", "e-waste", "electronics disposal", "scrap metal",
    "dump", "dumping",
]
# Generic item/location words ("furniture", "couch", "garage", "remove", "pickup") were
# deliberately dropped: they also appear in unrelated posts (moving help, item requests)
# that use the same generic demand phrasing. They only count as topical when paired with
# an explicit disposal word above (e.g. "furniture cleanout", "haul away the couch").


def _is_demand_lead(text: str) -> tuple[bool, str]:
    """Return (passes, reason) so callers can log rejections."""
    t = text.lower()
    if any(kw in t for kw in _CONTAINER_SIGNALS):
        return False, "container_signal"
    has_demand = any(kw in t for kw in _DEMAND_SIGNALS)
    has_supply = any(kw in t for kw in _SUPPLY_SIGNALS)
    has_selling = any(kw in t for kw in _SELLING_SIGNALS)
    if not has_demand:
        return False, "no_demand_signal"
    if has_supply:
        return False, "supply_signal"
    if has_selling:
        return False, "selling_signal"
    if not any(kw in t for kw in _TOPICAL_SIGNALS):
        return False, "no_topical_signal"
    return True, "ok"


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


def _score_urgency(text: str) -> int:
    text_l = text.lower()
    score = 1
    for kw in _URGENCY_KEYWORDS:
        if kw in text_l:
            score = min(score + 2, 10)
    return score


def _score_size(text: str) -> int:
    text_l = text.lower()
    for kw in _SIZE_LARGE:
        if kw in text_l:
            return 8
    for kw in _SIZE_MEDIUM:
        if kw in text_l:
            return 5
    for kw in _SIZE_SMALL:
        if kw in text_l:
            return 3
    return 4


class ScraperAgent:
    AGENT_NAME = "scraper"

    def __init__(self, audit: AuditLogger):
        self._audit = audit
        self._sheets = SheetsClient()
        self._dedup = DedupStore(f"{config.data_dir}/seen_listings.json")

    async def run(self) -> int:
        """Scrape FB Marketplace across all configured keywords. Returns count of new leads added."""
        if config.dry_run:
            logger.info("Scraper run skipped (DRY_RUN=true)")
            self._audit.log(self.AGENT_NAME, "scan_skipped_dry_run", {})
            return 0

        logger.info("Scraper run started")
        self._audit.log(self.AGENT_NAME, "scan_started", {"keywords": config.fb_search_keywords})

        all_leads: List[Dict] = []
        async with async_playwright() as pw:
            ctx = await self._browser_context(pw)
            page = await ctx.new_page()
            await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
            try:
                for keyword in config.fb_search_keywords:
                    leads = await self._scrape_keyword(page, keyword)
                    all_leads.extend(leads)
                    await asyncio.sleep(random.uniform(2.0, 4.0))
            finally:
                await ctx.close()

        added = 0
        for lead in all_leads:
            url = lead.get("listing_url", "")
            title = lead.get("description", "")
            if self._dedup.is_seen(url, title):
                continue
            try:
                self._sheets.add_lead(lead)
                self._dedup.mark_seen(url, title)
                added += 1
                self._audit.log(self.AGENT_NAME, "lead_added", {
                    "url": url,
                    "job_type": lead.get("job_type", ""),
                    "urgency_score": lead.get("urgency_score"),
                    "size_score": lead.get("size_score"),
                    "location": lead.get("location", ""),
                })
            except ValueError as exc:
                self._audit.log(self.AGENT_NAME, "lead_rejected", {"reason": str(exc), "url": url})

        self._audit.log(self.AGENT_NAME, "scan_completed", {"new_leads": added})
        logger.info("Scraper complete — %d new leads", added)
        return added

    async def login_flow(self) -> None:
        """Open a visible browser window so the user can log into Facebook once."""
        if config.dry_run:
            logger.info("login_flow skipped (DRY_RUN=true) — no browser launched")
            return
        import os
        os.makedirs(config.fb_profile_dir, exist_ok=True)
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(config.fb_profile_dir, headless=False)
            page = await ctx.new_page()
            await page.goto("https://www.facebook.com/login")
            logger.info("Log into Facebook in the browser window that opened. Close it when done.")
            try:
                await page.wait_for_url("https://www.facebook.com/", timeout=180_000)
                logger.info("Login successful — session saved to %s", config.fb_profile_dir)
            except Exception:
                logger.info("Browser closed — session state saved.")
            await ctx.close()

    async def _browser_context(self, pw) -> BrowserContext:
        import os
        os.makedirs(config.fb_profile_dir, exist_ok=True)
        return await pw.chromium.launch_persistent_context(
            config.fb_profile_dir,
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            user_agent=_USER_AGENT,
        )

    async def _scrape_keyword(self, page: Page, keyword: str) -> List[Dict]:
        leads: List[Dict] = []
        url = (
            "https://www.facebook.com/marketplace/search/"
            f"?query={keyword.replace(' ', '+')}"
            "&sortBy=creation_time_descend"
        )

        try:
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 3.5))

            if "login" in page.url.lower() or await page.query_selector('input[name="email"]'):
                logger.warning("FB login wall detected — run with --login flag first")
                self._audit.log(self.AGENT_NAME, "login_required")
                return leads

            # Scroll to trigger lazy-loaded listings
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(1.2, 2.0))

            links = await page.query_selector_all('a[href*="/marketplace/item/"]')
            seen_hrefs: set = set()

            for link in links[:25]:
                try:
                    href = await link.get_attribute("href") or ""
                    if not href or href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)

                    full_url = f"https://www.facebook.com{href}" if href.startswith("/") else href
                    # Strip query params to normalize URL
                    full_url = full_url.split("?")[0].rstrip("/") + "/"
                    if not validate_fb_url(full_url):
                        continue

                    raw_text = sanitize_text(await link.inner_text() or "", max_length=600)
                    if not raw_text:
                        continue
                    passes, reason = _is_demand_lead(raw_text)
                    if not passes:
                        self._audit.log(self.AGENT_NAME, "lead_rejected", {
                            "reason": reason, "url": full_url, "text_preview": raw_text[:120],
                        })
                        continue

                    leads.append({
                        "listing_url": full_url,
                        "description": raw_text,
                        "job_type": keyword,
                        "location": config.fb_search_location,
                        "urgency_score": _score_urgency(raw_text),
                        "size_score": _score_size(raw_text),
                        "contact": extract_phone(raw_text) or "",
                        "name": "",
                    })
                except Exception as exc:
                    logger.debug("Link parse error: %s", exc)

        except Exception as exc:
            logger.error("Scrape error for keyword '%s': %s", keyword, exc)
            self._audit.log(self.AGENT_NAME, "scrape_error", {"keyword": keyword, "error": str(exc)})

        return leads


# Allow running as a script for the one-time login flow
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if "--login" in sys.argv:
        audit = AuditLogger()
        agent = ScraperAgent(audit)
        asyncio.run(agent.login_flow())
    else:
        print("Usage: python -m trash_hauling_bot.agents.scraper --login")

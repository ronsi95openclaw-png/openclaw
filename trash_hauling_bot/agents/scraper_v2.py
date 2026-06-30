"""
Sub-Agent 2 — Tightly Filtered FB Marketplace Scraper

Only catches actual junk removal requests and container drop-off services.
Max 5 high-quality leads per day.
"""
import asyncio
import logging
import random
from typing import Dict, List

from playwright.async_api import BrowserContext, Page, async_playwright

from config import config
from integrations.sheets import SheetsClient
from utils.audit import AuditLogger
from utils.dedup import DedupStore
from utils.sanitize import extract_phone, sanitize_text, validate_fb_url

logger = logging.getLogger(__name__)

# STRICT service keywords (must contain at least 2)
_JOB_KEYWORDS = [
    "junk removal", "haul away", "trash pickup", "furniture removal",
    "appliance removal", "debris cleanup", "get rid of", "need gone",
    "dumpster", "roll off", "container drop", "trash bin", "construction debris"
]

# Location must be within our service area
_SERVICE_ZIPS = ["75001", "75002", "75006", "75007", "75019", "75028", "75039"]

class ScraperAgentV2:
    AGENT_NAME = "scraper_v2"
    MAX_LEADS = 5  # Hard daily limit

    def __init__(self, audit: AuditLogger):
        self._audit = audit
        self._sheets = SheetsClient()
        self._dedup = DedupStore(f"{config.data_dir}/seen_listings_v2.json")

    async def run(self) -> int:
        """Scrape FB Marketplace with tight filters. Returns count of new leads added."""
        if config.dry_run:
            logger.info("Scraper run skipped (DRY_RUN=true)")
            return 0

        logger.info("Starting tight-filter scraper")
        all_leads: List[Dict] = []
        
        async with async_playwright() as pw:
            ctx = await self._browser_context(pw)
            page = await ctx.new_page()
            try:
                for keyword in ["junk removal", "dumpster rental"]:  # Only 2 core searches
                    leads = await self._scrape_keyword(page, keyword)
                    all_leads.extend(leads)
                    if len(all_leads) >= self.MAX_LEADS:
                        break
                    await asyncio.sleep(random.uniform(2.0, 4.0))
            finally:
                await ctx.close()

        # Process and deduplicate
        added = 0
        for lead in all_leads[:self.MAX_LEADS]:  # Hard cap
            if self._dedup.is_seen(lead["listing_url"]):
                continue
            
            try:
                self._sheets.add_lead(lead)
                self._dedup.mark_seen(lead["listing_url"])
                added += 1
                logger.info(f"Added lead: {lead['description'][:50]}...")
            except Exception as exc:
                logger.error(f"Failed to add lead: {exc}")

        logger.info(f"Scraper complete - {added} new leads")
        return added

    def _is_relevant(self, text: str, location: str) -> bool:
        """Strict relevance check"""
        text_l = text.lower()
        
        # Must contain service keywords
        keyword_count = sum(kw in text_l for kw in _JOB_KEYWORDS)
        if keyword_count < 2:
            return False
            
        # Location check
        if not any(zip in location for zip in _SERVICE_ZIPS):
            return False
            
        return True

    async def _scrape_keyword(self, page: Page, keyword: str) -> List[Dict]:
        """Scrape with tight filters"""
        leads = []
        url = f"https://www.facebook.com/marketplace/search/?query={keyword.replace(' ', '+')}"
        
        try:
            await page.goto(url, timeout=30000)
            await asyncio.sleep(2)
            
            # Scroll to load more
            for _ in range(2):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)
            
            links = await page.query_selector_all('a[href*="/marketplace/item/"]')
            
            for link in links[:15]:  # Only check first 15
                try:
                    href = await link.get_attribute("href") or ""
                    full_url = f"https://www.facebook.com{href}".split("?")[0]
                    
                    if not validate_fb_url(full_url):
                        continue
                        
                    raw_text = sanitize_text(await link.inner_text() or "")
                    location = config.fb_search_location
                    
                    if not self._is_relevant(raw_text, location):
                        continue
                        
                    leads.append({
                        "listing_url": full_url,
                        "description": raw_text,
                        "job_type": keyword,
                        "location": location,
                        "contact": extract_phone(raw_text) or "",
                        "source": "fb_marketplace_v2"
                    })
                except Exception:
                    continue
                    
        except Exception as exc:
            logger.error(f"Scrape error: {exc}")
            
        return leads

    async def _browser_context(self, pw) -> BrowserContext:
        """Reuse existing browser session"""
        return await pw.chromium.launch_persistent_context(
            config.fb_profile_dir,
            headless=True,
            channel="chrome",
            args=["--no-sandbox"]
        )
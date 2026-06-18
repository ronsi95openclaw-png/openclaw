"""
Free Facebook Marketplace listing poster.

Automates posting a FREE Facebook Marketplace listing advertising the HaulYeah
junk-hauling service (as opposed to the paid Graph Ads funnel in
integrations/fb_ads.py). Reuses the SAME persistent Playwright session the
scraper logs into (config.fb_profile_dir), so no separate login is needed.

CRITICAL SAFETY
---------------
Default behavior is DRY-RUN. When ``config.dry_run`` is true (or the caller
does not pass an explicit non-dry-run flag), the poster navigates and fills the
form for validation but DOES NOT click the final Publish button — it logs
exactly what it would post. Live publishing is gated behind an explicit
``dry_run=False`` argument AND a saved FB session.

Build a payload without launching a browser:
    payload = build_listing_payload()

First-time FB login is shared with the scraper:
    python -m agents.scraper --login
"""

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from config import config
from utils.audit import AuditLogger

logger = logging.getLogger(__name__)

_MARKETPLACE_CREATE_URL = "https://www.facebook.com/marketplace/create/item"

# Marketplace "Service Free" listings: price is typically listed as Free with
# the real pricing handled per-quote in conversation.
DEFAULT_TITLE = "Junk Removal & Hauling — Free Quotes"
DEFAULT_CATEGORY = "Home Improvement Supplies"
DEFAULT_DESCRIPTION = (
    "HaulYeah! Local junk removal and hauling. We haul away trash, old "
    "furniture, appliances, yard debris, garage and estate cleanouts — anything "
    "that fits in our F150 and trailer. Fast, friendly, affordable. Same-week "
    "pickup in the area. Message for a FREE quote!"
)


@dataclass
class ListingPayload:
    title: str = DEFAULT_TITLE
    description: str = DEFAULT_DESCRIPTION
    price: str = "Free"            # Free/Service listing
    category: str = DEFAULT_CATEGORY
    location: str = ""
    photos: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "title": self.title,
            "description": self.description,
            "price": self.price,
            "category": self.category,
            "location": self.location,
            "photos": list(self.photos),
        }


def build_listing_payload(
    title: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    photos: Optional[List[str]] = None,
    category: Optional[str] = None,
    price: str = "Free",
) -> ListingPayload:
    """Build a Marketplace listing payload, falling back to service defaults."""
    return ListingPayload(
        title=title or DEFAULT_TITLE,
        description=description or DEFAULT_DESCRIPTION,
        price=price or "Free",
        category=category or DEFAULT_CATEGORY,
        location=location if location is not None else (config.fb_search_location or ""),
        photos=list(photos) if photos else [],
    )


class MarketplacePoster:
    AGENT_NAME = "marketplace_poster"

    def __init__(self, audit: AuditLogger):
        self._audit = audit

    async def post_listing(
        self,
        payload: Optional[ListingPayload] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict:
        """Post (or, in dry-run, prepare) a free Marketplace service listing.

        Returns a dict describing what was done / would be done. The final
        Publish click only happens when dry_run is explicitly False AND
        config.dry_run is not forcing dry mode.
        """
        payload = payload or build_listing_payload()
        # DRY_RUN config always forces dry mode; an explicit dry_run arg can
        # only make it *more* conservative, never override the global safety.
        is_dry = True if config.dry_run else (True if dry_run is None else dry_run)

        if is_dry:
            logger.info("[DRY-RUN] Would post Marketplace listing: %s", payload.as_dict())
            self._audit.log(self.AGENT_NAME, "listing_dry_run", payload.as_dict())
            # In dry mode under global DRY_RUN we don't launch a browser at all
            # (mirrors scraper.run / login_flow behavior).
            if config.dry_run:
                return {"dry_run": True, "published": False, "payload": payload.as_dict()}

        return await self._drive_browser(payload, is_dry)

    async def _drive_browser(self, payload: ListingPayload, is_dry: bool) -> Dict:
        async with async_playwright() as pw:
            ctx = await self._browser_context(pw, is_dry)
            page = await ctx.new_page()
            try:
                published = await self._fill_form(page, payload, is_dry)
            finally:
                await ctx.close()
        return {"dry_run": is_dry, "published": published, "payload": payload.as_dict()}

    async def _browser_context(self, pw, is_dry: bool) -> BrowserContext:
        import os
        os.makedirs(config.fb_profile_dir, exist_ok=True)
        # Headful when actually publishing helps avoid bot checks; headless ok
        # for dry validation.
        return await pw.chromium.launch_persistent_context(
            config.fb_profile_dir,
            headless=is_dry,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

    async def _fill_form(self, page: Page, payload: ListingPayload, is_dry: bool) -> bool:
        await page.goto(_MARKETPLACE_CREATE_URL, timeout=45_000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2.0, 3.5))

        if "login" in page.url.lower() or await page.query_selector('input[name="email"]'):
            logger.warning("FB login wall detected — run `python -m agents.scraper --login` first")
            self._audit.log(self.AGENT_NAME, "login_required")
            return False

        # Best-effort field fills. Selectors are resilient label-based lookups;
        # Marketplace's DOM changes often, so failures are logged not fatal.
        await self._fill_labeled(page, "Title", payload.title)
        await self._fill_labeled(page, "Price", payload.price)
        await self._fill_labeled(page, "Description", payload.description)
        if payload.location:
            await self._fill_labeled(page, "Location", payload.location)

        for photo in payload.photos:
            try:
                file_input = await page.query_selector('input[type="file"]')
                if file_input:
                    await file_input.set_input_files(photo)
                    await asyncio.sleep(random.uniform(1.0, 2.0))
            except Exception as exc:
                logger.debug("Photo upload skipped (%s): %s", photo, exc)

        if is_dry:
            logger.info("[DRY-RUN] Form filled; NOT clicking Publish. Payload: %s", payload.as_dict())
            self._audit.log(self.AGENT_NAME, "listing_prepared_not_published", payload.as_dict())
            return False

        # --- Live publish path (explicitly non-dry-run only) ---
        publish_btn = await page.query_selector('div[aria-label="Publish"], div[aria-label="Post"]')
        if not publish_btn:
            logger.error("Publish button not found — aborting without posting")
            self._audit.log(self.AGENT_NAME, "publish_button_missing", {})
            return False
        await publish_btn.click()
        await asyncio.sleep(random.uniform(2.0, 4.0))
        logger.info("Marketplace listing published")
        self._audit.log(self.AGENT_NAME, "listing_published", {"title": payload.title})
        return True

    async def _fill_labeled(self, page: Page, label: str, value: str) -> None:
        """Fill a Marketplace form field located by its visible label text."""
        if not value:
            return
        try:
            field_el = await page.query_selector(f'label:has-text("{label}") input')
            if field_el is None:
                field_el = await page.query_selector(f'label:has-text("{label}") textarea')
            if field_el is not None:
                await field_el.fill(value)
                await asyncio.sleep(random.uniform(0.3, 0.8))
            else:
                logger.debug("Field '%s' not found (Marketplace DOM may have changed)", label)
        except Exception as exc:
            logger.debug("Could not fill '%s': %s", label, exc)

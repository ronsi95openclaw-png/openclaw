"""
Quick smoke test for the HaulYeah scraper.
Runs ONE keyword search, prints results, writes them to data/test_results.txt.
Does NOT write to Google Sheets — just shows what the scraper sees on FB.

Usage (from trash_hauling_bot/ with venv active):
    python test_scraper.py
"""

import asyncio
import logging
import random
from pathlib import Path
from datetime import datetime

# Patch dry_run to False so the scraper doesn't skip
import os
os.environ["DRY_RUN"] = "false"

from playwright.async_api import async_playwright
from config import config
from agents.scraper import _score_urgency, _score_size, _USER_AGENT
from utils.sanitize import sanitize_text, validate_fb_url, extract_phone

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TEST_KEYWORD = "junk removal"
OUTPUT_FILE = Path(config.data_dir) / "test_results.txt"


async def run_test():
    leads = []
    print(f"\n{'='*60}")
    print(f"  HaulYeah Scraper Test — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Keyword : {TEST_KEYWORD}")
    print(f"  Location: {config.fb_search_location}")
    print(f"{'='*60}\n")

    async with async_playwright() as pw:
        import os as _os
        _os.makedirs(config.fb_profile_dir, exist_ok=True)
        ctx = await pw.chromium.launch_persistent_context(
            config.fb_profile_dir,
            headless=True,
            channel="chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            user_agent=_USER_AGENT,
        )
        page = await ctx.new_page()
        await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

        url = (
            f"https://www.facebook.com/marketplace/search/"
            f"?query={TEST_KEYWORD.replace(' ', '+')}"
            f"&sortBy=creation_time_descend"
        )
        print(f"Navigating to FB Marketplace...")
        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2.0, 3.5))

        # Check for login wall
        if "login" in page.url.lower() or await page.query_selector('input[name="email"]'):
            msg = "⚠️  FB LOGIN WALL DETECTED — run START_FACEBOOK_LOGIN.bat to refresh your session."
            print(msg)
            with open(OUTPUT_FILE, "w") as f:
                f.write(msg + "\n")
            await ctx.close()
            return

        # Scroll to load more
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(1.2, 2.0))

        links = await page.query_selector_all('a[href*="/marketplace/item/"]')
        seen = set()
        for link in links[:25]:
            try:
                href = await link.get_attribute("href") or ""
                if not href or href in seen:
                    continue
                seen.add(href)
                full_url = f"https://www.facebook.com{href}" if href.startswith("/") else href
                full_url = full_url.split("?")[0].rstrip("/") + "/"
                if not validate_fb_url(full_url):
                    continue
                raw = sanitize_text(await link.inner_text() or "", max_length=300)
                if not raw:
                    continue
                leads.append({
                    "url": full_url,
                    "text": raw,
                    "urgency": _score_urgency(raw),
                    "size": _score_size(raw),
                    "phone": extract_phone(raw) or "—",
                })
            except Exception:
                pass

        await ctx.close()

    # Print + save results
    lines = []
    if not leads:
        lines.append("No listings found — FB may have changed its layout or the session needs refresh.")
    else:
        lines.append(f"✅  Found {len(leads)} listing(s) for '{TEST_KEYWORD}':\n")
        for i, lead in enumerate(leads, 1):
            lines.append(f"[{i}] Urgency={lead['urgency']}/10  Size={lead['size']}/10  Phone={lead['phone']}")
            lines.append(f"    {lead['text'][:120]}")
            lines.append(f"    {lead['url']}\n")

    output = "\n".join(lines)
    print(output)
    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"Test run: {datetime.now()}\nKeyword: {TEST_KEYWORD}\n\n")
        f.write(output)
    print(f"\nResults saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(run_test())

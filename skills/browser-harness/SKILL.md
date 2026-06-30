---
name: browser-harness
category: web-automation
description: Robust browser automation with proxy rotation, anti-detection, and debugging features
version: 0.1.3
source: https://github.com/browser-use/browser-harness
---

# Browser Harness Skill

Advanced web automation with:
- Proxy rotation
- Stealth mode
- Visual debugging
- Screenshot capability

## Setup
```python
from browser_harness import Harness

harness = Harness(
    headless=True,  # Set False for debugging
    stealth_mode=True,
    proxy_rotation=True
)
```

## Core Features

### 1. Smart Scraping
```python
async def scrape(url):
    await harness.launch()
    await harness.goto(url)
    content = await harness.extract_content()
    await harness.close()
    return content
```

### 2. Debug Mode
```python
harness = Harness(headless=False)  # Visible browser
await harness.goto(url)
await harness.screenshot('debug.png')
```

### 3. Anti-Bot Evasion
```python
await harness.enable_stealth()  # Bypass bot detection
```

## Integration Guide

### With Playwright
```python
# Use alongside existing Playwright scripts
from browser_harness import Harness
from playwright.async_api import async_playwright

harness = Harness()
async with async_playwright() as pw:
    browser = await pw.chromium.launch()
    # ... use both tools together
```
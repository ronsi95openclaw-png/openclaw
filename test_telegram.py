"""Quick smoke-test for Telegram alert delivery.

Sends a single online notification to the configured chat.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Run:
    py test_telegram.py
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from core.telegram_bot import AlertType, send_alert


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping live send.")
        print("Set them in .env and re-run to test delivery.")
        return

    print("Sending test alert...")
    await send_alert(
        AlertType.TRADE_SIGNAL,
        message="🦾 ClawBot is online and watching the markets.",
    )
    print("Alert sent successfully.")


if __name__ == "__main__":
    asyncio.run(main())

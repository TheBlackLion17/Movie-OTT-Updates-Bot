"""Test the optional Telegram log channel. Sends exactly one test message."""
import asyncio
import os
import logging

import httpx
from dotenv import load_dotenv

logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    load_dotenv("/opt/movie-ott-bot/.env") if os.path.exists("/opt/movie-ott-bot/.env") else load_dotenv(".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    channel = os.getenv("LOG_CHANNEL_ID", "").strip()
    if not token:
        print("ERROR: BOT_TOKEN is missing from .env")
        return
    if not channel:
        print("ERROR: LOG_CHANNEL_ID is empty. Configure the numeric -100... ID or public @username.")
        return
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            me = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            me_data = me.json()
            if not me_data.get("ok"):
                print("Bot credentials invalid:", me_data.get("description", "Unknown error"))
                return
            print("Bot:", "@" + me_data["result"]["username"])
            membership = await client.get(
                f"https://api.telegram.org/bot{token}/getChatMember",
                params={"chat_id": channel, "user_id": me_data["result"]["id"]})
            membership_data = membership.json()
            if not membership_data.get("ok"):
                print("Log channel lookup failed:", membership_data.get("description", "Unknown error"))
                print("Check LOG_CHANNEL_ID and add the bot as an administrator.")
                return
            member = membership_data["result"]
            print("Membership:", member.get("status"))
            if member.get("status") not in ("administrator", "creator"):
                print("ERROR: Bot must be a log channel administrator.")
                return
            if member.get("status") == "administrator" and not member.get("can_post_messages", False):
                print("ERROR: Bot administrator needs permission to post messages.")
                return
            result = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": channel, "text": "✅ Movie & OTT Bot: log channel test successful."})
            data = result.json()
            print("Telegram HTTP status:", result.status_code)
            print("Message sent:", data.get("ok", False))
            if not data.get("ok"):
                print("Telegram error:", data.get("description", "Unknown error"))
        except (httpx.HTTPError, ValueError) as exc:
            # Never print HTTP exception URLs: they may contain the bot token.
            print("Test failed:", type(exc).__name__)


if __name__ == "__main__":
    asyncio.run(main())

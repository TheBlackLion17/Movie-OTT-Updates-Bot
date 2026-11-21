"""Verify Telegram bot identity and channel posting rights without publishing.

Run: python -m app.check_telegram
"""
import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main():
    token = os.getenv("BOT_TOKEN", "").strip()
    channel = os.getenv("CHANNEL_ID", "").strip()
    if not token or not channel:
        print("Missing BOT_TOKEN or CHANNEL_ID in .env")
        return 1

    base = f"https://api.telegram.org/bot{token}/"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            async def api(method, params=None):
                response = await client.post(base + method, data=params or {})
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("description", "Telegram API rejected request"))
                return payload["result"]

            bot = await api("getMe")
            chat = await api("getChat", {"chat_id": channel})
            member = await api("getChatMember", {
                "chat_id": channel, "user_id": bot["id"]
            })
        print("Bot:", "@" + bot.get("username", "(no username)"))
        print("Destination:", chat.get("title", chat.get("type", "Unknown")))
        print("Chat type:", chat.get("type", "Unknown"))
        print("Membership:", member.get("status", "Unknown"))
        status = member.get("status")
        can_post = status == "creator" or (
            status == "administrator" and member.get("can_post_messages") is True
        )
        if chat.get("type") != "channel":
            print("FAILED: Destination is not a Telegram channel.")
            return 1
        if not can_post:
            print("FAILED: Bot is not authorized to post in this channel.")
            return 1
        print("Telegram channel posting permission: OK")
        print("No messages were sent.")
        return 0
    except Exception as exc:
        print("Telegram check failed:", type(exc).__name__)
        # Do not print HTTP URLs: Telegram bot tokens are embedded in API URLs.
        if isinstance(exc, RuntimeError):
            print("Telegram response:", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

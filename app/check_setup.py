"""Safe local diagnostics: python -m app.check_setup (never publishes)."""
import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main():
    required = ("BOT_TOKEN", "CHANNEL_ID", "TMDB_API_KEY",
                "MYSQL_USER", "MYSQL_PASSWORD")
    missing = [name for name in required if not os.getenv(name) or os.getenv(name) == "replace_me"]
    if missing:
        print("Missing .env settings:", ", ".join(missing))
        return 1

    try:
        from app.main import init_db, today_count, target
        init_db()
        print("MySQL connection: OK")
        print("Posts published today:", today_count())
        print("Daily posting target:", target())
    except Exception as exc:
        print("MySQL check failed:", type(exc).__name__, str(exc))
        return 1

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.themoviedb.org/3/configuration",
                params={"api_key": os.environ["TMDB_API_KEY"]})
            response.raise_for_status()
            print("TMDb API connection: OK")
    except Exception as exc:
        print("TMDb check failed:", type(exc).__name__, str(exc))
        return 1

    print("Diagnostics complete. No Telegram posts sent.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

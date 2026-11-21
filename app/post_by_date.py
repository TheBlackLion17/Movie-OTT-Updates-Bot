"""Manually publish one TMDb title released on a selected date.

Usage:
  python -m app.post_by_date --date 2026-11-21
  python -m app.post_by_date --date 2026-11-21 --send
  python -m app.post_by_date --date 2026-11-21 --send --chat-id -1001234567890

Preview is the default. --send posts once, ignoring the scheduler's time window
but respecting DRY_RUN and the existing MySQL duplicate registry.
"""
import argparse
import asyncio
import logging
from datetime import date

import httpx

from app import main as publisher

log = logging.getLogger("movie-ott.manual")


async def find_titles(client, day):
    date_str = day.isoformat()
    for kind, field, result_field in (
        ("movie", "primary_release_date", "release_date"),
        ("tv", "first_air_date", "first_air_date"),
    ):
        for page in range(1, 4):
            data = await publisher.tmdb(
                client, f"discover/{kind}",
                **{
                    f"{field}.gte": date_str,
                    f"{field}.lte": date_str,
                    "sort_by": "popularity.desc",
                    "include_adult": "false",
                    "page": page,
                }
            )
            for item in data.get("results", []):
                if item.get(result_field) != date_str:
                    continue
                name = item.get("title") or item.get("name")
                if not name or not item.get("poster_path"):
                    continue
                key = f"tmdb:{kind}:{item['id']}"
                if publisher.seen(key):
                    continue
                lang = dict((code, label) for code, label in publisher.LANGS).get(
                    item.get("original_language"), "Other"
                )
                yield (kind, item["id"], name, lang, item)
            if page >= data.get("total_pages", 0):
                break


async def run(args):
    publisher.init_db()
    if args.chat_id and args.chat_id != publisher.CHANNEL:
        raise SystemExit(
            "Alternate destination is not supported by this command: "
            "the existing publish() function writes to CHANNEL_ID. "
            "Use the configured channel, or configure a separate bot instance."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        async for item in find_titles(client, args.date):
            kind, ident, name, language, _ = item
            print(f"Eligible title: {name} | {kind} | {language} | TMDb {ident}")
            print(f"Destination: {publisher.CHANNEL}")
            if not args.send:
                print("PREVIEW ONLY. Add --send to publish this title.")
                return
            if publisher.DRY:
                raise SystemExit("DRY_RUN=true. Set DRY_RUN=false in .env before --send.")
            if await publisher.publish(client, item):
                print(f"SUCCESS: Published {name}")
                return
        print("No eligible unpublished titles found for this date.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 11, 21))
    parser.add_argument("--send", action="store_true", help="Send a live post")
    parser.add_argument("--chat-id", help="Must match configured CHANNEL_ID")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

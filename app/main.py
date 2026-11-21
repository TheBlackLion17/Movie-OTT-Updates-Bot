"""Movie & OTT Telegram channel publisher. Run: python -m app.main"""
import asyncio
import html
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pymysql
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
# httpx request logs can include the bot token in Telegram API URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("movie-ott")
TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]
KEY = os.environ["TMDB_API_KEY"]
LOG_CHANNEL = os.getenv("LOG_CHANNEL_ID", "").strip()
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
DRY = os.getenv("DRY_RUN", "true").lower() == "true"
LANGS = [("ml", "Malayalam"), ("ta", "Tamil"), ("te", "Telugu"),
         ("hi", "Hindi"), ("en", "English"), ("ko", "Korean")]


def db():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.getenv("MYSQL_DATABASE", "movie_updates"),
        charset="utf8mb4", autocommit=True,
        cursorclass=pymysql.cursors.DictCursor)


def init_db():
    with db() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS posts (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            source_key VARCHAR(191) NOT NULL UNIQUE,
            title VARCHAR(255), language VARCHAR(40),
            status VARCHAR(24) NOT NULL,
            telegram_message_id BIGINT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at DATETIME NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""CREATE TABLE IF NOT EXISTS daily_targets (
            day DATE PRIMARY KEY, target INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


def today_count():
    now = datetime.now(TZ)
    start = datetime.combine(now.date(), datetime.min.time(), TZ).astimezone(timezone.utc).replace(tzinfo=None)
    end = (datetime.combine(now.date(), datetime.min.time(), TZ) + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM posts WHERE status='published' AND published_at >= %s AND published_at < %s", (start, end))
        return cur.fetchone()["n"]


def target():
    day = datetime.now(TZ).date()
    low = int(os.getenv("DAILY_MIN", "6"))
    high = int(os.getenv("DAILY_MAX", "10"))
    if not 1 <= low <= high <= 30:
        raise ValueError("Invalid daily post limits")
    with db() as conn, conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO daily_targets(day,target) VALUES (%s,%s)", (day, random.randint(low, high)))
        cur.execute("SELECT target FROM daily_targets WHERE day=%s", (day,))
        return cur.fetchone()["target"]


def seen(key):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM posts WHERE source_key=%s", (key,))
        return cur.fetchone() is not None


def record(key, title, language, status, message_id=None):
    with db() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO posts(source_key,title,language,status,telegram_message_id,published_at)
            VALUES (%s,%s,%s,%s,%s,IF(%s='published',UTC_TIMESTAMP(),NULL))""",
            (key, title[:255], language, status, message_id, status))


async def notify_log(message):
    """Send operational alerts to a separate Telegram channel, if configured."""
    if not LOG_CHANNEL:
        return
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": LOG_CHANNEL, "text": message[:4000],
                      "disable_web_page_preview": True})
            payload = response.json()
            if not response.is_success or not payload.get("ok"):
                # Telegram descriptions are useful (e.g. chat not found), unlike
                # httpx exceptions whose URLs contain the private bot token.
                description = payload.get("description", "Unknown Telegram error")
                log.warning("Log channel notification failed (HTTP %s): %s",
                            response.status_code, description)
    except Exception as exc:
        # Never crash publishing because the optional log channel failed.
        # Avoid logging exception URLs, which can contain the bot token.
        log.warning("Log channel notification failed: %s", type(exc).__name__)


async def tmdb(client, path, **params):
    params["api_key"] = KEY
    response = await client.get("https://api.themoviedb.org/3/" + path, params=params)
    response.raise_for_status()
    return response.json()


async def candidates(client):
    items = []
    for code, label in LANGS:
        try:
            for kind in ("movie", "tv"):
                result = await tmdb(client, "discover/" + kind,
                    with_original_language=code, sort_by="popularity.desc",
                    include_adult="false", page=1)
                for item in result.get("results", [])[:12]:
                    if not item.get("poster_path"):
                        continue
                    name = item.get("title") or item.get("name")
                    if not name:
                        continue
                    items.append((kind, item["id"], name, label, item))
        except Exception:
            log.exception("TMDb fetch failed for %s", label)
    random.shuffle(items)
    return items


async def publish(client, item):
    kind, ident, name, language, data = item
    key = f"tmdb:{kind}:{ident}"
    if seen(key):
        return False
    url = f"https://www.themoviedb.org/{kind}/{ident}"
    caption = (
        f"🎬 <b>{html.escape(name)}</b>\n\n"
        f"🌐 Language: {language}\n"
        f"📅 Release: {html.escape(data.get('release_date') or data.get('first_air_date') or 'Not confirmed')}\n"
        f"⭐ TMDb rating: {data.get('vote_average', 0):.1f}/10\n\n"
        f"{html.escape((data.get('overview') or '')[:350])}\n\n"
        f"ℹ️ Movie/TV information, not a confirmed OTT premiere.\n"
        f"#MovieUpdates #OTTUpdates")
    poster = "https://image.tmdb.org/t/p/w780" + data["poster_path"]
    trailer = None
    try:
        videos = await tmdb(client, f"{kind}/{ident}/videos", language="en-US")
        for video in videos.get("results", []):
            if video.get("site") == "YouTube" and video.get("type") == "Trailer" and video.get("official"):
                trailer = "https://www.youtube.com/watch?v=" + video["key"]
                break
    except Exception:
        log.exception("Trailer lookup failed for %s", name)
    buttons = [[{"text": "🎬 Details", "url": url}]]
    if trailer:
        buttons.insert(0, [{"text": "▶ Official Trailer", "url": trailer}])
    if DRY:
        log.info("DRY RUN: %s (%s)", name, language)
        return False
    response = await client.post(
        f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
        json={"chat_id": CHANNEL, "photo": poster, "caption": caption,
              "parse_mode": "HTML", "reply_markup": {"inline_keyboard": buttons}})
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(str(result.get("description")))
    record(key, name, language, "published", result["result"]["message_id"])
    log.info("Published %s", name)
    await notify_log(f"✅ Movie/OTT post published\nTitle: {name}\nLanguage: {language}\nType: {kind}\nTMDb ID: {ident}\nTelegram message ID: {result['result']['message_id']}\nTime: {datetime.now(TZ):%Y-%m-%d %H:%M} {TZ}")
    return True


async def tick():
    hour = datetime.now(TZ).hour
    start = int(os.getenv("POST_START_HOUR", "9"))
    end = int(os.getenv("POST_END_HOUR", "22"))
    if not 0 <= start < end <= 24:
        log.error("Invalid publishing window: %s-%s", start, end)
        return
    if not start <= hour < end:
        log.info("Skipping tick: outside publishing hours (%02d:00-%02d:00 %s); current time %s",
                 start, end, TZ, datetime.now(TZ).strftime("%Y-%m-%d %H:%M"))
        return
    try:
        count, goal = today_count(), target()
        log.info("Publishing tick: %s/%s posts today; dry_run=%s", count, goal, DRY)
        if count >= goal:
            log.info("Skipping tick: daily target reached")
            return
        # Hourly pacing spreads posts across the configured publishing window.
        elapsed = max(1, hour - start + 1)
        allowance = max(1, (goal * elapsed + (end - start) - 1) // (end - start))
        if count >= allowance:
            log.info("Skipping tick: pacing limit reached (%s/%s allowed so far)", count, allowance)
            return
        async with httpx.AsyncClient(timeout=25) as client:
            available = await candidates(client)
            for item in available:
                if not seen(f"tmdb:{item[0]}:{item[1]}"):
                    await publish(client, item)
                    break
            else:
                log.warning("No new eligible TMDb titles found among %s candidates", len(available))
                await notify_log(f"⚠️ No new eligible TMDb titles found ({len(available)} checked).")
    except Exception as exc:
        log.exception("Publishing tick failed")
        await notify_log(f"❌ Publishing tick failed\nError type: {type(exc).__name__}\nTime: {datetime.now(TZ):%Y-%m-%d %H:%M} {TZ}\nCheck VPS journal for details.")


async def main():
    init_db()
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(tick, "cron", minute="0,30", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Publisher started: timezone=%s, window=%02d:00-%02d:00, dry_run=%s, next_check=%s",
             TZ, int(os.getenv("POST_START_HOUR", "9")),
             int(os.getenv("POST_END_HOUR", "22")), DRY,
             scheduler.get_jobs()[0].next_run_time)
    await notify_log(f"🤖 Movie & OTT Updates Bot started\nStatus: Online\nMain channel: {CHANNEL}\nMode: {'DRY RUN' if DRY else 'LIVE'}\nTimezone: {TZ}\nPublishing hours: {os.getenv('POST_START_HOUR', '9')}:00–{os.getenv('POST_END_HOUR', '22')}:00\nNext scheduled check: {scheduler.get_jobs()[0].next_run_time:%Y-%m-%d %H:%M %Z}")
    await tick()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())

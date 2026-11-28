"""Movie & OTT Telegram channel publisher. Run: python -m app.main"""
import asyncio
import html
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pymysql
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from script import init_welcome_db, welcome_loop

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
    # Download the poster ourselves; Telegram cannot always fetch TMDb image URLs.
    # Do not publish a text-only substitute: the poster is required.
    image_response = await client.get(poster, follow_redirects=True)
    if not image_response.is_success:
        raise RuntimeError(f"TMDb poster download failed (HTTP {image_response.status_code})")
    content_type = image_response.headers.get("content-type", "").split(";")[0].lower()
    if content_type not in ("image/jpeg", "image/png"):
        raise RuntimeError(f"TMDb poster returned unsupported content type: {content_type}")
    photo_bytes = image_response.content
    if not photo_bytes or len(photo_bytes) > 10 * 1024 * 1024:
        raise RuntimeError("TMDb poster is empty or exceeds Telegram's 10 MB photo limit")
    response = await client.post(
        f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
        data={"chat_id": CHANNEL, "caption": caption, "parse_mode": "HTML",
              "reply_markup": json.dumps({"inline_keyboard": buttons})},
        files={"photo": ("poster.jpg" if content_type == "image/jpeg" else "poster.png",
                         photo_bytes, content_type)},
        timeout=60,
    )
    result = response.json()
    if not response.is_success or not result.get("ok"):
        description = result.get("description", "Unknown Telegram error")
        raise RuntimeError(f"Telegram poster upload failed (HTTP {response.status_code}): {description}")
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



def news_sources():
    return [u.strip() for u in os.getenv("NEWS_RSS_URLS", "").split(",") if u.strip()]


async def news_tick():
    import hashlib
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from urllib.parse import urlparse
    now = datetime.now(timezone.utc)
    urls = news_sources()
    if not urls:
        log.warning("NEWS_RSS_URLS is empty; no news will be published")
        return
    entries = []
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        for url in urls:
            try:
                if urlparse(url).scheme != "https":
                    continue
                response = await client.get(url)
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for item in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                    def field(name):
                        return item.findtext(name) or item.findtext("{http://www.w3.org/2005/Atom}" + name) or ""
                    title = field("title").strip()
                    link = field("link").strip()
                    if not link:
                        for node in item.findall("{http://www.w3.org/2005/Atom}link"):
                            if node.get("rel", "alternate") == "alternate":
                                link = node.get("href", "")
                                break
                    date = field("pubDate") or field("published") or field("updated")
                    try:
                        try:
                            published = parsedate_to_datetime(date)
                        except (ValueError, TypeError):
                            published = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        if published.tzinfo is None:
                            continue
                        if not timedelta(0) <= now - published <= timedelta(hours=24):
                            continue
                    except (ValueError, TypeError, OverflowError):
                        continue
                    if not title or urlparse(link).scheme not in ("https", "http"):
                        continue
                    key = "news:" + hashlib.sha256(link.encode()).hexdigest()
                    entries.append((published, key, title, link))
            except Exception as exc:
                log.warning("News feed failed: %s", type(exc).__name__)
        for published, key, title, link in sorted(entries, reverse=True):
            try:
                if seen(key):
                    continue
                if DRY:
                    log.info("DRY RUN news: %s", title)
                    continue
                caption = "📰 <b>Latest Entertainment News</b>\n\n" + html.escape(title[:500]) + "\n\n📅 " + published.astimezone(TZ).strftime("%d %b %Y, %I:%M %p") + "\n\n#MovieNews #Entertainment"
                response = await client.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                    json={"chat_id": CHANNEL, "text": caption, "parse_mode": "HTML",
                          "reply_markup": {"inline_keyboard": [[{"text": "Read full update", "url": link}]]}})
                result = response.json()
                if not response.is_success or not result.get("ok"):
                    log.warning("News publishing rejected (HTTP %s)", response.status_code)
                    continue
                record(key, title, "News", "published", result["result"]["message_id"])
                await notify_log("News published: " + title[:250])
                await asyncio.sleep(1)
            except Exception as exc:
                log.warning("News publishing failed: %s", type(exc).__name__)


async def main():
    init_db()
    init_welcome_db(db)
    welcome_task = asyncio.create_task(welcome_loop(db, TOKEN))
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(news_tick, "cron", minute="*/15", max_instances=1, coalesce=True)
    log.info("24/7 news publisher started; dry_run=%s", DRY)
    await notify_log("Movie news publisher online: checks every 15 minutes.")
    await news_tick()
    scheduler.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        welcome_task.cancel()
        await asyncio.gather(welcome_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())

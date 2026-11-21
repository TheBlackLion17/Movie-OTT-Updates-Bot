"""Private-chat welcome message and owner/admin management for the publisher.

Uses the same Bot API token and MySQL connection as app.main.
Only one running instance may poll getUpdates for a bot token.
"""
import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx

log = logging.getLogger("movie-ott.welcome")
DEFAULT_TEXT = "🎬 Welcome to AGS Movie & OTT Updates!\n\nGet movie, series and OTT information here."
MAX_BUTTONS = 20


def init_welcome_db(db):
    with db() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS welcome_settings (
            id TINYINT PRIMARY KEY,
            welcome_text TEXT NOT NULL,
            photo_file_id VARCHAR(512) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("INSERT IGNORE INTO welcome_settings(id,welcome_text) VALUES(1,%s)", (DEFAULT_TEXT,))
        cur.execute("""CREATE TABLE IF NOT EXISTS welcome_buttons (
            id INT AUTO_INCREMENT PRIMARY KEY,
            label VARCHAR(64) NOT NULL,
            url VARCHAR(2048) NOT NULL,
            position INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


def _settings(db):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT welcome_text,photo_file_id FROM welcome_settings WHERE id=1")
        return cur.fetchone()


def _buttons(db):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,label,url FROM welcome_buttons ORDER BY position,id")
        return cur.fetchall()


def _save(db, field, value):
    if field not in ("welcome_text", "photo_file_id"):
        raise ValueError("Invalid setting")
    with db() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE welcome_settings SET {field}=%s WHERE id=1", (value,))


def _valid_url(value):
    parsed = urlparse(value)
    return parsed.scheme in ("https", "http") and bool(parsed.netloc) or (
        value.startswith("tg://") and bool(parsed.netloc)
    )


async def welcome_loop(db, token):
    owner = int(os.getenv("OWNER_ID", "0"))
    admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
    if not owner:
        log.warning("Welcome commands disabled: set OWNER_ID in .env")
        return
    admins.add(owner)
    api = f"https://api.telegram.org/bot{token}/"
    offset = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10)) as client:
        async def call(method, data):
            response = await client.post(api + method, json=data)
            result = response.json()
            if not result.get("ok"):
                # Do not include exception URLs; they contain the secret bot token.
                raise RuntimeError(result.get("description", "Telegram API error"))
            return result["result"]

        async def reply(chat_id, text):
            await call("sendMessage", {"chat_id": chat_id, "text": text[:4096]})

        async def show_start(chat_id):
            settings = _settings(db)
            buttons = _buttons(db)
            keyboard = {"inline_keyboard": [
                [{"text": b["label"], "url": b["url"]}] for b in buttons
            ]}
            message = settings["welcome_text"]
            photo = settings["photo_file_id"]
            if photo:
                payload = {"chat_id": chat_id, "photo": photo,
                           "caption": message[:1024], "reply_markup": keyboard}
                try:
                    await call("sendPhoto", payload)
                    if len(message) > 1024:
                        await reply(chat_id, message[1024:])
                    return
                except RuntimeError as exc:
                    log.warning("Welcome photo failed: %s", str(exc)[:180])
            await call("sendMessage", {"chat_id": chat_id, "text": message[:4096],
                                      "reply_markup": keyboard})

        log.info("Welcome command polling enabled")
        while True:
            try:
                args = {"timeout": 25, "allowed_updates": ["message"]}
                if offset is not None:
                    args["offset"] = offset
                updates = await call("getUpdates", args)
                for update in updates:
                    offset = update["update_id"] + 1
                    msg = update.get("message") or {}
                    chat = msg.get("chat") or {}
                    user = msg.get("from") or {}
                    if chat.get("type") != "private" or not user.get("id"):
                        continue
                    chat_id = chat["id"]
                    user_id = user["id"]
                    raw = (msg.get("text") or msg.get("caption") or "").strip()
                    cmd, _, arg = raw.partition(" ")
                    cmd = cmd.split("@")[0].lower()
                    try:
                        if cmd == "/start":
                            await show_start(chat_id)
                        elif cmd == "/setstartphoto" and user_id == owner:
                            photos = msg.get("photo") or []
                            if photos:
                                _save(db, "photo_file_id", photos[-1]["file_id"])
                                await reply(chat_id, "✅ Welcome photo updated.")
                            elif arg.startswith(("https://", "http://")) and _valid_url(arg):
                                _save(db, "photo_file_id", arg.strip())
                                await reply(chat_id, "✅ Welcome photo URL updated.")
                            else:
                                await reply(chat_id, "Send a photo with caption /setstartphoto, or /setstartphoto https://example.com/photo.jpg")
                        elif cmd == "/removestartphoto" and user_id == owner:
                            _save(db, "photo_file_id", None)
                            await reply(chat_id, "✅ Welcome photo removed.")
                        elif cmd == "/setstarttext" and user_id == owner:
                            if not arg:
                                await reply(chat_id, "Usage: /setstarttext Your welcome text")
                            elif len(arg) > 4096:
                                await reply(chat_id, "Text must be 4096 characters or less.")
                            else:
                                _save(db, "welcome_text", arg)
                                await reply(chat_id, "✅ Welcome text updated.")
                        elif cmd == "/addbutton" and user_id in admins:
                            parts = [x.strip() for x in arg.split("|", 1)]
                            if len(parts) != 2 or not parts[0] or len(parts[0]) > 64 or not _valid_url(parts[1]) or len(parts[1]) > 2048:
                                await reply(chat_id, "Usage: /addbutton Button name | https://example.com")
                            elif len(_buttons(db)) >= MAX_BUTTONS:
                                await reply(chat_id, "Maximum 20 buttons reached.")
                            else:
                                with db() as conn, conn.cursor() as cur:
                                    cur.execute("INSERT INTO welcome_buttons(label,url,position) VALUES(%s,%s,%s)",
                                                (parts[0], parts[1], len(_buttons(db))))
                                await reply(chat_id, "✅ Button added.")
                        elif cmd == "/removebutton" and user_id in admins:
                            if not arg.strip().isdigit():
                                await reply(chat_id, "Usage: /removebutton 1 (use /listbuttons to see IDs)")
                            else:
                                with db() as conn, conn.cursor() as cur:
                                    cur.execute("DELETE FROM welcome_buttons WHERE id=%s", (int(arg.strip()),))
                                    changed = cur.rowcount
                                await reply(chat_id, "✅ Button removed." if changed else "Button not found.")
                        elif cmd == "/listbuttons" and user_id in admins:
                            buttons = _buttons(db)
                            await reply(chat_id, "\n".join(f'{b["id"]}. {b["label"]} — {b["url"]}' for b in buttons)[:4096] or "No buttons configured.")
                        elif cmd == "/starthelp" and user_id in admins:
                            commands = "/addbutton Name | https://example.com\n/removebutton ID\n/listbuttons"
                            if user_id == owner:
                                commands += "\n/setstartphoto (as photo caption)\n/removestartphoto\n/setstarttext Your text"
                            await reply(chat_id, commands)
                    except Exception as exc:
                        log.warning("Welcome command failed: %s", type(exc).__name__)
                        try:
                            await reply(chat_id, "⚠️ Command failed. Check VPS logs.")
                        except Exception:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log request URL, token or entire exception.
                log.warning("Welcome polling error: %s", type(exc).__name__)
                await asyncio.sleep(5)

# Movie & OTT Updates Bot

Python + MySQL Telegram channel publisher for Malayalam, Tamil, Telugu, Hindi, English, and Korean movies and TV shows.

**Current scope:** Automatic TMDb poster/metadata posts and links to official YouTube trailers where available. This version does **not** independently verify OTT platform premiere dates or ingest entertainment news RSS. The daily target is 6–10 posts, subject to available new titles and successful API requests. The default is safe dry-run mode.

## Ubuntu 24.04 installation

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip mysql-server git
sudo mysql -e "CREATE DATABASE IF NOT EXISTS movie_updates CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
sudo mysql -e "CREATE USER IF NOT EXISTS 'moviebot'@'localhost' IDENTIFIED BY 'REPLACE_WITH_STRONG_PASSWORD'; GRANT ALL PRIVILEGES ON movie_updates.* TO 'moviebot'@'localhost';"
sudo useradd --system --home-dir /opt/movie-ott-bot --shell /usr/sbin/nologin moviebot || true
sudo git clone https://github.com/TheBlackLion17/Movie-OTT-Updates-Bot.git /opt/movie-ott-bot
cd /opt/movie-ott-bot
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
sudo cp .env.example .env
sudo nano .env
sudo chown -R moviebot:moviebot /opt/movie-ott-bot
sudo chmod 600 .env
sudo cp deploy/movie-ott-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now movie-ott-bot
sudo journalctl -u movie-ott-bot -f
```

Set `BOT_TOKEN`, `CHANNEL_ID`, `TMDB_API_KEY`, `MYSQL_PASSWORD` in `.env`. Add the bot as an administrator of your Telegram channel with permission to post. Keep `DRY_RUN=true` for initial checks. When ready, change it to `false` and restart: `sudo systemctl restart movie-ott-bot`.

**Important:** Never commit your real `.env`, bot token, database password, or TMDb key. This code has not been tested against your production VPS. Verify it in dry-run mode before enabling automatic posting.

TMDb data and images are subject to TMDb attribution and usage requirements; consult their API terms before production use.

## Check setup (safe, no posts)

After configuring `.env` and the MySQL user, run:

```bash
cd /opt/movie-ott-bot
venv/bin/python -m app.check_setup
```

This checks required settings, initializes MySQL tables, verifies TMDb credentials, and prints the daily posting target. It does not send Telegram messages. It explicitly loads `.env` and avoids the `load_dotenv()` stdin `AssertionError` seen with `python -` snippets. Do not paste `.env` contents into chat or commit secrets.

## Verify Telegram channel permissions (no posts)

```bash
cd /opt/movie-ott-bot
venv/bin/python -m app.check_telegram
```

The command checks bot identity, destination channel and administrator posting rights using Telegram's read-only API methods. It does not publish. Keep `DRY_RUN=true` until verification and a controlled posting test are complete. Avoid sharing tokens or HTTP request URLs in logs.


## Optional Telegram operations log channel

Create a **private Telegram channel** for bot activity. Add the same bot as an administrator with permission to post messages. Obtain the channel's numeric ID (typically `-100...`) or use a public channel username, then set `LOG_CHANNEL_ID` in your VPS `.env`.

```env
LOG_CHANNEL_ID=-1001234567890
```

With this setting the bot sends startup status, successful post details (title, language, message ID and timestamp), no-candidate warnings, and publishing failure alerts to the log channel. A log delivery failure is written to the VPS journal but does not interrupt movie publishing. The log channel is optional; leave `LOG_CHANNEL_ID` empty to disable it.

After editing `.env`, deploy and restart:

```bash
cd /opt/movie-ott-bot
git pull --ff-only origin main
venv/bin/python -m py_compile app/main.py
sudo systemctl restart movie-ott-bot
sudo journalctl -u movie-ott-bot -n 40 --no-pager
```

The bot sends a startup notification on restart. Never paste or commit the real bot token or other secrets.

## Diagnose a log-channel HTTP 400 error

After configuring `LOG_CHANNEL_ID` in your VPS `.env`, run:

```bash
cd /opt/movie-ott-bot
git pull --ff-only origin main
venv/bin/python -m app.check_log_channel
sudo systemctl restart movie-ott-bot
sudo journalctl -u movie-ott-bot -n 25 --no-pager -l
```

The test checks bot membership and post permission, then sends **one** test message. For private channels use the numeric `-100...` ID, and grant the bot administrator permission to post. If Telegram rejects the request, the tool displays its description without printing the bot token. Normal publisher logs also show Telegram's error description without logging token-bearing HTTP request URLs. If a bot token has appeared in old logs or screenshots, revoke it through BotFather and update the VPS `.env` before restarting.

## Welcome message and admin buttons

The bot now runs private-chat /start commands through `script.py` alongside its existing scheduled publisher. Welcome settings and URL buttons persist in MySQL. Set `OWNER_ID` to your numeric Telegram user ID and optionally `ADMIN_IDS` to comma-separated numeric user IDs in the VPS `.env`. Do not put these IDs in source code. The owner may manage all commands; listed admins may manage buttons only.

Owner: send a Telegram **photo with caption** `/setstartphoto`, or `/setstartphoto https://example.com/image.jpg`. Use `/removestartphoto` to remove it. Set text with `/setstarttext Your message` (plain text). Photos have a Telegram caption limit of 1024 characters; longer text is sent as a follow-up message.

Admins: `/addbutton Movie Channel | https://t.me/example`, `/listbuttons`, `/removebutton 1`. Each button occupies one row. Use `/starthelp` to see your permitted commands. Changes take effect on the next `/start` without restarting.

**Important:** Only one process may use Telegram `getUpdates` with this bot token. Remove any webhook and stop other polling bots using the same token before enabling the welcome feature. The bot only handles commands in private chat. A missing `OWNER_ID` disables command polling but leaves scheduled publishing active.

Deployment: pull GitHub main, set `OWNER_ID` and `ADMIN_IDS` in `.env`, compile `script.py` and `app/main.py`, then restart `movie-ott-bot`. Do not share `.env` or token-bearing HTTP logs.

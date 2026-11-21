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

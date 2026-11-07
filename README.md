# Telegram Channel Auto Poster

A self-hosted Telegram bot that posts videos to one or many channels **6 times a day** (or any schedule you like) and is fully controlled from Telegram.

Built for **Termux on Android** and **GitHub**, and it also runs on any Linux VPS, Docker, Railway, Render, or a Raspberry Pi.

---

## Features

- **Automatic video posting** — 6 posts/day by default (`08:00, 11:00, 14:00, 17:00, 20:00, 23:00`).
- **Multi-channel** — every active channel receives each scheduled post.
- **Control panel inside Telegram** — channels, videos, schedule, pause/resume, manual posting, stats.
- **No video downloads** — the bot stores Telegram `file_id`s, so the files stay in Telegram's cloud and posting is instant. No storage used on your phone.
- **Smart rotation** — least-posted video goes next, so nothing is repeated before everything has had airtime.
- **Per-video captions + caption templates** — mix stored captions with `{channel}`, `{date}`, or your own signature.
- **Timezone aware** — set once with `/timezone Asia/Kolkata`; daylight saving is handled.
- **Offline catch-up** — if the bot was stopped, it posts at most one catch-up video when it comes back, instead of a spam burst.
- **Pause / resume** — stop everything instantly without losing the schedule.
- **Backups** — `/backup` sends you the SQLite database.
- **Self-update from GitHub** — `/update` runs `git pull`, then restarts automatically thanks to the Termux watchdog.
- **Tests + CI** — GitHub Actions runs lint and tests on every push.
- **Private admin bot** — only IDs listed in `ADMIN_IDS` can control it.

---

## How it works

```text
Telegram app
    │  commands + videos
    ▼
Telegram Bot API  ⇄  main.py ── scheduler every 30s ──► your channels
                        │
                        ├── SQLite (channels, videos, posts, settings)
                        └── 6 daily slots in your timezone
```

- You send videos to the bot once; they are added to the library.
- Every 30 seconds, the scheduler checks whether a posting time is due.
- When due, it sends the least-posted video to **all active channels** and advances the schedule.
- The bot must be an **administrator** in each channel with permission to post messages.

> **Important — GitHub vs. Termux:** GitHub stores the code, history and CI checks. It cannot run an interactive Telegram polling bot 24/7 (Actions cannot receive Telegram commands, jobs are delayed and can be disabled). The live bot runs on Termux/VPS/Docker. Push to GitHub, then pull on Termux and run it there.

---

## 1. Create the bot and get your admin ID

### Option A — automatic bot + channel setup (recommended)

The setup script logs in as **your Telegram account**, talks to **@BotFather**, creates the bot for you, and can add it as admin to your channels.

1. Get an **API ID** and **API hash**:
   - Open <https://my.telegram.org>.
   - Log in, open **API development tools**, and create an app.
   - Copy `api_id` and `api_hash`.
2. Add them to `.env` (create it from `.env.example` first):
   ```dotenv
   TG_API_ID=123456
   TG_API_HASH=your_api_hash_here
   TG_PHONE=+15551234567
   BOT_NAME=Telegram Auto Poster
   TG_BOT_USERNAME=my_autoposter_bot
   ```
   `TG_BOT_USERNAME` must end in `bot` (5–32 characters). If it is taken, the script tries a random suffix.
3. Install the userbot dependency and run the setup:
   ```bash
   pip install -r requirements-userbot.txt
   python tools/create_telegram_channels.py --create-bot
   ```
4. Telethon asks for the login code (and 2FA password if enabled). After that it:
   - creates the bot through **@BotFather**;
   - saves `TELEGRAM_BOT_TOKEN` and your numeric `ADMIN_IDS` into `.env`;
   - adds the bot as administrator to the channels listed in `ADD_BOT_TO` inside the script;
   - registers those channels in `data/autoposter.db` if the bot has already created it;
   - prints the channel `chat_id` values. If the database did not exist yet, send `/addchannel <chat_id>` to the bot after it starts.
5. Check `.env`, then continue with [Configure](#2-configure).

> **Already created the bot manually?** Put its token in `.env` as
> `TELEGRAM_BOT_TOKEN`, set `TG_BOT_USERNAME`, and run:
> ```bash
> python tools/create_telegram_channels.py --write-env
> ```
> This skips bot creation, saves your numeric `ADMIN_IDS`, adds the bot to the
> channels in `ADD_BOT_TO`, and registers them if the database exists.

> The script is a one-time helper for your own account. Automating @BotFather is not an official Bot API feature, so do not run it in a loop. Keep `.env`, your API hash, and `data/userbot.session` private — they are ignored by Git.

### Option B — manual setup

1. Open **[@BotFather](https://t.me/BotFather)** in Telegram.
2. Send `/newbot`, choose a name and username. Copy the **token** (looks like `123456789:AA...`).
3. Get your numeric Telegram user ID:
   - Send `/start` to **[@userinfobot](https://t.me/userinfobot)**, or
   - Start this bot after setup — it replies with your ID if you are not authorized yet.
4. Create a channel, open **Administrators**, and add your bot with **Post messages** permission.

You will also need a **channel** where you are an administrator.

---

## 2. Configure

Copy the example file:

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Token from BotFather |
| `ADMIN_IDS` | yes | Your numeric Telegram ID. Multiple: `111,222` |
| `TIMEZONE` | no | Initial IANA timezone, default `UTC`. After first start, use `/timezone` |
| `DATABASE_PATH` | no | Default `data/autoposter.db` |
| `LOG_LEVEL` | no | `INFO` by default |
| `ENABLE_SELF_UPDATE` | no | Allows `/update` to `git pull` and restart |
| `NOTIFY_STARTUP` | no | Send “bot online” message to admins on start |
| `MEDIA_DIRS` | no | Watched local folders, separated by `;` or `,` |
| `IMPORT_INTERVAL` | no | Seconds between folder scans, default `60` |
| `IMPORT_MAX_PER_SCAN` | no | Maximum files imported per scan, default `25` |
| `IMPORT_RECURSIVE` | no | Scan subfolders, default `true` |
| `IMPORT_FILENAME_CAPTION` | no | Use the filename as the caption, default `false` |
| `MAX_UPLOAD_MB` | no | Maximum local file size to upload, default `49` |

> The `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`, `BOT_NAME`, and
> `TG_BOT_USERNAME` variables are only used by the one-time userbot setup in
> section 1. The running bot does not need them.

Example:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenFromBotFather
ADMIN_IDS=987654321
TIMEZONE=Asia/Kolkata
```

---

## 3. Run on Termux (Android, 24/7)

Use **Termux from F-Droid**, not the old Play Store build. Also install **Termux:Boot** from F-Droid if you want autostart.

### Install

```bash
pkg update -y && pkg install -y python git
# Clone your GitHub repo (or copy the project folder to the phone):
git clone https://github.com/YOUR_USERNAME/telegram-autoposter.git
cd telegram-autoposter

bash setup-termux.sh
nano .env          # add token and ADMIN_IDS
python main.py     # first test: send /start to your bot
```

Press `Ctrl+C` after the test, then run it 24/7:

```bash
bash run-termux.sh
```

For a detached background process:

```bash
nohup bash run-termux.sh >/dev/null 2>&1 &
```

### Start automatically after reboot

```bash
bash install-boot.sh
```

Then:

1. Install **Termux:Boot** from F-Droid and open it once.
2. Disable Android battery optimization for Termux (Settings → Apps → Termux → Battery → Unrestricted).
3. Reboot the phone and check `logs/termux.log`.

### Useful Termux commands

```bash
tail -f logs/termux.log     # watchdog + bot output
tail -f logs/bot.log        # application log
pkill -f run-termux.sh      # stop the watchdog and bot
bash run-termux.sh          # start again
```

The `run-termux.sh` watchdog restarts the bot after a crash and after `/update`.

> Android can still kill background apps on aggressive battery savers. The wake lock, Termux:Boot and “Unrestricted” battery setting are required for reliable 24/7 operation. A small VPS is more reliable if you need guaranteed uptime.

---

## 4. Put it on GitHub

From the project folder:

```bash
git init
git add .
git commit -m "Telegram channel auto poster"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/telegram-autoposter.git
git push -u origin main
```

The bundled workflow `.github/workflows/ci.yml` runs `ruff` and `pytest` on every push.

**Never commit `.env`** — it contains your bot token. It is already in `.gitignore`.

After pushing, clone the repo on Termux (or `git pull` there). Then `/update` in Telegram can pull future changes and restart the bot.

---

## 5. Add a channel

1. In the channel: **Administrators → Add Administrator → select your bot**.
2. Give it **Post messages** permission.
3. In the bot: `/addchannel @mychannel` or open **📢 Channels → Add another channel**.

Accepted formats:

```text
/addchannel @mychannel
/addchannel -1001234567890
/addchannel https://t.me/mychannel
/addchannel https://t.me/c/1234567890/5   ← private channel post link
```

The bot verifies it can post before saving the channel. For a private channel you must add the bot as admin **before** running `/addchannel`.

---

## 6. Add videos

Just **send or forward videos to the bot** — they are added automatically. No download happens; the bot stores the Telegram file ID. You can also send a video as a file if normal video compression is too aggressive.

Each video keeps the caption you sent with it. The rotation uses the least-posted video first.

Use `/videos` to browse and delete entries, `/addvideo` for instructions.

---

## 7. Schedule and control

Default: **6 posts per day** at `08:00, 11:00, 14:00, 17:00, 20:00, 23:00` in your timezone.

```text
/settimes 08:00 11:00 14:00 17:00 20:00 23:00
/timezone Asia/Kolkata
/pause
/resume
/postnow
```

- `/schedule` shows presets (3×, 4×, 6×/day) and the next post time.
- Each scheduled slot sends **one video to every active channel**.
- Pausing stops scheduled posts immediately. `/resume` sets the next post to the next future slot.
- `/postnow` sends the next video to one channel or all active channels without changing the schedule.

### All commands

| Command | What it does |
|---|---|
| `/start`, `/menu` | Control panel |
| `/status` | Uptime, channels, library size, post counts, next post |
| `/channels` | List / pause / resume / remove channels |
| `/addchannel <@name or ID or link>` | Add a channel (bot must be admin) |
| `/removechannel <@name or ID>` | Remove a channel |
| `/addmedia`, `/addvideo` | Instructions; or simply send/forward a photo, GIF, or video |
| `/videos` | Browse, pause/resume, and delete library entries |
| `/delvideo <id>` | Delete a media item by library ID |
| `/folders` | List and manage watched local media folders |
| `/addfolder <path>` | Add a folder to watch for photos, GIFs, and videos |
| `/removefolder <id or path>` | Stop watching a folder |
| `/scan` | Scan watched folders for new media immediately |
| `/schedule` | View schedule and presets |
| `/settimes HH:MM ...` | Set custom daily times |
| `/timezone <IANA>` | Set timezone, e.g. `Europe/London` |
| `/caption` | Show/change the caption template |
| `/postnow` | Post immediately |
| `/pause`, `/resume` | Pause/resume scheduled posting |
| `/backup` | Download the SQLite database |
| `/update` | `git pull` from GitHub and restart |
| `/help` | Help |
| `/id` | Show your Telegram user/chat ID |

### Caption templates

Set with `/caption`:

```text
/caption {caption}\n\n@mychannel
/caption {caption}\n\n{channel} • {date}
```

Variables:

- `{caption}` — caption stored with the video
- `{channel}` — channel title
- `{date}` — local post date/time
- Use `\n` for a new line

The bot escapes HTML automatically, so captions with `<`, `&`, etc. are safe.

---

## Run on Windows

1. Double-click `setup-windows.bat` (or run it in Command Prompt / PowerShell) to create `.venv` and install requirements.
2. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `ADMIN_IDS`.
3. Double-click `start-windows.bat` to launch the bot.

### Start automatically at login

Press `Win+R`, type `shell:startup`, and press Enter. Create a shortcut to
`start-windows.bat` in that folder (right-click → New → Shortcut). Set the
shortcut to run **Minimized** so the console stays out of the way but can still
be reopened from the taskbar.

Only one copy may poll the bot token at a time. If you also run the bot on
Termux or a VPS, stop the Windows copy first.

---

## Run on a VPS / Docker (alternative to Termux)

### Docker Compose

```bash
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` keeps it alive and makes `/update` restart it automatically.

### Linux systemd

```bash
sudo useradd --system --create-home autoposter
sudo mkdir -p /opt/telegram-autoposter
sudo chown autoposter:autoposter /opt/telegram-autoposter
# copy the project there, then:
cd /opt/telegram-autoposter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env

sudo cp deploy/telegram-autoposter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-autoposter
sudo journalctl -u telegram-autoposter -f
```

The service has `Restart=always`, so `/update` works there too.

---

## Update the bot

```bash
# on the phone / server
git pull
python -m pip install -r requirements.txt   # only if dependencies changed
```

Or from Telegram: **`/update`** (requires a git clone and `ENABLE_SELF_UPDATE=true`). The bot pulls the latest code, then exits; the watchdog/Docker/systemd restarts it. If `requirements.txt` changed, run `bash setup-termux.sh` or the pip command afterwards.

---

## Backup and restore

- `/backup` checkpoints the SQLite WAL and sends you `autoposter-backup-YYYYMMDD-HHMM.db`.
- To restore: stop the bot, replace `data/autoposter.db` with the backup, start again.
- Video files live in Telegram, not in the database — backups are tiny. `file_id`s remain valid for the same bot token.

---

## Troubleshooting

**The bot does not respond.**
- Check `logs/bot.log` and `logs/termux.log`.
- Confirm the token is correct and `ADMIN_IDS` contains your numeric ID.
- Only one bot instance may poll the same token. If you see `Conflict: terminated by other getUpdates request`, stop the other copy:

```bash
pkill -f run-termux.sh
pkill -f "main.py"
```

**Posts are not appearing.**
- `/status` — is it paused? Is the video library empty? Is the channel active?
- The bot must still be an admin with “Post messages” permission.
- Check `logs/bot.log` for Telegram errors.
- Use `/postnow` to test immediately.

**Private channel cannot be added.**
- Add the bot as administrator first.
- Copy a post link like `https://t.me/c/1234567890/5` and use `/addchannel <link>`. Invite links `t.me/+...` cannot be resolved by bots.

**Times look wrong.**
- Set your zone: `/timezone Asia/Kolkata` (use an IANA name). Check `/schedule` for the next post time.

**`api.telegram.org` times out, but `t.me` opens normally.**
- Some ISPs silently drop TLS connections whose server name (SNI) is `api.telegram.org`. This is not a Telegram outage.
- Enable the bundled localhost relay in `.env` and restart the bot:
  ```env
  TELEGRAM_SNI_RELAY=true
  ```
  It listens on `127.0.0.1:8443`, is used only by the bot, and keeps normal certificate validation.
- If you already have a VPN or proxy, set `TELEGRAM_PROXY=http://127.0.0.1:8080` (or a SOCKS5 URL) instead.
- To test the relay alone: `.venv\Scripts\python.exe -m app.relay` — it should print `listening on 127.0.0.1:8443`.

**Termux stops after a while.**
- Disable battery optimization for Termux.
- Keep the wake lock (the run script enables it).
- Install Termux:Boot and use `install-boot.sh`.
- Check that Android didn’t restrict background data for Termux.

**`/update` says “not a git repository”.**
- Clone the project from GitHub instead of copying a ZIP, or update manually.

---

## Project structure

```text
.
├── main.py                     # startup, logging, polling
├── app/
│   ├── config.py               # .env config
│   ├── constants.py            # defaults and presets
│   ├── database.py             # async SQLite layer
│   ├── handlers.py             # commands + callback routing
│   ├── importer.py             # folder scanning + watcher loop
│   ├── notify.py               # admin notifications
│   ├── poster.py               # media delivery + scheduler
│   ├── relay.py                # optional localhost SNI relay
│   ├── utils.py                # schedule/caption/channel helpers
│   └── views.py                # Telegram screens
├── tests/                      # pytest suite
├── tools/
│   └── create_telegram_channels.py  # userbot setup: bot + channels
├── requirements-userbot.txt    # optional Telethon dependency
├── .github/workflows/ci.yml    # GitHub Actions lint + tests
├── deploy/telegram-autoposter.service
├── setup-windows.bat
├── start-windows.bat
├── setup-termux.sh
├── run-termux.sh
├── install-boot.sh
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Security notes

- The bot is private: all control commands/callbacks are restricted to `ADMIN_IDS` in private chat.
- Never commit `.env` or share the bot token. If it leaks, revoke it in BotFather (`/revoke`) and update `.env`.
- The userbot setup uses your account's API ID/hash and creates `data/userbot.session`. Treat both like passwords; they are ignored by Git. Revoke the session from Telegram → **Devices** if it is ever exposed.
- `/update` runs `git pull` as the user running the bot. Only use it with a repo you control.
- Use a dedicated bot, not one that also handles sensitive chats.

---

## FAQ

**Can GitHub Actions run the bot instead of Termux?**
No — a Telegram polling bot must stay online to receive your commands. GitHub Actions is used for CI (lint/tests) and to host the code. Run the live process on Termux, a VPS, Docker, or another always-on machine.

**What if the phone is off at a posting time?**
When it comes back online, the bot notices the slot is overdue and publishes one catch-up post per channel, then continues with the normal schedule.

**How many channels can I add?**
There is no hard limit in the bot. Telegram’s own rate limits apply; a 6/day schedule is far below them.

**Will videos be re-posted?**
Yes, the library cycles. The least-posted video is chosen first, and every video can be reused indefinitely. Delete old entries with `/videos` if you want them gone.

**Is my media uploaded to GitHub?**
No. GitHub only holds code. Videos stay in Telegram’s cloud; the SQLite database is local to the running bot.

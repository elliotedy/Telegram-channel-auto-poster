# Telegram Auto Poster — Project Handoff

## 1. What this project is

A self-hosted **Telegram channel auto-poster** that:

- Posts **photos / GIFs / videos** to one or more Telegram channels.
- Runs on a configurable schedule (default **6 posts/day**: `08:00, 11:00, 14:00, 17:00, 20:00, 23:00`).
- Is **fully controlled from Telegram** by an admin via bot commands and inline keyboards.
- Accepts media manually (send / forward to the bot) or by **watched folder import** (phone storage, PC folder, or Syncthing-synced copy).
- Uses Telegram `file_id` caching so posted media can be re-sent quickly without re-uploading.
- Stores state in a local SQLite database.

It was built as inspiration / sibling to the local-media-first workflow of:

`https://github.com/harry0703/MoneyPrinterTurbo/blob/main/README-en.md`

## 2. Current working directory

- **Current workspace path (as of handoff): `C:\xampp\htdocs\telegram autoposterbot`**
- It was previously being edited under:
  `C:\Users\ELITEBOOK 1040-G8\Downloads\telegram autoposterbot`
- **Not a git repository yet** (no `.git` folder / no commits).
- The project also contains an unrelated `realestate-php/` folder. Leave it alone unless the user explicitly asks about it.

## 3. Status overview

- The core implementation is present and substantial, but **the project has not yet been linted, compiled, installed, or test-run** in this state.
- **Nothing has been committed to GitHub yet.**
- Several Telegram handler / callback integrations for the new folder/media features are **not yet wired into `app/handlers.py`**.
- Several views may still contain outdated wording or incomplete logic.

### Implemented pieces (good)

- `main.py` — app bootstrap, logging, post-init, post-shutdown, polling.
- `app/config.py` — env-based config, media dirs, import settings, upload limit.
- `app/constants.py` — default schedule, caption template, presets, timezones, videos per page.
- `app/database.py` — SQLite via `aiosqlite`; settings/channels/videos/folders/posts/stats/rotation/failure handling.
- `app/importer.py` — folder scanning, media import metadata, scan summary, watcher loop, env folder seeding.
- `app/poster.py` — delivery (`file_id`, local file upload, media types, fallbacks) and scheduler tick / retry / error handling.
- `app/notify.py` — admin notifications.
- `app/utils.py` — schedule/caption/channel helpers.
- `app/views.py` — Telegram text + keyboards; partially updated for folders/media.
- `app/handlers.py` — commands/callbacks; partially updated; still missing newer integrations.

### Completed (verified)

- [x] **Wire new folder/media handlers into `app/handlers.py`**:
  - Added commands: `/folders`, `/addfolder`, `/removefolder`, `/scan`, `/addmedia` (+ updated `/addvideo`).
  - Added callbacks:
    - `folders`
    - `folder_add`
    - `folder_toggle:<id>`
    - `folder_del:<id>`
    - `folder_del_yes:<id>`
    - `scan`
    - `vtoggle:<id>:<page>`
    - `toggle_import`
    - `toggle_filename_caption`
  - Updated `BOT_COMMANDS` and admin command registry.
- [x] **Finish views**:
  - `help_view` — documents folder commands and media workflows.
  - `post_now_view` — uses active media count and media terminology.
  - Aligned `videos_view` keyboard callbacks (`vtoggle:{video_id}:{page}`, `vdel`, `vdel_yes`).
  - `add_media_help_view` / `add_video_help_view` alias.
- [x] **Add photo support in Telegram flow**:
  - `_extract_media` handles `message.photo` (and photo documents/extensions).
  - `on_media` message filter includes `filters.PHOTO`.
  - `_store_media` handles `photo` with appropriate messaging.
- [x] **Fixed known bugs**:
  - Removed duplicate `@admin_required` decorator on `on_callback`.
  - Normalized `RetryAfter` to handle numeric or `datetime.timedelta`.
  - Removed obsolete `FSInputFile` references (PTB native `Path`/file input used).
  - Fixed caption trimming binary search logic and regex link matching.
  - Handled `MediaSourceError` in manual post routing.
- [x] **Update tests and tools**:
  - Updated stats assertion in `tests/test_database.py`.
  - Added folder CRUD, video toggling, importer scanning, and handler/view tests.
  - Added `setup-windows.bat` and `start-windows.bat`.
  - Updated `README.md` and `.env.example`.
  - Ran `ruff check .` (0 errors), `compileall` (passed), `pytest` (36 passed).

## 4. Architecture / module map

- `main.py`
  - Builds `Application`, registers handlers, runs polling.
  - `post_init`: connect DB, seed settings, seed folders from env, schedule next slot, start scheduler + importer tasks, startup notify.
  - `post_shutdown`: cancel tasks, close DB.
- `app/config.py`
  - `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `TIMEZONE`, `DATABASE_PATH`, `LOG_LEVEL`, `ENABLE_SELF_UPDATE`, `NOTIFY_STARTUP`.
  - New: `MEDIA_DIRS`, `IMPORT_INTERVAL`, `IMPORT_MAX_PER_SCAN`, `IMPORT_RECURSIVE`, `IMPORT_FILENAME_CAPTION`, `MAX_UPLOAD_MB`.
- `app/constants.py`
  - Defaults/presets: schedule, caption template, 3x/4x/6x presets, timezone presets, videos/page size.
- `app/database.py`
  - Schema and methods for settings, channels, videos, folders, posts, stats, rotation, failures.
- `app/importer.py`
  - Folder scan/import/watcher, `ScanSummary`, `normalize_folder_path`, `scan_once`, `importer_loop`, `seed_folders_from_env`.
- `app/poster.py`
  - `MediaSourceError`, `MediaTooLargeError`, `MediaMissingError`, `send_media`, `Poster.deliver`, retry helper, scheduler tick/loop.
- `app/views.py`
  - Telegram text/keyboards.
- `app/handlers.py`
  - Commands + callback routing.
- `app/notify.py`
  - Admin notification sender.
- `app/utils.py`
  - Pure helpers.

## 5. Database schema summary

- `settings(key TEXT PK, value TEXT NOT NULL)`
- `channels(id INTEGER PK, chat_id INTEGER UNIQUE, title TEXT, username TEXT, active INTEGER, next_post_at TEXT, created_at TEXT)`
- `videos(...)`:
  - `id`, `file_id`, `file_unique_id UNIQUE`, `media_type`, `caption`, `duration`, `file_path`, `filename`, `file_size`, `file_mtime_ns`, `import_key UNIQUE`, `active`, `fail_count`, `posted_count`, `last_posted_at`, `created_at`
- `folders(id, path UNIQUE, enabled, last_scan_at, created_at)`
- `posts(id, video_id, channel_id, posted_at, trigger)`
- Indexes present.

### Settings keys used

- `schedule_times`
- `timezone`
- `caption_template`
- `paused`
- `notify_posts`
- `import_enabled`
- `import_filename_caption`

### Defaults

- Schedule: `08:00,11:00,14:00,17:00,20:00,23:00`
- Presets: `three`, `four`, `six`.
- Import interval: `60` seconds, max per scan: `25`, recursive: `True`, filename caption: `False`, max upload: `49.0 MB`.

## 6. Behaviour summary

- Scheduler runs every ~30s, checks due channels, picks next active media by rotation (least posted / oldest last post), sends to each due active channel, advances schedule.
- On source failure: records failure, advances channel schedule, may disable after repeated failures.
- On Telegram failure: notifies admins, advances schedule.
- Importer scans enabled folders on interval; imports supported media metadata; later posts upload local file; caches `file_id`; handles too-large files.
- Manual `/postnow` picks next active media and posts to one/all active channels without advancing scheduled rotation.

## 7. Deployment paths known

- Termux: `setup-termux.sh`, `run-termux.sh`, `install-boot.sh`.
- Windows: `setup-windows.bat`, `start-windows.bat`.
- Docker: `Dockerfile`, `docker-compose.yml`, `deploy/telegram-autoposter.service`, `Procfile`, `run.sh`.

## 8. Optional Telegram userbot setup — create the bot and channels

- `tools/create_telegram_channels.py` — one-time Telethon userbot script that can:
  1. Log in via a user account (interactive phone / code / 2FA flow).
  2. Create a new bot by talking to **@BotFather**.
  3. Save the new `TELEGRAM_BOT_TOKEN` and numeric `ADMIN_IDS` to `.env`.
  4. Create each channel from `CHANNELS` (title, username, about, photo).
  5. Add the autoposter bot as admin to existing channels in `ADD_BOT_TO`.
  6. Register those channels in `data/autoposter.db` if it exists, or print
     `/addchannel <chat_id>` commands.
- `requirements-userbot.txt` — optional dependencies (`telethon>=1.36`, `python-dotenv`).

Usage:

```bash
pip install -r requirements-userbot.txt
python tools/create_telegram_channels.py --create-bot
```

Required env vars: `TG_API_ID`, `TG_API_HASH`. Optional: `TG_PHONE`,
`BOT_NAME`, `TG_BOT_USERNAME`, `CREATE_BOT`, `WRITE_ENV`,
`CREATE_CHANNELS`, `REGISTER_DB`.

`TG_BOT_USERNAME` must be 5–32 chars and end in `bot`. If the desired
username is taken, the script retries with a random suffix.

The normal `python-telegram-bot` cannot create bots or channels; this userbot
script is the optional full-automation path. It requires the user's own
Telegram login and must be run locally — an agent cannot complete the
phone/code/2FA login.

## 9. Handoff checklist for the receiving agent

1. Read this file first.
2. Inspect current folder state; the project moved to `C:\xampp\htdocs\telegram autoposterbot`.
3. Install dependencies and run checks:
   ```bash
   python -m venv .venv
   .venv\Scripts\python -m pip install -U pip
   .venv\Scripts\python -m pip install -r requirements-dev.txt
   .venv\Scripts\python -m ruff check . --fix
   .venv\Scripts\python -m compileall -q app main.py
   .venv\Scripts\python -m pytest
   ```
   Or Linux/macOS equivalents.
4. Fix known bugs before relying on the bot.
5. Complete handler/view integration for new folder commands.
6. Update tests, README, `.env.example`, scripts, docs.
7. Run the bot only with a valid `.env` and real bot token.
8. Do not commit `.env`.

## 10. Important notes / warnings

- Bot must be admin in each target channel to post.
- Local folders are machine-local; a running Termux process cannot read PC-only paths directly. Use synced folder / run on PC.
- Telegram Bot API does **not** allow bots to create bots or channels. The userbot script automates @BotFather / channel creation through the user's own account.
- User account access: not available to the receiving agent / should not be requested. The user must run the userbot script locally and enter their own phone/code/2FA.
- Never commit `.env`, `TG_API_HASH`, or `data/userbot.session`.

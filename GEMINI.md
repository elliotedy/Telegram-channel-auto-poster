# Telegram Auto Poster Bot — Antigravity Agent Rules

> Always read this file first. It is the authoritative context for every task in this workspace.

---

## 1. Project Identity

**What it is:** A self-hosted Python Telegram bot that auto-posts photos/GIFs/videos to one or more Telegram channels on a configurable schedule (default: 6×/day). It is fully controlled via Telegram commands and inline keyboards.

**Runtime:** Python 3.12+, asyncio, `python-telegram-bot ≥21.6`, `aiosqlite`, `python-dotenv`, `tzdata`.

**Working directory:** `C:\xampp\htdocs\telegram autoposterbot`

---

## 2. Module Map (source of truth)

| File | Responsibility |
|---|---|
| `main.py` | Entry point: build `Application`, register handlers, run polling. `post_init` connects DB, seeds settings, seeds env folders, schedules next slot, starts scheduler + importer tasks, sends startup notification. `post_shutdown` cancels tasks, closes DB. |
| `app/config.py` | `Config` dataclass loaded from env via `Config.from_env()`. All env vars parsed here. |
| `app/constants.py` | `DEFAULT_SCHEDULE`, `DEFAULT_CAPTION_TEMPLATE`, `SCHEDULE_PRESETS`, `TIMEZONE_PRESETS`, `VIDEOS_PER_PAGE`. |
| `app/database.py` | SQLite via `aiosqlite`. All DB access goes through `Database` class. Never use raw SQL outside this module. |
| `app/importer.py` | Folder scanning (`scan_once`), watcher loop (`importer_loop`), `seed_folders_from_env`, `normalize_folder_path`, extension sets. |
| `app/poster.py` | `Poster.deliver()` sends media. `scheduler_loop` ticks every 30 s. Exception types: `MediaSourceError`, `MediaTooLargeError`, `MediaMissingError`. |
| `app/views.py` | Returns `(text: str, markup: InlineKeyboardMarkup | None)` tuples — pure text/keyboard builders, no I/O. |
| `app/handlers.py` | All `CommandHandler` and `CallbackQueryHandler` logic. Admin gate via `@admin_required`. |
| `app/notify.py` | `notify_admins(bot, config, html_text)` — sends HTML to all admin IDs. |
| `app/utils.py` | Pure helpers: schedule parsing, timezone, caption building, `utc_now()`, `to_iso()`, `safe_zoneinfo()`, etc. |

---

## 3. Database Schema

```sql
settings(key TEXT PK, value TEXT NOT NULL)
channels(id, chat_id UNIQUE, title, username, active, next_post_at TEXT ISO8601, created_at)
videos(id, file_id, file_unique_id UNIQUE, media_type, caption, duration, file_path, filename,
       file_size, file_mtime_ns, import_key UNIQUE, active, fail_count, posted_count,
       last_posted_at, created_at)
folders(id, path UNIQUE, enabled, last_scan_at, created_at)
posts(id, video_id, channel_id, posted_at, trigger)
```

**Settings keys:** `schedule_times`, `timezone`, `caption_template`, `paused`, `notify_posts`, `import_enabled`, `import_filename_caption`.

---

## 4. All Telegram Commands (registered in BOT_COMMANDS)

| Command | Handler | Purpose |
|---|---|---|
| `/start` / `/menu` | `cmd_start` / `cmd_menu` | Show dashboard |
| `/status` | `cmd_status` | Bot + posting status |
| `/channels` | `cmd_channels` | List channels |
| `/addchannel <target>` | `cmd_addchannel` | Add channel (username, ID, or t.me link) |
| `/removechannel <target>` | `cmd_removechannel` | Remove channel |
| `/addmedia` / `/addvideo` | `cmd_addvideo` | Add media (or reply to media) |
| `/videos` | `cmd_videos` | Browse library (paginated) |
| `/delvideo <id>` | `cmd_delvideo` | Delete media by ID |
| `/folders` | `cmd_folders` | List watched folders |
| `/addfolder <path>` | `cmd_addfolder` | Add watched folder + immediate scan |
| `/removefolder <id/path>` | `cmd_removefolder` | Remove folder |
| `/scan` | `cmd_scan` | Manual scan of enabled folders |
| `/schedule` | `cmd_schedule` | View schedule |
| `/settimes <HH:MM …>` | `cmd_settimes` | Set daily posting times |
| `/timezone <IANA>` | `cmd_timezone` | Set timezone |
| `/caption [template]` | `cmd_caption` | View/set caption template |
| `/postnow` | `cmd_postnow` | Post immediately |
| `/pause` / `/resume` | `cmd_pause` / `cmd_resume` | Pause/resume scheduler |
| `/backup` | `cmd_backup` | Send DB file |
| `/update` | `cmd_update` | `git pull` self-update |
| `/help` | `cmd_help` | Help text |
| `/id` | `cmd_id` | Show Telegram user/chat IDs |

---

## 5. All Callback Routing (in `on_callback`)

`menu`, `status`, `channels`, `ch_add`, `ch_toggle:<chat_id>`, `ch_del:<chat_id>`, `ch_del_yes:<chat_id>`,
`schedule`, `preset:<name>`, `tz_list`, `tz_set:<tz>`, `videos[:<page>]`, `vdel:<id>[:<page>]`,
`vdel_yes:<id>[:<page>]`, `vtoggle:<id>:<page>`, `postnow`, `postnow_all`, `postnow_ch:<chat_id>`,
`folders`, `folder_add`, `folder_toggle:<id>`, `folder_del:<id>`, `folder_del_yes:<id>`,
`scan`, `toggle_import`, `toggle_filename_caption`, `notify_posts`, `help`, `backup`.

---

## 6. Configuration / Env Vars

| Var | Required | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — |
| `ADMIN_IDS` | ✅ | — (comma-separated numeric IDs) |
| `TIMEZONE` | | `UTC` |
| `DATABASE_PATH` | | `data/autoposter.db` |
| `LOG_LEVEL` | | `INFO` |
| `ENABLE_SELF_UPDATE` | | `true` |
| `NOTIFY_STARTUP` | | `true` |
| `MEDIA_DIRS` | | — (semicolon/newline separated paths) |
| `IMPORT_INTERVAL` | | `60` s |
| `IMPORT_MAX_PER_SCAN` | | `25` |
| `IMPORT_RECURSIVE` | | `true` |
| `IMPORT_FILENAME_CAPTION` | | `false` |
| `MAX_UPLOAD_MB` | | `49.0` |

---

## 7. Architecture Invariants (never violate)

- **Admin gate:** Every command/callback handler MUST use `@admin_required` (or the `_is_admin` check for public-facing handlers like `cmd_start`/`cmd_id`).
- **Views are pure:** `app/views.py` functions must never call `await` or access the bot/DB directly — only accept data and return `(str, markup)`.
- **DB only via Database class:** Never write raw `aiosqlite` queries outside `app/database.py`.
- **`file_unique_id` is the dedup key** for videos — not `file_id` (which changes per bot session).
- **`next_post_at` is always UTC ISO-8601 string** in the DB; convert with `safe_zoneinfo` + `format_next_post` for display.
- **Scheduler tick is 30 s** — do not change without updating all timing assumptions.
- **Admin IDs are `frozenset[int]`** in `Config.admin_ids`; never store as strings.
- **`parse_mode="HTML"`** everywhere — escape user content with `html.escape()`. Never use Markdown parse mode.
- **Bot must be channel admin** with "Post Messages" permission — always remind the user of this in error messages.
- **`realestate-php/`** is an unrelated sibling project — ignore it unless explicitly asked.

---

## 8. Media Type Handling

| Telegram input | `media_type` stored | Sent via |
|---|---|---|
| `message.photo` | `"photo"` | `send_photo` |
| `message.video` | `"video"` | `send_video` |
| `message.animation` | `"animation"` | `send_animation` |
| `message.document` (video mime/ext) | `"document"` | `send_video` or upload |
| `message.document` (image mime/ext) | `"photo"` | `send_photo` |
| `message.document` (gif/webm) | `"animation"` | `send_animation` |

Supported extensions: `VIDEO_EXTENSIONS`, `IMAGE_EXTENSIONS`, `ANIMATION_EXTENSIONS` from `app/importer.py`.

---

## 9. Scheduler / Posting Logic

1. `scheduler_loop` runs every 30 s inside `asyncio.create_task`.
2. Fetches all active channels where `next_post_at ≤ utc_now()`.
3. Picks the **least-posted active video** (by `posted_count ASC`, `last_posted_at ASC NULLS FIRST`).
4. Calls `Poster.deliver(bot, channel, video, config)`.
5. On success: increments `posted_count`, logs post, advances `next_post_at` to next slot.
6. On `MediaSourceError` subclass: records failure (`fail_count++`), optionally disables video after threshold, advances schedule.
7. On Telegram API error: notifies admins, advances schedule.

---

## 10. Import / Folder Watcher Logic

- `importer_loop` runs every `IMPORT_INTERVAL` seconds.
- `scan_once` walks enabled folders; for each media file: computes `import_key = path:mtime_ns`, checks DB uniqueness, inserts metadata row (no upload yet).
- Actual file upload happens in `Poster.deliver` when `file_id` is `NULL` — file is read from disk and sent; returned `file_id` is then cached.
- Files larger than `MAX_UPLOAD_MB` are marked with `fail_count` and skipped.

---

## 11. Testing

```powershell
# From the project root with venv active:
.venv\Scripts\python -m pytest            # run all tests
.venv\Scripts\python -m ruff check .      # lint
.venv\Scripts\python -m compileall -q app main.py  # syntax check
```

Test files: `tests/test_database.py`, `tests/test_handlers_and_views.py`, `tests/test_importer.py`, `tests/test_utils.py`.

---

## 12. Deployment Paths

| Target | Script/File |
|---|---|
| Windows | `setup-windows.bat`, `start-windows.bat` |
| Termux/Android | `setup-termux.sh`, `run-termux.sh`, `install-boot.sh` |
| Docker | `Dockerfile`, `docker-compose.yml` |
| Linux service | `deploy/telegram-autoposter.service` |
| PaaS (Railway/Render) | `Procfile`, `run.sh` |

---

## 13. Known Limitations / Gotchas

- **GitHub Actions cannot run the bot** — it only runs lint/tests. The live bot must run on Termux/VPS/Docker.
- **No userbot / channel creation** — Telegram Bot API cannot create channels. Users must create them manually and add the bot as admin.
- **Local folders are machine-local** — Termux cannot read PC-only paths. Use Syncthing or run the bot on the same machine as the media.
- **Private invite links (`t.me/+…`)** are not supported for `/addchannel` — use post links or numeric IDs.
- **`/update` requires a `.git` folder** — not applicable if the project is not a git repo (it currently is not).

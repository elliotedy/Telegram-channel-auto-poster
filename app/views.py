"""Telegram message views (text + inline keyboards)."""

from __future__ import annotations

import html
from datetime import UTC
from math import ceil
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config
from .constants import (
    DEFAULT_CAPTION_TEMPLATE,
    DEFAULT_SCHEDULE,
    SCHEDULE_PRESETS,
    TIMEZONE_PRESETS,
    VIDEOS_PER_PAGE,
)
from .database import Database
from .utils import (
    format_duration,
    format_next_post,
    format_schedule,
    format_uptime,
    from_iso,
    parse_schedule,
    safe_zoneinfo,
    shorten,
    to_iso,
    utc_now,
)


def _back(callback: str = "menu", label: str = "⬅️ Back") -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=callback)


def _media_icon(media_type: str | None) -> str:
    return {
        "photo": "🖼",
        "animation": "🎞",
        "document": "📄",
    }.get(media_type or "video", "🎬")


async def _schedule_times(db: Database):
    raw = await db.get_setting("schedule_times") or DEFAULT_SCHEDULE
    try:
        return parse_schedule(raw)
    except ValueError:
        return parse_schedule(DEFAULT_SCHEDULE)


async def _today_start_utc(tz) -> str:
    local_now = utc_now().astimezone(tz)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_iso(start.astimezone(UTC))


async def _common_state(db: Database) -> dict:
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    times = await _schedule_times(db)
    paused = await db.get_bool("paused")
    stats = await db.stats(await _today_start_utc(tz))
    next_post = await db.next_post_overall()
    folders = await db.list_folders()
    return {
        "tz": tz,
        "times": times,
        "paused": paused,
        "stats": stats,
        "next_post": next_post,
        "folders": folders,
    }


async def dashboard_view(db: Database, uptime_seconds: float) -> tuple[str, InlineKeyboardMarkup]:
    state = await _common_state(db)
    tz, times, paused, stats = state["tz"], state["times"], state["paused"], state["stats"]
    status = "⏸ Paused" if paused else "🟢 Running"
    next_text = "— (paused)" if paused else format_next_post(state["next_post"], tz)
    media_line = f"Media in library: <b>{stats['active_videos']}</b> active"
    if stats["total_videos"] != stats["active_videos"]:
        media_line += f" / {stats['total_videos']} total"

    text = (
        "<b>🎬 Telegram Auto Poster</b>\n\n"
        f"Status: <b>{status}</b>\n"
        f"Channels: <b>{stats['active_channels']} active</b> of {stats['total_channels']}\n"
        f"{media_line}\n"
        f"Watched folders: <b>{len(state['folders'])}</b>\n"
        f"Posts today: <b>{stats['posts_today']}</b>\n"
        f"Uptime: {format_uptime(uptime_seconds)}\n"
        f"Timezone: <b>{html.escape(tz.key)}</b>\n"
        f"Schedule: <b>{len(times)}x/day</b> — {format_schedule(times)}\n"
        f"Next post: {next_text}\n\n"
        "Pick an action below, or use /help to see every command."
    )
    pause_button = InlineKeyboardButton(
        "▶️ Resume posting" if paused else "⏸ Pause posting",
        callback_data="toggle_pause",
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("📢 Channels", callback_data="channels"),
            ],
            [
                InlineKeyboardButton("🎬 Media library", callback_data="videos:0"),
                InlineKeyboardButton("📁 Folders", callback_data="folders"),
            ],
            [
                InlineKeyboardButton("🕐 Schedule", callback_data="schedule"),
                InlineKeyboardButton("🚀 Post now", callback_data="postnow"),
            ],
            [
                pause_button,
                InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            ],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
        ]
    )
    return text, keyboard


async def status_view(db: Database, uptime_seconds: float) -> tuple[str, InlineKeyboardMarkup]:
    state = await _common_state(db)
    tz, times, paused, stats = state["tz"], state["times"], state["paused"], state["stats"]
    status = "⏸ Paused" if paused else "🟢 Running"
    next_text = "— (paused)" if paused else format_next_post(state["next_post"], tz)

    text = (
        "<b>📊 Bot status</b>\n\n"
        f"Bot: <b>{status}</b>\n"
        f"Uptime: {format_uptime(uptime_seconds)}\n"
        f"Timezone: <b>{html.escape(tz.key)}</b>\n"
        f"Schedule: <b>{len(times)}x/day</b> — {format_schedule(times)}\n"
        f"Next post: {next_text}\n\n"
        f"Channels: <b>{stats['active_channels']}</b> active / {stats['total_channels']} total\n"
        f"Media: <b>{stats['active_videos']}</b> active / {stats['total_videos']} total\n"
        f"Watched folders: <b>{len(state['folders'])}</b>\n"
        f"Posts today: <b>{stats['posts_today']}</b>\n"
        f"Posts all-time: <b>{stats['total_posts']}</b>\n\n"
        "The scheduler checks for due posts every 30 seconds."
    )
    return text, InlineKeyboardMarkup([[_back()]])


async def channels_view(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    channels = await db.list_channels()
    tz = safe_zoneinfo(await db.get_setting("timezone"))

    if not channels:
        text = (
            "<b>📢 Channels</b>\n\n"
            "No channels yet.\n\n"
            "Add the bot as an administrator in a channel, then use "
            "<code>/addchannel @username</code> or the button below."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ How to add a channel", callback_data="ch_add")],
                [_back()],
            ]
        )
        return text, keyboard

    lines = ["<b>📢 Channels</b>", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for index, channel in enumerate(channels, start=1):
        active = bool(channel["active"])
        dot = "🟢" if active else "⏸"
        title = html.escape(channel["title"] or str(channel["chat_id"]))
        username = f" (@{html.escape(channel['username'])})" if channel["username"] else ""
        line = f"{index}. {dot} <b>{title}</b>{username}\n   ID: <code>{channel['chat_id']}</code>"
        if active:
            line += f"\n   Next post: {format_next_post(channel['next_post_at'], tz)}"
        else:
            line += "\n   Inactive — tap its button to turn it back on."
        lines.append(line)

        label = f"{dot} {shorten(channel['title'] or str(channel['chat_id']), 22)}"
        rows.append(
            [
                InlineKeyboardButton(label, callback_data=f"ch_toggle:{channel['chat_id']}"),
                InlineKeyboardButton("🗑", callback_data=f"ch_del:{channel['chat_id']}"),
            ]
        )

    lines.append("")
    lines.append("Tap a channel name to pause or resume it. 🗑 removes it from the bot.")
    rows.append([InlineKeyboardButton("➕ Add another channel", callback_data="ch_add")])
    rows.append([_back()])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def add_channel_help_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>➕ Add a channel</b>\n\n"
        "1. Open the channel → Administrators → Add Administrator → select this bot.\n"
        "2. Give it permission to <b>post messages</b>.\n"
        "3. Send one of these commands here:\n\n"
        "<code>/addchannel @mychannel</code>\n"
        "<code>/addchannel -1001234567890</code>\n\n"
        "<b>Private channel?</b> Copy the link of any post "
        "(<code>https://t.me/c/1234567890/5</code>) and send:\n"
        "<code>/addchannel https://t.me/c/1234567890/5</code>\n\n"
        "The bot checks its admin rights before saving the channel."
    )
    keyboard = InlineKeyboardMarkup(
        [[_back("channels", "⬅️ Back to channels")]]
    )
    return text, keyboard


async def channel_delete_view(channel) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>🗑 Remove channel?</b>\n\n"
        f"<b>{html.escape(channel['title'])}</b>\n"
        f"ID: <code>{channel['chat_id']}</code>\n\n"
        "This removes it from this bot only. The bot stays in the channel and you can add "
        "it again later. Existing post history is kept."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 Yes, remove",
                    callback_data=f"ch_del_yes:{channel['chat_id']}",
                )
            ],
            [_back("channels", "↩️ Cancel")],
        ]
    )
    return text, keyboard


def _last_scan_text(iso_value: str | None, tz) -> str:
    if not iso_value:
        return "never"
    return from_iso(iso_value).astimezone(tz).strftime("%d %b, %H:%M")


async def folders_view(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    folders = await db.list_folders()
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    scan_enabled = await db.get_bool("import_enabled", True)

    if not folders:
        text = (
            "<b>📁 Media folders</b>\n\n"
            "No folders configured yet.\n\n"
            "Add a folder on the machine running this bot and it will automatically "
            "import new images and videos:\n"
            "<code>/addfolder D:\\Videos</code>\n"
            "<code>/addfolder ~/storage/shared/DCIM/Camera</code>\n\n"
            "Files are read in place and never moved or deleted."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ How folder import works", callback_data="folder_add")],
                [_back()],
            ]
        )
        return text, keyboard

    lines = [
        "<b>📁 Media folders</b>",
        "",
        f"Auto-import is <b>{'ON' if scan_enabled else 'OFF'}</b>. "
        "New JPG/PNG photos, GIFs and video files are added to the posting rotation.",
        "Files are read in place — the bot never moves or deletes them.",
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for index, folder in enumerate(folders, start=1):
        active = bool(folder["enabled"])
        dot = "🟢" if active else "⏸"
        path = folder["path"]
        lines.append(
            f"{index}. {dot} <code>{html.escape(path)}</code>\n"
            f"   Last scan: {_last_scan_text(folder['last_scan_at'], tz)}"
        )
        label = f"{dot} {shorten(Path(path).name or path, 24)}"
        rows.append(
            [
                InlineKeyboardButton(label, callback_data=f"folder_toggle:{folder['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"folder_del:{folder['id']}"),
            ]
        )

    lines.append("")
    lines.append("Tap a folder to pause/resume it. Tap 🗑 to stop watching it.")
    rows.append(
        [
            InlineKeyboardButton("📥 Scan now", callback_data="scan"),
            InlineKeyboardButton("➕ Add folder", callback_data="folder_add"),
        ]
    )
    rows.append([_back()])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def folders_help_view(config: Config) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>📁 Folder import</b>\n\n"
        "The bot watches folders <b>on the machine where it runs</b> and imports new "
        "photos and videos automatically. Files are read in place; nothing is moved, "
        "copied or deleted.\n\n"
        "<b>Android / Termux</b>\n"
        "Grant storage access first:\n"
        "<code>termux-setup-storage</code>\n"
        "Then add a folder:\n"
        "<code>/addfolder ~/storage/shared/DCIM/Camera</code>\n"
        "<code>/addfolder /storage/emulated/0/Movies</code>\n\n"
        "<b>Windows / PC</b> (run the bot on the PC)\n"
        "<code>/addfolder D:\\Videos</code>\n"
        "<code>/addfolder C:\\Users\\Me\\Downloads\\clips</code>\n\n"
        "<b>Using a PC folder while the bot runs on the phone?</b>\n"
        "Sync the folder to the phone first (for example with Syncthing) and point "
        "the bot at the synced copy. A running process can only read its own machine.\n\n"
        f"Supported: JPG/JPEG/PNG, GIF, MP4/MOV/MKV/AVI/WEBM and similar. "
        f"Scan interval: {config.import_interval}s. "
        f"Local file upload limit: {config.max_upload_mb:g} MB per file.\n\n"
        "The first time a file is posted it is uploaded to Telegram; the resulting "
        "file ID is cached, so later posts are instant."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📁 Media folders", callback_data="folders")],
            [_back()],
        ]
    )
    return text, keyboard


async def folder_delete_view(folder) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>🗑 Stop watching this folder?</b>\n\n"
        f"<code>{html.escape(folder['path'])}</code>\n\n"
        "Media already imported from this folder stays in the library. "
        "The folder itself and its files are not touched."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 Yes, remove folder",
                    callback_data=f"folder_del_yes:{folder['id']}",
                )
            ],
            [_back("folders", "↩️ Cancel")],
        ]
    )
    return text, keyboard


def scan_result_view(text: str) -> tuple[str, InlineKeyboardMarkup]:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Scan again", callback_data="scan")],
            [_back("folders", "⬅️ Media folders")],
            [_back()],
        ]
    )
    return text, keyboard


async def videos_view(db: Database, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    total = await db.count_videos()
    active_total = await db.count_videos(active_only=True)
    pages = max(1, ceil(total / VIDEOS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    videos = await db.list_videos(VIDEOS_PER_PAGE, page * VIDEOS_PER_PAGE)

    if total == 0:
        text = (
            "<b>🎬 Media library</b>\n\n"
            "The library is empty.\n\n"
            "Send me a photo, GIF or video (or forward one) and it is added "
            "automatically. You can also point the bot at a folder with "
            "<code>/addfolder</code> and it will import new files for you.\n\n"
            "Media sent through Telegram is not downloaded — Telegram stores the "
            "file and the bot reuses its file ID, so posting is fast."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ How to add media", callback_data="add_video_help")],
                [InlineKeyboardButton("📁 Add a folder", callback_data="folder_add")],
                [_back()],
            ]
        )
        return text, keyboard

    lines = [
        f"<b>🎬 Media library</b> — {active_total} active / {total} total",
        "",
        "Send or forward photos, GIFs and videos, or let a watched folder import them. "
        "Everything rotates automatically: the least-posted item goes next.",
        "Tap a media item to pause/resume it. 🗑 deletes the library entry.",
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for video in videos:
        icon = _media_icon(video["media_type"])
        state = "" if video["active"] else "⏸ "
        bits = [f"{state}{icon} #{video['id']}"]
        if video["media_type"] in {"video", "animation"}:
            bits.append(format_duration(video["duration"]))
        bits.append(f"{video['posted_count']}x")
        detail = video["caption"] or video["filename"]
        if detail:
            bits.append(shorten(detail, 20))
        label = shorten(" · ".join(bits), 58)
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"vtoggle:{video['id']}:{page}",
                ),
                InlineKeyboardButton(
                    "🗑",
                    callback_data=f"vdel:{video['id']}:{page}",
                ),
            ]
        )

    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"videos:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"videos:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("➕ How to add media", callback_data="add_video_help")])
    rows.append([InlineKeyboardButton("📁 Media folders", callback_data="folders")])
    rows.append([_back()])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def add_media_help_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>➕ Add media</b>\n\n"
        "<b>Send it to this chat</b>\n"
        "Send or forward a photo, GIF or video. I add it to the library instantly — "
        "no download, no disk space used. You can send a video <b>as a file</b> if "
        "Telegram compresses it too much.\n\n"
        "<b>Import a folder</b>\n"
        "Point the bot at a folder on the machine where it runs:\n"
        "<code>/addfolder D:\\Videos</code>\n"
        "<code>/addfolder ~/storage/shared/DCIM/Camera</code>\n"
        "New JPG/PNG photos, GIFs and videos are imported automatically, and "
        "<code>/scan</code> imports them immediately.\n\n"
        "<b>Tip:</b> Captions on media you send are stored and used when it is posted. "
        "You can combine them with a template in /caption.\n\n"
        "Media is re-used in rotation: least-posted first, so everything gets airtime."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [_back("videos:0", "⬅️ Media library")],
            [InlineKeyboardButton("📁 Media folders", callback_data="folders")],
            [_back()],
        ]
    )
    return text, keyboard


add_video_help_view = add_media_help_view


async def video_delete_view(video, page: int) -> tuple[str, InlineKeyboardMarkup]:
    caption = shorten(video["caption"], 200) if video["caption"] else "—"
    kind = {
        "photo": "Photo",
        "animation": "GIF / animation",
        "document": "Document",
    }.get(video["media_type"], "Video")
    source = (
        f"Imported file: <code>{html.escape(video['filename'] or video['file_path'])}</code>\n"
        if video["file_path"]
        else "Source: Telegram file ID\n"
    )
    duration = (
        f"Duration: {format_duration(video['duration'])}\n"
        if video["media_type"] in {"video", "animation"}
        else ""
    )
    text = (
        f"<b>🗑 Remove media #{video['id']}?</b>\n\n"
        f"Type: {kind}\n"
        f"{duration}"
        f"{source}"
        f"Posted: {video['posted_count']} time{'s' if video['posted_count'] != 1 else ''}\n"
        f"Caption: {html.escape(caption)}\n\n"
        "This deletes only the library entry. The original Telegram file or local "
        "file stays untouched. This cannot be undone."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 Yes, delete",
                    callback_data=f"vdel_yes:{video['id']}:{page}",
                )
            ],
            [_back(f"videos:{page}", "↩️ Cancel")],
        ]
    )
    return text, keyboard


async def schedule_view(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    state = await _common_state(db)
    tz, times, paused = state["tz"], state["times"], state["paused"]
    next_text = "— (paused)" if paused else format_next_post(state["next_post"], tz)

    preset_buttons: list[InlineKeyboardButton] = []
    for key, (label, value) in SCHEDULE_PRESETS.items():
        parsed = parse_schedule(value)
        preset_buttons.append(
            InlineKeyboardButton(
                f"{label} ({len(parsed)})",
                callback_data=f"preset:{key}",
            )
        )

    text = (
        "<b>🕐 Posting schedule</b>\n\n"
        f"Timezone: <b>{html.escape(tz.key)}</b>\n"
        f"Times: <b>{format_schedule(times)}</b> ({len(times)}x/day)\n"
        f"Next post: {next_text}\n\n"
        "One media item is sent to every active channel at each slot.\n\n"
        "Use a preset below, or set custom times:\n"
        "<code>/settimes 08:00 11:00 14:00 17:00 20:00 23:00</code>\n\n"
        "Changing the schedule resets the next post time for all channels."
    )
    rows = [preset_buttons, [InlineKeyboardButton("🌍 Change timezone", callback_data="tz_menu")], [_back()]]
    return text, InlineKeyboardMarkup(rows)


async def timezone_view(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    current = safe_zoneinfo(await db.get_setting("timezone"))
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for zone in TIMEZONE_PRESETS:
        label = f"✅ {zone}" if zone == current.key else zone
        pair.append(InlineKeyboardButton(label, callback_data=f"tz_set:{zone}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_back("settings", "⬅️ Back to settings")])

    text = (
        "<b>🌍 Timezone</b>\n\n"
        f"Current: <b>{html.escape(current.key)}</b>\n\n"
        "All posting times use this timezone and follow daylight-saving changes.\n\n"
        "Pick a common zone below, or send a custom IANA name:\n"
        "<code>/timezone Europe/Paris</code>"
    )
    return text, InlineKeyboardMarkup(rows)


async def settings_view(db: Database, config: Config) -> tuple[str, InlineKeyboardMarkup]:
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    notify_posts = await db.get_bool("notify_posts")
    template = await db.get_setting("caption_template") or DEFAULT_CAPTION_TEMPLATE
    folders = await db.list_folders()
    import_enabled = await db.get_bool("import_enabled", True)
    filename_caption = await db.get_bool(
        "import_filename_caption", config.import_filename_caption
    )

    self_update = "Enabled" if config.enable_self_update else "Disabled"
    text = (
        "<b>⚙️ Settings</b>\n\n"
        f"🌍 Timezone: <b>{html.escape(tz.key)}</b>\n"
        f"🔔 Post notifications: <b>{'On' if notify_posts else 'Off'}</b>\n"
        f"🔄 GitHub self-update: <b>{self_update}</b>\n\n"
        "<b>📁 Folder import</b>\n"
        f"Watched folders: <b>{len(folders)}</b>\n"
        f"Auto-import: <b>{'On' if import_enabled else 'Off'}</b> "
        f"(scans every {config.import_interval}s)\n"
        f"Filename as caption: <b>{'On' if filename_caption else 'Off'}</b>\n"
        f"Local upload limit: <b>{config.max_upload_mb:g} MB</b>\n\n"
        "<b>📝 Caption template</b>\n"
        f"<code>{html.escape(template)}</code>\n\n"
        "Variables: <code>{caption}</code> <code>{channel}</code> <code>{date}</code>\n"
        "Change it with:\n"
        "<code>/caption {caption}\\n\\n@mychannel</code>\n"
        "Use <code>\\n</code> for a new line, or <code>/caption reset</code>."
    )
    toggle_label = "🔕 Notifications: Off" if notify_posts else "🔔 Notifications: On"
    import_label = "📥 Auto-import: Off" if import_enabled else "📥 Auto-import: On"
    filename_label = (
        "🏷 Filename captions: Off"
        if filename_caption
        else "🏷 Filename captions: On"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌍 Timezone", callback_data="tz_menu"),
                InlineKeyboardButton(toggle_label, callback_data="toggle_notify"),
            ],
            [
                InlineKeyboardButton(import_label, callback_data="toggle_import"),
                InlineKeyboardButton(filename_label, callback_data="toggle_filename_caption"),
            ],
            [InlineKeyboardButton("📁 Media folders", callback_data="folders")],
            [InlineKeyboardButton("♻️ Reset caption template", callback_data="caption_reset")],
            [_back()],
        ]
    )
    return text, keyboard


def termux_help_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>📱 Install on Termux (Android)</b>\n\n"
        "<b>1. Install apps</b>\n"
        "Install <b>Termux</b> and <b>Termux:Boot</b> from F-Droid "
        "(not the Play Store build).\n\n"
        "<b>2. Install packages</b>\n"
        "<code>pkg update -y &amp;&amp; pkg install -y python git</code>\n\n"
        "<b>3. Get the code</b>\n"
        "<code>git clone https://github.com/elliotedy/Telegram-channel-auto-poster.git</code>\n"
        "<code>cd Telegram-channel-auto-poster</code>\n\n"
        "<b>4. Install the bot</b>\n"
        "<code>bash setup-termux.sh</code>\n\n"
        "<b>5. Configure</b>\n"
        "<code>nano .env</code>\n"
        "Set <code>TELEGRAM_BOT_TOKEN</code> and <code>ADMIN_IDS</code>. "
        "Save with Ctrl+O, Enter, then Ctrl+X.\n\n"
        "<b>6. First test</b>\n"
        "<code>.venv/bin/python main.py</code>\n"
        "Send /start to your bot, then press Ctrl+C.\n\n"
        "<b>7. Run 24/7</b>\n"
        "<code>bash run-termux.sh</code>\n"
        "Or detach it:\n"
        "<code>nohup bash run-termux.sh &gt;/dev/null 2&gt;&amp;1 &amp;</code>\n\n"
        "<b>8. Auto-start after reboot</b>\n"
        "<code>bash install-boot.sh</code>\n"
        "Open Termux:Boot once and disable battery optimization for Termux.\n\n"
        "<b>9. Telegram videos folder</b>\n"
        "<code>termux-setup-storage</code>\n"
        "Then send:\n"
        "<code>/addfolder /storage/emulated/0/Android/media/org.telegram.messenger/Telegram/Telegram Video</code>\n\n"
        "Use /update from Telegram to pull new code later."
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📖 Full guide",
                    url="https://github.com/elliotedy/Telegram-channel-auto-poster#3-run-on-termux-android-247",
                )
            ],
            [_back("help", "⬅️ Commands")],
        ]
    )
    return text, markup


def help_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>ℹ️ Commands</b>\n\n"
        "<b>Channels</b>\n"
        "/channels — list, pause/resume or remove channels\n"
        "/addchannel @name or -100… — add a channel (bot must be admin)\n"
        "/removechannel @name — remove a channel\n\n"
        "<b>Media Library</b>\n"
        "/addmedia or /addvideo — instructions (or send/forward media here)\n"
        "/videos — browse the media library\n"
        "/delvideo &lt;id&gt; — delete a library entry\n\n"
        "<b>Watched Folders</b>\n"
        "/folders — view and manage watched local folders\n"
        "/addfolder &lt;path&gt; — watch a folder for photos, GIFs and videos\n"
        "/removefolder &lt;id or path&gt; — stop watching a folder\n"
        "/scan — scan watched folders now\n\n"
        "<b>Schedule</b>\n"
        "/schedule — view the schedule\n"
        "/settimes 08:00 11:00 14:00 17:00 20:00 23:00\n"
        "/timezone Asia/Kolkata\n\n"
        "<b>Posting</b>\n"
        "/postnow — post the next media item immediately\n"
        "/pause — stop scheduled posting\n"
        "/resume — continue scheduled posting\n\n"
        "<b>Setup</b>\n"
        "/termux — install and run the bot on Android with Termux\n\n"
        "<b>Maintenance</b>\n"
        "/status — uptime and stats\n"
        "/caption — view or change the caption template\n"
        "/backup — download the SQLite database\n"
        "/update — git pull the latest code from GitHub\n"
        "/id — show your Telegram ID\n\n"
        "<b>How it works</b>\n"
        "At every scheduled time, the least-posted media item is sent to every active channel. "
        "If the bot was offline, it catches up with one post when it comes back online."
    )
    return text, InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📱 Termux install guide", callback_data="termux")],
            [_back()],
        ]
    )


async def post_now_view(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    channels = await db.list_channels(active_only=True)
    media_count = await db.count_videos(active_only=True)

    if media_count == 0:
        text = (
            "<b>🚀 Post now</b>\n\n"
            "The media library has no active items. Send or forward media to add one first, "
            "or scan a watched folder."
        )
        return text, InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🎬 Media library", callback_data="videos:0")],
                [_back()],
            ]
        )

    if not channels:
        text = (
            "<b>🚀 Post now</b>\n\n"
            "There are no active channels. Add a channel or activate one first."
        )
        return text, InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📢 Channels", callback_data="channels")],
                [_back()],
            ]
        )

    text = (
        "<b>🚀 Post now</b>\n\n"
        "Choose a destination for the next media item in rotation. "
        "Manual posts do not change the schedule.\n\n"
        f"Active media available: <b>{media_count}</b>"
    )
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        label = f"📤 {shorten(channel['title'], 38)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"pnow:{channel['chat_id']}")]
        )
    if len(channels) > 1:
        rows.append(
            [InlineKeyboardButton("📢 All active channels", callback_data="pnow:all")]
        )
    rows.append([_back()])
    return text, InlineKeyboardMarkup(rows)


def post_results_view(lines: list[str]) -> tuple[str, InlineKeyboardMarkup]:
    text = "<b>🚀 Manual post results</b>\n\n" + "\n".join(lines)
    return text, InlineKeyboardMarkup([[_back()]])

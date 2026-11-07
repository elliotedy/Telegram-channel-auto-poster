"""All Telegram commands and callback handlers."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import views
from .config import ROOT_DIR, Config
from .constants import DEFAULT_CAPTION_TEMPLATE, DEFAULT_SCHEDULE, SCHEDULE_PRESETS
from .database import Database
from .importer import (
    ANIMATION_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    normalize_folder_path,
    scan_once,
)
from .notify import notify_admins
from .poster import MediaSourceError, Poster
from .utils import (
    channel_post_link,
    format_next_post,
    next_slot_utc,
    parse_channel_target,
    parse_schedule,
    safe_zoneinfo,
    shorten,
    to_iso,
    utc_now,
)

log = logging.getLogger(__name__)


BOT_COMMANDS = [
    BotCommand("start", "Open the control panel"),
    BotCommand("menu", "Show the control panel"),
    BotCommand("status", "Bot and posting status"),
    BotCommand("channels", "Manage channels"),
    BotCommand("addchannel", "Add a channel"),
    BotCommand("removechannel", "Remove a channel"),
    BotCommand("addmedia", "Add media to the library"),
    BotCommand("addvideo", "Add a video or photo to the library"),
    BotCommand("videos", "Browse the media library"),
    BotCommand("delvideo", "Delete a media entry by ID"),
    BotCommand("folders", "Manage watched folders"),
    BotCommand("addfolder", "Add a watched media folder"),
    BotCommand("removefolder", "Remove a watched folder"),
    BotCommand("scan", "Scan watched folders now"),
    BotCommand("schedule", "View posting schedule"),
    BotCommand("settimes", "Set daily posting times"),
    BotCommand("timezone", "Set the bot timezone"),
    BotCommand("caption", "Set the caption template"),
    BotCommand("postnow", "Post media immediately"),
    BotCommand("pause", "Pause scheduled posts"),
    BotCommand("resume", "Resume scheduled posts"),
    BotCommand("backup", "Download a database backup"),
    BotCommand("update", "Pull updates from GitHub"),
    BotCommand("help", "Show help"),
    BotCommand("id", "Show your Telegram ID"),
]


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["config"]


def _uptime(context: ContextTypes.DEFAULT_TYPE) -> float:
    started_at = context.bot_data.get("started_at")
    return time.monotonic() - started_at if started_at else 0.0


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    return bool(user and user.id in context.bot_data.get("admin_ids", frozenset()))


def admin_required(func):
    """Only allow private-chat commands/callbacks from configured admins."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is not None and chat.type != "private":
            return None
        if _is_admin(update, context):
            return await func(update, context)

        user = update.effective_user
        if update.callback_query is not None:
            await update.callback_query.answer("⛔ Not authorized.", show_alert=True)
        elif update.effective_message is not None:
            user_id = user.id if user else "unknown"
            await update.effective_message.reply_text(
                f"⛔ Not authorized.\nYour Telegram ID: <code>{user_id}</code>",
                parse_mode="HTML",
            )
        return None

    return wrapper


async def _reply(update: Update, text: str, reply_markup=None) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _safe_edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            log.warning("Could not edit message: %s", exc)


async def _reschedule_all(db: Database) -> str:
    times = parse_schedule(await db.get_setting("schedule_times") or DEFAULT_SCHEDULE)
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    next_at = to_iso(next_slot_utc(times, tz))
    await db.reschedule_all(next_at)
    return next_at


async def _next_slot_iso(db: Database) -> str:
    times = parse_schedule(await db.get_setting("schedule_times") or DEFAULT_SCHEDULE)
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    return to_iso(next_slot_utc(times, tz))


def _message_text_after_command(update: Update) -> str:
    message = update.effective_message
    if message is None or not message.text:
        return ""
    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _extract_media(message):
    if message.photo:
        return message.photo[-1], "photo"
    if message.video is not None:
        return message.video, "video"
    if message.animation is not None:
        return message.animation, "animation"
    if message.document is not None:
        document = message.document
        mime = (document.mime_type or "").lower()
        name = (document.file_name or "").lower()
        if mime.startswith("video/") or any(name.endswith(ext) for ext in VIDEO_EXTENSIONS):
            return document, "document"
        if mime.startswith("image/gif") or any(name.endswith(ext) for ext in ANIMATION_EXTENSIONS):
            return document, "animation"
        if mime.startswith("image/") or any(name.endswith(ext) for ext in IMAGE_EXTENSIONS):
            return document, "photo"
    return None, None


async def _store_media(update: Update, context: ContextTypes.DEFAULT_TYPE, message) -> bool:
    media, media_type = _extract_media(message)
    if media is None or media_type is None:
        await _reply(
            update,
            "⚠️ That file is not supported media. Send a photo, GIF, video, or media file "
            "(jpg, png, mp4, mov, mkv, webm…).",
        )
        return False

    db = _db(context)
    row, created = await db.add_video(
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        media_type=media_type,
        caption=message.caption,
        duration=getattr(media, "duration", None),
    )
    total = await db.count_videos()
    label = {
        "photo": "photo",
        "animation": "animation",
        "document": "document",
    }.get(media_type, "video")

    if not created:
        await _reply(
            update,
            f"ℹ️ That {label} is already in the library as <b>#{row['id']}</b> "
            f"(posted {row['posted_count']} times).\n\nLibrary size: <b>{total}</b>",
        )
        return False

    caption_note = ""
    if row["caption"]:
        caption_note = f"\nStored caption: <i>{html.escape(shorten(row['caption'], 80))}</i>"
    await _reply(
        update,
        f"✅ Added <b>{label} #{row['id']}</b> to the library.{caption_note}\n\n"
        f"Library size: <b>{total}</b>\n"
        "It will be posted automatically in rotation.",
    )
    return True


@admin_required
async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None and (
        message.photo or message.video or message.animation or message.document
    ):
        await _store_media(update, context, message)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    if not _is_admin(update, context):
        user = update.effective_user
        await _reply(
            update,
            "👋 This is a private auto-poster bot.\n\n"
            f"Your Telegram ID: <code>{user.id if user else 'unknown'}</code>\n"
            "Ask the owner to add this ID to <code>ADMIN_IDS</code>.",
        )
        return
    await show_dashboard(update, context)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.dashboard_view(_db(context), _uptime(context))
    if update.callback_query is not None:
        await _safe_edit(update.callback_query, text, markup)
    else:
        await _reply(update, text, markup)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    user = update.effective_user
    chat = update.effective_chat
    await _reply(
        update,
        f"Your user ID: <code>{user.id if user else 'unknown'}</code>\n"
        f"Chat ID: <code>{chat.id}</code>",
    )


@admin_required
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_dashboard(update, context)


@admin_required
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = views.help_view()
    await _reply(update, text, markup)


@admin_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.status_view(_db(context), _uptime(context))
    await _reply(update, text, markup)


@admin_required
async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.channels_view(_db(context))
    await _reply(update, text, markup)


@admin_required
async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _message_text_after_command(update)
    if not raw:
        text, markup = views.add_channel_help_view()
        await _reply(update, text, markup)
        return

    target = parse_channel_target(raw)
    if target is None:
        await _reply(
            update,
            "⚠️ I could not understand that channel reference.\n\n"
            "Use <code>/addchannel @username</code>, a numeric ID, or a post link "
            "(<code>https://t.me/c/1234567890/5</code>).",
        )
        return

    try:
        chat = await context.bot.get_chat(target)
    except TelegramError as exc:
        log.info("get_chat failed for %r: %s", target, exc)
        await _reply(
            update,
            "❌ Could not find that channel.\n\n"
            "• Check the username/ID/link.\n"
            "• Add me as an <b>administrator</b> in the channel first, then try again.\n"
            "• Private invite links (<code>t.me/+…</code>) are not supported — use "
            "<code>/addchannel https://t.me/c/1234567890/5</code> instead.",
        )
        return

    if chat.type != "channel":
        await _reply(update, "⚠️ That is not a channel. This bot posts to Telegram channels only.")
        return

    try:
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat.id, me.id)
    except TelegramError as exc:
        log.info("get_chat_member failed for %s: %s", chat.id, exc)
        await _reply(
            update,
            "❌ I cannot verify my admin rights in that channel.\n\n"
            "Add me as an administrator with permission to post messages, then try again.",
        )
        return

    if member.status not in {"administrator", "creator"}:
        await _reply(
            update,
            "❌ I am not an administrator in that channel.\n"
            "Open the channel → Administrators → Add Administrator → select me.",
        )
        return
    if member.status == "administrator" and getattr(member, "can_post_messages", None) is False:
        await _reply(
            update,
            "❌ I am an administrator but I do not have permission to post messages.\n"
            "Enable “Post messages” in my admin rights.",
        )
        return

    db = _db(context)
    title = chat.title or str(chat.id)
    next_at = await _next_slot_iso(db)
    row, created = await db.add_channel(
        chat_id=int(chat.id),
        title=title,
        username=chat.username,
        next_post_at=next_at,
    )
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    if created:
        await _reply(
            update,
            f"✅ Added channel <b>{html.escape(title)}</b>.\n"
            f"First scheduled post: {format_next_post(row['next_post_at'], tz)}\n\n"
            "Send me videos to build the library.",
        )
    else:
        await _reply(
            update,
            f"ℹ️ <b>{html.escape(title)}</b> is already added.\n"
            f"Status: {'active' if row['active'] else 'paused'}\n"
            f"Next post: {format_next_post(row['next_post_at'], tz)}",
        )


@admin_required
async def cmd_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _message_text_after_command(update)
    if not raw:
        await _reply(update, "Usage: <code>/removechannel @username</code> or the channel ID.")
        return

    db = _db(context)
    target = parse_channel_target(raw)
    if isinstance(target, int):
        channel = await db.get_channel(target)
    elif isinstance(target, str):
        channel = await db.find_channel(target)
    else:
        channel = await db.find_channel(raw)

    if channel is None:
        await _reply(update, "❌ That channel is not in my list. Use /channels to see them.")
        return

    await db.delete_channel(int(channel["chat_id"]))
    await _reply(update, f"🗑 Removed <b>{html.escape(channel['title'])}</b> from the bot.")


@admin_required
async def cmd_addvideo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None and message.reply_to_message is not None:
        await _store_media(update, context, message.reply_to_message)
        return
    text, markup = views.add_media_help_view()
    await _reply(update, text, markup)


cmd_addmedia = cmd_addvideo


@admin_required
async def cmd_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.videos_view(_db(context), 0)
    await _reply(update, text, markup)


@admin_required
async def cmd_delvideo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _message_text_after_command(update)
    if not raw.isdigit():
        await _reply(update, "Usage: <code>/delvideo 12</code> (use /videos to see IDs).")
        return
    db = _db(context)
    video = await db.get_video(int(raw))
    if video is None:
        await _reply(update, "❌ No media with that ID. Use /videos to see the library.")
        return
    await db.delete_video(int(raw))
    await _reply(update, f"🗑 Deleted <b>media #{raw}</b> from the library.")


@admin_required
async def cmd_folders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.folders_view(_db(context))
    await _reply(update, text, markup)


@admin_required
async def cmd_addfolder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_path = _message_text_after_command(update)
    if not raw_path:
        text, markup = views.folders_help_view(_config(context))
        await _reply(update, text, markup)
        return

    path = normalize_folder_path(raw_path)
    if not path.is_dir():
        await _reply(
            update,
            f"⚠️ That folder does not exist or is not a directory:\n"
            f"<code>{html.escape(str(path))}</code>\n\n"
            "Create the directory or check the path.",
        )
        return

    db = _db(context)
    row, created = await db.add_folder(str(path))
    if not created:
        await _reply(
            update,
            f"ℹ️ That folder is already being watched:\n"
            f"<code>{html.escape(str(path))}</code>",
        )
        return

    config = _config(context)
    await _reply(
        update,
        f"✅ Added folder:\n<code>{html.escape(str(path))}</code>\n\n"
        "Running initial scan now…",
    )
    summary = await scan_once(db, config)
    await _reply(
        update,
        summary.as_html(config.max_upload_mb, empty_message=True),
    )


@admin_required
async def cmd_removefolder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _message_text_after_command(update)
    db = _db(context)
    folders = await db.list_folders()
    if not target:
        if not folders:
            await _reply(update, "ℹ️ No folders are configured.")
            return
        lines = ["<b>📁 Watched folders:</b>", ""]
        for f in folders:
            lines.append(f"• ID <code>{f['id']}</code>: <code>{html.escape(f['path'])}</code>")
        lines.append("\nUse <code>/removefolder &lt;id or path&gt;</code> to stop watching a folder.")
        await _reply(update, "\n".join(lines))
        return

    target_id = _parse_int(target)
    matching = None
    if target_id is not None:
        matching = await db.get_folder(target_id)
    if matching is None:
        norm = str(normalize_folder_path(target))
        for f in folders:
            if f["path"] == target or f["path"] == norm:
                matching = f
                break

    if matching is None:
        await _reply(
            update,
            f"⚠️ Could not find a folder matching <code>{html.escape(target)}</code>.\n"
            "Use /folders to see all folders.",
        )
        return

    await db.delete_folder(int(matching["id"]))
    await _reply(
        update,
        f"🗑 Stopped watching folder:\n<code>{html.escape(matching['path'])}</code>\n\n"
        "Media already imported from it stays in the library.",
    )


@admin_required
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    config = _config(context)
    folders = await db.list_folders(enabled_only=True)
    if not folders:
        await _reply(
            update,
            "ℹ️ No active media folders to scan. Add one with /addfolder.",
        )
        return

    summary = await scan_once(db, config)
    await _reply(
        update,
        summary.as_html(config.max_upload_mb, empty_message=True),
    )


@admin_required
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.schedule_view(_db(context))
    await _reply(update, text, markup)


@admin_required
async def cmd_settimes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _message_text_after_command(update)
    if not raw:
        await _reply(
            update,
            "Usage: <code>/settimes 08:00 11:00 14:00 17:00 20:00 23:00</code>\n"
            "Use 24-hour HH:MM times, separated by spaces or commas.",
        )
        return
    try:
        times = parse_schedule(raw)
    except ValueError as exc:
        await _reply(update, f"⚠️ {html.escape(str(exc))}")
        return

    db = _db(context)
    await db.set_setting("schedule_times", ",".join(slot.strftime("%H:%M") for slot in times))
    await _reschedule_all(db)
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    next_at = await db.next_post_overall()
    await _reply(
        update,
        f"✅ Schedule updated: <b>{len(times)}x/day</b>\n"
        f"Times: <b>{', '.join(slot.strftime('%H:%M') for slot in times)}</b>\n"
        f"Timezone: <b>{html.escape(tz.key)}</b>\n"
        f"Next post: {format_next_post(next_at, tz)}",
    )


@admin_required
async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _message_text_after_command(update)
    db = _db(context)
    if not raw:
        text, markup = await views.timezone_view(db)
        await _reply(update, text, markup)
        return

    try:
        zone = ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        await _reply(
            update,
            "⚠️ Unknown timezone. Use an IANA name, for example "
            "<code>Asia/Kolkata</code>, <code>Europe/London</code> or "
            "<code>America/New_York</code>.",
        )
        return

    await db.set_setting("timezone", zone.key)
    await _reschedule_all(db)
    await _reply(update, f"✅ Timezone set to <b>{html.escape(zone.key)}</b>. All posting times now use it.")


@admin_required
async def cmd_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    template = await db.get_setting("caption_template") or DEFAULT_CAPTION_TEMPLATE
    raw = _message_text_after_command(update)

    if not raw:
        await _reply(
            update,
            "<b>📝 Caption template</b>\n\n"
            f"Current:\n<code>{html.escape(template)}</code>\n\n"
            "Variables: <code>{caption}</code> (caption stored with the video), "
            "<code>{channel}</code>, <code>{date}</code>.\n\n"
            "Example:\n<code>/caption {caption}\\n\\n@mychannel</code>\n\n"
            "Use <code>\\n</code> for a new line. Reset with <code>/caption reset</code>.",
        )
        return

    if raw.lower() in {"reset", "default"}:
        await db.set_setting("caption_template", DEFAULT_CAPTION_TEMPLATE)
        await _reply(update, "✅ Caption template reset to <code>{caption}</code>.")
        return

    template = raw.replace("\\n", "\n")
    if len(template) > 500:
        await _reply(update, "⚠️ The template is too long. Keep it under 500 characters.")
        return

    await db.set_setting("caption_template", template)
    await _reply(
        update,
        "✅ Caption template updated.\n\n"
        f"<code>{html.escape(template)}</code>\n\n"
        "It will be used for the next post.",
    )


@admin_required
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _db(context).set_bool("paused", True)
    text, markup = await views.dashboard_view(_db(context), _uptime(context))
    await _reply(update, "⏸ <b>Posting paused.</b> No scheduled posts will be sent until you resume.\n\n" + text, markup)


@admin_required
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await db.set_bool("paused", False)
    await _reschedule_all(db)
    text, markup = await views.dashboard_view(db, _uptime(context))
    await _reply(update, "▶️ <b>Posting resumed.</b>\n\n" + text, markup)


@admin_required
async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await views.post_now_view(_db(context))
    await _reply(update, text, markup)


@admin_required
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    await db.checkpoint()
    if not db.path.exists():
        await _reply(update, "⚠️ Database file not found.")
        return
    filename = f"autoposter-backup-{utc_now():%Y%m%d-%H%M}.db"
    await update.effective_message.reply_document(
        document=db.path,
        filename=filename,
        caption="💾 Auto poster database backup",
    )


@admin_required
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = _config(context)
    if not config.enable_self_update:
        await _reply(update, "ℹ️ Self-update is disabled. Set <code>ENABLE_SELF_UPDATE=true</code> in .env.")
        return
    if not (ROOT_DIR / ".git").exists():
        await _reply(
            update,
            "⚠️ This copy is not a git repository, so /update cannot run. "
            "Clone the project from GitHub or update it manually.",
        )
        return

    status_message = await update.effective_message.reply_text("🔄 Pulling the latest code from GitHub…")
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(ROOT_DIR),
            "pull",
            "--ff-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=120)
        except TimeoutError:
            process.kill()
            await process.wait()
            await status_message.edit_text("⌛ git pull timed out. Check the connection and try again.")
            return
    except FileNotFoundError:
        await status_message.edit_text("❌ git is not installed on this machine.")
        return
    except Exception as exc:
        log.exception("git pull failed")
        await status_message.edit_text(f"❌ Update failed: {html.escape(shorten(str(exc), 300))}")
        return

    output = output_bytes.decode(errors="replace").strip()
    if process.returncode != 0:
        await status_message.edit_text(
            "❌ <b>git pull failed</b>\n\n"
            f"<code>{html.escape(shorten(output, 1200))}</code>",
            parse_mode="HTML",
        )
        return

    if "already up" in output.lower():
        await status_message.edit_text(
            "✅ Already up to date.\n\n"
            f"<code>{html.escape(shorten(output, 600))}</code>",
            parse_mode="HTML",
        )
        return

    await status_message.edit_text(
        "✅ Updated from GitHub. Restarting now…\n\n"
        "<i>If requirements.txt changed, run setup-termux.sh again afterwards.</i>",
        parse_mode="HTML",
    )
    log.info("Self-update applied, restarting process")
    await asyncio.sleep(2)
    os._exit(0)


@admin_required
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    try:
        await query.answer()
    except TelegramError:
        pass

    data = query.data or ""
    db = _db(context)
    config = _config(context)

    try:
        if data == "menu":
            await show_dashboard(update, context)
        elif data == "status":
            text, markup = await views.status_view(db, _uptime(context))
            await _safe_edit(query, text, markup)
        elif data == "channels":
            text, markup = await views.channels_view(db)
            await _safe_edit(query, text, markup)
        elif data == "ch_add":
            text, markup = views.add_channel_help_view()
            await _safe_edit(query, text, markup)
        elif data.startswith("ch_toggle:"):
            chat_id = _parse_int(data.split(":", 1)[1])
            channel = await db.get_channel(chat_id) if chat_id is not None else None
            if channel is not None:
                make_active = not bool(channel["active"])
                if make_active:
                    await db.set_channel_next(chat_id, await _next_slot_iso(db))
                await db.set_channel_active(chat_id, make_active)
            text, markup = await views.channels_view(db)
            await _safe_edit(query, text, markup)
        elif data.startswith("ch_del:"):
            chat_id = _parse_int(data.split(":", 1)[1])
            channel = await db.get_channel(chat_id) if chat_id is not None else None
            if channel is None:
                text, markup = await views.channels_view(db)
            else:
                text, markup = await views.channel_delete_view(channel)
            await _safe_edit(query, text, markup)
        elif data.startswith("ch_del_yes:"):
            chat_id = _parse_int(data.split(":", 1)[1])
            if chat_id is not None:
                await db.delete_channel(chat_id)
            text, markup = await views.channels_view(db)
            await _safe_edit(query, text, markup)
        elif data == "folders":
            text, markup = await views.folders_view(db)
            await _safe_edit(query, text, markup)
        elif data == "folder_add":
            text, markup = views.folders_help_view(config)
            await _safe_edit(query, text, markup)
        elif data.startswith("folder_toggle:"):
            folder_id = _parse_int(data.split(":", 1)[1])
            folder = await db.get_folder(folder_id) if folder_id is not None else None
            if folder is not None:
                await db.set_folder_enabled(folder_id, not bool(folder["enabled"]))
            text, markup = await views.folders_view(db)
            await _safe_edit(query, text, markup)
        elif data.startswith("folder_del:"):
            folder_id = _parse_int(data.split(":", 1)[1])
            folder = await db.get_folder(folder_id) if folder_id is not None else None
            if folder is None:
                text, markup = await views.folders_view(db)
            else:
                text, markup = await views.folder_delete_view(folder)
            await _safe_edit(query, text, markup)
        elif data.startswith("folder_del_yes:"):
            folder_id = _parse_int(data.split(":", 1)[1])
            if folder_id is not None:
                await db.delete_folder(folder_id)
            text, markup = await views.folders_view(db)
            await _safe_edit(query, text, markup)
        elif data == "scan":
            await _safe_edit(query, "⏳ Scanning folders for new media…")
            summary = await scan_once(db, config)
            text, markup = views.scan_result_view(
                summary.as_html(config.max_upload_mb, empty_message=True)
            )
            await _safe_edit(query, text, markup)
        elif data.startswith("videos:"):
            page = _parse_int(data.split(":", 1)[1]) or 0
            text, markup = await views.videos_view(db, page)
            await _safe_edit(query, text, markup)
        elif data.startswith("vtoggle:"):
            parts = data.split(":")
            video_id = _parse_int(parts[1]) if len(parts) > 1 else None
            page = _parse_int(parts[2]) if len(parts) > 2 else 0
            if video_id is not None:
                video = await db.get_video(video_id)
                if video is not None:
                    await db.set_video_active(video_id, not bool(video["active"]))
            text, markup = await views.videos_view(db, page)
            await _safe_edit(query, text, markup)
        elif data.startswith("vdel:"):
            parts = data.split(":")
            video_id = _parse_int(parts[1]) if len(parts) > 1 else None
            page = _parse_int(parts[2]) if len(parts) > 2 else 0
            video = await db.get_video(video_id) if video_id is not None else None
            if video is None:
                text, markup = await views.videos_view(db, page)
            else:
                text, markup = await views.video_delete_view(video, page)
            await _safe_edit(query, text, markup)
        elif data.startswith("vdel_yes:"):
            parts = data.split(":")
            video_id = _parse_int(parts[1]) if len(parts) > 1 else None
            page = _parse_int(parts[2]) if len(parts) > 2 else 0
            if video_id is not None:
                await db.delete_video(video_id)
            text, markup = await views.videos_view(db, page)
            await _safe_edit(query, text, markup)
        elif data == "add_video_help":
            text, markup = views.add_media_help_view()
            await _safe_edit(query, text, markup)
        elif data == "schedule":
            text, markup = await views.schedule_view(db)
            await _safe_edit(query, text, markup)
        elif data.startswith("preset:"):
            preset = SCHEDULE_PRESETS.get(data.split(":", 1)[1])
            if preset is not None:
                await db.set_setting("schedule_times", preset[1])
                await _reschedule_all(db)
            text, markup = await views.schedule_view(db)
            await _safe_edit(query, text, markup)
        elif data == "tz_menu":
            text, markup = await views.timezone_view(db)
            await _safe_edit(query, text, markup)
        elif data.startswith("tz_set:"):
            zone_name = data.split(":", 1)[1]
            try:
                zone = ZoneInfo(zone_name)
            except (ZoneInfoNotFoundError, ValueError):
                zone = None
            if zone is not None:
                await db.set_setting("timezone", zone.key)
                await _reschedule_all(db)
            text, markup = await views.settings_view(db, config)
            await _safe_edit(query, text, markup)
        elif data == "settings":
            text, markup = await views.settings_view(db, config)
            await _safe_edit(query, text, markup)
        elif data == "toggle_notify":
            current = await db.get_bool("notify_posts")
            await db.set_bool("notify_posts", not current)
            text, markup = await views.settings_view(db, config)
            await _safe_edit(query, text, markup)
        elif data == "toggle_import":
            current = await db.get_bool("import_enabled", True)
            await db.set_bool("import_enabled", not current)
            text, markup = await views.settings_view(db, config)
            await _safe_edit(query, text, markup)
        elif data == "toggle_filename_caption":
            current = await db.get_bool(
                "import_filename_caption", config.import_filename_caption
            )
            await db.set_bool("import_filename_caption", not current)
            text, markup = await views.settings_view(db, config)
            await _safe_edit(query, text, markup)
        elif data == "caption_reset":
            await db.set_setting("caption_template", DEFAULT_CAPTION_TEMPLATE)
            text, markup = await views.settings_view(db, config)
            await _safe_edit(query, text, markup)
        elif data == "postnow":
            text, markup = await views.post_now_view(db)
            await _safe_edit(query, text, markup)
        elif data.startswith("pnow:"):
            await _handle_manual_post(query, context, data.split(":", 1)[1])
        elif data == "toggle_pause":
            paused = await db.get_bool("paused")
            await db.set_bool("paused", not paused)
            if paused:
                await _reschedule_all(db)
            text, markup = await views.dashboard_view(db, _uptime(context))
            await _safe_edit(query, text, markup)
        elif data == "help":
            text, markup = views.help_view()
            await _safe_edit(query, text, markup)
        elif data == "noop":
            pass
        else:
            await show_dashboard(update, context)
    except Exception:
        log.exception("Callback handler failed for %r", data)
        await _safe_edit(
            query,
            "⚠️ Something went wrong. Check <code>logs/bot.log</code>.",
        )


async def _handle_manual_post(query, context: ContextTypes.DEFAULT_TYPE, target: str) -> None:
    db = _db(context)
    video = await db.next_video()
    if video is None:
        await _safe_edit(
            query,
            "⚠️ The media library is empty. Send or forward media first.",
            InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🎬 Media library", callback_data="videos:0")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="menu")],
                ]
            ),
        )
        return

    if target == "all":
        channels = await db.list_channels(active_only=True)
    else:
        chat_id = _parse_int(target)
        channel = await db.get_channel(chat_id) if chat_id is not None else None
        channels = [channel] if channel is not None and channel["active"] else []

    if not channels:
        await _safe_edit(query, "⚠️ That channel is no longer active.")
        return

    await _safe_edit(query, f"📤 Posting media <b>#{video['id']}</b>…")
    poster = Poster(context.bot, db, _config(context))
    lines: list[str] = []
    for channel in channels:
        try:
            message = await poster.deliver(
                channel,
                video,
                trigger="manual",
                advance_schedule=False,
            )
            link = channel_post_link(
                int(channel["chat_id"]),
                channel["username"],
                int(message.message_id),
            )
            line = f"✅ <b>{html.escape(channel['title'])}</b>"
            if link:
                line += f' — <a href="{link}">view post</a>'
            lines.append(line)
        except MediaSourceError as exc:
            failures, still_active = await db.record_video_failure(int(video["id"]))
            lines.append(
                f"❌ <b>{html.escape(channel['title'])}</b> — Media error: {html.escape(shorten(str(exc), 200))}"
            )
        except TelegramError as exc:
            lines.append(
                f"❌ <b>{html.escape(channel['title'])}</b> — {html.escape(shorten(str(exc), 200))}"
            )
        except Exception as exc:
            log.exception("Manual post failed for %s", channel["title"])
            lines.append(
                f"❌ <b>{html.escape(channel['title'])}</b> — {html.escape(shorten(str(exc), 200))}"
            )

    text, markup = views.post_results_view(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n…"
    await _safe_edit(query, text, markup)


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def on_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    if not _is_admin(update, context):
        return
    await _reply(update, "🤔 Unknown command. Use /help to see what I can do.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception while processing an update", exc_info=context.error)
    if context.error is None or isinstance(context.error, TelegramError):
        return
    config: Config | None = context.bot_data.get("config")
    if config is None:
        return
    now = time.monotonic()
    last = context.bot_data.get("_last_error_notice", 0.0)
    if now - last < 600:
        return
    context.bot_data["_last_error_notice"] = now
    await notify_admins(
        context.bot,
        config,
        f"⚠️ <b>Unexpected bot error</b>\n<code>{html.escape(shorten(str(context.error), 600))}</code>",
    )


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))

    admin_commands = {
        "menu": cmd_menu,
        "help": cmd_help,
        "status": cmd_status,
        "channels": cmd_channels,
        "addchannel": cmd_addchannel,
        "removechannel": cmd_removechannel,
        "addmedia": cmd_addmedia,
        "addvideo": cmd_addvideo,
        "videos": cmd_videos,
        "delvideo": cmd_delvideo,
        "folders": cmd_folders,
        "addfolder": cmd_addfolder,
        "removefolder": cmd_removefolder,
        "scan": cmd_scan,
        "schedule": cmd_schedule,
        "settimes": cmd_settimes,
        "timezone": cmd_timezone,
        "caption": cmd_caption,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "postnow": cmd_postnow,
        "backup": cmd_backup,
        "update": cmd_update,
    }
    for command, callback in admin_commands.items():
        app.add_handler(CommandHandler(command, callback))

    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL,
            on_media,
        )
    )
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.COMMAND, on_unknown_command))
    app.add_error_handler(on_error)

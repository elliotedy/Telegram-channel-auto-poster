"""Media delivery and the background scheduler."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Bot, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import Application

from .config import Config
from .database import Database
from .notify import notify_admins
from .utils import (
    build_caption,
    channel_post_link,
    next_slot_utc,
    parse_schedule,
    safe_zoneinfo,
    shorten,
    to_iso,
    utc_now,
)

log = logging.getLogger(__name__)

SCHEDULER_INTERVAL_SECONDS = 30
EMPTY_LIBRARY_NOTICE_INTERVAL = 12 * 60 * 60
ERROR_NOTICE_INTERVAL = 3 * 60 * 60

_last_empty_notice = 0.0
_error_notice_at: dict[int, float] = {}


class MediaSourceError(Exception):
    """A local media file cannot be used for posting."""


class MediaTooLargeError(MediaSourceError):
    pass


class MediaMissingError(MediaSourceError):
    pass


def _should_notify_error(chat_id: int) -> bool:
    now = time.monotonic()
    last = _error_notice_at.get(chat_id, 0.0)
    if now - last >= ERROR_NOTICE_INTERVAL:
        _error_notice_at[chat_id] = now
        return True
    return False


async def _schedule_context(db: Database) -> tuple[list, ZoneInfo, str, str | None]:
    times = parse_schedule(await db.get_setting("schedule_times"))
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    template = await db.get_setting("caption_template") or "{caption}"
    paused = await db.get_setting("paused")
    return times, tz, template, paused


def _caption_kwargs(caption: str) -> dict:
    if not caption:
        return {}
    return {"caption": caption, "parse_mode": ParseMode.HTML}


def _local_file(video, max_upload_mb: float) -> Path:
    raw_path = video["file_path"]
    if not raw_path:
        raise MediaSourceError("The library entry has neither a Telegram file ID nor a local path.")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise MediaMissingError(f"File not found: {path}")
    size = path.stat().st_size
    limit = int(max_upload_mb * 1024 * 1024)
    if size > limit:
        raise MediaTooLargeError(
            f"{path.name} is {size / 1024 / 1024:.1f} MB; the bot upload limit is "
            f"{max_upload_mb:g} MB."
        )
    return path


async def send_media(
    bot: Bot,
    chat_id: int,
    video,
    caption: str,
    max_upload_mb: float,
) -> Message:
    kwargs: dict = {"chat_id": chat_id}
    kwargs.update(_caption_kwargs(caption))

    file_id = video["file_id"]
    media_type = video["media_type"]

    if file_id:
        if media_type == "photo":
            return await bot.send_photo(photo=file_id, **kwargs)
        if media_type == "animation":
            return await bot.send_animation(animation=file_id, **kwargs)
        if media_type == "document":
            return await bot.send_document(document=file_id, **kwargs)
        return await bot.send_video(video=file_id, **kwargs)

    path = _local_file(video, max_upload_mb)

    if media_type == "photo":
        try:
            return await bot.send_photo(photo=path, **kwargs)
        except BadRequest:
            log.warning("Photo upload failed for %s; sending it as a document", path)
            return await bot.send_document(document=path, **kwargs)
    if media_type == "animation":
        try:
            return await bot.send_animation(animation=path, **kwargs)
        except BadRequest:
            log.warning("Animation upload failed for %s; sending it as a document", path)
            return await bot.send_document(document=path, **kwargs)
    if media_type == "document":
        return await bot.send_document(document=path, **kwargs)

    try:
        return await bot.send_video(
            video=path,
            supports_streaming=True,
            **kwargs,
        )
    except BadRequest:
        log.warning("Video upload failed for %s; sending it as a document", path)
        return await bot.send_document(document=path, **kwargs)


def extract_sent_media(message: Message):
    """Return (file_id, file_unique_id, media_type, duration) from a sent message."""
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, photo.file_unique_id, "photo", None
    if message.video:
        return (
            message.video.file_id,
            message.video.file_unique_id,
            "video",
            message.video.duration,
        )
    if message.animation:
        return (
            message.animation.file_id,
            message.animation.file_unique_id,
            "animation",
            message.animation.duration,
        )
    if message.document:
        return (
            message.document.file_id,
            message.document.file_unique_id,
            "document",
            None,
        )
    return None, None, None, None


class Poster:
    def __init__(self, bot: Bot, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config

    async def deliver(
        self,
        channel,
        video,
        trigger: str,
        advance_schedule: bool = False,
        now: datetime | None = None,
    ) -> Message:
        """Send one library item to one channel and record the result."""
        moment = now or utc_now()
        media = video if isinstance(video, dict) else dict(video)
        tz = safe_zoneinfo(await self.db.get_setting("timezone"))
        template = await self.db.get_setting("caption_template") or "{caption}"

        next_post_at: str | None = None
        if advance_schedule:
            times = parse_schedule(await self.db.get_setting("schedule_times"))
            next_post_at = to_iso(next_slot_utc(times, tz, moment))

        caption = build_caption(
            template,
            media["caption"],
            channel["title"],
            moment.astimezone(tz),
        )
        message = await send_media(
            self.bot,
            int(channel["chat_id"]),
            media,
            caption,
            self.config.max_upload_mb,
        )

        if not media.get("file_id"):
            file_id, unique_id, sent_type, duration = extract_sent_media(message)
            if file_id and sent_type:
                try:
                    await self.db.set_video_telegram_file(
                        int(media["id"]),
                        file_id,
                        unique_id,
                        sent_type,
                        duration,
                    )
                    media["file_id"] = file_id
                    media["media_type"] = sent_type
                    log.info("Cached Telegram file_id for media #%s", media["id"])
                except Exception:
                    log.exception("Could not cache file_id for media #%s", media["id"])

        await self.db.record_post(
            video_id=int(media["id"]),
            channel_id=int(channel["id"]),
            posted_at=to_iso(moment),
            trigger=trigger,
            next_post_at=next_post_at,
        )
        return message


async def _advance_channel(db: Database, chat_id: int) -> None:
    try:
        times, tz, _, _ = await _schedule_context(db)
        await db.set_channel_next(chat_id, to_iso(next_slot_utc(times, tz)))
    except Exception:
        log.exception("Could not advance the schedule for channel %s", chat_id)


async def _deliver_with_retries(
    poster: Poster,
    channel,
    video,
    *,
    advance_schedule: bool,
    now: datetime,
) -> Message:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return await poster.deliver(
                channel,
                video,
                trigger="schedule",
                advance_schedule=advance_schedule,
                now=now,
            )
        except RetryAfter as exc:
            retry_after = getattr(exc, "retry_after", 5)
            if hasattr(retry_after, "total_seconds"):
                raw_wait = retry_after.total_seconds()
            else:
                try:
                    raw_wait = float(retry_after) if retry_after is not None else 5.0
                except (TypeError, ValueError):
                    raw_wait = 5.0
            wait = min(max(raw_wait, 1.0), 120.0)
            log.warning(
                "Telegram asked to wait %ss before posting to %s",
                wait,
                channel["chat_id"],
            )
            await asyncio.sleep(wait)
            last_error = exc
        except NetworkError as exc:
            last_error = exc
            log.warning(
                "Network error posting to %s (attempt %s/3): %s",
                channel["chat_id"],
                attempt + 1,
                exc,
            )
            await asyncio.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _posted_notice(channel, video, message: Message) -> str:
    lines = [
        f"✅ Posted media <b>#{video['id']}</b> to <b>{html.escape(channel['title'])}</b>.",
    ]
    link = channel_post_link(
        int(channel["chat_id"]),
        channel["username"],
        int(message.message_id),
    )
    if link:
        lines.append(f'<a href="{link}">Open post</a>')
    return "\n".join(lines)


async def run_scheduler_tick(app: Application, db: Database, config: Config) -> None:
    global _last_empty_notice

    if await db.get_bool("paused"):
        return

    now = utc_now()
    due_channels = await db.due_channels(to_iso(now))
    if not due_channels:
        return

    video_row = await db.next_video()
    if video_row is None:
        if time.monotonic() - _last_empty_notice >= EMPTY_LIBRARY_NOTICE_INTERVAL:
            _last_empty_notice = time.monotonic()
            await notify_admins(
                app.bot,
                config,
                "⚠️ A scheduled post was skipped because the media library is empty.\n"
                "Send media to the bot or add a folder, then run /scan.",
            )
        return

    video = dict(video_row)
    poster = Poster(app.bot, db, config)
    notify_posts = await db.get_bool("notify_posts")

    for channel in due_channels:
        chat_id = int(channel["chat_id"])
        try:
            message = await _deliver_with_retries(
                poster,
                channel,
                video,
                advance_schedule=True,
                now=now,
            )
            log.info(
                "Posted media #%s to %s (%s)",
                video["id"],
                channel["title"],
                chat_id,
            )
            if notify_posts:
                await notify_admins(app.bot, config, _posted_notice(channel, video, message))
        except MediaSourceError as exc:
            failures, still_active = await db.record_video_failure(int(video["id"]))
            log.error(
                "Media #%s cannot be posted to %s: %s",
                video["id"],
                channel["title"],
                exc,
            )
            await _advance_channel(db, chat_id)
            if not still_active or _should_notify_error(chat_id):
                file_label = video["filename"] or video["file_path"] or "unknown file"
                if still_active:
                    hint = (
                        f"It will be retried; after 3 failed attempts it is disabled "
                        f"in /videos. Failed attempts so far: {failures}."
                    )
                else:
                    hint = (
                        "It has been disabled in /videos. Fix the file, then tap the "
                        "video's button to re-enable it."
                    )
                await notify_admins(
                    app.bot,
                    config,
                    f"⚠️ Could not post media <b>#{video['id']}</b> to "
                    f"<b>{html.escape(channel['title'])}</b>.\n"
                    f"File: <code>{html.escape(str(file_label))}</code>\n"
                    f"Reason: {html.escape(str(exc))}\n\n{hint}",
                )
        except Forbidden as exc:
            await db.set_channel_active(chat_id, False)
            await notify_admins(
                app.bot,
                config,
                f"⛔️ <b>{html.escape(channel['title'])}</b> was deactivated.\n"
                f"Telegram says: {html.escape(str(exc))}\n\n"
                "Make me an administrator again, then reactivate the channel in /channels.",
            )
        except BadRequest as exc:
            error_text = str(exc).lower()
            if any(
                marker in error_text
                for marker in ("chat not found", "channel not found", "peer_id_invalid")
            ):
                await db.set_channel_active(chat_id, False)
                reason = "The channel is no longer reachable, so it was deactivated."
            else:
                reason = "The post was skipped and will be tried again at the next slot."
            log.error("Could not post to %s: %s", channel["title"], exc)
            if _should_notify_error(chat_id):
                await notify_admins(
                    app.bot,
                    config,
                    f"⚠️ Could not post to <b>{html.escape(channel['title'])}</b>.\n"
                    f"{html.escape(str(exc))}\n\n{reason}",
                )
            await _advance_channel(db, chat_id)
        except TelegramError as exc:
            log.error("Telegram error posting to %s: %s", channel["title"], exc)
            if _should_notify_error(chat_id):
                await notify_admins(
                    app.bot,
                    config,
                    f"⚠️ Could not post to <b>{html.escape(channel['title'])}</b>.\n"
                    f"{html.escape(str(exc))}",
                )
            await _advance_channel(db, chat_id)
        except Exception as exc:
            log.exception("Unexpected error posting to %s", channel["title"])
            if _should_notify_error(chat_id):
                await notify_admins(
                    app.bot,
                    config,
                    f"⚠️ Unexpected error while posting to "
                    f"<b>{html.escape(channel['title'])}</b>.\n{html.escape(shorten(str(exc), 300))}",
                )
            await _advance_channel(db, chat_id)


async def scheduler_loop(app: Application, db: Database, config: Config) -> None:
    await asyncio.sleep(5)
    log.info("Scheduler started (checking every %ss)", SCHEDULER_INTERVAL_SECONDS)
    while True:
        try:
            await run_scheduler_tick(app, db, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)

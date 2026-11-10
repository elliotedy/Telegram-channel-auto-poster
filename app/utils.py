"""Pure helper functions used across the bot."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = UTC
MAX_CAPTION_LENGTH = 1024

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_TME_CHANNEL_LINK_RE = re.compile(
    r"^(?:https?://)?t(?:elegram)?\.me/c/(\d+)(?:/\d+)?/?$", re.IGNORECASE
)
_TME_USERNAME_LINK_RE = re.compile(
    r"^(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]{4,})(?:/\d+)?/?$", re.IGNORECASE
)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    """Store timestamps as sortable UTC ISO strings."""
    return value.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_schedule(raw: str | None) -> list[time]:
    """Parse ``"08:00, 12:30 20:00"`` into sorted unique ``time`` objects."""
    if not raw or not raw.strip():
        raise ValueError("No times were provided. Use 24-hour HH:MM values, for example 08:00 12:00 20:00.")

    chunks = [chunk for chunk in re.split(r"[,\s]+", raw.strip()) if chunk]
    parsed: set[time] = set()
    for chunk in chunks:
        match = _TIME_RE.match(chunk)
        if not match:
            raise ValueError(f"Invalid time {chunk!r}. Use 24-hour HH:MM, for example 08:00.")
        parsed.add(time(int(match.group(1)), int(match.group(2))))

    if not parsed:
        raise ValueError("No valid times were provided.")
    if len(parsed) > 24:
        raise ValueError("A maximum of 24 daily posting times is supported.")
    return sorted(parsed)


def format_schedule(times: list[time]) -> str:
    return ", ".join(slot.strftime("%H:%M") for slot in times)


def safe_zoneinfo(name: str | None) -> ZoneInfo:
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")


def next_slot_utc(
    times: list[time],
    tz: ZoneInfo,
    after: datetime | None = None,
) -> datetime:
    """Return the first scheduled moment after ``after``, in UTC."""
    reference = after or utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    local_reference = reference.astimezone(tz)

    for day_offset in range(0, 8):
        day = local_reference.date() + timedelta(days=day_offset)
        for slot in times:
            candidate = datetime.combine(day, slot, tzinfo=tz)
            if candidate > local_reference:
                return candidate.astimezone(UTC)

    raise RuntimeError("Could not calculate the next posting time.")


def humanize_delta(seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "less than a minute"


def format_next_post(iso_value: str | None, tz: ZoneInfo) -> str:
    if not iso_value:
        return "not scheduled"
    when = from_iso(iso_value)
    local = when.astimezone(tz)
    seconds_left = (when - utc_now()).total_seconds()
    if seconds_left <= 0:
        return f"{local:%d %b, %H:%M} (due now)"
    return f"{local:%d %b, %H:%M} (in {humanize_delta(seconds_left)})"


def format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "0:00"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def shorten(text: str | None, limit: int = 40) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip() + "…"


_CAPTION_TOKEN_RE = re.compile(r"\{(caption|channel|date|random_caption)\}")
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")


def _close_open_tags(text: str) -> str:
    """Append closing tags for anything left open by truncation."""
    stack: list[str] = []
    for match in _HTML_TAG_RE.finditer(text):
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
        if name == "br" or attrs.rstrip().endswith("/"):
            continue
        if not closing:
            stack.append(name)
            continue
        while stack:
            if stack.pop() == name:
                break
    return text + "".join(f"</{name}>" for name in reversed(stack))


def _truncate_caption(text: str, limit: int = MAX_CAPTION_LENGTH) -> str:
    """Trim HTML caption text without cutting a tag or entity in half."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_open = cut.rfind("<")
    if last_open > cut.rfind(">"):
        cut = cut[:last_open]
    last_amp = cut.rfind("&")
    if last_amp > cut.rfind(";") and limit - last_amp < 10:
        cut = cut[:last_amp]
    return _close_open_tags(cut.rstrip())


def build_caption(
    template: str | None,
    video_caption: str | None,
    channel_title: str | None,
    when_local: datetime,
    random_caption: str | None = None,
) -> str:
    """Fill the caption template and escape it for Telegram HTML mode.

    Template text, media captions and channel titles are escaped so they are
    always shown literally. ``{random_caption}`` is replaced with an
    admin-authored pool entry that may contain Telegram HTML.
    """
    source = template if template is not None else "{caption}"
    values = {
        "caption": html.escape((video_caption or "").strip(), quote=False),
        "channel": html.escape((channel_title or "").strip(), quote=False),
        "date": html.escape(when_local.strftime("%d %b %Y, %H:%M"), quote=False),
        "random_caption": (random_caption or "").strip(),
    }

    parts = _CAPTION_TOKEN_RE.split(source)
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            rendered.append(html.escape(part, quote=False))
        else:
            rendered.append(values[part])
    text = "".join(rendered)

    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return ""
    return _truncate_caption(cleaned)


def parse_channel_target(raw: str) -> int | str | None:
    """Accept @username, numeric ID, t.me/username or t.me/c/... links."""
    value = (raw or "").strip()
    if not value:
        return None
    value = value.split("?", 1)[0].rstrip("/")

    link_match = _TME_CHANNEL_LINK_RE.match(value)
    if link_match:
        return int(f"-100{link_match.group(1)}")

    username_link_match = _TME_USERNAME_LINK_RE.match(value)
    if username_link_match:
        return f"@{username_link_match.group(1)}"

    if value.startswith("@"):
        username = value[1:].strip()
        return f"@{username}" if _USERNAME_RE.match(username) else None

    if re.fullmatch(r"-?\d{5,}", value):
        return int(value)

    return None


def channel_post_link(chat_id: int, username: str | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"
    raw_id = str(chat_id)
    if raw_id.startswith("-100"):
        return f"https://t.me/c/{raw_id[4:]}/{message_id}"
    return None

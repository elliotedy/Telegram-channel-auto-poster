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


def build_caption(
    template: str | None,
    video_caption: str | None,
    channel_title: str | None,
    when_local: datetime,
) -> str:
    """Fill the caption template and escape it for Telegram HTML mode."""
    text = template if template is not None else "{caption}"
    text = text.replace("{caption}", (video_caption or "").strip())
    text = text.replace("{channel}", (channel_title or "").strip())
    text = text.replace("{date}", when_local.strftime("%d %b %Y, %H:%M"))

    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return ""

    escaped = html.escape(cleaned, quote=False)
    # Telegram allows 1024 characters in a media caption. Trim the raw text
    # so that the escaped result fits within MAX_CAPTION_LENGTH.
    if len(escaped) <= MAX_CAPTION_LENGTH:
        return escaped

    low, high = 0, min(len(cleaned), MAX_CAPTION_LENGTH)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        cand_raw = cleaned[:mid].rstrip()
        cand_esc = html.escape(cand_raw, quote=False)
        if len(cand_esc) <= MAX_CAPTION_LENGTH:
            best = cand_esc
            low = mid + 1
        else:
            high = mid - 1
    return best


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

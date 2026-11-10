"""Shared constants."""

from __future__ import annotations

DEFAULT_SCHEDULE = "08:00,11:00,14:00,17:00,20:00,23:00"
DEFAULT_CAPTION_TEMPLATE = "{random_caption}"

SCHEDULE_PRESETS: dict[str, tuple[str, str]] = {
    # key: (button label, comma separated times)
    "three": ("3x / day", "10:00,15:00,20:00"),
    "four": ("4x / day", "09:00,13:00,17:00,21:00"),
    "six": ("6x / day (default)", DEFAULT_SCHEDULE),
}

TIMEZONE_PRESETS = [
    "UTC",
    "Asia/Kolkata",
    "Asia/Karachi",
    "Asia/Dhaka",
    "Asia/Dubai",
    "Asia/Manila",
    "Asia/Jakarta",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Los_Angeles",
    "Africa/Lagos",
]

VIDEOS_PER_PAGE = 8

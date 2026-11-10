from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.utils import (
    build_caption,
    channel_post_link,
    format_duration,
    humanize_delta,
    next_slot_utc,
    parse_channel_target,
    parse_schedule,
)


def test_parse_schedule_sorts_and_deduplicates() -> None:
    assert parse_schedule("20:00, 08:00 08:00,11:30") == [
        time(8, 0),
        time(11, 30),
        time(20, 0),
    ]


@pytest.mark.parametrize("value", ["", "   ", "8", "25:00", "12:60", "noon", "12:00 bad"])
def test_parse_schedule_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule(value)


def test_next_slot_same_day() -> None:
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    result = next_slot_utc([time(8, 0), time(20, 0)], ZoneInfo("UTC"), after)
    assert result == datetime(2026, 1, 1, 20, 0, tzinfo=UTC)


def test_next_slot_rolls_to_next_day() -> None:
    after = datetime(2026, 1, 1, 21, 0, tzinfo=UTC)
    result = next_slot_utc([time(8, 0), time(20, 0)], ZoneInfo("UTC"), after)
    assert result == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)


def test_next_slot_uses_timezone() -> None:
    after = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    result = next_slot_utc([time(8, 0)], ZoneInfo("Asia/Kolkata"), after)
    assert result == datetime(2026, 1, 1, 2, 30, tzinfo=UTC)


def test_build_caption_replaces_variables_and_escapes_html() -> None:
    caption = build_caption(
        "{caption}\n\n{channel} — {date}",
        "Fish <b>fry</b> & chips",
        "My Channel",
        datetime(2026, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC")),
    )
    assert "Fish &lt;b&gt;fry&lt;/b&gt; &amp; chips" in caption
    assert "My Channel" in caption
    assert "01 Jan 2026, 08:00" in caption


def test_build_caption_is_trimmed_to_telegram_limit() -> None:
    caption = build_caption("{caption}", "&" * 2000, "channel", datetime.now(UTC))
    assert 0 < len(caption) <= 1024


def test_build_caption_can_return_empty() -> None:
    assert build_caption("{caption}", None, "channel", datetime.now(UTC)) == ""


def test_build_caption_inserts_pool_html_but_escapes_other_values() -> None:
    caption = build_caption(
        "{caption}\n\n{random_caption}\n{channel}",
        "Fish <b>fry</b> & chips",
        "My <Channel>",
        datetime.now(UTC),
        '<b>Join</b> <a href="https://t.me/example">now</a>',
    )
    assert "Fish &lt;b&gt;fry&lt;/b&gt; &amp; chips" in caption
    assert "My &lt;Channel&gt;" in caption
    assert '<b>Join</b> <a href="https://t.me/example">now</a>' in caption


def test_build_caption_escapes_html_in_the_template_itself() -> None:
    caption = build_caption("Price < 500 & rising", None, None, datetime.now(UTC))
    assert caption == "Price &lt; 500 &amp; rising"


def test_build_caption_trims_without_breaking_html() -> None:
    pool_caption = '<a href="https://t.me/example">' + "x" * 2000 + "</a>"
    caption = build_caption("{random_caption}", None, None, datetime.now(UTC), pool_caption)
    assert caption.startswith('<a href="https://t.me/example">')
    assert caption.endswith("</a>")
    assert caption.count("<a ") == 1
    visible = caption[: caption.rindex("</a>")].split(">", 1)[1]
    assert 0 < len(visible) <= 1024


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@mychannel", "@mychannel"),
        ("https://t.me/mychannel", "@mychannel"),
        ("t.me/mychannel/25", "@mychannel"),
        ("-1001234567890", -1001234567890),
        ("https://t.me/c/1234567890/42", -1001234567890),
        ("t.me/c/1234567890", -1001234567890),
        ("https://t.me/+invitehash", None),
        ("", None),
        ("not a channel", None),
    ],
)
def test_parse_channel_target(raw: str, expected) -> None:
    assert parse_channel_target(raw) == expected


def test_channel_post_link() -> None:
    assert channel_post_link(-1001234567890, None, 7) == "https://t.me/c/1234567890/7"
    assert channel_post_link(-1001234567890, "mychannel", 7) == "https://t.me/mychannel/7"


def test_format_duration() -> None:
    assert format_duration(None) == "0:00"
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"


def test_humanize_delta() -> None:
    assert humanize_delta(30) == "less than a minute"
    assert humanize_delta(3665) == "1h 1m"
    assert humanize_delta(90065) == "1d 1h"

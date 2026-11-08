from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from telegram.error import RetryAfter

from app import views
from app.config import Config
from app.database import Database
from app.handlers import (
    BOT_COMMANDS,
    _extract_media,
)
from app.poster import Poster, _deliver_with_retries
from app.utils import utc_now


def test_bot_commands_list() -> None:
    command_names = [cmd.command for cmd in BOT_COMMANDS]
    assert "start" in command_names
    assert "folders" in command_names
    assert "addfolder" in command_names
    assert "removefolder" in command_names
    assert "scan" in command_names
    assert "addmedia" in command_names


def test_extract_media() -> None:
    # Photo test
    photo_mock = MagicMock()
    msg_photo = MagicMock()
    msg_photo.photo = [MagicMock(), photo_mock]
    msg_photo.video = None
    msg_photo.animation = None
    msg_photo.document = None

    extracted, media_type = _extract_media(msg_photo)
    assert extracted == photo_mock
    assert media_type == "photo"

    # Video test
    video_mock = MagicMock()
    msg_video = MagicMock()
    msg_video.photo = None
    msg_video.video = video_mock
    msg_video.animation = None
    msg_video.document = None

    extracted, media_type = _extract_media(msg_video)
    assert extracted == video_mock
    assert media_type == "video"

    # Animation test
    anim_mock = MagicMock()
    msg_anim = MagicMock()
    msg_anim.photo = None
    msg_anim.video = None
    msg_anim.animation = anim_mock
    msg_anim.document = None

    extracted, media_type = _extract_media(msg_anim)
    assert extracted == anim_mock
    assert media_type == "animation"

    # Document as image
    doc_mock = MagicMock()
    doc_mock.mime_type = "image/png"
    doc_mock.file_name = "test.png"
    msg_doc = MagicMock()
    msg_doc.photo = None
    msg_doc.video = None
    msg_doc.animation = None
    msg_doc.document = doc_mock

    extracted, media_type = _extract_media(msg_doc)
    assert extracted == doc_mock
    assert media_type == "photo"

    # Unknown
    msg_unknown = MagicMock()
    msg_unknown.photo = None
    msg_unknown.video = None
    msg_unknown.animation = None
    msg_unknown.document = None
    assert _extract_media(msg_unknown) == (None, None)


def test_views(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "views.db")
        await db.connect()
        await db.seed_settings({"timezone": "UTC"})

        config = Config(
            bot_token="fake:token",
            admin_ids=frozenset([123]),
        )

        # Help view
        text, markup = views.help_view()
        assert "/folders" in text
        assert "/scan" in text
        assert "/addmedia" in text
        assert "/termux" in text

        # Termux install guide
        text, markup = views.termux_help_view()
        assert "Termux" in text
        assert ".venv/bin/python main.py" in text
        assert "pip forbidden" in text
        assert "termux-setup-storage" in text
        assert "/addfolder" in text

        # Add media help view and alias
        text, markup = views.add_media_help_view()
        assert "Add media" in text
        assert views.add_video_help_view == views.add_media_help_view

        # Post now view with empty library
        text, markup = await views.post_now_view(db)
        assert "no active items" in text.lower() or "empty" in text.lower()

        # Folders view empty
        text, markup = await views.folders_view(db)
        assert "No folders configured" in text

        # Add a folder and recheck
        await db.add_folder("/test/folder")
        text, markup = await views.folders_view(db)
        assert "/test/folder" in text

        # Folders help view
        text, markup = views.folders_help_view(config)
        assert "Folder import" in text

        # Scan result view
        text, markup = views.scan_result_view("<b>Result</b>")
        assert "Result" in text

        await db.close()

    asyncio.run(scenario())


def test_deliver_with_retries_timedelta_handling() -> None:
    async def scenario() -> None:
        poster = MagicMock(spec=Poster)
        deliver_mock = AsyncMock()

        class TimedeltaRetryAfter(RetryAfter):
            def __init__(self):
                super().__init__(5)

            @property
            def retry_after(self):
                return timedelta(seconds=0.01)

        deliver_mock.side_effect = [TimedeltaRetryAfter(), MagicMock(message_id=99)]
        poster.deliver = deliver_mock

        channel = {"chat_id": 12345, "title": "Test Channel"}
        video = {"id": 1}

        msg = await _deliver_with_retries(
            poster, channel, video, advance_schedule=False, now=utc_now()
        )
        assert msg.message_id == 99
        assert deliver_mock.call_count == 2

    asyncio.run(scenario())

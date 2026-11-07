from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.database import Database
from app.utils import to_iso


def test_channel_video_rotation_and_stats(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        await db.seed_settings({"schedule_times": "08:00,20:00", "timezone": "UTC"})

        channel, created = await db.add_channel(
            chat_id=-1001234567890,
            title="Test Channel",
            username="testchannel",
            next_post_at=to_iso(datetime.now(UTC) - timedelta(minutes=1)),
        )
        assert created is True
        duplicate, created_again = await db.add_channel(
            chat_id=-1001234567890,
            title="Test Channel",
            username="testchannel",
            next_post_at=to_iso(datetime.now(UTC)),
        )
        assert created_again is False
        assert duplicate["id"] == channel["id"]

        video_one, created = await db.add_video("file-1", "unique-1", "video", "First", 60)
        assert created is True
        video_two, _ = await db.add_video("file-2", "unique-2", "video", "Second", 90)
        duplicate_video, created = await db.add_video("file-2b", "unique-2", "video", "Second", 90)
        assert created is False
        assert duplicate_video["id"] == video_two["id"]

        assert (await db.next_video())["id"] == video_one["id"]

        now = to_iso(datetime.now(UTC))
        next_at = to_iso(datetime.now(UTC) + timedelta(hours=4))
        await db.record_post(
            video_id=int(video_one["id"]),
            channel_id=int(channel["id"]),
            posted_at=now,
            trigger="schedule",
            next_post_at=next_at,
        )
        assert (await db.next_video())["id"] == video_two["id"]

        await db.record_post(
            video_id=int(video_two["id"]),
            channel_id=int(channel["id"]),
            posted_at=now,
            trigger="manual",
        )
        assert (await db.next_video())["id"] == video_one["id"]

        stats = await db.stats("2000-01-01T00:00:00+00:00")
        assert stats == {
            "total_channels": 1,
            "active_channels": 1,
            "total_videos": 2,
            "active_videos": 2,
            "total_posts": 2,
            "posts_today": 2,
        }

        due = await db.due_channels(to_iso(datetime.now(UTC) + timedelta(hours=5)))
        assert len(due) == 1
        assert due[0]["chat_id"] == channel["chat_id"]

        assert await db.find_channel("@testchannel") is not None
        assert await db.find_channel("test channel") is not None

        await db.delete_channel(int(channel["chat_id"]))
        assert await db.list_channels() == []

        await db.close()

    asyncio.run(scenario())



def test_schedule_pause_settings(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()
        await db.seed_settings({"paused": "0", "notify_posts": "0"})

        assert await db.get_bool("paused") is False
        await db.set_bool("paused", True)
        assert await db.get_bool("paused") is True
        await db.set_setting("timezone", "Asia/Kolkata")
        assert await db.get_setting("timezone") == "Asia/Kolkata"
        assert await db.get_setting("missing", "fallback") == "fallback"

        await db.close()

    asyncio.run(scenario())


def test_database_folder_crud_and_video_toggle(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test_folders.db")
        await db.connect()

        # Add folder
        folder, created = await db.add_folder("/media/clips")
        assert created is True
        assert folder["path"] == "/media/clips"
        assert folder["enabled"] == 1

        # Duplicate folder
        dup, created_again = await db.add_folder("/media/clips")
        assert created_again is False
        assert dup["id"] == folder["id"]

        # List folders
        folders = await db.list_folders()
        assert len(folders) == 1

        # Disable folder
        await db.set_folder_enabled(int(folder["id"]), False)
        assert len(await db.list_folders(enabled_only=True)) == 0
        updated = await db.get_folder(int(folder["id"]))
        assert updated is not None
        assert updated["enabled"] == 0

        # Enable folder
        await db.set_folder_enabled(int(folder["id"]), True)
        assert len(await db.list_folders(enabled_only=True)) == 1

        # Touch folder
        now = to_iso(datetime.now(UTC))
        await db.touch_folder(int(folder["id"]), now)
        touched = await db.get_folder(int(folder["id"]))
        assert touched["last_scan_at"] == now

        # Add video and toggle active
        video, _ = await db.add_video("file-x", "unique-x", "video", "Test", 10)
        assert video["active"] == 1
        assert await db.count_videos(active_only=True) == 1

        await db.set_video_active(int(video["id"]), False)
        assert await db.count_videos(active_only=True) == 0
        assert await db.next_video() is None

        await db.set_video_active(int(video["id"]), True)
        assert await db.count_videos(active_only=True) == 1
        assert (await db.next_video())["id"] == video["id"]

        # Delete folder
        deleted = await db.delete_folder(int(folder["id"]))
        assert deleted is True
        assert len(await db.list_folders()) == 0

        await db.close()

    asyncio.run(scenario())



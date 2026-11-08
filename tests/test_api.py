from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.api import api_add_channel, api_list_channels, api_status
from app.database import Database
from app.utils import to_iso


def test_api_status_and_channels(tmp_path) -> None:
    db_path = tmp_path / "api.db"

    async def seed() -> None:
        db = Database(db_path)
        await db.connect()
        await db.seed_settings(
            {
                "schedule_times": "08:00,11:00,14:00,17:00,20:00,23:00",
                "timezone": "UTC",
                "paused": "0",
            }
        )
        await db.add_channel(
            -1001234567890,
            "Test",
            "testchannel",
            to_iso(datetime.now(UTC)),
        )
        await db.close()

    asyncio.run(seed())

    status = api_status(db_path)
    assert status["ok"] is True
    assert status["channels"] == 1
    assert status["active_channels"] == 1

    listed = api_list_channels(db_path)
    assert listed["channels"][0]["username"] == "testchannel"

    added = api_add_channel(
        db_path, {"chat_id": -1009999999999, "title": "New", "username": "@newchannel"}
    )
    assert added["ok"] is True
    assert added["channel"]["username"] == "newchannel"

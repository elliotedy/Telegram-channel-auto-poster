from __future__ import annotations

import json
import random

from app.captions import DEFAULT_CAPTIONS, dump_pool, get_pool, parse_pool, pick_caption, save_pool
from app.database import Database


def test_parse_pool_returns_defaults_when_missing_or_invalid() -> None:
    assert parse_pool(None) == DEFAULT_CAPTIONS
    assert parse_pool("") == DEFAULT_CAPTIONS
    assert parse_pool("not json") == DEFAULT_CAPTIONS
    assert parse_pool(json.dumps({"nope": 1})) == DEFAULT_CAPTIONS
    assert parse_pool("[]") == DEFAULT_CAPTIONS


def test_parse_pool_cleans_entries() -> None:
    raw = json.dumps(["  first  ", "", "second", "   "])
    assert parse_pool(raw) == ["first", "second"]


def test_dump_pool_round_trips() -> None:
    captions = ["One <b>bold</b>", "Two"]
    assert parse_pool(dump_pool(captions)) == captions


def test_pick_caption_is_none_for_empty_pool_and_stable_with_seed() -> None:
    assert pick_caption([]) is None
    pool = ["a", "b", "c"]
    assert pick_caption(pool, random.Random(1)) in pool
    assert pick_caption(pool, random.Random(1)) == pick_caption(pool, random.Random(1))


def test_pool_round_trips_through_the_database(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "captions.db")
        await db.connect()
        try:
            assert await get_pool(db) == DEFAULT_CAPTIONS
            await save_pool(db, ["custom one", "custom two"])
            assert await get_pool(db) == ["custom one", "custom two"]
        finally:
            await db.close()

    import asyncio

    asyncio.run(scenario())

"""Async SQLite storage for channels, videos, posts and settings."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import IntegrityError
from typing import Any

import aiosqlite

from .utils import to_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    username TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    next_post_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT,
    file_unique_id TEXT UNIQUE,
    media_type TEXT NOT NULL DEFAULT 'video',
    caption TEXT,
    duration INTEGER,
    file_path TEXT,
    filename TEXT,
    file_size INTEGER,
    file_mtime_ns INTEGER,
    import_key TEXT UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    fail_count INTEGER NOT NULL DEFAULT 0,
    posted_count INTEGER NOT NULL DEFAULT 0,
    last_posted_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scan_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER,
    channel_id INTEGER,
    posted_at TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'schedule',
    FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE SET NULL,
    FOREIGN KEY (channel_id) REFERENCES channels (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_channels_due
    ON channels (active, next_post_at);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at
    ON posts (posted_at);
CREATE INDEX IF NOT EXISTS idx_videos_rotation
    ON videos (active, posted_count, last_posted_at);
CREATE INDEX IF NOT EXISTS idx_folders_enabled
    ON folders (enabled);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database.connect() has not been called yet.")
        return self._connection

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._connection.execute("PRAGMA busy_timeout=5000")
        await self._connection.executescript(SCHEMA)
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def checkpoint(self) -> None:
        async with self._write_lock:
            await self.connection.execute("PRAGMA wal_checkpoint(FULL)")
            await self.connection.commit()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    async def seed_settings(self, defaults: Mapping[str, str]) -> None:
        async with self._write_lock:
            await self.connection.executemany(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                list(defaults.items()),
            )
            await self.connection.commit()

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        cursor = await self.connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            await self.connection.commit()

    async def get_bool(self, key: str, default: bool = False) -> bool:
        raw = await self.get_setting(key)
        if raw is None:
            return default
        return raw.strip() == "1"

    async def set_bool(self, key: str, value: bool) -> None:
        await self.set_setting(key, "1" if value else "0")

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------
    async def add_channel(
        self,
        chat_id: int,
        title: str,
        username: str | None,
        next_post_at: str,
    ) -> tuple[aiosqlite.Row, bool]:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "SELECT * FROM channels WHERE chat_id = ?", (chat_id,)
            )
            existing = await cursor.fetchone()
            if existing is not None:
                return existing, False

            await self.connection.execute(
                """
                INSERT INTO channels (chat_id, title, username, active, next_post_at, created_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (chat_id, title, username, next_post_at, to_iso(datetime.now(UTC))),
            )
            await self.connection.commit()
            cursor = await self.connection.execute(
                "SELECT * FROM channels WHERE chat_id = ?", (chat_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            return row, True

    async def list_channels(self, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM channels"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY active DESC, title COLLATE NOCASE ASC"
        cursor = await self.connection.execute(sql)
        return list(await cursor.fetchall())

    async def get_channel(self, chat_id: int) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(
            "SELECT * FROM channels WHERE chat_id = ?", (chat_id,)
        )
        return await cursor.fetchone()

    async def find_channel(self, query: str) -> aiosqlite.Row | None:
        value = query.strip()
        if value.startswith("@"):
            cursor = await self.connection.execute(
                "SELECT * FROM channels WHERE LOWER(username) = LOWER(?)",
                (value[1:],),
            )
            return await cursor.fetchone()
        if value.lstrip("-").isdigit():
            return await self.get_channel(int(value))
        cursor = await self.connection.execute(
            "SELECT * FROM channels WHERE LOWER(title) = LOWER(?)", (value,)
        )
        return await cursor.fetchone()

    async def set_channel_active(self, chat_id: int, active: bool) -> None:
        async with self._write_lock:
            await self.connection.execute(
                "UPDATE channels SET active = ? WHERE chat_id = ?",
                (1 if active else 0, chat_id),
            )
            await self.connection.commit()

    async def set_channel_next(self, chat_id: int, next_post_at: str | None) -> None:
        async with self._write_lock:
            await self.connection.execute(
                "UPDATE channels SET next_post_at = ? WHERE chat_id = ?",
                (next_post_at, chat_id),
            )
            await self.connection.commit()

    async def delete_channel(self, chat_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "DELETE FROM channels WHERE chat_id = ?", (chat_id,)
            )
            await self.connection.commit()
            return cursor.rowcount > 0

    async def reschedule_all(self, next_post_at: str) -> None:
        async with self._write_lock:
            await self.connection.execute(
                "UPDATE channels SET next_post_at = ?", (next_post_at,)
            )
            await self.connection.commit()

    async def schedule_missing(self, next_post_at: str) -> None:
        async with self._write_lock:
            await self.connection.execute(
                "UPDATE channels SET next_post_at = ? WHERE next_post_at IS NULL",
                (next_post_at,),
            )
            await self.connection.commit()

    async def due_channels(self, now_iso: str) -> list[aiosqlite.Row]:
        cursor = await self.connection.execute(
            """
            SELECT * FROM channels
            WHERE active = 1
              AND next_post_at IS NOT NULL
              AND next_post_at <= ?
            ORDER BY next_post_at ASC
            """,
            (now_iso,),
        )
        return list(await cursor.fetchall())

    async def next_post_overall(self) -> str | None:
        cursor = await self.connection.execute(
            """
            SELECT MIN(next_post_at) AS next_at
            FROM channels
            WHERE active = 1 AND next_post_at IS NOT NULL
            """
        )
        row = await cursor.fetchone()
        return row["next_at"] if row else None

    # ------------------------------------------------------------------
    # Media folders
    # ------------------------------------------------------------------
    async def add_folder(self, path: str) -> tuple[aiosqlite.Row, bool]:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "SELECT * FROM folders WHERE path = ?", (path,)
            )
            existing = await cursor.fetchone()
            if existing is not None:
                return existing, False

            await self.connection.execute(
                "INSERT INTO folders (path, enabled, created_at) VALUES (?, 1, ?)",
                (path, to_iso(datetime.now(UTC))),
            )
            await self.connection.commit()
            cursor = await self.connection.execute(
                "SELECT * FROM folders WHERE path = ?", (path,)
            )
            row = await cursor.fetchone()
            assert row is not None
            return row, True

    async def list_folders(self, enabled_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM folders"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY enabled DESC, path COLLATE NOCASE ASC"
        cursor = await self.connection.execute(sql)
        return list(await cursor.fetchall())

    async def get_folder(self, folder_id: int) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(
            "SELECT * FROM folders WHERE id = ?", (folder_id,)
        )
        return await cursor.fetchone()

    async def set_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        async with self._write_lock:
            await self.connection.execute(
                "UPDATE folders SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, folder_id),
            )
            await self.connection.commit()

    async def delete_folder(self, folder_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "DELETE FROM folders WHERE id = ?", (folder_id,)
            )
            await self.connection.commit()
            return cursor.rowcount > 0

    async def touch_folder(self, folder_id: int, when_iso: str) -> None:
        async with self._write_lock:
            await self.connection.execute(
                "UPDATE folders SET last_scan_at = ? WHERE id = ?",
                (when_iso, folder_id),
            )
            await self.connection.commit()

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------
    async def add_video(
        self,
        file_id: str,
        file_unique_id: str,
        media_type: str,
        caption: str | None,
        duration: int | None,
    ) -> tuple[aiosqlite.Row, bool]:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "SELECT * FROM videos WHERE file_unique_id = ?", (file_unique_id,)
            )
            existing = await cursor.fetchone()
            if existing is not None:
                return existing, False

            await self.connection.execute(
                """
                INSERT INTO videos
                    (file_id, file_unique_id, media_type, caption, duration, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    file_unique_id,
                    media_type,
                    caption,
                    duration,
                    to_iso(datetime.now(UTC)),
                ),
            )
            await self.connection.commit()
            cursor = await self.connection.execute(
                "SELECT * FROM videos WHERE file_unique_id = ?", (file_unique_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            return row, True

    async def add_imported_video(
        self,
        *,
        file_path: str,
        filename: str,
        file_size: int,
        file_mtime_ns: int,
        media_type: str,
        caption: str | None,
        import_key: str,
    ) -> tuple[aiosqlite.Row, bool]:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "SELECT * FROM videos WHERE import_key = ?", (import_key,)
            )
            existing = await cursor.fetchone()
            if existing is not None:
                return existing, False

            await self.connection.execute(
                """
                INSERT INTO videos
                    (file_path, filename, file_size, file_mtime_ns, media_type,
                     caption, import_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_path,
                    filename,
                    file_size,
                    file_mtime_ns,
                    media_type,
                    caption,
                    import_key,
                    to_iso(datetime.now(UTC)),
                ),
            )
            await self.connection.commit()
            cursor = await self.connection.execute(
                "SELECT * FROM videos WHERE import_key = ?", (import_key,)
            )
            row = await cursor.fetchone()
            assert row is not None
            return row, True

    async def get_video(self, video_id: int) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        )
        return await cursor.fetchone()

    async def list_videos(self, limit: int, offset: int) -> list[aiosqlite.Row]:
        cursor = await self.connection.execute(
            "SELECT * FROM videos ORDER BY active DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return list(await cursor.fetchall())

    async def count_videos(self, active_only: bool = False) -> int:
        sql = "SELECT COUNT(*) AS count FROM videos"
        if active_only:
            sql += " WHERE active = 1"
        cursor = await self.connection.execute(sql)
        row = await cursor.fetchone()
        return int(row["count"]) if row else 0

    async def delete_video(self, video_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "DELETE FROM videos WHERE id = ?", (video_id,)
            )
            await self.connection.commit()
            return cursor.rowcount > 0

    async def update_video_caption(self, video_id: int, caption: str | None) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "UPDATE videos SET caption = ? WHERE id = ?", (caption, video_id)
            )
            await self.connection.commit()
            return cursor.rowcount > 0

    async def set_video_active(self, video_id: int, active: bool) -> None:
        async with self._write_lock:
            await self.connection.execute(
                """
                UPDATE videos
                SET active = ?,
                    fail_count = CASE WHEN ? = 1 THEN 0 ELSE fail_count END
                WHERE id = ?
                """,
                (1 if active else 0, 1 if active else 0, video_id),
            )
            await self.connection.commit()

    async def set_video_telegram_file(
        self,
        video_id: int,
        file_id: str,
        file_unique_id: str | None,
        media_type: str,
        duration: int | None = None,
    ) -> None:
        """Cache the Telegram file_id after the first upload of a local file."""
        async with self._write_lock:
            try:
                await self.connection.execute(
                    """
                    UPDATE videos
                    SET file_id = ?,
                        file_unique_id = COALESCE(?, file_unique_id),
                        media_type = ?,
                        duration = COALESCE(?, duration)
                    WHERE id = ?
                    """,
                    (file_id, file_unique_id, media_type, duration, video_id),
                )
                await self.connection.commit()
            except IntegrityError:
                # Another library entry already holds this file_unique_id.
                await self.connection.rollback()
                await self.connection.execute(
                    """
                    UPDATE videos
                    SET file_id = ?, media_type = ?, duration = COALESCE(?, duration)
                    WHERE id = ?
                    """,
                    (file_id, media_type, duration, video_id),
                )
                await self.connection.commit()

    async def record_video_failure(self, video_id: int) -> tuple[int, bool]:
        async with self._write_lock:
            await self.connection.execute(
                """
                UPDATE videos
                SET fail_count = fail_count + 1,
                    active = CASE WHEN fail_count + 1 >= 3 THEN 0 ELSE active END
                WHERE id = ?
                """,
                (video_id,),
            )
            await self.connection.commit()
            cursor = await self.connection.execute(
                "SELECT fail_count, active FROM videos WHERE id = ?", (video_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return 0, False
            return int(row["fail_count"]), bool(row["active"])

    async def next_video(self) -> aiosqlite.Row | None:
        """Least-posted active media, with the oldest last post first."""
        cursor = await self.connection.execute(
            """
            SELECT * FROM videos
            WHERE active = 1
            ORDER BY posted_count ASC,
                     CASE WHEN last_posted_at IS NULL THEN 0 ELSE 1 END ASC,
                     last_posted_at ASC,
                     id ASC
            LIMIT 1
            """
        )
        return await cursor.fetchone()

    # ------------------------------------------------------------------
    # Post history / stats
    # ------------------------------------------------------------------
    async def record_post(
        self,
        video_id: int,
        channel_id: int,
        posted_at: str,
        trigger: str,
        next_post_at: str | None = None,
    ) -> None:
        async with self._write_lock:
            await self.connection.execute(
                """
                UPDATE videos
                SET posted_count = posted_count + 1,
                    last_posted_at = ?,
                    fail_count = 0
                WHERE id = ?
                """,
                (posted_at, video_id),
            )
            await self.connection.execute(
                """
                INSERT INTO posts (video_id, channel_id, posted_at, trigger)
                VALUES (?, ?, ?, ?)
                """,
                (video_id, channel_id, posted_at, trigger),
            )
            if next_post_at is not None:
                await self.connection.execute(
                    "UPDATE channels SET next_post_at = ? WHERE id = ?",
                    (next_post_at, channel_id),
                )
            await self.connection.commit()

    async def stats(self, today_start_iso: str) -> dict[str, Any]:
        cursor = await self.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM channels) AS total_channels,
                (SELECT COUNT(*) FROM channels WHERE active = 1) AS active_channels,
                (SELECT COUNT(*) FROM videos) AS total_videos,
                (SELECT COUNT(*) FROM videos WHERE active = 1) AS active_videos,
                (SELECT COUNT(*) FROM posts) AS total_posts,
                (SELECT COUNT(*) FROM posts WHERE posted_at >= ?) AS posts_today
            """,
            (today_start_iso,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {
                "total_channels": 0,
                "active_channels": 0,
                "total_videos": 0,
                "active_videos": 0,
                "total_posts": 0,
                "posts_today": 0,
            }
        return {
            "total_channels": int(row["total_channels"]),
            "active_channels": int(row["active_channels"]),
            "total_videos": int(row["total_videos"]),
            "active_videos": int(row["active_videos"]),
            "total_posts": int(row["total_posts"]),
            "posts_today": int(row["posts_today"]),
        }

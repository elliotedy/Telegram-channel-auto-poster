from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Config
from app.database import Database
from app.importer import (
    ScanSummary,
    normalize_folder_path,
    scan_once,
)


def test_normalize_folder_path() -> None:
    path = normalize_folder_path(' "my/relative/folder" ')
    assert isinstance(path, Path)
    assert path.is_absolute()


def test_scan_summary_as_html() -> None:
    summary = ScanSummary(
        folders_scanned=2,
        imported_photos=3,
        imported_videos=1,
        imported_animations=2,
        skipped_large=1,
    )
    html = summary.as_html(49.0)
    assert "Folders scanned: <b>2</b>" in html
    assert "1 video" in html
    assert "3 photos" in html
    assert "2 animations" in html
    assert "49 MB" in html


def test_scan_once_imports_files(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.connect()

        media_folder = tmp_path / "media"
        media_folder.mkdir()

        # Create files
        p1 = media_folder / "pic1.jpg"
        p1.write_bytes(b"image data")
        p2 = media_folder / "clip1.mp4"
        p2.write_bytes(b"video data")
        p3 = media_folder / "anim1.gif"
        p3.write_bytes(b"gif data")
        p4 = media_folder / "ignore.txt"
        p4.write_bytes(b"text data")

        # Set mtime back so files are not skipped by the "still writing" buffer (<2s)
        old_time = 1000000.0
        import os
        for p in (p1, p2, p3, p4):
            os.utime(p, (old_time, old_time))

        # Add folder to db
        await db.add_folder(str(media_folder))

        config = Config(
            bot_token="fake:token",
            admin_ids=frozenset([12345]),
            media_dirs=(str(media_folder),),
        )

        summary = await scan_once(db, config)
        assert summary.folders_scanned == 1
        assert summary.imported_photos == 1
        assert summary.imported_videos == 1
        assert summary.imported_animations == 1
        assert summary.total_imported == 3

        # Scanning again should find 0 new items
        summary2 = await scan_once(db, config)
        assert summary2.total_imported == 0

        # Videos table should now contain 3 items
        assert await db.count_videos() == 3

        await db.close()

    asyncio.run(scenario())

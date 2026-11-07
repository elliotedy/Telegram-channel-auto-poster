"""Folder watcher that imports images and videos from local directories."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram.ext import Application

from .config import ROOT_DIR, Config
from .database import Database
from .notify import notify_admins
from .utils import to_iso, utc_now

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ANIMATION_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".3gp",
    ".mpeg",
    ".mpg",
    ".wmv",
    ".flv",
}

IMPORT_NOTICE_INTERVAL = 5 * 60
_scan_lock = asyncio.Lock()
_last_notice_at = 0.0


@dataclass
class FileCandidate:
    path: Path
    size: int
    mtime_ns: int


@dataclass
class ScanSummary:
    folders_scanned: int = 0
    imported_photos: int = 0
    imported_videos: int = 0
    imported_animations: int = 0
    skipped_large: int = 0
    skipped_limit: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_imported(self) -> int:
        return self.imported_photos + self.imported_videos + self.imported_animations

    @property
    def has_changes(self) -> bool:
        return bool(self.total_imported or self.skipped_large or self.errors)

    def as_html(self, max_upload_mb: float, *, empty_message: bool = False) -> str:
        lines = ["<b>📥 Folder scan</b>", ""]
        lines.append(f"Folders scanned: <b>{self.folders_scanned}</b>")

        if self.total_imported:
            parts: list[str] = []
            if self.imported_videos:
                parts.append(f"{self.imported_videos} video{'s' if self.imported_videos != 1 else ''}")
            if self.imported_photos:
                parts.append(f"{self.imported_photos} photo{'s' if self.imported_photos != 1 else ''}")
            if self.imported_animations:
                parts.append(
                    f"{self.imported_animations} animation{'s' if self.imported_animations != 1 else ''}"
                )
            lines.append("Imported: <b>" + ", ".join(parts) + "</b>")

        if self.skipped_large:
            lines.append(
                f"⚠️ Skipped <b>{self.skipped_large}</b> file(s) larger than "
                f"{max_upload_mb:g} MB (Telegram bot upload limit)."
            )
        if self.skipped_limit:
            lines.append(
                f"⏳ <b>{self.skipped_limit}</b> more file(s) will be imported on the next scans."
            )
        if self.errors:
            lines.append("")
            lines.append("<b>Problems:</b>")
            for error in self.errors[:5]:
                lines.append(f"• {html.escape(error)}")
            if len(self.errors) > 5:
                lines.append(f"• …and {len(self.errors) - 5} more")

        if empty_message and not self.total_imported and not self.errors:
            lines.append("")
            lines.append("🟢 No new supported media files found.")

        if self.total_imported:
            lines.append("")
            lines.append("They will enter the posting rotation automatically.")
        return "\n".join(lines)


def normalize_folder_path(raw: str) -> Path:
    value = raw.strip().strip('"').strip("'")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve(strict=False)


def _clean_filename_caption(stem: str) -> str | None:
    text = re.sub(r"[_\-\.]+", " ", stem)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:250]


def _media_type_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "photo"
    if suffix in ANIMATION_EXTENSIONS:
        return "animation"
    return None


def _collect_files(root: Path, recursive: bool) -> list[FileCandidate]:
    candidates: list[FileCandidate] = []
    if recursive:
        walker = os.walk(root, followlinks=False)
        for current, dirs, files in walker:
            dirs[:] = sorted(
                directory
                for directory in dirs
                if not directory.startswith(".") and directory != "__pycache__"
            )
            for name in files:
                if name.startswith("."):
                    continue
                candidate = _make_candidate(Path(current) / name)
                if candidate is not None:
                    candidates.append(candidate)
    else:
        try:
            entries = list(root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            candidate = _make_candidate(entry)
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=lambda item: item.mtime_ns, reverse=True)
    return candidates


def _make_candidate(path: Path) -> FileCandidate | None:
    if _media_type_for(path) is None:
        return None
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size <= 0:
        return None
    return FileCandidate(path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


async def seed_folders_from_env(db: Database, config: Config) -> None:
    for raw in config.media_dirs:
        path = normalize_folder_path(raw)
        if not path.is_dir():
            log.warning("MEDIA_DIRS entry does not exist or is not a folder: %s", path)
            continue
        _, created = await db.add_folder(str(path))
        if created:
            log.info("Seeded media folder from .env: %s", path)


async def scan_once(db: Database, config: Config) -> ScanSummary:
    """Scan all enabled folders and import new media metadata into SQLite."""
    async with _scan_lock:
        folders = await db.list_folders(enabled_only=True)
        summary = ScanSummary(folders_scanned=len(folders))
        if not folders:
            return summary

        use_filename_caption = await db.get_bool(
            "import_filename_caption", config.import_filename_caption
        )
        max_bytes = int(config.max_upload_mb * 1024 * 1024)
        now = utc_now()

        for folder in folders:
            folder_path = Path(folder["path"])
            if not folder_path.is_dir():
                summary.errors.append(f"Folder not found: {folder_path}")
                await db.touch_folder(int(folder["id"]), to_iso(now))
                continue

            candidates = await asyncio.to_thread(
                _collect_files,
                folder_path,
                config.import_recursive,
            )
            imported_for_folder = 0

            for candidate in candidates:
                if imported_for_folder >= config.import_max_per_scan:
                    summary.skipped_limit += 1
                    continue

                media_type = _media_type_for(candidate.path)
                if media_type is None:
                    continue
                if candidate.size > max_bytes:
                    summary.skipped_large += 1
                    continue

                # Do not pick up files that are still being copied/downloaded.
                if time.time() - (candidate.mtime_ns / 1_000_000_000) < 2:
                    summary.skipped_limit += 1
                    continue

                import_key = hashlib.sha1(
                    f"{candidate.path}|{candidate.size}|{candidate.mtime_ns}".encode()
                ).hexdigest()
                caption = (
                    _clean_filename_caption(candidate.path.stem)
                    if use_filename_caption
                    else None
                )
                _, created = await db.add_imported_video(
                    file_path=str(candidate.path),
                    filename=candidate.path.name,
                    file_size=candidate.size,
                    file_mtime_ns=candidate.mtime_ns,
                    media_type=media_type,
                    caption=caption,
                    import_key=import_key,
                )
                if not created:
                    continue

                imported_for_folder += 1
                if media_type == "photo":
                    summary.imported_photos += 1
                elif media_type == "animation":
                    summary.imported_animations += 1
                else:
                    summary.imported_videos += 1

            await db.touch_folder(int(folder["id"]), to_iso(now))

        return summary


async def importer_loop(app: Application, db: Database, config: Config) -> None:
    global _last_notice_at

    await asyncio.sleep(8)
    log.info("Media folder watcher started (every %ss)", config.import_interval)
    while True:
        try:
            if await db.get_bool("import_enabled", True):
                summary = await scan_once(db, config)
                if summary.has_changes:
                    log.info(
                        "Folder scan imported %s media file(s)",
                        summary.total_imported,
                    )
                    now = time.monotonic()
                    if now - _last_notice_at >= IMPORT_NOTICE_INTERVAL or summary.errors:
                        _last_notice_at = now
                        await notify_admins(
                            app.bot,
                            config,
                            summary.as_html(config.max_upload_mb),
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Folder watcher failed")
        await asyncio.sleep(config.import_interval)

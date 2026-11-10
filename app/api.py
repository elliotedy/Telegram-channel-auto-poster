"""Minimal JSON API for the Android manager app.

Disabled by default. Enable it with API_ENABLED=true in .env.
Set API_KEY and send it as the X-API-Key header from the app.

This uses only the standard library so no extra dependencies are needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .database import Database
from .utils import next_slot_utc, parse_schedule, safe_zoneinfo, to_iso

log = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


async def _db_call(db_path: Path, operation):
    db = Database(db_path)
    await db.connect()
    try:
        return await operation(db)
    finally:
        await db.close()


def _run(db_path: Path, operation):
    return asyncio.run(_db_call(db_path, operation))


class ApiState:
    def __init__(self, db_path: Path, api_key: str = "") -> None:
        self.db_path = db_path
        self.api_key = api_key


def make_handler(state: ApiState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoPosterAPI/1.0"

        def log_message(self, format, *args):  # noqa: A002
            log.debug("%s - %s", self.address_string(), format % args)

        def _send_json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not state.api_key:
                return True
            return self.headers.get("X-API-Key") == state.api_key

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def do_OPTIONS(self):
            self._send_json({"ok": True})

        def do_GET(self):
            if not self._authorized():
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/health":
                    self._send_json({"ok": True})
                elif path == "/api/status":
                    self._send_json(api_status(state.db_path))
                elif path == "/api/channels":
                    self._send_json(api_list_channels(state.db_path))
                elif path == "/api/media":
                    self._send_json(api_list_media(state.db_path))
                elif path == "/api/settings":
                    self._send_json(api_get_settings(state.db_path))
                else:
                    self._send_json({"ok": False, "error": "not found"}, 404)
            except Exception as exc:  # noqa: BLE001
                log.exception("API GET %s failed", path)
                self._send_json({"ok": False, "error": str(exc)}, 500)

        def do_POST(self):
            if not self._authorized():
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            path = urlparse(self.path).path
            payload = self._read_json()
            try:
                if path == "/api/channels":
                    self._send_json(api_add_channel(state.db_path, payload), 201)
                elif path == "/api/settings":
                    self._send_json(api_update_settings(state.db_path, payload))
                elif path.startswith("/api/channels/") and path.endswith("/toggle"):
                    chat_id = int(path.split("/")[3])
                    self._send_json(api_toggle_channel(state.db_path, chat_id, payload))
                else:
                    self._send_json({"ok": False, "error": "not found"}, 404)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                log.exception("API POST %s failed", path)
                self._send_json({"ok": False, "error": str(exc)}, 500)

        def do_DELETE(self):
            if not self._authorized():
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            path = urlparse(self.path).path
            try:
                if path.startswith("/api/channels/"):
                    chat_id = int(path.split("/")[3])
                    self._send_json(api_delete_channel(state.db_path, chat_id))
                else:
                    self._send_json({"ok": False, "error": "not found"}, 404)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                log.exception("API DELETE %s failed", path)
                self._send_json({"ok": False, "error": str(exc)}, 500)

    return Handler


def api_status(db_path: Path) -> dict:
    async def op(db: Database) -> dict:
        channels = await db.list_channels()
        media_count = await db.count_videos(active_only=True)
        schedule = await db.get_setting("schedule_times", "")
        timezone = await db.get_setting("timezone", "UTC")
        paused = await db.get_bool("paused")
        due = [dict(c) for c in channels if c["active"]]
        next_post = min(
            (c["next_post_at"] for c in due if c["next_post_at"]),
            default=None,
        )
        return {
            "ok": True,
            "channels": len(channels),
            "active_channels": len(due),
            "media": media_count,
            "schedule_times": schedule,
            "timezone": timezone,
            "paused": paused,
            "next_post_at": next_post,
        }

    return _run(db_path, op)


def api_list_channels(db_path: Path) -> dict:
    async def op(db: Database) -> dict:
        rows = await db.list_channels()
        return {"ok": True, "channels": [_row_to_dict(r) for r in rows]}

    return _run(db_path, op)


def api_add_channel(db_path: Path, payload: dict) -> dict:
    try:
        chat_id = int(payload["chat_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("chat_id must be an integer") from exc
    title = str(payload.get("title") or f"Channel {chat_id}").strip()
    username = payload.get("username")
    if username:
        username = str(username).lstrip("@").strip() or None

    async def op(db: Database) -> dict:
        schedule = await db.get_setting("schedule_times", "08:00,11:00,14:00,17:00,20:00,23:00")
        timezone = await db.get_setting("timezone", "UTC")
        next_at = to_iso(next_slot_utc(parse_schedule(schedule), safe_zoneinfo(timezone)))
        row, created = await db.add_channel(chat_id, title, username, next_at)
        return {"ok": True, "created": created, "channel": _row_to_dict(row)}

    return _run(db_path, op)


def api_toggle_channel(db_path: Path, chat_id: int, payload: dict) -> dict:
    active = bool(payload.get("active", True))

    async def op(db: Database) -> dict:
        await db.set_channel_active(chat_id, active)
        return {"ok": True, "chat_id": chat_id, "active": active}

    return _run(db_path, op)


def api_delete_channel(db_path: Path, chat_id: int) -> dict:
    async def op(db: Database) -> dict:
        deleted = await db.delete_channel(chat_id)
        return {"ok": True, "deleted": deleted}

    return _run(db_path, op)


def api_list_media(db_path: Path, limit: int = 50) -> dict:
    async def op(db: Database) -> dict:
        rows = await db.list_videos(limit=limit, offset=0)
        return {"ok": True, "media": [_row_to_dict(r) for r in rows]}

    return _run(db_path, op)


def api_get_settings(db_path: Path) -> dict:
    async def op(db: Database) -> dict:
        return {
            "ok": True,
            "schedule_times": await db.get_setting("schedule_times", ""),
            "timezone": await db.get_setting("timezone", "UTC"),
            "paused": await db.get_bool("paused"),
            "caption_template": await db.get_setting("caption_template", "{random_caption}"),
        }

    return _run(db_path, op)


def api_update_settings(db_path: Path, payload: dict) -> dict:
    async def op(db: Database) -> dict:
        if "schedule_times" in payload:
            times = parse_schedule(str(payload["schedule_times"]))
            await db.set_setting("schedule_times", ",".join(f"{h:02d}:{m:02d}" for h, m in times))
        if "timezone" in payload:
            tz = safe_zoneinfo(str(payload["timezone"]))
            await db.set_setting("timezone", tz.key)
        if "paused" in payload:
            await db.set_bool("paused", bool(payload["paused"]))
        if "caption_template" in payload:
            await db.set_setting("caption_template", str(payload["caption_template"]))

        schedule = await db.get_setting("schedule_times", "")
        timezone = await db.get_setting("timezone", "UTC")
        next_at = to_iso(next_slot_utc(parse_schedule(schedule), safe_zoneinfo(timezone)))
        await db.schedule_missing(next_at)
        return {
            "ok": True,
            "schedule_times": schedule,
            "timezone": timezone,
            "paused": await db.get_bool("paused"),
        }

    return _run(db_path, op)


def start_api_server(
    db_path: Path,
    host: str = "0.0.0.0",
    port: int = 8000,
    api_key: str = "",
) -> None:
    state = ApiState(db_path, api_key)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    log.info("Manager API listening on %s:%s", host, port)
    thread = threading.Thread(target=server.serve_forever, name="autoposter-api", daemon=True)
    thread.start()

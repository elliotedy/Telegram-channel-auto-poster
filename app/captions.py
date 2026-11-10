"""Rotating caption pool used for scheduled and manual posts.

Admins manage the pool with /captions, /addcaption and /delcaption. Entries
are stored as JSON in the ``caption_pool`` setting and are treated as trusted
HTML so links and formatting render in Telegram.
"""

from __future__ import annotations

import json
import logging
import random

from .database import Database

log = logging.getLogger(__name__)

CAPTION_POOL_KEY = "caption_pool"
MAX_CAPTIONS = 50
MAX_CAPTION_LENGTH = 1000

DEFAULT_CAPTIONS: list[str] = [
    (
        "✅ <b>OFFICIAL NETWORK UPDATE</b>\n\n"
        "Never miss a drop! All exclusive videos and updates are shared across our "
        "official network.\n\n"
        '👉 <a href="https://t.me/naijahotsg">Join Naija Hot</a>\n'
        '👉 <a href="https://t.me/freshsxxtv">Join Fresh S*x TV</a>\n'
        '👉 <a href="https://t.me/hotpornsupdate">Join Hot Updates</a>\n'
        '👉 <a href="https://t.me/notjustupdatexxx">Join Not Just Update</a>\n\n'
        "🔔 <i>Turn on post notifications!</i>"
    ),
    (
        "🚨 <b>HURRY UP &amp; SUBSCRIBE NOW!</b> 🚨\n\n"
        "Fresh video updates drop daily. Join our official channels before links "
        "expire:\n\n"
        '⚡ <a href="https://t.me/naijahotsg">Naija Hot</a>\n'
        '⚡ <a href="https://t.me/freshsxxtv">Fresh S*x TV</a>\n'
        '⚡ <a href="https://t.me/hotpornsupdate">Hot Updates</a>\n'
        '⚡ <a href="https://t.me/notjustupdatexxx">Not Just Update</a>\n\n'
        "👇 <i>Click any channel link to join!</i>"
    ),
    (
        "⚠️ <b>STAY CONNECTED – JOIN OUR BACKUPS</b> ⚠️\n\n"
        "To make sure you never lose access to updates, join our verified network:\n\n"
        '🔹 <a href="https://t.me/naijahotsg">Naija Hot Backup</a>\n'
        '🔹 <a href="https://t.me/freshsxxtv">Fresh S*x TV Backup</a>\n'
        '🔹 <a href="https://t.me/hotpornsupdate">Hot Updates Feed</a>\n'
        '🔹 <a href="https://t.me/notjustupdatexxx">Not Just Update Hub</a>\n\n'
        "📌 <i>Join every channel above to stay updated!</i>"
    ),
    (
        "🔥 <b>EXCLUSIVE DAILY DROPS</b>\n\n"
        "This is my official channel! Tap below to access the full catalog across "
        "all 4 channels:\n\n"
        '1️⃣ <a href="https://t.me/naijahotsg">Naija Hot Community</a>\n'
        '2️⃣ <a href="https://t.me/freshsxxtv">Fresh S*x TV VIP</a>\n'
        '3️⃣ <a href="https://t.me/hotpornsupdate">Hot Updates Channel</a>\n'
        '4️⃣ <a href="https://t.me/notjustupdatexxx">Not Just Update Base</a>\n\n'
        "🚀 <b>New videos dropping round the clock!</b>"
    ),
    (
        "👋 <b>Welcome! You're in the right place.</b>\n\n"
        "New videos and trending drops are added regularly. Jump into our active "
        "channels now:\n\n"
        '➡️ <a href="https://t.me/naijahotsg">@naijahotsg</a>\n'
        '➡️ <a href="https://t.me/freshsxxtv">@freshsxxtv</a>\n'
        '➡️ <a href="https://t.me/hotpornsupdate">@hotpornsupdate</a>\n'
        '➡️ <a href="https://t.me/notjustupdatexxx">@notjustupdatexxx</a>\n\n'
        "🔥 <i>Tap the links and join today!</i>"
    ),
]


def parse_pool(raw: str | None) -> list[str]:
    """Turn the stored JSON value into a clean list of captions."""
    if not raw:
        return list(DEFAULT_CAPTIONS)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("Setting %s is not valid JSON; using default captions", CAPTION_POOL_KEY)
        return list(DEFAULT_CAPTIONS)
    if not isinstance(data, list):
        log.warning("Setting %s is not a JSON list; using default captions", CAPTION_POOL_KEY)
        return list(DEFAULT_CAPTIONS)
    pool = [str(item).strip() for item in data if str(item).strip()]
    return pool or list(DEFAULT_CAPTIONS)


def dump_pool(pool: list[str]) -> str:
    return json.dumps(pool, ensure_ascii=False)


async def get_pool(db: Database) -> list[str]:
    return parse_pool(await db.get_setting(CAPTION_POOL_KEY))


async def save_pool(db: Database, pool: list[str]) -> None:
    await db.set_setting(CAPTION_POOL_KEY, dump_pool(pool))


def pick_caption(pool: list[str], rng: random.Random | None = None) -> str | None:
    """Choose one caption, or None when the pool is empty."""
    if not pool:
        return None
    return (rng or random).choice(pool)

"""Entry point for the Telegram channel auto poster."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import time
from contextlib import suppress

from telegram import Update
from telegram.ext import Application

from app.api import start_api_server
from app.captions import DEFAULT_CAPTIONS, dump_pool
from app.config import ROOT_DIR, Config
from app.constants import DEFAULT_CAPTION_TEMPLATE, DEFAULT_SCHEDULE
from app.database import Database
from app.handlers import BOT_COMMANDS, register_handlers
from app.importer import importer_loop, seed_folders_from_env
from app.notify import notify_admins
from app.poster import scheduler_loop
from app.relay import start_relay_thread
from app.utils import next_slot_utc, parse_schedule, safe_zoneinfo, to_iso

log = logging.getLogger(__name__)


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "bot.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        log.warning("Could not write logs/bot.log: %s", exc)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _upgrade_caption_settings(db: Database) -> None:
    """One-time switch from media-only captions to the rotating caption pool."""
    if await db.get_bool("captions_migrated"):
        return
    template = await db.get_setting("caption_template")
    if template in (None, "", "{caption}"):
        await db.set_setting("caption_template", DEFAULT_CAPTION_TEMPLATE)
    await db.set_bool("captions_migrated", True)


async def on_post_init(app: Application) -> None:
    config: Config = app.bot_data["config"]
    db: Database = app.bot_data["db"]

    await db.connect()
    await db.seed_settings(
        {
            "schedule_times": DEFAULT_SCHEDULE,
            "timezone": config.default_timezone,
            "caption_template": DEFAULT_CAPTION_TEMPLATE,
            "caption_pool": dump_pool(DEFAULT_CAPTIONS),
            "captions_migrated": "0",
            "paused": "0",
            "notify_posts": "0",
            "import_enabled": "1",
            "import_filename_caption": "1" if config.import_filename_caption else "0",
        }
    )
    await _upgrade_caption_settings(db)
    await seed_folders_from_env(db, config)

    times = parse_schedule(await db.get_setting("schedule_times"))
    tz = safe_zoneinfo(await db.get_setting("timezone"))
    await db.schedule_missing(to_iso(next_slot_utc(times, tz)))

    await app.bot.set_my_commands(BOT_COMMANDS)
    app.bot_data["started_at"] = time.monotonic()

    scheduler_task = asyncio.create_task(
        scheduler_loop(app, db, config),
        name="autoposter-scheduler",
    )
    app.bot_data["scheduler_task"] = scheduler_task
    importer_task = asyncio.create_task(
        importer_loop(app, db, config),
        name="autoposter-importer",
    )
    app.bot_data["importer_task"] = importer_task

    folders = await db.list_folders()
    log.info(
        "Auto poster is online. admins=%s, timezone=%s, folders=%s, database=%s",
        len(config.admin_ids),
        tz.key,
        len(folders),
        db.path,
    )

    if config.notify_startup:
        me = await app.bot.get_me()
        await notify_admins(
            app.bot,
            config,
            "✅ <b>Telegram Auto Poster is online.</b>\n"
            f"Bot: @{me.username}\n"
            f"Timezone: <b>{tz.key}</b>\n"
            f"Watched folders: <b>{len(folders)}</b>\n"
            "Use /start to open the control panel.",
        )


async def on_post_shutdown(app: Application) -> None:
    for key in ("scheduler_task", "importer_task"):
        task: asyncio.Task | None = app.bot_data.get(key)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
    db: Database | None = app.bot_data.get("db")
    if db is not None:
        await db.close()


def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    proxy = config.telegram_proxy
    if config.telegram_sni_relay:
        if proxy:
            log.warning(
                "Both TELEGRAM_PROXY and TELEGRAM_SNI_RELAY are set; using the local relay."
            )
        start_relay_thread(
            config.telegram_relay_host,
            config.telegram_relay_port,
            config.telegram_relay_upstream_host,
            config.telegram_relay_upstream_port,
        )
        proxy = f"http://{config.telegram_relay_host}:{config.telegram_relay_port}"

    builder = (
        Application.builder()
        .token(config.bot_token)
        .post_init(on_post_init)
        .post_shutdown(on_post_shutdown)
        .read_timeout(60)
        .write_timeout(600)
        .connect_timeout(30)
    )
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
        log.info("Using Telegram API proxy %s", proxy)

    db = Database(config.database_path)
    if config.api_enabled:
        start_api_server(
            config.database_path,
            host=config.api_host,
            port=config.api_port,
            api_key=config.api_key,
        )
    app = builder.build()
    app.bot_data["config"] = config
    app.bot_data["db"] = db
    app.bot_data["admin_ids"] = config.admin_ids
    register_handlers(app)

    log.info("Starting bot polling…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

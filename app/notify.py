"""Admin notifications."""

from __future__ import annotations

import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from .config import Config

log = logging.getLogger(__name__)


async def notify_admins(bot: Bot, config: Config, text: str) -> None:
    for admin_id in sorted(config.admin_ids):
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramError as exc:
            log.warning("Could not notify admin %s: %s", admin_id, exc)

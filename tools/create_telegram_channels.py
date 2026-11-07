#!/usr/bin/env python3
"""One-time Telegram userbot script: create autoposter channels and add the bot as admin.

This uses a *user* Telegram account through Telethon (MTProto), not the Bot API.
The normal bot (python-telegram-bot) cannot create channels, so this script is the
optional way to automate true channel creation / bot onboarding.

Run it on the machine where the user account is logged in:

    pip install -r requirements-userbot.txt
    python tools/create_telegram_channels.py

Required environment variables:
    TG_API_ID      - API ID from https://my.telegram.org
    TG_API_HASH    - API hash from https://my.telegram.org
    TG_PHONE       - your Telegram phone number, e.g. +15551234567
    TG_BOT_USERNAME - bot username that should be made admin, e.g. MyAutoPosterBot

The script can:
    1. Create new channels from `CHANNELS`.
    2. Add the autoposter bot as admin to existing channels from `ADD_BOT_TO`.

Security notes:
    - Do not commit the Telegram session file.
    - Prefer a dedicated account for bot/channel creation.
    - This is automation on a user account; use only for legitimate purposes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    EditPhotoRequest,
    GetFullChannelRequest,
    UpdateUsernameRequest,
)

log = logging.getLogger("tg-userbot")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


@dataclass(frozen=True)
class ChannelSpec:
    title: str
    about: str = ""
    username: str
    bot_username: str
    post_messages: bool = True
    photo: str | None = None


CHANNELS: list[ChannelSpec] = [
    # Add new-channel specs here, e.g.:
    # ChannelSpec(
    #     title="Telegram Auto Poster",
    #     about="Automated Telegram channel posts.",
    #     username="my_autoposter_channel",
    #     bot_username="my_autoposter_bot",
    #     photo="C:/path/to/channel-photo.jpg",
    # ),
]

# Existing channels where the autoposter bot should be added as admin.
# Public usernames and/or private invite links are accepted.
ADD_BOT_TO: list[str] = [
    "t.me/+IQjLAcxBMxlkNzRk",
    "t.me/+rWiHwFPWsJdkNjM0",
    "t.me/naijahotsg",
    "t.me/+Gd2DnzU6vVhlYzc0",
    "t.me/freshsxxtv",
]


async def create_and_configure(client, spec: ChannelSpec) -> None:
    log.info("Creating channel %r ...", spec.title)
    result = await client(
        CreateChannelRequest(
            title=spec.title,
            about=spec.about,
            broadcast=True,
        )
    )
    log.info("CreateChannel result: %s", result)

    channel = await client.get_input_entity(spec.username)
    await client(UpdateUsernameRequest(channel, spec.username, True))
    log.info("Set channel username to @%s", spec.username)

    bot = await client.get_input_entity(spec.bot_username)
    await client.edit_admin(
        channel,
        bot,
        post_messages=spec.post_messages,
    )
    log.info("Added bot @%s as admin (post_messages=%s).", spec.bot_username, spec.post_messages)

    if spec.photo:
        log.info("Setting channel photo from %s ...", spec.photo)
        photo = await client.upload_file(spec.photo)
        await client(EditPhotoRequest(channel, photo))
        log.info("Channel photo updated.")

    details = await client(GetFullChannelRequest(channel))
    log.info(
        "Channel details: title=%r username=%r chat_id=%s about=%r",
        details.full_chat.title,
        details.full_chat.username,
        details.full_chat.id,
        details.full_chat.about,
    )


async def add_bot_to_target(client, target: str, bot_username: str, post_messages: bool = True) -> None:
    log.info("Adding bot @%s to %s ...", bot_username, target)
    entity = await client.get_input_entity(target)
    bot = await client.get_input_entity(bot_username)
    await client.edit_admin(
        entity,
        bot,
        post_messages=post_messages,
    )
    log.info("Bot @%s added to %s with post_messages=%s.", bot_username, target, post_messages)


async def main() -> None:
    api_id = int(os.environ.get("TG_API_ID") or 0)
    api_hash = os.environ.get("TG_API_HASH") or ""
    if not api_id or not api_hash:
        raise RuntimeError("TG_API_ID and TG_API_HASH must be set (from https://my.telegram.org).")

    bot_username = os.environ.get("TG_BOT_USERNAME") or "autoposter"

    client = TelegramClient(
        session="userbot",
        api_id=api_id,
        api_hash=api_hash,
    )
    await client.start()

    try:
        for spec in CHANNELS:
            await create_and_configure(client, spec)

        for target in ADD_BOT_TO:
            await add_bot_to_target(client, target, bot_username)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

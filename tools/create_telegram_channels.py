#!/usr/bin/env python3
"""One-time Telegram userbot helper: create a bot, create channels, and add the bot as admin.

This uses a *user* Telegram account through Telethon (MTProto), not the Bot API.
The normal bot (python-telegram-bot) cannot create bots or channels, so this script
is the optional full-automation path.

Run it on the machine where the user account is logged in:

    pip install -r requirements-userbot.txt
    python tools/create_telegram_channels.py --create-bot

What it can do:
    1. Create a new bot by talking to @BotFather for you.
    2. Save the new token and your numeric admin ID to `.env`.
    3. Create channels from `CHANNELS`.
    4. Add the bot as administrator to the existing channels in `ADD_BOT_TO`.
    5. Optionally register those channels in `data/autoposter.db` (or print
       `/addchannel` commands if the database does not exist yet).

Required environment variables (can live in `.env`):
    TG_API_ID       - API ID from https://my.telegram.org
    TG_API_HASH     - API hash from https://my.telegram.org
    TG_PHONE        - your Telegram phone number, e.g. +15551234567
                      (optional after the first login; Telethon will prompt otherwise)

Optional environment variables:
    BOT_NAME        - display name for the new bot (default: Telegram Auto Poster)
    TG_BOT_USERNAME - desired bot username, must end in "bot"
                      (default: autoposter_bot)
    CREATE_BOT      - "true" to create a bot without the --create-bot flag
    WRITE_ENV       - "true" to save the token/admin ID to `.env`
    CREATE_CHANNELS - "true" to create channels from `CHANNELS`
    REGISTER_DB     - "true" to insert channels into the autoposter database

Security notes:
    - Do not commit the Telegram session file, `.env`, or your API hash.
    - Prefer a dedicated account for bot/channel creation.
    - Automating @BotFather is not an official Bot API feature and may be
      rate-limited. Use it only on an account you control.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"
SESSION_PATH = ROOT_DIR / "data" / "userbot"

# Running `python tools/...` puts tools/ on sys.path, not the project root.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements-userbot.txt installs it
    load_dotenv = None

try:
    from telethon import TelegramClient, errors, utils
    from telethon.tl.functions.channels import (
        CreateChannelRequest,
        EditPhotoRequest,
        GetFullChannelRequest,
        UpdateUsernameRequest,
    )
    from telethon.tl.functions.messages import CheckChatInviteRequest
    from telethon.tl.types import Channel, ChatInviteAlready
except ImportError as exc:  # pragma: no cover - shown to the user at runtime
    raise SystemExit("Telethon is not installed. Run:\n    pip install -r requirements-userbot.txt") from exc

from app.constants import DEFAULT_SCHEDULE  # noqa: E402
from app.utils import (  # noqa: E402
    next_slot_utc,
    parse_schedule,
    safe_zoneinfo,
    to_iso,
)

log = logging.getLogger("tg-userbot")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

BOTFATHER = "BotFather"
TOKEN_RE = re.compile(r"\b(\d{6,12}:[A-Za-z0-9_-]{30,})\b")
INVITE_LINK_RE = re.compile(
    r"(?:t(?:elegram)?\.me)/(?:\+|joinchat/)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BotSpec:
    name: str
    username: str


@dataclass(frozen=True)
class ChannelSpec:
    title: str
    username: str
    about: str = ""
    photo: str | None = None
    bot_username: str | None = None
    post_messages: bool = True


@dataclass(frozen=True)
class ChannelInfo:
    chat_id: int
    title: str
    username: str | None


# New channels to create. Leave empty if you only want to add the bot to
# the existing channels in ADD_BOT_TO.
CHANNELS: list[ChannelSpec] = [
    # ChannelSpec(
    #     title="Telegram Auto Poster",
    #     username="my_autoposter_channel",
    #     about="Automated Telegram channel posts.",
    #     photo="C:/path/to/channel-photo.jpg",
    # ),
]

# Existing channels where the autoposter bot should be added as admin.
# Public usernames and private invite links are accepted. Your user account
# must already be a member/administrator of private channels.
ADD_BOT_TO: list[str] = [
    "t.me/+IQjLAcxBMxlkNzRk",
    "t.me/+rWiHwFPWsJdkNjM0",
    "t.me/naijahotsg",
    "t.me/+Gd2DnzU6vVhlYzc0",
    "t.me/freshsxxtv",
]


class BotFatherError(RuntimeError):
    """Raised when the @BotFather conversation fails."""


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_valid_bot_username(username: str) -> bool:
    """Bot usernames are 5-32 chars and must end with "bot"."""
    return (
        5 <= len(username) <= 32
        and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", username))
        and username.lower().endswith("bot")
    )


def parse_bot_username(raw: str) -> str:
    value = (raw or "").strip()
    value = re.sub(
        r"^(?:https?://)?(?:t(?:elegram)?\.me)/",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = value.split("?", 1)[0].rstrip("/")
    value = value.lstrip("@").strip()
    if not is_valid_bot_username(value):
        raise ValueError(
            f"Invalid bot username {value!r}. Bot usernames must be 5-32 characters, "
            "start with a letter, use only letters/numbers/underscores, and end in 'bot'. "
            "Example: autoposter_bot"
        )
    return value


def parse_invite_hash(value: str) -> str | None:
    """Return the invite hash from t.me/+hash or t.me/joinchat/hash."""
    match = INVITE_LINK_RE.search(value or "")
    if match:
        return match.group(1)
    stripped = (value or "").strip()
    if stripped.startswith("+") and len(stripped) > 1:
        return stripped[1:]
    return None


def extract_token(text: str) -> str | None:
    match = TOKEN_RE.search(text or "")
    return match.group(1) if match else None


def mask_token(token: str) -> str:
    return f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"


def username_taken(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        phrase in lower
        for phrase in (
            "already taken",
            "already in use",
            "username is occupied",
            "is already occupied",
        )
    )


def username_invalid(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        phrase in lower
        for phrase in (
            "username is invalid",
            "invalid username",
            "can't be used",
            "cannot be used",
            "please choose another",
        )
    )


def candidate_username(base: str, attempt: int) -> str:
    """Generate a valid username if the desired one is taken."""
    stem = base[:-3] if base.lower().endswith("bot") else base
    stem = stem.rstrip("_") or "autoposter"
    for _ in range(20):
        suffix = f"_{random.randint(1000, 9999)}_bot"
        candidate = f"{stem[: 32 - len(suffix)]}{suffix}"
        if is_valid_bot_username(candidate):
            return candidate
    return f"autoposter_{random.randint(1000, 9999)}_bot"


async def expect_botfather(
    conversation,
    patterns: tuple[str, ...],
    *,
    step: str,
    timeout: float = 45,
) -> str:
    """Read BotFather messages until one matches any regex in ``patterns``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen: list[str] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            detail = "\n".join(seen[-3:]) or "no response"
            raise BotFatherError(f"Timed out waiting for BotFather during {step}: {detail}")

        try:
            message = await conversation.get_response(timeout=remaining)
        except TimeoutError as exc:
            detail = "\n".join(seen[-3:]) or "no response"
            raise BotFatherError(f"Timed out waiting for BotFather during {step}: {detail}") from exc

        text = (message.raw_text or "").strip()
        if not text:
            continue
        log.debug("BotFather: %s", text)
        seen.append(text)
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return text


async def collect_botfather(
    conversation,
    *,
    stop,
    step: str,
    timeout: float = 45,
) -> str:
    """Collect BotFather messages until ``stop`` returns True."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen: list[str] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            message = await conversation.get_response(timeout=remaining)
        except TimeoutError:
            break

        text = (message.raw_text or "").strip()
        if not text:
            continue
        log.debug("BotFather: %s", text)
        seen.append(text)
        joined = "\n".join(seen)
        if stop(joined):
            return joined

    detail = "\n".join(seen[-3:]) or "no response"
    raise BotFatherError(f"Unexpected response from BotFather during {step}: {detail}")


async def drain_botfather(conversation, *, timeout: float = 1.0) -> None:
    """Discard any immediate follow-up messages after a failed username attempt."""
    while True:
        try:
            await conversation.get_response(timeout=timeout)
        except TimeoutError:
            return


async def create_bot_via_botfather(client: TelegramClient, spec: BotSpec) -> tuple[str, str]:
    """Talk to @BotFather and return (token, username)."""
    desired = parse_bot_username(spec.username)
    display_name = (spec.name or "Telegram Auto Poster").strip() or "Telegram Auto Poster"

    log.info("Asking @BotFather to create bot %r (@%s) ...", display_name, desired)
    async with client.conversation(BOTFATHER, timeout=45, total_timeout=240) as conversation:
        await conversation.send_message("/newbot")
        await expect_botfather(
            conversation,
            (r"name for your bot", r"call it", r"choose a name"),
            step="name prompt",
        )

        await conversation.send_message(display_name)
        await expect_botfather(
            conversation,
            (r"choose a username", r"must end in", r"username for your bot"),
            step="username prompt",
        )

        candidate = desired
        for attempt in range(1, 6):
            await conversation.send_message(candidate)
            text = await collect_botfather(
                conversation,
                stop=lambda value: (
                    bool(extract_token(value)) or username_taken(value) or username_invalid(value)
                ),
                step=f"username attempt {attempt}",
            )

            token = extract_token(text)
            if token:
                log.info("Bot created: @%s", candidate)
                return token, candidate

            if username_taken(text):
                candidate = candidate_username(desired, attempt)
                log.warning("Username was taken; trying @%s instead.", candidate)
                await drain_botfather(conversation, timeout=0.5)
                continue

            if username_invalid(text):
                candidate = candidate_username(desired, attempt)
                log.warning("Username was rejected; trying @%s instead.", candidate)
                await drain_botfather(conversation, timeout=0.5)
                continue

            break

    raise BotFatherError(
        "Could not create the bot after 5 username attempts. "
        "Open @BotFather manually, check for a pending /newbot conversation, and try again."
    )


def get_bot_username_from_token(token: str) -> str:
    """Validate a bot token with the Bot API and return the bot username."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getMe",
        headers={"User-Agent": "telegram-autoposter-setup/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram Bot API rejected the token (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not reach Telegram Bot API to verify the bot token.") from exc

    if not payload.get("ok"):
        raise RuntimeError("Telegram Bot API error: " + str(payload.get("description") or "unknown error"))
    return str(payload["result"]["username"])


def update_env(values: dict[str, str], path: Path = ENV_PATH) -> None:
    """Create/update .env without touching unrelated lines."""
    if not path.exists() and ENV_EXAMPLE_PATH.exists():
        path.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for key, value in values.items():
        prefix = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for index, line in enumerate(lines):
            if prefix.match(line):
                lines[index] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def resolve_database_path() -> Path:
    raw = (os.getenv("DATABASE_PATH") or "data/autoposter.db").strip()
    path = Path(raw)
    return path if path.is_absolute() else ROOT_DIR / path


def register_channels_in_database(channels: list[ChannelInfo]) -> tuple[int, int]:
    """Insert/refresh channels in the autoposter SQLite DB.

    Returns (inserted, updated). If the database or schema does not exist yet,
    returns (0, 0) and the caller prints manual /addchannel commands instead.
    """
    if not channels:
        return 0, 0

    db_path = resolve_database_path()
    if not db_path.exists():
        return 0, 0

    try:
        connection = sqlite3.connect(db_path)
    except sqlite3.Error:
        return 0, 0

    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'channels'"
        ).fetchone()
        if table is None:
            return 0, 0

        settings: dict[str, str] = {}
        settings_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
        ).fetchone()
        if settings_table is not None:
            settings = dict(connection.execute("SELECT key, value FROM settings").fetchall())

        schedule_raw = settings.get("schedule_times") or DEFAULT_SCHEDULE
        timezone_name = settings.get("timezone") or os.getenv("TIMEZONE") or "UTC"
        try:
            times = parse_schedule(schedule_raw)
            next_post_at = to_iso(next_slot_utc(times, safe_zoneinfo(timezone_name)))
        except (ValueError, RuntimeError):
            next_post_at = None

        now = datetime.now(UTC).isoformat(timespec="seconds")
        inserted = 0
        updated = 0
        for channel in channels:
            existing = connection.execute(
                "SELECT 1 FROM channels WHERE chat_id = ?", (channel.chat_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO channels
                        (chat_id, title, username, active, next_post_at, created_at)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (
                        channel.chat_id,
                        channel.title,
                        channel.username,
                        next_post_at,
                        now,
                    ),
                )
                inserted += 1
            else:
                connection.execute(
                    "UPDATE channels SET title = ?, username = ? WHERE chat_id = ?",
                    (channel.title, channel.username, channel.chat_id),
                )
                updated += 1
        connection.commit()
        return inserted, updated
    except sqlite3.Error as exc:
        log.warning("Could not register channels in %s: %s", db_path, exc)
        return 0, 0
    finally:
        connection.close()


async def resolve_bot(
    client: TelegramClient,
    username: str,
    *,
    attempts: int = 5,
):
    """Resolve a bot username, retrying briefly after BotFather creates it."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await client.get_input_entity(username)
        except (ValueError, errors.UsernameNotOccupiedError) as exc:
            last_error = exc
            if attempt < attempts:
                log.info("Bot @%s is not resolvable yet; retrying in 2s ...", username)
                await asyncio.sleep(2)
    raise RuntimeError(
        f"Could not resolve bot @{username} after {attempts} attempts."
    ) from last_error


async def resolve_channel(client: TelegramClient, target: str) -> Channel:
    """Resolve a public channel username or private invite link to a Channel."""
    invite_hash = parse_invite_hash(target)
    if invite_hash:
        try:
            invite = await client(CheckChatInviteRequest(invite_hash))
        except errors.InviteHashExpiredError as exc:
            raise RuntimeError(
                f"Invite link expired: {target}. Ask the channel owner for a new link."
            ) from exc
        except errors.InviteHashInvalidError as exc:
            raise RuntimeError(f"Invalid invite link: {target}.") from exc

        if isinstance(invite, ChatInviteAlready):
            chat = invite.chat
            if isinstance(chat, Channel):
                return chat
            raise RuntimeError(f"{target} is a group chat, not a channel.")

        raise RuntimeError(
            f"Your user account is not a member of {target}. "
            "Open the invite link, join the channel, make sure your account is an "
            "administrator, then run this script again."
        )

    try:
        entity = await client.get_entity(target)
    except (ValueError, errors.UsernameNotOccupiedError) as exc:
        raise RuntimeError(f"Could not find channel: {target}.") from exc

    if not isinstance(entity, Channel):
        raise RuntimeError(f"{target} is not a Telegram channel.")
    return entity


def channel_info(channel: Channel, *, username: str | None = None) -> ChannelInfo:
    return ChannelInfo(
        chat_id=utils.get_peer_id(channel),
        title=channel.title or str(utils.get_peer_id(channel)),
        username=username if username is not None else getattr(channel, "username", None),
    )


async def add_bot_to_target(
    client: TelegramClient,
    target: str,
    bot_username: str,
    post_messages: bool = True,
) -> ChannelInfo:
    log.info("Adding @%s to %s ...", bot_username, target)
    channel = await resolve_channel(client, target)
    bot = await resolve_bot(client, bot_username)
    if getattr(channel, "megagroup", False):
        await client.edit_admin(channel, bot, is_admin=True)
    else:
        await client.edit_admin(channel, bot, post_messages=post_messages)
    info = channel_info(channel)
    log.info(
        "Bot @%s added to %r (chat_id=%s, username=%s).",
        bot_username,
        info.title,
        info.chat_id,
        f"@{info.username}" if info.username else "private",
    )
    return info


async def create_and_configure_channel(
    client: TelegramClient,
    spec: ChannelSpec,
    bot_username: str,
) -> ChannelInfo:
    log.info("Creating channel %r (@%s) ...", spec.title, spec.username)
    result = await client(
        CreateChannelRequest(
            title=spec.title,
            about=spec.about,
            broadcast=True,
        )
    )
    channel = next(
        (chat for chat in getattr(result, "chats", []) if isinstance(chat, Channel)),
        None,
    )
    if channel is None:
        raise RuntimeError(f"Telegram did not return the new channel for {spec.title!r}.")

    channel_username = spec.username.lstrip("@")
    try:
        await client(UpdateUsernameRequest(channel, channel_username))
        channel.username = channel_username
        log.info("Set channel username to @%s.", channel_username)
    except errors.UsernameOccupiedError:
        log.error("Channel created, but @%s is already taken.", channel_username)
        channel_username = None
    except errors.UsernameInvalidError:
        log.error("Channel created, but @%s is not a valid username.", channel_username)
        channel_username = None

    if spec.photo:
        photo_path = Path(spec.photo)
        if photo_path.exists():
            log.info("Setting channel photo from %s ...", photo_path)
            uploaded = await client.upload_file(str(photo_path))
            await client(EditPhotoRequest(channel, uploaded))
            log.info("Channel photo updated.")
        else:
            log.warning("Channel photo not found, skipping: %s", photo_path)

    target_bot = spec.bot_username or bot_username
    if target_bot:
        bot = await resolve_bot(client, target_bot)
        if getattr(channel, "megagroup", False):
            await client.edit_admin(channel, bot, is_admin=True)
        else:
            await client.edit_admin(channel, bot, post_messages=spec.post_messages)
        log.info("Added @%s as channel admin.", target_bot)

    details = await client(GetFullChannelRequest(channel))
    log.info(
        "Channel details: title=%r username=%s chat_id=%s about=%r",
        channel.title,
        f"@{channel_username}" if channel_username else "private",
        utils.get_peer_id(channel),
        getattr(details.full_chat, "about", None),
    )
    return channel_info(channel, username=channel_username)


async def async_main(args: argparse.Namespace) -> int:
    if load_dotenv is not None:
        load_dotenv(ENV_PATH)

    api_id_raw = (os.getenv("TG_API_ID") or "").strip()
    api_hash = (os.getenv("TG_API_HASH") or "").strip()
    if not api_id_raw or not api_hash:
        raise SystemExit(
            "TG_API_ID and TG_API_HASH must be set (from https://my.telegram.org).\n"
            "You can put them in .env or export them in your shell."
        )
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise SystemExit("TG_API_ID must be a number.") from exc

    phone = (os.getenv("TG_PHONE") or "").strip() or None
    create_bot = args.create_bot or env_bool("CREATE_BOT")
    write_env = (args.write_env or create_bot or env_bool("WRITE_ENV")) and not args.no_write_env
    create_channels = args.create_channels or env_bool("CREATE_CHANNELS")
    register_db = not args.no_register_db and (args.register_db or env_bool("REGISTER_DB", True))

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    bot_username_raw = (os.getenv("TG_BOT_USERNAME") or os.getenv("BOT_USERNAME") or "").strip()

    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION_PATH), api_id, api_hash)
    await client.start(phone=phone)

    try:
        me = await client.get_me()
        log.info("Logged in as %s (id=%s).", me.first_name or "user", me.id)

        if create_bot:
            desired_username = bot_username_raw or "autoposter_bot"
            token, created_username = await create_bot_via_botfather(
                client,
                BotSpec(
                    name=(os.getenv("BOT_NAME") or "Telegram Auto Poster").strip(),
                    username=desired_username,
                ),
            )
            log.info("New bot token: %s", mask_token(token))
            try:
                bot_username = await asyncio.to_thread(get_bot_username_from_token, token)
            except RuntimeError as exc:
                log.warning("Could not verify the new token: %s", exc)
                bot_username = parse_bot_username(created_username)
            log.info("Bot username: @%s", bot_username)
        else:
            if bot_username_raw:
                try:
                    bot_username = parse_bot_username(bot_username_raw)
                except ValueError as exc:
                    raise SystemExit(str(exc)) from exc
            elif token:
                try:
                    bot_username = await asyncio.to_thread(get_bot_username_from_token, token)
                except RuntimeError as exc:
                    raise SystemExit(
                        f"Could not determine the bot username from the token: {exc}\n"
                        "Set TG_BOT_USERNAME in .env or pass it in the environment."
                    ) from exc
            else:
                raise SystemExit(
                    "No bot configured. Run with --create-bot, or set TELEGRAM_BOT_TOKEN "
                    "and TG_BOT_USERNAME in .env."
                )

        if write_env and token:
            values = {"TELEGRAM_BOT_TOKEN": token}
            if not (os.getenv("ADMIN_IDS") or "").strip():
                values["ADMIN_IDS"] = str(me.id)
            values["TG_BOT_USERNAME"] = bot_username
            if not (os.getenv("TG_API_ID") or "").strip():
                values["TG_API_ID"] = api_id_raw
            if not (os.getenv("TG_API_HASH") or "").strip():
                values["TG_API_HASH"] = api_hash
            update_env(values)
            log.info("Saved bot token and admin ID to %s.", ENV_PATH)
        elif token:
            print("\nBot token:")
            print(token)
            print("\nAdd this to your .env file as TELEGRAM_BOT_TOKEN if it is not there yet.\n")

        all_channels: list[ChannelInfo] = []
        failures: list[str] = []

        if create_channels:
            if not CHANNELS:
                log.info("No channels configured in CHANNELS; skipping channel creation.")
            for spec in CHANNELS:
                try:
                    info = await create_and_configure_channel(client, spec, bot_username)
                    all_channels.append(info)
                except Exception as exc:  # noqa: BLE001 - report and continue
                    log.error("Could not create %r: %s", spec.title, exc)
                    failures.append(f"create channel {spec.title!r}: {exc}")

        if not args.no_add_to_channels:
            if not ADD_BOT_TO:
                log.info("ADD_BOT_TO is empty; no existing channels to update.")
            for target in ADD_BOT_TO:
                try:
                    info = await add_bot_to_target(client, target, bot_username)
                    all_channels.append(info)
                except Exception as exc:  # noqa: BLE001 - report and continue
                    log.error("Could not add the bot to %s: %s", target, exc)
                    failures.append(f"add bot to {target}: {exc}")

        # Deduplicate by chat_id while keeping order.
        seen: set[int] = set()
        unique_channels: list[ChannelInfo] = []
        for info in all_channels:
            if info.chat_id not in seen:
                seen.add(info.chat_id)
                unique_channels.append(info)

        registration_ok = False
        if register_db and unique_channels:
            inserted, updated = register_channels_in_database(unique_channels)
            registration_ok = bool(inserted or updated)
            if registration_ok:
                log.info(
                    "Registered channels in %s: %s new, %s refreshed. "
                    "Restart the autoposter if it is already running.",
                    resolve_database_path(),
                    inserted,
                    updated,
                )
            else:
                log.warning(
                    "Database not found/schema not initialized at %s; use the /addchannel commands below.",
                    resolve_database_path(),
                )

        if unique_channels:
            print("\nChannels ready for the autoposter:")
            for info in unique_channels:
                print(
                    f"  {info.title} | chat_id={info.chat_id} | "
                    f"{'@' + info.username if info.username else 'private'}"
                )
            if not registration_ok:
                print("\nSend these to the bot once it is running:")
                for info in unique_channels:
                    print(f"  /addchannel {info.chat_id}")

        if failures:
            print("\nSome steps failed:")
            for failure in failures:
                print(f"  - {failure}")
            return 1

        print("\nDone.")
        return 0
    finally:
        await client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Telegram bot through @BotFather, create channels, and add "
            "the bot as administrator using a user account."
        )
    )
    parser.add_argument(
        "--create-bot",
        action="store_true",
        help="create a new bot through @BotFather",
    )
    parser.add_argument(
        "--no-write-env",
        action="store_true",
        help="do not save the bot token/admin ID to .env",
    )
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="save the existing bot token/admin ID to .env",
    )
    parser.add_argument(
        "--create-channels",
        action="store_true",
        help="create the channels listed in CHANNELS",
    )
    parser.add_argument(
        "--no-add-to-channels",
        action="store_true",
        help="skip adding the bot to ADD_BOT_TO",
    )
    parser.add_argument(
        "--register-db",
        action="store_true",
        help="insert/refresh channels in data/autoposter.db if it exists (default)",
    )
    parser.add_argument(
        "--no-register-db",
        action="store_true",
        help="do not insert channels into the autoposter database",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

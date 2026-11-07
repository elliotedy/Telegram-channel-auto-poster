"""Local HTTP CONNECT relay for networks that filter Telegram's API hostname.

Some ISPs drop TLS connections whose SNI is ``api.telegram.org``. This relay
accepts a standard HTTP ``CONNECT`` request from the bot, connects to a known
Telegram API IP, and splits the first TLS ClientHello across two TLS records so
that simple SNI-based filters cannot match the hostname. Certificate validation
still happens end-to-end in the bot: the relay is a raw TCP tunnel and never
sees plaintext.

The relay binds to localhost only and only accepts ``CONNECT`` requests for
``api.telegram.org``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from typing import Final

log = logging.getLogger(__name__)

ALLOWED_TARGET: Final = "api.telegram.org"
DEFAULT_UPSTREAM_HOST: Final = "149.154.166.110"
DEFAULT_UPSTREAM_PORT: Final = 443
DEFAULT_LISTEN_HOST: Final = "127.0.0.1"
DEFAULT_LISTEN_PORT: Final = 8443


def split_client_hello(record: bytes) -> tuple[bytes, bytes] | None:
    """Split one TLS ClientHello record into two valid TLS records.

    The split is placed inside the SNI hostname when possible, so a filter that
    only inspects a single record cannot see the full hostname. Returns ``None``
    when *record* is not a TLS ClientHello.
    """
    if len(record) < 6 or record[0] != 0x16:  # handshake
        return None

    header, payload = record[:5], record[5:]
    if len(payload) < 4 or payload[0] != 0x01:  # ClientHello
        return None

    split_in_body: int | None = None
    try:
        body = payload[4:]
        pos = 2 + 32  # legacy_version + random
        pos += 1 + body[pos]  # session_id
        pos += 2 + int.from_bytes(body[pos : pos + 2], "big")  # cipher_suites
        pos += 1 + body[pos]  # compression_methods
        ext_end = pos + 2 + int.from_bytes(body[pos : pos + 2], "big")
        pos += 2
        while pos + 4 <= ext_end:
            ext_type = int.from_bytes(body[pos : pos + 2], "big")
            ext_len = int.from_bytes(body[pos + 2 : pos + 4], "big")
            if ext_type == 0x0000 and ext_len >= 5:  # server_name
                name_len = int.from_bytes(body[pos + 7 : pos + 9], "big")
                name_start = pos + 9
                host = body[name_start : name_start + name_len]
                log.debug("splitting ClientHello for SNI %r", host.decode("ascii", "replace"))
                split_in_body = name_start + max(1, name_len // 2)
                break
            pos += 4 + ext_len
    except (IndexError, ValueError):
        split_in_body = None

    split_at = 4 + (split_in_body if split_in_body is not None else len(body) // 2)
    split_at = max(1, min(len(payload) - 1, split_at))

    def wrap(part: bytes) -> bytes:
        return header[0:1] + header[1:3] + len(part).to_bytes(2, "big") + part

    return wrap(payload[:split_at]), wrap(payload[split_at:])


def _parse_target(request_line: bytes) -> tuple[str, int] | None:
    parts = request_line.split()
    if len(parts) < 2:
        return None
    target = parts[1].decode("ascii", "replace")
    host, separator, port = target.rpartition(":")
    if not separator or not host:
        return None
    try:
        return host, int(port)
    except ValueError:
        return None


async def _relay(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
) -> None:
    peer = client_writer.get_extra_info("peername")
    try:
        request_line = await asyncio.wait_for(client_reader.readline(), timeout=15)
        target = _parse_target(request_line)
        if not request_line.startswith(b"CONNECT ") or target is None:
            client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await client_writer.drain()
            return
        if target[0].lower() != ALLOWED_TARGET:
            client_writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            await client_writer.drain()
            return

        # Drain the remaining proxy request headers.
        while True:
            line = await asyncio.wait_for(client_reader.readline(), timeout=5)
            if line in (b"\r\n", b"\n", b""):
                break

        upstream_reader, upstream_writer = await asyncio.open_connection(
            upstream_host, upstream_port
        )
        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        try:
            header = await asyncio.wait_for(client_reader.readexactly(5), timeout=15)
            length = int.from_bytes(header[3:5], "big")
            if 0 < length <= 65535:
                payload = await asyncio.wait_for(
                    client_reader.readexactly(length), timeout=15
                )
                record = header + payload
                parts = split_client_hello(record)
                if parts:
                    upstream_writer.write(parts[0])
                    await upstream_writer.drain()
                    await asyncio.sleep(0.05)
                    upstream_writer.write(parts[1])
                else:
                    upstream_writer.write(record)
                await upstream_writer.drain()
            else:
                upstream_writer.write(header)
                await upstream_writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError):
            pass

        log.debug("tunnel open %s", peer)
        await asyncio.gather(
            _relay(client_reader, upstream_writer),
            _relay(upstream_reader, client_writer),
        )
        log.debug("tunnel closed %s", peer)
    except Exception as exc:  # noqa: BLE001
        log.warning("relay connection from %s failed: %s", peer, exc)
    finally:
        try:
            client_writer.close()
        except OSError:
            pass


async def serve_forever(
    listen_host: str = DEFAULT_LISTEN_HOST,
    listen_port: int = DEFAULT_LISTEN_PORT,
    upstream_host: str = DEFAULT_UPSTREAM_HOST,
    upstream_port: int = DEFAULT_UPSTREAM_PORT,
) -> None:
    """Serve the relay until the current task is cancelled."""
    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(
            reader, writer, upstream_host, upstream_port
        ),
        listen_host,
        listen_port,
    )
    log.info(
        "Telegram SNI relay listening on %s:%s -> %s:%s",
        listen_host,
        listen_port,
        upstream_host,
        upstream_port,
    )
    async with server:
        await server.serve_forever()


def start_relay_thread(
    listen_host: str = DEFAULT_LISTEN_HOST,
    listen_port: int = DEFAULT_LISTEN_PORT,
    upstream_host: str = DEFAULT_UPSTREAM_HOST,
    upstream_port: int = DEFAULT_UPSTREAM_PORT,
    timeout: float = 10.0,
) -> None:
    """Start the relay in a daemon thread and wait until it is listening."""
    ready = threading.Event()
    startup_error: list[BaseException] = []

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            server = loop.run_until_complete(
                asyncio.start_server(
                    lambda reader, writer: _handle_client(
                        reader, writer, upstream_host, upstream_port
                    ),
                    listen_host,
                    listen_port,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            startup_error.append(exc)
            ready.set()
            loop.close()
            return
        ready.set()
        try:
            loop.run_forever()
        finally:
            server.close()
            loop.run_until_complete(server.wait_closed())
            loop.close()

    thread = threading.Thread(target=runner, name="telegram-sni-relay", daemon=True)
    thread.start()
    if not ready.wait(timeout):
        raise RuntimeError("Timed out while starting the local Telegram relay")
    if startup_error:
        raise RuntimeError(
            f"Could not start the local Telegram relay on {listen_host}:{listen_port}"
        ) from startup_error[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Local SNI relay for the Telegram Bot API")
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--upstream-host", default=DEFAULT_UPSTREAM_HOST)
    parser.add_argument("--upstream-port", type=int, default=DEFAULT_UPSTREAM_PORT)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    try:
        asyncio.run(
            serve_forever(
                args.listen_host,
                args.listen_port,
                args.upstream_host,
                args.upstream_port,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

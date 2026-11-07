from __future__ import annotations

from app.relay import split_client_hello


def _client_hello(host: str) -> bytes:
    name = host.encode("ascii")
    server_name = (
        (len(name) + 3).to_bytes(2, "big")
        + b"\x00"
        + len(name).to_bytes(2, "big")
        + name
    )
    extension = b"\x00\x00" + len(server_name).to_bytes(2, "big") + server_name
    body = (
        b"\x03\x03"
        + b"\x00" * 32
        + b"\x00"
        + b"\x00\x02\x00\x2f"
        + b"\x01\x00"
        + len(extension).to_bytes(2, "big")
        + extension
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


def test_split_client_hello_hides_sni_and_keeps_payload() -> None:
    record = _client_hello("api.telegram.org")
    parts = split_client_hello(record)

    assert parts is not None
    first, second = parts
    assert first[0] == 0x16 and second[0] == 0x16
    assert int.from_bytes(first[3:5], "big") == len(first) - 5
    assert int.from_bytes(second[3:5], "big") == len(second) - 5
    assert first[5:] + second[5:] == record[5:]
    assert b"api.telegram.org" not in first[5:]
    assert b"api.telegram.org" not in second[5:]


def test_split_client_hello_without_sni_still_splits() -> None:
    # Rebuild a ClientHello without any extensions.
    body = (
        b"\x03\x03"
        + b"\x00" * 32
        + b"\x00"
        + b"\x00\x02\x00\x2f"
        + b"\x01\x00"
        + b"\x00\x00"
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    bare = b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake

    parts = split_client_hello(bare)
    assert parts is not None
    assert parts[0][5:] + parts[1][5:] == bare[5:]


def test_split_client_hello_rejects_non_tls() -> None:
    assert split_client_hello(b"GET / HTTP/1.1\r\n\r\n") is None
    assert split_client_hello(b"") is None

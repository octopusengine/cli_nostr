"""Presentation-free NIP-17 actions reusable by CLI and future integrations.

This module deliberately does not read ``.env``, print to a terminal, or write
to SQLite. Callers supply a normalized private key and decide how to present
and persist the returned results. That keeps the Nostr transport usable from a
CLI today and from another local integration later.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Mapping, Sequence

from lib import wrapp_nostr as nostr


__version__ = "0.1.2"


class NostrRunnerError(ValueError):
    """A configuration or NIP-17 operation error safe to present to a caller."""


@dataclass(frozen=True)
class NostrSendResult:
    """Non-secret outcome of one NIP-17 message publication."""

    recipient_pubkey: str
    sender_pubkey: str
    recipient_npub: str
    rumor_id: str
    rumor_created_at: int | None
    recipient_event_id: str
    sender_copy_event_id: str
    relay_statuses: Mapping[str, Mapping[str, Mapping[str, object]]]

    @property
    def confirmed_relays(self) -> tuple[str, ...]:
        return tuple(
            relay_url
            for relay_url, statuses in self.relay_statuses.items()
            if statuses.get(self.recipient_event_id, {}).get("ok") is True
        )

    @property
    def delivery_status(self) -> str:
        return f"confirmed {len(self.confirmed_relays)}/{len(self.relay_statuses)}" if self.confirmed_relays else "unconfirmed"


@dataclass(frozen=True)
class NostrInboxRelayResult:
    """Read-only NIP-17 gift-wrap inventory from one relay."""

    relay_url: str
    event_ids: tuple[str, ...]
    error: str | None = None


def inspect_nip17_inbox(
    recipient_pubkey: str,
    relay_urls: Sequence[str],
    *,
    since: int,
    timeout: float,
    limit: int = 100,
) -> tuple[NostrInboxRelayResult, ...]:
    """List gift-wrap IDs addressed to a profile without decrypting their contents."""

    if not isinstance(recipient_pubkey, str) or len(recipient_pubkey) != 64:
        raise NostrRunnerError("Recipient public key must be 64 hexadecimal characters.")
    if isinstance(since, bool) or not isinstance(since, int) or since < 0:
        raise NostrRunnerError("Inbox lookback timestamp must be a non-negative integer.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise NostrRunnerError("Relay timeout must be a positive number.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NostrRunnerError("Inbox event limit must be a positive integer.")
    if not relay_urls:
        raise NostrRunnerError("At least one relay URL is required for inbox inspection.")

    try:
        tornado_ioloop, gen, websocket_connect, *_unused = nostr.nostr_runtime()
    except nostr.NostrError as error:
        raise NostrRunnerError(str(error)) from error
    results: list[NostrInboxRelayResult] = []
    for relay_url in relay_urls:
        loop = tornado_ioloop.IOLoop()
        websocket = None
        event_ids: list[str] = []
        error: str | None = None

        def stop() -> None:
            if websocket is not None:
                websocket.close()
            loop.stop()

        def on_message(message: object) -> None:
            try:
                item = json.loads(message) if message is not None else []
            except (TypeError, json.JSONDecodeError):
                return
            if not isinstance(item, list) or not item:
                return
            if item[0] == "EVENT" and len(item) >= 3 and isinstance(item[2], dict):
                event_id = item[2].get("id")
                if isinstance(event_id, str) and event_id not in event_ids:
                    event_ids.append(event_id)
            elif item[0] in {"EOSE", "CLOSED"}:
                loop.add_callback(stop)

        @gen.coroutine
        def connect() -> object:
            nonlocal websocket, error
            try:
                websocket = yield gen.with_timeout(
                    loop.time() + float(timeout),
                    websocket_connect(relay_url, on_message_callback=on_message, ping_interval=0),
                )
                request = [
                    "REQ",
                    f"cli-nostr-inbox-{uuid.uuid4().hex}",
                    {"kinds": [1059], "#p": [recipient_pubkey], "since": since, "limit": limit},
                ]
                websocket.write_message(json.dumps(request))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                loop.add_callback(stop)

        try:
            loop.spawn_callback(connect)
            loop.call_later(float(timeout), stop)
            loop.start()
        finally:
            if websocket is not None:
                websocket.close()
            loop.close(all_fds=True)
        results.append(NostrInboxRelayResult(relay_url, tuple(event_ids), error))
    return tuple(results)


def send_nip17_message(
    private_key_hex: str,
    recipient: str,
    content: str,
    relay_urls: Sequence[str],
    *,
    timeout: float,
    verbose: int = 0,
) -> NostrSendResult:
    """Encrypt and publish a NIP-17 message without terminal-side effects."""

    if not isinstance(private_key_hex, str) or len(private_key_hex) != 64:
        raise NostrRunnerError("Sender private key must be a normalized 64-character hexadecimal value.")
    if not isinstance(content, str) or not content.strip():
        raise NostrRunnerError("Message text must not be empty.")
    if not relay_urls or any(not isinstance(url, str) or not url for url in relay_urls):
        raise NostrRunnerError("At least one valid relay URL is required.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise NostrRunnerError("Relay timeout must be a positive number.")

    try:
        from pynostr.key import PrivateKey
        from lib_nostr import nip17
    except ModuleNotFoundError as error:
        raise NostrRunnerError("Install Nostr dependencies with: python -m pip install -r requirements_nostr.txt") from error

    try:
        sender_key = PrivateKey.from_hex(private_key_hex)
        recipient_key = nostr.friend_public_key(recipient)
    except (ValueError, TypeError, nostr.NostrError) as error:
        raise NostrRunnerError(str(error)) from error
    recipient_hex = recipient_key.hex()
    rumor, _seal, recipient_wrap = nip17.make_gift_wrap(
        sender_key, recipient_hex, content, relay_url=relay_urls[0]
    )
    _sender_rumor, _sender_seal, sender_wrap = nip17.make_sender_copy(
        sender_key, recipient_hex, content, relay_url=relay_urls[0], rumor=rumor
    )
    statuses = {
        relay_url: nostr.publish_events(relay_url, [recipient_wrap, sender_wrap], float(timeout), verbose)
        for relay_url in relay_urls
    }
    return NostrSendResult(
        recipient_pubkey=recipient_hex,
        sender_pubkey=sender_key.public_key.hex(),
        recipient_npub=recipient_key.bech32(),
        rumor_id=str(rumor["id"]),
        rumor_created_at=int(rumor["created_at"]) if isinstance(rumor.get("created_at"), int) else None,
        recipient_event_id=str(recipient_wrap.id),
        sender_copy_event_id=str(sender_wrap.id),
        relay_statuses=statuses,
    )

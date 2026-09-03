"""Nostr keys, relays, and NIP-17 helpers for :mod:`cli_nostr`.

This module deliberately depends only on the Python standard library and the
Nostr runtime packages. It never imports another local helper module; the CLI
composes these helpers with terminal and database storage.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

__version__ = "0.25.02"

class NostrError(ValueError):
    """An expected Nostr configuration or runtime error."""


def load_relays(path: Path) -> list[str]:
    """Load and validate relay URLs from a JSON file."""

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise NostrError(f"Cannot read relay list {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise NostrError(f"Relay list is not valid JSON: {path}: {error}") from error
    if not isinstance(raw_data, dict) or not isinstance(raw_data.get("relays"), list):
        raise NostrError(f"{path} must contain an object with a 'relays' list.")
    relays: list[str] = []
    for relay in raw_data["relays"]:
        if not isinstance(relay, str) or not relay.startswith(("ws://", "wss://")):
            raise NostrError(f"Invalid relay URL in {path}: {relay!r}")
        if relay not in relays:
            relays.append(relay)
    if not relays:
        raise NostrError(f"{path} does not contain any relays.")
    return relays


def nostr_runtime() -> tuple[object, ...]:
    """Import optional relay dependencies only for Nostr actions."""

    try:
        import tornado.ioloop
        from tornado import gen
        from tornado.websocket import websocket_connect
        from pynostr.base_relay import RelayPolicy
        from pynostr.event import Event
        from pynostr.filters import Filters, FiltersList
        from pynostr.message_pool import MessagePool
        from pynostr.message_type import RelayMessageType
        from pynostr.relay import Relay
    except ModuleNotFoundError as error:
        raise NostrError("Install relay dependencies with: python -m pip install -r requirements.txt") from error
    return (tornado.ioloop, gen, websocket_connect, RelayPolicy, Event, Filters, FiltersList, MessagePool, RelayMessageType, Relay)


def probe_relay(relay_url: str, timeout: float, verbose: int) -> tuple[bool, str, float]:
    """Open a relay WebSocket and return a concise status."""

    tornado_ioloop, gen, websocket_connect, *_unused = nostr_runtime()
    loop = tornado_ioloop.IOLoop()
    started = time.monotonic()
    try:
        websocket = loop.run_sync(lambda: gen.with_timeout(loop.time() + timeout, websocket_connect(relay_url)), timeout=timeout + 1)
        protocol = getattr(websocket, "selected_subprotocol", None) or "(none)"
        websocket.close()
        return True, (f"WebSocket OK, protocol: {protocol}" if verbose else "OK"), time.monotonic() - started
    except Exception as error:
        return False, (repr(error) if verbose else type(error).__name__), time.monotonic() - started
    finally:
        loop.stop()
        loop.close(all_fds=True)


def relay_info_url(relay_url: str) -> str:
    """Convert a WebSocket relay URL to its NIP-11 HTTP information endpoint."""

    parsed = urlsplit(relay_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise NostrError(f"Invalid relay URL: {relay_url!r}")
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, parsed.path or "/", "", ""))


def fetch_relay_info(relay_url: str, timeout: float) -> tuple[dict[str, object] | None, str]:
    """Read optional NIP-11 relay metadata without treating its absence as failure."""

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise NostrError("Relay information timeout must be a positive number.")
    info_url = relay_info_url(relay_url)
    request = Request(
        info_url,
        headers={"Accept": "application/nostr+json", "User-Agent": "cli-nostr/0.2"},
    )
    try:
        with urlopen(request, timeout=float(timeout)) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as error:
        return None, f"NIP-11 HTTP {error.code}"
    except (URLError, OSError, TimeoutError, UnicodeDecodeError) as error:
        return None, f"NIP-11 unavailable: {type(error).__name__}"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None, "NIP-11 response is not JSON"
    if not isinstance(data, dict):
        return None, "NIP-11 response is not an object"
    return data, "NIP-11 OK"


def connect_relays(relays_path: Path, timeout: float, verbose: int) -> int:
    """Probe all configured relays and print their connection status."""

    relays = load_relays(relays_path)
    print(f"Checking relays: {len(relays)}")
    successful = 0
    for relay_url in relays:
        if verbose:
            print(f"Connecting: {relay_url}")
        ok, detail, elapsed = probe_relay(relay_url, timeout, verbose)
        successful += int(ok)
        suffix = f" ({elapsed:.2f} s; {detail})" if verbose else ""
        print(f"{'OK ' if ok else 'ERROR'} {relay_url}{suffix}")
    print(f"Available relays: {successful}/{len(relays)}")
    return 0 if successful else 3


def select_live_relay(relays_path: Path, timeout: float, verbose: int) -> str | None:
    """Return the first reachable relay in configured priority order."""

    for relay_url in load_relays(relays_path):
        if verbose:
            print(f"Checking relay for stream: {relay_url}")
        ok, detail, elapsed = probe_relay(relay_url, timeout, verbose)
        if ok:
            if verbose:
                print(f"Using relay: {relay_url} ({elapsed:.2f} s; {detail})")
            return relay_url
        if verbose:
            print(f"Unavailable relay: {relay_url} ({elapsed:.2f} s; {detail})")
    return None


def select_live_relays(relays_path: Path, timeout: float, verbose: int, limit: int) -> list[str]:
    """Return up to ``limit`` reachable relays in configured priority order."""

    selected: list[str] = []
    for relay_url in load_relays(relays_path):
        if verbose:
            print(f"Checking relay for message: {relay_url}")
        ok, detail, elapsed = probe_relay(relay_url, timeout, verbose)
        if ok:
            selected.append(relay_url)
            if verbose:
                print(f"Using relay: {relay_url} ({elapsed:.2f} s; {detail})")
            if len(selected) >= limit:
                break
        elif verbose:
            print(f"Unavailable relay: {relay_url} ({elapsed:.2f} s; {detail})")
    return selected


def event_time_utc(timestamp: object) -> str:
    """Format a Nostr timestamp defensively for terminal output."""

    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OSError):
        return "unknown time"


def print_stream_event(event: object, number: int) -> None:
    """Print the public fields of one kind-1 event."""

    print(f"\n--- message {number} ---")
    print(f"time:    {event_time_utc(getattr(event, 'created_at', None))}")
    print(f"author:  {getattr(event, 'pubkey', '?')}")
    print(f"event:   {getattr(event, 'id', '?')}")
    print("content:")
    print(getattr(event, "content", ""))


def load_friends(path: Path) -> dict[str, str]:
    """Load friends from ``{name: npub}`` or a list of name/key records."""

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise NostrError(f"Cannot read friends list {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise NostrError(f"Friends list is not valid JSON: {path}: {error}") from error
    friends: dict[str, str] = {}
    if isinstance(raw_data, dict) and all(isinstance(key, str) for key in raw_data):
        for name, key in raw_data.items():
            if isinstance(key, str) and name.strip() and key.strip():
                friends[name.strip()] = key.strip()
    elif isinstance(raw_data, list):
        for item in raw_data:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("key"), str):
                name, key = item["name"].strip(), item["key"].strip()
                if name and key:
                    friends[name] = key
    else:
        raise NostrError(f"{path} must be an object {{'name': 'npub…'}} or a list of name/key objects.")
    if not friends:
        raise NostrError(f"{path} does not contain a valid friend.")
    return friends


def friend_public_key(value: str) -> object:
    """Parse an ``npub`` or 64-character hexadecimal public key."""

    try:
        from pynostr.key import PublicKey
    except ModuleNotFoundError as error:
        raise NostrError("Install message dependencies with: python -m pip install -r requirements.txt") from error
    if value.startswith("npub1"):
        return PublicKey.from_npub(value)
    if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
        return PublicKey.from_hex(value.lower())
    raise NostrError("A friend public key must be npub1… or 64 hexadecimal characters.")


def publish_events(relay_url: str, events: Sequence[object], timeout: float, verbose: int) -> dict[str, dict[str, object]]:
    """Publish signed events and collect NIP-01 ``OK`` responses."""

    tornado_ioloop, gen, _websocket_connect, RelayPolicy, _Event, _Filters, _FiltersList, MessagePool, RelayMessageType, Relay = nostr_runtime()
    logging.getLogger("tornado.general").setLevel(logging.ERROR)
    statuses = {str(event.id): {"ok": None, "detail": "no relay response", "sent": False} for event in events}
    loop = tornado_ioloop.IOLoop()
    relay = None

    def on_message(message_json: list[object]) -> None:
        nonlocal relay
        message_type = message_json[0] if message_json else None
        if message_type == RelayMessageType.OK and len(message_json) >= 4:
            event_id = str(message_json[1])
            status = statuses.get(event_id)
            if status is not None:
                status["ok"], status["detail"] = bool(message_json[2]), str(message_json[3])
                if verbose:
                    print(f"Relay OK {event_id}: {status['ok']} {status['detail']!r}")
            if relay is not None and all(item["ok"] is not None for item in statuses.values()):
                loop.add_callback(relay.close)
        elif message_type == RelayMessageType.NOTICE and len(message_json) >= 2 and verbose:
            print(f"Relay NOTICE: {message_json[1]}")

    try:
        relay = Relay(relay_url, MessagePool(first_response_only=False), loop, RelayPolicy(), timeout=timeout, close_on_eose=False, message_callback=on_message)
        for event in events:
            relay.publish(event.to_message())
        loop.run_sync(relay.connect, timeout=timeout + 2)
    except gen.TimeoutError:
        if verbose:
            print(f"Publishing timed out after {timeout:.1f} s.", file=sys.stderr)
    except Exception as error:
        for status in statuses.values():
            status["detail"] = repr(error)
    finally:
        if relay is not None:
            for status in statuses.values():
                status["sent"] = relay.num_sent_events > 0
            try:
                if relay.is_connected:
                    loop.run_sync(relay.close, timeout=2)
            except Exception as error:
                if verbose:
                    print(f"Warning while closing relay: {error!r}", file=sys.stderr)
        loop.stop()
        loop.close(all_fds=True)
    return statuses

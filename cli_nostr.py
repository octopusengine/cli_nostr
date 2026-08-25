#!/usr/bin/env python3
"""A small, safe command-line entry point for the Nostr experiments."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from lib.wrapp_terminal import Terminal


__version__ = "0.2.0"

DEFAULT_ENV_PATH = Path(".env")
DEFAULT_KEY_NAME = "NOSTR_KEY"
DEFAULT_RELAYS_PATH = Path("nostr") / "relays.json"
DEFAULT_FRIENDS_PATH = Path("data") / "friends.json"
DEFAULT_SETUP_PATH = Path("cli_nostr.json")
DEFAULT_NOSTR_MESSAGES_DATABASE_PATH = Path("nostr_msg.db")
DEFAULT_NOSTR_STREAM_DATABASE_PATH = Path("data") / "stream.db"
# The order of the secp256k1 group. A valid Nostr secret is in [1, order).
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
ENV_ASSIGNMENT = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=.*$")


class CliNostrError(ValueError):
    """An expected error that should be shown without a traceback."""


def configure_console_encoding() -> None:
    """Make arbitrary public Nostr text printable in Windows terminals too."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def record_local_message(args: argparse.Namespace, **fields: object) -> None:
    """Persist a decrypted or outgoing DM without hiding a completed relay action."""

    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, record_message

        uid = record_message(args.db, **fields)
    except (NostrMessageDatabaseError, OSError, TypeError, ValueError) as error:
        print(f"Varování: zprávu se nepodařilo uložit do {args.db}: {error}", file=sys.stderr)
        return
    if args.verbose:
        print(f"Uloženo do DB: #{uid}")


def list_message_database(args: argparse.Namespace) -> int:
    """Print the most recent local message rows, with simple direction colors."""

    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, format_message_rows, list_messages

        rows = list_messages(args.db, args.db_limit)
        lines = format_message_rows(rows)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error

    if not rows:
        print(f"Databáze {args.db} zatím neobsahuje žádné zprávy.")
        return 0
    terminal = Terminal()
    for row, line in zip(rows, lines):
        terminal.print("g" if row["direction"] == "sent" else "c", line)
    return 0


def record_stream_event(args: argparse.Namespace, relay_url: str, event: object) -> None:
    """Persist one public event fetched by --stream without interrupting its output."""

    try:
        from lib.wrapp_nostr_stream_db import NostrStreamDatabaseError, record_event

        record_event(
            args.stream_db,
            relay=relay_url,
            event_id=str(event.id),
            created_at=int(event.created_at) if isinstance(event.created_at, int) else None,
            kind=int(event.kind),
            tags=[list(tag) for tag in event.tags],
            author_pubkey=str(event.pubkey),
            content=str(event.content),
            event_json=dict(event.to_dict()),
        )
    except (NostrStreamDatabaseError, OSError, TypeError, ValueError) as error:
        print(f"Varování: stream událost se nepodařilo uložit do {args.stream_db}: {error}", file=sys.stderr)


def list_stream_database(args: argparse.Namespace) -> int:
    """Print the most recent locally saved public Nostr events."""

    try:
        from lib.wrapp_nostr_stream_db import NostrStreamDatabaseError, format_event_rows, list_events

        rows = list_events(args.stream_db, args.db_limit)
        lines = format_event_rows(rows)
    except (NostrStreamDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if not rows:
        print(f"Stream databáze {args.stream_db} zatím neobsahuje žádné události.")
        return 0
    terminal = Terminal()
    for line in lines:
        terminal.print("m", line)
    return 0


def show_stream_event(args: argparse.Namespace) -> int:
    """Print one full stored Nostr event selected by its local stream DB ID."""

    try:
        from lib.wrapp_nostr_stream_db import NostrStreamDatabaseError, get_event

        row = get_event(args.stream_db, args.db_show)
    except (NostrStreamDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if row is None:
        raise CliNostrError(f"Stream událost #{args.db_show} nebyla v {args.stream_db} nalezena.")

    try:
        raw_event = json.loads(row["event_json"])
    except (TypeError, json.JSONDecodeError):
        raw_event = {}
    if not raw_event:
        # Rows saved before event_json was introduced still have all fields
        # needed to inspect the content and tags, except the original signature.
        try:
            tags = json.loads(row["tags"])
        except (TypeError, json.JSONDecodeError):
            tags = []
        raw_event = {
            "id": row["event_id"],
            "pubkey": row["author_pubkey"],
            "created_at": row["created_at"],
            "kind": row["kind"],
            "tags": tags,
            "content": row["content"],
            "sig": "(not stored for legacy record)",
        }
    terminal = Terminal()

    def print_item(name: str, value: object) -> None:
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        elif isinstance(value, str):
            rendered = value
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        print(f"{terminal.style(name, fg='y')}: {terminal.style(rendered, fg='w')}")

    print_item("Local stream ID", f"#{row['uid']}")
    print_item("Relay", row["relay"])
    print_item("Saved", row["saved_at"])
    for name, value in sorted(raw_event.items()):
        print_item(name, value)
    return 0


def load_relays(path: Path) -> list[str]:
    """Load and validate the small relay-list JSON file."""

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise CliNostrError(f"Nelze číst seznam relayů {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliNostrError(f"Seznam relayů není platný JSON: {path}: {error}") from error

    if not isinstance(raw_data, dict) or not isinstance(raw_data.get("relays"), list):
        raise CliNostrError(f"{path} musí obsahovat objekt s polem 'relays'.")
    relays: list[str] = []
    for relay in raw_data["relays"]:
        if not isinstance(relay, str) or not relay.startswith(("ws://", "wss://")):
            raise CliNostrError(f"Neplatná URL relay v {path}: {relay!r}")
        if relay not in relays:
            relays.append(relay)
    if not relays:
        raise CliNostrError(f"{path} neobsahuje žádný relay.")
    return relays


def load_setup(path: Path) -> dict[str, object]:
    """Load the optional CLI setup object, rejecting malformed configuration."""

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise CliNostrError(f"Nelze číst setup {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliNostrError(f"Setup není platný JSON: {path}: {error}") from error
    if not isinstance(raw_data, dict):
        raise CliNostrError(f"Setup {path} musí být JSON objekt.")
    return raw_data


def _setup_path(setup: dict[str, object], name: str, default: Path) -> Path:
    value = setup.get(name, str(default))
    if not isinstance(value, str) or not value.strip():
        raise CliNostrError(f"{name} v {DEFAULT_SETUP_PATH} musí být neprázdná cesta.")
    return Path(value)


def _setup_positive_int(setup: dict[str, object], name: str, default: int) -> int:
    value = setup.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CliNostrError(f"{name} v {DEFAULT_SETUP_PATH} musí být kladné celé číslo.")
    return value


def _setup_positive_number(setup: dict[str, object], name: str, default: float) -> float:
    value = setup.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CliNostrError(f"{name} v {DEFAULT_SETUP_PATH} musí být kladné číslo.")
    return float(value)


def apply_setup(args: argparse.Namespace) -> None:
    """Attach fixed runtime settings from cli_nostr.json to parsed CLI actions."""

    setup = load_setup(DEFAULT_SETUP_PATH)
    args.relays = _setup_path(setup, "relays_path", DEFAULT_RELAYS_PATH)
    args.friends = _setup_path(setup, "friends_path", DEFAULT_FRIENDS_PATH)
    args.db = _setup_path(setup, "db_path", DEFAULT_NOSTR_MESSAGES_DATABASE_PATH)
    args.stream_db = _setup_path(setup, "stream_db_path", DEFAULT_NOSTR_STREAM_DATABASE_PATH)
    args.db_limit = _setup_positive_int(setup, "db_limit", 100)
    args.num_msg_relays = _setup_positive_int(setup, "num_msg_relays", 3)
    args.msg_timeout = _setup_positive_number(setup, "msg_timeout", 100)
    lookback = setup.get("msg_lookback", 3 * 24 * 60 * 60)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 0:
        raise CliNostrError(f"msg_lookback v {DEFAULT_SETUP_PATH} musí být nezáporné celé číslo.")
    args.msg_lookback = lookback
    args.timeout = _setup_positive_number(setup, "timeout", 8)


def message_relay_limit(args: argparse.Namespace) -> int:
    """Get the message relay fan-out from CLI or ``num_msg_relays`` setup."""

    configured_value = args.num_msg_relays
    if isinstance(configured_value, bool) or not isinstance(configured_value, int) or configured_value < 1:
        raise CliNostrError("num_msg_relays musí být kladné celé číslo.")
    return configured_value


def message_wait_timeout(args: argparse.Namespace) -> float:
    """Get the direct-message listening duration from CLI or setup."""

    configured_value = args.msg_timeout
    if isinstance(configured_value, bool) or not isinstance(configured_value, (int, float)):
        raise CliNostrError("msg_timeout musí být kladné číslo sekund.")
    timeout = float(configured_value)
    if timeout <= 0:
        raise CliNostrError("msg_timeout musí být kladné číslo sekund.")
    return timeout


def message_lookback_seconds(args: argparse.Namespace) -> int:
    """Get the NIP-17 history window, allowing for randomized wrap timestamps."""

    configured_value = args.msg_lookback
    if isinstance(configured_value, bool) or not isinstance(configured_value, int) or configured_value < 0:
        raise CliNostrError("msg_lookback musí být nezáporný počet sekund.")
    return configured_value


def nostr_runtime() -> tuple[object, ...]:
    """Import relay dependencies only for commands that need the network."""

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
        raise CliNostrError(
            "Pro relay příkazy nainstalujte závislosti: python -m pip install -r requirements.txt"
        ) from error
    return (
        tornado.ioloop,
        gen,
        websocket_connect,
        RelayPolicy,
        Event,
        Filters,
        FiltersList,
        MessagePool,
        RelayMessageType,
        Relay,
    )


def probe_relay(relay_url: str, timeout: float, verbose: int) -> tuple[bool, str, float]:
    """Open one WebSocket connection and return its status without publishing data."""

    tornado_ioloop, gen, websocket_connect, *_unused = nostr_runtime()
    loop = tornado_ioloop.IOLoop()
    started = time.monotonic()
    try:
        websocket = loop.run_sync(
            lambda: gen.with_timeout(loop.time() + timeout, websocket_connect(relay_url)),
            timeout=timeout + 1,
        )
        protocol = getattr(websocket, "selected_subprotocol", None) or "(žádný)"
        websocket.close()
        detail = f"WebSocket OK, protokol: {protocol}" if verbose else "OK"
        return True, detail, time.monotonic() - started
    except Exception as error:
        detail = repr(error) if verbose else type(error).__name__
        return False, detail, time.monotonic() - started
    finally:
        loop.stop()
        loop.close(all_fds=True)


def configured_relays(args: argparse.Namespace) -> list[str]:
    """Load relays from the fixed JSON setup path."""

    return load_relays(args.relays)


def connect_relays(args: argparse.Namespace) -> int:
    """Probe each configured relay and report whether its WebSocket is reachable."""

    relays = configured_relays(args)
    print(f"Kontrola relayů: {len(relays)}")
    successful = 0
    for relay_url in relays:
        if args.verbose:
            print(f"Připojuji: {relay_url}")
        ok, detail, elapsed = probe_relay(relay_url, args.timeout, args.verbose)
        if ok:
            successful += 1
        suffix = f" ({elapsed:.2f} s; {detail})" if args.verbose else ""
        print(f"{'OK ' if ok else 'CHYBA'} {relay_url}{suffix}")
    print(f"Dostupné relaye: {successful}/{len(relays)}")
    return 0 if successful else 3


def select_live_relay(args: argparse.Namespace) -> str | None:
    """Return the first live relay, preserving the configured priority order."""

    for relay_url in configured_relays(args):
        if args.verbose:
            print(f"Ověřuji relay pro stream: {relay_url}")
        ok, detail, elapsed = probe_relay(relay_url, args.timeout, args.verbose)
        if ok:
            if args.verbose:
                print(f"Použit relay: {relay_url} ({elapsed:.2f} s; {detail})")
            return relay_url
        if args.verbose:
            print(f"Nedostupný relay: {relay_url} ({elapsed:.2f} s; {detail})")
    return None


def select_live_relays(args: argparse.Namespace, limit: int) -> list[str]:
    """Return up to ``limit`` live relays in their configured priority order."""

    selected: list[str] = []
    for relay_url in configured_relays(args):
        if args.verbose:
            print(f"Ověřuji relay pro zprávu: {relay_url}")
        ok, detail, elapsed = probe_relay(relay_url, args.timeout, args.verbose)
        if ok:
            selected.append(relay_url)
            if args.verbose:
                print(f"Použit relay: {relay_url} ({elapsed:.2f} s; {detail})")
            if len(selected) >= limit:
                break
        elif args.verbose:
            print(f"Nedostupný relay: {relay_url} ({elapsed:.2f} s; {detail})")
    return selected


def event_time_utc(timestamp: object) -> str:
    """Format a Nostr event timestamp defensively for terminal output."""

    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OSError):
        return "neznámý čas"


def print_stream_event(event: object, number: int) -> None:
    """Print the public parts of one kind-1 event in a compact, readable form."""

    print()
    print(f"--- zpráva {number} ---")
    print(f"čas:    {event_time_utc(getattr(event, 'created_at', None))}")
    print(f"autor:  {getattr(event, 'pubkey', '?')}")
    print(f"event:  {getattr(event, 'id', '?')}")
    print("obsah:")
    print(getattr(event, "content", ""))


def load_friends(path: Path) -> dict[str, str]:
    """Load friends from ``{"name": "npub…"}`` or an explicit record list."""

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise CliNostrError(f"Nelze číst seznam přátel {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliNostrError(f"Seznam přátel není platný JSON: {path}: {error}") from error

    friends: dict[str, str] = {}
    if isinstance(raw_data, dict) and all(isinstance(key, str) for key in raw_data):
        for name, key in raw_data.items():
            if isinstance(key, str) and name.strip() and key.strip():
                friends[name.strip()] = key.strip()
    elif isinstance(raw_data, list):
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            name, key = item.get("name"), item.get("key")
            if isinstance(name, str) and isinstance(key, str) and name.strip() and key.strip():
                friends[name.strip()] = key.strip()
    else:
        raise CliNostrError(
            f"{path} musí být objekt {{\"name\": \"npub…\"}} nebo seznam objektů name/key."
        )
    if not friends:
        raise CliNostrError(f"{path} neobsahuje žádného platného přítele.")
    return friends


def friend_public_key(value: str) -> object:
    """Parse an npub or 64-character hexadecimal public key."""

    try:
        from pynostr.key import PublicKey
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Pro zprávy nainstalujte závislosti: python -m pip install -r requirements.txt"
        ) from error
    if value.startswith("npub1"):
        return PublicKey.from_npub(value)
    if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
        return PublicKey.from_hex(value.lower())
    raise CliNostrError("Klíč přítele musí být npub1… nebo 64 hexadecimálních znaků.")


def publish_events(
    relay_url: str,
    events: Sequence[object],
    timeout: float,
    verbose: int,
) -> dict[str, dict[str, object]]:
    """Publish signed events and collect NIP-01 OK responses from one relay."""

    (
        tornado_ioloop,
        gen,
        _websocket_connect,
        RelayPolicy,
        _Event,
        _Filters,
        _FiltersList,
        MessagePool,
        RelayMessageType,
        Relay,
    ) = nostr_runtime()
    logging.getLogger("tornado.general").setLevel(logging.ERROR)
    statuses = {
        str(event.id): {"ok": None, "detail": "bez odpovědi relay", "sent": False}
        for event in events
    }
    loop = tornado_ioloop.IOLoop()
    relay = None

    def on_message(message_json: list[object]) -> None:
        nonlocal relay
        message_type = message_json[0] if message_json else None
        if message_type == RelayMessageType.OK and len(message_json) >= 4:
            event_id = str(message_json[1])
            status = statuses.get(event_id)
            if status is not None:
                status["ok"] = bool(message_json[2])
                status["detail"] = str(message_json[3])
                if verbose:
                    print(f"Relay OK {event_id}: {status['ok']} {status['detail']!r}")
            if relay is not None and all(item["ok"] is not None for item in statuses.values()):
                loop.add_callback(relay.close)
        elif message_type == RelayMessageType.NOTICE and len(message_json) >= 2:
            if verbose:
                print(f"Relay NOTICE: {message_json[1]}")

    try:
        relay = Relay(
            relay_url,
            MessagePool(first_response_only=False),
            loop,
            RelayPolicy(),
            timeout=timeout,
            close_on_eose=False,
            message_callback=on_message,
        )
        for event in events:
            relay.publish(event.to_message())
        loop.run_sync(relay.connect, timeout=timeout + 2)
    except gen.TimeoutError:
        if verbose:
            print(f"Časový limit publikace po {timeout:.1f} s.", file=sys.stderr)
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
                    print(f"Varování při ukončení relay: {error!r}", file=sys.stderr)
        loop.stop()
        loop.close(all_fds=True)
    return statuses


def send_friend_message(args: argparse.Namespace) -> int:
    """Encrypt and publish a NIP-17 direct message to a named friend."""

    name, message = args.msg
    if not message.strip():
        raise CliNostrError("Text zprávy nesmí být prázdný.")
    friends = load_friends(args.friends)
    recipient_value = friends.get(name)
    if recipient_value is None:
        available = ", ".join(sorted(friends))
        raise CliNostrError(f"Přítel {name!r} nebyl nalezen. K dispozici: {available}")
    relay_limit = message_relay_limit(args)
    relay_urls = select_live_relays(args, relay_limit)
    if not relay_urls:
        print("Není dostupný žádný nakonfigurovaný relay.", file=sys.stderr)
        return 3

    try:
        from pynostr.key import PrivateKey
        from nostr import nip17
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Pro zprávy nainstalujte závislosti: python -m pip install -r requirements.txt"
        ) from error
    sender_value = get_env_value(args.key_env, args.env)
    if not sender_value:
        raise CliNostrError(f"{args.key_env} není nastaven v {args.env} ani v prostředí.")
    sender_key = PrivateKey.from_hex(normalize_private_key(sender_value))
    recipient_key = friend_public_key(recipient_value)
    recipient_hex = recipient_key.hex()
    relay_hint = relay_urls[0]
    rumor, _seal, recipient_wrap = nip17.make_gift_wrap(
        sender_key, recipient_hex, message, relay_url=relay_hint
    )
    _sender_rumor, _sender_seal, sender_wrap = nip17.make_sender_copy(
        sender_key, recipient_hex, message, relay_url=relay_hint, rumor=rumor
    )

    print(f"NIP-17 zpráva pro: {name}")
    print(f"Relaye: {', '.join(relay_urls)}")
    print(f"Příjemce: {recipient_key.bech32()}")
    if args.verbose:
        print(f"Požadovaný počet relayů: {relay_limit}")
        print(f"Délka textu: {len(message.encode('utf-8'))} B")
        print(f"Příjemcův gift-wrap: {recipient_wrap.id}")
        print(f"Vlastní gift-wrap:   {sender_wrap.id}")

    statuses_by_relay = {
        relay_url: publish_events(relay_url, [recipient_wrap, sender_wrap], args.timeout, args.verbose)
        for relay_url in relay_urls
    }
    confirmed = sum(
        status["ok"] is True
        for statuses in statuses_by_relay.values()
        for status in statuses.values()
    )
    expected = len(relay_urls) * 2
    recipient_confirmed = []
    for relay_url, statuses in statuses_by_relay.items():
        recipient_status = statuses[str(recipient_wrap.id)]
        if recipient_status["ok"] is True:
            recipient_confirmed.append(relay_url)
        for event_id, status in statuses.items():
            print(f"{relay_url} {event_id}: ok={status['ok']} detail={status['detail']!r}")
    print(f"Potvrzené zápisy: {confirmed}/{expected}")
    print(f"Příjemcova zpráva potvrzena na: {len(recipient_confirmed)}/{len(relay_urls)} relayů")
    record_local_message(
        args,
        direction="sent",
        relay=", ".join(recipient_confirmed or relay_urls),
        event_id=str(recipient_wrap.id),
        rumor_id=str(rumor["id"]),
        rumor_created_at=int(rumor["created_at"]),
        sender_pubkey=sender_key.public_key.hex(),
        recipient_pubkey=recipient_hex,
        friend_name=name,
        content=message,
        delivery_status=(
            f"confirmed {len(recipient_confirmed)}/{len(relay_urls)}"
            if recipient_confirmed
            else "unconfirmed"
        ),
    )
    return 0 if recipient_confirmed else 3


def receive_friend_messages(args: argparse.Namespace) -> int:
    """Listen directly for fresh NIP-17 gift wraps without retaining a message pool."""

    sender_value = get_env_value(args.key_env, args.env)
    if not sender_value:
        raise CliNostrError(f"{args.key_env} není nastaven v {args.env} ani v prostředí.")
    relay_limit = message_relay_limit(args)
    relay_urls = select_live_relays(args, relay_limit)
    if not relay_urls:
        print("Není dostupný žádný nakonfigurovaný relay.", file=sys.stderr)
        return 3
    wait_timeout = message_wait_timeout(args)
    lookback = message_lookback_seconds(args)

    try:
        from pynostr.key import PrivateKey
        from nostr import nip17
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Pro příjem zpráv nainstalujte závislosti: python -m pip install -r requirements.txt"
        ) from error
    (
        tornado_ioloop,
        gen,
        websocket_connect,
        _RelayPolicy,
        Event,
        Filters,
        FiltersList,
        _MessagePool,
        RelayMessageType,
        _Relay,
    ) = nostr_runtime()
    private_key = PrivateKey.from_hex(normalize_private_key(sender_value))
    own_pubkey = private_key.public_key.hex()
    received_count = 0
    decrypt_errors = 0
    seen_gift_wrap_ids: set[str] = set()
    sockets: dict[str, object] = {}
    loop = tornado_ioloop.IOLoop()
    countdown_active = False

    def finish_countdown(*, show_zero: bool = False) -> None:
        nonlocal countdown_active
        if not countdown_active:
            return
        if show_zero:
            print("0", end="", flush=True)
        print(flush=True)
        countdown_active = False

    def countdown_tick(remaining_tens: int) -> None:
        if countdown_active:
            print(f"{remaining_tens} ", end="", flush=True)

    def on_message(message_json: list[object], relay_url: str) -> None:
        nonlocal received_count, decrypt_errors
        message_type = message_json[0] if message_json else None
        if message_type == RelayMessageType.END_OF_STORED_EVENTS:
            if args.verbose:
                finish_countdown()
                print(f"Relay připraven pro nové zprávy: {relay_url}")
            return
        if message_type == RelayMessageType.NOTICE:
            if args.verbose and len(message_json) >= 2:
                finish_countdown()
                print(f"Relay NOTICE {relay_url}: {message_json[1]}")
            return
        if message_type != RelayMessageType.EVENT or len(message_json) < 3:
            return

        gift_wrap = Event.from_dict(message_json[2])
        gift_wrap_id = str(gift_wrap.id)
        if gift_wrap_id in seen_gift_wrap_ids:
            return
        seen_gift_wrap_ids.add(gift_wrap_id)
        try:
            seal, rumor = nip17.unwrap_gift_wrap(private_key, gift_wrap)
        except Exception as error:
            decrypt_errors += 1
            if args.verbose:
                finish_countdown()
                print(f"Nelze dešifrovat gift-wrap {gift_wrap.id} z {relay_url}: {error!r}")
            return

        received_count += 1
        finish_countdown()
        record_local_message(
            args,
            direction="received",
            relay=relay_url,
            event_id=gift_wrap_id,
            rumor_id=str(rumor.get("id", "")),
            rumor_created_at=(
                int(rumor["created_at"])
                if isinstance(rumor.get("created_at"), int) and not isinstance(rumor.get("created_at"), bool)
                else None
            ),
            sender_pubkey=str(seal.pubkey),
            recipient_pubkey=own_pubkey,
            content=str(rumor.get("content", "")),
            delivery_status="received",
        )
        print(f"--- NIP-17 zpráva {received_count} ---")
        print(f"relay:   {relay_url}")
        print(f"čas:     {event_time_utc(gift_wrap.created_at)}")
        print(f"odesílatel: {seal.pubkey}")
        print(f"rumor:   {rumor.get('id', '?')}")
        print("obsah:")
        print(rumor.get("content", ""))

    def stop_listening() -> None:
        finish_countdown(show_zero=True)
        for websocket in sockets.values():
            websocket.close()
        # Direct tornado sockets run without a periodic ping task. Leave one
        # loop turn for close frames, then stop exactly at msg_timeout.
        loop.call_later(0.05, loop.stop)

    since = max(0, int(time.time()) - lookback)
    filters = FiltersList(
        [Filters(kinds=[nip17.KIND_GIFT_WRAP], pubkey_refs=[own_pubkey], since=since)]
    )

    @gen.coroutine
    def connect_relay(relay_url: str, subscription_id: str) -> object:
        request = json.dumps(["REQ", subscription_id, filters.to_json_array()[0]])
        try:
            websocket = yield gen.with_timeout(
                loop.time() + args.timeout,
                websocket_connect(
                    relay_url,
                    on_message_callback=lambda message: on_raw_message(message, relay_url),
                    ping_interval=0,
                ),
            )
            sockets[relay_url] = websocket
            websocket.write_message(request)
            if args.verbose:
                finish_countdown()
                print(f"Přihlášen odběr NIP-17: {relay_url}")
        except Exception as error:
            if args.verbose:
                finish_countdown()
                print(f"Připojení pro příjem selhalo {relay_url}: {error!r}", file=sys.stderr)

    def on_raw_message(message: object, relay_url: str) -> None:
        if message is None:
            if args.verbose:
                finish_countdown()
                print(f"Relay ukončil spojení: {relay_url}")
            return
        try:
            message_json = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            if args.verbose:
                finish_countdown()
                print(f"Neplatná zpráva z relay: {relay_url}")
            return
        on_message(message_json, relay_url)

    try:
        for index, relay_url in enumerate(relay_urls, start=1):
            loop.spawn_callback(connect_relay, relay_url, f"cli-nostr-dm-{index}-{uuid.uuid4().hex}")
        print(f"Čekám na NIP-17 zprávy: {wait_timeout:g} s")
        print(f"Relaye: {', '.join(relay_urls)}")
        print(f"Načítám gift-wrapy od: {event_time_utc(since)}")
        print("Příjem ukončíte také klávesami Ctrl+C.")
        whole_tens = int(wait_timeout // 10)
        if whole_tens:
            countdown_active = True
            print("Odpočet (×10 s): ", end="", flush=True)
            for remaining_tens in range(whole_tens - 1, 0, -1):
                delay = (whole_tens - remaining_tens) * 10
                loop.call_later(delay, countdown_tick, remaining_tens)
        loop.call_later(wait_timeout, stop_listening)
        loop.start()
    except KeyboardInterrupt:
        finish_countdown()
        print("\nPříjem přerušen uživatelem.")
    finally:
        for websocket in sockets.values():
            websocket.close()
        loop.run_sync(lambda: gen.sleep(0.05), timeout=1)
        loop.close(all_fds=True)

    if received_count:
        print(f"Přijato zpráv: {received_count}")
        return 0
    print("V historii ani během čekání nebyla nalezena žádná NIP-17 zpráva.")
    if args.verbose and decrypt_errors:
        print(f"Nedešifrovatelných gift-wrapů: {decrypt_errors}")
    return 0


def stream_events(args: argparse.Namespace) -> int:
    """Fetch up to three recent public kind-1 notes from the first live relay."""

    relay_url = select_live_relay(args)
    if relay_url is None:
        print("Není dostupný žádný nakonfigurovaný relay.", file=sys.stderr)
        return 3

    (
        tornado_ioloop,
        gen,
        _websocket_connect,
        RelayPolicy,
        Event,
        Filters,
        FiltersList,
        MessagePool,
        RelayMessageType,
        Relay,
    ) = nostr_runtime()
    # pynostr currently configures an incompatible ping timeout with modern
    # Tornado. The resulting warning is internal and does not affect a short,
    # read-only stream request, so keep the terminal output focused on Nostr.
    logging.getLogger("tornado.general").setLevel(logging.ERROR)
    subscription_id = f"cli-nostr-stream-{uuid.uuid4().hex}"
    events: list[object] = []
    seen_ids: set[str] = set()
    notices: list[str] = []
    loop = tornado_ioloop.IOLoop()
    relay = None

    def on_message(message_json: list[object]) -> None:
        nonlocal relay
        message_type = message_json[0] if message_json else None
        if message_type == RelayMessageType.EVENT and len(message_json) >= 3:
            event = Event.from_dict(message_json[2])
            event_id = str(event.id)
            if event_id not in seen_ids:
                seen_ids.add(event_id)
                events.append(event)
            if len(events) >= 3 and relay is not None:
                loop.add_callback(relay.close)
        elif message_type == RelayMessageType.NOTICE and len(message_json) >= 2:
            notices.append(str(message_json[1]))
            if args.verbose:
                print(f"Relay NOTICE: {message_json[1]}")

    try:
        filters = FiltersList([Filters(kinds=[1], limit=3)])
        relay = Relay(
            relay_url,
            MessagePool(first_response_only=False),
            loop,
            RelayPolicy(),
            timeout=args.timeout,
            close_on_eose=True,
            message_callback=on_message,
        )
        relay.add_subscription(subscription_id, filters)
        if args.verbose:
            print(f"Stream relay:      {relay_url}")
            print(f"Subscription ID:   {subscription_id}")
            print("Filtr:             kind 1, limit 3")
        loop.run_sync(relay.connect, timeout=args.timeout + 2)
    except gen.TimeoutError:
        if args.verbose:
            print(f"Stream timeout po {args.timeout:.1f} s.", file=sys.stderr)
    except Exception as error:
        print(f"Chyba streamu z {relay_url}: {error!r}", file=sys.stderr)
        return 3
    finally:
        if relay is not None:
            try:
                if relay.is_connected:
                    loop.run_sync(relay.close, timeout=2)
            except Exception as error:
                if args.verbose:
                    print(f"Varování při ukončení relay: {error!r}", file=sys.stderr)
        loop.stop()
        loop.close(all_fds=True)

    for number, event in enumerate(events, start=1):
        record_stream_event(args, relay_url, event)
        print_stream_event(event, number)
    if len(events) < 3:
        print(f"Relay vrátil pouze {len(events)}/3 zpráv.", file=sys.stderr)
        return 3
    if args.verbose and notices:
        print(f"NOTICE zprávy: {len(notices)}")
    return 0


def generate_private_key_hex() -> str:
    """Generate a uniformly valid secp256k1 secret without requiring pynostr."""

    while True:
        value = int.from_bytes(secrets.token_bytes(32), "big")
        if 0 < value < SECP256K1_ORDER:
            return f"{value:064x}"


def read_dotenv(path: Path) -> dict[str, str]:
    """Read simple dotenv assignments without modifying process environment."""

    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CliNostrError(f"Nelze číst {path}: {error}") from error

    result: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[name] = value
    return result


def get_env_value(name: str, env_path: Path) -> str | None:
    """Read a setting, letting an explicitly exported environment value win."""

    from_process = os.environ.get(name)
    if from_process:
        return from_process.strip()
    return read_dotenv(env_path).get(name)


def write_dotenv_value(path: Path, name: str, value: str, *, replace: bool) -> None:
    """Add one unquoted safe dotenv value while preserving other lines/comments."""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise CliNostrError(f"Neplatný název proměnné: {name!r}")
    if "\n" in value or "\r" in value:
        raise CliNostrError("Hodnota pro .env nesmí obsahovat nový řádek.")

    try:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as error:
        raise CliNostrError(f"Nelze číst {path}: {error}") from error

    output: list[str] = []
    found = False
    for raw_line in original.splitlines():
        match = ENV_ASSIGNMENT.match(raw_line)
        if match and match.group("name") == name:
            if found:
                # Do not retain a second, potentially conflicting assignment.
                continue
            found = True
            if not replace:
                raise CliNostrError(
                    f"{name} již existuje v {path}. Pro nahrazení použijte --force."
                )
            output.append(f"{name}={value}")
        else:
            output.append(raw_line)

    if not found:
        if output and output[-1] != "":
            output.append("")
        output.append(f"{name}={value}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    except OSError as error:
        raise CliNostrError(f"Nelze zapsat {path}: {error}") from error


def normalize_private_key(value: str) -> str:
    """Validate a hex private key.  nsec support is deliberately delegated to pynostr."""

    key = value.strip().strip("\"").strip("'")
    if len(key) != 64 or not all(char in "0123456789abcdefABCDEF" for char in key):
        if key.startswith("nsec1"):
            raise CliNostrError(
                "NOSTR_KEY je nsec1…; pro jeho zobrazení nainstalujte závislosti z requirements.txt."
            )
        raise CliNostrError("NOSTR_KEY musí mít 64 hexadecimálních znaků nebo formát nsec1…")
    number = int(key, 16)
    if not 0 < number < SECP256K1_ORDER:
        raise CliNostrError("NOSTR_KEY není platný secp256k1 privátní klíč.")
    return key.lower()


def private_key_to_public_npub(secret: str) -> str:
    """Derive an npub only when the optional Nostr dependency is available."""

    try:
        from pynostr.key import PrivateKey
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Pro --key-info nainstalujte závislosti: python -m pip install -r requirements.txt"
        ) from error
    return PrivateKey.from_hex(secret).public_key.bech32()


def create_key(args: argparse.Namespace) -> int:
    existing = get_env_value(args.key_env, args.env)
    if existing and not args.force:
        raise CliNostrError(
            f"{args.key_env} už je nastaven v prostředí nebo v {args.env}. "
            "Klíč nebyl změněn; pro úmyslné nahrazení použijte --force."
        )

    secret = generate_private_key_hex()
    write_dotenv_value(args.env, args.key_env, secret, replace=args.force)
    print(f"Nový privátní klíč byl uložen do {args.env} jako {args.key_env}.")
    print("Hodnota klíče se z bezpečnostních důvodů nevypisuje.")
    try:
        print(f"Veřejný klíč: {private_key_to_public_npub(secret)}")
    except CliNostrError:
        print("Veřejný klíč zobrazíte po instalaci závislostí příkazem --key-info.")
    return 0


def show_key_info(args: argparse.Namespace) -> int:
    value = get_env_value(args.key_env, args.env)
    if not value:
        raise CliNostrError(f"{args.key_env} není nastaven v {args.env} ani v prostředí.")
    secret = normalize_private_key(value)
    npub = private_key_to_public_npub(secret)
    print(f"Zdroj: {args.env} / proměnná prostředí")
    print(f"Proměnná: {args.key_env}")
    print(f"Veřejný klíč: {npub}")
    print(f"Privátní klíč: {secret[:6]}…{secret[-4:]} (skrytý)")
    return 0


def show_config(args: argparse.Namespace) -> int:
    env_values = read_dotenv(args.env)
    print(f"Soubor .env: {args.env.resolve()}")
    print(f"{args.key_env}: {'nastaven' if get_env_value(args.key_env, args.env) else 'nenastaven'}")
    configured = sorted(name for name in env_values if name.startswith("NOSTR_"))
    if configured:
        print("NOSTR proměnné v .env:", ", ".join(configured))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="cli_nostr – jednotné CLI pro lokální Nostr klíče a postupně i relay akce."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--key-create", action="store_true", help="vygeneruje a uloží zvolený privátní klíč do .env")
    action.add_argument("--key-info", action="store_true", help="zobrazí veřejný klíč zvolené identity")
    action.add_argument("--config", action="store_true", help="zobrazí stav NOSTR konfigurace bez tajných hodnot")
    action.add_argument("-c", "--connect", action="store_true", help="ověří WebSocket spojení s relayi")
    action.add_argument("-s", "--stream", action="store_true", help="načte tři veřejné Nostr zprávy kind 1")
    action.add_argument("-m", "--msg", nargs=2, metavar=("KOMU", "CO"), help="odešle NIP-17 zprávu příteli")
    action.add_argument("-r", "--receive", action="store_true", help="čeká na nové NIP-17 zprávy")
    action.add_argument("--db-msg", action="store_true", help="vypíše uložené Nostr zprávy")
    action.add_argument("--db-str", action="store_true", help="vypíše uložené veřejné stream události")
    action.add_argument("--db-show", type=int, metavar="ID", help="detail stream události podle #ID z --db-str")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, metavar="CESTA", help="soubor .env (výchozí: .env)")
    key_selector = parser.add_mutually_exclusive_group()
    key_selector.add_argument(
        "--user",
        dest="key_env",
        default=DEFAULT_KEY_NAME,
        metavar="NOSTR_KEY",
        help="pro toto spuštění použije klíč z uvedené proměnné v .env",
    )
    key_selector.add_argument(
        "--key-env",
        dest="key_env",
        metavar="NÁZEV",
        help="název proměnné s privátním klíčem (kompatibilní alias pro --user)",
    )
    parser.add_argument("--force", action="store_true", help="povolí nahrazení existujícího klíče při --key-create")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="vypíše podrobný stav spojení")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_console_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    apply_setup(args)
    if args.key_create:
        return create_key(args)
    if args.key_info:
        return show_key_info(args)
    if args.config:
        return show_config(args)
    if args.connect:
        return connect_relays(args)
    if args.stream:
        return stream_events(args)
    if args.msg:
        return send_friend_message(args)
    if args.receive:
        return receive_friend_messages(args)
    if args.db_msg:
        return list_message_database(args)
    if args.db_str:
        return list_stream_database(args)
    if args.db_show is not None:
        return show_stream_event(args)
    parser.error("Nebyla vybrána akce.")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CliNostrError as error:
        print(f"Chyba: {error}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""A small, safe command-line entry point for the Nostr experiments."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
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

from lib import wrapp_nostr as nostr
from lib.wrapp_terminal import Terminal


__version__ = "0.2.5"

PROJECT_ROOT = Path(__file__).resolve().parent
LIBRARY_DIR = PROJECT_ROOT / "lib"
DEFAULT_ENV_PATH = Path(".env")
DEFAULT_RELAYS_PATH = Path("data_nostr") / "relays.json"
DEFAULT_PROFILES_PATH = Path("data_nostr") / "profiles.json"
DEFAULT_PROFILE_NAME = "user1"
DEFAULT_FRIENDS_PATH = Path("data_nostr") / "friends.json"
DEFAULT_SETUP_PATH = Path("cli_nostr.json")
DEFAULT_NOSTR_MESSAGES_DATABASE_PATH = Path("data_nostr") / "nostr_msg.db"
DEFAULT_NOSTR_STREAM_DATABASE_PATH = Path("data_nostr") / "nostr_stream.db"
DEFAULT_NOSTR_FOLLOWS_DATABASE_PATH = Path("data_nostr") / "nostr_follows.db"
# The order of the secp256k1 group. A valid Nostr secret is in [1, order).
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
ENV_ASSIGNMENT = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=.*$")


@dataclass(frozen=True)
class UserProfile:
    """One selected local Nostr identity, without its private-key value."""

    identifier: str
    name: str
    pub_key: str
    priv_key_name: str
    dm_relays: tuple[str, ...]


# The CLI owns presentation; Nostr helpers supply the shared error type.
CliNostrError = nostr.NostrError


def configure_console_encoding() -> None:
    """Make arbitrary public Nostr text printable in Windows terminals too."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def declared_module_version(path: Path) -> str:
    """Read one wrapper's literal ``__version__`` without importing it."""

    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return "unavailable"
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets):
            continue
        try:
            version = ast.literal_eval(statement.value)
        except ValueError:
            return "invalid"
        return str(version) if isinstance(version, (str, int, float)) else "invalid"
    return "not declared"


def used_library_modules() -> list[Path]:
    """Find local ``lib.wrapp_*`` imports used by this CLI source file."""

    try:
        source = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    except (OSError, SyntaxError):
        return []
    module_names: set[str] = set()
    for statement in ast.walk(source):
        if not isinstance(statement, ast.ImportFrom):
            continue
        if statement.module == "lib":
            module_names.update(alias.name for alias in statement.names if alias.name.startswith("wrapp_") or alias.name == "nostr_runner")
        elif statement.module and (statement.module.startswith("lib.wrapp_") or statement.module == "lib.nostr_runner"):
            module_names.add(statement.module.removeprefix("lib."))
    return [LIBRARY_DIR / f"{name}.py" for name in sorted(module_names) if (LIBRARY_DIR / f"{name}.py").is_file()]


def show_library_versions() -> int:
    """Print versions of the CLI and its imported local wrapper libraries."""

    print(f"cli_nostr.py: {__version__}")
    for module_path in used_library_modules():
        print(f"lib/{module_path.name}: {declared_module_version(module_path)}")
    return 0


def show_examples() -> int:
    """Print concise command examples without requiring local configuration."""

    print("Create a new local Nostr profile:")
    print('python cli_nostr.py --profile-create user2 "MyName" NOSTR_KEY2')
    return 0


def record_local_message(args: argparse.Namespace, **fields: object) -> None:
    """Persist a decrypted or outgoing DM without hiding a completed relay action."""

    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, record_message

        uid = record_message(args.db, **fields)
    except (NostrMessageDatabaseError, OSError, TypeError, ValueError) as error:
        print(f"Warning: could not save message to {args.db}: {error}", file=sys.stderr)
        return
    if args.verbose:
        print(f"Saved to database: #{uid}")


def list_message_database(args: argparse.Namespace) -> int:
    """Print the most recent local message rows, with simple direction colors."""

    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, format_message_rows, list_messages

        rows = list_messages(args.db, args.db_limit)
        lines = format_message_rows(rows)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error

    if not rows:
        print(f"Database {args.db} does not contain any messages yet.")
        return 0
    terminal = Terminal()
    for row, line in zip(rows, lines):
        terminal.print("g" if row["direction"] == "sent" else "c", line)
    return 0


def show_message(args: argparse.Namespace) -> int:
    """Print one complete direct-message record and its handling lifecycle."""

    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, get_message

        row = get_message(args.db, args.db_msg_show)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if row is None:
        raise CliNostrError(f"Message #{args.db_msg_show} was not found in {args.db}.")

    terminal = Terminal()
    labels = {
        "uid": "Local message ID",
        "saved_at": "Received/saved",
        "handled_at": "Handled at",
        "handling_report": "Handling report",
        "replied_at": "Replied at",
        "reply_event_id": "Reply event ID",
        "reply_status": "Reply status",
        "reply_content": "Reply content",
    }
    for field in row.keys():
        value = row[field]
        if field == "uid":
            value = f"#{value}"
        if value in (None, ""):
            value = "-"
        print(f"{terminal.style(labels.get(field, field), fg='y')}: {terminal.style(str(value), fg='w')}")
    return 0


def mark_message_done(args: argparse.Namespace) -> int:
    """Mark one received message as handled and save a concise outcome report."""

    uid, report = args.msg_done
    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, mark_message_handled

        mark_message_handled(args.db, uid, report)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    print(f"Message #{uid} marked as handled.")
    return 0


def reply_to_message(args: argparse.Namespace) -> int:
    """Send a NIP-17 reply to the sender of a handled incoming message."""

    uid, message = args.msg_reply
    if not message.strip():
        raise CliNostrError("Reply text must not be empty.")
    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, get_message, record_message_reply

        row = get_message(args.db, uid)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if row is None:
        raise CliNostrError(f"Message #{uid} was not found in {args.db}.")
    if row["direction"] != "received":
        raise CliNostrError("Only a received message can be replied to by local message ID.")
    if not row["handled_at"]:
        raise CliNostrError(f"Message #{uid} must first be marked handled with --msg-done.")
    if row["replied_at"] and not args.force:
        raise CliNostrError(f"Message #{uid} already has a recorded reply; use --force to send another one.")

    relay_urls = select_live_relays(args, message_relay_limit(args))
    if not relay_urls:
        print("No configured relay is available.", file=sys.stderr)
        return 3
    sender_value = get_env_value(args.key_env, args.env)
    if not sender_value:
        raise CliNostrError(f"{args.key_env} is not set in {args.env} or the environment.")
    try:
        from lib.nostr_runner import NostrRunnerError, send_nip17_message

        result = send_nip17_message(
            normalize_private_key(sender_value), str(row["sender_pubkey"]), message, relay_urls,
            timeout=args.timeout, verbose=args.verbose,
        )
        record_message_reply(
            args.db, uid, event_id=result.recipient_event_id, status=result.delivery_status, content=message,
        )
    except (NostrRunnerError, NostrMessageDatabaseError, OSError, TypeError, ValueError) as error:
        raise CliNostrError(str(error)) from error

    record_local_message(
        args,
        direction="sent",
        relay=", ".join(result.confirmed_relays or relay_urls),
        event_id=result.recipient_event_id,
        rumor_id=result.rumor_id,
        rumor_created_at=result.rumor_created_at,
        sender_pubkey=result.sender_pubkey,
        recipient_pubkey=result.recipient_pubkey,
        friend_name=str(row["friend_name"] or ""),
        content=message,
        delivery_status=result.delivery_status,
    )
    print(f"Reply to message #{uid}: {result.delivery_status}")
    print(f"Recipient event: {result.recipient_event_id}")
    return 0 if result.confirmed_relays else 3


def record_stream_event(args: argparse.Namespace, relay_url: str, event: object) -> None:
    """Persist one public event fetched by --stream without interrupting its output."""

    if not args.save_stream_to_db:
        return
    try:
        from lib.wrapp_nostr_db import NostrStreamDatabaseError, record_event

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
        print(f"Warning: could not save stream event to {args.stream_db}: {error}", file=sys.stderr)


def list_stream_database(args: argparse.Namespace) -> int:
    """Print the most recent locally saved public Nostr events."""

    try:
        from lib.wrapp_nostr_db import NostrStreamDatabaseError, format_event_rows, list_events

        rows = list_events(args.stream_db, args.db_limit)
        lines = format_event_rows(rows)
    except (NostrStreamDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if not rows:
        print(f"Stream database {args.stream_db} does not contain any events yet.")
        return 0
    terminal = Terminal()
    for line in lines:
        terminal.print("m", line)
    return 0


def show_stream_event(args: argparse.Namespace) -> int:
    """Print one full stored Nostr event selected by its local stream DB ID."""

    try:
        from lib.wrapp_nostr_db import NostrStreamDatabaseError, get_event

        row = get_event(args.stream_db, args.db_show)
    except (NostrStreamDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if row is None:
        raise CliNostrError(f"Stream event #{args.db_show} was not found in {args.stream_db}.")

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


def add_follow(args: argparse.Namespace) -> int:
    """Save one named public key in the local follows database."""

    name, pubkey = args.flw_add
    try:
        from lib.wrapp_nostr_db import NostrFollowDatabaseError, add_follow as add_follow_record

        uid = add_follow_record(args.follows_db, name, pubkey)
    except (NostrFollowDatabaseError, OSError, TypeError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    print(f"Follow saved to {args.follows_db}: #{uid} | {name} | {pubkey}")
    return 0


def list_follows_database(args: argparse.Namespace) -> int:
    """Print follows stored by ``--flw-add``."""

    try:
        from lib.wrapp_nostr_db import NostrFollowDatabaseError, format_follow_rows, list_follows

        rows = list_follows(args.follows_db, args.db_limit)
        lines = format_follow_rows(rows)
    except (NostrFollowDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if not rows:
        print(f"Follow database {args.follows_db} does not contain any records yet.")
        return 0
    terminal = Terminal()
    for line in lines:
        terminal.print("y", line)
    return 0


def load_relays(path: Path) -> list[str]:
    return nostr.load_relays(path)


def load_setup(path: Path) -> dict[str, object]:
    """Load the optional CLI setup object, rejecting malformed configuration."""

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise CliNostrError(f"Cannot read setup {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliNostrError(f"Setup is not valid JSON: {path}: {error}") from error
    if not isinstance(raw_data, dict):
        raise CliNostrError(f"Setup {path} must be a JSON object.")
    return raw_data


def load_profiles_configuration(path: Path) -> dict[str, object]:
    """Read the editable JSON configuration behind local user profiles."""

    try:
        configuration = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise CliNostrError(f"Cannot read profiles {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CliNostrError(f"Profiles file is not valid JSON: {path}: {error}") from error
    if not isinstance(configuration, dict) or configuration.get("version") != 1:
        raise CliNostrError("Profiles configuration requires version 1.")
    raw_profiles = configuration.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise CliNostrError("Profiles configuration requires a non-empty profiles object.")
    return configuration


def load_profiles(path: Path) -> dict[str, UserProfile]:
    """Load local profile metadata while keeping private keys in ``.env``."""

    configuration = load_profiles_configuration(path)
    raw_profiles = configuration["profiles"]
    assert isinstance(raw_profiles, dict)

    profiles: dict[str, UserProfile] = {}
    for identifier, raw_profile in raw_profiles.items():
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise CliNostrError(f"Invalid profile identifier: {identifier!r}")
        if not isinstance(raw_profile, dict):
            raise CliNostrError(f"Profile {identifier!r} must be an object.")
        name = raw_profile.get("name")
        pub_key = raw_profile.get("pub_key")
        priv_key_name = raw_profile.get("priv_key_name")
        raw_dm_relays = raw_profile.get("dm_relays", [])
        if not isinstance(name, str) or not name.strip():
            raise CliNostrError(f"Profile {identifier!r} requires a non-empty name.")
        if not isinstance(pub_key, str) or not pub_key.startswith("npub1"):
            raise CliNostrError(f"Profile {identifier!r} requires an npub public key.")
        if not isinstance(priv_key_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", priv_key_name):
            raise CliNostrError(f"Profile {identifier!r} has an invalid priv_key_name.")
        if not isinstance(raw_dm_relays, list) or not raw_dm_relays:
            raise CliNostrError(f"Profile {identifier!r} requires a non-empty dm_relays list.")
        dm_relays: list[str] = []
        for relay_url in raw_dm_relays:
            if not isinstance(relay_url, str) or not relay_url.startswith(("ws://", "wss://")):
                raise CliNostrError(f"Profile {identifier!r} has an invalid DM relay: {relay_url!r}")
            if relay_url not in dm_relays:
                dm_relays.append(relay_url)
        profiles[identifier] = UserProfile(identifier, name.strip(), pub_key, priv_key_name, tuple(dm_relays))
    return profiles


def select_user_profile(path: Path, identifier: str) -> UserProfile:
    """Return the requested profile and name available alternatives on failure."""

    profiles = load_profiles(path)
    profile = profiles.get(identifier)
    if profile is None:
        raise CliNostrError(f"Profile {identifier!r} was not found. Available: {', '.join(sorted(profiles))}")
    return profile


def _setup_path(setup: dict[str, object], name: str, default: Path) -> Path:
    value = setup.get(name, str(default))
    if not isinstance(value, str) or not value.strip():
        raise CliNostrError(f"{name} in {DEFAULT_SETUP_PATH} must be a non-empty path.")
    return Path(value)


def _setup_positive_int(setup: dict[str, object], name: str, default: int) -> int:
    value = setup.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CliNostrError(f"{name} in {DEFAULT_SETUP_PATH} must be a positive integer.")
    return value


def _setup_positive_number(setup: dict[str, object], name: str, default: float) -> float:
    value = setup.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CliNostrError(f"{name} in {DEFAULT_SETUP_PATH} must be a positive number.")
    return float(value)


def _setup_bool(setup: dict[str, object], name: str, default: bool) -> bool:
    """Read one explicit true/false setting from the CLI configuration."""

    value = setup.get(name, default)
    if not isinstance(value, bool):
        raise CliNostrError(f"{name} in {DEFAULT_SETUP_PATH} must be true or false.")
    return value


def apply_setup(args: argparse.Namespace) -> None:
    """Attach fixed runtime settings from cli_nostr.json to parsed CLI actions."""

    setup = load_setup(DEFAULT_SETUP_PATH)
    args.relays = _setup_path(setup, "relays_path", DEFAULT_RELAYS_PATH)
    args.friends = _setup_path(setup, "friends_path", DEFAULT_FRIENDS_PATH)
    args.db = _setup_path(setup, "db_path", DEFAULT_NOSTR_MESSAGES_DATABASE_PATH)
    args.stream_db = _setup_path(setup, "stream_db_path", DEFAULT_NOSTR_STREAM_DATABASE_PATH)
    args.follows_db = _setup_path(setup, "follows_db_path", DEFAULT_NOSTR_FOLLOWS_DATABASE_PATH)
    args.db_limit = _setup_positive_int(setup, "db_limit", 100)
    args.num_msg_relays = _setup_positive_int(setup, "num_msg_relays", 3)
    args.msg_timeout = _setup_positive_number(setup, "msg_timeout", 100)
    lookback = setup.get("msg_lookback", 3 * 24 * 60 * 60)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 0:
        raise CliNostrError(f"msg_lookback in {DEFAULT_SETUP_PATH} must be a non-negative integer.")
    args.msg_lookback = lookback
    args.timeout = _setup_positive_number(setup, "timeout", 8)
    args.follow_stream_timeout = _setup_positive_number(setup, "follow_stream_timeout", 100)
    args.save_stream_to_db = _setup_bool(setup, "save_stream_to_db", True)
    args.user_profile = select_user_profile(args.profiles, args.user)
    args.key_env = args.user_profile.priv_key_name
    args.pub_key = args.user_profile.pub_key


def message_relay_limit(args: argparse.Namespace) -> int:
    """Get the message relay fan-out from CLI or ``num_msg_relays`` setup."""

    configured_value = args.num_msg_relays
    if isinstance(configured_value, bool) or not isinstance(configured_value, int) or configured_value < 1:
        raise CliNostrError("num_msg_relays must be a positive integer.")
    return configured_value


def message_wait_timeout(args: argparse.Namespace) -> float:
    """Get the direct-message listening duration from CLI or setup."""

    configured_value = args.msg_timeout
    if isinstance(configured_value, bool) or not isinstance(configured_value, (int, float)):
        raise CliNostrError("msg_timeout must be a positive number of seconds.")
    timeout = float(configured_value)
    if timeout <= 0:
        raise CliNostrError("msg_timeout must be a positive number of seconds.")
    return timeout


def message_lookback_seconds(args: argparse.Namespace) -> int:
    """Get the NIP-17 history window, allowing for randomized wrap timestamps."""

    configured_value = args.msg_lookback
    if isinstance(configured_value, bool) or not isinstance(configured_value, int) or configured_value < 0:
        raise CliNostrError("msg_lookback must be a non-negative number of seconds.")
    return configured_value


def nostr_runtime() -> tuple[object, ...]:
    """Compatibility facade for the Nostr runtime wrapper."""

    return nostr.nostr_runtime()


def probe_relay(relay_url: str, timeout: float, verbose: int) -> tuple[bool, str, float]:
    """Compatibility facade for relay probing."""

    return nostr.probe_relay(relay_url, timeout, verbose)


def configured_relays(args: argparse.Namespace) -> list[str]:
    return nostr.load_relays(args.relays)


def connect_relays(args: argparse.Namespace) -> int:
    return nostr.connect_relays(args.relays, args.timeout, args.verbose)


def select_live_relay(args: argparse.Namespace) -> str | None:
    return nostr.select_live_relay(args.relays, args.timeout, args.verbose)


def select_live_relays(args: argparse.Namespace, limit: int) -> list[str]:
    return nostr.select_live_relays(args.relays, args.timeout, args.verbose, limit)


def event_time_utc(timestamp: object) -> str:
    return nostr.event_time_utc(timestamp)


def print_stream_event(event: object, number: int) -> None:
    nostr.print_stream_event(event, number)


def load_friends(path: Path) -> dict[str, str]:
    return nostr.load_friends(path)


def friend_public_key(value: str) -> object:
    return nostr.friend_public_key(value)


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
        str(event.id): {"ok": None, "detail": "no relay response", "sent": False}
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


def event_content(source: str) -> str:
    """Return literal event text, UTF-8 file text, or standard-input content."""

    if source == "-":
        content = sys.stdin.read()
    else:
        candidate = Path(source)
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8")
            except OSError as error:
                raise CliNostrError(f"Cannot read event file {candidate}: {error}") from error
        else:
            content = source
    if not content.strip():
        raise CliNostrError("Event text must not be empty.")
    return content


def build_public_event(args: argparse.Namespace, content: str) -> object:
    """Build and sign one kind-1 text event for the active user profile."""

    try:
        from pynostr.event import Event
        from pynostr.key import PrivateKey
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Install event dependencies with: python -m pip install -r requirements.txt"
        ) from error
    private_value = get_env_value(args.key_env, args.env)
    if not private_value:
        raise CliNostrError(f"{args.key_env} is not set in {args.env} or the environment.")
    secret = normalize_private_key(private_value)
    private_key = PrivateKey.from_hex(secret)
    if private_key.public_key.bech32() != args.pub_key:
        raise CliNostrError(
            f"Profile {args.user_profile.identifier!r} public key does not match {args.key_env}; "
            "correct profiles.json or select the matching profile."
        )
    event = Event(content=content, pubkey=private_key.public_key.hex())
    event.sign(secret)
    return event


def publish_public_event(args: argparse.Namespace) -> int:
    """Publish literal, file, or piped text as a signed public kind-1 event."""

    content = event_content(args.event)
    relay_urls = select_live_relays(args, message_relay_limit(args))
    if not relay_urls:
        print("No configured relay is available.", file=sys.stderr)
        return 3
    event = build_public_event(args, content)
    statuses_by_relay = {
        relay_url: nostr.publish_events(relay_url, [event], args.timeout, args.verbose)
        for relay_url in relay_urls
    }
    event_id = str(event.id)
    confirmed = 0
    print(f"Profile: {args.user_profile.identifier} ({args.user_profile.name})")
    print(f"Public event: {event_id}")
    for relay_url, statuses in statuses_by_relay.items():
        status = statuses[event_id]
        is_confirmed = status["ok"] is True
        confirmed += int(is_confirmed)
        print(f"{relay_url}: {'confirmed' if is_confirmed else 'unconfirmed'} | {status['detail']}")
    print(f"Confirmed relays: {confirmed}/{len(relay_urls)}")
    return 0 if confirmed else 3


def publish_dm_relay_list(args: argparse.Namespace) -> int:
    """Publish the active profile's NIP-17 DM inbox relay list (kind 10050)."""

    try:
        from pynostr.event import Event
        from pynostr.key import PrivateKey
    except ModuleNotFoundError as error:
        raise CliNostrError("Install event dependencies with: python -m pip install -r requirements.txt") from error
    private_value = get_env_value(args.key_env, args.env)
    if not private_value:
        raise CliNostrError(f"{args.key_env} is not set in {args.env} or the environment.")
    secret = normalize_private_key(private_value)
    private_key = PrivateKey.from_hex(secret)
    if private_key.public_key.bech32() != args.pub_key:
        raise CliNostrError(
            f"Profile {args.user_profile.identifier!r} public key does not match {args.key_env}; "
            "correct profiles.json or select the matching profile."
        )
    inbox_relays = list(args.user_profile.dm_relays)
    publish_relays = select_live_relays(args, len(configured_relays(args)))
    if not publish_relays:
        print("No configured relay is available.", file=sys.stderr)
        return 3
    event = Event(
        pubkey=private_key.public_key.hex(),
        kind=10050,
        tags=[["relay", relay_url] for relay_url in inbox_relays],
        content="",
    )
    event.sign(secret)
    statuses_by_relay = {
        relay_url: nostr.publish_events(relay_url, [event], args.timeout, args.verbose)
        for relay_url in publish_relays
    }
    event_id = str(event.id)
    confirmed = 0
    print(f"Profile: {args.user_profile.identifier} ({args.user_profile.name})")
    print("NIP-17 DM inbox relays:")
    for relay_url in inbox_relays:
        print(f"  {relay_url}")
    print(f"Relay list event (kind 10050): {event_id}")
    for relay_url, statuses in statuses_by_relay.items():
        status = statuses[event_id]
        is_confirmed = status["ok"] is True
        confirmed += int(is_confirmed)
        print(f"{relay_url}: {'confirmed' if is_confirmed else 'unconfirmed'} | {status['detail']}")
    print(f"Confirmed relays: {confirmed}/{len(publish_relays)}")
    return 0 if confirmed else 3


def inspect_dm_inbox(args: argparse.Namespace) -> int:
    """Inspect NIP-17 gift-wrap IDs without decrypting or saving a message."""

    try:
        from lib.nostr_runner import NostrRunnerError, inspect_nip17_inbox
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, message_event_ids

        recipient_pubkey = friend_public_key(args.user_profile.pub_key).hex()
        known_event_ids = message_event_ids(args.db)
        since = max(0, int(time.time()) - message_lookback_seconds(args))
        reports = inspect_nip17_inbox(
            recipient_pubkey,
            args.user_profile.dm_relays,
            since=since,
            timeout=args.timeout,
        )
    except (NostrRunnerError, NostrMessageDatabaseError, OSError, TypeError, ValueError) as error:
        raise CliNostrError(str(error)) from error

    print("NIP-17 inbox inspection (read-only; no decryption and no database writes)")
    print(f"Profile: {args.user_profile.identifier} ({args.user_profile.name})")
    print(f"Public key (hex): {recipient_pubkey}")
    print(f"Lookback: {message_lookback_seconds(args)} s since {event_time_utc(since)}")
    returned_ids: set[str] = set()
    available = 0
    for report in reports:
        if report.error:
            print(f"{report.relay_url}: error | {report.error}")
            continue
        available += 1
        ids = set(report.event_ids)
        returned_ids.update(ids)
        stored = ids & known_event_ids
        absent = ids - known_event_ids
        print(
            f"{report.relay_url}: {len(ids)} envelope(s) returned | "
            f"{len(stored)} present in local DB | {len(absent)} not in local DB"
        )
        if absent:
            print(f"  Not in local DB: {', '.join(sorted(absent))}")
    stored_total = returned_ids & known_event_ids
    absent_total = returned_ids - known_event_ids
    print(
        f"Unique envelopes: {len(returned_ids)} returned | {len(stored_total)} present in local DB | "
        f"{len(absent_total)} not in local DB"
    )
    return 0 if available else 3


def send_friend_message(args: argparse.Namespace) -> int:
    """Encrypt and publish a NIP-17 direct message to a named friend."""

    name, message = args.msg
    if not message.strip():
        raise CliNostrError("Message text must not be empty.")
    friends = load_friends(args.friends)
    recipient_value = friends.get(name)
    if recipient_value is None:
        available = ", ".join(sorted(friends))
        raise CliNostrError(f"Friend {name!r} was not found. Available: {available}")
    relay_limit = message_relay_limit(args)
    relay_urls = select_live_relays(args, relay_limit)
    if not relay_urls:
        print("No configured relay is available.", file=sys.stderr)
        return 3

    sender_value = get_env_value(args.key_env, args.env)
    if not sender_value:
        raise CliNostrError(f"{args.key_env} is not set in {args.env} or the environment.")
    try:
        from lib.nostr_runner import NostrRunnerError, send_nip17_message

        result = send_nip17_message(
            normalize_private_key(sender_value), recipient_value, message, relay_urls,
            timeout=args.timeout, verbose=args.verbose,
        )
    except (NostrRunnerError, OSError, TypeError, ValueError) as error:
        raise CliNostrError(str(error)) from error

    print(f"NIP-17 message for: {name}")
    print(f"Relays: {', '.join(relay_urls)}")
    print(f"Recipient: {result.recipient_npub}")
    if args.verbose:
        print(f"Requested relay count: {relay_limit}")
        print(f"Text length: {len(message.encode('utf-8'))} B")
        print(f"Recipient gift wrap: {result.recipient_event_id}")
        print(f"Sender gift wrap:    {result.sender_copy_event_id}")

    statuses_by_relay = result.relay_statuses
    confirmed = sum(
        status["ok"] is True
        for statuses in statuses_by_relay.values()
        for status in statuses.values()
    )
    expected = len(relay_urls) * 2
    recipient_confirmed = []
    for relay_url, statuses in statuses_by_relay.items():
        recipient_status = statuses[result.recipient_event_id]
        if recipient_status["ok"] is True:
            recipient_confirmed.append(relay_url)
        for event_id, status in statuses.items():
            print(f"{relay_url} {event_id}: ok={status['ok']} detail={status['detail']!r}")
    print(f"Confirmed writes: {confirmed}/{expected}")
    print(f"Recipient message confirmed on: {len(recipient_confirmed)}/{len(relay_urls)} relays")
    record_local_message(
        args,
        direction="sent",
        relay=", ".join(recipient_confirmed or relay_urls),
        event_id=result.recipient_event_id,
        rumor_id=result.rumor_id,
        rumor_created_at=result.rumor_created_at,
        sender_pubkey=result.sender_pubkey,
        recipient_pubkey=result.recipient_pubkey,
        friend_name=name,
        content=message,
        delivery_status=result.delivery_status,
    )
    return 0 if recipient_confirmed else 3


def receive_friend_messages(args: argparse.Namespace) -> int:
    """Receive NIP-17 gift wraps, optionally stopping when relay history is complete."""

    sender_value = get_env_value(args.key_env, args.env)
    if not sender_value:
        raise CliNostrError(f"{args.key_env} is not set in {args.env} or the environment.")
    relay_limit = message_relay_limit(args)
    relay_urls = select_live_relays(args, relay_limit)
    if not relay_urls:
        print("No configured relay is available.", file=sys.stderr)
        return 3
    wait_timeout = message_wait_timeout(args)
    lookback = message_lookback_seconds(args)

    try:
        from pynostr.key import PrivateKey
        from lib_nostr import nip17
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Install message-receiving dependencies with: python -m pip install -r requirements.txt"
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
    known_event_ids = getattr(args, "known_event_ids", ())
    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, message_event_ids

        stored_event_ids = message_event_ids(args.db)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    seen_gift_wrap_ids: set[str] = {str(event_id) for event_id in known_event_ids}
    seen_gift_wrap_ids.update(stored_event_ids)
    stop_after_message = bool(getattr(args, "stop_after_message", False))
    sync_history = bool(getattr(args, "sync_history", False))
    sockets: dict[str, object] = {}
    connected_relays: set[str] = set()
    pending_connections: set[str] = set(relay_urls)
    completed_relays: set[str] = set()
    loop = tornado_ioloop.IOLoop()
    countdown_active = False
    stopping = False
    suppress_wait_output = bool(getattr(args, "suppress_wait_output", False))
    cancel_event = getattr(args, "receive_cancel_event", None)

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

    def finish_sync_if_complete() -> None:
        """Stop a history-only receive after every connected relay sent EOSE."""

        if sync_history and not pending_connections and connected_relays <= completed_relays:
            loop.add_callback(stop_listening)

    def on_message(message_json: list[object], relay_url: str) -> None:
        nonlocal received_count, decrypt_errors
        message_type = message_json[0] if message_json else None
        if message_type == RelayMessageType.END_OF_STORED_EVENTS:
            completed_relays.add(relay_url)
            if args.verbose:
                finish_countdown()
                print(f"Relay history complete: {relay_url}")
            finish_sync_if_complete()
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
                print(f"Cannot decrypt gift wrap {gift_wrap.id} from {relay_url}: {error!r}")
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
        if not getattr(args, "suppress_received_output", False):
            print(f"--- NIP-17 message {received_count} ---")
            print(f"relay:   {relay_url}")
            print(f"time:    {event_time_utc(gift_wrap.created_at)}")
            print(f"sender:  {seal.pubkey}")
            print(f"rumor:   {rumor.get('id', '?')}")
            print("content:")
            print(rumor.get("content", ""))
        if stop_after_message:
            loop.add_callback(stop_listening)

    def stop_listening() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        finish_countdown(show_zero=True)
        for websocket in sockets.values():
            websocket.close()
        # Direct tornado sockets run without a periodic ping task. Leave one
        # loop turn for close frames, then stop exactly at msg_timeout.
        loop.call_later(0.05, loop.stop)

    def stop_when_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            stop_listening()
        else:
            loop.call_later(0.1, stop_when_cancelled)

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
            connected_relays.add(relay_url)
            websocket.write_message(request)
            if args.verbose:
                finish_countdown()
                print(f"NIP-17 subscription registered: {relay_url}")
        except Exception as error:
            if args.verbose:
                finish_countdown()
                print(f"Receiving connection failed for {relay_url}: {error!r}", file=sys.stderr)
        finally:
            pending_connections.discard(relay_url)
            finish_sync_if_complete()

    def on_raw_message(message: object, relay_url: str) -> None:
        if message is None:
            if args.verbose:
                finish_countdown()
                print(f"Relay closed the connection: {relay_url}")
            return
        try:
            message_json = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            if args.verbose:
                finish_countdown()
                print(f"Invalid message from relay: {relay_url}")
            return
        on_message(message_json, relay_url)

    try:
        for index, relay_url in enumerate(relay_urls, start=1):
            loop.spawn_callback(connect_relay, relay_url, f"cli-nostr-dm-{index}-{uuid.uuid4().hex}")
        if not suppress_wait_output:
            if sync_history:
                print("Syncing NIP-17 message history; stopping after relay history is complete.")
            else:
                print(f"Waiting for NIP-17 messages: {wait_timeout:g} s")
            print(f"Relays: {', '.join(relay_urls)}")
            print(f"Loading gift wraps since: {event_time_utc(since)}")
            if not sync_history:
                print("You can also stop receiving with Ctrl+C.")
            whole_tens = int(wait_timeout // 10)
            if whole_tens and not sync_history:
                countdown_active = True
                print("Countdown (×10 s): ", end="", flush=True)
                for remaining_tens in range(whole_tens - 1, 0, -1):
                    delay = (whole_tens - remaining_tens) * 10
                    loop.call_later(delay, countdown_tick, remaining_tens)
        loop.call_later(wait_timeout, stop_listening)
        if cancel_event is not None:
            loop.call_later(0.1, stop_when_cancelled)
        loop.start()
    except KeyboardInterrupt:
        finish_countdown()
        print("\nReceiving interrupted by user.")
    finally:
        for websocket in sockets.values():
            websocket.close()
        loop.run_sync(lambda: gen.sleep(0.05), timeout=1)
        loop.close(all_fds=True)

    if received_count:
        if not suppress_wait_output:
            print(f"Messages received: {received_count}")
        return 0
    if not suppress_wait_output:
        print("No new decryptable NIP-17 message was found." if sync_history else "No NIP-17 message was found in history or during the wait.")
    if (args.verbose or sync_history) and decrypt_errors and not suppress_wait_output:
        print(f"Undecryptable gift wraps: {decrypt_errors}")
    return 0


def sync_friend_messages(args: argparse.Namespace) -> int:
    """Fetch and save the configured NIP-17 history window, without live waiting."""

    try:
        from lib.wrapp_nostr_db import NostrMessageDatabaseError, message_event_ids

        before = message_event_ids(args.db)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    args.sync_history = True
    result = receive_friend_messages(args)
    try:
        after = message_event_ids(args.db)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    print(f"Sync complete: {len(after - before)} message(s) added to local database.")
    return result


def stream_events(args: argparse.Namespace) -> int:
    """Fetch up to three recent public kind-1 notes from the first live relay."""

    relay_url = select_live_relay(args)
    if relay_url is None:
        print("No configured relay is available.", file=sys.stderr)
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
        print(f"Stream error from {relay_url}: {error!r}", file=sys.stderr)
        return 3
    finally:
        if relay is not None:
            try:
                if relay.is_connected:
                    loop.run_sync(relay.close, timeout=2)
            except Exception as error:
                if args.verbose:
                    print(f"Warning while closing relay: {error!r}", file=sys.stderr)
        loop.stop()
        loop.close(all_fds=True)

    for number, event in enumerate(events, start=1):
        record_stream_event(args, relay_url, event)
        print_stream_event(event, number)
    if len(events) < 3:
        print(f"Relay returned only {len(events)}/3 messages.", file=sys.stderr)
        return 3
    if args.verbose and notices:
        print(f"NOTICE messages: {len(notices)}")
    return 0


def followed_authors(args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    """Read every follow and convert its public key to a Nostr author filter."""

    try:
        from lib.wrapp_nostr_db import NostrFollowDatabaseError, list_all_follows

        follows = list_all_follows(args.follows_db)
    except (NostrFollowDatabaseError, OSError, ValueError) as error:
        raise CliNostrError(str(error)) from error
    if not follows:
        raise CliNostrError(f"No follows are stored in {args.follows_db}. Add one with --flw-add first.")

    author_names: dict[str, str] = {}
    for follow in follows:
        try:
            public_key = friend_public_key(str(follow["pubkey"])).hex()
        except CliNostrError as error:
            raise CliNostrError(f"Follow {follow['name']!r} has an invalid public key: {error}") from error
        author_names[public_key] = str(follow["name"])
    return list(author_names), author_names


def follow_stream_timeout(args: argparse.Namespace) -> float:
    """Return the configured total listening period for a follow stream."""

    value = args.follow_stream_timeout
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CliNostrError("follow_stream_timeout must be a positive number of seconds.")
    return float(value)


def follow_stream(args: argparse.Namespace) -> int:
    """Stream and save newly arriving events authored by local follows."""

    authors, author_names = followed_authors(args)
    relay_urls = select_live_relays(args, message_relay_limit(args))
    if not relay_urls:
        print("No configured relay is available.", file=sys.stderr)
        return 3
    (
        tornado_ioloop,
        gen,
        websocket_connect,
        _RelayPolicy,
        Event,
        Filters,
        FiltersList,
        _MessagePool,
        _RelayMessageType,
        _Relay,
    ) = nostr_runtime()
    timeout = follow_stream_timeout(args)
    filters = FiltersList([Filters(authors=authors, since=int(time.time()))])
    loop = tornado_ioloop.IOLoop()
    sockets: dict[str, object] = {}
    seen_ids: set[str] = set()
    received_count = 0

    def show_event(event: object, relay_url: str) -> None:
        nonlocal received_count
        event_id = str(event.id)
        if event_id in seen_ids:
            return
        seen_ids.add(event_id)
        received_count += 1
        author = str(event.pubkey)
        record_stream_event(args, relay_url, event)
        print(f"\n--- follow event {received_count} ---")
        print(f"follow:  {author_names.get(author, author)}")
        print(f"relay:   {relay_url}")
        print(f"time:    {event_time_utc(event.created_at)}")
        print(f"kind:    {event.kind}")
        print(f"event:   {event_id}")
        print("content:")
        print(event.content)

    def on_message(message_json: list[object], relay_url: str) -> None:
        if not message_json or message_json[0] != "EVENT" or len(message_json) < 3:
            if args.verbose and message_json and message_json[0] == "NOTICE" and len(message_json) >= 2:
                print(f"Relay NOTICE {relay_url}: {message_json[1]}")
            return
        try:
            show_event(Event.from_dict(message_json[2]), relay_url)
        except (TypeError, ValueError, KeyError) as error:
            if args.verbose:
                print(f"Invalid follow event from {relay_url}: {error!r}", file=sys.stderr)

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
                print(f"Follow subscription registered: {relay_url}")
        except Exception as error:
            if args.verbose:
                print(f"Follow stream connection failed for {relay_url}: {error!r}", file=sys.stderr)

    def on_raw_message(message: object, relay_url: str) -> None:
        if message is None:
            return
        try:
            message_json = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            if args.verbose:
                print(f"Invalid relay message from {relay_url}", file=sys.stderr)
            return
        if isinstance(message_json, list):
            on_message(message_json, relay_url)

    def stop_listening() -> None:
        for websocket in sockets.values():
            websocket.close()
        loop.call_later(0.05, loop.stop)

    try:
        for index, relay_url in enumerate(relay_urls, start=1):
            loop.spawn_callback(connect_relay, relay_url, f"cli-nostr-follows-{index}-{uuid.uuid4().hex}")
        print(f"Following {len(authors)} account(s) for {timeout:g} s")
        print(f"Relays: {', '.join(relay_urls)}")
        print("Press Ctrl+C to stop.")
        loop.call_later(timeout, stop_listening)
        loop.start()
    except KeyboardInterrupt:
        print("\nFollow stream interrupted by user.")
    finally:
        for websocket in sockets.values():
            websocket.close()
        loop.run_sync(lambda: gen.sleep(0.05), timeout=1)
        loop.close(all_fds=True)

    print(f"Follow events received: {received_count}")
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
        raise CliNostrError(f"Cannot read {path}: {error}") from error

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
        raise CliNostrError(f"Invalid variable name: {name!r}")
    if "\n" in value or "\r" in value:
        raise CliNostrError("A .env value must not contain a newline.")

    try:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as error:
        raise CliNostrError(f"Cannot read {path}: {error}") from error

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
                    f"{name} already exists in {path}. Use --force to replace it."
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
        raise CliNostrError(f"Cannot write {path}: {error}") from error


def normalize_private_key(value: str) -> str:
    """Validate a hex private key.  nsec support is deliberately delegated to pynostr."""

    key = value.strip().strip("\"").strip("'")
    if len(key) != 64 or not all(char in "0123456789abcdefABCDEF" for char in key):
        if key.startswith("nsec1"):
            raise CliNostrError(
                "NOSTR_KEY is nsec1…; install dependencies from requirements.txt to inspect it."
            )
        raise CliNostrError("NOSTR_KEY must have 64 hexadecimal characters or use the nsec1… format.")
    number = int(key, 16)
    if not 0 < number < SECP256K1_ORDER:
        raise CliNostrError("NOSTR_KEY is not a valid secp256k1 private key.")
    return key.lower()


def private_key_to_public_npub(secret: str) -> str:
    """Derive an npub only when the optional Nostr dependency is available."""

    try:
        from pynostr.key import PrivateKey
    except ModuleNotFoundError as error:
        raise CliNostrError(
            "Install dependencies for --key-info with: python -m pip install -r requirements.txt"
        ) from error
    return PrivateKey.from_hex(secret).public_key.bech32()


def write_profiles_configuration(path: Path, configuration: dict[str, object]) -> None:
    """Write validated profile metadata as readable UTF-8 JSON."""

    try:
        path.write_text(json.dumps(configuration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        raise CliNostrError(f"Cannot write profiles {path}: {error}") from error


def create_profile(args: argparse.Namespace) -> int:
    """Create a profile, its private key reference, and its derived public key."""

    identifier, name, priv_key_name = args.profile_create
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise CliNostrError("Profile identifier may contain only letters, digits, underscores, and hyphens.")
    if not name.strip():
        raise CliNostrError("Profile name must not be empty.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", priv_key_name):
        raise CliNostrError("Private-key variable name is invalid.")

    configuration = load_profiles_configuration(args.profiles)
    profiles = configuration["profiles"]
    assert isinstance(profiles, dict)
    if identifier in profiles:
        raise CliNostrError(f"Profile {identifier!r} already exists in {args.profiles}; it was not changed.")
    if get_env_value(priv_key_name, args.env) and not args.force:
        raise CliNostrError(
            f"{priv_key_name} is already set in the environment or {args.env}. "
            "The profile was not created; use --force to replace the .env value intentionally."
        )

    secret = generate_private_key_hex()
    npub = private_key_to_public_npub(secret)
    profiles[identifier] = {
        "name": name.strip(),
        "pub_key": npub,
        "priv_key_name": priv_key_name,
        "dm_relays": load_relays(_setup_path(load_setup(DEFAULT_SETUP_PATH), "relays_path", DEFAULT_RELAYS_PATH)),
    }
    write_dotenv_value(args.env, priv_key_name, secret, replace=args.force)
    write_profiles_configuration(args.profiles, configuration)
    print(f"Profile created: {identifier} ({name.strip()})")
    print(f"Public key: {npub}")
    print(f"Private key saved to {args.env} as {priv_key_name}.")
    print(f"Use it with: python cli_nostr.py --user {identifier} --key-info")
    return 0


def create_key(args: argparse.Namespace) -> int:
    existing = get_env_value(args.key_env, args.env)
    if existing and not args.force:
        raise CliNostrError(
            f"{args.key_env} is already set in the environment or {args.env}. "
            "The key was not changed; use --force to replace it intentionally."
        )

    secret = generate_private_key_hex()
    write_dotenv_value(args.env, args.key_env, secret, replace=args.force)
    print(f"New private key saved to {args.env} as {args.key_env}.")
    print("The key value is not printed for security reasons.")
    try:
        print(f"Public key: {private_key_to_public_npub(secret)}")
    except CliNostrError:
        print("Install dependencies and run --key-info to display the public key.")
    return 0


def show_key_info(args: argparse.Namespace) -> int:
    value = get_env_value(args.key_env, args.env)
    if not value:
        raise CliNostrError(f"{args.key_env} is not set in {args.env} or the environment.")
    secret = normalize_private_key(value)
    npub = private_key_to_public_npub(secret)
    profile = args.user_profile
    print(f"Profile: {profile.identifier} ({profile.name})")
    print(f"Source: {args.env} / environment variable")
    print(f"Variable: {args.key_env}")
    print(f"Profile public key: {profile.pub_key}")
    print(f"Derived public key: {npub}")
    if npub != profile.pub_key:
        print("Warning: the profile public key does not match the configured private key.", file=sys.stderr)
    print(f"Private key: {secret[:6]}…{secret[-4:]} (hidden)")
    return 0


def show_config(args: argparse.Namespace) -> int:
    env_values = read_dotenv(args.env)
    profile = args.user_profile
    print(f"Profile: {profile.identifier} ({profile.name})")
    print(f"Profile public key: {profile.pub_key}")
    print(f"Profiles file: {args.profiles.resolve()}")
    print(f".env file: {args.env.resolve()}")
    print(f"{args.key_env}: {'set' if get_env_value(args.key_env, args.env) else 'not set'}")
    configured = sorted(name for name in env_values if name.startswith("NOSTR_"))
    if configured:
        print("NOSTR variables in .env:", ", ".join(configured))
    return 0


def doctor(args: argparse.Namespace) -> int:
    """Check local Nostr prerequisites without contacting relays or exposing keys."""

    checks: list[tuple[str, bool, str]] = []
    checks.append(("Profile", True, f"{args.user_profile.identifier} ({args.user_profile.name})"))
    checks.append(("Private key", bool(get_env_value(args.key_env, args.env)), f"{args.key_env} in {args.env}"))
    try:
        relays = load_relays(args.relays)
        checks.append(("Relay list", True, f"{len(relays)} relay(s): {args.relays}"))
    except CliNostrError as error:
        checks.append(("Relay list", False, str(error)))
    try:
        friends = load_friends(args.friends)
        checks.append(("Friends list", True, f"{len(friends)} friend(s): {args.friends}"))
    except CliNostrError as error:
        checks.append(("Friends list", False, str(error)))
    for label, path in (("Message database", args.db), ("Stream database", args.stream_db), ("Follows database", args.follows_db)):
        checks.append((label, path.parent.is_dir(), f"{path} ({'exists' if path.exists() else 'created on first use'})"))
    agent = load_setup(DEFAULT_SETUP_PATH).get("agent", {})
    if isinstance(agent, dict):
        enabled = agent.get("enabled", False)
        allowed = agent.get("allowed_senders", [])
        checks.append(("Agent policy", isinstance(enabled, bool) and isinstance(allowed, list),
                       f"{'enabled' if enabled else 'disabled'}; {len(allowed) if isinstance(allowed, list) else '?'} allowed sender(s)"))
    else:
        checks.append(("Agent policy", False, "agent in cli_nostr.json must be an object"))

    failed = 0
    terminal = Terminal()
    for label, ok, detail in checks:
        terminal.print("g" if ok else "r", f"{'OK' if ok else 'ERROR'} {label}: {detail}")
        failed += int(not ok)
    print("Relay connectivity is not checked by --doctor; use --connect for that.")
    return 0 if not failed else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="cli_nostr – a unified CLI for local Nostr keys and relay actions."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--key-create", action="store_true", help="generate and save the selected private key to .env")
    action.add_argument("--key-info", action="store_true", help="show the selected identity public key")
    action.add_argument("--config", action="store_true", help="show NOSTR configuration status without secrets")
    action.add_argument("--doctor", action="store_true", help="check local Nostr setup without contacting relays")
    action.add_argument("-L", "--lib-version", action="store_true", help="show versions of local wrapper modules used by this CLI")
    action.add_argument("--examples", action="store_true", help="show command examples")
    action.add_argument(
        "--profile-create",
        nargs=3,
        metavar=("PROFILE", "NAME", "ENV_VAR"),
        help="create PROFILE with NAME and private-key ENV_VAR",
    )
    action.add_argument("-c", "--connect", action="store_true", help="check WebSocket connections to relays")
    action.add_argument("-s", "--stream", action="store_true", help="fetch three public kind-1 Nostr messages")
    action.add_argument(
        "--event",
        metavar="TEXT|FILE|-",
        help="publish a public text event from literal TEXT, UTF-8 FILE, or stdin (-)",
    )
    action.add_argument(
        "--dm-relays-publish",
        action="store_true",
        help="publish the active profile's NIP-17 DM inbox relay list (kind 10050)",
    )
    action.add_argument(
        "--dm-inbox",
        action="store_true",
        help="inspect NIP-17 gift-wrap IDs on the active profile's inbox relays without saving them",
    )
    action.add_argument("-m", "--msg", nargs=2, metavar=("TO", "TEXT"), help="send a NIP-17 message to a friend")
    action.add_argument("-r", "--receive", action="store_true", help="wait for new NIP-17 messages")
    action.add_argument("--sync", action="store_true", help="fetch and save NIP-17 history, then stop after relay history is complete")
    action.add_argument("--db-msg", action="store_true", help="list saved Nostr messages")
    action.add_argument("--db-msg-show", type=int, metavar="ID", help="show one saved Nostr message and its lifecycle")
    action.add_argument("--msg-done", nargs=2, metavar=("ID", "REPORT"), help="mark a received message handled with REPORT")
    action.add_argument("--msg-reply", nargs=2, metavar=("ID", "TEXT"), help="reply to a handled received message by local ID")
    action.add_argument("--db-str", action="store_true", help="list saved public stream events")
    action.add_argument("--db-show", type=int, metavar="ID", help="show a stream event by its --db-str #ID")
    action.add_argument("--flw-add", nargs=2, metavar=("NAME", "PUBKEY"), help="add or update a follow")
    action.add_argument("--db-flw", action="store_true", help="list saved follows")
    action.add_argument("-f", "--follow-stream", action="store_true", help="stream new events from every saved follow")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, metavar="PATH", help=".env file (default: .env)")
    parser.add_argument(
        "--user",
        default=DEFAULT_PROFILE_NAME,
        metavar="PROFILE",
        help="select PROFILE from data_nostr/profiles.json (default: user1)",
    )
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES_PATH, metavar="PATH", help="profiles JSON file")
    parser.add_argument("--force", action="store_true", help="allow replacing a key or sending an additional recorded reply")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="show detailed connection status")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_console_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lib_version:
        return show_library_versions()
    if args.examples:
        return show_examples()
    if args.profile_create:
        return create_profile(args)
    apply_setup(args)
    if args.key_create:
        return create_key(args)
    if args.key_info:
        return show_key_info(args)
    if args.config:
        return show_config(args)
    if args.doctor:
        return doctor(args)
    if args.connect:
        return connect_relays(args)
    if args.stream:
        return stream_events(args)
    if args.event is not None:
        return publish_public_event(args)
    if args.dm_relays_publish:
        return publish_dm_relay_list(args)
    if args.dm_inbox:
        return inspect_dm_inbox(args)
    if args.msg:
        return send_friend_message(args)
    if args.receive:
        return receive_friend_messages(args)
    if args.sync:
        return sync_friend_messages(args)
    if args.db_msg:
        return list_message_database(args)
    if args.db_msg_show is not None:
        return show_message(args)
    if args.msg_done:
        return mark_message_done(args)
    if args.msg_reply:
        return reply_to_message(args)
    if args.db_str:
        return list_stream_database(args)
    if args.db_show is not None:
        return show_stream_event(args)
    if args.flw_add:
        return add_follow(args)
    if args.db_flw:
        return list_follows_database(args)
    if args.follow_stream:
        return follow_stream(args)
    parser.error("No action was selected.")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CliNostrError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)

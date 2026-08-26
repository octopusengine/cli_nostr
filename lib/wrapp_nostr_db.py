"""Shared SQLite storage for local Nostr databases.

The JSON schemas in ``data/`` are loaded whenever a database is initialized.
They contain the base SQL definitions and any additive migrations; this module
keeps the application-specific read/write helpers in one place.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


__version__ = "0.26.03"

DATA_PATH = Path(__file__).resolve().parents[1] / "data"
DEFAULT_NOSTR_MESSAGES_DATABASE_PATH = Path("data") / "nostr_msg.db"
DEFAULT_NOSTR_STREAM_DATABASE_PATH = Path("data") / "nostr_stream.db"
DEFAULT_NOSTR_FOLLOWS_DATABASE_PATH = Path("data") / "nostr_follows.db"
NOSTR_MESSAGES_SCHEMA_PATH = DATA_PATH / "nostr_msg.json"
NOSTR_STREAM_SCHEMA_PATH = DATA_PATH / "nostr_stream.json"
NOSTR_FOLLOWS_SCHEMA_PATH = DATA_PATH / "nostr_follows.json"


class NostrDatabaseError(ValueError):
    """An SQLite or schema problem suitable for concise CLI reporting."""


# Compatibility aliases for earlier imports.
NostrMessageDatabaseError = NostrDatabaseError
NostrStreamDatabaseError = NostrDatabaseError
NostrFollowDatabaseError = NostrDatabaseError


def initialize_database(database_path: Path, schema_path: Path) -> None:
    """Create and migrate ``database_path`` according to its JSON schema."""

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise NostrDatabaseError(f"Cannot read database schema {schema_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise NostrDatabaseError(f"Database schema {schema_path} is not valid JSON: {error}") from error
    if not isinstance(schema, dict):
        raise NostrDatabaseError(f"Database schema {schema_path} must be a JSON object.")
    table = schema.get("table")
    columns = schema.get("columns")
    indexes = schema.get("indexes", [])
    migrations = schema.get("migrations", {})
    if not _is_identifier(table) or not isinstance(columns, list) or not columns:
        raise NostrDatabaseError(f"Database schema {schema_path} must contain a table and non-empty columns list.")
    if not isinstance(indexes, list) or any(not isinstance(index, dict) for index in indexes):
        raise NostrDatabaseError(f"Indexes in schema {schema_path} must be a list of objects.")
    if not isinstance(migrations, dict) or not all(
        isinstance(table, str) and isinstance(columns, dict)
        and all(isinstance(name, str) and isinstance(definition, str) for name, definition in columns.items())
        for table, columns in migrations.items()
    ):
        raise NostrDatabaseError(f"Migrations in schema {schema_path} have an invalid format.")

    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.execute(_create_table_sql(table, columns))
            for index in indexes:
                connection.execute(_create_index_sql(table, index))
            for table, columns in migrations.items():
                existing = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
    except sqlite3.Error as error:
        raise NostrDatabaseError(f"Cannot initialize database {database_path}: {error}") from error


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _create_table_sql(table: str, columns: list[object]) -> str:
    definitions: list[str] = []
    for column in columns:
        if not isinstance(column, dict):
            raise NostrDatabaseError("Each database column must be an object.")
        field, column_type, constraints = column.get("field"), column.get("type"), column.get("constraints", [])
        if not _is_identifier(field) or not isinstance(column_type, str) or not column_type.strip():
            raise NostrDatabaseError("Each database column requires a valid field and type.")
        if not isinstance(constraints, list) or not all(isinstance(item, str) and item.strip() for item in constraints):
            raise NostrDatabaseError(f"Column {field} has invalid constraints.")
        definitions.append(f'"{field}" {column_type} {" ".join(constraints)}'.rstrip())
    return f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(definitions)})'


def _create_index_sql(table: str, index: dict[str, object]) -> str:
    name, columns = index.get("name"), index.get("columns")
    if not _is_identifier(name) or not isinstance(columns, list) or not columns:
        raise NostrDatabaseError("Each index requires a valid name and non-empty columns list.")
    if not all(isinstance(column, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\s+(?:ASC|DESC))?", column) for column in columns):
        raise NostrDatabaseError(f"Index {name} has invalid columns.")
    return f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({", ".join(columns)})'


def create_message_database(database_path: Path) -> None:
    initialize_database(database_path, NOSTR_MESSAGES_SCHEMA_PATH)


def create_stream_database(database_path: Path) -> None:
    initialize_database(database_path, NOSTR_STREAM_SCHEMA_PATH)


def create_follows_database(database_path: Path) -> None:
    initialize_database(database_path, NOSTR_FOLLOWS_SCHEMA_PATH)


def record_message(database_path: Path, *, direction: str, relay: str, event_id: str, rumor_id: str,
                   rumor_created_at: int | None, sender_pubkey: str, recipient_pubkey: str,
                   content: str, friend_name: str = "", delivery_status: str) -> int:
    """Insert one direct-message record, updating it when the event is known."""

    fields = {"direction": direction, "relay": relay, "event_id": event_id, "rumor_id": rumor_id,
              "sender_pubkey": sender_pubkey, "recipient_pubkey": recipient_pubkey,
              "friend_name": friend_name, "content": content, "delivery_status": delivery_status}
    if direction not in {"sent", "received"}:
        raise NostrDatabaseError("Message direction must be 'sent' or 'received'.")
    for name, value in fields.items():
        if not isinstance(value, str) or (name not in {"friend_name", "content"} and not value):
            raise NostrDatabaseError(f"Message field {name} must be valid text.")
    if rumor_created_at is not None and (isinstance(rumor_created_at, bool) or not isinstance(rumor_created_at, int) or rumor_created_at < 0):
        raise NostrDatabaseError("Rumor timestamp must be a non-negative integer or None.")
    create_message_database(database_path)
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """INSERT INTO nostr_messages (saved_at, direction, relay, event_id, rumor_id, rumor_created_at,
                   sender_pubkey, recipient_pubkey, friend_name, content, delivery_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET relay = excluded.relay,
                   delivery_status = excluded.delivery_status, friend_name = excluded.friend_name""",
                (saved_at, direction, relay, event_id, rumor_id, rumor_created_at, sender_pubkey,
                 recipient_pubkey, friend_name, content, delivery_status),
            )
            row = connection.execute("SELECT uid FROM nostr_messages WHERE event_id = ?", (event_id,)).fetchone()
            assert row is not None
            return int(row[0])
    except sqlite3.Error as error:
        raise NostrDatabaseError(f"Cannot save Nostr message to {database_path}: {error}") from error


def list_messages(database_path: Path, limit: int = 100) -> list[sqlite3.Row]:
    _validate_limit(limit, "database listing")
    create_message_database(database_path)
    return _query_rows(database_path, "SELECT * FROM nostr_messages ORDER BY saved_at DESC, uid DESC LIMIT ?", (limit,), "Nostr messages")


def record_event(database_path: Path, *, relay: str, event_id: str, created_at: int | None, kind: int,
                 tags: list[list[object]], author_pubkey: str, content: str, event_json: dict[str, object]) -> int:
    """Save one public event once, updating its relay and metadata on repeats."""

    for name, value in {"relay": relay, "event_id": event_id, "author_pubkey": author_pubkey, "content": content}.items():
        if not isinstance(value, str) or (name != "content" and not value):
            raise NostrDatabaseError(f"Stream field {name} must be non-empty text.")
    if created_at is not None and (isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0):
        raise NostrDatabaseError("Event timestamp must be a non-negative integer or None.")
    if isinstance(kind, bool) or not isinstance(kind, int) or kind < 0:
        raise NostrDatabaseError("Event kind must be a non-negative integer.")
    if not isinstance(tags, list) or any(not isinstance(tag, list) for tag in tags):
        raise NostrDatabaseError("Event tags must be a list of lists.")
    if not isinstance(event_json, dict):
        raise NostrDatabaseError("Full stream event must be a JSON object.")
    group_id, channel_id = "", event_id if kind == 40 else ""
    for tag in tags:
        if len(tag) >= 2 and isinstance(tag[0], str) and isinstance(tag[1], str):
            if tag[0] == "h" and not group_id:
                group_id = tag[1]
            elif tag[0] == "e" and (kind == 41 or (len(tag) >= 4 and tag[3] == "root")) and not channel_id:
                channel_id = tag[1]
    try:
        tags_json = json.dumps(tags, ensure_ascii=False, separators=(",", ":"))
        raw_event_json = json.dumps(event_json, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise NostrDatabaseError("Full stream event cannot be serialized as JSON.") from error
    create_stream_database(database_path)
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """INSERT INTO stream_events (saved_at, relay, event_id, created_at, kind, tags, group_id,
                   channel_id, author_pubkey, content, event_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET relay = excluded.relay, kind = excluded.kind,
                   tags = excluded.tags, group_id = excluded.group_id, channel_id = excluded.channel_id,
                   event_json = excluded.event_json""",
                (saved_at, relay, event_id, created_at, kind, tags_json, group_id, channel_id,
                 author_pubkey, content, raw_event_json),
            )
            row = connection.execute("SELECT uid FROM stream_events WHERE event_id = ?", (event_id,)).fetchone()
            assert row is not None
            return int(row[0])
    except sqlite3.Error as error:
        raise NostrDatabaseError(f"Cannot save stream event to {database_path}: {error}") from error


def list_events(database_path: Path, limit: int = 100) -> list[sqlite3.Row]:
    _validate_limit(limit, "stream database")
    create_stream_database(database_path)
    return _query_rows(database_path, "SELECT * FROM stream_events ORDER BY created_at DESC, uid DESC LIMIT ?", (limit,), "stream database")


def get_event(database_path: Path, uid: int) -> sqlite3.Row | None:
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
        raise NostrDatabaseError("Stream event ID must be a positive integer.")
    create_stream_database(database_path)
    rows = _query_rows(database_path, "SELECT * FROM stream_events WHERE uid = ?", (uid,), "stream event")
    return rows[0] if rows else None


def add_follow(database_path: Path, name: str, pubkey: str) -> int:
    """Add or update a locally named Nostr follow."""

    if not isinstance(name, str) or not name.strip():
        raise NostrDatabaseError("Follow name must be non-empty text.")
    if not isinstance(pubkey, str) or not pubkey.strip():
        raise NostrDatabaseError("Follow public key must be non-empty text.")
    create_follows_database(database_path)
    added_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """INSERT INTO nostr_follows (added_at, name, pubkey) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET added_at = excluded.added_at, pubkey = excluded.pubkey""",
                (added_at, name.strip(), pubkey.strip()),
            )
            row = connection.execute("SELECT uid FROM nostr_follows WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()
            assert row is not None
            return int(row[0])
    except sqlite3.Error as error:
        raise NostrDatabaseError(f"Cannot save follow to {database_path}: {error}") from error


def list_follows(database_path: Path, limit: int = 100) -> list[sqlite3.Row]:
    _validate_limit(limit, "follow database")
    create_follows_database(database_path)
    return _query_rows(database_path, "SELECT * FROM nostr_follows ORDER BY added_at DESC, uid DESC LIMIT ?", (limit,), "follow database")


def list_all_follows(database_path: Path) -> list[sqlite3.Row]:
    """Return every locally stored follow without a display-list limit."""

    create_follows_database(database_path)
    return _query_rows(database_path, "SELECT * FROM nostr_follows ORDER BY added_at DESC, uid DESC", (), "follow database")


def _validate_limit(limit: int, label: str) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NostrDatabaseError(f"The {label} limit must be a positive integer.")


def _query_rows(database_path: Path, query: str, values: tuple[object, ...], label: str) -> list[sqlite3.Row]:
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            return list(connection.execute(query, values))
    except sqlite3.Error as error:
        raise NostrDatabaseError(f"Cannot read {label} from {database_path}: {error}") from error


def short_text(value: object, width: int = 48) -> str:
    if width < 4:
        raise ValueError("Preview width must be at least 4.")
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else f"{text[:width - 1]}…"


def display_datetime(value: object) -> str:
    try:
        timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return short_text(value, 14)


def format_message_rows(rows: Iterable[sqlite3.Row], content_width: int = 48) -> list[str]:
    return [f"#{row['uid']} | {display_datetime(row['saved_at'])} | {row['direction']} | {row['friend_name'] or '-'} | {short_text(row['content'], content_width)}" for row in rows]


def format_event_rows(rows: Iterable[sqlite3.Row]) -> list[str]:
    def event_time(value: object) -> str:
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone().strftime("%y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            return "??-??-?? ??:??"
    return [f"#{row['uid']} | {event_time(row['created_at'])} | k{row['kind']} | {row['author_pubkey'][:12]}… | {short_text(row['content'], 54)}" for row in rows]


def format_follow_rows(rows: Iterable[sqlite3.Row]) -> list[str]:
    return [f"#{row['uid']} | {display_datetime(row['added_at'])} | {row['name']} | {row['pubkey']}" for row in rows]

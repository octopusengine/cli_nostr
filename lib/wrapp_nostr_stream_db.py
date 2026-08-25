"""SQLite storage for public Nostr events seen by ``cli_nostr --stream``."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_NOSTR_STREAM_DATABASE_PATH = Path("data") / "stream.db"


class NostrStreamDatabaseError(ValueError):
    """An SQLite problem suitable for concise CLI reporting."""


def create_database(database_path: Path) -> None:
    """Create the event table and its indexes on first use."""

    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stream_events (
                    uid INTEGER PRIMARY KEY AUTOINCREMENT,
                    saved_at TEXT NOT NULL,
                    relay TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    created_at INTEGER,
                    kind INTEGER NOT NULL,
                    tags TEXT NOT NULL,
                    group_id TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    author_pubkey TEXT NOT NULL,
                    content TEXT NOT NULL,
                    event_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_stream_events_created_at "
                "ON stream_events(created_at DESC, uid DESC)"
            )
            existing_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(stream_events)")
            }
            migrations = {
                "kind": "INTEGER NOT NULL DEFAULT 1",
                "tags": "TEXT NOT NULL DEFAULT '[]'",
                "group_id": "TEXT NOT NULL DEFAULT ''",
                "channel_id": "TEXT NOT NULL DEFAULT ''",
                "event_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, definition in migrations.items():
                if name not in existing_columns:
                    connection.execute(f"ALTER TABLE stream_events ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_stream_events_group_id "
                "ON stream_events(group_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_stream_events_channel_id "
                "ON stream_events(channel_id, created_at DESC)"
            )
    except sqlite3.Error as error:
        raise NostrStreamDatabaseError(f"Nelze vytvořit stream databázi {database_path}: {error}") from error


def record_event(
    database_path: Path,
    *,
    relay: str,
    event_id: str,
    created_at: int | None,
    kind: int,
    tags: list[list[object]],
    author_pubkey: str,
    content: str,
    event_json: dict[str, object],
) -> int:
    """Save one public event once, updating the relay when it is seen again."""

    for name, value in {
        "relay": relay,
        "event_id": event_id,
        "author_pubkey": author_pubkey,
        "content": content,
    }.items():
        if not isinstance(value, str) or not value:
            raise NostrStreamDatabaseError(f"Pole streamu {name} musí být neprázdný text.")
    if created_at is not None and (
        isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0
    ):
        raise NostrStreamDatabaseError("Čas události musí být nezáporné celé číslo nebo None.")
    if isinstance(kind, bool) or not isinstance(kind, int) or kind < 0:
        raise NostrStreamDatabaseError("Kind události musí být nezáporné celé číslo.")
    if not isinstance(tags, list) or any(not isinstance(tag, list) for tag in tags):
        raise NostrStreamDatabaseError("Tagy události musí být seznam seznamů.")
    if not isinstance(event_json, dict):
        raise NostrStreamDatabaseError("Celá stream událost musí být JSON objekt.")

    group_id = ""
    channel_id = event_id if kind == 40 else ""
    for tag in tags:
        if len(tag) < 2 or not isinstance(tag[0], str) or not isinstance(tag[1], str):
            continue
        if tag[0] == "h" and not group_id:
            group_id = tag[1]
        elif tag[0] == "e" and (
            kind == 41 or (len(tag) >= 4 and tag[3] == "root")
        ) and not channel_id:
            channel_id = tag[1]
    tags_json = json.dumps(tags, ensure_ascii=False, separators=(",", ":"))
    try:
        raw_event_json = json.dumps(event_json, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise NostrStreamDatabaseError("Celá stream událost není serializovatelná do JSON.") from error

    create_database(database_path)
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO stream_events (
                    saved_at, relay, event_id, created_at, kind, tags, group_id,
                    channel_id, author_pubkey, content, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    relay = excluded.relay,
                    kind = excluded.kind,
                    tags = excluded.tags,
                    group_id = excluded.group_id,
                    channel_id = excluded.channel_id,
                    event_json = excluded.event_json
                """,
                (
                    saved_at,
                    relay,
                    event_id,
                    created_at,
                    kind,
                    tags_json,
                    group_id,
                    channel_id,
                    author_pubkey,
                    content,
                    raw_event_json,
                ),
            )
            row = connection.execute(
                "SELECT uid FROM stream_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            return int(row[0])
    except sqlite3.Error as error:
        raise NostrStreamDatabaseError(f"Nelze uložit stream událost do {database_path}: {error}") from error


def list_events(database_path: Path, limit: int = 100) -> list[sqlite3.Row]:
    """Return saved public events, newest Nostr timestamp first."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NostrStreamDatabaseError("Limit stream databáze musí být kladné celé číslo.")
    create_database(database_path)
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            return list(
                connection.execute(
                    "SELECT * FROM stream_events ORDER BY created_at DESC, uid DESC LIMIT ?", (limit,)
                )
            )
    except sqlite3.Error as error:
        raise NostrStreamDatabaseError(f"Nelze číst stream databázi {database_path}: {error}") from error


def get_event(database_path: Path, uid: int) -> sqlite3.Row | None:
    """Return one stream event by its positive local ``#ID``."""

    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
        raise NostrStreamDatabaseError("ID stream události musí být kladné celé číslo.")
    create_database(database_path)
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM stream_events WHERE uid = ?", (uid,)).fetchone()
    except sqlite3.Error as error:
        raise NostrStreamDatabaseError(f"Nelze číst stream událost z {database_path}: {error}") from error


def _display_event_time(value: object) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone().strftime("%y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "??-??-?? ??:??"


def _short_text(value: object, width: int = 54) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else f"{text[:width - 1]}…"


def format_event_rows(rows: Iterable[sqlite3.Row]) -> list[str]:
    """Render compact rows; callers choose terminal colors."""

    return [
        f"#{row['uid']} | {_display_event_time(row['created_at'])} | k{row['kind']} | "
        f"{row['author_pubkey'][:12]}… | {_short_text(row['content'])}"
        for row in rows
    ]

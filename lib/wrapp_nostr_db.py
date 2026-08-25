"""Small SQLite storage for local Nostr direct-message history.

The wrapper deliberately stores the decrypted text supplied by ``cli_nostr``.
It is local convenience storage, not encrypted-at-rest storage.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


__version__ = "0.1.0"

DEFAULT_NOSTR_MESSAGES_DATABASE_PATH = Path("nostr_msg.db")


class NostrMessageDatabaseError(ValueError):
    """An SQLite problem suitable for concise CLI reporting."""


def create_database(database_path: Path) -> None:
    """Create the message table and indexes when they do not exist yet."""

    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS nostr_messages (
                    uid INTEGER PRIMARY KEY AUTOINCREMENT,
                    saved_at TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('sent', 'received')),
                    relay TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    rumor_id TEXT NOT NULL,
                    rumor_created_at INTEGER,
                    sender_pubkey TEXT NOT NULL,
                    recipient_pubkey TEXT NOT NULL,
                    friend_name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    delivery_status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_nostr_messages_saved_at "
                "ON nostr_messages(saved_at DESC, uid DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_nostr_messages_rumor_id "
                "ON nostr_messages(rumor_id)"
            )
    except sqlite3.Error as error:
        raise NostrMessageDatabaseError(f"Nelze vytvořit databázi {database_path}: {error}") from error


def record_message(
    database_path: Path,
    *,
    direction: str,
    relay: str,
    event_id: str,
    rumor_id: str,
    rumor_created_at: int | None,
    sender_pubkey: str,
    recipient_pubkey: str,
    content: str,
    friend_name: str = "",
    delivery_status: str,
) -> int:
    """Insert one direct-message record, updating it when the event is already known."""

    text_fields = {
        "direction": direction,
        "relay": relay,
        "event_id": event_id,
        "rumor_id": rumor_id,
        "sender_pubkey": sender_pubkey,
        "recipient_pubkey": recipient_pubkey,
        "friend_name": friend_name,
        "content": content,
        "delivery_status": delivery_status,
    }
    if direction not in {"sent", "received"}:
        raise NostrMessageDatabaseError("Směr zprávy musí být 'sent' nebo 'received'.")
    for name, value in text_fields.items():
        if not isinstance(value, str) or (name not in {"friend_name", "content"} and not value):
            raise NostrMessageDatabaseError(f"Pole zprávy {name} musí být platný text.")
    if rumor_created_at is not None and (
        isinstance(rumor_created_at, bool) or not isinstance(rumor_created_at, int) or rumor_created_at < 0
    ):
        raise NostrMessageDatabaseError("Čas rumor zprávy musí být nezáporné celé číslo nebo None.")

    create_database(database_path)
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO nostr_messages (
                    saved_at, direction, relay, event_id, rumor_id, rumor_created_at,
                    sender_pubkey, recipient_pubkey, friend_name, content, delivery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    relay = excluded.relay,
                    delivery_status = excluded.delivery_status,
                    friend_name = excluded.friend_name
                """,
                (
                    saved_at,
                    direction,
                    relay,
                    event_id,
                    rumor_id,
                    rumor_created_at,
                    sender_pubkey,
                    recipient_pubkey,
                    friend_name,
                    content,
                    delivery_status,
                ),
            )
            row = connection.execute(
                "SELECT uid FROM nostr_messages WHERE event_id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            return int(row[0])
    except sqlite3.Error as error:
        raise NostrMessageDatabaseError(f"Nelze uložit Nostr zprávu do {database_path}: {error}") from error


def list_messages(database_path: Path, limit: int = 100) -> list[sqlite3.Row]:
    """Return latest local message rows, creating an empty DB on first use."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NostrMessageDatabaseError("Limit databázového výpisu musí být kladné celé číslo.")
    create_database(database_path)
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            return list(
                connection.execute(
                    "SELECT * FROM nostr_messages ORDER BY saved_at DESC, uid DESC LIMIT ?", (limit,)
                )
            )
    except sqlite3.Error as error:
        raise NostrMessageDatabaseError(f"Nelze číst Nostr zprávy z {database_path}: {error}") from error


def short_text(value: object, width: int = 48) -> str:
    """Collapse whitespace for a compact one-line terminal preview."""

    if width < 4:
        raise ValueError("Šířka náhledu musí být alespoň 4.")
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else f"{text[:width - 1]}…"


def display_datetime(value: object) -> str:
    """Render an ISO timestamp in local time as a compact ``YY-MM-DD HH:MM``."""

    try:
        timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return short_text(value, 14)


def format_message_rows(rows: Iterable[sqlite3.Row], content_width: int = 48) -> list[str]:
    """Render row text without terminal colors; callers choose their own styling."""

    lines: list[str] = []
    for row in rows:
        friend = row["friend_name"] or "-"
        line = (
            f"#{row['uid']} | {display_datetime(row['saved_at'])} | {row['direction']} | {friend} | "
            f"{short_text(row['content'], content_width)}"
        )
        lines.append(line)
    return lines

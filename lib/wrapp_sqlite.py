"""SQLite inspection and record-management primitives for terminal tools."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Mapping

__version__ = "0.26.01"


class SQLiteBrowserError(ValueError):
    """An expected database-browser error suitable for CLI display."""


def resolve_database(database_dir: Path, database_name: str) -> Path:
    """Resolve an existing ``.db`` name safely below ``database_dir``."""

    if not isinstance(database_name, str) or not database_name.endswith(".db"):
        raise SQLiteBrowserError("Database name must end with .db.")
    requested = Path(database_name)
    if requested.is_absolute() or len(requested.parts) != 1:
        raise SQLiteBrowserError("Database name must be a file name inside the configured database directory.")
    base = database_dir.resolve()
    path = (base / requested.name).resolve()
    if path.parent != base:
        raise SQLiteBrowserError("Database path must stay inside the configured database directory.")
    if not path.is_file():
        available = sorted(item.name for item in base.glob("*.db") if item.is_file()) if base.is_dir() else []
        suffix = f" Available databases: {', '.join(available)}." if available else " No .db files are available."
        raise SQLiteBrowserError(f"Database does not exist: {path}.{suffix}")
    return path


def list_tables(database_path: Path) -> list[str]:
    """Return user tables in deterministic order."""

    try:
        with _connect_readonly(database_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            return [str(row[0]) for row in rows]
    except sqlite3.Error as error:
        raise SQLiteBrowserError(f"Cannot read database {database_path}: {error}") from error


def select_table(database_path: Path, requested_table: str | None = None) -> str:
    """Select one table, requiring an explicit choice for multi-table DBs."""

    tables = list_tables(database_path)
    if not tables:
        raise SQLiteBrowserError(f"Database {database_path} contains no user tables.")
    if requested_table is not None:
        if requested_table not in tables:
            raise SQLiteBrowserError(f"Table {requested_table!r} is not available. Tables: {', '.join(tables)}")
        return requested_table
    if len(tables) != 1:
        raise SQLiteBrowserError(f"Database contains multiple tables; choose one with --table: {', '.join(tables)}")
    return tables[0]


def list_records(
    database_path: Path, table: str, limit: int, offset: int = 0
) -> tuple[list[str], str, list[sqlite3.Row]]:
    """Return one newest-first record page, beginning at ``offset``."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise SQLiteBrowserError("Limit must be a positive integer.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise SQLiteBrowserError("Record offset must be a non-negative integer.")
    _validate_identifier(table)
    try:
        with _connect_readonly(database_path) as connection:
            columns = _table_columns(connection, table)
            id_field = _id_field(connection, table, columns)
            rendered_columns = ", ".join(_quote_identifier(column) for column in columns)
            rows = list(connection.execute(
                f"SELECT {rendered_columns} FROM {_quote_identifier(table)} "
                f"ORDER BY {_quote_identifier(id_field)} DESC LIMIT ? OFFSET ?", (limit, offset)
            ))
            return columns, id_field, rows
    except sqlite3.Error as error:
        raise SQLiteBrowserError(f"Cannot list records from {database_path}: {error}") from error


def count_records(database_path: Path, table: str) -> int:
    """Return the number of records in one table."""

    _validate_identifier(table)
    try:
        with _connect_readonly(database_path) as connection:
            _table_columns(connection, table)
            value = connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
            return int(value)
    except sqlite3.Error as error:
        raise SQLiteBrowserError(f"Cannot count records in {database_path}: {error}") from error


def list_record_ids(database_path: Path, table: str) -> tuple[str, list[object]]:
    """Return all record IDs in ascending order for previous/next navigation."""

    _validate_identifier(table)
    try:
        with _connect_readonly(database_path) as connection:
            columns = _table_columns(connection, table)
            id_field = _id_field(connection, table, columns)
            rows = connection.execute(
                f"SELECT {_quote_identifier(id_field)} FROM {_quote_identifier(table)} "
                f"ORDER BY {_quote_identifier(id_field)} ASC"
            )
            return id_field, [row[id_field] for row in rows]
    except sqlite3.Error as error:
        raise SQLiteBrowserError(f"Cannot list record IDs from {database_path}: {error}") from error


def get_record(database_path: Path, table: str, record_id: object) -> tuple[str, sqlite3.Row | None]:
    """Return one record by its primary key, uid, or id field."""

    _validate_identifier(table)
    try:
        with _connect_readonly(database_path) as connection:
            columns = _table_columns(connection, table)
            id_field = _id_field(connection, table, columns)
            row = connection.execute(
                f"SELECT * FROM {_quote_identifier(table)} WHERE {_quote_identifier(id_field)} = ?", (record_id,)
            ).fetchone()
            return id_field, row
    except sqlite3.Error as error:
        raise SQLiteBrowserError(f"Cannot read record from {database_path}: {error}") from error


def delete_record(database_path: Path, table: str, record_id: object) -> bool:
    """Delete one record and report whether it existed."""

    _validate_identifier(table)
    try:
        with sqlite3.connect(database_path) as connection:
            columns = _table_columns(connection, table)
            id_field = _id_field(connection, table, columns)
            cursor = connection.execute(
                f"DELETE FROM {_quote_identifier(table)} WHERE {_quote_identifier(id_field)} = ?", (record_id,)
            )
            return cursor.rowcount == 1
    except sqlite3.Error as error:
        raise SQLiteBrowserError(f"Cannot delete record from {database_path}: {error}") from error


def format_records(rows: Iterable[sqlite3.Row], columns: Iterable[Mapping[str, object]]) -> list[str]:
    """Render configured fixed-width columns for the compact record list."""

    configured = _configured_columns(columns)
    header = " | ".join(short_text(name, width).ljust(width) for _field, name, width in configured)
    lines = [header]
    for row in rows:
        if any(field not in row.keys() for field, _name, _width in configured):
            raise SQLiteBrowserError("A configured list column is not available in the selected table.")
        lines.append(
            " | ".join(
                short_text(format_value(field, row[field]), width).ljust(width)
                for field, _name, width in configured
            )
        )
    return lines


def record_as_dict(row: sqlite3.Row) -> dict[str, object]:
    """Convert a row to a JSON-compatible mapping with compact timestamps."""

    return {key: format_value(key, row[key]) for key in row.keys()}


def format_value(field_name: str, value: object) -> object:
    """Shorten recognised date/time values to ``RR-MM-DD hh:mm`` for display."""

    if value is None or not _is_datetime_field(field_name):
        return value
    parsed: datetime | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc).astimezone()
        except (OverflowError, OSError, ValueError):
            pass
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone()
        except ValueError:
            pass
    return parsed.strftime("%y-%m-%d %H:%M") if parsed is not None else value


def short_text(value: object, width: int = 30) -> str:
    """Collapse whitespace and return a compact fixed-width-friendly preview."""

    if width < 3:
        raise ValueError("Preview width must be at least 3.")
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= width else f"{text[:width - 2]}.."


def _configured_columns(columns: Iterable[Mapping[str, object]]) -> list[tuple[str, str, int]]:
    configured: list[tuple[str, str, int]] = []
    for column in columns:
        field, name, width = column.get("field"), column.get("name"), column.get("width")
        if not isinstance(field, str) or not field:
            raise SQLiteBrowserError("Each list column requires a non-empty field.")
        if not isinstance(name, str) or not name.strip():
            raise SQLiteBrowserError("Each list column requires a non-empty name.")
        if isinstance(width, bool) or not isinstance(width, int) or width < 3:
            raise SQLiteBrowserError("Each list column width must be a whole number of at least 3.")
        configured.append((field, name, width))
    if not configured:
        raise SQLiteBrowserError("At least one list column is required.")
    return configured


def _is_datetime_field(field_name: str) -> bool:
    return field_name == "datetime" or field_name.endswith(("_at", "_datetime", "_time", "timestamp"))


def _connect_readonly(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")]
    if not columns:
        raise SQLiteBrowserError(f"Table {table!r} does not exist.")
    return columns


def _id_field(connection: sqlite3.Connection, table: str, columns: list[str]) -> str:
    details = list(connection.execute(f"PRAGMA table_info({_quote_identifier(table)})"))
    primary_keys = [str(row[1]) for row in details if int(row[5]) > 0]
    if len(primary_keys) == 1:
        return primary_keys[0]
    if "uid" in columns:
        return "uid"
    if "id" in columns:
        return "id"
    raise SQLiteBrowserError(f"Table {table!r} has no single-column primary key, uid, or id field.")


def _validate_identifier(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SQLiteBrowserError(f"Invalid SQLite identifier: {value!r}")


def _quote_identifier(value: str) -> str:
    _validate_identifier(value)
    return f'"{value}"'

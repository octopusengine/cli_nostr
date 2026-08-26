#!/usr/bin/env python3
"""Terminal list, detail, and deletion tool for local SQLite databases."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from lib.wrapp_sqlite import (
    SQLiteBrowserError,
    count_records,
    delete_record,
    format_records,
    format_value,
    get_record,
    list_record_ids,
    list_records,
    list_tables,
    resolve_database,
    select_table,
)
from lib.wrapp_terminal import Terminal


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "cli_sqlite.json"


def configure_output() -> None:
    """Keep an unencodable remote message from aborting a redirected list."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass


def load_config(path: Path) -> tuple[Path, int]:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise SQLiteBrowserError(f"Cannot read configuration {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SQLiteBrowserError(f"Configuration {path} is not valid JSON: {error}") from error
    directory, max_list_rows = config.get("database_dir"), config.get("max_list_rows", 20)
    if not isinstance(directory, str) or not directory.strip():
        raise SQLiteBrowserError("database_dir must be a non-empty path.")
    if isinstance(max_list_rows, bool) or not isinstance(max_list_rows, int) or max_list_rows < 1:
        raise SQLiteBrowserError("max_list_rows must be a positive integer.")
    return (path.parent / directory).resolve(), max_list_rows


def resolve_base_config(database_dir: Path, database_path: Path, requested_name: str | None) -> Path:
    """Resolve ``NAME_base.json`` or one explicitly selected list layout."""

    name = requested_name or f"{database_path.stem}_base.json"
    requested = Path(name)
    if requested.suffix != ".json" or requested.is_absolute() or len(requested.parts) != 1:
        raise SQLiteBrowserError("Base configuration must be a .json file name inside the configured database directory.")
    base_dir = database_dir.resolve()
    config_path = (base_dir / requested.name).resolve()
    if config_path.parent != base_dir:
        raise SQLiteBrowserError("Base configuration must stay inside the configured database directory.")
    if not config_path.is_file():
        available = sorted(item.name for item in base_dir.glob("*_base*.json") if item.is_file()) if base_dir.is_dir() else []
        suffix = f" Available bases: {', '.join(available)}." if available else ""
        raise SQLiteBrowserError(f"Base configuration does not exist: {config_path}.{suffix}")
    return config_path


def load_list_columns(config_path: Path, available_fields: list[str]) -> list[dict[str, object]]:
    """Read and validate the configured columns for one compact list."""

    try:
        configuration = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise SQLiteBrowserError(f"Cannot read base configuration {config_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SQLiteBrowserError(f"Base configuration is not valid JSON: {config_path}: {error}") from error
    if not isinstance(configuration, dict) or configuration.get("version") != 1:
        raise SQLiteBrowserError("Base configuration requires version 1.")
    columns = configuration.get("columns")
    if not isinstance(columns, list) or not columns:
        raise SQLiteBrowserError("Base configuration requires a non-empty columns array.")

    configured: list[dict[str, object]] = []
    fields: set[str] = set()
    for column in columns:
        if not isinstance(column, dict):
            raise SQLiteBrowserError("Each base list column must be an object.")
        field, name, width = column.get("field"), column.get("name"), column.get("width")
        if not isinstance(field, str) or field not in available_fields:
            raise SQLiteBrowserError(f"Base list column has an unknown field: {field!r}")
        if field in fields:
            raise SQLiteBrowserError(f"Base list column is duplicated: {field!r}")
        if not isinstance(name, str) or not name.strip():
            raise SQLiteBrowserError("Each base list column name must be non-empty text.")
        if isinstance(width, bool) or not isinstance(width, int) or width < 3:
            raise SQLiteBrowserError("Each base list column width must be a whole number of at least 3.")
        fields.add(field)
        configured.append({"field": field, "name": name, "width": width})
    return configured


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List, inspect, and delete records in local SQLite databases.")
    parser.add_argument("database", nargs="?", metavar="NAME.db", help="database file inside database_dir")
    parser.add_argument("-l", "--list", action="store_true", help="list every .db file inside database_dir")
    parser.add_argument("--table", metavar="NAME", help="table name; required when the database has multiple tables")
    parser.add_argument("--base", metavar="NAME_base.json", help="list layout JSON inside database_dir")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--show", metavar="ID", help="show one record by primary key, uid, or id")
    actions.add_argument("--delete", "-d", metavar="ID", help="delete one record by primary key, uid, or id")
    actions.add_argument(
        "--export",
        nargs=2,
        metavar=("ID", "FILE.json"),
        help="export one complete record as a new JSON file",
    )
    parser.add_argument("--limit", type=int, metavar="N", help="rows per list page; overrides max_list_rows")
    return parser


def render_record(database_path: Path, table: str, row: object) -> None:
    """Print a complete record with concise time values."""

    terminal = Terminal()
    print(f"Database: {database_path}\nTable: {table}\n")
    for field_name in row.keys():  # type: ignore[union-attr]
        field_value = row[field_name]  # type: ignore[index]
        print(f"{terminal.color('y', f'{field_name}:')} {format_value(field_name, field_value)}")


def print_detail(database_path: Path, table: str, record_id: object) -> int:
    id_field, row = get_record(database_path, table, record_id)
    if row is None:
        print(f"Record #{record_id} was not found in {table} ({id_field}).", file=sys.stderr)
        return 1
    render_record(database_path, table, row)
    return 0


def resolve_export_path(value: str) -> Path:
    """Resolve a new JSON export path inside this project."""

    requested = Path(value)
    if requested.suffix != ".json" or requested.is_absolute():
        raise SQLiteBrowserError("Export file must be a relative .json path inside the project.")
    destination = (PROJECT_ROOT / requested).resolve()
    try:
        destination.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise SQLiteBrowserError("Export file must stay inside the project.") from error
    if destination.exists():
        raise SQLiteBrowserError(f"Export file already exists and will not be overwritten: {destination}")
    if not destination.parent.is_dir():
        raise SQLiteBrowserError(f"Export directory does not exist: {destination.parent}")
    return destination


def export_record(database_path: Path, table: str, record_id: object, destination: Path) -> int:
    """Write one complete record in its stored form as UTF-8 JSON."""

    id_field, row = get_record(database_path, table, record_id)
    if row is None:
        print(f"Record #{record_id} was not found in {table} ({id_field}).", file=sys.stderr)
        return 1
    contents = {field_name: row[field_name] for field_name in row.keys()}
    try:
        destination.write_text(json.dumps(contents, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as error:
        raise SQLiteBrowserError(f"Cannot export record to {destination}: {error}") from error
    print(f"Record exported: {destination}")
    return 0


def read_terminal_key() -> str:
    """Read one keypress, including left and right arrows, without Enter."""

    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            return {"K": "left", "M": "right", "H": "up", "P": "down"}.get(key, "")
        return key.casefold()

    import termios
    import tty

    descriptor = sys.stdin.fileno()
    original_settings = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        key = sys.stdin.read(1)
        if key == "\x1b":
            key += sys.stdin.read(2)
            return {"\x1b[D": "left", "\x1b[C": "right", "\x1b[A": "up", "\x1b[B": "down"}.get(key, "escape")
        return key.casefold()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original_settings)


def clear_screen() -> None:
    print("\033[2J\033[H", end="", flush=True)


def confirm_delete(record_id: object, read_key: Callable[[], str] | None = None) -> bool:
    """Ask for a single-key confirmation before interactive deletion."""

    print(f"Delete record {record_id}? (y/n)", end=" ", flush=True)
    key_reader = read_key or read_terminal_key
    while True:
        key = key_reader()
        if key == "y":
            return True
        if key in {"n", "q", "escape"}:
            return False


def browse_records(database_path: Path, table: str, start_id: object) -> None:
    """Interactively show a record, navigate with arrows, or delete it."""

    _id_field, record_ids = list_record_ids(database_path, table)
    if start_id not in record_ids:
        raise SQLiteBrowserError(f"Record is no longer available: {start_id}")
    current_id = start_id
    while True:
        _id_field, row = get_record(database_path, table, current_id)
        if row is None:
            raise SQLiteBrowserError(f"Record is no longer available: {current_id}")
        clear_screen()
        render_record(database_path, table, row)
        print(f"\nID: {current_id}")
        print("← previous | → next | d delete | q quit")
        key = read_terminal_key()
        if key in {"q", "escape"}:
            return
        index = record_ids.index(current_id)
        if key == "left":
            current_id = record_ids[(index - 1) % len(record_ids)]
        elif key == "right":
            current_id = record_ids[(index + 1) % len(record_ids)]
        elif key == "d" and confirm_delete(current_id):
            if not delete_record(database_path, table, current_id):
                raise SQLiteBrowserError(f"Record is no longer available: {current_id}")
            record_ids.pop(index)
            if not record_ids:
                clear_screen()
                print("No records remain.")
                return
            current_id = record_ids[index % len(record_ids)]


def interactive_select(
    database_path: Path,
    table: str,
    id_field: str,
    rows: list[object],
    page_size: int,
    total_records: int,
    list_columns: list[dict[str, object]],
) -> int:
    """Browse fixed-size list pages with arrows and open a selected record."""

    if not sys.stdin.isatty() or not sys.stdout.isatty() or not rows:
        return 0
    selected = 0
    offset = 0
    terminal = Terminal()
    while True:
        lines = format_records(rows, list_columns)
        header, record_lines = lines[0], lines[1:]
        clear_screen()
        first_row, last_row = offset + 1, offset + len(rows)
        page_number = offset // page_size + 1
        page_count = (total_records + page_size - 1) // page_size
        print(
            f"{database_path.name} / {table} — řádky {first_row}-{last_row} z {total_records} "
            "| ↑/↓ select, Enter show, q quit"
        )
        print(header)
        for index, line in enumerate(record_lines):
            rendered_line = f"{'>' if index == selected else ' '} {line}"
            if index == selected:
                terminal.y(rendered_line)
            else:
                print(rendered_line)
        context = terminal.color("y", f"{database_path.name}/{table}")
        current_page = terminal.color("y", page_number)
        print(f"\npage: {current_page} / {page_count} | ← previous | → next | q quit || {context}")
        key = read_terminal_key()
        if key in {"q", "escape"}:
            return 0
        if key == "\r":
            browse_records(database_path, table, rows[selected][id_field])  # type: ignore[index]
        elif key == "left" and offset > 0:
            offset = max(0, offset - page_size)
            _fields, id_field, rows = list_records(database_path, table, page_size, offset)
            selected = min(selected, len(rows) - 1)
        elif key == "right" and offset + len(rows) < total_records:
            offset += page_size
            _fields, id_field, rows = list_records(database_path, table, page_size, offset)
            selected = min(selected, len(rows) - 1)
        elif key == "up":
            if selected > 0:
                selected -= 1
            elif offset > 0:
                offset = max(0, offset - page_size)
                _fields, id_field, rows = list_records(database_path, table, page_size, offset)
                selected = len(rows) - 1
        elif key == "down":
            if selected < len(record_lines) - 1:
                selected += 1
            elif offset + len(rows) < total_records:
                offset += page_size
                _fields, id_field, rows = list_records(database_path, table, page_size, offset)
                selected = 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_output()
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SQLiteBrowserError("Limit must be a positive integer.")
    database_dir, max_list_rows = load_config(DEFAULT_CONFIG_PATH)
    if args.list:
        if args.database or any((args.table, args.base, args.show, args.delete, args.export, args.limit)):
            raise SQLiteBrowserError("--list cannot be combined with a database or another database action.")
        databases = sorted(item.name for item in database_dir.glob("*.db") if item.is_file())
        print(f"Databases: {database_dir}")
        print(*databases, sep="\n") if databases else print("No .db files found.")
        return 0
    if args.database is None:
        raise SQLiteBrowserError("NAME.db is required unless --list is used.")
    database_path = resolve_database(database_dir, args.database)
    if args.table is None:
        tables = list_tables(database_path)
        if len(tables) > 1:
            print(f"Database: {database_path}\nTables:")
            print(*tables, sep="\n")
            print(f"\nChoose a table, for example: python cli_sqlite.py {args.database} --table {tables[0]}")
            return 0
    table = select_table(database_path, args.table)

    if args.delete is not None:
        if delete_record(database_path, table, args.delete):
            print(f"Record deleted: {args.delete}")
            return 0
        print(f"Record #{args.delete} was not found in {table}.", file=sys.stderr)
        return 1

    if args.export is not None:
        record_id, output_file = args.export
        return export_record(database_path, table, record_id, resolve_export_path(output_file))

    if args.show is not None:
        id_field, row = get_record(database_path, table, args.show)
        if row is None:
            print(f"Record #{args.show} was not found in {table} ({id_field}).", file=sys.stderr)
            return 1
        if sys.stdin.isatty() and sys.stdout.isatty():
            browse_records(database_path, table, row[id_field])
        else:
            render_record(database_path, table, row)
        return 0

    page_size = args.limit if args.limit is not None else max_list_rows
    fields, id_field, rows = list_records(database_path, table, page_size)
    total_records = count_records(database_path, table)
    base_config = resolve_base_config(database_dir, database_path, args.base)
    list_columns = load_list_columns(base_config, fields)
    lines = format_records(rows, list_columns)
    last_row = len(rows)
    print(
        f"Database: {database_path}\nTable: {table}\nRecords: {total_records} "
        f"(showing 1-{last_row})\nBase: {base_config.name}"
    )
    if not rows:
        print("No records found.")
        return 0
    print(*lines, sep="\n")
    return interactive_select(database_path, table, id_field, rows, page_size, total_records, list_columns)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SQLiteBrowserError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)

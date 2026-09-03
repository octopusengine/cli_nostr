#!/usr/bin/env python3
"""Interactive test client for receiving and replying to local Nostr DMs.

It deliberately reuses the CLI's setup, NIP-17 receiver, message lifecycle,
and reply action. It is a terminal exercise for the same workflow a future
agent integration will use; it is not an autonomous bot.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Sequence

import cli_nostr as cli
from lib.wrapp_nostr_db import (
    NostrMessageDatabaseError,
    format_message_rows,
    list_messages,
    message_event_ids,
    message_summary,
)
from lib.wrapp_terminal import Terminal


__version__ = "0.1.3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interactive NIP-17 receive-and-reply test client using cli_nostr configuration."
    )
    parser.add_argument("--all", action="store_true", help="show all saved messages at startup without asking")
    parser.add_argument("--user", default=cli.DEFAULT_PROFILE_NAME, metavar="PROFILE", help="local profile (default: user1)")
    parser.add_argument("--env", type=Path, default=cli.DEFAULT_ENV_PATH, metavar="PATH", help=".env file (default: .env)")
    parser.add_argument("--profiles", type=Path, default=cli.DEFAULT_PROFILES_PATH, metavar="PATH", help="profiles JSON file")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="show relay receive diagnostics")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def cli_receive_args(options: argparse.Namespace) -> argparse.Namespace:
    """Build cli_nostr's receive arguments so setup behavior remains identical."""

    arguments = ["--receive", "--user", options.user, "--env", str(options.env), "--profiles", str(options.profiles)]
    arguments.extend("-v" for _ in range(options.verbose))
    args = cli.build_parser().parse_args(arguments)
    cli.apply_setup(args)
    args.stop_after_message = True
    args.suppress_received_output = True
    args.suppress_wait_output = True
    return args


def read_choice(prompt: str, default: str = "") -> str:
    """Read one terminal answer, treating EOF as a request to exit safely."""

    try:
        value = input(prompt).strip()
    except EOFError:
        return "exit"
    return value or default


def startup_rows(args: argparse.Namespace, show_all: bool) -> list[object]:
    try:
        rows = list_messages(args.db, args.db_limit)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        raise cli.CliNostrError(str(error)) from error
    if show_all:
        return rows
    return [row for row in rows if row["direction"] == "received" and not row["handled_at"] and not row["replied_at"]]


def print_rows(rows: list[object], *, heading: str) -> None:
    print(heading)
    if not rows:
        print("  (none)")
        return
    terminal = Terminal()
    for row, line in zip(rows, format_message_rows(rows)):
        terminal.print("c" if row["direction"] == "received" else "g", f"  {line}")


def handle_message(args: argparse.Namespace, uid: int) -> bool:
    """Let the person leave, handle, or reply to one received message."""

    args.db_msg_show = uid
    cli.show_message(args)
    answer = read_choice("Reply text [Enter=wait, /done=handled, /exit=quit]: ")
    answer_command = answer.casefold()
    if answer_command in {"/quit", "/exit"}:
        return False
    if not answer:
        args.wait_immediately = True
        return True
    if answer_command.startswith("/") and answer_command != "/done":
        print("Reply text beginning with '/' is a command-style input and was not sent.")
        return True
    report = read_choice("Handling report: ")
    if report.casefold() in {"/quit", "/exit"}:
        return False
    if not report:
        print("A handling report is required; message remains pending.")
        return True
    args.msg_done = (uid, report)
    cli.mark_message_done(args)
    if answer_command == "/done":
        return True
    args.msg_reply = (uid, answer)
    cli.reply_to_message(args)
    return True


def handle_pending_messages(args: argparse.Namespace, rows: list[object]) -> bool:
    """Offer the reply flow only for incoming messages not already handled."""

    for row in rows:
        if row["direction"] != "received" or row["handled_at"] or row["replied_at"]:
            continue
        print(f"\n--- pending message #{row['uid']} ---")
        if not handle_message(args, int(row["uid"])):
            return False
        if getattr(args, "wait_immediately", False):
            return True
    return True


def show_relays(args: argparse.Namespace) -> None:
    """Probe configured relays and print optional NIP-11 software metadata."""

    try:
        relays = cli.load_relays(args.relays)
    except cli.CliNostrError as error:
        print(f"Error: {error}", file=sys.stderr)
        return
    terminal = Terminal()
    print(f"Configured relays: {len(relays)}")
    available = 0
    for relay_url in relays:
        ok, detail, elapsed = cli.probe_relay(relay_url, args.timeout, args.verbose)
        if not ok:
            terminal.print("r", f"ERROR {relay_url} | {detail} | {elapsed:.2f} s")
            continue
        available += 1
        try:
            info, info_status = cli.nostr.fetch_relay_info(relay_url, args.timeout)
        except cli.CliNostrError as error:
            info, info_status = None, str(error)
        implementation = "not published"
        if info is not None:
            software = info.get("software")
            version = info.get("version")
            name = info.get("name")
            parts = [str(value) for value in (software, version) if isinstance(value, str) and value]
            implementation = " ".join(parts) or (str(name) if isinstance(name, str) and name else "published without software/version")
        terminal.print("g", f"OK {relay_url} | WebSocket {elapsed:.2f} s | {implementation} | {info_status}")
    args.last_relay_summary = f"{available}/{len(relays)} WebSocket relay(s) available"


def show_friends(args: argparse.Namespace) -> None:
    """Print configured NIP-17 contacts; they are public-key identifiers only."""

    try:
        friends = cli.load_friends(args.friends)
    except cli.CliNostrError as error:
        print(f"Error: {error}", file=sys.stderr)
        return
    terminal = Terminal()
    print(f"Configured friends: {len(friends)}")
    for name, public_key in sorted(friends.items(), key=lambda item: item[0].casefold()):
        terminal.print("w", f"{name}: {public_key}")


def show_user(args: argparse.Namespace) -> None:
    """Show the active local Nostr identity without exposing its private key."""

    profile = args.user_profile
    print(f"Profile: {profile.identifier} ({profile.name})")
    print(f"Public key (npub): {profile.pub_key}")
    try:
        public_key_hex = cli.friend_public_key(profile.pub_key).hex()
    except cli.CliNostrError as error:
        print(f"Public key (hex): unavailable ({error})")
    else:
        print(f"Public key (hex): {public_key_hex}")
    print(f"Private key variable: {profile.priv_key_name}")
    print("DM inbox relays:")
    for relay_url in profile.dm_relays:
        print(f"  {relay_url}")


def show_status(args: argparse.Namespace) -> None:
    """Print a short local overview, including the latest relay check in this run."""

    try:
        summary = message_summary(args.db)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return
    profile = args.user_profile
    print(f"Profile: {profile.identifier} ({profile.name})")
    print(
        "Messages: "
        f"{summary['total']} total | {summary['received']} received | {summary['pending']} pending | "
        f"{summary['handled']} handled | {summary['replied']} replied"
    )
    relay_summary = getattr(args, "last_relay_summary", None)
    print(f"Relay check: {relay_summary or 'not checked in this messenger run'}")


def parse_message_id(parts: list[str], command: str) -> int | None:
    """Parse a local positive database ID or print concise command usage."""

    if len(parts) != 2:
        print(f"Usage: {command} ID")
        return None
    try:
        uid = int(parts[1])
    except ValueError:
        uid = 0
    if uid < 1:
        print("Message ID must be a positive integer.")
        return None
    return uid


def show_history(args: argparse.Namespace, parts: list[str]) -> None:
    """List recent message history, optionally limited by ``/history COUNT``."""

    if len(parts) > 2:
        print("Usage: /history [COUNT]")
        return
    limit = args.db_limit
    if len(parts) == 2:
        try:
            limit = int(parts[1])
        except ValueError:
            limit = 0
        if limit < 1:
            print("History count must be a positive integer.")
            return
    try:
        rows = list_messages(args.db, limit)
    except (NostrMessageDatabaseError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return
    print_rows(rows, heading="Message history:")


def execute_command(args: argparse.Namespace, command: str) -> bool | None:
    """Handle a slash command; return False only when the user chose to exit."""

    parts = command.casefold().split()
    name = parts[0] if parts else ""
    if name.startswith("//"):
        name = f"/{name[2:]}"
    if name in {"/quit", "/exit"}:
        return False
    if name in {"/info", "/doctor", "/i"}:
        if len(parts) != 1:
            print(f"Usage: {name}")
        else:
            cli.doctor(args)
        return True
    if name in {"/relays", "/relay", "/r"}:
        if len(parts) != 1:
            print(f"Usage: {name}")
        else:
            show_relays(args)
        return True
    if name in {"/publish-relays", "/dm-relays-publish"}:
        if len(parts) != 1:
            print(f"Usage: {name}")
        else:
            try:
                cli.publish_dm_relay_list(args)
            except cli.CliNostrError as error:
                print(f"Error: {error}", file=sys.stderr)
        return True
    if name in {"/inbox", "/dm-inbox"}:
        if len(parts) != 1:
            print(f"Usage: {name}")
        else:
            try:
                cli.inspect_dm_inbox(args)
            except cli.CliNostrError as error:
                print(f"Error: {error}", file=sys.stderr)
        return True
    if name == "/sync":
        if len(parts) != 1:
            print("Usage: /sync")
        else:
            try:
                cli.sync_friend_messages(args)
            except cli.CliNostrError as error:
                print(f"Error: {error}", file=sys.stderr)
        return True
    if name in {"/friends", "/f"}:
        if len(parts) != 1:
            print("Usage: /friends")
        else:
            show_friends(args)
        return True
    if name == "/user":
        if len(parts) != 1:
            print("Usage: /user")
        else:
            show_user(args)
        return True
    if name in {"/status", "/s"}:
        if len(parts) != 1:
            print("Usage: /status")
        else:
            show_status(args)
        return True
    if name == "/pending":
        if len(parts) != 1:
            print("Usage: /pending")
        else:
            print_rows(startup_rows(args, False), heading="Pending received messages:")
        return True
    if name in {"/history", "/h"}:
        show_history(args, parts)
        return True
    if name == "/show":
        uid = parse_message_id(parts, "/show")
        if uid is not None:
            args.db_msg_show = uid
            try:
                cli.show_message(args)
            except cli.CliNostrError as error:
                print(f"Error: {error}", file=sys.stderr)
        return True
    if name == "/done":
        uid = parse_message_id(parts, "/done")
        if uid is not None:
            report = read_choice("Handling report: ")
            if report.casefold() not in {"/quit", "/exit"} and report:
                args.msg_done = (uid, report)
                try:
                    cli.mark_message_done(args)
                except cli.CliNostrError as error:
                    print(f"Error: {error}", file=sys.stderr)
        return True
    if name == "/reply":
        uid = parse_message_id(parts, "/reply")
        if uid is not None:
            try:
                return handle_message(args, uid)
            except cli.CliNostrError as error:
                print(f"Error: {error}", file=sys.stderr)
        return True
    if name == "/help":
        print("Enter waits for one new message. Commands:")
        terminal = Terminal()
        commands = (
            ("/status  (/s)", "show profile, message counts, and the latest relay check"),
            ("/pending", "list received messages that are not yet handled"),
            ("/history [COUNT]  (/h [COUNT])", "list recent message history"),
            ("/show ID", "show all saved fields for one message"),
            ("/done ID", "record handling time and a short handling report"),
            ("/reply ID", "handle and reply to one received message"),
            ("/info  (/i)", "check local profile, files, databases, and agent policy"),
            ("/relays  (/r)", "check relay WebSockets and published relay software info"),
            ("/publish-relays", "publish this profile's NIP-17 DM inbox relay list"),
            ("/inbox", "inspect NIP-17 envelope IDs on inbox relays without saving them"),
            ("/sync", "fetch and save valid NIP-17 messages in the configured history window"),
            ("/friends  (/f)", "list configured public-key contacts"),
            ("/user", "show the active profile, npub/hex public keys, and DM inbox relays"),
            ("/exit", "stop waiting and quit the messenger"),
        )
        for command_name, description in commands:
            print(f"  {terminal.style(command_name, fg='y')}  {terminal.style(description, fg='g')}")
        return True
    print("Unknown command. Use /help; commands must start with '/'.")
    return True


def wait_for_message(args: argparse.Namespace) -> tuple[bool, list[object]]:
    """Receive in a worker while Windows console commands stay available."""

    try:
        import msvcrt
    except ImportError:  # pragma: no cover - the project currently targets Windows
        msvcrt = None

    args.known_event_ids = message_event_ids(args.db)
    before = set(args.known_event_ids)
    args.receive_cancel_event = threading.Event()
    result: dict[str, object] = {"error": None}

    def receive() -> None:
        try:
            cli.receive_friend_messages(args)
        except Exception as error:  # returned on the terminal thread below
            result["error"] = error

    worker = threading.Thread(target=receive, name="nostr-receive", daemon=True)
    worker.start()
    print("Waiting for one new message. Type /help for commands; /exit stops waiting and quits.")
    line = ""
    exit_requested = False
    while worker.is_alive():
        if msvcrt is None:
            worker.join(timeout=0.2)
            continue
        while msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in {"\r", "\n"}:
                print()
                command, line = line, ""
                if command:
                    command_result = execute_command(args, command)
                    if command_result is False:
                        args.receive_cancel_event.set()
                        exit_requested = True
                    elif worker.is_alive():
                        print("Waiting for one new message. Type /help for commands; /exit stops waiting and quits.")
                continue
            if key == "\x03":
                args.receive_cancel_event.set()
                exit_requested = True
                continue
            if key in {"\b", "\x7f"}:
                if line:
                    line = line[:-1]
                    print("\b \b", end="", flush=True)
                continue
            if key >= " ":
                line += key
                print(key, end="", flush=True)
        time.sleep(0.05)
    worker.join(timeout=1)
    error = result["error"]
    if error is not None:
        raise error  # handled by the caller with the other receive errors
    rows = [row for row in startup_rows(args, True) if row["event_id"] not in before]
    return exit_requested, rows


def choose_startup_view(args: argparse.Namespace, show_all: bool) -> bool | None:
    """Choose the first message view while also accepting slash commands."""

    if show_all or not sys.stdin.isatty():
        return show_all
    while True:
        choice = read_choice("Show [n]ew pending messages or [a]ll history? [n] | /help: ", "n")
        if choice.startswith("/"):
            if execute_command(args, choice) is False:
                return None
            continue
        if choice.casefold() in {"n", "new"}:
            return False
        if choice.casefold() in {"a", "all"}:
            return True
        print("Choose n, a, or a slash command such as /help.")


def run(options: argparse.Namespace) -> int:
    args = cli_receive_args(options)
    show_all = choose_startup_view(args, options.all)
    if show_all is None:
        return 0

    initial = startup_rows(args, show_all)
    print_rows(initial, heading="Saved messages:" if show_all else "Pending received messages:")
    if not handle_pending_messages(args, initial):
        return 0

    while True:
        if not getattr(args, "wait_immediately", False):
            command = read_choice("\nEnter=wait | /status | /pending | /help | /exit: ")
            if command:
                result = execute_command(args, command)
                if result is False:
                    return 0
                continue
        args.wait_immediately = False
        try:
            exit_requested, rows = wait_for_message(args)
            if exit_requested:
                return 0
        except KeyboardInterrupt:
            cancel_event = getattr(args, "receive_cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
            time.sleep(0.15)
            print("\nWaiting interrupted.")
            continue
        except (cli.CliNostrError, NostrMessageDatabaseError, OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
            continue
        if not rows:
            print("No new message saved.")
            continue
        print_rows(rows, heading="New message:")
        if not handle_pending_messages(args, rows):
            return 0


def main(argv: Sequence[str] | None = None) -> int:
    cli.configure_console_encoding()
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except cli.CliNostrError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)

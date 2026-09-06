# cli_nostr

A unified CLI for Nostr experiments. Source material and working inspiration
remain in `inspirace/`; the current entry point is `cli_nostr.py`.

## Architecture

`cli_nostr.py` owns argument parsing, user-facing orchestration, and the
composition of local storage. `lib/wrapp_nostr.py` contains Nostr-specific
relay, key, contact, and publishing primitives, and intentionally has no
dependency on another local wrapper. `lib/wrapp_nostr_db.py` is selected by the
CLI only when persistent local storage is needed.

`data_nostr/profiles.json` holds named local profiles. It stores an `npub` and the
name of the corresponding private-key variable in `.env`, never the private
key itself. Optional public Nostr metadata may later include `display_name`,
`about`, `picture`, `banner`, `website`, `nip05`, and `lud16`.

Create a complete new profile and its key with:

```powershell
python cli_nostr.py --profile-create user2 "Moje jméno" NOSTR_KEY2
```

The command writes the private key to `.env`, derives its public `npub`, and
adds the profile to `data_nostr/profiles.json`. It refuses existing profile names
and key variables unless `--force` is supplied for the key variable.

## Phase 1 – local key and `.env`

```powershell
python cli_nostr.py --key-create
python cli_nostr.py --key-info
python cli_nostr.py --user user1 --key-info
python cli_nostr.py --config
python cli_nostr.py --doctor
python cli_nostr.py --lib-version
python cli_nostr.py --examples
python cli_nostr.py --connect -v
python cli_nostr.py --stream -v
python cli_nostr.py --event "Hello, Nostr!"
python cli_nostr.py --event event.txt
Get-Content event.txt | python cli_nostr.py --event -
python cli_nostr.py --msg holinky "test 1 from Dell"
python cli_nostr.py --db-msg
python cli_nostr.py --db-msg-show 5
python cli_nostr.py --msg-done 5 "Saved requested information"
python cli_nostr.py --msg-reply 5 "Done."
python nostr_messenger.py
python cli_nostr.py --db-str
python cli_nostr.py --db-show 1
python cli_nostr.py --flw-add holinky npub1…
python cli_nostr.py --db-flw
python cli_nostr.py --follow-stream
python cli_nostr.py --follow-stream 3
```

`--key-create` stores a randomly generated, valid secp256k1 private key as
`NOSTR_KEY` in `.env`. An existing key is never overwritten without an explicit
`--force`. The private key is not printed, and `.env` is ignored by Git.

## Profiles / keys

### Standalone key generation and vanity search

```powershell
python cli_nostr.py --key-generate
python cli_nostr.py --key-generate --vanity "acx"
python cli_nostr.py --key-generate --vanity "acx" 5
python cli_nostr.py --key-generate --vanity "*acx" 2
python cli_nostr.py --key-generate --vanity "jame|jam3|j4m3" 3
```

These commands run offline without profiles or configuration. Plain generation
creates one key; vanity defaults to three distinct public keys. A plain pattern
matches immediately after `npub1`; a leading `*` searches anywhere after `npub1`,
including the checksum. Patterns are lowercase literal strings, not regexes.
Separate alternatives with `|` inside quotes (required to prevent shell piping),
for example `"jame|jam3|j4m3"` or `"*jame|*jam3|*j4m3"`.
Each candidate is checked against all alternatives; any match is accepted.
N is the total number of distinct addresses across all alternatives, not per
alternative. Overlapping alternatives count an address only once. Numeric
patterns are unambiguous: `--vanity "234|567" 3` finds three total addresses.
The Bech32 alphabet is `qpzry9x8gf2tvdw0s3jn54khce6mua7l`:
`b`, `i`, `o`, and `1` are unavailable, so `abc` is rejected.
Invalid-pattern errors include the complete allowed alphabet. Bech32 uses the
implementation bundled with the existing `pynostr` dependency; no standalone
`bech32` package or duplicate local codec is needed.

Output is `nostr_temp_YYMMDD_HHMM.txt` in the current directory, using local time.
Existing files are preserved by adding `_1`, `_2`, etc. Each record contains
the private key as 64 hex characters, the `npub`, then the public key as 64 hex
characters, with a blank line between records. This file contains plaintext
private keys and is excluded by `.gitignore`. Each match is flushed to disk
before its public address is announced. Progress appears every 100,000 attempted keys;
Ctrl+C retains saved matches and returns exit code 130. Long patterns can take
a very long time: a short prefix of length k needs roughly 32^k attempts per match.

### Changes imported from `zmeny` and portability

`--relays` lists configured relays without contacting them. The optional
`"stream": {"allowed": ["npub1…", "64-character public hex key"]}` section in
`cli_nostr.json` adds public authors to `--follow-stream`, alongside saved
follows. Duplicate public keys are merged, preserving saved follow names.
This setting does not change direct-message authorization. The imported
`suppress_wait_output` behavior supports quiet embedding by callers.

For transfer back to the other project, copy `cli_nostr.py`,
`lib/nostr_runner.py`, `lib/wrapp_nostr.py`, `lib_nostr/tools.py`, and
`requirements_nostr.txt`. Also carry over the `nostr_temp_*.txt` Git ignore rule.
The receiving project must also retain this project's existing `lib` wrappers
and `lib_nostr` package used by those files. Install the CLI dependencies with
`python -m pip install -r requirements_nostr.txt`; the existing
`requirements.txt` additionally includes the PyQt6 messenger dependency.
`stream.allowed` is optional, so existing configuration remains usable.
Generated keys, `.env`, and local databases are not part of the transfer.

Run offline regression checks with `python -m unittest discover -s tests -v`.

The default identity is profile `user1` in `data_nostr/profiles.json`. Use
`--user PROFILE` to select another profile. The selected profile supplies its
display name, public `npub`, and `priv_key_name`; the latter names the private
key variable in `.env`. Its complete metadata is held for the whole CLI run,
while the private key itself remains only in `.env`.

## Public text events

`--event` sends a standard public Nostr kind-1 text event from the active
profile (by default `user1`) to the available configured relays. Its one
argument is literal text, an existing UTF-8 text file, or `-` to read the
event body from standard input.

```powershell
python cli_nostr.py --event "Hello, Nostr!"
python cli_nostr.py --event event.txt
Get-Content event.txt | python cli_nostr.py --event -
```

```powershell
python cli_nostr.py --user user1 --key-info
python cli_nostr.py --user user1 --msg holinky "message from this identity"
python cli_nostr.py --user user1 --receive
```

Install the dependencies to display the derived `npub` and to use relay
commands:

```powershell
python -m pip install -r requirements.txt
```

## Relays and public stream

The relay list is stored in `data_nostr/relays.json`. `-c` / `--connect` only opens
a WebSocket connection and does not publish anything. `-v` / `--verbose` adds
progress, timing, and error details. `-s` / `--stream` selects the first
reachable relay and prints its three most recent public kind-1 messages.

```powershell
python cli_nostr.py -c -v
python cli_nostr.py -s -v
python cli_nostr.py -s '#nostr'
python cli_nostr.py -V
```

`-s` accepts an optional hashtag such as `'#nostr'` (the quotes prevent
PowerShell from treating `#` as a comment). This asks the relay for the Nostr
standard `#t` tag filter and displays only notes carrying the corresponding
`["t", "nostr"]` tag. Notes that merely contain `#nostr` as untagged text are
not included.

## Private message to a friend

`data_nostr/friends.json` maps a name to a public key. It supports the concise form
`{"holinky": "npub1…"}` and a list of records such as `[ {"name": "holinky",
"key": "npub1…"} ]`. `-m` creates and sends an encrypted NIP-17 message (a
gift wrap for the recipient and a local copy). `num_msg_relays` in
`cli_nostr.json` sets the number of relays used for a message; the default is
3. For every selected relay, the CLI waits for a NIP-01 `OK` response for up to
the configured `timeout`.

```powershell
python cli_nostr.py -m holinky "test 1 from Dell" -v
```

## Local message history

`data_nostr/nostr_msg.db` is a local SQLite database. After a message is sent, its
text and delivery status are stored; received text is stored after successful
decryption. The file therefore contains readable direct-message contents and
is listed in `.gitignore`.

```powershell
python cli_nostr.py --db-msg
python cli_nostr.py --db-str
```

Incoming direct messages have an explicit local lifecycle. `--db-msg-show ID`
shows all saved fields. After completing any requested work, record the outcome
before replying:

```powershell
python cli_nostr.py --msg-done 5 "Export created successfully"
python cli_nostr.py --msg-reply 5 "The export is ready."
```

This stores when the message was handled, a short handling report, when the
reply was attempted, its NIP-17 event ID, delivery status, and reply text.
`--msg-reply` deliberately requires the received message to be marked handled
first. A second recorded reply requires `--force`.

## Interactive messenger test

`nostr_messenger.py` is a small terminal client built on the same CLI setup,
receiver, lifecycle storage, and `nostr_runner` sender. At startup it asks
whether to show only pending received messages (the default) or all history.
It then waits for one previously unseen NIP-17 message at a time, displays it,
and offers a reply. Before a reply, it asks for the required handling report.

```powershell
python nostr_messenger.py
python nostr_messenger.py --all
python nostr_messenger.py --user user2 -v
```

Press Enter to wait for the next message. Type `exit` or `quit` at a prompt to
end the client; type `/info` (or `/doctor`) for local setup status. Type
`/relays` to check configured relay WebSockets and optional NIP-11
software/version metadata, or `/friends` to list public-key contacts from
`friends.json`. Commands always start with `/`; `/exit` ends the client.
`Ctrl+C` leaves the current wait and returns to its menu.

While waiting for a message, type a slash command and press Enter — for
example `/status`, `/pending`, `/relays`, or `/help`. `/exit` cancels the
current receive operation and ends the messenger. The messenger does not show
a countdown.

Useful message commands are `/status`, `/pending`, `/history [COUNT]`,
`/show ID`, `/done ID`, and `/reply ID`. The last two prompt for the handling
report and reply text as needed; `/reply ID` follows the same local lifecycle
rules as `--msg-reply`.

## NIP-17 DM relay list

Every profile declares its DM inbox relays in `data_nostr/profiles.json` as
`dm_relays`. Publish the selected profile's public NIP-17 relay-list event
(`kind 10050`) before expecting other clients to discover where to deliver a
direct message:

```powershell
python cli_nostr.py --dm-relays-publish
python cli_nostr.py --user user2 --dm-relays-publish
```

The messenger equivalent is `/publish-relays`. This is a public metadata
publish operation, not a message send. The list is per profile and should stay
small (the configured three relays are appropriate).

To diagnose a possible missed delivery without changing local state, inspect
the gift-wrap IDs currently returned by the profile's inbox relays:

```powershell
python cli_nostr.py --dm-inbox
```

The messenger equivalent is `/inbox`. It neither decrypts the envelopes nor
writes them to SQLite; it only compares returned NIP-17 event IDs with the
local message database.

To fetch the configured historical window and save every valid, previously
unknown NIP-17 message, use a bounded history sync. It stops after the selected
relays finish their stored-event response rather than waiting for new traffic:

```powershell
python cli_nostr.py --sync -v
```

The messenger equivalent is `/sync`.
It ends with the exact number of messages newly added to the local database.

## Integration preparation

`lib/nostr_runner.py` contains a presentation-free NIP-17 send operation. It
accepts an already normalized private key and explicit relay URLs, returns only
non-secret delivery data, and does not read `.env`, print, or write SQLite.
`cli_nostr.py` uses it for `--msg`; another local integration can use the same
module without invoking the CLI.

`--doctor` validates the selected profile, key presence, local JSON files,
database directories, and reserved agent policy without contacting a relay.
Use `--connect` for live relay checks. The `agent` block in `cli_nostr.json` is
reserved for a future external integration; it is disabled by default and does
not cause the CLI to send messages automatically.

## Local follows

`data_nostr/nostr_follows.db` stores manually added name/public-key pairs. Names are
case-insensitive unique; a repeated `--flw-add` updates the existing entry.
`-f` / `--follow-stream` opens a live subscription for all stored follows,
prints and saves every newly received Nostr event, and stops after
`follow_stream_timeout` seconds (100 by default in `cli_nostr.json`). Give it
an optional positive whole number of days to also request stored events from
that preceding period before it continues with the live stream; for example,
`-f 3` includes the last three days and `-f 14` the last fourteen days.
Set `save_stream_to_db` to `false` in `cli_nostr.json` to keep both public
stream commands terminal-only without writing `nostr_stream.db`.

```powershell
python cli_nostr.py --flw-add holinky npub1…
python cli_nostr.py --db-flw
python cli_nostr.py -f 3
python cli_nostr.py -f 14
```

The versioned base SQL definitions are `data_nostr/nostr_msg.json`,
`data_nostr/nostr_stream.json`, and `data_nostr/nostr_follows.json`.

## SQLite browser

`cli_sqlite.py` lists, inspects, and deletes records in databases below the
`database_dir` configured in `cli_sqlite.json` (default: `./data_nostr`). Its normal
list view uses `NAME_base.json` next to `NAME.db`; the JSON file selects the
shown columns and their widths. Select another layout with `--base`.

```powershell
python cli_sqlite.py --list
python cli_sqlite.py nostr_msg.db
python cli_sqlite.py nostr_msg.db --show 1
python cli_sqlite.py nostr_stream.db --show 1
python cli_sqlite.py nostr_msg.db --base nostr_msg_base2.json
python cli_sqlite.py nostr_msg.db --delete 1
python cli_sqlite.py nostr_msg.db --export 1 exported_message.json
```

In an interactive terminal, the list supports Up/Down and Enter to open the
selected record. In the detail view use Left/Right for previous/next, `d` to
delete after confirmation, and `q` or Escape to quit. Dates and times are
shown as `RR-MM-DD hh:mm`. For a database containing several tables, add
`--table NAME`. The default number of rows in a list is set by
`max_list_rows` in `cli_sqlite.json` (20); override it once with `--limit N`.
In the interactive list, Up/Down crosses to the preceding or following page
at its first or last row; Left/Right changes the whole page immediately.

## Receiving messages

`-r` / `--receive` opens up to `num_msg_relays` relays in parallel, listens
only for new NIP-17 gift wraps addressed to the selected key, and prints each
incoming message immediately. It does not retain or process messages from the
message pool. `msg_timeout` in `cli_nostr.json` defaults to 100 seconds. During
that interval, a countdown (`9 8 7 … 0`) is printed on one line every ten
seconds.

Because NIP-17 deliberately randomizes gift-wrap timestamps into the past,
`-r` also fetches the preceding fourteen days (`msg_lookback: 1209600`).
Duplicate events received from multiple relays are displayed only once.

```powershell
python cli_nostr.py -r -v
```

## ToDo

- [ ] Add profile and metadata management from `nostr_meta_info.py`.
- [ ] Add publishing of public events from `nostr_publish_event.py`.
- [ ] Add contact and user-data management from `nostr_user.py`.
- [ ] Extend the terminal and database wrappers for additional Nostr actions.

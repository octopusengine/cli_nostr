# cli_nostr

A unified CLI for Nostr experiments. Source material and working inspiration
remain in `inspirace/`; the current entry point is `cli_nostr.py`.

## Architecture

`cli_nostr.py` owns argument parsing, user-facing orchestration, and the
composition of local storage. `lib/wrapp_nostr.py` contains Nostr-specific
relay, key, contact, and publishing primitives, and intentionally has no
dependency on another local wrapper. `lib/wrapp_nostr_db.py` is selected by the
CLI only when persistent local storage is needed.

`nostr/profiles.json` holds named local profiles. It stores an `npub` and the
name of the corresponding private-key variable in `.env`, never the private
key itself. Optional public Nostr metadata may later include `display_name`,
`about`, `picture`, `banner`, `website`, `nip05`, and `lud16`.

Create a complete new profile and its key with:

```powershell
python cli_nostr.py --profile-create user2 "Moje jméno" NOSTR_KEY2
```

The command writes the private key to `.env`, derives its public `npub`, and
adds the profile to `nostr/profiles.json`. It refuses existing profile names
and key variables unless `--force` is supplied for the key variable.

## Phase 1 – local key and `.env`

```powershell
python cli_nostr.py --key-create
python cli_nostr.py --key-info
python cli_nostr.py --user user1 --key-info
python cli_nostr.py --config
python cli_nostr.py --lib-version
python cli_nostr.py --examples
python cli_nostr.py --connect -v
python cli_nostr.py --stream -v
python cli_nostr.py --event "Hello, Nostr!"
python cli_nostr.py --event event.txt
Get-Content event.txt | python cli_nostr.py --event -
python cli_nostr.py --msg holinky "test 1 from Dell"
python cli_nostr.py --db-msg
python cli_nostr.py --db-str
python cli_nostr.py --db-show 1
python cli_nostr.py --flw-add holinky npub1…
python cli_nostr.py --db-flw
python cli_nostr.py --follow-stream
```

`--key-create` stores a randomly generated, valid secp256k1 private key as
`NOSTR_KEY` in `.env`. An existing key is never overwritten without an explicit
`--force`. The private key is not printed, and `.env` is ignored by Git.

## Profiles / keys

The default identity is profile `user1` in `nostr/profiles.json`. Use
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

The relay list is stored in `nostr/relays.json`. `-c` / `--connect` only opens
a WebSocket connection and does not publish anything. `-v` / `--verbose` adds
progress, timing, and error details. `-s` / `--stream` selects the first
reachable relay and prints its three most recent public kind-1 messages.

```powershell
python cli_nostr.py -c -v
python cli_nostr.py -s -v
python cli_nostr.py -V
```

## Private message to a friend

`data/friends.json` maps a name to a public key. It supports the concise form
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

`data/nostr_msg.db` is a local SQLite database. After a message is sent, its
text and delivery status are stored; received text is stored after successful
decryption. The file therefore contains readable direct-message contents and
is listed in `.gitignore`.

```powershell
python cli_nostr.py --db-msg
python cli_nostr.py --db-str
```

## Local follows

`data/nostr_follows.db` stores manually added name/public-key pairs. Names are
case-insensitive unique; a repeated `--flw-add` updates the existing entry.
`-f` / `--follow-stream` opens a live subscription for all stored follows,
prints and saves every newly received Nostr event, and stops after
`follow_stream_timeout` seconds (100 by default in `cli_nostr.json`).
Set `save_stream_to_db` to `false` in `cli_nostr.json` to keep both public
stream commands terminal-only without writing `nostr_stream.db`.

```powershell
python cli_nostr.py --flw-add holinky npub1…
python cli_nostr.py --db-flw
```

The versioned base SQL definitions are `data/nostr_msg.json`,
`data/nostr_stream.json`, and `data/nostr_follows.json`.

## SQLite browser

`cli_sqlite.py` lists, inspects, and deletes records in databases below the
`database_dir` configured in `cli_sqlite.json` (default: `./data`). Its normal
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
`-r` also fetches the preceding three days (`msg_lookback: 259200`). Duplicate
events received from multiple relays are displayed only once.

```powershell
python cli_nostr.py -r -v
```

## ToDo

- [ ] Add profile and metadata management from `nostr_meta_info.py`.
- [ ] Add publishing of public events from `nostr_publish_event.py`.
- [ ] Add contact and user-data management from `nostr_user.py`.
- [ ] Extend the terminal and database wrappers for additional Nostr actions.

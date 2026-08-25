# cli_nostr

A unified CLI for Nostr experiments. Source material and working inspiration
remain in `inspirace/`; the current entry point is `cli_nostr.py`.

## Phase 1 – local key and `.env`

```powershell
python cli_nostr.py --key-create
python cli_nostr.py --key-info
python cli_nostr.py --user NOSTR_KEY2 --key-info
python cli_nostr.py --config
python cli_nostr.py --connect -v
python cli_nostr.py --stream -v
python cli_nostr.py --msg holinky "test 1 from Dell"
python cli_nostr.py --db-msg
python cli_nostr.py --db-str
python cli_nostr.py --db-show 1
```

`--key-create` stores a randomly generated, valid secp256k1 private key as
`NOSTR_KEY` in `.env`. An existing key is never overwritten without an explicit
`--force`. The private key is not printed, and `.env` is ignored by Git.

## Multiple users / keys

`NOSTR_KEY` is the default identity. To choose another key variable from
`.env` for a single CLI run, use `--user`. For example, `--user NOSTR_KEY2`
uses `NOSTR_KEY2` throughout that run. The selection applies to `--key-info`,
`--msg`, and `--receive`; with `--key-create`, it creates a key under the
selected name. It does not modify `.env` or change the default identity for
later runs.

```powershell
python cli_nostr.py --user NOSTR_KEY2 --key-info
python cli_nostr.py --user NOSTR_KEY2 --msg holinky "message from the second identity"
python cli_nostr.py --user NOSTR_KEY2 --receive
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

# Nostr Messenger

`nostr_messenger.py` is an interactive terminal test client for receiving and
replying to NIP-17 direct messages. It is deliberately a human-operated client,
not an autonomous agent. Its purpose is to exercise the same configuration,
transport, database, and message-lifecycle operations that a future MCP or AI
integration can call as a module.

## Start

Run it from the project directory after installing `requirements.txt`:

```powershell
python nostr_messenger.py
python nostr_messenger.py --all
python nostr_messenger.py --user user2 -v
```

The selected profile defaults to `user1`. Profiles live in
`data_nostr/profiles.json`; their private-key environment variable is read from
`.env` by `cli_nostr.py` and is never displayed by the messenger.

At startup the messenger offers the pending incoming messages, or all locally
saved history with `--all`. Press Enter at the main prompt to wait for one
previously unseen NIP-17 message. While waiting, type a slash command and press
Enter; `/exit` cancels the active receive operation and closes the client.

## Message lifecycle

Incoming messages are saved in `data_nostr/nostr_msg.db` before the reply UI is
shown. The intended lifecycle is:

```text
received -> handled (handling report) -> replied
```

The handling report records what was done and its result. A reply is only sent
after that report has been saved. An empty reply prompt does not send an empty
message: it leaves the message pending and returns directly to waiting. The
messenger also refuses reply text beginning with `/`, preventing an accidental
terminal command such as `/status` from being sent as a Nostr message. The
same rules are available in the non-interactive CLI as `--msg-done` and
`--msg-reply`.

Event IDs prevent duplicates: the database has a unique `event_id`, the
receiver ignores the same gift-wrap ID received from multiple relays, and the
messenger seeds a receive pass with all IDs already in the local database. This
does not deduplicate two separately published events that happen to have the
same text.

## Commands

All messenger commands use a slash prefix, so regular text cannot accidentally
be treated as a control command.

| Command | Purpose |
| --- | --- |
| `/status` | Show the selected profile, local message counts, and the latest relay check. |
| `/pending` | List incoming messages that have not been handled. |
| `/history [COUNT]` | List local message history. |
| `/show ID` | Show all saved fields and lifecycle metadata for one local message. |
| `/done ID` | Save a handling report without sending a reply. |
| `/reply ID` | Open one incoming message, request a handling report, and send the reply. |
| `/info` or `/doctor` | Validate local profile, files, databases, and agent-policy configuration without contacting relays. |
| `/relays` | Test configured relay WebSockets and show available NIP-11 software/version metadata. |
| `/publish-relays` | Publish the selected profile's NIP-17 DM relay-list event (`kind:10050`). |
| `/inbox` | Read-only inventory of NIP-17 gift-wrap IDs on the selected profile's inbox relays, compared with local DB IDs. It does not decrypt or save messages. |
| `/sync` | Fetch the configured history window, decrypt and save all valid previously unknown messages, then stop after relay history is complete. |
| `/friends` | List public-key contacts in `data_nostr/friends.json`. |
| `/user` | Show the selected profile, its `npub` and hexadecimal public keys, private-key variable name, and DM inbox relays. |
| `/help` | Show the command list in the terminal. |
| `/exit` or `/quit` | Cancel waiting and close the messenger. |

Short aliases are `/s` for `/status`, `/h` for `/history`, `/i` for `/info`,
`/r` for `/relays`, and `/f` for `/friends`. The same aliases also accept a
second leading slash, for example `//h`.

`/friends` is a contact list for addressing outgoing messages. It is not an
incoming-message whitelist. The reserved `agent.allowed_senders` policy in
`cli_nostr.json` remains the place for a future agent authorization policy.

## Connection to `cli_nostr.py`

The messenger is a thin interactive layer; Nostr operations remain owned by
`cli_nostr.py` and its reusable wrappers.

1. `cli_receive_args()` builds `cli_nostr.py --receive` arguments and calls
   `cli.apply_setup()`. Thus both programs use the same profile, `.env`, relay
   list, database paths, message timeout, and `msg_lookback` setting.
2. `wait_for_message()` runs `cli.receive_friend_messages()` in a background
   thread. This keeps the Windows console responsive to slash commands while
   the Nostr WebSocket receive loop is active.
3. Incoming events are decrypted and persisted by
   `cli.receive_friend_messages()` through `record_local_message()`.
4. `/done` and `/reply` call `cli.mark_message_done()` and
   `cli.reply_to_message()`. The latter sends through
   `lib.nostr_runner.send_nip17_message()` and records the delivery outcome.
5. `/publish-relays`, `/inbox`, and `/sync` call
   `cli.publish_dm_relay_list()`, `cli.inspect_dm_inbox()`, and
   `cli.sync_friend_messages()` respectively.

`lib/nostr_runner.py` is presentation-free: it does not read `.env`, write to
SQLite, or print to the terminal. That separation is the intended boundary for
a future MCP integration. An integration should select its own profile and
authorization policy, call the runner or CLI-level lifecycle functions, and
keep UI and agent decisions outside the Nostr transport layer.

## Relays and local data

The profile field `dm_relays` defines the recipient inbox relays. Publishing it
with `/publish-relays` creates the public NIP-17 `kind:10050` relay-list event
so compatible clients know where to send messages. The configured receive
fan-out, timeout, and historical query window are in `cli_nostr.json`:

```json
{
  "num_msg_relays": 3,
  "msg_timeout": 100,
  "msg_lookback": 1209600
}
```

`msg_lookback` is currently 14 days. It limits how far back this client asks
relays for gift-wraps; it does not determine how long relays retain events.
Reading from a relay does not remove a message. Relay retention is an operator
policy, therefore locally saved messages in `nostr_msg.db` are the durable
working record for this project.

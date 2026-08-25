import json
import secrets
import time

from pynostr.event import Event
from pynostr.key import PrivateKey

try:
    # Used from the unified cli_nostr project.
    from . import nip44
except ImportError:  # pragma: no cover - supports running this prototype directly
    import nip44


KIND_SEAL = 13
KIND_CHAT_MESSAGE = 14
KIND_GIFT_WRAP = 1059


def _random_past_timestamp(max_age_seconds=2 * 24 * 60 * 60):
    return int(time.time()) - secrets.randbelow(max_age_seconds + 1)


def _event_json(event_dict):
    return json.dumps(event_dict, separators=(",", ":"), ensure_ascii=False)


def _event_id(pubkey, created_at, kind, tags, content):
    event = Event(
        pubkey=pubkey,
        created_at=created_at,
        kind=kind,
        tags=tags,
        content=content,
    )
    return event.id


def make_rumor(sender_key, recipient_pubkey_hex, message, relay_url=None):
    tags = [["p", recipient_pubkey_hex]]
    if relay_url:
        tags = [["p", recipient_pubkey_hex, relay_url]]

    rumor = {
        "pubkey": sender_key.public_key.hex(),
        "created_at": int(time.time()),
        "kind": KIND_CHAT_MESSAGE,
        "tags": tags,
        "content": message,
    }
    rumor["id"] = _event_id(
        rumor["pubkey"],
        rumor["created_at"],
        rumor["kind"],
        rumor["tags"],
        rumor["content"],
    )
    return rumor


def seal_rumor(sender_key, recipient_pubkey_hex, rumor):
    conversation_key = nip44.get_conversation_key(sender_key.hex(), recipient_pubkey_hex)
    encrypted_rumor = nip44.encrypt(_event_json(rumor), conversation_key)

    seal = Event(
        pubkey=sender_key.public_key.hex(),
        created_at=_random_past_timestamp(),
        kind=KIND_SEAL,
        tags=[],
        content=encrypted_rumor,
    )
    seal.sign(sender_key.hex())
    return seal


def wrap_seal(recipient_pubkey_hex, seal_event, relay_url=None):
    wrapper_key = PrivateKey()
    conversation_key = nip44.get_conversation_key(wrapper_key.hex(), recipient_pubkey_hex)
    encrypted_seal = nip44.encrypt(_event_json(seal_event.to_dict()), conversation_key)

    tags = [["p", recipient_pubkey_hex]]
    if relay_url:
        tags = [["p", recipient_pubkey_hex, relay_url]]

    gift_wrap = Event(
        pubkey=wrapper_key.public_key.hex(),
        created_at=_random_past_timestamp(),
        kind=KIND_GIFT_WRAP,
        tags=tags,
        content=encrypted_seal,
    )
    gift_wrap.sign(wrapper_key.hex())
    return gift_wrap


def make_gift_wrap(sender_key, recipient_pubkey_hex, message, relay_url=None):
    rumor = make_rumor(sender_key, recipient_pubkey_hex, message, relay_url=relay_url)
    seal = seal_rumor(sender_key, recipient_pubkey_hex, rumor)
    gift_wrap = wrap_seal(recipient_pubkey_hex, seal, relay_url=relay_url)
    return rumor, seal, gift_wrap


def make_sender_copy(sender_key, recipient_pubkey_hex, message, relay_url=None, rumor=None):
    if rumor is None:
        rumor = make_rumor(sender_key, recipient_pubkey_hex, message, relay_url=relay_url)
    seal = seal_rumor(sender_key, sender_key.public_key.hex(), rumor)
    gift_wrap = wrap_seal(sender_key.public_key.hex(), seal, relay_url=relay_url)
    return rumor, seal, gift_wrap


def unwrap_gift_wrap(recipient_key, gift_wrap_event):
    wrapper_pubkey_hex = gift_wrap_event.pubkey
    gift_conversation_key = nip44.get_conversation_key(
        recipient_key.hex(), wrapper_pubkey_hex
    )
    seal_json = nip44.decrypt(gift_wrap_event.content, gift_conversation_key)
    seal_dict = json.loads(seal_json)
    seal = Event.from_dict(seal_dict)
    if not seal.verify():
        raise ValueError("invalid seal signature")
    if seal.kind != KIND_SEAL:
        raise ValueError(f"unexpected seal kind {seal.kind}")
    if seal.tags:
        raise ValueError("seal tags must be empty")

    seal_conversation_key = nip44.get_conversation_key(recipient_key.hex(), seal.pubkey)
    rumor_json = nip44.decrypt(seal.content, seal_conversation_key)
    rumor = json.loads(rumor_json)
    if rumor.get("pubkey") != seal.pubkey:
        raise ValueError("seal pubkey does not match rumor pubkey")
    if rumor.get("id") != _event_id(
        rumor.get("pubkey"),
        rumor.get("created_at"),
        rumor.get("kind"),
        rumor.get("tags"),
        rumor.get("content"),
    ):
        raise ValueError("rumor id mismatch")
    return seal, rumor

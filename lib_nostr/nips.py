#!/usr/bin/env python

"""
nips
https://github.com/nostr-protocol/nips

NIP-01 filtering. Explicitly supports "#e" and "#p" tag filters via `event_refs`
and `pubkey_refs`. Arbitrary NIP-12 single-letter tag filters are also supported via
`add_arbitrary_tag`. If a particular single-letter tag gains prominence, explicit
support should be added. For example:

# arbitrary tag
filter.add_arbitrary_tag('t', [hashtags])
# promoted to explicit support
Filters(hashtag_refs=[hashtags])
:param ids: List[str]
:param kinds: List[EventKind]
:param authors: List[str]
:param since: int
:param until: int
:param event_refs: List[str]
:param pubkey_refs: List[str]
:param limit: int
"""

nip_11_struct = [ "name","description","pubkey","contact","supported_nips","software","version"]
nip_11_struct_adv = [ "name","description","pubkey","contact","supported_nips","software","version","limitation"]

"""
NIP-11
 "name": <string identifying relay>,
 "description": <string with detailed information>,
 "pubkey": <administrative contact pubkey>,
 "contact": <administrative alternate contact>,
 "supported_nips": <a list of NIP numbers supported by the relay>,
 "software": <string identifying relay software URL>,
 "version": <string version identifier>
"""

meta_struct = ["name","username","displayName","about","picture","banner","website","lud16","lud06","nip05"]

"""
NIP-05: Mapping Nostr keys to DNS-based internet identifiers

meta_info

{"display_name":"relay_info",
 "name":"relay9may",
 "username":"relay9may",
 "displayName":"relay_info",
 "about":"relays info",
 "lud16":"...*@walletofsatoshi.com",
 "picture":"https://.../relay9may-300x297.png",
 "banner":"https://nostr.build/i/3e58af31b3f68fb1c5bef12de3c29d8f604f13460bc0cf40fa0b2a20390a0a05.jpg",
 "website":"",
 "nip05":"",
 "lud06":""}


"""
#Events-kind enum:
SET_METADATA = 0
TEXT_NOTE = 1
RECOMMEND_RELAY = 2
CONTACTS = 3
ENCRYPTED_DIRECT_MESSAGE = 4
DELETE = 5
REACTION = 7
BADGE_AWARD = 8
CHANNEL_CREATE = 40
CHANNEL_META = 41
CHANNEL_MESSAGE = 42
CHANNEL_HIDE = 43
CHANNEL_MUTE = 44
REPORT = 1984
ZAP_REQUEST = 9734
ZAPPER = 9735
RELAY_LIST_METADATA = 10002
PROFILE_BADGES = 30008
BADGE_DEFINITION = 30009
LONG_FORM_CONTENT = 30023

import base64
import hashlib
import hmac
import math
import secrets
import struct

from cryptography.hazmat.primitives import hashes, hmac as crypto_hmac
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

from pynostr.key import PrivateKey


NIP44_SALT = b"nip44-v2"
NIP44_VERSION = 2
MIN_PLAINTEXT_SIZE = 1
MAX_PLAINTEXT_SIZE = 65535


def hkdf_extract(ikm, salt):
    mac = crypto_hmac.HMAC(salt, hashes.SHA256())
    mac.update(ikm)
    return mac.finalize()


def hkdf_expand(prk, info, length):
    hkdf = HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info)
    return hkdf.derive(prk)


def get_conversation_key(private_key_hex, public_key_hex):
    private_key = PrivateKey.from_hex(private_key_hex)
    shared_x = private_key.compute_shared_secret(public_key_hex)
    return hkdf_extract(shared_x, NIP44_SALT)


def get_message_keys(conversation_key, nonce):
    if len(conversation_key) != 32:
        raise ValueError("invalid conversation_key length")
    if len(nonce) != 32:
        raise ValueError("invalid nonce length")

    keys = hkdf_expand(conversation_key, nonce, 76)
    return keys[0:32], keys[32:44], keys[44:76]


def calc_padded_len(unpadded_len):
    if unpadded_len <= 0:
        raise ValueError("invalid plaintext length")
    if unpadded_len <= 32:
        return 32

    next_power = 1 << (math.floor(math.log2(unpadded_len - 1)) + 1)
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * (math.floor((unpadded_len - 1) / chunk) + 1)


def pad(plaintext):
    unpadded = plaintext.encode("utf-8")
    unpadded_len = len(unpadded)
    if unpadded_len < MIN_PLAINTEXT_SIZE or unpadded_len > MAX_PLAINTEXT_SIZE:
        raise ValueError("invalid plaintext length")

    prefix = unpadded_len.to_bytes(2, "big")
    suffix = bytes(calc_padded_len(unpadded_len) - unpadded_len)
    return prefix + unpadded + suffix


def unpad(padded):
    if len(padded) < 34:
        raise ValueError("invalid padding")

    unpadded_len = int.from_bytes(padded[0:2], "big")
    unpadded = padded[2 : 2 + unpadded_len]
    if (
        unpadded_len == 0
        or len(unpadded) != unpadded_len
        or len(padded) != 2 + calc_padded_len(unpadded_len)
    ):
        raise ValueError("invalid padding")
    return unpadded.decode("utf-8")


def _rotl32(value, shift):
    return ((value << shift) & 0xFFFFFFFF) | (value >> (32 - shift))


def _quarter_round(state, a, b, c, d):
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 7)


def _chacha20_block(key, nonce, counter):
    constants = b"expand 32-byte k"
    state = list(struct.unpack("<4I", constants))
    state.extend(struct.unpack("<8I", key))
    state.append(counter)
    state.extend(struct.unpack("<3I", nonce))

    working = state.copy()
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)

    output = [(working[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return struct.pack("<16I", *output)


def chacha20(key, nonce, data):
    if len(key) != 32:
        raise ValueError("invalid chacha key length")
    if len(nonce) != 12:
        raise ValueError("invalid chacha nonce length")

    out = bytearray()
    counter = 0
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key, nonce, counter)
        chunk = data[offset : offset + 64]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block)))
        counter = (counter + 1) & 0xFFFFFFFF
    return bytes(out)


def hmac_aad(key, message, aad):
    if len(aad) != 32:
        raise ValueError("AAD associated data must be 32 bytes")
    return hmac.new(key, aad + message, hashlib.sha256).digest()


def encrypt(plaintext, conversation_key, nonce=None):
    if nonce is None:
        nonce = secrets.token_bytes(32)
    chacha_key, chacha_nonce, hmac_key = get_message_keys(conversation_key, nonce)
    ciphertext = chacha20(chacha_key, chacha_nonce, pad(plaintext))
    mac = hmac_aad(hmac_key, ciphertext, nonce)
    return base64.b64encode(bytes([NIP44_VERSION]) + nonce + ciphertext + mac).decode()


def decode_payload(payload):
    if not payload or payload[0] == "#":
        raise ValueError("unknown version")
    if len(payload) < 132 or len(payload) > 87472:
        raise ValueError("invalid payload size")

    data = base64.b64decode(payload, validate=True)
    if len(data) < 99 or len(data) > 65603:
        raise ValueError("invalid data size")
    if data[0] != NIP44_VERSION:
        raise ValueError(f"unknown version {data[0]}")
    return data[1:33], data[33:-32], data[-32:]


def decrypt(payload, conversation_key):
    nonce, ciphertext, mac = decode_payload(payload)
    chacha_key, chacha_nonce, hmac_key = get_message_keys(conversation_key, nonce)
    expected_mac = hmac_aad(hmac_key, ciphertext, nonce)
    if not hmac.compare_digest(expected_mac, mac):
        raise ValueError("invalid MAC")
    return unpad(chacha20(chacha_key, chacha_nonce, ciphertext))

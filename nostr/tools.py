#!/usr/bin/env python

"""
basic tools for agama_nost client

"""
import os
import time, datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


DEBUG = True
WIDTH = 39


def new_key_generate(print_out=True):
        from pynostr.key import PrivateKey
        private_key = PrivateKey()
        public_key = private_key.public_key
        if print_out:
            print("[tools] New keys generate") 
            #self.print_keys_info()
        return public_key.bech32(), private_key


def get_nostr_key(key='NOSTR_KEY', env_file='.env'):
    load_env_file(env_file)

    value = os.environ.get(key)
    if not value:
        env_path = Path(env_file).resolve()
        raise RuntimeError(f"You need to set {key} in {env_path}")
    return value.strip().strip('"').strip("'")


def load_env_file(env_file='.env'):
    if load_dotenv:
        load_dotenv(dotenv_path=env_file)
        return

    env_path = Path(env_file)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def normalize_nostr_private_key(value):
    """Return a hex private key from either hex or nsec notation."""
    key = str(value).strip().strip('"').strip("'")

    if key.startswith("nsec1"):
        try:
            from bech32 import bech32_decode, convertbits
        except ModuleNotFoundError as exc:
            raise RuntimeError("bech32 is required for nsec1 keys. Install requirements with the same Python used to run the script.") from exc

        hrp, data = bech32_decode(key)
        if hrp != "nsec" or data is None:
            raise ValueError("Invalid nsec private key")
        decoded = convertbits(data, 5, 8, False)
        if decoded is None:
            raise ValueError("Invalid nsec private key payload")
        key = bytes(decoded).hex()

    is_hex = len(key) == 64 and all(c in "0123456789abcdefABCDEF" for c in key)
    if not is_hex:
        raise ValueError("NOSTR_KEY must be a 64-character hex key or nsec1... key")

    return key.lower()


def private_key_to_mnemonic(value, language="english"):
    key_hex = normalize_nostr_private_key(value)
    try:
        from mnemonic import Mnemonic
    except ModuleNotFoundError as exc:
        raise RuntimeError("mnemonic is required for BIP39 export. Install requirements with the same Python used to run the script.") from exc

    return Mnemonic(language).to_mnemonic(bytes.fromhex(key_hex))


def mnemonic_to_private_key_hex(words, language="english"):
    try:
        from mnemonic import Mnemonic
    except ModuleNotFoundError as exc:
        raise RuntimeError("mnemonic is required for BIP39 import. Install requirements with the same Python used to run the script.") from exc

    mnemo = Mnemonic(language)
    normalized_words = " ".join(str(words).strip().split())
    if not mnemo.check(normalized_words):
        raise ValueError("Invalid BIP39 mnemonic")

    entropy = mnemo.to_entropy(normalized_words)
    if len(entropy) != 32:
        raise ValueError("Nostr private keys need a 24-word BIP39 mnemonic with 32 bytes of entropy")
    return entropy.hex()


def print_head(label="head"):
    print()
    print("-"*WIDTH)
    print("-"*5, label)
    print("-"*WIDTH)


def short_str(s,l=10): 
    try:
        if len(s)>l*2+12: # 32+
            return str(s[:l])+"..."+str(s[-l:])
        else:
            return s
    except:
        return s


def timestamp_from_now():
    current_timestamp = time.time()
    if DEBUG: print("current_timestamp",current_timestamp)
    one_month_from_now = datetime.datetime.fromtimestamp(current_timestamp) + datetime.timedelta(days=30)
    one_month_from_now_timestamp = int(one_month_from_now.timestamp())
    one_week_from_now = datetime.datetime.fromtimestamp(current_timestamp) + datetime.timedelta(days=7)
    one_week_from_now_timestamp = int(one_week_from_now.timestamp())
    return one_week_from_now_timestamp, one_month_from_now_timestamp


def get_relay_information(url: str, timeout: float = 2, add_url: bool = True):
    # NIP-11 // 'pynostr'
    import requests 
    headers = {'Accept': 'application/nostr+json', 'User-Agent': 'agama_nostr'}
    if "wss" in url:
        metadata_uri = url.replace("wss", "https")
    elif "ws" in url:
        metadata_uri = url.replace("ws", "http")
    else:
        raise Exception(f"{url} is not a websocket url")
    try:
        response = requests.get(metadata_uri, headers=headers, timeout=timeout)

        response.raise_for_status()

        metadata = response.json()
        if add_url:
            metadata["url"] = url
        return metadata
    except requests.exceptions.Timeout:
        # Handle a timeout error
        print("Request timed out. Please try again later.")

    except requests.exceptions.HTTPError as err:
        # Handle an HTTP error
        print(f"HTTP error occurred: {err}")

    except requests.exceptions.RequestException as err:
        # Handle any other request exception
        print(f"An error occurred: {err}")

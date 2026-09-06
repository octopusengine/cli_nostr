import contextlib
import io
import os
from pathlib import Path
import tempfile
from argparse import Namespace
import unittest
from unittest.mock import patch

import cli_nostr as cli
from pynostr.key import PrivateKey, PublicKey


class KeyGenerationTests(unittest.TestCase):
    def test_alternative_options(self):
        self.assertEqual(cli.vanity_options(["jame|jam3|j4m3"]),
                         ([("jame", False), ("jam3", False), ("j4m3", False)], 3))
        self.assertEqual(cli.vanity_options(["234 | *jam3 | 234", "5"]),
                         ([("234", False), ("jam3", True)], 5))
        for pattern in ("|jame", "jame|", "jame||jam3", "jame|abc"):
            with self.assertRaises(cli.CliNostrError):
                cli.vanity_options([pattern])

    def test_alternatives_match_once_and_use_total_count(self):
        original = Path.cwd()
        secrets = [f"{index:064x}" for index in range(1, 4)]
        npubs = [PrivateKey.from_hex(secret).public_key.bech32() for secret in secrets]
        # Each full prefix also overlaps its substring alternative.
        variants = "|".join(npub[5:] for npub in npubs) + "|*" + npubs[0][5:]
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                with patch.object(cli, "generate_private_key_hex", side_effect=[secrets[0], secrets[0], *secrets[1:]]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--key-generate", "--vanity", variants]), 0)
                blocks = next(Path(folder).glob("*.txt")).read_text().strip().split("\n\n")
                self.assertEqual([block.splitlines()[1] for block in blocks], npubs)
            finally:
                os.chdir(original)

    def test_invalid_pattern_prints_alphabet(self):
        with self.assertRaises(cli.CliNostrError) as error:
            cli.vanity_options(["abc"])
        self.assertIn(cli.BECH32_CHARSET, str(error.exception))

    def test_nsec_uses_bundled_codec(self):
        from lib_nostr.tools import normalize_nostr_private_key
        # Preserve trailing zero bytes, and reject checksum/padding errors.
        for secret in ("0" * 63 + "1", "12" * 31 + "00"):
            nsec = PrivateKey.from_hex(secret).bech32()
            self.assertEqual(normalize_nostr_private_key(nsec), secret)
            with self.assertRaises(ValueError):
                normalize_nostr_private_key(nsec[:-1] + ("q" if nsec[-1] != "q" else "p"))

    def test_imported_stream_authors_merge(self):
        public = PrivateKey.from_hex("0" * 63 + "1").public_key
        args = Namespace(follows_db=Path("unused.db"), stream_allowed=[public.bech32(), public.hex()])
        with patch("lib.wrapp_nostr_db.list_all_follows", return_value=[]):
            authors, names = cli.followed_authors(args)
            self.assertEqual(authors, [public.hex()])
        with patch("lib.wrapp_nostr_db.list_all_follows", return_value=[{"pubkey": public.bech32(), "name": "Friend"}]):
            authors, names = cli.followed_authors(args)
            self.assertEqual(authors, [public.hex()])
            self.assertEqual(names[public.hex()], "Friend")

    def test_imported_stream_config_validation(self):
        self.assertEqual(cli._setup_stream_allowed({}), ())
        self.assertEqual(cli._setup_stream_allowed({"stream": {"allowed": [" a ", "a"]}}), ("a",))
        for stream in (None, [], {"allowed": "a"}, {"allowed": [""]}):
            with self.assertRaises(cli.CliNostrError):
                cli._setup_stream_allowed({"stream": stream})

    def test_invalid_patterns_and_counts(self):
        for values in (["abc"], ["*"], ["ACX"], ["a", "0"], ["a", "-2"],
                       ["a", "x"], ["a", "1", "2"], ["q" * 59]):
            with self.subTest(values=values), self.assertRaises(cli.CliNostrError):
                cli.vanity_options(values)

    def test_vanity_requires_generation(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--examples", "--vanity", "acx"])

    def test_real_generation_roundtrip_collision_and_offline(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                with patch.object(cli, "apply_setup", side_effect=AssertionError("must be offline")), contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertEqual(cli.main(["--key-generate"]), 0)
                    self.assertEqual(cli.main(["--key-generate", "--vanity", "q"]), 0)
                    self.assertEqual(cli.main(["--key-generate", "--vanity", "*x", "2"]), 0)
                files = sorted(Path(folder).glob("nostr_temp_*.txt"))
                self.assertEqual(len(files), 3)
                total = 0
                for path in files:
                    blocks = path.read_text().strip().split("\n\n")
                    total += len(blocks)
                    for block in blocks:
                        secret, npub, public_hex = block.splitlines()
                        self.assertEqual(PrivateKey.from_hex(secret).public_key.hex(), public_hex)
                        self.assertEqual(PublicKey.from_npub(npub).hex(), public_hex)
                        self.assertNotIn(secret, log.getvalue())
                        if len(blocks) == 3:
                            self.assertTrue(npub.startswith("npub1q"))
                        if len(blocks) == 2:
                            self.assertIn("x", npub[5:])
                self.assertEqual(total, 6)
            finally:
                os.chdir(original)

    def test_interrupt_preserves_match(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                with patch.object(cli, "generate_private_key_hex", side_effect=["0" * 63 + "1", KeyboardInterrupt]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--key-generate", "--vanity", "*q", "2"]), 130)
                blocks = next(Path(folder).glob("*.txt")).read_text().strip().split("\n\n")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(len(blocks[0].splitlines()), 3)
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()

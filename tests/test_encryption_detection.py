"""is_encrypted must recognise real ciphertext only (RT-16)."""

import base64
import logging
import os
import random
import secrets
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from modules import encryption_utils as eu


def _old_is_encrypted(value: str) -> bool:
    """The previous heuristic, kept to show the cases it got wrong."""
    try:
        if not value:
            return False
        decoded = base64.urlsafe_b64decode(value.encode())
        return len(decoded) >= 32 and len(value) > 50
    except Exception:
        return False


def _random_plain_token(rng: random.Random) -> str:
    raw = rng.randbytes(rng.randint(30, 260))
    token = base64.urlsafe_b64encode(raw).decode()
    if rng.random() < 0.5:
        token = token.rstrip("=")
    prefix = rng.choice(["", "BQ", "ya29.", "AQ", "1//0", "EAAB"])
    return prefix + token


class _KeyDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.key_path = os.path.join(self._tmp.name, ".encryption_key")
        self._patch = patch.object(eu, "_key_file_path", return_value=self.key_path)
        self._patch.start()
        eu._cached_key = None
        eu._cached_fernet = None

    def tearDown(self) -> None:
        self._patch.stop()
        eu._cached_key = None
        eu._cached_fernet = None
        self._tmp.cleanup()


class IsEncryptedTests(_KeyDirCase):
    def test_random_plaintext_tokens_are_not_ciphertext(self) -> None:
        rng = random.Random(1234)
        old_false_positives = 0
        for _ in range(20000):
            token = _random_plain_token(rng)
            self.assertFalse(eu.is_encrypted(token), token)
            old_false_positives += _old_is_encrypted(token)
        # The old heuristic misread a large share of these as ciphertext.
        self.assertGreater(old_false_positives, 1000)

    def test_url_safe_random_strings_are_not_ciphertext(self) -> None:
        for size in range(1, 400, 3):
            for _ in range(10):
                self.assertFalse(eu.is_encrypted(secrets.token_urlsafe(size)))

    def test_real_ciphertext_is_recognised_and_round_trips(self) -> None:
        rng = random.Random(99)
        for _ in range(500):
            plain = secrets.token_urlsafe(rng.randint(1, 300))
            encrypted = eu.encrypt_value(plain)
            self.assertTrue(eu.is_encrypted(encrypted))
            self.assertEqual(eu.ensure_encrypted(encrypted), encrypted)
            self.assertEqual(eu.ensure_decrypted(encrypted), plain)

    def test_legacy_key_ciphertext_still_decrypts(self) -> None:
        legacy_key = eu._derive_legacy_key()
        stored = base64.urlsafe_b64encode(
            Fernet(legacy_key).encrypt(b"legacy-secret")
        ).decode()
        # Key file missing: the decrypt path persists the legacy key.
        self.assertTrue(eu.is_encrypted(stored))
        self.assertEqual(eu.ensure_decrypted(stored), "legacy-secret")
        with open(self.key_path, "rb") as fh:
            self.assertEqual(fh.read().strip(), legacy_key)

    def test_ciphertext_from_other_key_is_still_treated_as_ciphertext(self) -> None:
        eu.encrypt_value("x")  # creates this install's key
        foreign = base64.urlsafe_b64encode(
            Fernet(Fernet.generate_key()).encrypt(b"secret")
        ).decode()
        self.assertTrue(eu.is_encrypted(foreign))
        # Never re-encrypted (that would wrap it twice).
        self.assertEqual(eu.ensure_encrypted(foreign), foreign)

    def test_old_false_positive_token_is_plaintext_and_gets_encrypted(self) -> None:
        rng = random.Random(7)
        token = None
        while token is None:
            candidate = _random_plain_token(rng)
            if _old_is_encrypted(candidate):
                token = candidate
        with self.assertNoLogs(eu.logger, level=logging.ERROR):
            self.assertEqual(eu.ensure_decrypted(token), token)
        encrypted = eu.ensure_encrypted(token)
        self.assertNotEqual(encrypted, token)
        self.assertEqual(eu.ensure_decrypted(encrypted), token)

    def test_edge_inputs(self) -> None:
        for value in ("", None, "====", "Z0FBQUFB", "gAAAAA" + "A" * 100, 12345):
            self.assertFalse(eu.is_encrypted(value))  # type: ignore[arg-type]
        encrypted = eu.encrypt_value("abc")
        self.assertFalse(eu.is_encrypted(encrypted + "A"))
        self.assertFalse(eu.is_encrypted(" " + encrypted))
        self.assertFalse(eu.is_encrypted(encrypted[:-4]))


if __name__ == "__main__":
    unittest.main()

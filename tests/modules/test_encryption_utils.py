# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/encryption_utils.py: key file handling, encrypt/decrypt, detection."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from cryptography.fernet import Fernet

from modules import encryption_utils as eu
from modules import notification_engine


@pytest.fixture
def key_path(tmp_path, monkeypatch):
    path = tmp_path / "keys" / ".encryption_key"
    monkeypatch.setattr(eu, "_key_file_path", lambda: str(path))
    monkeypatch.setattr(eu, "_cached_key", None)
    monkeypatch.setattr(eu, "_cached_fernet", None)
    monkeypatch.delenv("MYCELIAN_MACHINE_ID", raising=False)
    return path


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(notification_engine, "notify_critical", lambda *a, **k: calls.append((a, k)))
    return calls


# ----------------------------------------------------------------- key path / legacy key

def test_key_file_path_uses_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert eu._key_file_path() == os.path.join(str(tmp_path), ".encryption_key")


def test_derive_legacy_key_depends_on_machine_id(monkeypatch):
    monkeypatch.delenv("MYCELIAN_MACHINE_ID", raising=False)
    default = eu._derive_legacy_key()
    assert default == eu._derive_legacy_key()
    Fernet(default)  # valid key
    monkeypatch.setenv("MYCELIAN_MACHINE_ID", "machine-2")
    assert eu._derive_legacy_key() != default


# ----------------------------------------------------------------- _persist_key

def test_persist_key_writes_file_with_private_mode(tmp_path):
    path = tmp_path / "sub" / "k"
    eu._persist_key(b"abc", str(path))
    assert path.read_bytes() == b"abc"
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_persist_key_refuses_to_overwrite(tmp_path):
    path = tmp_path / "k"
    path.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        eu._persist_key(b"new", str(path))
    assert path.read_bytes() == b"old"


def test_persist_key_without_parent_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eu._persist_key(b"xyz", "bare_key")
    assert (tmp_path / "bare_key").read_bytes() == b"xyz"


def test_persist_key_removes_partial_file_on_write_error(tmp_path, monkeypatch):
    path = tmp_path / "k"

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(eu.os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        eu._persist_key(b"abc", str(path))
    assert not path.exists()


def test_persist_key_unlink_failure_still_reraises(tmp_path, monkeypatch):
    path = tmp_path / "k"
    monkeypatch.setattr(eu.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync")))
    monkeypatch.setattr(eu.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("unlink")))
    with pytest.raises(OSError, match="fsync"):
        eu._persist_key(b"abc", str(path))


def test_persist_key_ignores_chmod_failure(tmp_path, monkeypatch):
    path = tmp_path / "k"
    monkeypatch.setattr(eu.os, "chmod", lambda *a: (_ for _ in ()).throw(OSError("chmod")))
    eu._persist_key(b"abc", str(path))
    assert path.read_bytes() == b"abc"


# ----------------------------------------------------------------- _load_key_from_file

def test_load_key_strips_whitespace(tmp_path):
    p = tmp_path / "k"
    p.write_bytes(b"  key123\n")
    assert eu._load_key_from_file(str(p)) == b"key123"


def test_load_key_empty_file_raises(tmp_path):
    p = tmp_path / "k"
    p.write_bytes(b"  \n")
    with pytest.raises(eu.EncryptionError, match="empty"):
        eu._load_key_from_file(str(p))


# ----------------------------------------------------------------- _get_encryption_key

def test_get_key_new_install_generates_random_key(key_path):
    key = eu._get_encryption_key()
    assert key != eu._derive_legacy_key()
    assert key_path.read_bytes() == key
    assert eu._cached_key == key
    # Cached: same value even if file changes
    key_path.write_bytes(b"other")
    assert eu._get_encryption_key() == key


def test_get_key_decrypting_persists_legacy_key(key_path):
    key = eu._get_encryption_key(decrypting=True)
    assert key == eu._derive_legacy_key()
    assert key_path.read_bytes() == key


def test_get_key_machine_id_env_uses_legacy(key_path, monkeypatch):
    monkeypatch.setenv("MYCELIAN_MACHINE_ID", "abc")
    key = eu._get_encryption_key()
    assert key == eu._derive_legacy_key()


def test_get_key_reads_existing_file(key_path):
    key_path.parent.mkdir(parents=True)
    existing = Fernet.generate_key()
    key_path.write_bytes(existing + b"\n")
    assert eu._get_encryption_key() == existing


def test_get_key_race_file_created_meanwhile(key_path, monkeypatch):
    other = Fernet.generate_key()

    def racing_persist(key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(other)
        raise FileExistsError(path)

    monkeypatch.setattr(eu, "_persist_key", racing_persist)
    assert eu._get_encryption_key() == other
    assert eu._cached_key == other


def test_get_fernet_caches_instance(key_path):
    f1 = eu._get_fernet()
    f2 = eu._get_fernet(decrypting=True)
    assert f1 is f2
    assert isinstance(f1, Fernet)


# ----------------------------------------------------------------- encrypt / decrypt

def test_encrypt_empty_returns_empty(key_path):
    assert eu.encrypt_value("") == ""
    assert not key_path.exists()


def test_encrypt_decrypt_round_trip(key_path, notes):
    enc = eu.encrypt_value("secret value")
    assert enc != "secret value"
    assert eu.is_encrypted(enc)
    assert eu.decrypt_value(enc) == "secret value"
    assert notes == []


def test_encrypt_failure_notifies_and_raises(key_path, notes, monkeypatch):
    def broken(**kw):
        raise ValueError("no fernet")

    monkeypatch.setattr(eu, "_get_fernet", broken)
    with pytest.raises(eu.EncryptionError, match="no fernet") as info:
        eu.encrypt_value("x")
    assert isinstance(info.value.__cause__, ValueError)
    assert len(notes) == 1
    assert notes[0][1]["dedupe_key"] == "crypto:encrypt_failed"
    assert notes[0][1]["details"] == "no fernet"


def test_encrypt_failure_reraises_encryption_error_as_is(key_path, notes, monkeypatch):
    err = eu.EncryptionError("empty key")
    monkeypatch.setattr(eu, "_get_fernet", lambda **kw: (_ for _ in ()).throw(err))
    with pytest.raises(eu.EncryptionError) as info:
        eu.encrypt_value("x")
    assert info.value is err


def test_encrypt_failure_notify_failure_is_swallowed(key_path, monkeypatch):
    monkeypatch.setattr(eu, "_get_fernet", lambda **kw: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(notification_engine, "notify_critical",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("notify")))
    with pytest.raises(eu.EncryptionError):
        eu.encrypt_value("x")


def test_decrypt_empty_returns_empty(key_path):
    assert eu.decrypt_value("") == ""


def test_decrypt_garbage_returns_input_and_notifies(key_path, notes):
    assert eu.decrypt_value("not-ciphertext!!") == "not-ciphertext!!"
    assert len(notes) == 1
    assert notes[0][1]["dedupe_key"] == "crypto:decrypt_failed"


def test_decrypt_wrong_key_returns_input(key_path, notes):
    other = Fernet(Fernet.generate_key())
    stored = base64.urlsafe_b64encode(other.encrypt(b"hi")).decode()
    assert eu.decrypt_value(stored) == stored
    assert "InvalidToken" in notes[0][1]["details"]


def test_decrypt_notify_failure_swallowed(key_path, monkeypatch):
    monkeypatch.setattr(notification_engine, "notify_critical",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("notify")))
    assert eu.decrypt_value("zzz") == "zzz"


# ----------------------------------------------------------------- detection helpers

@pytest.mark.parametrize("text", ["", "abc", "ab=c", "a+b/", "abc!"])
def test_strict_b64_rejects_bad_input(text):
    assert eu._strict_urlsafe_b64decode(text) is None


def test_strict_b64_accepts_canonical():
    assert eu._strict_urlsafe_b64decode(base64.urlsafe_b64encode(b"hello!").decode()) == b"hello!"


def test_strict_b64_rejects_non_canonical():
    # "QQ==" is canonical for b"A"; "QR==" has stray bits.
    assert eu._strict_urlsafe_b64decode("QQ==") == b"A"
    assert eu._strict_urlsafe_b64decode("QR==") is None


def test_strict_b64_decode_exception(monkeypatch):
    monkeypatch.setattr(eu.base64, "urlsafe_b64decode", lambda b: (_ for _ in ()).throw(ValueError()))
    assert eu._strict_urlsafe_b64decode("QQ==") is None


def test_is_fernet_token_layout():
    good = bytes([0x80]) + b"\x00" * (eu._FERNET_OVERHEAD - 1 + 16)
    assert eu._is_fernet_token(good)
    assert not eu._is_fernet_token(bytes([0x81]) + good[1:])
    assert not eu._is_fernet_token(good[:-1])
    assert not eu._is_fernet_token(good + b"\x00")


def test_is_encrypted_non_string_and_empty():
    assert not eu.is_encrypted(None)
    assert not eu.is_encrypted(123)
    assert not eu.is_encrypted("")


def test_is_encrypted_outer_not_ascii():
    outer = base64.urlsafe_b64encode("é".encode("utf-8") * 3).decode()
    assert not eu.is_encrypted(outer)


def test_is_encrypted_inner_not_fernet():
    outer = base64.urlsafe_b64encode(base64.urlsafe_b64encode(b"x" * 100)).decode()
    assert not eu.is_encrypted(outer)


def test_is_encrypted_swallows_exceptions(monkeypatch):
    monkeypatch.setattr(eu, "_strict_urlsafe_b64decode", lambda t: (_ for _ in ()).throw(RuntimeError()))
    assert eu.is_encrypted("QQ==") is False


def test_ensure_encrypted_and_decrypted(key_path, notes):
    enc = eu.ensure_encrypted("plain")
    assert eu.is_encrypted(enc)
    assert eu.ensure_encrypted(enc) == enc
    assert eu.ensure_decrypted(enc) == "plain"
    assert eu.ensure_decrypted("plain") == "plain"

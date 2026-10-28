# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/api_credentials_manager.py (credentials file and key file under tmp_path only)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import api_credentials_manager as acm
from modules import encryption_utils as eu

SERVICES = ["twitch", "chatbot", "spotify", "youtube", "streamlabs"]


@pytest.fixture(autouse=True)
def tmp_key(tmp_path, monkeypatch):
    key = tmp_path / "key" / ".encryption_key"
    monkeypatch.setattr(eu, "_key_file_path", lambda: str(key))
    monkeypatch.setattr(eu, "_cached_key", None)
    monkeypatch.setattr(eu, "_cached_fernet", None)
    return key


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(acm, "notify_critical", lambda *a, **k: calls.append(k))
    return calls


@pytest.fixture
def mgr(tmp_path):
    return acm.APICredentialsManager(str(tmp_path / "creds" / "api_credentials.json"))


@pytest.fixture
def global_mgr(monkeypatch, tmp_path):
    m = acm.APICredentialsManager(str(tmp_path / "global" / "api_credentials.json"))
    monkeypatch.setattr(acm, "api_credentials_manager", m)
    return m


def _uninit(m, monkeypatch):
    """Make initialize() a no-op that leaves no credentials."""
    monkeypatch.setattr(m, "initialize", lambda: False)


# ----------------------------------------------------------------- dataclass / init

def test_dataclass_defaults():
    c = acm.APICredentials()
    for svc in SERVICES:
        assert getattr(c, f"{svc}_client_id") == ""
        assert getattr(c, f"{svc}_client_secret") == ""
    assert c.config_version == "1.0"


def test_constructor_defaults(mgr):
    assert mgr._credentials is None
    assert mgr._initialized is False


def test_default_path_is_in_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert acm.APICredentialsManager().config_path == tmp_path / "api_credentials.json"
    assert acm.APICredentialsManager("other.json").config_path == Path("other.json")


def test_initialize_creates_empty_file(mgr, notes):
    assert mgr.initialize() is True
    data = json.loads(mgr.config_path.read_text())
    assert data["twitch_client_id"] == ""
    assert data["last_updated"]
    assert notes == []
    mgr.config_path.unlink()
    assert mgr.initialize() is True  # already initialized: no rewrite
    assert not mgr.config_path.exists()


def test_initialize_loads_existing_filters_unknown(mgr):
    mgr.config_path.parent.mkdir(parents=True)
    mgr.config_path.write_text(json.dumps({"twitch_client_id": "abc", "chatbot_client_id": "cb", "zzz": 1}))
    assert mgr.initialize() is True
    assert mgr.get_credentials().twitch_client_id == "abc"
    assert mgr.get_credentials().chatbot_client_id == "cb"


def test_load_bad_json_returns_empty(mgr):
    mgr.config_path.parent.mkdir(parents=True)
    mgr.config_path.write_text("{oops")
    creds = mgr._load_credentials()
    assert creds.twitch_client_id == ""
    assert creds.last_updated


def test_initialize_failure_notifies(mgr, notes, monkeypatch):
    monkeypatch.setattr(mgr, "_create_empty_credentials", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert mgr.initialize() is False
    assert notes[-1]["dedupe_key"] == "api_creds:init_failed"
    assert "bad" in notes[-1]["details"]


# ----------------------------------------------------------------- saving

def test_save_without_credentials(mgr):
    assert mgr._save_credentials() is False


def test_save_writes_atomically(mgr):
    mgr._credentials = acm.APICredentials(twitch_client_id="x")
    assert mgr._save_credentials() is True
    data = json.loads(mgr.config_path.read_text())
    assert data["twitch_client_id"] == "x"
    assert list(data) == sorted(data)
    assert not mgr.config_path.with_name("api_credentials.json.tmp").exists()


def test_save_failure_cleans_tmp_and_notifies(mgr, notes, monkeypatch):
    mgr._credentials = acm.APICredentials()
    monkeypatch.setattr(acm.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("nope")))
    assert mgr._save_credentials() is False
    assert not mgr.config_path.with_name("api_credentials.json.tmp").exists()
    assert notes[-1]["dedupe_key"] == "api_creds:save_failed"


def test_save_failure_unlink_error_ignored(mgr, notes, monkeypatch):
    mgr._credentials = acm.APICredentials()
    monkeypatch.setattr(acm.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("nope")))
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(OSError("x")))
    assert mgr._save_credentials() is False
    assert notes


# ----------------------------------------------------------------- per-service get/update

@pytest.mark.parametrize("svc", SERVICES)
def test_update_encrypts_and_get_decrypts(mgr, svc):
    update = getattr(mgr, f"update_{svc}_credentials")
    get = getattr(mgr, f"get_{svc}_credentials")
    assert update(client_id="id-" + svc, client_secret="secret-" + svc) is True
    stored_id = getattr(mgr._credentials, f"{svc}_client_id")
    stored_secret = getattr(mgr._credentials, f"{svc}_client_secret")
    assert eu.is_encrypted(stored_id) and eu.is_encrypted(stored_secret)
    assert "id-" + svc not in mgr.config_path.read_text()
    assert get() == {"client_id": "id-" + svc, "client_secret": "secret-" + svc}
    # Reload from disk in a fresh manager
    other = acm.APICredentialsManager(str(mgr.config_path))
    assert getattr(other, f"get_{svc}_credentials")()["client_id"] == "id-" + svc


@pytest.mark.parametrize("svc", SERVICES)
def test_update_only_id(mgr, svc):
    update = getattr(mgr, f"update_{svc}_credentials")
    assert update(client_id="only") is True
    assert getattr(mgr, f"get_{svc}_credentials")() == {"client_id": "only", "client_secret": ""}


@pytest.mark.parametrize("svc", SERVICES)
def test_update_nothing_does_not_save(mgr, svc, monkeypatch):
    mgr.initialize()
    monkeypatch.setattr(mgr, "_save_credentials", lambda: pytest.fail("saved"))
    assert getattr(mgr, f"update_{svc}_credentials")() is True


@pytest.mark.parametrize("svc", SERVICES)
def test_update_without_credentials_fails(mgr, svc, monkeypatch):
    _uninit(mgr, monkeypatch)
    assert getattr(mgr, f"update_{svc}_credentials")(client_id="x") is False


@pytest.mark.parametrize("svc", SERVICES)
def test_get_without_credentials_returns_blank(mgr, svc, monkeypatch):
    _uninit(mgr, monkeypatch)
    assert getattr(mgr, f"get_{svc}_credentials")() == {"client_id": "", "client_secret": ""}


@pytest.mark.parametrize("svc", SERVICES)
def test_get_plaintext_values_pass_through(mgr, svc):
    mgr.initialize()
    setattr(mgr._credentials, f"{svc}_client_id", "plain-id")
    assert getattr(mgr, f"get_{svc}_credentials")()["client_id"] == "plain-id"


@pytest.mark.parametrize("svc", SERVICES)
def test_already_encrypted_value_not_double_encrypted(mgr, svc):
    enc = eu.encrypt_value("abc")
    getattr(mgr, f"update_{svc}_credentials")(client_id=enc)
    assert getattr(mgr._credentials, f"{svc}_client_id") == enc


@pytest.mark.parametrize("secret", ["", "   "])
def test_streamlabs_blank_secret_keeps_existing(mgr, secret):
    mgr.update_streamlabs_credentials(client_id="i", client_secret="keep")
    assert mgr.update_streamlabs_credentials(client_secret=secret) is True
    assert mgr.get_streamlabs_credentials()["client_secret"] == "keep"


def test_update_returns_save_result(mgr, monkeypatch):
    mgr.initialize()
    monkeypatch.setattr(mgr, "_save_credentials", lambda: False)
    assert mgr.update_twitch_credentials(client_id="x") is False


# ----------------------------------------------------------------- export / import

def test_export_without_credentials(mgr, tmp_path):
    assert mgr.export_credentials(str(tmp_path / "e.json")) is False


def test_export_import_round_trip(mgr, tmp_path):
    mgr.update_spotify_credentials(client_id="sp", client_secret="ss")
    out = tmp_path / "exp" / "e.json"
    assert mgr.export_credentials(str(out)) is True
    data = json.loads(out.read_text())
    data["unknown"] = 1
    out.write_text(json.dumps(data))
    other = acm.APICredentialsManager(str(tmp_path / "other.json"))
    assert other.import_credentials(str(out)) is True
    assert other.get_spotify_credentials() == {"client_id": "sp", "client_secret": "ss"}
    assert (tmp_path / "other.json").exists()


def test_export_failure(mgr, tmp_path):
    mgr.initialize()
    f = tmp_path / "f"
    f.write_text("")
    assert mgr.export_credentials(str(f / "x.json")) is False


def test_import_missing(mgr, tmp_path):
    assert mgr.import_credentials(str(tmp_path / "missing.json")) is False


def test_import_bad_json(mgr, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[[")
    assert mgr.import_credentials(str(p)) is False


def test_import_save_fails(mgr, tmp_path, monkeypatch):
    p = tmp_path / "in.json"
    p.write_text(json.dumps(asdict(acm.APICredentials(twitch_client_id="t"))))
    monkeypatch.setattr(mgr, "_save_credentials", lambda: False)
    assert mgr.import_credentials(str(p)) is False
    assert mgr._credentials.twitch_client_id == "t"


# ----------------------------------------------------------------- module-level helpers

def test_module_get_update_helpers(global_mgr):
    assert acm.initialize_api_credentials() is True
    assert acm.update_twitch_credentials("tid", "tsec") is True
    assert acm.get_twitch_credentials() == {"client_id": "tid", "client_secret": "tsec"}
    assert acm.update_youtube_credentials("yid", "ysec") is True
    assert acm.get_youtube_credentials() == {"client_id": "yid", "client_secret": "ysec"}
    assert acm.update_streamlabs_credentials("lid", "lsec") is True
    assert acm.get_streamlabs_credentials() == {"client_id": "lid", "client_secret": "lsec"}
    global_mgr.update_chatbot_credentials("cid", "csec")
    assert acm.get_chatbot_credentials() == {"client_id": "cid", "client_secret": "csec"}
    global_mgr.update_spotify_credentials("sid", "ssec")
    assert acm.get_spotify_credentials() == {"client_id": "sid", "client_secret": "ssec"}


ENCRYPTED_GETTERS = [
    ("get_encrypted_twitch_client_id", "twitch_client_id"),
    ("get_encrypted_twitch_client_secret", "twitch_client_secret"),
    ("get_encrypted_spotify_client_id", "spotify_client_id"),
    ("get_encrypted_spotify_client_secret", "spotify_client_secret"),
    ("get_encrypted_youtube_client_id", "youtube_client_id"),
    ("get_encrypted_youtube_client_secret", "youtube_client_secret"),
]


@pytest.mark.parametrize("func,field", ENCRYPTED_GETTERS)
def test_encrypted_getters_return_raw_value(global_mgr, func, field):
    assert getattr(acm, func)() == ""  # initializes lazily
    assert global_mgr._initialized
    setattr(global_mgr._credentials, field, "ENC")
    assert getattr(acm, func)() == "ENC"


@pytest.mark.parametrize("func,field", ENCRYPTED_GETTERS)
def test_encrypted_getters_without_credentials(global_mgr, monkeypatch, func, field):
    _uninit(global_mgr, monkeypatch)
    assert getattr(acm, func)() == ""


def test_get_credentials_initializes_lazily(mgr):
    creds = mgr.get_credentials()
    assert isinstance(creds, acm.APICredentials)
    assert mgr._initialized and mgr.config_path.exists()

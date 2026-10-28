# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/donation_settings.py: load, merge, secret handling, failed-read recovery."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import database_manager, donation_settings as ds  # noqa: E402
from modules.encryption_utils import ensure_decrypted, ensure_encrypted  # noqa: E402


class FakeStore:
    def __init__(self, stored=None):
        self.stored = stored if stored is not None else {}
        self.fail_reads = False
        self.raise_reads = False
        self.write_result = True
        self.raise_writes = False
        self.writes = []
        self.reads = 0

    def get_data_checked(self, path):
        assert path == ds.PATH
        self.reads += 1
        if self.raise_reads:
            raise RuntimeError("db down")
        if self.fail_reads:
            return {}, False
        return self.stored, True

    def set_data(self, path, payload):
        assert path == ds.PATH
        if self.raise_writes:
            raise RuntimeError("write boom")
        self.writes.append(dict(payload))
        if self.write_result:
            self.stored = dict(payload)
        return self.write_result


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore({"alert_currency": "EUR", "streamlabs_access_token": ensure_encrypted("tok"),
                      "streamelements_jwt": "jwt-plain", "unknown_key": "ignored"})
    monkeypatch.setattr(database_manager, "get_data_checked", fake.get_data_checked)
    monkeypatch.setattr(database_manager, "set_data", fake.set_data)
    monkeypatch.setattr(ds, "_cache", None)
    monkeypatch.setattr(ds, "_load_ok", True)
    monkeypatch.setattr(ds, "_changed_since_failed_load", set())
    return fake


@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("   ", True), ("x", False), (0, False), ([], False),
])
def test_blank(value, expected):
    assert ds._blank(value) is expected


def test_from_raw_filters_unknown_and_decrypts():
    s = ds._from_raw({"alert_currency": "GBP", "streamlabs_refresh_token": ensure_encrypted("r"),
                      "streamelements_jwt": "", "bogus": 1})
    assert s.alert_currency == "GBP"
    assert s.streamlabs_refresh_token == "r"
    assert s.streamelements_jwt == ""


def test_from_raw_none_gives_defaults():
    assert ds._from_raw(None) == ds.DonationSettings()


def test_read_raw_non_dict(store):
    store.stored = ["x"]
    assert ds._read_raw() == ({}, True)


def test_read_raw_exception(store):
    store.raise_reads = True
    assert ds._read_raw() == ({}, False)


def test_load_sets_flag_and_clears_pending(store):
    ds._changed_since_failed_load.add("alert_currency")
    store.fail_reads = True
    s = ds._load()
    assert s == ds.DonationSettings()
    assert ds._load_ok is False
    assert ds._changed_since_failed_load == set()
    store.fail_reads = False
    assert ds._load().alert_currency == "EUR"
    assert ds._load_ok is True


def test_get_settings_caches_and_returns_copies(store):
    a = ds.get_settings()
    assert a.alert_currency == "EUR"
    assert a.streamlabs_access_token == "tok"
    assert a.streamelements_jwt == "jwt-plain"
    a.alert_currency = "MUTATED"
    b = ds.get_settings()
    assert b.alert_currency == "EUR"
    assert store.reads == 1


def test_update_settings_merges_and_encrypts(store):
    assert ds.update_settings(alert_currency="CAD", streamelements_channel_id="ch", not_a_field="x") is True
    written = store.writes[-1]
    assert written["alert_currency"] == "CAD"
    assert written["streamelements_channel_id"] == "ch"
    assert "not_a_field" not in written
    assert written["streamlabs_access_token"] != "tok"
    assert ensure_decrypted(written["streamlabs_access_token"]) == "tok"
    assert written["streamlabs_refresh_token"] == ""  # empty secret stays empty


def test_update_settings_blank_secret_does_not_wipe(store):
    assert ds.update_settings(streamlabs_access_token="  ", streamelements_jwt=None) is True
    s = ds.get_settings()
    assert s.streamlabs_access_token == "tok"
    assert s.streamelements_jwt == "jwt-plain"


def test_update_settings_none_non_secret_becomes_empty(store):
    ds.update_settings(streamlabs_last_error=None)
    assert ds.get_settings().streamlabs_last_error == ""


def test_update_settings_blank_secret_sets_when_current_empty(store):
    ds.update_settings(streamlabs_refresh_token="")
    assert ds.get_settings().streamlabs_refresh_token == ""


def test_update_settings_uses_existing_cache(store):
    ds.get_settings()
    store.stored = {"alert_currency": "JPY"}
    ds.update_settings(streamlabs_last_error="e")
    assert store.writes[-1]["alert_currency"] == "EUR"  # cache, not a new read


def test_update_settings_write_failure(store):
    store.write_result = False
    assert ds.update_settings(alert_currency="AUD") is False


def test_persist_exception_returns_false(store):
    store.raise_writes = True
    assert ds._persist(ds.DonationSettings()) is False


def test_update_settings_while_unreadable_returns_false_and_tracks(store):
    store.fail_reads = True
    assert ds.update_settings(alert_currency="NZD") is False
    assert store.writes == []
    assert ds._changed_since_failed_load == {"alert_currency"}
    assert ds.get_settings().alert_currency == "NZD"


def test_recover_before_write_paths(store):
    cur = ds.DonationSettings(alert_currency="X")
    assert ds._recover_before_write(cur) is cur  # load ok
    ds._load_ok = False
    store.fail_reads = True
    assert ds._recover_before_write(cur) is None
    store.fail_reads = False
    ds._changed_since_failed_load.add("alert_currency")
    out = ds._recover_before_write(cur)
    assert out.alert_currency == "X"
    assert out.streamlabs_access_token == "tok"
    assert ds._load_ok is True and ds._changed_since_failed_load == set()


@pytest.mark.parametrize("field", ["streamlabs_access_token", "streamelements_jwt", "streamelements_channel_id",
                                   "streamlabs_last_error", "streamelements_last_error"])
def test_clear_secret_allowed_fields(store, field):
    ds.update_settings(streamelements_channel_id="c", streamlabs_last_error="e", streamelements_last_error="e2")
    assert ds.clear_secret(field) is True
    assert getattr(ds.get_settings(), field) == ""
    assert store.writes[-1][field] == ""


def test_clear_secret_rejects_other_fields(store):
    assert ds.clear_secret("alert_currency") is False
    assert store.writes == []


def test_clear_secret_while_unreadable(store):
    store.fail_reads = True
    assert ds.clear_secret("streamlabs_access_token") is False
    assert "streamlabs_access_token" in ds._changed_since_failed_load
    store.fail_reads = False
    assert ds.clear_secret("streamelements_jwt") is True
    written = store.writes[-1]
    assert written["streamlabs_access_token"] == "" and written["streamelements_jwt"] == ""
    assert written["alert_currency"] == "EUR"


def test_clear_secret_uses_cache(store):
    ds.get_settings()
    reads = store.reads
    assert ds.clear_secret("streamlabs_refresh_token") is True
    assert store.reads == reads


def test_set_status_unknown_service(store):
    ds.set_status("paypal", "Connected")
    assert store.writes == []


def test_set_status_writes_and_truncates(store):
    ds.set_status("streamlabs", "Error", "e" * 500)
    written = store.writes[-1]
    assert written["streamlabs_connection_status"] == "Error"
    assert written["streamlabs_last_error"] == "e" * 240


def test_set_status_unchanged_skips_write(store):
    ds.set_status("streamelements", "Disconnected")
    assert store.writes == []
    ds.set_status("streamelements", "Connected", None)
    assert len(store.writes) == 1
    ds.set_status("streamelements", "Connected", "")
    assert len(store.writes) == 1

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Donation settings never overwrite stored tokens after a failed read."""

from __future__ import annotations

import pytest

from modules import database_manager, donation_settings
from modules.encryption_utils import ensure_decrypted


class FakeStore:
    def __init__(self, stored):
        self.stored = stored
        self.fail_reads = False
        self.writes = []

    def get_data_checked(self, path):
        if self.fail_reads:
            return {}, False
        return dict(self.stored), True

    def set_data(self, path, payload):
        self.writes.append(dict(payload))
        self.stored = dict(payload)
        return True


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore({"alert_currency": "EUR", "streamelements_jwt": "jwt-123", "streamelements_channel_id": "chan"})
    monkeypatch.setattr(database_manager, "get_data_checked", fake.get_data_checked)
    monkeypatch.setattr(database_manager, "set_data", fake.set_data)
    monkeypatch.setattr(donation_settings, "_cache", None)
    monkeypatch.setattr(donation_settings, "_load_ok", True)
    monkeypatch.setattr(donation_settings, "_changed_since_failed_load", set())
    return fake


def test_normal_load_and_save_unchanged(store):
    assert donation_settings.get_settings().alert_currency == "EUR"
    assert donation_settings.update_settings(alert_currency="GBP")
    written = store.writes[-1]
    assert written["alert_currency"] == "GBP"
    assert ensure_decrypted(written["streamelements_jwt"]) == "jwt-123"
    assert written["streamelements_channel_id"] == "chan"


def test_status_update_after_failed_read_does_not_wipe_tokens(store):
    store.fail_reads = True
    assert donation_settings.get_settings().streamelements_jwt == ""  # unreadable → defaults in memory
    donation_settings.set_status("streamlabs", "Connecting")
    assert store.writes == []  # nothing written while unreadable
    assert store.stored["streamelements_jwt"] == "jwt-123"


def test_changes_are_applied_onto_the_stored_copy_once_readable(store):
    store.fail_reads = True
    donation_settings.get_settings()
    donation_settings.set_status("streamlabs", "Connecting")
    store.fail_reads = False
    assert donation_settings.update_settings(alert_currency="CAD")
    written = store.writes[-1]
    assert written["alert_currency"] == "CAD"
    assert written["streamlabs_connection_status"] == "Connecting"
    assert ensure_decrypted(written["streamelements_jwt"]) == "jwt-123"
    assert written["streamelements_channel_id"] == "chan"
    # Back to the normal path afterwards.
    assert donation_settings.get_settings().streamelements_jwt == "jwt-123"

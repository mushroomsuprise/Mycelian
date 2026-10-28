# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/template_copy_progress.py (copy/delete progress for the footer)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import notification_engine  # noqa: E402
from modules import template_copy_progress as tcp  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_state(monkeypatch):
    monkeypatch.setattr(tcp, "_state", dict(tcp._state))
    calls = []
    monkeypatch.setattr(notification_engine, "start_alert_trim_footer_poll", lambda: calls.append(1))
    return calls


def test_default_state_is_idle():
    tcp.mark_template_copy_idle()
    assert tcp.template_copy_is_active() is False
    assert tcp.get_template_copy_progress() == {
        "active": False, "phase": "idle", "copied": 0, "total": 0, "label": "", "title": "Copying",
    }


def test_get_progress_returns_copy():
    snap = tcp.get_template_copy_progress()
    snap["active"] = True
    assert tcp.template_copy_is_active() is False


def test_set_state_active_starts_footer_poll(_restore_state):
    tcp.set_template_copy_state(active=True, phase="copying", copied=1, total=4)
    assert tcp.template_copy_is_active() is True
    assert tcp.get_template_copy_progress()["copied"] == 1
    assert _restore_state == [1]


def test_set_state_inactive_does_not_start_poll(_restore_state):
    tcp.set_template_copy_state(label="x")
    assert tcp.get_template_copy_progress()["label"] == "x"
    assert _restore_state == []


def test_set_state_swallows_poll_errors(monkeypatch):
    def boom():
        raise RuntimeError("no loop")

    monkeypatch.setattr(notification_engine, "start_alert_trim_footer_poll", boom)
    tcp.set_template_copy_state(active=True)
    assert tcp.template_copy_is_active()


def test_mark_idle_resets_everything():
    tcp.set_template_copy_state(active=True, phase="copying", copied=3, total=9, label="L", title="Deleting")
    tcp.mark_template_copy_idle()
    assert tcp.get_template_copy_progress() == {
        "active": False, "phase": "idle", "copied": 0, "total": 0, "label": "", "title": "Copying",
    }


@pytest.mark.parametrize(
    "progress,expected",
    [
        (None, None),
        ({}, None),
        ({"active": False, "copied": 1, "total": 2}, None),
        ({"active": True, "phase": "preparing"}, ("Preparing", "info")),
        ({"active": True, "phase": "preparing", "title": "Deleting"}, ("Preparing", "info")),
        ({"active": True, "phase": "copying", "copied": 2, "total": 5}, ("2 of 5", "warning")),
        ({"active": True, "phase": "copying", "copied": 2, "total": 5, "title": "Deleting"}, ("2 of 5", "error")),
        ({"active": True, "phase": "copying", "copied": -3, "total": 5}, ("0 of 5", "warning")),
        ({"active": True, "phase": "copying", "copied": 0, "total": 0}, ("Copying", "warning")),
        ({"active": True, "copied": 0, "total": 0, "title": "Deleting"}, ("Deleting", "error")),
        ({"active": True, "copied": "x", "total": 5}, ("Copying", "warning")),
        ({"active": True, "copied": 1, "total": [1]}, ("Copying", "warning")),
        ({"active": True, "copied": None, "total": "7", "title": None}, ("0 of 7", "warning")),
    ],
)
def test_format_badge(progress, expected):
    assert tcp.format_template_copy_badge(progress) == expected

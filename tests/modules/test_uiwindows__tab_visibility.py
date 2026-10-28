# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.uiwindows.tab_visibility`` (hidden Chatbot sub-tab detection)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import builtins

import pytest

from modules.help_system import contextual_help as ch
from modules.uiwindows import tab_visibility as tv


class FakeTabs:
    def __init__(self, value):
        self.value = value
        self.handlers = []

    def on_value_change(self, handler):
        self.handlers.append(handler)

    def set(self, value):
        self.value = value
        for handler in self.handlers:
            handler(None)


class BrokenTabs:
    value = "Chatbot"

    def on_value_change(self, handler):
        raise RuntimeError("no hook")


@pytest.fixture
def strips(monkeypatch):
    main_tabs, chatbot_tabs = FakeTabs("Chatbot"), FakeTabs("Commands")
    monkeypatch.setattr(ch, "_main_tabs", main_tabs)
    monkeypatch.setattr(ch, "_chatbot_tabs", chatbot_tabs)
    monkeypatch.setattr(tv, "_hooked_tabs", [])
    monkeypatch.setattr(tv, "_show_callbacks", {})
    return main_tabs, chatbot_tabs


def test_tab_strips_returns_contextual_help_strips(strips):
    assert tv._tab_strips() == strips


def test_tab_strips_import_failure_returns_none_pair(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if fromlist and "contextual_help" in fromlist:
            raise ImportError("gone")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert tv._tab_strips() == (None, None)


def test_label_none_is_empty():
    assert tv._label(None) == ""


def test_label_reads_value_string():
    assert tv._label(FakeTabs("Alerts")) == "Alerts"


def test_label_resolves_tab_object_props():
    class Tab:
        _props = {"label": "Quotes"}

    assert tv._label(FakeTabs(Tab())) == "Quotes"


def test_label_failure_returns_empty():
    class Exploding:
        @property
        def value(self):
            raise RuntimeError("boom")

    assert tv._label(Exploding()) == ""


@pytest.mark.parametrize("main", sorted(tv._MAIN_TAB_LABELS - {"Chatbot"}))
def test_other_main_tab_hides_every_subtab(strips, main):
    strips[0].value = main
    assert tv.chatbot_subtab_hidden("Commands") is True


@pytest.mark.parametrize("sub", sorted(tv._CHATBOT_SUB_TABS))
def test_current_subtab_is_visible_others_hidden(strips, sub):
    strips[1].value = sub
    assert tv.chatbot_subtab_hidden(sub) is False
    for other in tv._CHATBOT_SUB_TABS - {sub}:
        assert tv.chatbot_subtab_hidden(other) is True


def test_chatbot_subtab_hidden_hooks_strips_once(strips):
    tv.chatbot_subtab_hidden("Polls")
    tv.chatbot_subtab_hidden("Polls")
    assert len(strips[0].handlers) == 1 and len(strips[1].handlers) == 1
    assert tv._hooked_tabs == [strips[0], strips[1]]


def test_ensure_hooks_skips_none_and_tolerates_failure(monkeypatch):
    monkeypatch.setattr(tv, "_hooked_tabs", [])
    broken = BrokenTabs()
    ok = FakeTabs("x")
    tv._ensure_hooks(None, broken, ok)
    assert tv._hooked_tabs == [ok]
    assert ok.handlers == [tv._on_tab_change]


def test_ensure_hooks_identity_not_equality(monkeypatch):
    class EqTabs(FakeTabs):
        def __eq__(self, other):
            return True

        __hash__ = object.__hash__

    a, b = EqTabs("x"), EqTabs("y")
    monkeypatch.setattr(tv, "_hooked_tabs", [])
    tv._ensure_hooks(a)
    tv._ensure_hooks(b)
    assert len(tv._hooked_tabs) == 2


def test_on_chatbot_subtab_shown_registers_and_replaces(strips):
    first, second = lambda: None, lambda: None
    tv.on_chatbot_subtab_shown("Quotes", first)
    assert tv._show_callbacks == {"Quotes": first}
    tv.on_chatbot_subtab_shown("Quotes", second)
    assert tv._show_callbacks == {"Quotes": second}


def test_on_tab_change_runs_only_visible_callbacks_and_swallows_errors(strips):
    calls = []

    def bad():
        calls.append("bad")
        raise ValueError("x")

    tv._show_callbacks.update({"Commands": bad, "Events": lambda: calls.append("events")})
    tv._on_tab_change()
    assert calls == ["bad"]
    strips[1].value = "Events"
    tv._on_tab_change(object())
    assert calls == ["bad", "events"]

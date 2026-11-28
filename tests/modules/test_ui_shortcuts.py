# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_shortcuts.py: the Ctrl/Cmd+S save shortcut."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import layout_events, ui_shortcuts  # noqa: E402
from modules.uiwindows import alertsettings, chatbot, connectors, customsources  # noqa: E402
from modules.uiwindows import settings as settings_mod  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(tab="Activity Feed", calls=[])
    monkeypatch.setattr(ui_shortcuts, "_current_main_tab", lambda: state.tab)
    monkeypatch.setattr(chatbot, "_active_editor", None)
    monkeypatch.setattr(connectors, "_active_editor", None)
    return state


def _editor(calls, *, open_=True, fail=False):
    def save():
        if fail:
            raise RuntimeError("boom")
        calls.append("editor saved")

    return SimpleNamespace(dialog=SimpleNamespace(value=open_, is_deleted=False), save=save)


def test_open_editor_wins_over_the_tab(env, monkeypatch):
    env.tab = "Alerts"
    monkeypatch.setattr(connectors, "_active_editor", _editor(env.calls))
    assert ui_shortcuts.handle_save_shortcut() == "editor" and env.calls == ["editor saved"]
    # A closed editor is ignored; a failing one is logged, not raised.
    monkeypatch.setattr(connectors, "_active_editor", _editor(env.calls, open_=False))
    monkeypatch.setattr(chatbot, "_active_editor", _editor(env.calls, fail=True))
    assert ui_shortcuts.handle_save_shortcut() == ""


def test_saves_the_dirty_tab_in_front(env, monkeypatch):
    saved = []
    monkeypatch.setattr(alertsettings, "alerts_have_unsaved_changes", lambda: True)
    monkeypatch.setattr(alertsettings, "save_active_alert", lambda done: saved.append("alerts"))
    monkeypatch.setattr(customsources, "source_settings_is_dirty", lambda: True)
    monkeypatch.setattr(customsources, "save_open_source_settings", lambda: saved.append("source") or True)
    ui = SimpleNamespace(_active_tab_name="OBS", _tab_is_dirty=lambda n: n == "OBS",
                         save_tab=lambda n: saved.append(f"settings:{n}"))
    monkeypatch.setattr(settings_mod, "settings_ui", ui)
    for tab, expected in (("Alerts", "alerts"), ("Source Settings", "source"), ("Settings", "settings:OBS")):
        env.tab = tab
        assert ui_shortcuts.handle_save_shortcut() == tab
        assert saved[-1] == expected
    env.tab = "Connectors"  # nothing to save here
    assert ui_shortcuts.handle_save_shortcut() == ""


def test_clean_tabs_and_errors_save_nothing(env, monkeypatch):
    monkeypatch.setattr(alertsettings, "alerts_have_unsaved_changes", lambda: False)
    env.tab = "Alerts"
    assert ui_shortcuts.handle_save_shortcut() == ""
    monkeypatch.setattr(customsources, "source_settings_is_dirty", lambda: (_ for _ in ()).throw(RuntimeError()))
    env.tab = "Source Settings"
    assert ui_shortcuts.handle_save_shortcut() == ""


def test_install_routes_the_page_event(monkeypatch):
    seen = {}
    monkeypatch.setattr(layout_events, "set_default", lambda event, handler: seen.update({event: handler}))
    ui_shortcuts.install()
    assert seen == {layout_events.EVENT_SAVE_SHORTCUT: ui_shortcuts.handle_save_shortcut}
    assert layout_events.EVENT_SAVE_SHORTCUT in layout_events.EVENTS
    js = ui_shortcuts.SHORTCUTS_HEAD_HTML
    # Ctrl on Windows/Linux, Cmd on macOS; leaves keys someone else handled alone.
    assert "e.metaKey || e.ctrlKey" in js and "defaultPrevented" in js and "'mycelian-save'" in js

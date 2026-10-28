# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/spore_studio/editor_view.py (NiceGUI iframe host)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402

from modules import notification_engine  # noqa: E402
from modules.uiwindows.spore_studio import editor_view as ev  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeEl:
    def __init__(self):
        self.prop_calls = []
        self.style_calls = []

    def props(self, value):
        self.prop_calls.append(value)
        return self

    def style(self, value):
        self.style_calls.append(value)
        return self


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(ev, "_live_state", {"current": None})
    monkeypatch.setattr(ev, "_INJECTED_CSS", {"injected": False})
    monkeypatch.setattr(ev, "web_engine_module", SimpleNamespace(web_engine_instance=None))
    notes = []
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: notes.append((a, k)))
    return notes


def _server(monkeypatch, running=True, port=5123):
    inst = SimpleNamespace(is_running=running, port=port)
    monkeypatch.setattr(ev, "web_engine_module", SimpleNamespace(web_engine_instance=inst))
    return inst


# ---------------------------------------------------------------- _editor_url

def test_editor_url_no_instance():
    assert ev._editor_url() == ""


def test_editor_url_not_running(monkeypatch):
    _server(monkeypatch, running=False)
    assert ev._editor_url() == ""


def test_editor_url_running_with_port(monkeypatch):
    _server(monkeypatch, port=6001)
    assert ev._editor_url() == "http://127.0.0.1:6001/_spore_studio_editor"


def test_editor_url_port_fallback(monkeypatch):
    _server(monkeypatch, port=0)
    assert ev._editor_url() == "http://127.0.0.1:5000/_spore_studio_editor"


def test_state_shape():
    assert ev._state() == {"iframe": None, "placeholder": None, "container": None,
                           "pending_template": ""}


# ---------------------------------------------------------------- _refresh_iframe

def test_refresh_without_elements_is_noop():
    ev._refresh_iframe({"iframe": None, "placeholder": FakeEl()})
    ev._refresh_iframe({"iframe": FakeEl(), "placeholder": None})


def test_refresh_server_down_shows_placeholder():
    iframe, ph = FakeEl(), FakeEl()
    ev._refresh_iframe({"iframe": iframe, "placeholder": ph, "pending_template": "keep"})
    assert iframe.prop_calls == ["src="]
    assert iframe.style_calls == ["display:none"]
    assert ph.style_calls == ["display:flex"]


def test_refresh_server_up_sets_cache_busted_src(monkeypatch):
    _server(monkeypatch, port=7000)
    monkeypatch.setattr(ev.time, "time", lambda: 12.345)
    iframe, ph = FakeEl(), FakeEl()
    state = {"iframe": iframe, "placeholder": ph, "pending_template": " my tpl/1 "}
    ev._refresh_iframe(state)
    assert iframe.style_calls == ["display:block"]
    assert iframe.prop_calls == [
        'src="http://127.0.0.1:7000/_spore_studio_editor?_cb=12345&template=my%20tpl/1"'
    ]
    assert ph.style_calls == ["display:none"]
    assert "pending_template" not in state
    ev._refresh_iframe(state)
    assert iframe.prop_calls[-1].endswith('?_cb=12345"')


# ---------------------------------------------------------------- open_template

def test_open_template_blank_ignored():
    ev.open_template("  ")
    ev.open_template(None)
    assert ev._live_state == {"current": None}


def test_open_template_before_tab_exists_is_queued():
    ev.open_template("  alerts ")
    assert ev._live_state["pending_template"] == "alerts"


def test_open_template_with_live_state_refreshes(monkeypatch):
    calls = []
    monkeypatch.setattr(ev, "_refresh_iframe", lambda s: calls.append(dict(s)))
    state = {"iframe": None}
    ev._live_state["current"] = state
    ev.open_template("a")
    assert state["pending_template"] == "a" and calls == []
    state["iframe"] = object()
    ev.open_template("b")
    assert calls and calls[0]["pending_template"] == "b"


# ---------------------------------------------------------------- _open_externally

def test_open_externally_server_down_warns(monkeypatch, isolate):
    opened = []
    monkeypatch.setattr(ev.webbrowser, "open", lambda *a, **k: opened.append(a))
    ev._open_externally()
    assert opened == []
    (args, kwargs), = isolate
    assert kwargs["type"] == "warning" and kwargs["source"] == "overlay_server"


def test_open_externally_opens_browser(monkeypatch, isolate):
    _server(monkeypatch, port=5555)
    opened = []
    monkeypatch.setattr(ev.webbrowser, "open", lambda url, new=0: opened.append((url, new)))
    ev._open_externally()
    assert opened == [("http://127.0.0.1:5555/_spore_studio_editor?external=1", 2)]
    assert isolate == []


def test_open_externally_browser_error_notifies(monkeypatch, isolate):
    _server(monkeypatch, port=5555)

    def boom(*_a, **_k):
        raise RuntimeError("no browser")

    monkeypatch.setattr(ev.webbrowser, "open", boom)
    ev._open_externally()
    (args, kwargs), = isolate
    assert kwargs["type"] == "error"
    assert kwargs["actions"][0]["text"].endswith("?external=1")
    assert "RuntimeError: no browser" in kwargs["details"]


# ---------------------------------------------------------------- full tab build

def _capture_schedule(monkeypatch):
    scheduled = []
    monkeypatch.setattr(ev, "layout_schedule",
                        lambda interval, cb, once=False: scheduled.append((interval, cb, once)))
    return scheduled


def test_tab_builds_placeholder_and_polls(monkeypatch):
    scheduled = _capture_schedule(monkeypatch)
    head = []
    monkeypatch.setattr(ev.ui, "add_head_html", lambda html, shared=False: head.append(html))

    async def check(user):
        await user.should_see("Reload editor")
        await user.should_see("Open externally")
        state = ev._live_state["current"]
        assert state["iframe"] is not None and state["body"] is not None
        # first poll (0.5s), server down -> placeholder visible, reschedule
        assert scheduled[0][0] == 0.5 and scheduled[0][2] is True
        scheduled[0][1]()
        assert state["placeholder"]._style.get("display") == "flex"
        await user.should_see("Waiting for the overlay server to start…")
        assert scheduled[1][0] == 1.5
        # Retry with the server up loads the iframe
        _server(monkeypatch, port=5999)
        user.find("Retry").click()
        assert "127.0.0.1:5999" in state["iframe"]._props["src"]
        assert state["placeholder"]._style.get("display") == "none"
        # next poll finds the URL and stops rescheduling
        n = len(scheduled)
        scheduled[1][1]()
        assert len(scheduled) == n

    run_ui(ev.create_spore_studio_tab, check)
    assert ev._INJECTED_CSS["injected"] is True
    assert any("spore-studio-tab-css" in h for h in head)


def test_poll_gives_up_after_budget(monkeypatch):
    scheduled = _capture_schedule(monkeypatch)
    monkeypatch.setattr(ev.ui, "add_head_html", lambda *a, **k: None)
    refreshes = []
    monkeypatch.setattr(ev, "_refresh_iframe", lambda state: refreshes.append(state))

    async def check(user):
        scheduled[0][1]()
        # drive the re-polls until the budget (40) runs out
        for _ in range(60):
            if len(scheduled) == 1:
                break
            interval, cb, _once = scheduled.pop()
            cb()
        assert len(scheduled) == 1
        assert len(refreshes) == 41  # first poll + 40 retries

    run_ui(ev.create_spore_studio_tab, check)


def test_tab_picks_up_pending_template_and_buttons(monkeypatch):
    _capture_schedule(monkeypatch)
    monkeypatch.setattr(ev.ui, "add_head_html", lambda *a, **k: None)
    ev.open_template("queued")
    opened = []
    monkeypatch.setattr(ev.webbrowser, "open", lambda url, new=0: opened.append(url))

    async def check(user):
        state = ev._live_state["current"]
        assert state["pending_template"] == "queued"
        assert "pending_template" not in ev._live_state
        _server(monkeypatch, port=5001)
        user.find("Reload editor").click()
        assert state["iframe"]._props["src"].endswith("&template=queued")
        user.find("Open externally").click()
        assert opened == ["http://127.0.0.1:5001/_spore_studio_editor?external=1"]
        # open_template now goes straight to the live iframe
        ev.open_template("next one")
        assert state["iframe"]._props["src"].endswith("&template=next%20one")

    run_ui(ev.create_spore_studio_tab, check)


def test_inject_css_only_once(monkeypatch):
    _capture_schedule(monkeypatch)
    head, js = [], []
    monkeypatch.setattr(ev.ui, "add_head_html", lambda html, shared=False: head.append(html))
    monkeypatch.setattr(ev.ui, "run_javascript", lambda code: js.append(code))

    def build():
        ev.create_spore_studio_tab()
        ev.create_spore_studio_tab()

    run_ui(build)
    assert len(head) == 1 and len(js) == 1
    assert "spore-nudge" in js[0]

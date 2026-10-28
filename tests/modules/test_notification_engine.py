# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.notification_engine``: history, actions, dedupe, pop-up delivery and ``notify``.

The status footer and service watcher are in ``test_notification_engine_part2.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import contextlib
import json
import types
from types import SimpleNamespace

import pytest

from modules import notification_engine as ne
from _ne_fixtures import ne_env  # noqa: F401  (fixture)


# --------------------------------------------------------------------------- renderer asset

def test_toast_head_html_includes_renderer_and_glue():
    out = ne._toast_head_html()
    assert out.startswith("<script>") and out.endswith("</script>")
    assert "mycelian-toast-action" in out and "MycelianToast.ensureStyles()" in out
    assert len(out) > 500  # the renderer itself was read


def test_toast_head_html_missing_asset(monkeypatch, caplog):
    from modules import path_utils

    def boom(_p):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(path_utils, "get_assets_path", boom)
    out = ne._toast_head_html()
    assert out.startswith("<script>\nif(window.MycelianToast)")
    assert "Could not load the notification renderer" in caplog.text


# --------------------------------------------------------------------------- history persistence

def test_history_path_uses_data_dir():
    p = ne._history_path()
    assert p.name == ne.HISTORY_FILENAME


def test_load_history_missing_file(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_history", [{"stale": 1}])
    monkeypatch.setattr(ne, "_history_loaded", False)
    ne.load_history()
    assert ne._history == [] and ne._history_loaded is True


@pytest.mark.parametrize("content,expected", [
    (json.dumps([{"id": "a"}, 3, "x", {"id": "b"}]), [{"id": "a"}, {"id": "b"}]),
    (json.dumps({"id": "a"}), []),
    ("{not json", []),
])
def test_load_history_contents(ne_env, monkeypatch, content, expected):
    (ne_env.tmp / "history.json").write_text(content, encoding="utf-8")
    monkeypatch.setattr(ne, "_history_loaded", False)
    ne.load_history()
    assert ne._history == expected and ne._history_loaded is True


def test_ensure_history_loaded(ne_env, monkeypatch):
    calls = []
    monkeypatch.setattr(ne, "load_history", lambda: calls.append(1))
    ne._ensure_history_loaded()
    assert calls == []
    monkeypatch.setattr(ne, "_history_loaded", False)
    ne._ensure_history_loaded()
    assert calls == [1]


def test_ensure_history_loaded_rechecks_under_lock(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_history_loaded", False)
    monkeypatch.setattr(ne, "load_history", lambda: pytest.fail("another thread already loaded it"))

    @contextlib.contextmanager
    def lock():
        ne._history_loaded = True
        yield

    monkeypatch.setattr(ne, "_history_lock", lock())
    ne._ensure_history_loaded()


def test_save_history_writes_last_items(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_history", [{"id": str(i)} for i in range(ne.MAX_HISTORY_ITEMS + 5)])
    monkeypatch.setattr(ne, "_history_path", lambda: ne_env.tmp / "sub" / "h.json")
    ne.save_history()
    data = json.loads((ne_env.tmp / "sub" / "h.json").read_text())
    assert len(data) == ne.MAX_HISTORY_ITEMS and data[0]["id"] == "5"


def test_save_history_failure_is_logged(ne_env, monkeypatch, caplog):
    blocker = ne_env.tmp / "file"
    blocker.write_text("x")
    monkeypatch.setattr(ne, "_history_path", lambda: blocker / "h.json")
    ne.save_history()
    assert "Could not save notification history" in caplog.text


def test_flush_history_soon_schedules_one_timer(monkeypatch, tmp_path):
    monkeypatch.setattr(ne, "_history_flush_timer", None)
    created = []

    class FakeTimer:
        def __init__(self, delay, fn):
            self.delay, self.fn, self.daemon, self.started = delay, fn, False, False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(ne.threading, "Timer", FakeTimer)
    saved = []
    monkeypatch.setattr(ne, "save_history", lambda: saved.append(1))
    ne._flush_history_soon()
    ne._flush_history_soon()
    assert len(created) == 1 and created[0].daemon and created[0].started and created[0].delay == 1.0
    created[0].fn()
    assert saved == [1] and ne._history_flush_timer is None


# --------------------------------------------------------------------------- refresh callbacks / loop helpers

def test_register_history_refresh_replaces(ne_env):
    a, b = (lambda: None), (lambda: None)
    ne.register_history_refresh(a)
    ne.register_history_refresh(b)
    assert ne._history_refresh_callbacks == [b]


def test_trigger_history_refresh_runs_callbacks_and_swallows(ne_env):
    seen = []

    def bad():
        raise RuntimeError("x")

    ne._history_refresh_callbacks.extend([bad, lambda: seen.append(1)])
    ne._trigger_history_refresh()
    assert seen == [1] and len(ne_env.ran) == 1


def test_run_on_ui_loop_and_app_schedule_delegate(monkeypatch):
    from modules import ui_timer

    seen = []
    monkeypatch.setattr(ui_timer, "run_on_ui_loop", lambda fn: seen.append(("loop", fn)))
    monkeypatch.setattr(ui_timer, "app_schedule", lambda *a, **k: seen.append(("sched", a, k)) or "timer")
    f = lambda: None
    ne._run_on_ui_loop(f)
    assert ne._app_schedule(1.0, f, once=True) == "timer"
    assert seen == [("loop", f), ("sched", (1.0, f), {"once": True})]


def test_get_clear_remove_history(ne_env, monkeypatch):
    refreshed = []
    monkeypatch.setattr(ne, "_trigger_history_refresh", lambda: refreshed.append(1))
    monkeypatch.setattr(ne, "_history", [{"id": "a"}, {"id": "b"}])
    h = ne.get_history()
    h.append({"id": "zzz"})
    assert [e["id"] for e in ne.get_history()] == ["a", "b"]
    ne.remove_history_item("a")
    assert [e["id"] for e in ne._history] == ["b"]
    assert json.loads((ne_env.tmp / "history.json").read_text()) == [{"id": "b"}]
    ne.clear_history()
    assert ne._history == [] and refreshed == [1, 1]


# --------------------------------------------------------------------------- clipboard

@pytest.fixture
def no_pyperclip(monkeypatch):
    mod = types.ModuleType("pyperclip")

    def copy(text):
        raise RuntimeError("no clipboard backend")

    mod.copy = copy
    monkeypatch.setitem(sys.modules, "pyperclip", mod)


def test_clipboard_pyperclip(monkeypatch):
    got = []
    mod = types.ModuleType("pyperclip")
    mod.copy = got.append
    monkeypatch.setitem(sys.modules, "pyperclip", mod)
    monkeypatch.setattr(ne.subprocess, "run", lambda *a, **k: pytest.fail("no subprocess"))
    assert ne.copy_text_to_clipboard(None) is True
    assert got == [""]


@pytest.mark.parametrize("platform,cmd", [("darwin", ["pbcopy"]), ("win32", ["clip"]),
                                          ("linux", ["xclip", "-selection", "clipboard"])])
def test_clipboard_platform_fallbacks(monkeypatch, no_pyperclip, platform, cmd):
    runs = []
    monkeypatch.setattr(ne.sys, "platform", platform)
    monkeypatch.setattr(ne.subprocess, "run", lambda c, **k: runs.append((c, k)))
    assert ne.copy_text_to_clipboard("héllo") is True
    assert runs[0][0] == cmd and runs[0][1]["input"] == "héllo".encode("utf-8")


def test_clipboard_linux_tries_xsel_then_gives_up(monkeypatch, no_pyperclip):
    runs = []
    monkeypatch.setattr(ne.sys, "platform", "linux")

    def run(cmd, **k):
        runs.append(cmd[0])
        if cmd[0] == "xclip":
            raise FileNotFoundError
        return None

    monkeypatch.setattr(ne.subprocess, "run", run)
    assert ne.copy_text_to_clipboard("x") is True
    assert runs == ["xclip", "xsel"]

    def none(cmd, **k):
        raise FileNotFoundError

    monkeypatch.setattr(ne.subprocess, "run", none)
    assert ne.copy_text_to_clipboard("x") is False


def test_clipboard_subprocess_error(monkeypatch, no_pyperclip):
    monkeypatch.setattr(ne.sys, "platform", "darwin")

    def run(cmd, **k):
        raise OSError("denied")

    monkeypatch.setattr(ne.subprocess, "run", run)
    assert ne.copy_text_to_clipboard("x") is False


# --------------------------------------------------------------------------- action labels/icons/normalisation

@pytest.mark.parametrize("action,label", [
    ({"label": "Custom", "kind": "navigate"}, "Custom"),
    ({"kind": "copy"}, "Copy details"),
    ({"kind": "call"}, "Open"),
    ({"kind": "navigate", "spore_template": "t"}, "Open in Spore Studio"),
    ({"kind": "navigate", "source_template": "t"}, "Open in Source Settings"),
    ({"kind": "navigate", "settings_subtab": "OBS"}, "Open OBS settings"),
    ({"kind": "navigate", "chatbot_tab": "Polls"}, "Open Chatbot → Polls"),
    ({"kind": "navigate", "alerts_tab": "Queue"}, "Open Alerts → Queue"),
    ({"kind": "navigate", "main_tab": "Connectors"}, "Open Connectors"),
    ({"kind": "navigate"}, "Open"),
])
def test_action_label(action, label):
    assert ne._action_label(action) == label


@pytest.mark.parametrize("action,icon", [
    ({"icon": "star"}, "star"), ({"kind": "copy"}, "content_copy"),
    ({"kind": "navigate", "spore_template": "x"}, "auto_awesome"), ({"kind": "navigate"}, "open_in_new"),
])
def test_action_icon(action, icon):
    assert ne._action_icon(action) == icon


def test_normalize_actions():
    fn = lambda: None
    out = ne._normalize_actions([
        "junk",
        {"kind": "navigate"},  # no target -> dropped
        {"kind": "navigate", "main_tab": "Alerts", "label": "", "extra": 1, "alerts_tab": "Queue"},
        {"kind": "copy", "text": "t", "label": None, "junk": 1},
        {"kind": "call", "fn": fn, "label": "Do"},
        {"kind": "call", "fn": "not callable"},
        {"kind": "other"},
    ])
    assert out == [
        {"kind": "navigate", "main_tab": "Alerts", "alerts_tab": "Queue"},
        {"kind": "copy", "text": "t"},
        {"kind": "call", "fn": fn, "label": "Do"},
    ]
    assert ne._normalize_actions(None) == []


# --------------------------------------------------------------------------- run_action

def test_run_action_copy(monkeypatch):
    copied, notes = [], []
    monkeypatch.setattr(ne, "copy_text_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr(ne, "notify", lambda *a, **k: notes.append((a, k)))
    ne.run_action({"kind": "copy", "text": "explicit"}, message="m")
    ne.run_action({"kind": "copy"}, message="m", entry={"title": "T", "details": "D", "source": "obs"})
    ne.run_action({"kind": "copy"}, message="fallback", entry=None)
    assert copied == ["explicit", "T\n\nD", "fallback"]
    assert notes[1][1]["source"] == "obs" and notes[0][0] == ("Copied to the clipboard",)
    assert notes[0][1]["dedupe_key"] == "notify:copied"


def test_run_action_copy_failure(monkeypatch):
    monkeypatch.setattr(ne, "copy_text_to_clipboard", lambda t: False)
    monkeypatch.setattr(ne, "notify", lambda *a, **k: pytest.fail("no notice on failure"))
    ne.run_action({"kind": "copy"}, message="m")


def test_run_action_call(caplog):
    seen = []
    ne.run_action({"kind": "call", "fn": lambda: seen.append(1)}, message="")
    ne.run_action({"kind": "call", "fn": None}, message="")

    def bad():
        raise ValueError("nope")

    ne.run_action({"kind": "call", "fn": bad}, message="")
    assert seen == [1] and "Notification action failed" in caplog.text


def test_run_action_none_and_unknown():
    ne.run_action(None, message="")
    ne.run_action({"kind": "weird"}, message="")


@pytest.fixture
def nav(monkeypatch):
    from modules.help_system import contextual_help as ch

    calls = []
    monkeypatch.setattr(ch, "navigate_to_settings_subtab", lambda s, main_tab=None: calls.append(("settings", s, main_tab)))
    monkeypatch.setattr(ch, "navigate_to_chatbot_subtab", lambda s: calls.append(("chatbot", s)))
    monkeypatch.setattr(ch, "navigate_to_alerts_subtab", lambda s: calls.append(("alerts", s)))
    monkeypatch.setattr(ch, "navigate_to_source_settings_template", lambda s: calls.append(("source", s)))
    monkeypatch.setattr(ch, "navigate_to_main_tab", lambda s: calls.append(("main", s)))
    import modules.uiwindows.spore_studio.editor_view as editor

    monkeypatch.setattr(editor, "open_template", lambda name: calls.append(("open_template", name)))
    return calls


@pytest.mark.parametrize("action,expected", [
    ({"settings_subtab": "OBS", "main_tab": "Settings"}, [("settings", "OBS", "Settings")]),
    ({"settings_subtab": "OBS"}, [("settings", "OBS", None)]),
    ({"chatbot_tab": "Polls"}, [("chatbot", "Polls")]),
    ({"alerts_tab": "Queue"}, [("alerts", "Queue")]),
    ({"source_template": "Cfg"}, [("source", "Cfg")]),
    ({"main_tab": "Connectors"}, [("main", "Connectors")]),
    ({"spore_template": "Card"}, [("main", "Spore Studio"), ("open_template", "Card")]),
    ({}, []),
])
def test_run_action_navigate(nav, action, expected):
    ne.run_action(dict(action, kind="navigate"), message="")
    assert nav == expected


def test_copy_text_for():
    assert ne._copy_text_for(None) == ""
    assert ne._copy_text_for({"details": "D"}) == "D"
    assert ne._copy_text_for({"title": "T", "message": "M", "details": "D"}) == "T - M\n\nD"
    assert ne._copy_text_for({"title": "T", "message": None, "hint": "H"}) == "T\nH"


# --------------------------------------------------------------------------- dedupe / repeat index

def test_repeat_index_and_prune(ne_env, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(ne.time, "monotonic", lambda: t[0])
    ne._remember_repeat("k", "e1")
    assert ne._find_repeat("k", 10) == "e1"
    assert ne._find_repeat("missing", 10) is None
    t[0] += 10
    assert ne._find_repeat("k", 10) is None
    monkeypatch.setattr(ne, "_REPEAT_INDEX_LIMIT", 2)
    t[0] += ne._DEDUPE_ENTRY_TTL_SEC + 1
    ne._remember_repeat("a", "1")
    ne._remember_repeat("b", "2")
    assert set(ne._repeat_index) == {"a", "b"}  # "k" pruned once over the limit


def test_dedupe_marks_and_prune(ne_env, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(ne.time, "monotonic", lambda: t[0])
    assert ne._should_skip_dedupe(None, 10) is False
    assert ne._should_skip_dedupe("k", 10) is False
    ne._mark_dedupe("k")
    assert ne._should_skip_dedupe("k", 10) is True
    t[0] += 10
    assert ne._should_skip_dedupe("k", 10) is False
    monkeypatch.setattr(ne, "_DEDUPE_PRUNE_THRESHOLD", 1)
    t[0] += ne._DEDUPE_ENTRY_TTL_SEC + 1
    ne._mark_dedupe("new")
    assert set(ne._last_emit_monotonic) == {"new"}


# --------------------------------------------------------------------------- payload / delivery

def test_toast_payload():
    entry = {"id": "x", "source": "psn", "title": None, "message": "m", "count": None,
             "actions": [{"kind": "copy"}, {"kind": "navigate", "main_tab": "A"}, {"kind": "navigate", "main_tab": "B"}],
             "progress": 40}
    p = ne._toast_payload(entry, 1234)
    assert p == {"id": "x", "level": "info", "source": "PlayStation", "title": "", "message": "m", "hint": "",
                 "actions": [{"label": "Copy details", "icon": "content_copy"},
                             {"label": "Open A", "icon": "open_in_new"}, {"label": "Open B", "icon": "open_in_new"}],
                 "count": 1, "timeout": 1234, "progress": 40, "click": 1}
    assert ne._toast_payload({"id": "y"}, 0)["click"] is None


class FakeClient:
    def __init__(self, cid, deleted=False, fail=False):
        self.id = cid
        self._deleted = deleted
        self.sent = []
        self.fail = fail
        self.outbox = self

    def enqueue_message(self, kind, data, target):
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append((kind, data, target))


@pytest.fixture
def ng(monkeypatch):
    from nicegui import Client, core

    state = SimpleNamespace(app=SimpleNamespace(is_started=True), clients={}, loop=None)
    monkeypatch.setattr(core, "app", state.app, raising=False)
    monkeypatch.setattr(core, "loop", None, raising=False)
    monkeypatch.setattr(Client, "instances", state.clients)
    state.core = core
    return state


def test_deliver_js_not_started(ng, monkeypatch):
    monkeypatch.setattr(ng.core, "app", None)
    assert ne._deliver_js("x") is False
    monkeypatch.setattr(ng.core, "app", SimpleNamespace(is_started=False))
    assert ne._deliver_js("x") is False


def test_deliver_js_no_live_clients(ng):
    ng.clients["a"] = FakeClient("a", deleted=True)
    assert ne._deliver_js("x") is False


def test_deliver_js_error_logged(ng, monkeypatch, caplog):
    from nicegui import Client

    monkeypatch.setattr(Client, "instances", None)
    assert ne._deliver_js("x") is False
    assert "notification delivery failed" in caplog.text


def test_deliver_js_enqueues_directly_without_loop(ng):
    good, dead, broken = FakeClient("g"), FakeClient("d", deleted=True), FakeClient("b", fail=True)
    ng.clients.update({"g": good, "d": dead, "b": broken})
    assert ne._deliver_js("alert(1)") is True
    assert good.sent == [("run_javascript", {"code": "alert(1)"}, "g")]
    assert dead.sent == [] and broken.sent == []


def test_deliver_js_uses_running_loop(ng, monkeypatch):
    good = FakeClient("g")
    ng.clients["g"] = good
    scheduled = []
    loop = SimpleNamespace(is_running=lambda: True, call_soon_threadsafe=scheduled.append)
    monkeypatch.setattr(ng.core, "loop", loop)
    assert ne._deliver_js("c") is True
    assert good.sent == [] and len(scheduled) == 1
    scheduled[0]()
    assert good.sent


def test_deliver_js_loop_closed_falls_back(ng, monkeypatch):
    good = FakeClient("g")
    ng.clients["g"] = good

    def closed(fn):
        raise RuntimeError("loop closed")

    monkeypatch.setattr(ng.core, "loop", SimpleNamespace(is_running=lambda: True, call_soon_threadsafe=closed))
    assert ne._deliver_js("c") is True
    assert good.sent


def test_deliver_toast_builds_code(ne_env, monkeypatch):
    injected, codes = [], []
    monkeypatch.setattr(ne, "inject_notification_ui_assets", lambda: injected.append(1))
    monkeypatch.setattr(ne, "_deliver_js", lambda code: codes.append(code) or True)
    assert ne._deliver_toast({"id": "x"}, {"config": {"max": 2}}) is True
    assert '"max": 2' in codes[0] and 'MycelianToast.show({"id": "x"})' in codes[0]
    ne._deliver_toast("plain text", {"type": "negative"})
    payload = json.loads(codes[1].split("MycelianToast.show(")[1].rstrip("))"))
    assert payload["title"] == "plain text" and payload["level"] == "error"
    assert payload["timeout"] == ne.DEFAULT_TOAST_TIMEOUT_MS
    assert '"position": "top-right"' in codes[1]  # config from prefs
    ne._deliver_toast("x")
    assert injected == [1, 1, 1]


def test_dismiss_notification(monkeypatch):
    codes = []
    monkeypatch.setattr(ne, "_deliver_js", codes.append)
    ne.dismiss_notification(42)
    assert codes == ['window.MycelianToast&&MycelianToast.dismiss("42")']


# --------------------------------------------------------------------------- pending toasts

def test_queue_toast_replaces_same_id(ne_env, monkeypatch):
    ensured = []
    monkeypatch.setattr(ne, "_ensure_toast_flush_timer", lambda: ensured.append(1))
    ne._queue_toast({"id": "a", "level": "info"}, {"o": 1})
    ne._queue_toast({"id": "a", "level": "info", "v": 2}, {})
    assert ne._pending_toasts == [({"id": "a", "level": "info", "v": 2}, {})]
    assert len(ensured) == 2


def test_queue_toast_trims_to_limit(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_ensure_toast_flush_timer", lambda: None)
    monkeypatch.setattr(ne, "_MAX_PENDING_TOASTS", 2)
    for i in range(4):
        ne._queue_toast({"id": f"i{i}", "level": "info"}, {})
    assert [p["id"] for p, _ in ne._pending_toasts] == ["i2", "i3"]


@pytest.mark.xfail(strict=True, reason="BUG: _queue_toast puts errors first then keeps the LAST N, so queued "
                                       "errors are the first thing dropped instead of being kept")
def test_queue_toast_trim_keeps_errors(ne_env, monkeypatch):
    ensured = []
    monkeypatch.setattr(ne, "_ensure_toast_flush_timer", lambda: ensured.append(1))
    monkeypatch.setattr(ne, "_MAX_PENDING_TOASTS", 3)
    ne._pending_toasts[:] = [("plain", {})]
    ne._queue_toast({"id": "e", "level": "error"}, {})
    ne._queue_toast({"id": "i1", "level": "info"}, {})
    ne._queue_toast({"id": "i2", "level": "info"}, {})
    ids = [p["id"] if isinstance(p, dict) else p for p, _ in ne._pending_toasts]
    assert ids == ["e", "i1", "i2"]


def test_toast_flush_tick(ne_env, monkeypatch):
    flushed = []
    monkeypatch.setattr(ne, "flush_pending_toasts", lambda: flushed.append(1))
    ne._toast_flush_tick()  # no timer
    timer = SimpleNamespace(active=True)
    monkeypatch.setattr(ne, "_toast_flush_timer", timer)
    ne._pending_toasts.append(("x", {}))
    ne._toast_flush_tick()
    assert timer.active is True
    ne._pending_toasts.clear()
    ne._toast_flush_tick()
    assert timer.active is False

    class Stubborn:
        @property
        def active(self):
            return True

        @active.setter
        def active(self, v):
            raise RuntimeError("deleted")

    monkeypatch.setattr(ne, "_toast_flush_timer", Stubborn())
    ne._toast_flush_tick()
    assert len(flushed) == 4


def test_ensure_toast_flush_timer(ne_env, monkeypatch):
    ne._ensure_toast_flush_timer()
    assert ne_env.sched.calls[0][:2] == (1.0, ne._toast_flush_tick)
    timer = ne._toast_flush_timer
    timer.active = False
    ne._ensure_toast_flush_timer()
    assert timer.active is True and len(ne_env.sched.calls) == 1

    class Dead:
        def __setattr__(self, k, v):
            raise RuntimeError("deleted")

    monkeypatch.setattr(ne, "_toast_flush_timer", Dead())
    ne._ensure_toast_flush_timer()
    assert len(ne_env.sched.calls) == 2

    def boom(*a, **k):
        raise RuntimeError("no app")

    monkeypatch.setattr(ne, "_toast_flush_timer", None)
    monkeypatch.setattr(ne, "_app_schedule", boom)
    ne._ensure_toast_flush_timer()
    assert ne._toast_flush_timer is None


def test_flush_pending_toasts(ne_env, monkeypatch):
    ensured = []
    monkeypatch.setattr(ne, "_ensure_toast_flush_timer", lambda: ensured.append(1))
    ne.flush_pending_toasts()  # empty
    ne._pending_toasts.extend([({"id": "ok"}, {}), ({"id": "fail"}, {})])
    monkeypatch.setattr(ne, "_deliver_toast", lambda m, o: m["id"] == "ok")
    ne.flush_pending_toasts()
    assert ne._pending_toasts == [({"id": "fail"}, {})] and ensured == [1]
    monkeypatch.setattr(ne, "_deliver_toast", lambda m, o: True)
    ne.flush_pending_toasts()
    assert ne._pending_toasts == [] and ensured == [1]


def test_find_entry_and_stored_actions(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_history", [{"id": "a", "v": 1}, {"id": "a", "v": 2}])
    assert ne._find_entry("a")["v"] == 2 and ne._find_entry("zz") is None
    fn = lambda: None
    assert ne._stored_actions([{"kind": "call", "fn": fn, "label": "x"}]) == [{"kind": "call", "label": "x"}]


# --------------------------------------------------------------------------- notify

@pytest.fixture
def shown(ne_env, monkeypatch):
    out = []
    monkeypatch.setattr(ne, "_deliver_toast", lambda payload, opts=None: out.append(payload) or True)
    monkeypatch.setattr(ne, "dismiss_notification", lambda i: out.append({"dismissed": i}))
    monkeypatch.setattr(ne, "_trigger_history_refresh", lambda: None)
    return out


def test_notify_basic_entry(shown, ne_env):
    eid = ne.notify("Saved", type="positive", source="CHATBOT", position="top", timeout=3, sticky=True, foo=1)
    (entry,) = ne._history
    assert entry["id"] == eid and entry["level"] == "success" and entry["type"] == "positive"
    assert entry["source"] == "chatbot" and entry["title"] == "" and entry["message"] == "Saved"
    assert entry["muted"] is False and entry["progress"] is None and entry["count"] == 1
    assert shown[0]["timeout"] == 3000 and ne_env.flushes == [1]


def test_notify_details_add_copy_action_once(shown):
    ne.notify("x", details="trace", actions=[{"kind": "copy", "text": "t"}])
    ne.notify("y", details="trace")
    assert [a["kind"] for a in ne._history[0]["actions"]] == ["copy"]
    assert ne._history[1]["actions"] == [{"kind": "copy"}]


def test_notify_muted_not_recorded(shown, ne_env):
    ne_env.prefs.muted = {"obs": ["warning"]}
    ne_env.prefs.record_muted = False
    assert ne.notify("x", type="warning", source="obs") is None
    assert ne._history == [] and shown == []


def test_notify_repeat_inherits_hint_details_and_call_actions(shown):
    fn = lambda: None
    first = ne.notify("Same", title="T", hint="H", details="D", actions=[{"kind": "call", "fn": fn, "label": "Fix"}])
    assert ne._call_actions[first][0]["fn"] is fn
    second = ne.notify("Same", title="T")
    assert first == second
    e = ne._history[0]
    assert e["count"] == 2 and e["read"] is False and e["hint"] == "H" and e["details"] == "D"
    assert [a["kind"] for a in e["actions"]] == ["call", "copy"]


def test_notify_repeat_after_cooldown_is_new_entry(shown, monkeypatch):
    t = [100.0]
    monkeypatch.setattr(ne.time, "monotonic", lambda: t[0])
    a = ne.notify("x", dedupe_cooldown_sec=5)
    t[0] += 6
    b = ne.notify("x", dedupe_cooldown_sec=5)
    assert a != b and len(ne._history) == 2


def test_notify_dedupe_key_is_marked(shown):
    ne.notify("x", dedupe_key="k1")
    assert "k1" in ne._last_emit_monotonic
    assert ne._history[0]["dedupe_key"] == "k1"


def test_notify_history_trimmed(shown, monkeypatch):
    monkeypatch.setattr(ne, "MAX_HISTORY_ITEMS", 3)
    for i in range(5):
        ne.notify(f"m{i}")
    assert [e["message"] for e in ne._history] == ["m2", "m3", "m4"]


def test_notify_call_actions_limit(shown, monkeypatch):
    monkeypatch.setattr(ne, "_CALL_ACTIONS_LIMIT", 2)
    ids = [ne.notify(f"m{i}", actions=[{"kind": "call", "fn": lambda: None}]) for i in range(3)]
    assert list(ne._call_actions) == ids[1:]


def test_notify_with_unknown_notice_id_creates_entry_with_that_id(shown):
    assert ne.notify("x", notice_id="fixed") == "fixed"
    assert ne.notify("y", notice_id="fixed") == "fixed"
    assert len(ne._history) == 1 and ne._history[0]["message"] == "y"
    assert ne._repeat_index == {}


def test_notify_ongoing_keeps_progress(shown):
    ne.notify("x", type="ongoing", progress=50)
    assert ne._history[0]["progress"] == 50 and shown[0]["timeout"] == 0


def test_notify_desktop_when_minimized(shown, monkeypatch):
    from modules import system_notify, tray_controller

    sent = []
    monkeypatch.setattr(tray_controller, "is_minimized", lambda: True)
    monkeypatch.setattr(system_notify, "notify_async", lambda msg, title: sent.append((msg, title)))
    ne.notify("body", type="error", title="Head")
    ne.notify("only text", type="warning")
    ne.notify("info is quiet", type="info")
    assert sent == [("body", "Head"), ("", "only text")]
    ne.notify("only text", type="warning")  # repeat: no second desktop notice
    assert len(sent) == 2


def test_notify_desktop_errors_swallowed(shown, monkeypatch):
    from modules import tray_controller

    def boom():
        raise RuntimeError("no tray")

    monkeypatch.setattr(tray_controller, "is_minimized", boom)
    assert ne.notify("x", type="error")


def test_notify_desktop_disabled_or_not_minimized(shown, monkeypatch, ne_env):
    from modules import system_notify, tray_controller

    monkeypatch.setattr(tray_controller, "is_minimized", lambda: False)
    monkeypatch.setattr(system_notify, "notify_async", lambda *a, **k: pytest.fail("not minimized"))
    ne.notify("x", type="error")
    ne_env.prefs.desktop_when_minimized = False
    monkeypatch.setattr(tray_controller, "is_minimized", lambda: pytest.fail("should not ask"))
    ne.notify("y", type="error")


def test_notify_skip_toast_and_muted_progress_dismisses(shown, ne_env):
    ne.notify("x", skip_toast=True)
    assert shown == []
    ne_env.prefs.popups = False
    eid = ne.notify("y", notice_id="n1", type="ongoing")
    assert shown == [{"dismissed": eid}]


def test_notify_queues_when_delivery_fails(ne_env, monkeypatch):
    queued = []
    monkeypatch.setattr(ne, "_deliver_toast", lambda p, o=None: False)
    monkeypatch.setattr(ne, "_queue_toast", lambda p, o: queued.append((p, o)))
    monkeypatch.setattr(ne, "_trigger_history_refresh", lambda: None)
    eid = ne.notify("x")
    assert queued[0][0]["id"] == eid and "config" in queued[0][1]


def test_notify_loads_history_first(ne_env, monkeypatch, shown):
    (ne_env.tmp / "history.json").write_text(json.dumps([{"id": "old"}]))
    monkeypatch.setattr(ne, "_history_loaded", False)
    ne.notify("new")
    assert [e["id"] for e in ne._history][0] == "old" and len(ne._history) == 2


# --------------------------------------------------------------------------- progress notices

def test_progress_notice_lifecycle(shown, monkeypatch):
    removed = []
    monkeypatch.setattr(ne, "remove_history_item", removed.append)
    n = ne.notify_progress("Copying", source="files", message="start", progress=1)
    assert ne._history[0]["level"] == "ongoing" and ne._history[0]["source"] == "files"
    n.update("half", progress=50, title="Copying more")
    assert n.title == "Copying more" and ne._history[0]["progress"] == 50
    n.update(None)
    assert ne._history[0]["message"] == ""
    n.fail(message="broke", hint="h")
    assert ne._history[0]["level"] == "error" and ne._history[0]["title"] == "Copying more"
    n.update("ignored")
    n.done("ignored")
    n.fail("ignored")
    assert ne._history[0]["message"] == "broke"
    n.close()
    assert removed == [n.id] and shown[-1] == {"dismissed": n.id}


def test_progress_done_with_type(shown):
    n = ne.ProgressNotice("Work")
    n.done(message="ok", type="warning", hint="careful")
    assert ne._history[0]["level"] == "warning" and ne._history[0]["title"] == "Work"
    assert ne._history[0]["hint"] == "careful" and n.finished


def test_progress_context_manager_failure(shown):
    with pytest.raises(KeyError):
        with ne.notify_progress("Loading") as n:
            raise KeyError("boom")
    e = ne._history[0]
    assert e["level"] == "error" and e["message"] == "Something went wrong."
    assert e["details"] == "KeyError: 'boom'"


def test_progress_context_manager_unfinished_closes(shown, monkeypatch):
    removed = []
    monkeypatch.setattr(ne, "remove_history_item", removed.append)
    with ne.notify_progress("Checking") as n:
        n.update("halfway")
    assert removed == [n.id] and n.finished


def test_progress_context_manager_finished_inside(shown, monkeypatch):
    monkeypatch.setattr(ne, "remove_history_item", lambda i: pytest.fail("finished: keep it"))
    with ne.notify_progress("Loading") as n:
        n.done("Loaded")
    assert ne._history[0]["title"] == "Loaded"


# --------------------------------------------------------------------------- nav helpers / critical

def test_nav_helpers():
    assert ne.nav_actions_settings("OBS") == [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": "OBS"}]
    assert ne.nav_actions_main_tab("Alerts") == [{"kind": "navigate", "main_tab": "Alerts"}]
    assert ne.nav_chatbot("Polls") == [{"kind": "navigate", "main_tab": "Chatbot", "chatbot_tab": "Polls"}]
    assert ne.nav_alerts("Queue") == [{"kind": "navigate", "main_tab": "Alerts", "alerts_tab": "Queue"}]
    assert ne.nav_spore_template("C") == [{"kind": "navigate", "main_tab": "Spore Studio", "spore_template": "C"}]
    assert ne.nav_source_template("S") == [{"kind": "navigate", "main_tab": "Source Settings", "source_template": "S"}]


def test_notify_critical_forwards(monkeypatch):
    seen = []
    monkeypatch.setattr(ne, "notify", lambda msg, **kw: seen.append((msg, kw)) or "id")
    assert ne.notify_critical("bad", source="obs", title="T", hint="H", details="D", dedupe_key="k",
                              actions=[1], timeout=5) == "id"
    msg, kw = seen[0]
    assert msg == "bad" and kw["type"] == "error" and kw["dedupe_cooldown_sec"] == 120.0
    assert kw["source"] == "obs" and kw["actions"] == [1] and kw["timeout"] == 5


# --------------------------------------------------------------------------- game hook suggestion

@pytest.fixture
def hooks(monkeypatch, ne_env):
    import modules.game_hooks as gh
    import modules.game_hooks.base as ghb

    state = SimpleNamespace(cls=SimpleNamespace(ui=SimpleNamespace(supported_platforms=("windows", "macos"),
                                                                   title="FF7")),
                            enabled=False, os="windows", notes=[])
    monkeypatch.setattr(gh, "get_hook_class", lambda hid: state.cls)
    monkeypatch.setattr(gh, "is_hook_enabled", lambda hid: state.enabled)
    monkeypatch.setattr(ghb, "runtime_os_key", lambda: state.os)
    monkeypatch.setattr(ne, "notify", lambda msg, **kw: state.notes.append((msg, kw)))
    return state


def test_game_hook_suggested_once(hooks):
    ne.maybe_suggest_game_hook_for_category("  Final Fantasy VII ")
    ne.maybe_suggest_game_hook_for_category("FF7")
    ((msg, kw),) = hooks.notes
    assert msg == "The FF7 game hook can show live game data in your overlays."
    assert kw["title"] == "Playing Final Fantasy VII? Try the game hook"
    assert kw["source"] == "game_hooks" and kw["dedupe_key"] == "game_hook_suggest:ff7"
    assert kw["actions"][0]["settings_subtab"] == "Game Hooks"
    assert kw["hint"] == ne._GAME_HOOK_HINTS["ff7"]
    assert ne._game_hook_suggested == {"ff7"}


@pytest.mark.parametrize("case", ["unknown", "none", "no_class", "platform", "enabled", "error"])
def test_game_hook_not_suggested(hooks, monkeypatch, case):
    category = "factorio"
    if case == "unknown":
        category = "Just Chatting"
    elif case == "none":
        category = None
    elif case == "no_class":
        hooks.cls = None
    elif case == "platform":
        hooks.os = "linux"
    elif case == "enabled":
        hooks.enabled = True
    else:
        import modules.game_hooks as gh

        def boom(hid):
            raise RuntimeError("registry")

        monkeypatch.setattr(gh, "get_hook_class", boom)
    ne.maybe_suggest_game_hook_for_category(category)
    assert hooks.notes == [] and ne._game_hook_suggested == set()

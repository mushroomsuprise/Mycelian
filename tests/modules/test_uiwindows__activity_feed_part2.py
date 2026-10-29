# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/activity_feed.py (part 2): the tab host and its state slots, live
push debounce, queue/meta polling, timers, assets, renderer actions, the fail-open watchdog,
fallback list and client lifecycle hooks. The tab is built with NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.client import Client  # noqa: E402

from modules import alert_processor, dataobjects, mainuiwindow, notification_engine, path_utils, tray_controller, web_engine  # noqa: E402
from modules.help_system import contextual_help, help_render  # noqa: E402
from modules.uiwindows import activity_feed as feed  # noqa: E402
from ui_sim import run_ui  # noqa: E402

_ORIGINALS = {name: getattr(feed, name) for name in ("_emit_overlay_event", "_run_on_ui_loop")}

_GLOBALS = (
    "_live_seq", "_legacy_hours_cache", "_history_payload", "_person_payload", "_history_task_running",
    "_history_rerun", "_condensed_update_scheduled", "_condensed_rebuild_running", "_condensed_rebuild_rerun",
    "_host", "_slot_cache", "_slot_rev", "_live_push_scheduled", "_queue_poll_timer", "_watchdog_timer",
    "_last_queue_sig", "_last_meta", "_meta_tick", "_health_failures", "_registered_event_clients",
    "_assets_shared", "_hooks_registered_client_ids",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in _GLOBALS:
        monkeypatch.setattr(feed, name, copy.copy(getattr(feed, name)))
    monkeypatch.setattr(feed, "activity_feed_state", feed.ActivityFeedState())
    vm = feed.ActivityFeedViewManager()
    vm._loaded = True
    monkeypatch.setattr(vm, "save", lambda: True)
    monkeypatch.setattr(feed, "view_manager", vm)
    emitted = []
    monkeypatch.setattr(feed, "_emit_overlay_event", lambda event, payload, to=None: emitted.append((event, payload, to)) or True)
    notes = []
    monkeypatch.setattr(feed, "notify", lambda msg, **k: notes.append((msg, k)))
    ui_calls = []
    monkeypatch.setattr(feed, "_run_on_ui_loop", lambda fn: ui_calls.append(fn))
    monkeypatch.setattr(feed, "current_feed_settings", lambda app_settings=None: {"layout": "rows"})
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: True)
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    timers = []

    class _Timer:
        active = True

    def schedule(delay, fn, once=False, active=True):
        t = _Timer()
        timers.append((delay, fn, once, t))
        return t

    monkeypatch.setattr(feed, "app_schedule", schedule)
    return SimpleNamespace(vm=vm, emitted=emitted, notes=notes, timers=timers)


@pytest.fixture
def sync_ui(monkeypatch):
    """Run UI-loop callbacks immediately."""
    monkeypatch.setattr(feed, "_run_on_ui_loop", lambda fn: fn())
    monkeypatch.setattr(feed, "run_on_ui_loop", lambda fn: fn())


class FakeEl:
    def __init__(self):
        self.is_deleted = False
        self.client = object()
        self._props = {}
        self.updates = 0
        self.added, self.removed = [], []
        self.content = None

    def update(self):
        self.updates += 1

    def classes(self, add=None, remove=None):
        if add:
            self.added.append(add)
        if remove:
            self.removed.append(remove)

    def set_content(self, html):
        self.content = html


def fake_host(**slots):
    names = feed._SLOT_NAMES
    return feed._FeedHost(FakeEl(), {n: slots.get(n, FakeEl()) for n in names}, FakeEl())


# ----------------------------------------------------------------- slots

def test_slot_values(monkeypatch):
    monkeypatch.setattr(feed, "current_queue_state", lambda: {"q": 1})
    monkeypatch.setattr(feed, "_meta_state", lambda: {"m": 1})
    feed.activity_feed_state.live_alerts = [{"type": "follow", "username": "A"}]
    assert feed._slot_value("settings") == {"layout": "rows"}
    assert feed._slot_value("view")["mode"] == "live"
    assert feed._slot_value("live")[0]["kind"] == "follow"
    assert feed._slot_value("history") is feed._history_payload
    assert feed._slot_value("person") is feed._person_payload
    assert feed._slot_value("queue") == {"q": 1} and feed._slot_value("meta") == {"m": 1}
    assert feed._slot_value("nope") is None


def test_apply_slot_and_push(monkeypatch, sync_ui):
    feed._apply_slot("live", "[]")  # no host
    host = fake_host()
    monkeypatch.setattr(feed, "_host", host)
    feed._apply_slot("live", '{"a":1}')
    el = host.slots["live"]
    assert el._props == {"data-json": '{"a":1}', "data-rev": "1"} and el.updates == 1
    feed._apply_slot("live", '{"a":1}')  # cached
    assert el.updates == 1
    host.slots["view"].is_deleted = True
    feed._apply_slot("view", "{}")
    host.slots.pop("queue")
    feed._apply_slot("queue", "{}")
    assert feed._slot_rev == 1
    feed._push_slot("history", {"rows": [], "obj": object()})
    assert json.loads(host.slots["history"]._props["data-json"]) == {"rows": []}

    class _Bad:
        pass

    monkeypatch.setattr(feed, "_json_ready", lambda v: 1 / 0)
    feed._push_slot("meta", _Bad())  # not serialisable: logged
    assert "data-json" not in host.slots["meta"]._props


def test_push_slot_without_tab(monkeypatch):
    called = []
    monkeypatch.setattr(feed, "_run_on_ui_loop", lambda fn: called.append(fn))
    feed._push_slot("live", [])
    assert called == []


def test_push_all_slots(monkeypatch):
    pushed = []
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: pushed.append(name))
    monkeypatch.setattr(feed, "_slot_value", lambda name: name)
    feed.push_all_slots()
    assert tuple(pushed) == feed._SLOT_NAMES


def test_schedule_live_push(monkeypatch, isolated, sync_ui):
    pushed = []
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: pushed.append(name))
    feed._schedule_live_push()  # no tab
    assert isolated.timers == []
    monkeypatch.setattr(feed, "_host", fake_host())
    feed._schedule_live_push()
    feed._schedule_live_push()  # coalesced
    assert len(isolated.timers) == 1 and isolated.timers[0][0] == feed._LIVE_PUSH_DEBOUNCE_SEC
    isolated.timers[0][1]()
    assert pushed == ["live"] and feed._live_push_scheduled is False

    def boom(*a, **k):
        raise RuntimeError("no app")

    monkeypatch.setattr(feed, "app_schedule", boom)
    feed._schedule_live_push()  # pushes right away
    assert pushed == ["live", "live"]


# ----------------------------------------------------------------- queue / meta

def test_fallback_queue_state(monkeypatch):
    alerts = [SimpleNamespace(alert_id="a", alert_type="bits", username="U")]
    monkeypatch.setattr(alert_processor, "snapshot_alert_queue", lambda: alerts)
    engine = SimpleNamespace(ALERT_PLAYING=True, ALERTS_PAUSED=True, ALERTS_MUTED=False)
    s = feed._fallback_queue_state(engine)
    assert s["playing"] == {"alert_id": None, "type": "", "username": ""} and s["paused"] is True
    assert s["queue"] == [{"alert_id": "a", "type": "bits", "username": "U"}]
    monkeypatch.setattr(alert_processor, "snapshot_alert_queue", lambda: 1 / 0)
    s = feed._fallback_queue_state(SimpleNamespace())
    assert s["queue"] == [] and s["playing"] is None


def test_current_queue_state(monkeypatch):
    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: {"paused": True, "queue": [{"alert_id": "q"}]})
    s = feed.current_queue_state()
    assert s["contract"] is True and s["paused"] is True and s["queue"][0]["alert_id"] == "q"
    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: 1 / 0)
    monkeypatch.setattr(feed, "_fallback_queue_state", lambda engine: {"paused": False, "queue": []})
    assert feed.current_queue_state()["contract"] is False
    monkeypatch.setattr(web_engine, "get_alert_queue_state", None)
    assert feed.current_queue_state()["contract"] is False
    monkeypatch.setitem(sys.modules, "modules.web_engine", None)
    import modules

    monkeypatch.delattr(modules, "web_engine")
    assert feed.current_queue_state()["contract"] is False


def test_queue_sig_and_meta(monkeypatch):
    assert feed._queue_sig({"a": 1, "server_now_ms": 5}) == feed._queue_sig({"a": 1, "server_now_ms": 9})
    assert feed._meta_state() == {"app": True, "dock_open": None}
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(_overlay_connected_routes=lambda: {"activity_feed"}))
    assert feed._meta_state()["dock_open"] is True
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(_overlay_connected_routes=lambda: 1 / 0))
    assert feed._meta_state()["dock_open"] is None


def test_poll_queue(monkeypatch):
    pushed = []
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: pushed.append(name))
    state = {"paused": False}
    monkeypatch.setattr(feed, "current_queue_state", lambda: dict(state))
    meta = {"app": True}
    monkeypatch.setattr(feed, "_meta_state", lambda: dict(meta))
    feed._poll_queue()  # no tab
    assert pushed == []
    monkeypatch.setattr(feed, "_host", fake_host())
    feed._poll_queue()
    feed._poll_queue()  # unchanged
    assert pushed == ["queue"]
    state["paused"] = True
    for _ in range(8):
        feed._poll_queue()
    assert pushed == ["queue", "queue", "meta"]
    feed._poll_queue(force=True)
    assert pushed[-2:] == ["queue", "meta"]


def test_sync_helpers(monkeypatch, sync_ui):
    polls = []
    monkeypatch.setattr(feed, "_poll_queue", lambda force=False: polls.append(force))
    feed.sync_pause_breath(True)
    feed.sync_pause_button_state()
    feed.sync_mute_button_state()
    assert polls == [True, True, True]


def test_timers(isolated):
    feed._start_timers()
    feed._start_timers()  # already running
    assert [d for d, *_ in isolated.timers] == [feed._QUEUE_POLL_SEC, feed._FEED_WATCHDOG_INTERVAL_SEC]
    poll, watch = feed._queue_poll_timer, feed._watchdog_timer

    class _Bad:
        @property
        def active(self):
            return True

        @active.setter
        def active(self, v):
            raise RuntimeError("x")

    feed._watchdog_timer = _Bad()
    feed._stop_timers()
    assert poll.active is False and feed._queue_poll_timer is None and feed._watchdog_timer is None


# ----------------------------------------------------------------- assets

def test_read_asset(monkeypatch, tmp_path):
    (tmp_path / "default_assets").mkdir()
    (tmp_path / "default_assets" / "x.js").write_text("JS", encoding="utf-8")
    monkeypatch.setattr(path_utils, "get_assets_path", lambda rel: str(tmp_path / rel))
    assert feed._read_asset("x.js") == "JS"
    assert feed._read_asset("missing.js") == ""


def test_real_assets_and_stylesheet():
    assert "MycelianFeed" in feed._read_asset("activity_feed.js")
    css = feed.feed_app_stylesheet()
    assert ".afx-host" in css and css.count("{") == css.count("}")


def test_inject_assets(monkeypatch):
    added, injected = [], []
    monkeypatch.setattr(help_render, "inject_page_stylesheet", lambda sid, css: added.append(sid))
    monkeypatch.setattr(feed.ui, "add_head_html", lambda code, shared=False: added.append(("head", shared)))
    monkeypatch.setattr(feed, "_reinject_renderer", lambda js=None: injected.append(js))
    monkeypatch.setattr(feed, "_read_asset", lambda name: "" if name.endswith(".js") else "css")
    feed._inject_assets()
    assert added == [feed._STYLE_ID] and injected == []
    monkeypatch.setattr(feed, "_read_asset", lambda name: "JS" if name.endswith(".js") else "css")
    feed._inject_assets()
    feed._inject_assets()
    assert added.count(("head", True)) == 1 and injected == ["JS", "JS"]


def test_reinject_renderer(monkeypatch):
    ran = []
    client = SimpleNamespace(has_socket_connection=True, run_javascript=lambda js: ran.append(js))
    monkeypatch.setattr(feed, "_feed_client", lambda: client)
    monkeypatch.setattr(feed, "_read_asset", lambda name: "JS")
    feed._reinject_renderer()
    assert ran == ["JS"]
    client.has_socket_connection = False
    feed._reinject_renderer("X")
    assert ran == ["JS"]
    monkeypatch.setattr(feed, "_feed_client", lambda: SimpleNamespace(has_socket_connection=True, run_javascript=lambda js: 1 / 0))
    feed._reinject_renderer("X")
    monkeypatch.setattr(feed, "_feed_client", lambda: None)
    feed._reinject_renderer("X")


def test_feed_client(monkeypatch):
    host = fake_host()
    monkeypatch.setattr(feed, "_host", host)
    assert feed._feed_client() is host.host.client

    class _Gone:
        is_deleted = False
        n = 0

        @property
        def client(self):
            _Gone.n += 1
            if _Gone.n > 1:
                raise ValueError("x")
            return object()

    monkeypatch.setattr(feed, "_host", feed._FeedHost(_Gone(), {}, None))
    assert feed._feed_client() is None
    monkeypatch.setattr(feed, "_host", None)
    import nicegui

    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client="CTX"))
    assert feed._feed_client() == "CTX"

    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(nicegui, "context", _Ctx())
    assert feed._feed_client() is None


# ----------------------------------------------------------------- actions

def test_open_settings_section(monkeypatch, sync_ui):
    nav, ran = [], []
    monkeypatch.setattr(contextual_help, "navigate_to_settings_subtab", lambda sub, main_tab=None: nav.append((sub, main_tab)))
    monkeypatch.setattr(feed, "_feed_client", lambda: SimpleNamespace(run_javascript=lambda js: ran.append(js)))
    feed.open_feed_settings()
    assert nav == [("App Settings", "Settings")] and '"aps-activity-feed"' in ran[0]
    monkeypatch.setattr(feed, "_feed_client", lambda: SimpleNamespace(run_javascript=lambda js: 1 / 0))
    feed._open_settings_section("aps-storage")
    monkeypatch.setattr(feed, "_feed_client", lambda: None)
    feed._open_settings_section()
    monkeypatch.setattr(contextual_help, "navigate_to_settings_subtab", lambda *a, **k: 1 / 0)
    feed._open_settings_section()
    assert len(nav) == 3


def test_open_help(monkeypatch):
    opened = []
    monkeypatch.setattr(contextual_help, "_open_help_target", lambda t: opened.append(t))
    feed._open_help()
    assert opened == ["activity_feed"]
    monkeypatch.setattr(contextual_help, "get_help_target_for_tab", lambda tab: None)
    feed._open_help()
    assert opened[-1] == "alert_testing"
    monkeypatch.setattr(contextual_help, "_open_help_target", lambda t: 1 / 0)
    feed._open_help()


def test_dock_url(monkeypatch):
    assert feed.dock_url() == "http://127.0.0.1:5000/activity_feed"
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(port=6000, _local_connect_host=lambda: "localhost"))
    assert feed.dock_url() == "http://localhost:6000/activity_feed"
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(port="x", _local_connect_host=lambda: "h"))
    assert feed.dock_url() == "http://127.0.0.1:5000/activity_feed"


def test_copy_dock_link(monkeypatch, isolated):
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda text: True)
    feed._copy_dock_link()
    msg, kw = isolated.notes[-1]
    assert kw["type"] == "success" and kw["actions"] is None and msg.endswith("/activity_feed")
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda text: 1 / 0)
    written = []
    monkeypatch.setattr(feed.ui.clipboard, "write", lambda text: written.append(text))
    feed._copy_dock_link()
    assert isolated.notes[-1][1]["type"] == "success" and written
    monkeypatch.setattr(feed.ui.clipboard, "write", lambda text: 1 / 0)
    monkeypatch.setattr(notification_engine, "copy_text_to_clipboard", lambda text: False)
    feed._copy_dock_link()
    kw = isolated.notes[-1][1]
    assert kw["type"] == "warning" and kw["actions"][0]["kind"] == "copy"


@pytest.fixture
def actions(monkeypatch):
    calls = []
    monkeypatch.setattr(mainuiwindow, "toggle_alerts", lambda: calls.append("toggle_alerts"))
    monkeypatch.setattr(mainuiwindow, "toggle_mute", lambda: calls.append("toggle_mute"))
    monkeypatch.setattr(feed, "_poll_queue", lambda force=False: calls.append("poll"))
    monkeypatch.setattr(feed, "skip_alert", lambda data: calls.append(("skip", data)) or {"ok": True})
    monkeypatch.setattr(feed, "replay_alert", lambda data: calls.append(("replay", data)) or {"ok": True})
    monkeypatch.setattr(feed, "go_to_page", lambda page: calls.append(("page", page)))
    monkeypatch.setattr(feed, "_open_settings_section", lambda anchor="": calls.append(("settings", anchor)))
    monkeypatch.setattr(feed, "_open_help", lambda: calls.append("help"))
    monkeypatch.setattr(feed, "_copy_dock_link", lambda: calls.append("copy"))
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", False)
    monkeypatch.setattr(web_engine, "ALERTS_MUTED", True)
    return calls


def test_handle_feed_action_queue_controls(actions):
    feed.handle_feed_action("pause", {})
    feed.handle_feed_action("pause", {"paused": False})  # already unpaused: no toggle
    feed.handle_feed_action("pause", {"paused": True})
    feed.handle_feed_action("mute", None)
    feed.handle_feed_action("mute", {"muted": True})  # already muted
    assert actions == ["toggle_alerts", "poll", "poll", "toggle_alerts", "poll", "toggle_mute", "poll", "poll"]
    actions.clear()
    assert feed.handle_feed_action("skip", {"alert_id": "a"}) == {"ok": True}
    feed.handle_feed_action("skip", {})
    assert actions == [("skip", {"alert_id": "a"}), "poll", ("skip", {"type": "current"}), "poll"]
    actions.clear()
    feed.handle_feed_action("replay", {"alert_id": "r", "label": "Bits", "user": "U"})
    assert actions == [("replay", {"alert_id": "r", "type": "Bits", "username": "U"})]


def test_handle_feed_action_view_and_misc(actions, isolated):
    feed.handle_feed_action("mode", {"mode": "history"})
    assert isolated.vm.snapshot()["mode"] == "history"
    feed.handle_feed_action("mode", {"mode": "bogus"})
    feed.handle_feed_action("filter", {"key": "bits", "value": 0})
    feed.handle_feed_action("filter", {"key": "nope", "value": 0})
    assert isolated.vm.snapshot()["filters"]["bits"] is False
    feed.handle_feed_action("search", {"text": "bob"})
    assert isolated.vm.snapshot()["search"] == "bob"
    feed.handle_feed_action("page", {"page": 2})
    feed.handle_feed_action("settings")
    feed.handle_feed_action("storage")
    feed.handle_feed_action("help")
    feed.handle_feed_action("copy-dock")
    feed.handle_feed_action("whatever")
    assert actions == [("page", 2), ("settings", "aps-activity-feed"), ("settings", "aps-storage"), "help", "copy"]


def test_handle_feed_action_error(monkeypatch):
    monkeypatch.setattr(feed, "go_to_page", lambda page: 1 / 0)
    assert feed.handle_feed_action("page", {"page": 1}) is None


def test_on_feed_event(monkeypatch):
    seen = []
    monkeypatch.setattr(feed, "handle_feed_action", lambda action, data: seen.append((action, data)))
    feed._on_feed_event(SimpleNamespace(args="x"))
    feed._on_feed_event(SimpleNamespace())
    feed._on_feed_event(SimpleNamespace(args={"action": "help"}))
    feed._on_feed_event(SimpleNamespace(args={}))
    assert seen == [("help", {"action": "help"}), ("", {})]


# ----------------------------------------------------------------- watchdog / fallback

@pytest.mark.parametrize(
    "result, failures, out",
    [
        (None, 1, ("skip", 1)),
        ({"ok": True}, 3, ("ok", 0)),
        ({"ok": False, "hosts": 0, "visible": 1}, 1, ("skip", 1)),
        ({"ok": False, "hosts": 1, "visible": 0}, 1, ("skip", 1)),
        ({"ok": False, "hosts": 1, "visible": 1}, 0, ("reinject", 1)),
        ({"ok": False, "hosts": 1, "visible": 1}, 1, ("fallback", 2)),
    ],
)
def test_feed_health_action(result, failures, out):
    assert feed._feed_health_action(result, failures) == out


def test_render_fallback_html():
    html = feed.render_fallback_html([])
    assert "No events yet this session." in html
    rows = [{"user": "<b>", "what": "cheered", "what_full": "cheered for 3 months", "amount": "100 bits", "msg": "hi<", "ts": 1}]
    html = feed.render_fallback_html(rows)
    assert "<b>&lt;b&gt;</b> cheered for 3 months · 100 bits — <i>hi&lt;</i>" in html
    rows = [{"user": "u"}] * 5
    assert feed.render_fallback_html(rows, limit=2).count('class="afx-fb-row"') == 2


def test_render_fallback_without_duration(monkeypatch):
    monkeypatch.setattr(feed, "show_sub_duration", lambda app_settings=None: False)
    html = feed.render_fallback_html([{"user": "u", "what": "subscribed", "what_full": "subscribed for 3 months"}])
    assert "subscribed<small>" in html


def test_set_fallback(monkeypatch):
    feed._set_fallback(True)  # no host
    host = fake_host()
    monkeypatch.setattr(feed, "_host", host)
    feed.activity_feed_state.live_alerts = [{"type": "follow", "username": "A"}]
    feed._set_fallback(True)
    assert "A" in host.fallback.content and host.host.added == ["is-fallback"] and host.fallback_on
    feed._set_fallback(True)  # stays on (no second warning)
    feed._set_fallback(False)
    assert host.host.removed == ["is-fallback"] and not host.fallback_on
    feed._set_fallback(False)
    host.fallback.set_content = lambda html: 1 / 0
    feed._set_fallback(True)
    assert host.fallback_on


def test_feed_ui_is_on_screen(monkeypatch):
    monkeypatch.setattr(tray_controller, "is_minimized", lambda: True)
    assert feed._feed_ui_is_on_screen() is False
    monkeypatch.setattr(tray_controller, "is_minimized", lambda: 1 / 0)
    monkeypatch.setattr(contextual_help, "get_current_tab_context", lambda: ("Alerts", None))
    assert feed._feed_ui_is_on_screen() is False
    monkeypatch.setattr(contextual_help, "get_current_tab_context", lambda: ("Activity Feed", None))
    monkeypatch.setattr(feed, "_get_feed_client", lambda: object())
    assert feed._feed_ui_is_on_screen() is True
    monkeypatch.setattr(contextual_help, "get_current_tab_context", lambda: 1 / 0)
    monkeypatch.setattr(feed, "_get_feed_client", lambda: None)
    assert feed._feed_ui_is_on_screen() is False


def test_get_feed_client(monkeypatch):
    connected = SimpleNamespace(has_socket_connection=True)
    host = fake_host()
    host.host.client = connected
    monkeypatch.setattr(feed, "_host", host)
    assert feed._get_feed_client() is connected
    monkeypatch.setattr(feed, "_host", None)
    other = SimpleNamespace(has_socket_connection=True)
    monkeypatch.setattr(Client, "instances", {"a": SimpleNamespace(has_socket_connection=False), "b": other})
    assert feed._get_feed_client() is other
    monkeypatch.setattr(Client, "instances", {})
    assert feed._get_feed_client() is None

    class _Bad:
        def values(self):
            raise RuntimeError("x")

    monkeypatch.setattr(Client, "instances", _Bad())
    assert feed._get_feed_client() is None


class _JSClient:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    async def run_javascript(self, code, timeout=0):
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.parametrize(
    "result, failures, expect",
    [
        ({"ok": True}, 1, ["fallback-off"]),
        ({"ok": False, "hosts": 1, "visible": 1}, 0, ["reinject", "push"]),
        ({"ok": False, "hosts": 1, "visible": 1}, 1, ["fallback-on", "reinject"]),
        ("weird", 0, []),
    ],
)
def test_probe_feed_health(monkeypatch, result, failures, expect):
    calls = []
    monkeypatch.setattr(feed, "_health_failures", failures)
    monkeypatch.setattr(feed, "_feed_client", lambda: _JSClient(result))
    monkeypatch.setattr(feed, "_set_fallback", lambda on: calls.append("fallback-on" if on else "fallback-off"))
    monkeypatch.setattr(feed, "_reinject_renderer", lambda js=None: calls.append("reinject"))
    monkeypatch.setattr(feed, "push_all_slots", lambda: calls.append("push"))
    asyncio.run(feed._probe_feed_health())
    assert calls == expect


def test_probe_feed_health_no_client_or_error(monkeypatch):
    monkeypatch.setattr(feed, "_feed_client", lambda: None)
    asyncio.run(feed._probe_feed_health())
    monkeypatch.setattr(feed, "_feed_client", lambda: _JSClient(exc=TimeoutError("slow")))
    asyncio.run(feed._probe_feed_health())
    assert feed._health_failures == 0


def test_run_feed_watchdog(monkeypatch):
    from modules import shutdown

    created, calls = [], []

    def create(coro, name=""):
        created.append(name)
        coro.close()

    monkeypatch.setattr(feed.background_tasks, "create", create)
    monkeypatch.setattr(feed, "_set_fallback", lambda on: calls.append(on))
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: True)
    feed._run_feed_watchdog()
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: 1 / 0)
    feed._run_feed_watchdog()  # no tab
    host = fake_host()
    monkeypatch.setattr(feed, "_host", host)
    monkeypatch.setattr(feed, "_feed_ui_is_on_screen", lambda: False)
    feed._run_feed_watchdog()
    assert created == []
    monkeypatch.setattr(feed, "_feed_ui_is_on_screen", lambda: True)
    feed._run_feed_watchdog()
    host.fallback_on = True
    feed._run_feed_watchdog()
    assert created == ["activity_feed_health"] * 2 and calls == [True]

    def boom(coro, name=""):
        coro.close()
        raise RuntimeError("no loop")

    monkeypatch.setattr(feed.background_tasks, "create", boom)
    feed._run_feed_watchdog()


# ----------------------------------------------------------------- lifecycle

def test_recover_after_reconnect(monkeypatch):
    pushed = []
    monkeypatch.setattr(feed, "push_all_slots", lambda: pushed.append(1))
    feed._slot_cache["live"] = "x"
    feed.recover_after_client_reconnect()
    assert feed._slot_cache == {} and pushed == [1]


def test_register_client_lifecycle_hooks(monkeypatch, isolated):
    import nicegui

    class _Client:
        id = "c1"

        def __init__(self):
            self.connect, self.delete = [], []

        def on_connect(self, fn):
            self.connect.append(fn)

        def on_delete(self, fn):
            self.delete.append(fn)

    c = _Client()
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=c))
    feed.register_client_lifecycle_hooks()
    feed.register_client_lifecycle_hooks()  # once per client
    assert len(c.connect) == 1 and "c1" in feed._hooks_registered_client_ids
    c.connect[0]()
    assert isolated.timers[-1][0] == 0.5 and isolated.timers[-1][1] is feed.recover_after_client_reconnect
    feed._registered_event_clients.add("c1")
    c.delete[0]()
    assert "c1" not in feed._hooks_registered_client_ids and "c1" not in feed._registered_event_clients

    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(nicegui, "context", _Ctx())
    feed.register_client_lifecycle_hooks()


# ----------------------------------------------------------------- building the tab

@pytest.fixture
def tab_env(monkeypatch):
    monkeypatch.setattr(feed, "current_queue_state", lambda: {"paused": False, "queue": []})
    monkeypatch.setattr(feed, "_meta_state", lambda: {"app": True, "dock_open": None})
    monkeypatch.setattr(feed, "_read_asset", lambda name: "/*css*/" if name.endswith(".css") else "window.MycelianFeed={};")
    refreshed, rebuilt = [], []
    monkeypatch.setattr(feed, "refresh_restored_alerts", lambda: refreshed.append(1))
    monkeypatch.setattr(feed, "_schedule_condensed_rebuild", lambda reason: rebuilt.append(reason))
    return SimpleNamespace(refreshed=refreshed, rebuilt=rebuilt)


@pytest.mark.parametrize("mode", ["live", "history", "person"])
def test_create_activity_feed_tab(monkeypatch, isolated, tab_env, mode):
    isolated.vm._view.update(feed.mode_patch(mode))
    feed.activity_feed_state.live_alerts = [{"type": "follow", "username": "Amy"}]
    made = {}

    def build():
        feed.create_activity_feed_tab()
        made["host"] = feed._host

    async def check(user):
        host = made["host"]
        assert "afx-host" in host.host.classes and host.host.props.get("data-afx-app") == "1"
        for name in feed._SLOT_NAMES:
            el = host.slots[name]
            assert el.props.get("data-slot") == name and "data-json" in el.props
        live = json.loads(host.slots["live"].props["data-json"])
        assert live[0]["user"] == "Amy"
        assert json.loads(host.slots["view"].props["data-json"])["mode"] == mode
        assert user.client.id in feed._registered_event_clients
        # renderer actions arrive through the layout event
        seen = []
        monkeypatch.setattr(feed, "handle_feed_action", lambda action, data: seen.append(action))
        from nicegui.testing.user_interaction import UserInteraction

        types = {l.type for l in user.client.layout._event_listeners.values()}
        event_type = next(t for t in types if t.lower().replace("-", "") == feed._FEED_EVENT.replace("-", ""))
        UserInteraction(user, {user.client.layout}, None).trigger(event_type, {"action": "help"})
        assert seen == ["help"]

    run_ui(build, check)
    assert feed.activity_feed_state.is_initialized is True
    assert [d for d, *_ in isolated.timers] == [feed._QUEUE_POLL_SEC, feed._FEED_WATCHDOG_INTERVAL_SEC]
    assert tab_env.refreshed == ([1] if mode == "history" else [])
    assert tab_env.rebuilt == (["tab_open"] if mode == "person" else [])
    assert 'window.MycelianFeed' in Client.shared_head_html


def test_create_tab_skips_unserialisable_slot(monkeypatch, isolated, tab_env):
    real = feed._slot_value
    monkeypatch.setattr(feed, "_slot_value", lambda name: object() if name == "meta" else real(name))
    real_dumps = json.dumps

    def dumps(value, **kw):
        if value is feed._JSON_DROP:
            raise TypeError("cannot serialise")
        return real_dumps(value, **kw)

    monkeypatch.setattr(feed.json, "dumps", dumps)

    def build():
        feed.create_activity_feed_tab()  # a slot that can't be serialised is skipped

    async def check(user):
        assert "data-json" not in feed._host.slots["meta"].props
        assert "data-json" in feed._host.slots["live"].props

    run_ui(build, check)


def test_create_tab_without_context_logs(monkeypatch, isolated, tab_env, caplog):
    import nicegui

    real_context = nicegui.context

    class _Ctx:
        """Raises for the listener lookup only; element creation keeps the real slot stack."""

        def __getattr__(self, name):
            return getattr(real_context, name)

        @property
        def client(self):
            raise RuntimeError("no client yet")

    def build():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(nicegui, "context", _Ctx())
            feed.create_activity_feed_tab()

    run_ui(build)
    assert "could not listen for feed actions" in caplog.text


def test_tab_alive_and_live_push_end_to_end(monkeypatch, isolated, tab_env):
    monkeypatch.setattr(feed, "_run_on_ui_loop", lambda fn: fn())
    monkeypatch.setattr(feed, "run_on_ui_loop", lambda fn: fn())

    def build():
        feed.create_activity_feed_tab()

    async def check(user):
        assert feed._tab_alive() is True
        feed.alert_event_handler.process_alert_immediately({"type": "bits", "username": "Bo", "bits": 100})
        isolated.timers[-1][1]()  # debounce fires
        live = json.loads(feed._host.slots["live"].props["data-json"])
        assert live[0]["user"] == "Bo" and live[0]["amount"] == "100 bits"
        feed._set_fallback(True)
        assert "is-fallback" in feed._host.host.classes and "Bo" in feed._host.fallback.content

    run_ui(build, check)


# --------------------------------------------------------------------------- By person profile pictures

PIC = "https://static-cdn.jtvnw.net/jtv_user_pictures/x-profile_image-300x300.png"


def _group(name, *kinds):
    return {"username": name, "items": [{"kind": k} for k in kinds]}


def test_person_avatars_only_for_twitch_viewers(monkeypatch):
    from modules import twitch_avatars

    asked = []
    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {n: PIC for n in names if n == "Bitsy"})
    monkeypatch.setattr(twitch_avatars, "request_avatars", lambda names, on_update=None: asked.append((names, on_update)))
    groups = [_group("Bitsy", "bits"), _group("Raider", "raid", "donation"), _group("Tipper", "donation"),
              _group("Member", "member", "superchat")]
    feed.attach_person_avatars(groups)
    assert groups[0]["avatar"] == PIC
    assert all("avatar" not in g for g in groups[1:])
    assert asked == [(["Bitsy", "Raider"], feed._person_avatars_arrived)]


def test_person_avatars_skip_the_lookup_when_all_are_known(monkeypatch):
    from modules import twitch_avatars

    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {n: PIC for n in names})
    monkeypatch.setattr(twitch_avatars, "request_avatars", lambda *a, **k: pytest.fail("nothing to fetch"))
    groups = [_group("Bitsy", "follow")]
    feed.attach_person_avatars(groups)
    assert groups[0]["avatar"] == PIC
    feed.attach_person_avatars([_group("Tipper", "donation")])  # no Twitch viewers: no lookups at all


def test_person_avatars_arriving_rebuilds_the_view_on_the_ui_loop(monkeypatch):
    hops, rebuilds = [], []
    monkeypatch.setattr(feed, "run_on_ui_loop", lambda fn: hops.append(fn))
    monkeypatch.setattr(feed, "schedule_condensed_view_update", lambda reason: rebuilds.append(reason))
    feed._person_avatars_arrived()
    assert rebuilds == []
    hops[0]()
    assert rebuilds == ["avatars"]


def test_condensed_build_data_carries_avatars(monkeypatch):
    from modules import twitch_avatars

    monkeypatch.setattr(feed, "_person_hours", lambda: 2)
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view",
                        lambda cutoff: ([{"type": "Follow", "username": "Fan"}], 1, 1))
    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {"Fan": PIC})
    data = feed._collect_condensed_build_data()
    assert data["groups"][0]["avatar"] == PIC
    assert feed._condensed_view_model(data)["groups"][0]["avatar"] == PIC


def test_dock_payload_carries_avatars(monkeypatch):
    from modules import twitch_avatars

    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view",
                        lambda cutoff: ([{"type": "Follow", "username": "Fan"}], 1, 1))
    monkeypatch.setattr(twitch_avatars, "cached_avatars", lambda names: {"Fan": PIC})
    payload = feed.build_condensed_overlay_payload(hours=2, filter_state={"all": True}, search="")
    assert payload["groups"][0]["avatar"] == PIC

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.tray_controller (main-process tray orchestration; no real tray, window or child)."""

from __future__ import annotations

import logging
import multiprocessing
import multiprocessing.spawn
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import tray_controller as tc


# --------------------------------------------------------------------------- fixtures / fakes

@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    for name, value in {
        "_process": None,
        "_conn": None,
        "_reader_thread": None,
        "_minimized": False,
        "_window_url": None,
        "_tray_unavailable_reason": None,
        "_native_handlers_registered": False,
        "_database_warning_shown": False,
        "_fallback_applied": False,
        "_quit_requested": False,
    }.items():
        monkeypatch.setattr(tc, name, value)


class RecThread:
    """Records threads instead of starting them."""

    created = []

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target, self.args, self.name, self.daemon = target, args, name, daemon
        self.started = False
        RecThread.created.append(self)

    def start(self):
        self.started = True


@pytest.fixture(autouse=True)
def threads(monkeypatch):
    RecThread.created = []
    monkeypatch.setattr(tc.threading, "Thread", RecThread)
    return RecThread


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(tc.time, "sleep", lambda s: calls.append(s))
    return calls


@pytest.fixture
def fakes(monkeypatch):
    """Fake the modules tray_controller imports lazily."""
    ns = SimpleNamespace(
        suspended=[], flushed=0, ui_loop=[], shutdown_in_progress=False, shutdowns=[], exits=0,
        notifications=[], db_type="sql", autostart_syncs=0, notify_error=None, flush_error=None,
        db_error=None, autostart_error=None, suspend_error=None,
    )

    def mod(name, **attrs):
        m = ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)
        return m

    def set_monitor_suspended(flag):
        if ns.suspend_error:
            raise ns.suspend_error
        ns.suspended.append(flag)

    def flush_pending_prompt():
        if ns.flush_error:
            raise ns.flush_error
        ns.flushed += 1

    def run_on_ui_loop(fn):
        ns.ui_loop.append(fn)
        fn()

    def force_exit():
        ns.exits += 1

    def notify(message, **kw):
        if ns.notify_error:
            raise ns.notify_error
        ns.notifications.append((message, kw))

    def get_database_type():
        if ns.db_error:
            raise ns.db_error
        return ns.db_type

    def sync_with_settings():
        if ns.autostart_error:
            raise ns.autostart_error
        ns.autostart_syncs += 1

    mod("modules.ui_health_monitor", set_monitor_suspended=set_monitor_suspended)
    mod("modules.updater", update_manager=SimpleNamespace(flush_pending_prompt=flush_pending_prompt),
        _force_application_exit=force_exit)
    mod("modules.ui_timer", run_on_ui_loop=run_on_ui_loop)
    mod("modules.shutdown", is_shutdown_in_progress=lambda: ns.shutdown_in_progress,
        shutdown_application=lambda **kw: ns.shutdowns.append(kw))
    mod("modules.notification_engine", notify=notify, nav_actions_settings=lambda tab: [{"nav": tab}])
    mod("modules.config_manager", get_database_type=get_database_type)
    mod("modules.autostart", sync_with_settings=sync_with_settings)
    return ns


class FakeWindow:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise RuntimeError(f"{name} failed")

    def hide(self):
        self._do("hide")

    def show(self):
        self._do("show")

    def restore(self):
        self._do("restore")

    def load_url(self, url):
        self._do("load_url", url)


@pytest.fixture
def pushed(monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "push_window_method", lambda name, *a: calls.append((name, *a)) or True)
    return calls


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "_send_to_tray", lambda payload: calls.append(payload) or True)
    return calls


class FakeConn:
    def __init__(self, incoming=(), send_error=None, close_error=None):
        self.incoming = list(incoming)
        self.sent = []
        self.closed = 0
        self.send_error = send_error
        self.close_error = close_error

    def send(self, payload):
        if self.send_error:
            raise self.send_error
        self.sent.append(payload)

    def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise EOFError

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


class FakeProcess:
    def __init__(self, alive=True, pid=4242, exits_on_join=True, terminate_error=None, name="mycelian-tray"):
        self.alive = alive
        self.pid = pid
        self.name = name
        self.exits_on_join = exits_on_join
        self.terminate_error = terminate_error
        self.calls = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.calls.append(("join", timeout))
        if self.exits_on_join:
            self.alive = False

    def terminate(self):
        self.calls.append(("terminate",))
        if self.terminate_error:
            raise self.terminate_error

    def kill(self):
        self.calls.append(("kill",))
        self.alive = False

    def start(self):
        self.calls.append(("start",))


def install_nicegui(monkeypatch, *, main_window=None, method_queue="unset", config=None, event_manager=None):
    """Fake the parts of nicegui tray_controller reaches into."""
    ng = ModuleType("nicegui")
    ng.app = SimpleNamespace(native=SimpleNamespace(main_window=main_window))
    ng.core = SimpleNamespace(app=SimpleNamespace(config=config))
    native_pkg = ModuleType("nicegui.native")
    native_pkg.native = SimpleNamespace(method_queue=None if method_queue == "unset" else method_queue)
    em_mod = ModuleType("nicegui.native.event_manager")
    em_mod.event_manager = event_manager
    monkeypatch.setitem(sys.modules, "nicegui", ng)
    monkeypatch.setitem(sys.modules, "nicegui.native", native_pkg)
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", em_mod)
    return ng


# --------------------------------------------------------------------------- settings

@pytest.fixture
def app_settings(monkeypatch):
    from modules import dataobjects

    holder = SimpleNamespace(value=SimpleNamespace(minimize_to_tray=False, start_minimized=False))
    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=lambda: holder.value))
    return holder


def test_app_settings_reads_state_manager(app_settings):
    assert tc._app_settings() is app_settings.value


@pytest.mark.parametrize(
    "minimize, start_min, wanted",
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_settings_readers(app_settings, minimize, start_min, wanted):
    app_settings.value = SimpleNamespace(minimize_to_tray=minimize, start_minimized=start_min)
    assert tc.minimize_to_tray_enabled() is minimize
    assert tc.start_minimized_enabled() is start_min
    assert tc.tray_wanted() is wanted


def test_settings_missing_attributes(app_settings):
    app_settings.value = SimpleNamespace()
    assert tc.minimize_to_tray_enabled() is False
    assert tc.start_minimized_enabled() is False


# --------------------------------------------------------------------------- window plumbing

def test_set_and_get_window_url(monkeypatch):
    tc.set_window_url("")
    tc.set_window_url(tc.BLANK_URL)
    assert tc._window_url is None
    tc.set_window_url("http://127.0.0.1:9000/")
    assert tc.get_window_url() == "http://127.0.0.1:9000/"


@pytest.mark.parametrize(
    "config, expected",
    [
        (SimpleNamespace(host="0.0.0.0", port=8080), "http://0.0.0.0:8080/"),
        (SimpleNamespace(host=None, port=8081), "http://127.0.0.1:8081/"),
        (SimpleNamespace(host="h", port=None), None),
        (None, None),
    ],
)
def test_get_window_url_fallback_from_config(monkeypatch, config, expected):
    install_nicegui(monkeypatch, config=config)
    assert tc.get_window_url() == expected


def test_get_window_url_fallback_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "nicegui", None)
    assert tc.get_window_url() is None


def test_main_window(monkeypatch):
    win = FakeWindow()
    install_nicegui(monkeypatch, main_window=win)
    assert tc._main_window() is win
    ng = install_nicegui(monkeypatch)
    ng.app = SimpleNamespace()
    assert tc._main_window() is None
    monkeypatch.setitem(sys.modules, "nicegui", None)
    assert tc._main_window() is None


class FakeQueue:
    def __init__(self, error=None):
        self.items = []
        self.error = error

    def put(self, item):
        if self.error:
            raise self.error
        self.items.append(item)


def test_push_window_method(monkeypatch, caplog):
    q = FakeQueue()
    install_nicegui(monkeypatch, method_queue=q)
    assert tc.push_window_method("hide") is True
    assert tc.push_window_method("mycelian_set_tray_mode", True) is True
    assert q.items == [("hide", (), {}), ("mycelian_set_tray_mode", (True,), {})]

    install_nicegui(monkeypatch)  # no queue: no native mode
    assert tc.push_window_method("hide") is False

    install_nicegui(monkeypatch, method_queue=FakeQueue(error=RuntimeError("closed")))
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        assert tc.push_window_method("hide") is False
    assert "could not push window method hide: closed" in caplog.text


def test_push_helpers(pushed):
    tc.push_tray_mode(1)
    tc.allow_window_close()
    tc.set_dock_visible(0)
    assert pushed == [("mycelian_set_tray_mode", True), ("mycelian_allow_close",),
                      ("mycelian_set_dock_visible", False)]


def test_destroy_native_window_without_window(monkeypatch, pushed):
    monkeypatch.setattr(tc, "_main_window", lambda: None)
    tc.destroy_native_window()
    assert pushed == [("mycelian_force_quit",)]


def test_destroy_native_window_hide_error(monkeypatch, pushed):
    win = FakeWindow(fail={"hide"})
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    tc.destroy_native_window()
    assert win.calls == [("hide",)]
    assert pushed == [("mycelian_force_quit",)]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch, sleeps):
    c = Clock()
    monkeypatch.setattr(tc.time, "monotonic", c)

    def sleep(s):
        sleeps.append(s)
        c.t += s

    monkeypatch.setattr(tc.time, "sleep", sleep)
    return c


def test_wait_for_exit_returns_when_only_tray_left(monkeypatch, clock, sleeps):
    tray = FakeProcess(pid=1)
    monkeypatch.setattr(tc, "_process", tray)
    window = FakeProcess(pid=2, name="webview")
    rounds = [[tray, window], [tray, window], [tray]]
    monkeypatch.setattr(multiprocessing, "active_children", lambda: rounds.pop(0) if rounds else [tray])
    tc._wait_for_native_window_exit(timeout=1.0)
    assert sleeps == [0.05, 0.05]
    assert window.calls == []


def test_wait_for_exit_kills_stubborn_children(monkeypatch, clock, caplog):
    tray = FakeProcess(pid=1)
    monkeypatch.setattr(tc, "_process", tray)
    stubborn = FakeProcess(pid=2, name="webview", exits_on_join=False)
    polite = FakeProcess(pid=3, name="other", exits_on_join=True)
    broken = FakeProcess(pid=4, name="broken", terminate_error=OSError("gone"))
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [tray, stubborn, polite, broken])
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        tc._wait_for_native_window_exit(timeout=0.1)
    assert stubborn.calls == [("terminate",), ("join", 1.0), ("kill",), ("join", 0.5)]
    assert polite.calls == [("terminate",), ("join", 1.0)]
    assert broken.calls == [("terminate",)]
    assert tray.calls == []
    assert "terminating window process webview" in caplog.text
    assert "could not terminate window process broken: gone" in caplog.text


def test_wait_for_exit_without_tray_process(monkeypatch, clock):
    child = FakeProcess(pid=None, name="webview")
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [])
    tc._wait_for_native_window_exit()
    assert child.calls == []


# --------------------------------------------------------------------------- minimize / restore

def test_is_minimized(monkeypatch):
    assert tc.is_minimized() is False
    monkeypatch.setattr(tc, "_minimized", True)
    assert tc.is_minimized() is True


def test_minimize_refuses_without_tray(monkeypatch, pushed, caplog):
    win = FakeWindow()
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "is_tray_running", lambda: False)
    monkeypatch.setattr(tc, "start_tray", lambda: False)
    with caplog.at_level(logging.WARNING, logger=tc.logger.name):
        assert tc.minimize_to_tray() is False
    assert "refusing to minimize without a tray icon" in caplog.text
    assert win.calls == [] and tc._minimized is False


def test_minimize_starts_tray_and_hides(monkeypatch, pushed, sent, fakes):
    win = FakeWindow()
    started = []
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "is_tray_running", lambda: False)
    monkeypatch.setattr(tc, "start_tray", lambda: started.append(1) or True)
    assert tc.minimize_to_tray(reason="x") is True
    assert started == [1]
    assert pushed == [("mycelian_set_dock_visible", False), ("mycelian_prepare_for_blank",)]
    assert win.calls == [("hide",), ("load_url", tc.BLANK_URL)]
    assert fakes.suspended == [True]
    assert sent == [{"cmd": "set_state", "minimized": True}]
    assert tc._minimized is True


def test_minimize_failure_keeps_state(monkeypatch, pushed, sent, caplog):
    win = FakeWindow(fail={"load_url"})
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "is_tray_running", lambda: True)
    with caplog.at_level(logging.ERROR, logger=tc.logger.name):
        assert tc.minimize_to_tray() is False
    assert tc._minimized is False and sent == []
    assert "failed to minimize window" in caplog.text


def test_restore_without_window_or_url(monkeypatch, caplog):
    monkeypatch.setattr(tc, "_main_window", lambda: None)
    assert tc.restore_from_tray() is False
    monkeypatch.setattr(tc, "_main_window", lambda: FakeWindow())
    monkeypatch.setattr(tc, "get_window_url", lambda: None)
    with caplog.at_level(logging.ERROR, logger=tc.logger.name):
        assert tc.restore_from_tray() is False
    assert "application URL unknown" in caplog.text


@pytest.mark.parametrize("was_minimized", [True, False])
def test_restore_success(monkeypatch, pushed, sent, fakes, was_minimized):
    win = FakeWindow()
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "_window_url", "http://u/")
    monkeypatch.setattr(tc, "_minimized", was_minimized)
    assert tc.restore_from_tray(reason="r") is True
    expected = ([("load_url", "http://u/")] if was_minimized else []) + [("show",), ("restore",)]
    assert win.calls == expected
    assert pushed == [("mycelian_set_dock_visible", True)]
    assert fakes.suspended == [False] and fakes.flushed == 1
    assert sent == [{"cmd": "set_state", "minimized": False}]
    assert tc._minimized is False


def test_restore_failure(monkeypatch, pushed, sent, fakes):
    win = FakeWindow(fail={"show"})
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "_window_url", "http://u/")
    monkeypatch.setattr(tc, "_minimized", True)
    assert tc.restore_from_tray() is False
    assert tc._minimized is True and sent == [] and fakes.flushed == 0


def test_paint_delay_restore_falls_back_without_window(monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "_main_window", lambda: None)
    monkeypatch.setattr(tc, "get_window_url", lambda: "http://u/")
    monkeypatch.setattr(tc, "restore_from_tray", lambda *, reason: calls.append(reason))
    tc._restore_with_paint_delay()
    monkeypatch.setattr(tc, "_main_window", lambda: FakeWindow())
    monkeypatch.setattr(tc, "get_window_url", lambda: None)
    tc._restore_with_paint_delay()
    assert calls == ["tray_menu", "tray_menu"]


def test_paint_delay_restore_from_minimized(monkeypatch, pushed, sent, fakes, sleeps):
    win = FakeWindow()
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "_window_url", "http://u/")
    monkeypatch.setattr(tc, "_minimized", True)
    tc._restore_with_paint_delay()
    assert win.calls == [("load_url", "http://u/"), ("show",), ("restore",)]
    assert sleeps == [tc._RESTORE_PAINT_DELAY_SEC]
    assert tc._minimized is False
    assert fakes.suspended == [False] and fakes.flushed == 1
    assert sent == [{"cmd": "set_state", "minimized": False}]


def test_paint_delay_restore_visible_window_no_delay(monkeypatch, pushed, sent, fakes, sleeps):
    win = FakeWindow()
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "_window_url", "http://u/")
    tc._restore_with_paint_delay()
    assert win.calls == [("show",), ("restore",)]
    assert sleeps == []


def test_paint_delay_restore_reload_error(monkeypatch, pushed, sent, fakes):
    win = FakeWindow(fail={"load_url"})
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "_window_url", "http://u/")
    monkeypatch.setattr(tc, "_minimized", True)
    tc._restore_with_paint_delay()
    assert tc._minimized is True and sent == []


def test_paint_delay_restore_show_error(monkeypatch, pushed, sent, fakes):
    win = FakeWindow(fail={"show"})
    monkeypatch.setattr(tc, "_main_window", lambda: win)
    monkeypatch.setattr(tc, "_window_url", "http://u/")
    tc._restore_with_paint_delay()
    assert sent == [] and fakes.flushed == 0


def test_suspend_ui_health_monitor(fakes, caplog):
    tc._suspend_ui_health_monitor(True)
    assert fakes.suspended == [True]
    fakes.suspend_error = RuntimeError("nope")
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        tc._suspend_ui_health_monitor(False)
    assert "suspend toggle failed: nope" in caplog.text


def test_flush_pending_update_prompt(fakes, caplog):
    tc._flush_pending_update_prompt()
    assert fakes.flushed == 1 and len(fakes.ui_loop) == 1
    fakes.flush_error = RuntimeError("no prompt")
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        tc._flush_pending_update_prompt()
    assert "no pending update prompt to flush: no prompt" in caplog.text


# --------------------------------------------------------------------------- close / quit

def test_close_request_minimizes_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "minimize_to_tray_enabled", lambda: True)
    monkeypatch.setattr(tc, "minimize_to_tray", lambda *, reason: calls.append(reason) or True)
    monkeypatch.setattr(tc, "quit_application", lambda *, reason: calls.append(("quit", reason)))
    tc.handle_close_request()
    assert calls == ["window_close"]


def test_quit_application_starts_worker(fakes, threads):
    tc.quit_application(reason="menu")
    assert tc._quit_requested is True
    (t,) = threads.created
    assert t.target is tc._quit_worker and t.args == ("menu",) and t.started and t.daemon
    tc.quit_application(reason="again")  # already requested
    assert len(threads.created) == 1


def test_quit_application_noop_during_shutdown(fakes, threads):
    fakes.shutdown_in_progress = True
    tc.quit_application(reason="menu")
    assert tc._quit_requested is False and threads.created == []


def test_quit_worker_order_and_forced_exit_on_error(monkeypatch, fakes):
    calls = []
    monkeypatch.setattr(tc, "destroy_native_window", lambda: calls.append("destroy"))
    monkeypatch.setattr(tc, "_wait_for_native_window_exit", lambda: calls.append("wait"))
    monkeypatch.setattr(tc, "stop_tray", lambda: calls.append("stop"))

    def boom(**kw):
        calls.append(("shutdown", kw))
        raise RuntimeError("teardown failed")

    sys.modules["modules.shutdown"].shutdown_application = boom
    with pytest.raises(RuntimeError):
        tc._quit_worker("menu")
    assert calls == ["destroy", "wait", "stop", ("shutdown", {"reason": "tray_menu", "force": False})]
    assert fakes.exits == 1


class FakeEventManager:
    def __init__(self):
        self.handlers = {}

    def on(self, name, handler):
        self.handlers[name] = handler


def test_register_native_event_handlers(monkeypatch, pushed):
    em = FakeEventManager()
    install_nicegui(monkeypatch, event_manager=em)
    tc.register_native_event_handlers()
    assert set(em.handlers) == {"mycelian_window_ready", "mycelian_close_requested", "mycelian_quit_requested",
                                "minimized", "mycelian_window_shown", "shown", "restored"}
    assert tc._native_handlers_registered is True
    em2 = FakeEventManager()
    install_nicegui(monkeypatch, event_manager=em2)
    tc.register_native_event_handlers()  # already registered
    assert em2.handlers == {}


def test_native_event_handlers_behaviour(monkeypatch, pushed):
    em = FakeEventManager()
    install_nicegui(monkeypatch, event_manager=em)
    calls = []
    state = SimpleNamespace(enabled=True, minimized=False)
    monkeypatch.setattr(tc, "minimize_to_tray_enabled", lambda: state.enabled)
    monkeypatch.setattr(tc, "is_minimized", lambda: state.minimized)
    monkeypatch.setattr(tc, "handle_close_request", lambda: calls.append("close"))
    monkeypatch.setattr(tc, "quit_application", lambda *, reason: calls.append(("quit", reason)))
    monkeypatch.setattr(tc, "minimize_to_tray", lambda *, reason: calls.append(("min", reason)))
    monkeypatch.setattr(tc, "restore_from_tray", lambda *, reason: calls.append(("restore", reason)))
    tc.register_native_event_handlers()
    h = em.handlers

    h["mycelian_window_ready"](SimpleNamespace(args={"url": "http://x/"}))
    assert tc._window_url == "http://x/"
    h["mycelian_window_ready"](SimpleNamespace(args=None))
    h["mycelian_window_ready"](SimpleNamespace())
    assert pushed == [("mycelian_set_tray_mode", True)] * 3

    h["mycelian_close_requested"](None)
    h["mycelian_quit_requested"](None)
    h["minimized"](None)
    state.minimized = True
    h["minimized"](None)  # already minimized: ignored
    state.enabled = False
    state.minimized = False
    h["minimized"](None)  # tray mode off: ignored
    h["shown"](None)  # not minimized: ignored
    state.minimized = True
    h["mycelian_window_shown"](None)
    h["restored"](None)
    assert calls == ["close", ("quit", "app_quit"), ("min", "window_minimize"),
                     ("restore", "window_shown"), ("restore", "window_shown")]


def test_register_handlers_without_event_manager(monkeypatch, caplog):
    install_nicegui(monkeypatch)
    monkeypatch.setitem(sys.modules, "nicegui.native.event_manager", None)
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        tc.register_native_event_handlers()
    assert tc._native_handlers_registered is False
    assert "event_manager unavailable" in caplog.text


# --------------------------------------------------------------------------- tray child process

def test_tray_icon_path(monkeypatch, tmp_path, caplog):
    from modules import path_utils

    assets = tmp_path / "assets"
    monkeypatch.setattr(path_utils, "get_assets_path", lambda rel="": str(assets / rel))
    with caplog.at_level(logging.ERROR, logger=tc.logger.name):
        assert tc._tray_icon_path() is None
    assert "no tray icon asset found" in caplog.text
    icons = assets / "default_assets" / "icons"
    icons.mkdir(parents=True)
    (icons / "Mycelian.ico").write_bytes(b"")
    assert tc._tray_icon_path() == str(icons / "Mycelian.ico")
    (icons / "Mycelian.png").write_bytes(b"")
    assert tc._tray_icon_path() == str(icons / "Mycelian.png")


def test_is_tray_running(monkeypatch):
    assert tc.is_tray_running() is False
    monkeypatch.setattr(tc, "_process", FakeProcess(alive=False))
    assert tc.is_tray_running() is False
    monkeypatch.setattr(tc, "_process", FakeProcess(alive=True))
    assert tc.is_tray_running() is True


def test_strip_main_module():
    def prep(name):
        return {"name": name, "init_main_from_name": "__main__", "init_main_from_path": "/main.py", "sys_path": []}

    patched = tc._strip_main_module(prep)
    assert patched("x") == {"name": "x", "sys_path": []}
    assert tc._strip_main_module(lambda n: {})("y") == {}


class FakeContext:
    def __init__(self, pipe_error=None, start_error=None):
        self.pipe_error = pipe_error
        self.start_error = start_error
        self.parent = FakeConn()
        self.child = FakeConn()
        self.process = None
        self.prep_during_start = None

    def Pipe(self, duplex):
        assert duplex is True
        if self.pipe_error:
            raise self.pipe_error
        return self.parent, self.child

    def Process(self, target, args, name, daemon):
        ctx = self
        proc = FakeProcess()
        proc.target, proc.args, proc.name, proc.daemon = target, args, name, daemon

        def start():
            ctx.prep_during_start = multiprocessing.spawn.get_preparation_data
            if ctx.start_error:
                raise ctx.start_error

        proc.start = start
        self.process = proc
        return proc


@pytest.fixture
def icon_ok(monkeypatch):
    monkeypatch.setattr(tc, "_tray_icon_path", lambda: "/icons/Mycelian.png")


def test_start_tray_already_running(monkeypatch):
    monkeypatch.setattr(tc, "_process", FakeProcess())
    monkeypatch.setattr(tc, "_tray_icon_path", lambda: pytest.fail("not needed"))
    assert tc.start_tray() is True


def test_start_tray_without_icon(monkeypatch):
    monkeypatch.setattr(tc, "_tray_icon_path", lambda: None)
    assert tc.start_tray() is False
    assert tc._tray_unavailable_reason == "tray icon asset missing"


def test_start_tray_success(monkeypatch, icon_ok, pushed, threads):
    from modules import tray_process

    ctx = FakeContext()
    monkeypatch.setattr(tc, "_SPAWN_CONTEXT", ctx)
    monkeypatch.setattr(tc, "minimize_to_tray_enabled", lambda: True)
    monkeypatch.setattr(tc, "_fallback_applied", True)
    monkeypatch.setattr(tc, "_tray_unavailable_reason", "old")
    monkeypatch.setattr(tc, "_minimized", True)
    original_prep = multiprocessing.spawn.get_preparation_data
    assert tc.start_tray() is True
    proc = ctx.process
    assert proc.target is tray_process.run_tray
    assert proc.args == (ctx.child, "/icons/Mycelian.png", True)
    assert proc.name == "mycelian-tray" and proc.daemon is True
    assert ctx.prep_during_start is not original_prep
    assert multiprocessing.spawn.get_preparation_data is original_prep
    assert ctx.child.closed == 1
    assert tc._process is proc and tc._conn is ctx.parent
    assert tc._tray_unavailable_reason is None and tc._fallback_applied is False
    (reader,) = threads.created
    assert reader.target is tc._read_from_tray and reader.args == (ctx.parent,) and reader.started
    assert tc._reader_thread is reader
    assert pushed == [("mycelian_set_tray_mode", True)]


def test_start_tray_spawn_failure_restores_prep(monkeypatch, icon_ok, threads, caplog):
    ctx = FakeContext(start_error=OSError("spawn failed"))
    monkeypatch.setattr(tc, "_SPAWN_CONTEXT", ctx)
    original_prep = multiprocessing.spawn.get_preparation_data
    with caplog.at_level(logging.ERROR, logger=tc.logger.name):
        assert tc.start_tray() is False
    assert multiprocessing.spawn.get_preparation_data is original_prep
    assert tc._tray_unavailable_reason == "spawn failed"
    assert tc._process is None and threads.created == []
    assert "could not start the tray process" in caplog.text


def test_start_tray_pipe_failure(monkeypatch, icon_ok):
    monkeypatch.setattr(tc, "_SPAWN_CONTEXT", FakeContext(pipe_error=OSError("no fds")))
    assert tc.start_tray() is False
    assert tc._tray_unavailable_reason == "no fds"


def test_stop_tray_nothing_running():
    tc.stop_tray()
    assert tc._process is None and tc._conn is None


def test_stop_tray_clean_exit(monkeypatch):
    proc, conn = FakeProcess(), FakeConn()
    monkeypatch.setattr(tc, "_process", proc)
    monkeypatch.setattr(tc, "_conn", conn)
    monkeypatch.setattr(tc, "_reader_thread", object())
    tc.stop_tray()
    assert conn.sent == [{"cmd": "stop"}] and conn.closed == 1
    assert proc.calls == [("join", 2.0)]
    assert tc._process is None and tc._conn is None and tc._reader_thread is None


def test_stop_tray_terminates_stuck_child(monkeypatch, caplog):
    proc = FakeProcess(exits_on_join=False)
    conn = FakeConn(send_error=BrokenPipeError(), close_error=OSError())
    monkeypatch.setattr(tc, "_process", proc)
    monkeypatch.setattr(tc, "_conn", conn)
    with caplog.at_level(logging.WARNING, logger=tc.logger.name):
        tc.stop_tray()
    assert proc.calls == [("join", 2.0), ("terminate",), ("join", 1.0)]
    assert "did not exit; terminating" in caplog.text


def test_stop_tray_terminate_error_and_dead_process(monkeypatch):
    proc = FakeProcess(exits_on_join=False, terminate_error=OSError("denied"))
    monkeypatch.setattr(tc, "_process", proc)
    tc.stop_tray()
    assert proc.calls == [("join", 2.0), ("terminate",)]
    dead = FakeProcess(alive=False)
    monkeypatch.setattr(tc, "_process", dead)
    tc.stop_tray()
    assert dead.calls == []


def test_send_to_tray(monkeypatch, caplog):
    assert tc._send_to_tray({"a": 1}) is False  # no conn
    conn = FakeConn()
    monkeypatch.setattr(tc, "_conn", conn)
    assert tc._send_to_tray({"a": 1}) is False  # no process
    monkeypatch.setattr(tc, "_process", FakeProcess())
    assert tc._send_to_tray({"a": 1}) is True
    assert conn.sent == [{"a": 1}]
    conn.send_error = OSError("broken")
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        assert tc._send_to_tray({"a": 2}) is False
    assert "send failed: broken" in caplog.text


def test_notify_and_update_payloads(sent):
    assert tc.notify_via_tray("T", "M") is True
    assert tc.set_tray_update_available(1) is True
    assert sent == [{"cmd": "notify", "title": "T", "message": "M"}, {"cmd": "set_update", "available": True}]


def test_read_from_tray_dispatches_actions(monkeypatch, threads, caplog):
    quits, fallbacks = [], []
    monkeypatch.setattr(tc, "quit_application", lambda *, reason: quits.append(reason))
    monkeypatch.setattr(tc, "_fall_back_to_plain_window", lambda r: fallbacks.append(r))
    conn = FakeConn(["junk", {"action": "ready"}, {"action": "restore"}, {"action": "quit"}, {"action": "??"}])
    with caplog.at_level(logging.WARNING, logger=tc.logger.name):
        tc._read_from_tray(conn)
    assert "icon is visible" in caplog.text
    (restore,) = threads.created
    assert restore.target is tc._restore_with_paint_delay and restore.started
    assert quits == ["menu"]
    assert fallbacks == []  # _conn is not this conn


def test_read_from_tray_unexpected_close_falls_back(monkeypatch):
    fallbacks = []
    monkeypatch.setattr(tc, "_fall_back_to_plain_window", lambda r: fallbacks.append(r))
    conn = FakeConn()
    monkeypatch.setattr(tc, "_conn", conn)
    tc._read_from_tray(conn)
    assert fallbacks == ["the tray icon stopped unexpectedly"]


def test_read_from_tray_unavailable(monkeypatch):
    fallbacks = []
    monkeypatch.setattr(tc, "_fall_back_to_plain_window", lambda r: fallbacks.append(r))
    conn = FakeConn([{"action": "unavailable", "error": "no tray"}, {"action": "quit"}])
    tc._read_from_tray(conn)
    assert tc._tray_unavailable_reason == "no tray"
    assert fallbacks == ["no tray"]
    assert len(conn.incoming) == 1  # stopped reading
    conn2 = FakeConn([{"action": "unavailable"}])
    tc._read_from_tray(conn2)
    assert tc._tray_unavailable_reason == "unknown"


def test_fallback_skipped_during_shutdown(fakes, pushed):
    fakes.shutdown_in_progress = True
    tc._fall_back_to_plain_window("x")
    assert tc._fallback_applied is False and pushed == []


def test_fallback_applies_once_and_notifies(fakes, pushed):
    tc._fall_back_to_plain_window("tray crashed")
    assert tc._fallback_applied is True
    assert pushed == [("mycelian_set_tray_mode", False), ("mycelian_set_dock_visible", True)]
    (message, kw), = fakes.notifications
    assert "normal window" in message
    assert kw["type"] == "warning" and kw["details"] == "tray crashed"
    assert kw["dedupe_key"] == "app:tray_unavailable" and kw["actions"] == [{"nav": "App Settings"}]
    tc._fall_back_to_plain_window("again")
    assert len(fakes.notifications) == 1


def test_fallback_restores_minimized_window_and_survives_notify_error(monkeypatch, fakes, pushed):
    restored = []
    monkeypatch.setattr(tc, "_minimized", True)
    monkeypatch.setattr(tc, "restore_from_tray", lambda *, reason: restored.append(reason))
    fakes.notify_error = RuntimeError("no ui")
    tc._fall_back_to_plain_window("x")
    assert restored == ["tray_unavailable"]
    assert pushed == [("mycelian_set_tray_mode", False)]


# --------------------------------------------------------------------------- orchestration

@pytest.fixture
def orchestration(monkeypatch, pushed):
    ns = SimpleNamespace(wanted=False, minimize=False, start_min=False, running=False, start_ok=True,
                         calls=[])
    monkeypatch.setattr(tc, "tray_wanted", lambda: ns.wanted)
    monkeypatch.setattr(tc, "minimize_to_tray_enabled", lambda: ns.minimize)
    monkeypatch.setattr(tc, "start_minimized_enabled", lambda: ns.start_min)
    monkeypatch.setattr(tc, "is_tray_running", lambda: ns.running)
    monkeypatch.setattr(tc, "start_tray", lambda: ns.calls.append("start") or ns.start_ok)
    monkeypatch.setattr(tc, "stop_tray", lambda: ns.calls.append("stop"))
    monkeypatch.setattr(tc, "restore_from_tray", lambda *, reason: ns.calls.append(("restore", reason)))
    monkeypatch.setattr(tc, "_mark_started_minimized", lambda: ns.calls.append("mark_min"))
    monkeypatch.setattr(tc, "warn_background_database_usage", lambda: ns.calls.append("warn"))
    monkeypatch.setattr(tc, "register_native_event_handlers", lambda: ns.calls.append("register"))
    ns.pushed = pushed
    return ns


def test_apply_settings_wanted_starts(orchestration):
    orchestration.wanted = orchestration.minimize = True
    tc.apply_settings()
    assert orchestration.calls == ["start"]
    assert orchestration.pushed == [("mycelian_set_tray_mode", True)]


def test_apply_settings_unwanted_restores_then_stops(orchestration, monkeypatch):
    orchestration.running = True
    monkeypatch.setattr(tc, "_minimized", True)
    tc.apply_settings()
    assert orchestration.calls == [("restore", "tray_disabled"), "stop"]
    assert orchestration.pushed == [("mycelian_set_tray_mode", False)]


def test_apply_settings_unwanted_visible_window(orchestration):
    orchestration.running = True
    tc.apply_settings()
    assert orchestration.calls == ["stop"]
    orchestration.calls.clear()
    orchestration.running = False
    tc.apply_settings()
    assert orchestration.calls == []


def test_initialize_nothing_wanted(orchestration, fakes):
    tc.initialize()
    assert orchestration.calls == ["register"]
    assert fakes.autostart_syncs == 1


def test_initialize_start_minimized_with_tray(orchestration, fakes):
    orchestration.wanted = orchestration.start_min = True
    tc.initialize()
    assert orchestration.calls == ["register", "start", "mark_min", "warn"]


def test_initialize_start_minimized_tray_already_running(orchestration, fakes):
    orchestration.start_min = True
    orchestration.running = True
    tc.initialize()
    assert orchestration.calls == ["register", "mark_min"]


def test_initialize_start_minimized_tray_failed(orchestration, fakes, caplog):
    orchestration.wanted = orchestration.start_min = True
    orchestration.start_ok = False
    fakes.autostart_error = RuntimeError("sync failed")
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        tc.initialize()
    assert orchestration.calls == ["register", "start", ("restore", "start_minimized_no_tray"), "warn"]
    assert tc._minimized is True
    assert "start_minimized requested but the tray icon failed" in caplog.text
    assert "autostart sync failed: sync failed" in caplog.text


def test_mark_started_minimized(monkeypatch, pushed, sent, fakes, caplog):
    with caplog.at_level(logging.INFO, logger=tc.logger.name):
        tc._mark_started_minimized()
    assert tc._minimized is True
    assert pushed == [("mycelian_set_dock_visible", False)]
    assert fakes.suspended == [True]
    assert sent == [{"cmd": "set_state", "minimized": True}]
    assert "started minimized" in caplog.text


def test_shutdown_stops_tray(monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "stop_tray", lambda: calls.append(1))
    tc.shutdown()
    assert calls == [1]


# --------------------------------------------------------------------------- firebase warning

def test_warn_once_for_firebase(fakes):
    fakes.db_type = "firebase"
    tc.warn_background_database_usage()
    tc.warn_background_database_usage()
    (message, kw), = fakes.notifications
    assert message == tc._FIREBASE_BANDWIDTH_WARNING
    assert kw["source"] == "database" and kw["dedupe_key"] == "database:firebase_background"
    assert kw["actions"] == [{"nav": "Database"}]
    assert tc._database_warning_shown is True


def test_warn_skipped_for_local_db_and_config_errors(fakes):
    tc.warn_background_database_usage()
    fakes.db_error = RuntimeError("config")
    tc.warn_background_database_usage()
    assert fakes.notifications == [] and tc._database_warning_shown is False


def test_warn_notify_error_logged(fakes, caplog):
    fakes.db_type = "firebase"
    fakes.notify_error = RuntimeError("no engine")
    with caplog.at_level(logging.DEBUG, logger=tc.logger.name):
        tc.warn_background_database_usage()
    assert tc._database_warning_shown is True
    assert "could not show the Firebase bandwidth warning: no engine" in caplog.text


# --------------------------------------------------------------------------- remaining edge branches

def test_settings_readers_survive_errors(monkeypatch):
    def boom():
        raise RuntimeError("not ready")

    monkeypatch.setattr(tc, "_app_settings", boom)
    assert tc.minimize_to_tray_enabled() is False
    assert tc.start_minimized_enabled() is False


def test_minimize_already_minimized_and_no_window(monkeypatch, caplog):
    monkeypatch.setattr(tc, "_minimized", True)
    monkeypatch.setattr(tc, "_main_window", lambda: pytest.fail("not looked up"))
    assert tc.minimize_to_tray() is True
    monkeypatch.setattr(tc, "_minimized", False)
    monkeypatch.setattr(tc, "_main_window", lambda: None)
    with caplog.at_level(logging.WARNING, logger=tc.logger.name):
        assert tc.minimize_to_tray() is False
    assert "no native window to minimize" in caplog.text


def test_close_request_quits_when_minimize_fails(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(tc, "minimize_to_tray_enabled", lambda: True)
    monkeypatch.setattr(tc, "minimize_to_tray", lambda *, reason: False)
    monkeypatch.setattr(tc, "quit_application", lambda *, reason: calls.append(reason))
    with caplog.at_level(logging.WARNING, logger=tc.logger.name):
        tc.handle_close_request()
    assert calls == ["window_close"]
    assert "minimize failed on close; quitting instead" in caplog.text

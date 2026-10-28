# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.native_window_bridge (webview-child patches, close veto, help window).

pywebview, NiceGUI's native_mode, AppKit and timers are all faked; no window is created.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import native_window_bridge as nwb


# --------------------------------------------------------------------------- fakes

class FakeEvent:
    def __init__(self, fail=False):
        self.handlers = []
        self._items = self.handlers
        self.fail = fail

    def __iadd__(self, handler):
        if self.fail:
            raise RuntimeError("cannot subscribe")
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self, fail=(), event_fail=(), missing_events=()):
        self.calls = []
        self.fail = set(fail)
        names = ("closing", "closed", "shown", "restored", "resized", "moved", "loaded")
        self.events = SimpleNamespace(**{n: FakeEvent(n in event_fail) for n in names if n not in missing_events})

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

    def destroy(self):
        self._do("destroy")

    def load_url(self, url):
        self._do("load_url", url)


class FakeSender:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def send(self, payload):
        if self.error:
            raise self.error
        self.sent.append(payload)


class FakeTimer:
    instances = []

    def __init__(self, delay, fn):
        self.delay, self.fn = delay, fn
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(nwb, "_tray_mode", False)
    monkeypatch.setattr(nwb, "_allow_close", False)
    monkeypatch.setattr(nwb, "_event_sender", None)
    monkeypatch.setattr(nwb, "_app_url", "")
    monkeypatch.setattr(nwb, "_help_window", None)
    monkeypatch.setattr(nwb, "_help_geometry", {})
    monkeypatch.setattr(nwb, "_help_geometry_timer", None)
    monkeypatch.delenv(nwb._MYCELIAN_NATIVE_ENV, raising=False)
    monkeypatch.delenv(nwb._MYCELIAN_TRAY_MODE_ENV, raising=False)
    FakeTimer.instances = []
    monkeypatch.setattr(threading, "Timer", FakeTimer)


@pytest.fixture
def sender(monkeypatch):
    s = FakeSender()
    monkeypatch.setattr(nwb, "_event_sender", s)
    return s


def events(sender):
    return [(p["type"], p["args"]) for p in sender.sent]


@pytest.fixture
def fake_webview(monkeypatch):
    created = []

    def create_window(**kwargs):
        w = FakeWindow()
        w.kwargs = kwargs
        created.append(w)
        return w

    mod = ModuleType("webview")
    mod.create_window = create_window
    mod.screens = [SimpleNamespace(x=0, y=0, width=1920, height=1080)]
    mod.created = created
    monkeypatch.setitem(sys.modules, "webview", mod)
    return mod


@pytest.fixture
def fake_nicegui(monkeypatch):
    ng = ModuleType("nicegui")
    native_pkg = ModuleType("nicegui.native")
    nm = ModuleType("nicegui.native.native_mode")
    calls = []

    def _open_window(protocol, host, port, title, width, height, fullscreen, frameless,
                     method_queue, response_queue, event_sender=None, favicon=None):
        calls.append(dict(protocol=protocol, host=host, port=port, event_sender=event_sender))
        return "opened"

    nm._open_window = _open_window
    nm.calls = calls
    native_pkg.native_mode = nm
    ng.native = native_pkg
    ng.core = SimpleNamespace(app=SimpleNamespace(native=SimpleNamespace(window_args={})))
    ng.helpers = SimpleNamespace(format_url=lambda proto, host, port: f"{proto}://{host}:{port}")
    for name, m in {"nicegui": ng, "nicegui.native": native_pkg, "nicegui.native.native_mode": nm}.items():
        monkeypatch.setitem(sys.modules, name, m)
    return SimpleNamespace(ng=ng, nm=nm, window_args=ng.core.app.native.window_args)


@pytest.fixture
def fake_appkit(monkeypatch):
    app_calls = []
    after = []
    shared = SimpleNamespace(
        setActivationPolicy_=lambda p: app_calls.append(("policy", p)),
        activateIgnoringOtherApps_=lambda f: app_calls.append(("activate", f)),
    )
    appkit = ModuleType("AppKit")
    appkit.NSApplication = SimpleNamespace(sharedApplication=lambda: shared)
    tools = ModuleType("PyObjCTools")
    tools.AppHelper = SimpleNamespace(callAfter=lambda fn: after.append(fn))
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "PyObjCTools", tools)
    monkeypatch.setattr(nwb.sys, "platform", "darwin")
    return SimpleNamespace(app_calls=app_calls, after=after)


# --------------------------------------------------------------------------- env merge

def test_merge_window_args_noop_for_empty_env(fake_nicegui, monkeypatch):
    nwb._merge_window_args_from_env()
    monkeypatch.setenv(nwb._MYCELIAN_NATIVE_ENV, "   ")
    nwb._merge_window_args_from_env()
    assert fake_nicegui.window_args == {}


def test_merge_window_args_invalid_json(fake_nicegui, monkeypatch, caplog):
    monkeypatch.setenv(nwb._MYCELIAN_NATIVE_ENV, "{nope")
    with caplog.at_level(logging.WARNING, logger=nwb.logger.name):
        nwb._merge_window_args_from_env()
    assert fake_nicegui.window_args == {}
    assert "Invalid MYCELIAN_NATIVE_WINDOW_ARGS JSON" in caplog.text


def test_merge_window_args_non_dict(fake_nicegui, monkeypatch):
    monkeypatch.setenv(nwb._MYCELIAN_NATIVE_ENV, "[1, 2]")
    nwb._merge_window_args_from_env()
    assert fake_nicegui.window_args == {}


def test_merge_window_args_converts_min_size(fake_nicegui, monkeypatch):
    monkeypatch.setenv(nwb._MYCELIAN_NATIVE_ENV, json.dumps({"min_size": [800, 600], "x": 5, "title": "M"}))
    nwb._merge_window_args_from_env()
    assert fake_nicegui.window_args == {"min_size": (800, 600), "x": 5, "title": "M"}


# --------------------------------------------------------------------------- native events / close veto

def test_send_native_event(sender):
    nwb._send_native_event("evt", a=1)
    assert sender.sent == [{"type": "evt", "args": {"a": 1}}]


def test_send_native_event_without_sender():
    nwb._send_native_event("evt")  # no sender: no error


@pytest.mark.parametrize("err", [OSError("x"), BrokenPipeError(), ValueError("closed")])
def test_send_native_event_swallows_pipe_errors(monkeypatch, err):
    monkeypatch.setattr(nwb, "_event_sender", FakeSender(error=err))
    nwb._send_native_event("evt")


def applicationShouldTerminate_():  # noqa: N802 - mimics the cocoa delegate frame name
    return nwb._on_window_closing()


def test_is_app_quit_request_detects_delegate_frame():
    assert nwb._is_app_quit_request() is False

    def applicationShouldTerminate_():  # noqa: N802
        return nwb._is_app_quit_request()

    assert applicationShouldTerminate_() is True


def test_is_app_quit_request_frame_error(monkeypatch):
    def boom(depth=0):
        raise ValueError("no frames")

    with monkeypatch.context() as m:
        m.setattr(nwb.sys, "_getframe", boom)
        result = nwb._is_app_quit_request()
    assert result is False


def test_closing_allowed_when_tray_mode_off(sender):
    assert nwb._on_window_closing() is None
    assert sender.sent == []


def test_closing_allowed_after_allow_close(monkeypatch, sender):
    monkeypatch.setattr(nwb, "_tray_mode", True)
    monkeypatch.setattr(nwb, "_allow_close", True)
    assert nwb._on_window_closing() is None
    assert sender.sent == []


def test_closing_vetoed_in_tray_mode(monkeypatch, sender):
    monkeypatch.setattr(nwb, "_tray_mode", True)
    assert nwb._on_window_closing() is False
    assert events(sender) == [("mycelian_close_requested", {})]


def test_closing_from_app_quit_passes_through(monkeypatch, sender):
    monkeypatch.setattr(nwb, "_tray_mode", True)
    assert applicationShouldTerminate_() is None
    assert events(sender) == [("mycelian_quit_requested", {})]


# --------------------------------------------------------------------------- Dock visibility

def test_dock_visibility_noop_off_macos(monkeypatch):
    monkeypatch.setattr(nwb.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    nwb._set_macos_dock_visible(True)


@pytest.mark.parametrize("visible, expected", [(True, [("policy", 0), ("activate", True)]), (False, [("policy", 1)])])
def test_dock_visibility_on_main_thread(fake_appkit, visible, expected):
    nwb._set_macos_dock_visible(visible)
    assert fake_appkit.app_calls == []  # deferred to the main thread
    (apply,) = fake_appkit.after
    apply()
    assert fake_appkit.app_calls == expected


def test_dock_visibility_import_error(monkeypatch, caplog):
    monkeypatch.setattr(nwb.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    with caplog.at_level(logging.WARNING, logger=nwb.logger.name):
        nwb._set_macos_dock_visible(False)
    assert "could not change Dock visibility" in caplog.text


# --------------------------------------------------------------------------- ESM warning suppression

def test_suppress_esm_warning_removes_only_nicegui_check():
    def check():
        pass

    check.__module__ = "nicegui.native.native_mode"

    def check_mine():
        pass

    check_mine.__name__ = "check"
    check_mine.__module__ = "modules.other"

    def other():
        pass

    win = FakeWindow()
    win.events.loaded.handlers.extend([check, check_mine, other, object()])
    nwb._suppress_esm_warning(win)
    assert win.events.loaded.handlers[:3] == [check_mine, other, win.events.loaded.handlers[2]]
    assert check not in win.events.loaded.handlers
    assert len(win.events.loaded.handlers) == 3


def test_suppress_esm_warning_without_events():
    nwb._suppress_esm_warning(object())


# --------------------------------------------------------------------------- help window geometry

@pytest.mark.parametrize(
    "geometry, expected",
    [
        ({"x": 100, "y": 100, "width": 800}, True),
        ({"x": -500, "y": 100, "width": 800}, False),  # centre at -100: off screen
        ({"x": 1900, "y": 100}, True),  # width missing -> 1px probe
        ({"x": 100, "y": 1070}, False),  # title bar below the screen
        ({"x": 3000, "y": 0, "width": 100}, False),
    ],
)
def test_help_geometry_on_screen(fake_webview, geometry, expected):
    assert nwb._help_geometry_on_screen(geometry) is expected


def test_help_geometry_on_second_screen(fake_webview):
    fake_webview.screens.append(SimpleNamespace(x=1920, y=0, width=1280, height=1024))
    assert nwb._help_geometry_on_screen({"x": 2000, "y": 50, "width": 600}) is True


def test_help_geometry_errors_are_off_screen(fake_webview, monkeypatch):
    assert nwb._help_geometry_on_screen({"y": 0}) is False  # KeyError
    monkeypatch.setitem(sys.modules, "webview", None)
    assert nwb._help_geometry_on_screen({"x": 0, "y": 0}) is False


def test_flush_help_geometry(sender):
    nwb._flush_help_geometry()
    assert sender.sent == []
    nwb._help_geometry.update(width=900, height=700)
    nwb._flush_help_geometry()
    assert events(sender) == [("mycelian_help_geometry", {"width": 900, "height": 700})]


def test_schedule_help_geometry_debounces(sender):
    nwb._schedule_help_geometry(width=900.7, height=None)
    first = FakeTimer.instances[0]
    assert first.delay == nwb._HELP_GEOMETRY_DEBOUNCE_SEC and first.daemon and first.started
    nwb._schedule_help_geometry(x=10, y=20)
    second = FakeTimer.instances[1]
    assert first.cancelled is True and second.cancelled is False
    assert nwb._help_geometry == {"width": 900, "x": 10, "y": 20}
    assert nwb._help_geometry_timer is second
    second.fn()
    assert events(sender) == [("mycelian_help_geometry", {"width": 900, "x": 10, "y": 20})]


def test_bring_to_front_non_mac(monkeypatch):
    monkeypatch.setattr(nwb.sys, "platform", "linux")
    win = FakeWindow(fail={"restore", "show"})
    nwb._bring_to_front(win)
    assert win.calls == [("restore",), ("show",)]


def test_bring_to_front_mac_activates(fake_appkit):
    win = FakeWindow()
    nwb._bring_to_front(win)
    (fn,) = fake_appkit.after
    fn()
    assert fake_appkit.app_calls == [("activate", True)]


def test_bring_to_front_mac_without_appkit(monkeypatch):
    monkeypatch.setattr(nwb.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    win = FakeWindow()
    nwb._bring_to_front(win)
    assert win.calls == [("restore",), ("show",)]


def test_on_help_closed(monkeypatch, sender):
    timer = FakeTimer(1, None)
    monkeypatch.setattr(nwb, "_help_geometry_timer", timer)
    monkeypatch.setattr(nwb, "_help_window", FakeWindow())
    nwb._help_geometry.update(width=800)
    nwb._on_help_closed()
    assert timer.cancelled and nwb._help_geometry_timer is None and nwb._help_window is None
    assert events(sender) == [("mycelian_help_geometry", {"width": 800}), ("mycelian_help_closed", {})]


def test_on_help_closed_without_timer(sender):
    nwb._on_help_closed()
    assert events(sender) == [("mycelian_help_closed", {})]


# --------------------------------------------------------------------------- open/focus/close help

def test_open_help_window_defaults(fake_webview, monkeypatch):
    monkeypatch.setattr(nwb.sys, "platform", "linux")
    nwb.open_help_window("http://h/help")
    (win,) = fake_webview.created
    assert win.kwargs == {"title": "Mycelian Help", "url": "http://h/help", "width": 1100, "height": 800,
                          "resizable": True, "min_size": (720, 480), "text_select": True}
    assert nwb._help_window is win
    assert nwb._help_geometry == {"width": 1100, "height": 800}
    assert len(win.events.resized.handlers) == 1 and len(win.events.moved.handlers) == 1
    assert win.events.closed.handlers == [nwb._on_help_closed]
    win.events.resized.handlers[0](1000, 900)
    win.events.moved.handlers[0](5, 6)
    assert nwb._help_geometry == {"width": 1000, "height": 900, "x": 5, "y": 6}


def test_open_help_window_with_options(fake_webview):
    nwb.open_help_window("u", {"title": "Docs", "width": 300, "height": 200, "x": 50, "y": 60,
                               "background_color": "#101010"})
    (win,) = fake_webview.created
    assert win.kwargs["title"] == "Docs"
    assert (win.kwargs["width"], win.kwargs["height"]) == (720, 480)  # clamped to the minimum
    assert (win.kwargs["x"], win.kwargs["y"]) == (50, 60)
    assert win.kwargs["background_color"] == "#101010"
    assert nwb._help_geometry == {"width": 720, "height": 480, "x": 50, "y": 60}


def test_open_help_window_offscreen_position_dropped(fake_webview):
    nwb.open_help_window("u", {"x": 99999, "y": 99999})
    (win,) = fake_webview.created
    assert "x" not in win.kwargs and "y" not in win.kwargs
    assert nwb._help_geometry == {"width": 1100, "height": 800}


def test_open_help_window_prefers_original_create(fake_webview):
    used = []

    def orig(**kwargs):
        used.append(kwargs)
        return FakeWindow()

    setattr(fake_webview, nwb._ORIG_CREATE_ATTR, orig)
    nwb.open_help_window("u")
    assert len(used) == 1 and fake_webview.created == []


def test_open_help_window_create_returns_none(fake_webview):
    fake_webview.create_window = lambda **kw: None
    nwb.open_help_window("u")
    assert nwb._help_window is None and nwb._help_geometry == {}


def test_open_help_window_event_attach_failure(fake_webview, caplog):
    win = FakeWindow(event_fail={"resized"})
    fake_webview.create_window = lambda **kw: win
    with caplog.at_level(logging.DEBUG, logger=nwb.logger.name):
        nwb.open_help_window("u")
    assert nwb._help_window is win
    assert "could not attach help window events" in caplog.text


def test_open_help_window_reuses_existing(fake_webview, monkeypatch):
    monkeypatch.setattr(nwb.sys, "platform", "linux")
    existing = FakeWindow()
    monkeypatch.setattr(nwb, "_help_window", existing)
    nwb.open_help_window("http://new")
    assert existing.calls == [("load_url", "http://new"), ("restore",), ("show",)]
    assert fake_webview.created == []
    existing.calls.clear()
    nwb.open_help_window("")
    assert existing.calls == [("restore",), ("show",)]


def test_open_help_window_recreates_broken_window(fake_webview, monkeypatch, caplog):
    broken = FakeWindow(fail={"load_url"})
    monkeypatch.setattr(nwb, "_help_window", broken)
    with caplog.at_level(logging.DEBUG, logger=nwb.logger.name):
        nwb.open_help_window("http://x")
    assert len(fake_webview.created) == 1
    assert nwb._help_window is fake_webview.created[0]
    assert "help window reuse failed, recreating" in caplog.text


def test_focus_help_window(monkeypatch):
    calls = []
    monkeypatch.setattr(nwb, "_bring_to_front", lambda w: calls.append(w))
    nwb.focus_help_window()
    win = FakeWindow()
    monkeypatch.setattr(nwb, "_help_window", win)
    nwb.focus_help_window()
    assert calls == [win]


def test_close_help_window(monkeypatch):
    nwb.close_help_window()  # nothing open
    win = FakeWindow()
    monkeypatch.setattr(nwb, "_help_window", win)
    nwb.close_help_window()
    assert win.calls == [("destroy",)]
    monkeypatch.setattr(nwb, "_help_window", FakeWindow(fail={"destroy"}))
    nwb.close_help_window()  # error swallowed


# --------------------------------------------------------------------------- tray hooks

def test_attach_tray_hooks_wires_everything(monkeypatch, sender):
    monkeypatch.setattr(nwb, "_app_url", "http://127.0.0.1:8080/")
    win = FakeWindow()
    nwb._attach_tray_hooks(win)
    assert win.events.closing.handlers == [nwb._on_window_closing]
    assert win.events.closed.handlers == [nwb.close_help_window]
    assert len(win.events.shown.handlers) == 1 and len(win.events.restored.handlers) == 1
    assert win.mycelian_open_help_window is nwb.open_help_window
    assert win.mycelian_focus_help_window is nwb.focus_help_window
    assert win.mycelian_close_help_window is nwb.close_help_window
    assert events(sender) == [("mycelian_window_ready", {"url": "http://127.0.0.1:8080/"})]
    sender.sent.clear()
    win.events.shown.handlers[0]()
    assert events(sender) == [("mycelian_window_shown", {})]


def test_tray_hook_helpers(monkeypatch, sender):
    win = FakeWindow()
    nwb._attach_tray_hooks(win)
    win.mycelian_set_tray_mode(1)
    assert nwb._tray_mode is True
    win.mycelian_set_tray_mode(0)
    assert nwb._tray_mode is False
    win.mycelian_allow_close()
    assert nwb._allow_close is True

    dock = []
    monkeypatch.setattr(nwb, "_set_macos_dock_visible", lambda v: dock.append(v))
    win.mycelian_set_dock_visible(0)
    assert dock == [False]

    suppressed = []
    monkeypatch.setattr(nwb, "_suppress_esm_warning", lambda w: suppressed.append(w))
    win.mycelian_prepare_for_blank()
    assert suppressed == [win]


def test_force_quit_clears_veto_and_destroys(monkeypatch, sender):
    closed = []
    monkeypatch.setattr(nwb, "close_help_window", lambda: closed.append(1))
    monkeypatch.setattr(nwb, "_tray_mode", True)
    win = FakeWindow()
    nwb._attach_tray_hooks(win)
    win.mycelian_force_quit()
    assert nwb._allow_close is True and nwb._tray_mode is False
    assert closed == [1]
    assert win.calls == [("hide",), ("destroy",)]
    assert nwb._on_window_closing() is None  # close now goes through


def test_force_quit_survives_window_errors(sender):
    win = FakeWindow(fail={"hide", "destroy"})
    nwb._attach_tray_hooks(win)
    win.mycelian_force_quit()
    assert win.calls == [("hide",), ("destroy",)]


def test_attach_tray_hooks_closing_failure(sender, caplog):
    win = FakeWindow(event_fail={"closing"})
    with caplog.at_level(logging.ERROR, logger=nwb.logger.name):
        nwb._attach_tray_hooks(win)
    assert "could not attach tray hooks" in caplog.text
    assert sender.sent == []  # bailed before announcing ready
    assert not hasattr(win, "mycelian_force_quit")


def test_attach_tray_hooks_partial_event_failures(sender, caplog):
    win = FakeWindow(event_fail={"closed", "shown"}, missing_events={"restored"})
    with caplog.at_level(logging.DEBUG, logger=nwb.logger.name):
        nwb._attach_tray_hooks(win)
    assert "could not attach help close hook" in caplog.text
    assert "could not attach shown handler" in caplog.text
    assert events(sender)[-1][0] == "mycelian_window_ready"


# --------------------------------------------------------------------------- create_window patch

def test_create_window_with_hooks_requires_original(fake_webview):
    with pytest.raises(RuntimeError, match="original create_window not installed"):
        nwb._create_window_with_tray_hooks()


def test_create_window_with_hooks(fake_webview, monkeypatch):
    attached = []
    monkeypatch.setattr(nwb, "_attach_tray_hooks", lambda w: attached.append(w))
    win = FakeWindow()
    setattr(fake_webview, nwb._ORIG_CREATE_ATTR, lambda *a, **k: win)
    assert nwb._create_window_with_tray_hooks("t", url="u") is win
    assert attached == [win]
    setattr(fake_webview, nwb._ORIG_CREATE_ATTR, lambda *a, **k: None)
    assert nwb._create_window_with_tray_hooks() is None
    assert attached == [win]


def test_patch_create_window_once(fake_webview):
    original = fake_webview.create_window
    nwb._patch_create_window()
    assert fake_webview.create_window is nwb._create_window_with_tray_hooks
    assert getattr(fake_webview, nwb._ORIG_CREATE_ATTR) is original
    nwb._patch_create_window()  # second call must not wrap the wrapper
    assert getattr(fake_webview, nwb._ORIG_CREATE_ATTR) is original


# --------------------------------------------------------------------------- _open_window context / patch

def _orig_open(protocol, host, port, title, event_sender=None):
    pass


def test_capture_context_builds_url(fake_nicegui):
    s = FakeSender()
    nwb._capture_open_window_context(_orig_open, ("https", "127.0.0.1", 8443, "T"), {"event_sender": s})
    assert nwb._event_sender is s
    assert nwb._app_url == "https://127.0.0.1:8443"


def test_capture_context_default_protocol_and_fallback_url(fake_nicegui):
    def boom(*a):
        raise RuntimeError("helpers changed")

    fake_nicegui.ng.helpers = SimpleNamespace(format_url=boom)
    nwb._capture_open_window_context(_orig_open, (None, "localhost", 8080, "T"), {})
    assert nwb._event_sender is None
    assert nwb._app_url == "http://localhost:8080/"


def test_capture_context_without_host_keeps_url(fake_nicegui):
    nwb._capture_open_window_context(_orig_open, ("http", None, 8080, "T"), {})
    assert nwb._app_url == ""


def test_capture_context_bind_error(caplog):
    with caplog.at_level(logging.WARNING, logger=nwb.logger.name):
        nwb._capture_open_window_context(_orig_open, (), {"bogus": 1})
    assert "could not bind _open_window args" in caplog.text


def test_install_patch(fake_nicegui):
    original = fake_nicegui.nm._open_window
    nwb.install()
    assert fake_nicegui.nm._open_window is nwb._open_window_with_mycelian_env
    assert getattr(fake_nicegui.nm, nwb._ORIG_ATTR) is original
    assert getattr(fake_nicegui.nm, nwb._PATCH_ATTR) is True
    nwb.install()  # idempotent
    assert getattr(fake_nicegui.nm, nwb._ORIG_ATTR) is original


def test_install_patch_without_nicegui(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "nicegui", None)
    with caplog.at_level(logging.DEBUG, logger=nwb.logger.name):
        nwb._install_patch()
    assert "native_mode not importable" in caplog.text


@pytest.mark.parametrize("env, tray", [("1", True), (" 1 ", True), ("0", False), (None, False)])
def test_open_window_wrapper(fake_nicegui, fake_webview, monkeypatch, env, tray):
    if env is not None:
        monkeypatch.setenv(nwb._MYCELIAN_TRAY_MODE_ENV, env)
    monkeypatch.setenv(nwb._MYCELIAN_NATIVE_ENV, json.dumps({"min_size": [10, 20]}))
    s = FakeSender()
    # Simulates the spawned child: the patch is not installed yet in this process.
    result = nwb._open_window_with_mycelian_env("http", "127.0.0.1", 8080, "Mycelian", 800, 600, False, False,
                                                "mq", "rq", event_sender=s)
    assert result == "opened"
    assert fake_nicegui.nm.calls == [dict(protocol="http", host="127.0.0.1", port=8080, event_sender=s)]
    assert fake_nicegui.window_args == {"min_size": (10, 20)}
    assert nwb._tray_mode is tray
    assert nwb._event_sender is s and nwb._app_url == "http://127.0.0.1:8080"
    assert fake_webview.create_window is nwb._create_window_with_tray_hooks


def test_open_window_wrapper_without_original(fake_nicegui, monkeypatch):
    setattr(fake_nicegui.nm, nwb._PATCH_ATTR, True)  # looks installed, but original missing
    with pytest.raises(RuntimeError, match="original _open_window not installed"):
        nwb._open_window_with_mycelian_env()


def test_open_window_wrapper_create_patch_failure(fake_nicegui, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "webview", None)
    with caplog.at_level(logging.ERROR, logger=nwb.logger.name):
        out = nwb._open_window_with_mycelian_env("http", "h", 1, "t", 1, 1, False, False, None, None)
    assert out == "opened"
    assert "create_window patch failed" in caplog.text

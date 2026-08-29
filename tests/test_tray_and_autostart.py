# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for minimize-to-tray, login items, and desktop notifications."""

import sys
from datetime import datetime, timedelta

import pytest

from modules import autostart, system_notify, tray_controller
from modules.dataobjects import AppSettings


# ----- settings schema -----


def test_background_settings_exist_and_default_off():
    """New installs must not silently start hiding the window or launching at login."""
    settings = AppSettings()
    assert settings.minimize_to_tray is False
    assert settings.start_minimized is False
    assert settings.run_at_startup is False


def test_database_defaults_include_background_settings():
    from modules import database_init

    source = database_init.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for field in ("minimize_to_tray", "start_minimized", "run_at_startup"):
        assert f'"{field}"' in text


# ----- tray controller -----


def test_tray_wanted_follows_settings(monkeypatch):
    def settings(minimize, start_min):
        return AppSettings(minimize_to_tray=minimize, start_minimized=start_min)

    monkeypatch.setattr(tray_controller, "_app_settings", lambda: settings(False, False))
    assert tray_controller.tray_wanted() is False

    monkeypatch.setattr(tray_controller, "_app_settings", lambda: settings(True, False))
    assert tray_controller.tray_wanted() is True

    # Start-minimized alone still needs a tray icon, or the app is unreachable.
    monkeypatch.setattr(tray_controller, "_app_settings", lambda: settings(False, True))
    assert tray_controller.tray_wanted() is True


def test_settings_readers_survive_missing_state(monkeypatch):
    def boom():
        raise RuntimeError("state manager not initialized")

    monkeypatch.setattr(tray_controller, "_app_settings", boom)
    assert tray_controller.minimize_to_tray_enabled() is False
    assert tray_controller.start_minimized_enabled() is False
    assert tray_controller.tray_wanted() is False


def test_set_window_url_ignores_the_blank_page(monkeypatch):
    """Caching about:blank would make restore reload nothing."""
    monkeypatch.setattr(tray_controller, "_window_url", None, raising=False)
    tray_controller.set_window_url(tray_controller.BLANK_URL)
    assert tray_controller._window_url is None

    tray_controller.set_window_url("http://127.0.0.1:8080/")
    assert tray_controller._window_url == "http://127.0.0.1:8080/"


def test_minimize_refuses_without_a_native_window(monkeypatch):
    monkeypatch.setattr(tray_controller, "_main_window", lambda: None)
    monkeypatch.setattr(tray_controller, "_minimized", False, raising=False)
    assert tray_controller.minimize_to_tray(reason="test") is False


def test_minimize_hides_then_blanks_and_restore_reverses_it(monkeypatch):
    """The unload only works if the window is blanked, not merely hidden."""

    class FakeWindow:
        def __init__(self):
            self.calls = []

        def hide(self):
            self.calls.append(("hide",))

        def show(self):
            self.calls.append(("show",))

        def restore(self):
            self.calls.append(("restore",))

        def load_url(self, url):
            self.calls.append(("load_url", url))

    window = FakeWindow()
    monkeypatch.setattr(tray_controller, "_main_window", lambda: window)
    monkeypatch.setattr(tray_controller, "is_tray_running", lambda: True)
    monkeypatch.setattr(tray_controller, "_send_to_tray", lambda payload: True)
    monkeypatch.setattr(tray_controller, "_suspend_ui_health_monitor", lambda flag: None)
    monkeypatch.setattr(tray_controller, "_flush_pending_update_prompt", lambda: None)
    monkeypatch.setattr(tray_controller, "_minimized", False, raising=False)
    monkeypatch.setattr(tray_controller, "_window_url", "http://127.0.0.1:8080/", raising=False)

    assert tray_controller.minimize_to_tray(reason="test") is True
    assert window.calls == [("hide",), ("load_url", tray_controller.BLANK_URL)]
    assert tray_controller.is_minimized() is True

    window.calls.clear()
    assert tray_controller.restore_from_tray(reason="test") is True
    assert window.calls[0] == ("load_url", "http://127.0.0.1:8080/")
    assert ("show",) in window.calls
    assert tray_controller.is_minimized() is False


def test_minimize_is_idempotent(monkeypatch):
    monkeypatch.setattr(tray_controller, "_minimized", True, raising=False)
    monkeypatch.setattr(tray_controller, "_main_window", lambda: None)
    # Already minimized, so it must not fail just because the window lookup would.
    assert tray_controller.minimize_to_tray(reason="test") is True


def test_destroy_native_window_hides_then_force_quits(monkeypatch):
    """Hide is immediate; destroy is async and vetoable, so the child does both."""
    calls = []

    class FakeWindow:
        def hide(self):
            calls.append("hide")

        def destroy(self):
            calls.append("destroy")

    monkeypatch.setattr(tray_controller, "_main_window", lambda: FakeWindow())
    monkeypatch.setattr(
        tray_controller, "push_window_method", lambda name, *a: calls.append(name) or True
    )

    tray_controller.destroy_native_window()
    # Restoring the Dock first would activate the app and keep the window visible.
    assert calls == ["hide", "mycelian_force_quit"]


def test_quit_closes_the_window_before_teardown(monkeypatch):
    """A tray Quit with the window open must hide it before services start stopping."""
    calls = []

    class FakeWindow:
        def hide(self):
            calls.append("hide")

    monkeypatch.setattr(tray_controller, "_main_window", lambda: FakeWindow())
    monkeypatch.setattr(
        tray_controller, "push_window_method", lambda name, *a: calls.append(name) or True
    )
    monkeypatch.setattr(
        tray_controller, "_wait_for_native_window_exit", lambda: calls.append("wait")
    )
    monkeypatch.setattr(tray_controller, "stop_tray", lambda: calls.append("stop_tray"))
    monkeypatch.setattr(tray_controller, "_quit_requested", True, raising=False)

    import modules.shutdown as shutdown_module
    import modules.updater as updater_module

    monkeypatch.setattr(
        shutdown_module,
        "shutdown_application",
        lambda **kw: calls.append(("shutdown", kw.get("reason"))),
    )
    monkeypatch.setattr(
        updater_module, "_force_application_exit", lambda: calls.append("exit")
    )

    tray_controller._quit_worker("menu")
    assert calls[:3] == ["hide", "mycelian_force_quit", "wait"]
    assert "stop_tray" in calls
    assert ("shutdown", "tray_menu") in calls
    assert calls[-1] == "exit"


def test_wait_for_window_exit_spares_the_tray_process(monkeypatch):
    """The tray child is still needed; only the webview process should be reaped."""
    import multiprocessing

    terminated = []

    class FakeChild:
        def __init__(self, name, pid):
            self.name = name
            self.pid = pid

        def terminate(self):
            terminated.append(self.name)

        def join(self, timeout=None):
            terminated.append(f"join:{self.name}")

    tray = FakeChild("mycelian-tray", 11)
    window = FakeChild("webview-child", 22)
    monkeypatch.setattr(tray_controller, "_process", tray, raising=False)
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [tray, window])
    monkeypatch.setattr(tray_controller.time, "monotonic", lambda: 0)
    # Timeout immediately so the leftover window child is terminated.
    tray_controller._wait_for_native_window_exit(timeout=0)
    assert terminated == ["webview-child", "join:webview-child"]


def test_shutdown_reaps_the_window_child_process(monkeypatch):
    """os._exit() skips multiprocessing's atexit hook, so children must be reaped here."""
    import multiprocessing

    from modules import shutdown as shutdown_module

    terminated = []

    class FakeChild:
        name = "webview-child"

        def terminate(self):
            terminated.append("terminate")

        def join(self, timeout=None):
            terminated.append("join")

    monkeypatch.setattr(multiprocessing, "active_children", lambda: [FakeChild()])
    monkeypatch.setattr(shutdown_module, "_cleanup_pywebview_windows", lambda: None)
    # Shorten the grace period; this child never exits on its own.
    monkeypatch.setattr(shutdown_module, "_NATIVE_WINDOW_EXIT_GRACE_SEC", 0.05)

    shutdown_module._close_native_window()
    assert terminated == ["terminate", "join"]


def test_shutdown_waits_for_a_child_that_exits_on_its_own(monkeypatch):
    """A window that closes cleanly must not then be terminated."""
    import multiprocessing

    from modules import shutdown as shutdown_module

    class FakeChild:
        name = "webview-child"

        def terminate(self):
            raise AssertionError("a child that exited must not be terminated")

    remaining = [[FakeChild()], []]
    monkeypatch.setattr(
        multiprocessing, "active_children", lambda: remaining.pop(0) if remaining else []
    )
    monkeypatch.setattr(shutdown_module, "_cleanup_pywebview_windows", lambda: None)
    shutdown_module._close_native_window()


def test_quitting_does_not_trip_the_tray_crash_fallback(monkeypatch):
    """Tray Quit stops the icon, which closes the pipe and looks like a crash."""
    restored = []
    monkeypatch.setattr(tray_controller, "_quit_requested", False, raising=False)
    monkeypatch.setattr(tray_controller, "_fallback_applied", False, raising=False)
    monkeypatch.setattr(
        tray_controller, "restore_from_tray", lambda *, reason: restored.append(reason)
    )
    monkeypatch.setattr(tray_controller, "push_tray_mode", lambda enabled: None)
    monkeypatch.setattr(tray_controller, "set_dock_visible", lambda visible: None)

    # Nothing has asked to quit: a dead tray must hand the window back.
    tray_controller._fall_back_to_plain_window("crashed")
    assert tray_controller._fallback_applied is True

    # Once a quit is under way the fallback must stay out of the way.
    monkeypatch.setattr(tray_controller, "_fallback_applied", False, raising=False)
    monkeypatch.setattr(tray_controller, "_quit_requested", True, raising=False)
    tray_controller._fall_back_to_plain_window("pipe closed during quit")
    assert tray_controller._fallback_applied is False


def test_close_request_quits_when_tray_mode_is_off(monkeypatch):
    """With the toggle off, the close button must still close the app."""
    calls = []
    monkeypatch.setattr(tray_controller, "minimize_to_tray_enabled", lambda: False)
    monkeypatch.setattr(
        tray_controller, "quit_application", lambda *, reason: calls.append(reason)
    )
    tray_controller.handle_close_request()
    assert calls == ["window_close"]


def test_close_request_quits_when_minimize_fails(monkeypatch):
    """A broken tray must never leave the user unable to close the window."""
    calls = []
    monkeypatch.setattr(tray_controller, "minimize_to_tray_enabled", lambda: True)
    monkeypatch.setattr(tray_controller, "minimize_to_tray", lambda *, reason: False)
    monkeypatch.setattr(
        tray_controller, "quit_application", lambda *, reason: calls.append(reason)
    )
    tray_controller.handle_close_request()
    assert calls == ["window_close"]


# ----- autostart -----


def test_autostart_unsupported_when_not_frozen():
    """Running from source there is no stable executable to register."""
    assert autostart.is_supported() is False
    assert autostart.is_enabled() is False
    assert autostart.set_enabled(True) is False
    assert autostart.get_executable_path() is None


def test_macos_launch_agent_roundtrip(tmp_path, monkeypatch):
    import plistlib

    plist = tmp_path / "com.mycelian.app.plist"
    monkeypatch.setattr(autostart, "_launch_agent_path", lambda: plist)

    assert autostart._macos_is_enabled() is False
    autostart._macos_set_enabled(True, "/Applications/Mycelian.app/Contents/MacOS/Mycelian")
    assert autostart._macos_is_enabled() is True

    payload = plistlib.loads(plist.read_bytes())
    assert payload["Label"] == autostart.BUNDLE_ID
    assert payload["RunAtLoad"] is True
    # KeepAlive would relaunch the app every time the user quits it.
    assert payload["KeepAlive"] is False
    assert payload["ProgramArguments"] == [
        "/Applications/Mycelian.app/Contents/MacOS/Mycelian"
    ]

    autostart._macos_set_enabled(False, "unused")
    assert autostart._macos_is_enabled() is False


def test_linux_desktop_entry_roundtrip(tmp_path, monkeypatch):
    entry = tmp_path / "autostart" / "mycelian.desktop"
    monkeypatch.setattr(autostart, "_desktop_entry_path", lambda: entry)

    assert autostart._linux_is_enabled() is False
    autostart._linux_set_enabled(True, "/opt/mycelian/Mycelian")
    assert autostart._linux_is_enabled() is True

    text = entry.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    # Quoted so a path containing spaces still launches.
    assert 'Exec="/opt/mycelian/Mycelian"' in text

    autostart._linux_set_enabled(False, "unused")
    assert autostart._linux_is_enabled() is False


def test_disabling_autostart_is_safe_when_nothing_is_registered(tmp_path, monkeypatch):
    entry = tmp_path / "autostart" / "mycelian.desktop"
    monkeypatch.setattr(autostart, "_desktop_entry_path", lambda: entry)
    assert autostart._linux_set_enabled(False, "unused") is True


# ----- system notifications -----


def test_system_notify_respects_the_notifications_setting(monkeypatch):
    sent = []
    monkeypatch.setattr(system_notify, "_notifications_enabled", lambda: False)
    monkeypatch.setattr(
        system_notify, "_dispatch", lambda title, message: sent.append((title, message))
    )

    assert system_notify.notify("hello") is False
    assert sent == []

    # force is for messages the user must not miss.
    system_notify.notify("hello", force=True)
    assert sent == [("Mycelian", "hello")]


def test_system_notify_never_raises(monkeypatch):
    monkeypatch.setattr(system_notify, "_notifications_enabled", lambda: True)

    def boom(title, message):
        raise OSError("no notification daemon")

    monkeypatch.setattr(system_notify, "_dispatch", boom)
    assert system_notify.notify("hello") is False


def test_applescript_escaping_prevents_broken_scripts():
    escaped = system_notify._escape_applescript('say "hi" \\ there')
    assert '\\"hi\\"' in escaped
    assert "\\\\" in escaped


# ----- 24/7 audit fixes -----


def test_unknown_token_expiry_counts_as_expired():
    """A missing expiry used to switch proactive refresh off entirely."""
    from modules.twitch_token_auth import is_access_token_expired

    assert is_access_token_expired("token", None) is True
    assert is_access_token_expired("", None) is True
    assert (
        is_access_token_expired("token", datetime.now() + timedelta(hours=4)) is False
    )
    assert is_access_token_expired("token", datetime.now() - timedelta(hours=1)) is True


def test_notification_dedupe_map_is_pruned():
    """The dedupe map is keyed by message identity and used to grow forever."""
    from modules import notification_engine as ne

    ne._last_emit_monotonic.clear()
    try:
        for i in range(ne._DEDUPE_PRUNE_THRESHOLD + 10):
            ne._last_emit_monotonic[f"stale-{i}"] = -ne._DEDUPE_ENTRY_TTL_SEC * 2
        ne._mark_dedupe("fresh")
        assert "fresh" in ne._last_emit_monotonic
        assert len(ne._last_emit_monotonic) == 1
    finally:
        ne._last_emit_monotonic.clear()


def test_recent_new_sub_alerts_evicts_expired_entries():
    """Entries were only dropped when the same user was re-checked."""
    import time as _time

    from modules.twitch_subscriber_registry import _NEW_SUB_ALERT_DEDUP_SECONDS
    from modules import twitch_subscriber_registry as registry_module

    registry = registry_module.SubscriberRegistry.__new__(
        registry_module.SubscriberRegistry
    )
    import threading

    registry._lock = threading.RLock()
    registry._recent_new_sub_alerts = {
        "old-user": _time.time() - (_NEW_SUB_ALERT_DEDUP_SECONDS + 60)
    }

    registry.mark_new_sub_alerted("new-user")

    assert "new-user" in registry._recent_new_sub_alerts
    assert "old-user" not in registry._recent_new_sub_alerts


# ----- close veto (webview subprocess side) -----


def _closing_event_with_bridge_handler():
    """Build a real pywebview closing Event wired to the bridge handler.

    pywebview creates ``closing`` with ``should_lock=True`` and treats a ``False``
    from any handler as "cancel the close", so ``Event.set()`` returning True means
    the window stays open.
    """
    from webview.event import Event

    from modules import native_window_bridge as bridge

    event = Event(window=None, should_lock=True)
    event += bridge._on_window_closing
    return event, bridge


def test_close_is_vetoed_only_while_tray_mode_is_on(monkeypatch):
    event, bridge = _closing_event_with_bridge_handler()
    sent = []
    monkeypatch.setattr(bridge, "_send_native_event", lambda t, **kw: sent.append(t))

    monkeypatch.setattr(bridge, "_tray_mode", False, raising=False)
    monkeypatch.setattr(bridge, "_allow_close", False, raising=False)
    assert event.set() is False, "with tray mode off the window must close normally"
    assert sent == []

    monkeypatch.setattr(bridge, "_tray_mode", True, raising=False)
    assert event.set() is True, "with tray mode on the close must be cancelled"
    assert sent == ["mycelian_close_requested"]


def test_app_quit_is_not_vetoed(monkeypatch):
    """Cmd+Q and the Quit menu reach the same closing event as the red X.

    On macOS ``applicationShouldTerminate_`` asks every window's ``closing`` event, so
    vetoing without telling the two apart made the app impossible to quit.
    """
    event, bridge = _closing_event_with_bridge_handler()
    sent = []
    monkeypatch.setattr(bridge, "_send_native_event", lambda t, **kw: sent.append(t))
    monkeypatch.setattr(bridge, "_tray_mode", True, raising=False)
    monkeypatch.setattr(bridge, "_allow_close", False, raising=False)

    # Emulate the backend frame that macOS puts on the stack for an app-level quit.
    scope: dict = {"event": event}
    exec(
        "def applicationShouldTerminate_():\n    return scope['event'].set()\n",
        {"scope": scope},
        scope,
    )
    assert scope["applicationShouldTerminate_"]() is False, "quit must not be cancelled"
    assert sent == ["mycelian_quit_requested"]

    # The window's own close button still goes to the tray.
    sent.clear()
    assert event.set() is True
    assert sent == ["mycelian_close_requested"]


def test_allow_close_overrides_the_veto(monkeypatch):
    """Shutdown pushes mycelian_allow_close so the window can be destroyed."""
    event, bridge = _closing_event_with_bridge_handler()
    monkeypatch.setattr(bridge, "_send_native_event", lambda t, **kw: None)
    monkeypatch.setattr(bridge, "_tray_mode", True, raising=False)
    monkeypatch.setattr(bridge, "_allow_close", True, raising=False)
    assert event.set() is False


def test_bridge_helpers_attach_to_a_pywebview_window(monkeypatch):
    """NiceGUI resolves window methods with getattr, so these must be real attributes."""
    from webview.event import Event
    from webview.window import Window

    from modules import native_window_bridge as bridge

    class Events:
        pass

    window = Window.__new__(Window)
    window.events = Events()
    window.events.closing = Event(window=None, should_lock=True)

    monkeypatch.setattr(bridge, "_send_native_event", lambda t, **kw: None)
    monkeypatch.setattr(bridge, "_tray_mode", False, raising=False)
    monkeypatch.setattr(bridge, "_allow_close", False, raising=False)

    bridge._attach_tray_hooks(window)

    for name in (
        "mycelian_set_tray_mode",
        "mycelian_allow_close",
        "mycelian_set_dock_visible",
        "mycelian_prepare_for_blank",
        "mycelian_force_quit",
    ):
        assert callable(getattr(window, name)), name

    # Must all return None, or NiceGUI's executor would push a stray response and
    # desynchronise every later window round-trip.
    assert window.mycelian_set_tray_mode(True) is None
    assert bridge._tray_mode is True
    assert window.mycelian_allow_close() is None
    assert bridge._allow_close is True
    assert window.mycelian_set_dock_visible(True) is None
    assert window.mycelian_prepare_for_blank() is None


def test_force_quit_clears_the_veto_then_hides_and_destroys(monkeypatch):
    """Tray Quit has to clear the veto in the webview child or cocoa cancels close."""
    from webview.event import Event
    from webview.window import Window

    from modules import native_window_bridge as bridge

    calls = []

    class Events:
        pass

    window = Window.__new__(Window)
    window.events = Events()
    window.events.closing = Event(window=None, should_lock=True)
    window.hide = lambda: calls.append("hide")
    window.destroy = lambda: calls.append("destroy")

    monkeypatch.setattr(bridge, "_send_native_event", lambda t, **kw: None)
    monkeypatch.setattr(bridge, "_tray_mode", True, raising=False)
    monkeypatch.setattr(bridge, "_allow_close", False, raising=False)

    bridge._attach_tray_hooks(window)
    assert window.mycelian_force_quit() is None
    assert bridge._allow_close is True
    assert bridge._tray_mode is False
    assert calls == ["hide", "destroy"]


def test_blanking_suppresses_the_vue_load_warning():
    """Parking on about:blank makes NiceGUI's Vue probe fail and log a scary error."""
    from webview.event import Event

    from modules import native_window_bridge as bridge

    class Events:
        pass

    class FakeWindow:
        pass

    window = FakeWindow()
    window.events = Events()
    window.events.loaded = Event(window=None)

    def check():  # same name/module shape NiceGUI uses
        return None

    check.__module__ = "nicegui.native.native_mode"

    def unrelated():
        return None

    window.events.loaded += check
    window.events.loaded += unrelated

    bridge._suppress_esm_warning(window)

    remaining = window.events.loaded._items
    assert check not in remaining, "NiceGUI's Vue probe should be removed"
    assert unrelated in remaining, "unrelated handlers must be left alone"


def test_minimize_hides_the_dock_icon_before_hiding_the_window(monkeypatch):
    """A hidden window with a live Dock icon can be reopened behind our back."""
    order = []

    class FakeWindow:
        def hide(self):
            order.append("hide")

        def show(self):
            order.append("show")

        def restore(self):
            order.append("restore")

        def load_url(self, url):
            order.append(f"load_url:{url}")

    monkeypatch.setattr(tray_controller, "_main_window", lambda: FakeWindow())
    monkeypatch.setattr(tray_controller, "is_tray_running", lambda: True)
    monkeypatch.setattr(tray_controller, "_send_to_tray", lambda payload: True)
    monkeypatch.setattr(tray_controller, "_suspend_ui_health_monitor", lambda flag: None)
    monkeypatch.setattr(tray_controller, "_minimized", False, raising=False)
    monkeypatch.setattr(
        tray_controller,
        "push_window_method",
        lambda name, *a: order.append(f"{name}:{a}") or True,
    )

    assert tray_controller.minimize_to_tray(reason="test") is True
    assert order.index("mycelian_set_dock_visible:(False,)") < order.index("hide")
    assert "mycelian_prepare_for_blank:()" in order
    assert order[-1] == f"load_url:{tray_controller.BLANK_URL}"


def test_live_alerts_are_capped():
    """live_alerts was insert-only, which only mattered once sessions ran for weeks."""
    from modules.uiwindows import activity_feed as feed

    state = feed.activity_feed_state
    original = state.live_alerts
    state.live_alerts = []
    try:
        for i in range(feed.MAX_LIVE_ALERTS + 25):
            state.live_alerts.insert(0, {"message": f"alert-{i}", "element": None})
            feed._trim_live_alerts()

        assert len(state.live_alerts) == feed.MAX_LIVE_ALERTS
        # Newest stays at the head, oldest are the ones dropped.
        assert state.live_alerts[0]["message"] == f"alert-{feed.MAX_LIVE_ALERTS + 24}"
        assert state.live_alerts[-1]["message"] == "alert-25"
    finally:
        state.live_alerts = original


def test_trim_live_alerts_releases_ui_elements():
    """Trimming must delete the NiceGUI elements or the DOM keeps growing."""
    from modules.uiwindows import activity_feed as feed

    class FakeElement:
        is_deleted = False

        def __init__(self):
            self.deleted = False
            self.client = object()

        def delete(self):
            self.deleted = True

    state = feed.activity_feed_state
    original = state.live_alerts
    doomed = FakeElement()
    state.live_alerts = [{"element": None} for _ in range(feed.MAX_LIVE_ALERTS)]
    state.live_alerts.append({"element": doomed})
    try:
        feed._trim_live_alerts()
        assert len(state.live_alerts) == feed.MAX_LIVE_ALERTS
        assert doomed.deleted is True
    finally:
        state.live_alerts = original


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS notification backend")
def test_macos_uses_osascript_not_the_tray(monkeypatch):
    """pystray's macOS notifications use an API Apple removed."""
    commands = []
    monkeypatch.setattr(system_notify, "_run", lambda cmd: commands.append(cmd) or True)
    monkeypatch.setattr(system_notify, "_notifications_enabled", lambda: True)

    assert system_notify.notify("body", title="Heads up") is True
    assert commands[0][0] == "osascript"
    assert "display notification" in commands[0][2]
    assert "Heads up" in commands[0][2]

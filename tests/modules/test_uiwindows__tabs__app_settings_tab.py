# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/app_settings_tab.py: building the App Settings tab, the status bar
Badge style / Minimal badge style pickers and their dimming, notification and Activity Feed panels,
Stream Deck plugin status/install, Save / Discard and the background actions.
state_manager, autostart, tray, alert trimming and feed saving are all fakes."""

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
from nicegui import ui  # noqa: E402
from nicegui.client import Client  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import alertutils, autostart, dataobjects, tray_controller, web_engine  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.notification_engine import FOOTER_FULL_STYLES, FOOTER_MINIMAL_STYLES  # noqa: E402
from modules.notify_settings import LEVELS, SOURCES, NotifyPrefs  # noqa: E402
from modules.streamdeck_plugin_utils import PluginInstallError, PluginInstallState, PluginStatus  # noqa: E402
from modules.uiwindows import activity_feed  # noqa: E402
from modules.uiwindows.tabs import app_settings_tab as ast  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# ----------------------------------------------------------------- fakes

class FakeState:
    def __init__(self, **values):
        self.settings = dataobjects.AppSettings(**values)
        self.updates = {}
        self.save_ok = True
        self.saves = 0

    def get_app_settings(self):
        return self.settings

    def update_app_setting(self, field, value):
        self.updates[field] = value
        setattr(self.settings, field, value)
        return True

    def save_changes(self):
        self.saves += 1
        return self.save_ok


@pytest.fixture
def state(monkeypatch):
    fake = FakeState()
    monkeypatch.setattr(ast, "state_manager", fake)
    return fake


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(ast, "notify", lambda msg, **k: calls.append((msg, k)))
    return calls


@pytest.fixture
def plugin(monkeypatch):
    holder = {"status": PluginStatus(PluginInstallState.NOT_INSTALLED, bundled_version="2.0")}
    calls = []
    monkeypatch.setattr(ast, "get_plugin_status", lambda: holder["status"])
    monkeypatch.setattr(ast, "maybe_notify_streamdeck_plugin_outdated", lambda: calls.append("outdated-check"))
    holder["calls"] = calls
    return holder


@pytest.fixture
def side(monkeypatch):
    """Everything save() and the switches reach outside the tab."""
    calls = []
    monkeypatch.setattr(ast, "apply_app_font", lambda family: calls.append(("font", family)))
    monkeypatch.setattr(ast, "apply_ui_scale", lambda pct: calls.append(("scale", pct)))
    monkeypatch.setattr(ast, "refresh_service_status_footer", lambda: calls.append("footer"))
    monkeypatch.setattr(autostart, "is_supported", lambda: True)
    monkeypatch.setattr(autostart, "set_enabled", lambda on: calls.append(("autostart", on)))
    monkeypatch.setattr(tray_controller, "apply_settings", lambda: calls.append("tray"))
    monkeypatch.setattr(tray_controller, "warn_background_database_usage", lambda: calls.append("warn-db"))
    monkeypatch.setattr(alertutils.alert_state_manager, "initialize", lambda: calls.append("alerts-init"))
    monkeypatch.setattr(alertutils, "schedule_auto_trim_stored_alerts", lambda: calls.append("trim"))
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    feed = []
    monkeypatch.setattr(activity_feed, "set_feed_setting", lambda key, value: feed.append((key, value)) or True)
    monkeypatch.setattr(activity_feed, "_legacy_person_hours", lambda: 12)
    return SimpleNamespace(calls=calls, feed=feed)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(ast, "get_available_font_families", lambda: ["System default", "Arial", "Fira Code"])


def fire(user, el, event="click"):
    UserInteraction(user, {el}, None).trigger(event)


def by_class(client, cls):
    return sorted((e for e in client.elements.values() if cls in e.classes), key=lambda e: e.id)


def button(client, text):
    for e in sorted(client.elements.values(), key=lambda e: e.id):
        if isinstance(e, ui.button) and e.text == text:
            return e
    raise AssertionError(text)


def dimmed(tab, key):
    return "is-dim" in tab._dims[key].classes


def build_tab(check):
    made = {}

    def build():
        tab = ast.AppSettingsTab()
        tab.build(ui.column())
        made["tab"] = tab

    async def run(user):
        with user.client:
            await check(user, made["tab"])

    run_ui(build, run)
    return made["tab"]


# ----------------------------------------------------------------- build

def test_build_renders_sections(state, plugin, side, notes):
    async def check(user, tab):
        for text in ("App Settings", "Appearance", "Status bar", "Badge style", "Minimal badges", "Minimal badge style",
                     "Window and startup", "Updates", "Activity feed and storage", "Notifications", "Activity Feed",
                     "Stream Deck plugin", "By level"):
            await user.should_see(text)
        ui_ = tab.ui_elements
        assert ui_["ui_font_family"].value == "System default"
        assert ui_["ui_scale_percent"].value == "100"
        assert ui_["status_footer_full_style"].options == dict(FOOTER_FULL_STYLES)
        assert ui_["status_footer_full_style"].value == "stacked"
        assert ui_["status_footer_minimal_style"].options == dict(FOOTER_MINIMAL_STYLES)
        assert ui_["status_footer_minimal_style"].value == "tiles"
        assert "All changes saved" in ui_["_dirty_label"].content
        assert "disable" in ui_["_discard_btn"].props
        assert ui_["install_plugin_button"].text == "Install Plugin"
        assert "Not installed" in ui_["plugin_badge"].content and "is-muted" in ui_["plugin_badge"].content
        assert len(by_class(user.client, "aps-level")) == len(LEVELS)
        assert len(by_class(user.client, "aps-mx")) == 2
        assert len(by_class(user.client, "aps-feed-tile")) == 3
        assert "is-on" in by_class(user.client, "aps-feed-tile")[0].classes

    build_tab(check)
    assert notes == []


@pytest.mark.parametrize(
    "enabled, minimal, dims",
    [
        (True, False, {"status_footer_minimal": False, "status_footer_full_style": False, "status_footer_minimal_style": True}),
        (True, True, {"status_footer_minimal": False, "status_footer_full_style": True, "status_footer_minimal_style": False}),
        (False, False, {"status_footer_minimal": True, "status_footer_full_style": True, "status_footer_minimal_style": True}),
        (False, True, {"status_footer_minimal": True, "status_footer_full_style": True, "status_footer_minimal_style": True}),
    ],
)
def test_badge_style_pickers_dimming(monkeypatch, plugin, side, enabled, minimal, dims):
    fake = FakeState(status_footer_enabled=enabled, status_footer_minimal=minimal)
    monkeypatch.setattr(ast, "state_manager", fake)

    async def check(user, tab):
        for key, expected in dims.items():
            assert dimmed(tab, key) is expected, key

    build_tab(check)


def test_badge_style_changes_and_live_dimming(state, plugin, side, notes):
    async def check(user, tab):
        full = tab.ui_elements["status_footer_full_style"]
        other_full = next(k for k in FOOTER_FULL_STYLES if k != "stacked")
        full.set_value(other_full)
        assert tab.buffer.status_footer_full_style == other_full and tab.dirty
        assert "Unsaved changes" in tab.ui_elements["_dirty_label"].content
        assert "disable" not in tab.ui_elements["_discard_btn"].props
        # turning minimal badges on dims the full picker and enables the minimal one
        tab.ui_elements["status_footer_minimal"].set_value(True)
        assert dimmed(tab, "status_footer_full_style") and not dimmed(tab, "status_footer_minimal_style")
        mini = tab.ui_elements["status_footer_minimal_style"]
        other_mini = next(k for k in FOOTER_MINIMAL_STYLES if k != "tiles")
        mini.set_value(other_mini)
        assert tab.buffer.status_footer_minimal_style == other_mini
        # turning the bar off dims all three
        tab.ui_elements["status_footer_enabled"].set_value(False)
        assert all(dimmed(tab, k) for k in ("status_footer_minimal", "status_footer_full_style", "status_footer_minimal_style"))
        fire(user, button(user.client, "Save"))
        assert state.updates["status_footer_full_style"] == other_full
        assert state.updates["status_footer_minimal_style"] == other_mini
        assert state.updates["status_footer_minimal"] is True and state.updates["status_footer_enabled"] is False

    build_tab(check)
    assert "footer" in side.calls


def test_unknown_stored_badge_styles_fall_back(monkeypatch, plugin, side):
    fake = FakeState(status_footer_full_style="gone", status_footer_minimal_style="nope", ui_scale_percent=97,
                     ui_font_family="Arial")
    monkeypatch.setattr(ast, "state_manager", fake)

    async def check(user, tab):
        assert tab.ui_elements["status_footer_full_style"].value == "stacked"
        assert tab.ui_elements["status_footer_minimal_style"].value == "tiles"
        assert tab.ui_elements["ui_scale_percent"].value == "100"
        assert tab.ui_elements["ui_font_family"].value == "Arial"
        assert "font-family: 'Arial', sans-serif" in tab._font_preview._style.get("font-family", "") or \
            tab._font_preview._style.get("font-family") == "'Arial', sans-serif"

    build_tab(check)


def test_appearance_changes(state, plugin, side):
    async def check(user, tab):
        tab.ui_elements["ui_font_family"].set_value("Fira Code")
        assert tab.buffer.ui_font_family == "Fira Code"
        tab.ui_elements["ui_font_family"].set_value("System default")
        assert tab.buffer.ui_font_family == ""
        assert tab._font_preview._style.get("font-family") == "inherit"
        tab.ui_elements["ui_scale_percent"].set_value("110")
        assert tab.buffer.ui_scale_percent == 110 and ("scale", 110) in side.calls
        tab.ui_elements["ui_scale_percent"].set_value(None)
        assert tab.buffer.ui_scale_percent == 100

    build_tab(check)


def test_window_and_storage_rows(state, plugin, side):
    async def check(user, tab):
        assert not dimmed(tab, "run_at_startup")
        tab.ui_elements["minimize_to_tray"].set_value(True)
        assert "warn-db" in side.calls
        assert "stay live while the window is in the tray" in tab._tray_note.content
        tab.ui_elements["minimize_to_tray"].set_value(False)
        assert tab._tray_note.content == ""
        tab.ui_elements["start_minimized"].set_value(True)
        tab.ui_elements["run_at_startup"].set_value(True)
        assert side.calls.count("warn-db") == 3
        # updates interval dims when auto update is off
        tab.ui_elements["auto_update"].set_value(False)
        assert dimmed(tab, "update_check_interval_minutes")
        assert tab.ui_elements["update_check_interval_minutes"].value == 30  # default shown
        # storage
        tab.ui_elements["activity_feed_limit"].set_value(10)
        tab.ui_elements["activity_feed_max_pages"].set_value(3)
        assert "<b>30</b> events" in tab._storage_note.content
        tab.ui_elements["alert_storage_auto_trim"].set_value(False)
        assert "kept forever" in tab._storage_note.content
        assert all(dimmed(tab, k) for k in ("alert_storage_trim_mode", "alert_storage_keep_count", "alert_storage_keep_days"))
        tab.ui_elements["alert_storage_auto_trim"].set_value(True)
        tab.ui_elements["alert_storage_trim_mode"].set_value("quantity")
        tab.ui_elements["alert_storage_keep_count"].set_value(1500)
        assert "Keeps the newest <b>1,500</b> alerts." in tab._storage_note.content
        assert dimmed(tab, "alert_storage_keep_days") and not dimmed(tab, "alert_storage_keep_count")
        tab.ui_elements["alert_storage_trim_mode"].set_value("time")
        tab.ui_elements["alert_storage_keep_days"].set_value(1)
        assert "older than <b>1 day</b>" in tab._storage_note.content
        assert dimmed(tab, "alert_storage_keep_count")
        tab.ui_elements["alert_storage_trim_mode"].set_value(None)
        tab.ui_elements["alert_storage_keep_days"].set_value(7)
        assert "none older than <b>7 days</b>" in tab._storage_note.content

    build_tab(check)


@pytest.mark.xfail(strict=True, reason="BUG: AppSettings has no update_check_interval_minutes field, so changing "
                                       "'Check every' raises AttributeError in _set and the interval is never saved")
def test_update_interval_can_be_changed_and_saved(state, plugin, side):
    tab = ast.AppSettingsTab()
    tab._load_from_state()
    tab._set("update_check_interval_minutes", 45)
    tab.save()
    assert state.updates.get("update_check_interval_minutes") == 45


def test_autostart_unsupported(state, plugin, side, monkeypatch):
    monkeypatch.setattr(autostart, "is_supported", lambda: False)

    async def check(user, tab):
        assert dimmed(tab, "run_at_startup")
        assert not tab.ui_elements["run_at_startup"].enabled
        await user.should_see("Run at login")
        assert tab._login_description() == "Only available in an installed build of Mycelian."

    build_tab(check)


def test_autostart_supported_errors(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(autostart, "is_supported", boom)
    assert ast.AppSettingsTab._autostart_supported() is False


def test_set_tray_option_warning_error(state, monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tray_controller, "warn_background_database_usage", boom)
    tab = ast.AppSettingsTab()
    tab._load_from_state()
    tab._set_tray_option("minimize_to_tray", True)
    assert tab.buffer.minimize_to_tray is True and tab.dirty


# ----------------------------------------------------------------- notifications panel

def test_notification_panel_controls(state, plugin, side, notes):
    async def check(user, tab):
        panel = tab._notify_panel
        selects = [e for e in panel.descendants() if isinstance(e, ui.select)]
        selects[0].set_value("bottom-right")
        assert tab.buffer.notify_position == "bottom-right"
        selects[0].set_value(None)
        assert tab.buffer.notify_position == "top-right"
        selects[1].set_value("6")
        assert tab.buffer.notify_max_visible == 6
        selects[1].set_value(None)
        assert tab.buffer.notify_max_visible == 4
        # level switch off -> panel rebuilt with is-off
        level_switches = [e for e in by_class(user.client, "aps-level-top")[0].descendants() if isinstance(e, ui.switch)]
        level_switches[0].set_value(False)
        assert tab.buffer.notify_levels["error"] is False
        assert "is-off" in by_class(user.client, "aps-level")[0].classes
        # durations: error is "never" by default (box disabled), warning editable
        boxes = [e for e in tab._notify_panel.descendants() if isinstance(e, ui.number)]
        assert not boxes[0].enabled and boxes[1].value == 8
        tab._set_duration("error", 5)  # never: ignored
        assert tab.buffer.notify_durations["error"] == 0
        boxes[1].value = 12
        fire(user, boxes[1], "blur")
        assert tab.buffer.notify_durations["warning"] == 12
        boxes[1].value = 999999
        fire(user, boxes[1], "keydown.enter")
        assert tab.buffer.notify_durations["warning"] == 86400.0
        tab._set_duration("warning", "abc")
        tab._set_duration("warning", 86400)  # unchanged
        assert tab.buffer.notify_durations["warning"] == 86400.0
        # never dismiss on / off
        tab._set_never("info", True)
        assert tab.buffer.notify_durations["info"] == 0
        tab._set_never("info", False)
        assert tab.buffer.notify_durations["info"] == 4
        tab._set_never("warning", False)  # already timed: unchanged
        assert tab.buffer.notify_durations["warning"] == 86400.0
        never_switches = [e for e in by_class(user.client, "aps-level-row") for e in e.descendants() if isinstance(e, ui.switch)]
        never_switches[0].set_value(False)  # error: back to a default duration
        assert tab.buffer.notify_durations["error"] == 10

    build_tab(check)


def test_feature_matrix(state, plugin, side):
    async def check(user, tab):
        sid = SOURCES[0][0]
        tab._set_source_level(sid, "info", False)
        assert tab.buffer.notify_muted == {sid: ["info"]}
        tab._set_source_level(sid, "info", True)
        assert tab.buffer.notify_muted == {}
        for lv in LEVELS:
            tab._set_source_level(sid, lv, False)
        assert sorted(tab.buffer.notify_muted[sid]) == sorted(LEVELS)
        await user.should_see("Muted")
        # checkbox in the table
        boxes = [e for e in by_class(user.client, "aps-mx")[0].descendants() if isinstance(e, ui.checkbox)]
        boxes[0].set_value(True)
        assert "error" not in tab.buffer.notify_muted.get(sid, [])
        # header click toggles a whole column
        headers = [e for e in by_class(user.client, "aps-mx")[0].descendants() if e.tag == "th"]
        fire(user, headers[0])
        assert all("error" in tab.buffer.notify_muted.get(s, []) for s, _l, _i in SOURCES)
        tab._toggle_level_column("error")
        assert all("error" not in tab.buffer.notify_muted.get(s, []) for s, _l, _i in SOURCES)

    build_tab(check)


def test_send_test_notifications(state, plugin, side, notes):
    async def check(user, tab):
        fire(user, button(user.client, "Send a test"))
        assert [k.get("dedupe_key") for _, k in notes] == ["test:error", "test:warning", "test:info", "test:success"]
        notes.clear()
        tab._set("notify_position", "bottom-left")
        tab._send_test_notifications()
        assert notes[0][0] == "Save first to test your new settings"

    build_tab(check)


def test_render_panels_skip_when_missing_or_deleted(state, plugin, side):
    tab = ast.AppSettingsTab()
    tab._render_notify_panel()
    tab._render_feed_panel()
    tab._notify_panel = SimpleNamespace(is_deleted=True)
    tab._feed_panel = SimpleNamespace(is_deleted=True)
    tab._render_notify_panel()
    tab._render_feed_panel()
    tab._refresh_derived()  # no buffer


# ----------------------------------------------------------------- activity feed panel

def test_feed_panel_controls(state, plugin, side, notes):
    async def check(user, tab):
        tiles = by_class(user.client, "aps-feed-tile")
        fire(user, tiles[1])
        assert side.feed[-1] == ("layout", "cards") and tab.buffer.activity_feed_layout == "cards"
        assert "is-on" in by_class(user.client, "aps-feed-tile")[1].classes
        selects = [e for e in tab._feed_panel.descendants() if isinstance(e, ui.select)]
        selects[0].set_value("spacious")
        assert side.feed[-1] == ("density", "spacious")
        switches = [e for e in tab._feed_panel.descendants() if isinstance(e, ui.switch)]
        switches[0].set_value(False)
        assert side.feed[-1] == ("icons", False)
        assert not dimmed(tab, "feed_sub_duration_style")
        tab._set_feed("sub_duration", False)  # rebuilds; the multi-month rows dim
        assert dimmed(tab, "feed_sub_duration_style") and dimmed(tab, "feed_sub_duration_place")
        assert notes == []
        # a save failure warns but keeps the value
        activity_feed.set_feed_setting = lambda k, v: False
        tab._set_feed("wrap", True)
        assert notes[-1][1]["title"] == "Couldn't save the Activity Feed setting"

    build_tab(check)


def test_feed_person_hours_custom_value(monkeypatch, plugin, side):
    fake = FakeState(activity_feed_person_hours=5)
    monkeypatch.setattr(ast, "state_manager", fake)

    async def check(user, tab):
        selects = [e for e in tab._feed_panel.descendants() if isinstance(e, ui.select)]
        person = [s for s in selects if 168 in s.options][0]
        assert person.options[5] == "5 hours" and person.options[1] == "1 hour" and person.options[168] == "7 days"
        assert person.value == 5

    build_tab(check)


def test_set_feed_without_buffer(side):
    tab = ast.AppSettingsTab()
    tab._set_feed("density", "compact")
    assert side.feed == [("density", "compact")]


# ----------------------------------------------------------------- stream deck plugin

@pytest.mark.parametrize(
    "status, css, icon, text",
    [
        (PluginStatus(PluginInstallState.UP_TO_DATE, "2.0", "2.0"), "is-ok", "check_circle", "Installed: v2.0"),
        (PluginStatus(PluginInstallState.OUTDATED, "2.0", "1.0"), "is-warn", "update", "New version available: v2.0"),
        (PluginStatus(PluginInstallState.UNKNOWN, "2.0", None), "is-ok", "check_circle", ""),
        (PluginStatus(PluginInstallState.UNAVAILABLE), "is-muted", "extension_off", "Stream Deck not detected"),
    ],
)
def test_plugin_status_display(state, plugin, side, status, css, icon, text):
    plugin["status"] = status

    async def check(user, tab):
        tab.on_enter()
        badge = tab.ui_elements["plugin_badge"].content
        assert f"aps-plugin-badge {css}" in badge and f">{icon}<" in badge
        assert text in badge
        inst = tab.ui_elements["plugin_installed_version"]
        new = tab.ui_elements["plugin_new_version"]
        assert inst.visible is (status.state in (PluginInstallState.UP_TO_DATE, PluginInstallState.OUTDATED, PluginInstallState.UNKNOWN))
        assert new.visible is (status.state == PluginInstallState.OUTDATED)
        if new.visible:
            assert new.text == "New version available: v2.0"
        assert tab.ui_elements["install_plugin_button"].text in ("Install Plugin", "Reinstall Plugin")

    build_tab(check)
    assert plugin["calls"] == ["outdated-check"]


def test_plugin_display_without_elements():
    tab = ast.AppSettingsTab()
    tab._apply_plugin_status_display(PluginStatus(PluginInstallState.OUTDATED, "2", "1"))
    assert tab.on_exit() is None


@pytest.mark.parametrize(
    "before, after, replaced, verb_title",
    [
        (PluginInstallState.NOT_INSTALLED, PluginInstallState.UP_TO_DATE, False, "Stream Deck plugin installed"),
        (PluginInstallState.UP_TO_DATE, PluginInstallState.UP_TO_DATE, False, "Stream Deck plugin reinstalled"),
        (PluginInstallState.NOT_INSTALLED, PluginInstallState.UNKNOWN, True, "Stream Deck plugin reinstalled"),
        (PluginInstallState.NOT_INSTALLED, PluginInstallState.OUTDATED, False, "Stream Deck plugin copied"),
    ],
)
def test_install_plugin(state, plugin, side, notes, monkeypatch, before, after, replaced, verb_title):
    plugin["status"] = PluginStatus(before, "2.0", "2.0" if before != PluginInstallState.NOT_INSTALLED else None)

    def install():
        plugin["status"] = PluginStatus(after, "2.0", "2.0")
        return SimpleNamespace(replaced_existing=replaced)

    monkeypatch.setattr(ast, "install_bundled_plugin", install)

    async def check(user, tab):
        fire(user, tab.ui_elements["install_plugin_button"])
        assert tab.ui_elements["install_plugin_button"].text == "Reinstall Plugin"

    build_tab(check)
    assert notes[-1][1]["title"] == verb_title


@pytest.mark.parametrize("exc, message", [(PluginInstallError("raw", user_message="Close it"), "Close it"),
                                          (RuntimeError("boom"), "")])
def test_install_plugin_errors(state, plugin, notes, monkeypatch, exc, message):
    def install():
        raise exc

    monkeypatch.setattr(ast, "install_bundled_plugin", install)
    ast.AppSettingsTab()._install_streamdeck_plugin()
    msg, kw = notes[-1]
    assert msg == message and kw["title"] == "Couldn't install the Stream Deck plugin"
    assert type(exc).__name__ in kw["details"]


# ----------------------------------------------------------------- save / discard

def test_save_persists_and_applies(state, plugin, side, notes, monkeypatch):
    state.settings.alert_storage_auto_trim = True
    emitted = []
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=lambda ev, data: emitted.append((ev, data))))

    async def check(user, tab):
        tab.ui_elements["ui_font_family"].set_value("Arial")
        tab.ui_elements["run_at_startup"].set_value(True)
        fire(user, button(user.client, "Save"))
        assert tab.dirty is False
        assert "All changes saved" in tab.ui_elements["_dirty_label"].content

    build_tab(check)
    assert "version" not in state.updates and "build_number" not in state.updates
    assert state.updates["ui_font_family"] == "Arial" and state.saves == 1
    assert ("font", "Arial") in side.calls and ("scale", 100) in side.calls
    assert ("autostart", True) in side.calls and "tray" in side.calls
    assert "alerts-init" in side.calls and "trim" in side.calls
    assert emitted and emitted[0][0] == "notify_settings"
    assert emitted[0][1] == NotifyPrefs.from_app_settings(state.settings).for_client()
    assert notes[-1][1]["title"] == "App settings saved"


def test_save_failure_keeps_dirty(state, plugin, side, notes):
    state.save_ok = False

    async def check(user, tab):
        tab._set("start_maximized", not tab.buffer.start_maximized)
        tab.save()
        assert tab.dirty is True

    build_tab(check)
    assert notes == [] and "tray" not in side.calls


def test_save_without_buffer():
    ast.AppSettingsTab().save()


def test_save_side_effect_failures(state, plugin, side, notes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(autostart, "set_enabled", boom)
    monkeypatch.setattr(tray_controller, "apply_settings", boom)
    monkeypatch.setattr(alertutils, "schedule_auto_trim_stored_alerts", boom)
    monkeypatch.setattr(ast, "refresh_service_status_footer", boom)
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=boom))
    state.settings.alert_storage_auto_trim = True
    tab = ast.AppSettingsTab()
    tab._load_from_state()
    tab.save()
    titles = [k["title"] for _, k in notes]
    assert "Couldn't turn 'Run at startup' off" in titles
    assert "Automatic alert cleanup couldn't start" in titles
    assert titles[-1] == "App settings saved"


def test_save_skips_trim_when_disabled(monkeypatch, plugin, side, notes):
    monkeypatch.setattr(ast, "state_manager", FakeState(alert_storage_auto_trim=False, run_at_startup=True))
    tab = ast.AppSettingsTab()
    tab._load_from_state()
    tab.save()
    assert "trim" not in side.calls and ("autostart", True) in side.calls


def test_discard_restores_controls(state, plugin, side):
    async def check(user, tab):
        tab.ui_elements["ui_font_family"].set_value("Arial")
        tab.ui_elements["ui_scale_percent"].set_value("120")
        tab.ui_elements["status_footer_minimal"].set_value(True)
        tab.ui_elements["status_footer_full_style"].set_value(next(k for k in FOOTER_FULL_STYLES if k != "stacked"))
        tab._set("notify_position", "bottom-left")
        fire(user, tab.ui_elements["_discard_btn"])
        assert tab.dirty is False
        assert tab.ui_elements["ui_font_family"].value == "System default"
        assert tab.ui_elements["ui_scale_percent"].value == "100"
        assert tab.ui_elements["status_footer_minimal"].value is False
        assert tab.ui_elements["status_footer_full_style"].value == "stacked"
        assert tab.buffer.notify_position == state.settings.notify_position
        assert side.calls[-1] == ("scale", 100)

    build_tab(check)


def test_static_helpers():
    assert ast.AppSettingsTab._css_font("") == "inherit"
    assert ast.AppSettingsTab._css_font("O'Bri\\en") == "'OBrien', sans-serif"
    assert ast.AppSettingsTab._font_from_value(None) == ""
    assert ast.AppSettingsTab._font_from_value("System default") == ""
    assert ast.AppSettingsTab._font_from_value(5) == "5"
    ast.AppSettingsTab._set_note(None, "x")


def test_sync_font_without_element(state):
    tab = ast.AppSettingsTab()
    tab._load_from_state()
    tab.buffer.ui_font_family = "Keep"
    tab._sync_font_from_ui()
    assert tab.buffer.ui_font_family == "Keep"


def test_load_from_state_is_detached(state):
    tab = ast.AppSettingsTab()
    tab._load_from_state()
    tab.buffer.start_maximized = not state.settings.start_maximized
    assert tab.buffer is not state.settings
    tab._set("start_maximized", tab.buffer.start_maximized)  # unchanged value: not dirty
    assert tab.dirty is False

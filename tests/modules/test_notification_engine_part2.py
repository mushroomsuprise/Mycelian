# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.notification_engine``: status footer (labels, tiers, badge styles, summary chip,
trim/copy progress badges) and the service watcher."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
for p in (HERE, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction

from modules import alertutils
from modules import connection_status_tracker as cst
from modules import notification_engine as ne
from modules import template_copy_progress as tcp
from modules.help_system import contextual_help as ch
from modules.service_alerts import Notice
from _ne_fixtures import FakeEl, ne_env  # noqa: F401  (fixture)
from ui_sim import run_ui


def _state(monkeypatch, settings):
    from modules import dataobjects

    def get():
        if isinstance(settings, Exception):
            raise settings
        return settings

    monkeypatch.setattr(dataobjects.state_manager, "get_app_settings", get)


# --------------------------------------------------------------------------- settings readers

@pytest.mark.parametrize("settings,expected", [
    (None, True), (SimpleNamespace(), True), (SimpleNamespace(status_footer_enabled=False), False),
    (RuntimeError("db"), True),
])
def test_status_footer_enabled(monkeypatch, settings, expected):
    _state(monkeypatch, settings)
    assert ne._status_footer_enabled() is expected


def test_service_status_label_delegates(monkeypatch):
    monkeypatch.setattr(cst, "get_connection_status", lambda key: f"status:{key}")
    assert ne._service_status_label("obs") == "status:obs"


@pytest.mark.parametrize("minimal,full,mini,expected", [
    (False, "pills", "x", "service-status-footer--full-pills"),
    (False, "nope", "x", "service-status-footer--full-stacked"),
    (True, "x", "rings", "service-status-footer--minimal service-status-footer--min-rings"),
    (True, "x", "nope", "service-status-footer--minimal service-status-footer--min-tiles"),
])
def test_footer_style_classes(minimal, full, mini, expected):
    assert ne.footer_style_classes(minimal, full, mini) == expected


def test_current_footer_style_classes(monkeypatch):
    _state(monkeypatch, SimpleNamespace(status_footer_minimal=False, status_footer_full_style=None,
                                        status_footer_minimal_style=None))
    assert ne.current_footer_style_classes() == "service-status-footer--full-stacked"
    _state(monkeypatch, RuntimeError("x"))
    assert ne.current_footer_style_classes() == "service-status-footer--full-stacked"
    _state(monkeypatch, SimpleNamespace(status_footer_minimal=True, status_footer_minimal_style="summary"))
    assert ne.current_footer_style_classes().endswith("--min-summary")


def test_all_style_classes_cover_every_style():
    for k in ne.FOOTER_FULL_STYLES:
        assert f"service-status-footer--full-{k}" in ne._ALL_FOOTER_STYLE_CLASSES
    for k in ne.FOOTER_MINIMAL_STYLES:
        assert f"service-status-footer--min-{k}" in ne._ALL_FOOTER_STYLE_CLASSES


def test_apply_footer_minimal_class(ne_env, monkeypatch):
    ne._apply_footer_minimal_class()  # no container: nothing
    el = FakeEl()
    el.cls = {"service-status-footer", "service-status-footer--full-pills"}
    monkeypatch.setattr(ne, "_footer_container", el)
    monkeypatch.setattr(ne, "current_footer_style_classes", lambda: "service-status-footer--full-bar")
    ne._apply_footer_minimal_class()
    assert el.cls == {"service-status-footer", "service-status-footer--full-bar"}
    assert el._mycelian_style == "service-status-footer--full-bar"
    el.cls.add("marker")
    ne._apply_footer_minimal_class()  # unchanged style: nothing touched
    assert "marker" in el.cls
    bad = FakeEl(fail=True)
    monkeypatch.setattr(ne, "_footer_container", bad)
    ne._apply_footer_minimal_class()  # swallowed


@pytest.mark.parametrize("tiers,expected", [
    ([], ("success", "All connected")),
    (["success", "muted"], ("success", "All connected")),
    (["info", "success"], ("info", "Connecting")),
    (["warning"], ("warning", "1 needs attention")),
    (["warning", "warning", "info"], ("warning", "2 need attention")),
    (["error", "warning"], ("error", "2 need attention")),
])
def test_footer_summary(tiers, expected):
    assert ne.footer_summary(tiers) == expected


def test_set_footer_item_tier():
    el = FakeEl()
    el.cls = {"service-status-tier-info", "keep"}
    ne._set_footer_item_tier(el, "error")
    assert el.cls == {"keep", "service-status-tier-error"}
    ne._set_footer_item_tier(el, "bogus")
    assert el.cls == {"keep", "service-status-tier-info"}
    ne._set_footer_item_tier(FakeEl(fail=True), "error")  # swallowed


def _entry(key="obs", raw="Connected", display="Connected", tier="success"):
    return ne.ServiceFooterEntry(key=key, label=key, status_raw=raw, display=display, tier=tier, visible=True)


def test_footer_item_tip(monkeypatch):
    from modules import web_engine

    assert ne._footer_item_tip("obs", _entry(display="Disp")) == "Disp"
    monkeypatch.setattr(web_engine, "get_webengine_health", lambda: {"detail": "Heartbeat 2s ago"})
    assert ne._footer_item_tip("webengine", _entry()) == "Heartbeat 2s ago"
    monkeypatch.setattr(web_engine, "get_webengine_health", lambda: {})
    assert ne._footer_item_tip("webengine", _entry(raw="running")) == "running"

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(web_engine, "get_webengine_health", boom)
    assert ne._footer_item_tip("webengine", _entry(raw="", display="Unknown")) == "Unknown"


def test_set_footer_item_tooltip_creates_then_updates():
    def build():
        el = ui.label("x")
        refs = {}
        ne._set_footer_item_tooltip(el, refs, "first")
        tip = refs["tooltip"]
        assert tip.props["target"] == f"#{el.html_id}" and tip.text == "first"
        ne._set_footer_item_tooltip(el, refs, "first")
        assert refs["tooltip"] is tip
        ne._set_footer_item_tooltip(el, refs, "second")
        assert refs["tooltip"] is tip and tip.text == "second" and refs["tooltip_text"] == "second"

    run_ui(build)


def test_set_footer_item_tooltip_error_swallowed():
    class BadTip:
        def set_text(self, t):
            raise RuntimeError("deleted")

    refs = {"tooltip": BadTip(), "tooltip_text": "old"}
    ne._set_footer_item_tooltip(FakeEl(), refs, "new")
    assert refs["tooltip_text"] == "old"


# --------------------------------------------------------------------------- display / tier

@pytest.mark.parametrize("key,raw,display", [
    ("twitch", "", "Unknown"), ("twitch", None, "Unknown"),
    ("webengine", "running", "Running"), ("webengine", "Stalled", "Stalled"), ("webengine", "overloaded", "Overloaded"),
    ("webengine", "crashed", "Crashed"), ("webengine", "restarting", "Restarting"), ("webengine", "starting", "Starting"),
    ("webengine", "stopped", "Stopped"), ("webengine", "Frozen (60s)", "Frozen"),
    ("twitch", "Authenticated but Disconnected", "Degraded"), ("twitch", "Degraded", "Degraded"),
    ("twitch", "Stale data", "Degraded"), ("twitch", "No recent events", "Degraded"),
    ("twitch", "Partial (2 of 3)", "Partial"), ("twitch", "Connected", "Connected"),
    ("twitch", "Connected as bob", "Connected"), ("twitch", "Reconnecting...", "Reconnecting"),
    ("twitch", "connecting", "Connecting"), ("twitch", "DISCONNECTING", "Disconnecting"),
    ("obs", "Not running", "Not running"), ("obs", "Can't connect", "Can't connect"),
    ("twitch", "Bot stopped", "Stopped"), ("twitch", "No Internet", "No Internet"),
    ("twitch", "Service Unreachable", "Unreachable"), ("twitch", "Checking Internet", "Checking"),
    ("psn", "Online", "Online"), ("psn", "offline", "Offline"),
    ("spotify", "Token Expired", "Expired"), ("spotify", "Token Refresh Failed", "Expired"),
    ("spotify", "Token refresh error", "Expired"), ("discord", "All channels failed", "Error"),
    ("twitch", "Authentication failed", "Auth Failed"), ("obs", "Auth Failed", "Auth Failed"),
    ("spotify", "Authorization Error", "Auth Failed"), ("spotify", "Authorization timeout", "Auth Failed"),
    ("twitch", "Disconnected", "Disconnected"), ("spotify", "Token refresh in progress", "Connecting"),
    ("twitch", "Not connected", "Idle"), ("twitch", "Not configured", "Idle"), ("spotify", "Not Initialized", "Idle"),
    ("youtube", "Configured but idle", "Idle"), ("spotify", "Authorization Required", "Idle"),
    ("spotify", "Awaiting Authorization", "Idle"), ("spotify", "Opening Browser...", "Idle"),
    ("youtube", "Missing API key", "Idle"), ("youtube", "Set channel URL", "Idle"),
    ("twitch", "Some error", "Error"), ("twitch", "Failure", "Error"),
    ("twitch", "Paused", "Paused"), ("twitch", "A very long status text here", "A very long…"),
])
def test_footer_status_display(key, raw, display):
    assert ne.footer_status_display(key, raw) == display


@pytest.mark.parametrize("key,raw,tier", [
    ("twitch", "Connected", "success"), ("webengine", "running", "success"), ("psn", "online", "success"),
    ("twitch", "connecting", "info"), ("twitch", "disconnecting", "info"), ("webengine", "starting", "info"),
    ("webengine", "restarting", "info"), ("twitch", "Reconnecting", "info"), ("twitch", "Checking", "info"),
    ("twitch", "Disconnected", "muted"), ("twitch", "Not configured", "muted"), ("twitch", "", "muted"),
    ("obs", "Not running", "muted"), ("obs", "Can't connect", "warning"),
    ("internet", "Offline", "error"), ("psn", "Offline", "warning"),
    ("twitch", "Degraded", "warning"), ("webengine", "stalled", "warning"), ("webengine", "overloaded", "warning"),
    ("twitch", "Partial", "warning"), ("webengine", "stopped", "warning"),
    ("twitch", "Error", "error"), ("webengine", "crashed", "error"), ("webengine", "frozen", "error"),
    ("spotify", "Token Expired", "error"), ("obs", "Auth Failed", "error"), ("twitch", "No Internet", "error"),
    ("twitch", "Unreachable", "error"), ("twitch", "Paused", "info"),
])
def test_footer_status_tier(key, raw, tier):
    assert ne.footer_status_tier(key, raw) == tier


def test_iter_service_footer_entries(monkeypatch):
    monkeypatch.setattr(ne, "_service_configured", lambda key: key in ("obs", "psn"))

    def label(key):
        if key == "psn":
            raise RuntimeError("x")
        return {"internet": "Online", "twitch": "Connected", "obs": "Can't connect"}[key]

    monkeypatch.setattr(ne, "_service_status_label", label)
    entries = ne.iter_service_footer_entries()
    assert [e.key for e in entries] == ["internet", "twitch", "obs", "psn"]
    obs = entries[2]
    assert (obs.label, obs.display, obs.tier, obs.visible) == ("OBS", "Can't connect", "warning", True)
    assert entries[3].status_raw == "Unknown" and entries[3].tier == "muted"


# --------------------------------------------------------------------------- background probe

def test_run_integration_probe_background(ne_env, monkeypatch):
    seen = []
    monkeypatch.setattr(cst, "probe_configured_services", lambda force: seen.append(force))
    monkeypatch.setattr(ne, "_footer_probe_running", True)
    ne._run_integration_probe_background(force=True)
    assert seen == [True] and ne._footer_probe_running is False and ne._footer_probe_refresh_pending is True

    def boom(force):
        raise RuntimeError("x")

    monkeypatch.setattr(cst, "probe_configured_services", boom)
    monkeypatch.setattr(ne, "_footer_probe_refresh_pending", False)
    ne._run_integration_probe_background(force=False)
    assert ne._footer_probe_refresh_pending is True


def test_refresh_footer_after_background_probe(ne_env, monkeypatch):
    refreshed = []
    monkeypatch.setattr(ne, "refresh_service_status_footer", lambda: refreshed.append(1))
    ne._refresh_footer_after_background_probe()
    assert refreshed == [] and ne_env.sched.calls == []
    monkeypatch.setattr(ne, "_footer_probe_running", True)
    ne._refresh_footer_after_background_probe()
    assert ne_env.sched.calls[-1][:2] == (0.15, ne._refresh_footer_after_background_probe)
    assert ne_env.sched.calls[-1][2] == {"once": True}
    monkeypatch.setattr(ne, "_footer_probe_refresh_pending", True)
    ne._refresh_footer_after_background_probe()
    assert refreshed == [1] and ne._footer_probe_refresh_pending is False


def test_schedule_service_status_probe(ne_env, monkeypatch):
    threads = []

    class FakeThread:
        def __init__(self, target, kwargs, daemon, name):
            self.target, self.kwargs, self.daemon, self.name = target, kwargs, daemon, name

        def start(self):
            threads.append(self)

    monkeypatch.setattr(ne.threading, "Thread", FakeThread)
    ne.schedule_service_status_probe(force=True, delay_seconds=0.5)
    assert ne._footer_probe_scheduled is True
    delay, start_probe, kw = ne_env.sched.calls[0]
    assert delay == 0.5 and kw == {"once": True}
    ne.schedule_service_status_probe(force=True)  # already scheduled
    assert len(ne_env.sched.calls) == 1
    start_probe()
    (t,) = threads
    assert t.target is ne._run_integration_probe_background and t.kwargs == {"force": True}
    assert t.daemon and t.name == "ServiceStatusProbe" and ne._footer_probe_running is True
    assert ne_env.sched.calls[-1][1] is ne._refresh_footer_after_background_probe
    ne.schedule_service_status_probe(force=False)  # non-forced probes always schedule
    assert len(ne_env.sched.calls) == 3


# --------------------------------------------------------------------------- click handlers / progress checks

@pytest.fixture
def navs(monkeypatch):
    calls = []
    monkeypatch.setattr(ch, "navigate_to_settings_subtab", lambda s, main_tab=None: calls.append(("settings", s, main_tab)))
    monkeypatch.setattr(ch, "navigate_to_main_tab", lambda s: calls.append(("main", s)))
    return calls


def test_footer_item_click(navs, monkeypatch):
    ne._on_footer_item_click("internet")
    ne._on_footer_item_click("obs")
    ne._on_footer_item_click("webengine")
    ne._on_footer_item_click("unknown")
    assert navs == [("settings", "OBS", "Settings"), ("settings", "App Settings", "Settings")]


def test_click_handlers_swallow_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no tabs")

    monkeypatch.setattr(ch, "navigate_to_settings_subtab", boom)
    monkeypatch.setattr(ch, "navigate_to_main_tab", boom)
    ne._on_footer_item_click("obs")
    ne._on_template_copy_footer_click()
    ne._on_alert_trim_footer_click()


def test_copy_and_trim_click_navigate(navs):
    ne._on_template_copy_footer_click(None)
    ne._on_alert_trim_footer_click(None)
    assert navs == [("main", "Source Settings"), ("settings", "App Settings", "Settings")]


def test_progress_active_checks(monkeypatch):
    monkeypatch.setattr(tcp, "template_copy_is_active", lambda: True)
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": 1})
    assert ne._template_copy_footer_is_active() is True and ne._alert_trim_footer_is_active() is True
    assert ne._footer_progress_active() is True

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tcp, "template_copy_is_active", boom)
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", boom)
    assert ne._template_copy_footer_is_active() is False and ne._alert_trim_footer_is_active() is False
    assert ne._footer_progress_active() is False


@pytest.mark.parametrize("enabled,trim,copy,expected", [
    (True, False, False, True), (False, True, False, True), (False, False, True, True), (False, False, False, False),
])
def test_footer_should_stay_open(monkeypatch, enabled, trim, copy, expected):
    monkeypatch.setattr(ne, "_status_footer_enabled", lambda: enabled)
    monkeypatch.setattr(ne, "_alert_trim_footer_is_active", lambda: trim)
    monkeypatch.setattr(ne, "_template_copy_footer_is_active", lambda: copy)
    assert ne._footer_should_stay_open() is expected


def test_set_footer_container_hidden(ne_env, monkeypatch):
    ne._set_footer_container_hidden(True)  # no container
    el = FakeEl()
    monkeypatch.setattr(ne, "_footer_container", el)
    ne._set_footer_container_hidden(True)
    assert "service-status-footer--hidden" in el.cls
    ne._set_footer_container_hidden(False)
    assert "service-status-footer--hidden" not in el.cls
    monkeypatch.setattr(ne, "_footer_container", FakeEl(fail=True))
    ne._set_footer_container_hidden(True)


# --------------------------------------------------------------------------- trim badge

def _trim_refs(fail=False):
    return {"container": FakeEl(fail=fail), "dot": FakeEl(fail=fail), "badge": FakeEl(fail=fail),
            "badge_wrap": FakeEl(fail=fail)}


def test_refresh_alert_trim_footer_no_refs(ne_env, monkeypatch):
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: pytest.fail("not called"))
    ne.refresh_alert_trim_footer()


def test_refresh_alert_trim_footer_shows_progress_then_hides(ne_env, monkeypatch):
    refs = _trim_refs()
    ne._trim_footer_refs.update(refs)
    footer = FakeEl()
    monkeypatch.setattr(ne, "_footer_container", footer)
    progress = {"active": True, "phase": "deleting", "to_delete": 10, "deleted": 4}
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: dict(progress))
    monkeypatch.setattr(alertutils, "format_trim_progress_badge", lambda p: ("4/10", "warning") if p["active"] else None)
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: False)
    ne.refresh_alert_trim_footer()
    assert refs["badge"].content == "4/10"
    assert refs["dot"].replaced == "service-status-dot warning"
    assert refs["badge_wrap"].replaced == "service-status-badge warning"
    assert "service-status-item--hidden" not in refs["container"].cls
    assert ne._trim_footer_last_signature == (True, "deleting", 10, 4)
    refs["badge"].content = "untouched"
    ne.refresh_alert_trim_footer()  # same signature: no work
    assert refs["badge"].content == "untouched"
    progress["active"] = False
    ne.refresh_alert_trim_footer()
    assert "service-status-item--hidden" in refs["container"].cls
    assert "service-status-footer--hidden" in footer.cls


def test_refresh_alert_trim_footer_hidden_keeps_footer_when_needed(ne_env, monkeypatch):
    refs = _trim_refs()
    ne._trim_footer_refs.update(refs)
    footer = FakeEl()
    monkeypatch.setattr(ne, "_footer_container", footer)
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)

    def boom():
        raise RuntimeError("no alert storage")

    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", boom)
    ne.refresh_alert_trim_footer()
    assert ne._trim_footer_last_signature == (False, "idle", 0, 0)
    assert "service-status-item--hidden" in refs["container"].cls and footer.cls == set()


def test_refresh_alert_trim_footer_bad_progress_values(ne_env, monkeypatch):
    ne._trim_footer_refs.update(_trim_refs())
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress",
                        lambda: {"active": True, "phase": "deleting", "to_delete": "lots"})
    monkeypatch.setattr(alertutils, "format_trim_progress_badge", lambda p: ("x", "info"))
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)
    ne.refresh_alert_trim_footer()
    assert ne._trim_footer_last_signature == (False, "idle", 0, 0)
    assert "service-status-item--hidden" in ne._trim_footer_refs["container"].cls


def test_refresh_alert_trim_footer_missing_container(ne_env, monkeypatch):
    ne._trim_footer_refs.update({"container": None})
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": True})
    monkeypatch.setattr(alertutils, "format_trim_progress_badge", lambda p: ("x", "info"))
    ne.refresh_alert_trim_footer()
    assert ne._trim_footer_last_signature == (True, "", 0, 0)


def test_refresh_alert_trim_footer_element_errors_swallowed(ne_env, monkeypatch):
    ne._trim_footer_refs.update(_trim_refs(fail=True))
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": True, "phase": "a"})
    monkeypatch.setattr(alertutils, "format_trim_progress_badge", lambda p: ("x", "info"))
    ne.refresh_alert_trim_footer()
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": False, "phase": "b"})
    monkeypatch.setattr(alertutils, "format_trim_progress_badge", lambda p: None)
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)
    ne.refresh_alert_trim_footer()


def test_refresh_alert_trim_footer_missing_optional_refs(ne_env, monkeypatch):
    ne._trim_footer_refs.update({"container": FakeEl()})
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": True})
    monkeypatch.setattr(alertutils, "format_trim_progress_badge", lambda p: ("x", "info"))
    ne.refresh_alert_trim_footer()


# --------------------------------------------------------------------------- copy badge

def _copy_refs(fail=False):
    refs = _trim_refs(fail)
    refs["name"] = FakeEl(fail=fail)
    return refs


def test_refresh_template_copy_footer_no_refs(ne_env, monkeypatch):
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: pytest.fail("not called"))
    ne.refresh_template_copy_footer()


def test_refresh_template_copy_footer_shows_and_hides(ne_env, monkeypatch):
    refs = _copy_refs()
    ne._copy_footer_refs.update(refs)
    footer = FakeEl()
    monkeypatch.setattr(ne, "_footer_container", footer)
    tips = []
    monkeypatch.setattr(ne, "_set_footer_item_tooltip", lambda c, r, t: tips.append(t))
    progress = {"active": True, "phase": "copying", "copied": 3, "total": 9, "label": "Copying A", "title": "Templates"}
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: dict(progress))
    monkeypatch.setattr(tcp, "format_template_copy_badge", lambda p: ("3/9", "info") if p["active"] else None)
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: False)
    ne.refresh_template_copy_footer()
    assert refs["badge"].content == "3/9" and refs["name"].content == "Templates" and tips == ["Copying A"]
    assert refs["dot"].replaced == "service-status-dot info"
    assert ne._copy_footer_last_signature == (True, "copying", 3, 9, "Copying A", "Templates")
    ne.refresh_template_copy_footer()
    assert tips == ["Copying A"]
    progress.update(label="", title="", copied=4)
    ne.refresh_template_copy_footer()
    assert refs["name"].content == "Copying" and tips[-1] == "Copying template"
    progress["active"] = False
    ne.refresh_template_copy_footer()
    assert "service-status-item--hidden" in refs["container"].cls
    assert "service-status-footer--hidden" in footer.cls


def test_refresh_template_copy_footer_errors(ne_env, monkeypatch):
    ne._copy_footer_refs.update(_copy_refs())
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tcp, "get_template_copy_progress", boom)
    ne.refresh_template_copy_footer()
    assert ne._copy_footer_last_signature == (False, "idle", 0, 0, "", "")
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: {"active": True, "copied": "x"})
    monkeypatch.setattr(tcp, "format_template_copy_badge", lambda p: ("x", "info"))
    ne._copy_footer_last_signature = None
    ne.refresh_template_copy_footer()
    assert ne._copy_footer_last_signature == (False, "idle", 0, 0, "", "")


def test_refresh_template_copy_footer_missing_container_and_failing_elements(ne_env, monkeypatch):
    ne._copy_footer_refs.update({"container": None})
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: {"active": True})
    monkeypatch.setattr(tcp, "format_template_copy_badge", lambda p: ("x", "info"))
    ne.refresh_template_copy_footer()
    ne._copy_footer_refs.clear()
    ne._copy_footer_refs.update(_copy_refs(fail=True))
    ne._copy_footer_last_signature = None
    ne.refresh_template_copy_footer()
    monkeypatch.setattr(tcp, "format_template_copy_badge", lambda p: None)
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: {"active": False, "phase": "z"})
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)
    ne.refresh_template_copy_footer()


def test_refresh_template_copy_footer_without_optional_refs(ne_env, monkeypatch):
    ne._copy_footer_refs.update({"container": FakeEl()})
    monkeypatch.setattr(ne, "_set_footer_item_tooltip", lambda *a: None)
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: {"active": True})
    monkeypatch.setattr(tcp, "format_template_copy_badge", lambda p: ("x", "info"))
    ne.refresh_template_copy_footer()


# --------------------------------------------------------------------------- polling the trim/copy badges

def test_poll_alert_trim_footer(ne_env, monkeypatch):
    seen = []
    monkeypatch.setattr(ne, "refresh_alert_trim_footer", lambda: seen.append("trim"))
    monkeypatch.setattr(ne, "refresh_template_copy_footer", lambda: seen.append("copy"))
    monkeypatch.setattr(ne, "_footer_progress_active", lambda: True)
    ne.poll_alert_trim_footer()
    assert seen == ["trim", "copy"]
    monkeypatch.setattr(ne, "_footer_progress_active", lambda: False)
    ne.poll_alert_trim_footer()  # no timer
    timer = SimpleNamespace(active=True)
    monkeypatch.setattr(ne, "_trim_footer_timer", timer)
    ne.poll_alert_trim_footer()
    assert timer.active is False

    class Dead:
        def __setattr__(self, k, v):
            raise RuntimeError("gone")

    monkeypatch.setattr(ne, "_trim_footer_timer", Dead())
    ne.poll_alert_trim_footer()
    assert seen == ["trim", "copy"]


def test_start_alert_trim_footer_poll(ne_env, monkeypatch):
    ne.start_alert_trim_footer_poll()
    assert ne._trim_footer_poll_started is True
    assert ne_env.sched.calls == [(0.25, ne.poll_alert_trim_footer, {"active": True})]
    timer = ne._trim_footer_timer
    timer.active = False
    ne.start_alert_trim_footer_poll()
    assert timer.active is True and len(ne_env.sched.calls) == 1

    class Dead:
        def __setattr__(self, k, v):
            raise RuntimeError("gone")

    monkeypatch.setattr(ne, "_trim_footer_timer", Dead())
    ne.start_alert_trim_footer_poll()
    monkeypatch.setattr(ne, "_trim_footer_timer", None)
    ne.start_alert_trim_footer_poll()  # already started once: no second timer
    assert len(ne_env.sched.calls) == 1


# --------------------------------------------------------------------------- service footer refresh

def _service_refs():
    return {"container": FakeEl(), "dot": FakeEl(), "badge": FakeEl(), "badge_wrap": FakeEl()}


def test_refresh_service_status_footer_no_container(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "iter_service_footer_entries", lambda: pytest.fail("not called"))
    ne.refresh_service_status_footer()


def test_refresh_service_status_footer_disabled_only_updates_progress(ne_env, monkeypatch):
    footer = FakeEl()
    monkeypatch.setattr(ne, "_footer_container", footer)
    monkeypatch.setattr(ne, "_status_footer_enabled", lambda: False)
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: False)
    monkeypatch.setattr(ne, "current_footer_style_classes", lambda: "service-status-footer--full-stacked")
    seen = []
    monkeypatch.setattr(ne, "refresh_alert_trim_footer", lambda: seen.append("trim"))
    monkeypatch.setattr(ne, "refresh_template_copy_footer", lambda: seen.append("copy"))
    monkeypatch.setattr(ne, "iter_service_footer_entries", lambda: pytest.fail("disabled"))
    ne.refresh_service_status_footer()
    assert "service-status-footer--hidden" in footer.cls and seen == ["trim", "copy"]


def test_refresh_service_status_footer_updates_badges(ne_env, monkeypatch):
    footer = FakeEl()
    monkeypatch.setattr(ne, "_footer_container", footer)
    monkeypatch.setattr(ne, "_status_footer_enabled", lambda: True)
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)
    monkeypatch.setattr(ne, "current_footer_style_classes", lambda: "service-status-footer--minimal")
    monkeypatch.setattr(ne, "refresh_alert_trim_footer", lambda: None)
    monkeypatch.setattr(ne, "refresh_template_copy_footer", lambda: None)
    tips = []
    monkeypatch.setattr(ne, "_set_footer_item_tooltip", lambda c, r, t: tips.append(t))
    refs = {k: _service_refs() for k in ("internet", "twitch", "obs")}
    refs["obs"]["container"].cls.add("service-status-item--hidden")
    refs["twitch"]["badge_wrap"] = None
    ne._footer_item_refs.update(refs)
    ne._footer_summary_refs["chip"] = FakeEl()
    monkeypatch.setattr(ne, "iter_service_footer_entries", lambda: [
        _entry("internet", "Online", "Online", "success"), _entry("twitch", "Error", "Error", "error")])
    ne.refresh_service_status_footer()
    assert "service-status-footer--minimal" in footer.cls and "service-status-footer--hidden" not in footer.cls
    assert refs["twitch"]["badge"].content == "Error"
    assert refs["twitch"]["dot"].replaced == "service-status-dot error"
    assert "service-status-tier-error" in refs["twitch"]["container"].cls
    assert refs["internet"]["badge_wrap"].replaced == "service-status-badge success"
    assert "service-status-item--hidden" in refs["obs"]["container"].cls
    assert tips == ["Online", "Error"]
    assert "1 needs attention" in ne._footer_summary_refs["chip"].content


def test_refresh_service_status_footer_element_errors_swallowed(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_footer_container", FakeEl())
    monkeypatch.setattr(ne, "_status_footer_enabled", lambda: True)
    monkeypatch.setattr(ne, "refresh_alert_trim_footer", lambda: None)
    monkeypatch.setattr(ne, "refresh_template_copy_footer", lambda: None)
    bad = {"container": FakeEl(fail=True), "dot": FakeEl(fail=True), "badge": FakeEl(fail=True), "badge_wrap": None}
    ne._footer_item_refs.update({"twitch": bad, "obs": dict(bad)})
    monkeypatch.setattr(ne, "iter_service_footer_entries", lambda: [_entry("twitch")])
    ne.refresh_service_status_footer()


def test_refresh_footer_summary(ne_env):
    ne._refresh_footer_summary(["error"])  # no chip
    chip = FakeEl()
    ne._footer_summary_refs["chip"] = chip
    ne._refresh_footer_summary(["success"])
    assert "check_circle" in chip.content and "All connected" in chip.content
    assert "service-status-tier-success" in chip.cls
    chip.content = "untouched"
    ne._refresh_footer_summary(["success"])
    assert chip.content == "untouched"
    ne._refresh_footer_summary(["info"])
    assert ">sync<" in chip.content and "Connecting" in chip.content
    ne._refresh_footer_summary(["warning"])
    assert ">error<" in chip.content and "service-status-tier-warning" in chip.cls
    ne._footer_summary_refs["chip"] = FakeEl(fail=True)
    ne._refresh_footer_summary(["error", "error"])  # new content, failing chip: swallowed


# --------------------------------------------------------------------------- building the footer in the UI

def test_create_service_status_footer_builds_and_clicks(ne_env, monkeypatch, navs):
    seen = []
    monkeypatch.setattr(ne, "start_alert_trim_footer_poll", lambda: seen.append("poll"))
    monkeypatch.setattr(ne, "schedule_service_status_probe", lambda force: seen.append(("probe", force)))
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": False})
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: {"active": False})
    monkeypatch.setattr(ne, "_footer_should_stay_open", lambda: True)
    monkeypatch.setattr(ne, "current_footer_style_classes", lambda: "service-status-footer--full-pills")

    def build():
        ne.create_service_status_footer()

    async def check(user):
        assert seen == ["poll", ("probe", True)]
        assert "service-status-footer--full-pills" in ne._footer_container.classes
        assert set(ne._footer_item_refs) == set(ne._SERVICE_KEYS)
        assert ne._trim_footer_refs["container"] is not None and ne._copy_footer_refs["name"] is not None
        await user.should_see("Overlay Server")
        await user.should_see("All connected")
        UserInteraction(user, {ne._footer_item_refs["obs"]["container"]}, None).click()
        UserInteraction(user, {ne._footer_item_refs["internet"]["container"]}, None).click()
        UserInteraction(user, {ne._trim_footer_refs["container"]}, None).click()
        UserInteraction(user, {ne._copy_footer_refs["container"]}, None).click()
        assert navs == [("settings", "OBS", "Settings"), ("settings", "App Settings", "Settings"),
                        ("main", "Source Settings")]
        # A real refresh against the built elements
        monkeypatch.setattr(ne, "iter_service_footer_entries", lambda: [_entry("twitch", "Disconnected",
                                                                                "Disconnected", "muted")])
        monkeypatch.setattr(ne, "_status_footer_enabled", lambda: True)
        ne.refresh_service_status_footer()
        assert ne._footer_item_refs["twitch"]["badge"].content == "Disconnected"
        assert "service-status-item--hidden" not in ne._footer_item_refs["twitch"]["container"].classes
        assert "service-status-item--hidden" in ne._footer_item_refs["obs"]["container"].classes

    run_ui(build, check)


def test_footer_tooltip_set_when_refreshed_from_app_timer(ne_env, monkeypatch):
    import asyncio

    from nicegui import app

    monkeypatch.setattr(ne, "start_alert_trim_footer_poll", lambda: None)
    monkeypatch.setattr(ne, "schedule_service_status_probe", lambda force: None)
    monkeypatch.setattr(alertutils, "get_alert_storage_trim_progress", lambda: {"active": False})
    monkeypatch.setattr(tcp, "get_template_copy_progress", lambda: {"active": False})
    monkeypatch.setattr(ne, "_status_footer_enabled", lambda: True)
    monkeypatch.setattr(ne, "iter_service_footer_entries",
                        lambda: [_entry("twitch", "Disconnected", "Disconnected", "muted")])

    def build():
        ne.create_service_status_footer()

    async def check(user):
        # The real app refreshes the footer from ui_timer.app_schedule (an app.timer).
        app.timer(0.01, ne.refresh_service_status_footer, once=True)
        await asyncio.sleep(0.2)
        assert ne._footer_item_refs["twitch"]["badge"].content == "Disconnected"  # the refresh ran
        refs = ne._footer_item_refs["twitch"]
        assert refs.get("tooltip_text") == "Disconnected"
        assert refs["tooltip"].parent_slot.parent is refs["container"]  # hover target is the badge item

    run_ui(build, check)


# --------------------------------------------------------------------------- service watcher

def test_poll_service_status_changes_notifies(ne_env, monkeypatch):
    refreshed, notes = [], []
    monkeypatch.setattr(ne, "refresh_service_status_footer", lambda: refreshed.append(1))
    monkeypatch.setattr(ne, "_footer_probe_refresh_pending", True)
    monkeypatch.setattr(ne, "_service_configured", lambda key: key == "obs")

    def label(key):
        if key == "obs":
            raise RuntimeError("x")
        return "Online"

    monkeypatch.setattr(ne, "_service_status_label", label)
    observed, forgot = [], []
    tracker = SimpleNamespace(
        forget=forgot.append,
        observe=lambda samples: observed.append(samples) or [
            Notice("twitch", "problem", "warning", "Twitch disconnected", "m", "h", source="twitch",
                   settings_tab="Twitch", dedupe_key="svc:twitch:problem"),
            Notice("internet", "offline", "warning", "You're offline", source="app", dedupe_key="svc:internet:offline"),
        ])
    monkeypatch.setattr(ne, "_service_alerts", tracker)
    monkeypatch.setattr(ne, "notify", lambda msg, **kw: notes.append((msg, kw)))
    ne.poll_service_status_changes()
    assert observed == [{"internet": "Online", "twitch": "Online"}]
    assert "psn" in forgot and "obs" not in forgot and "twitch" not in forgot
    assert notes[0][1]["actions"] == [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": "Twitch"}]
    assert notes[0][1]["dedupe_cooldown_sec"] == ne._SERVICE_STATUS_DEDUPE_COOLDOWN_SEC
    assert notes[0][1]["title"] == "Twitch disconnected" and notes[0][1]["hint"] == "h"
    assert notes[1][1]["actions"] is None
    assert refreshed == [1, 1] and ne._footer_probe_refresh_pending is False


def test_poll_service_connection_changes_alias(monkeypatch):
    seen = []
    monkeypatch.setattr(ne, "poll_service_status_changes", lambda: seen.append(1))
    ne.poll_service_connection_changes()
    assert seen == [1]


def test_poll_after_connectivity_refresh(ne_env, monkeypatch):
    monkeypatch.setattr(ne, "_connectivity_repoll_queued", True)
    seen = []
    monkeypatch.setattr(ne, "poll_service_status_changes", lambda: seen.append(1))
    ne._poll_after_connectivity_refresh()
    assert seen == [1] and ne._connectivity_repoll_queued is False

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ne, "poll_service_status_changes", boom)
    ne._poll_after_connectivity_refresh()


def test_on_connectivity_refreshed(ne_env, monkeypatch):
    queued = []
    monkeypatch.setattr(ne, "_run_on_ui_loop", queued.append)
    ne._on_connectivity_refreshed()  # watcher not started
    assert queued == []
    monkeypatch.setattr(ne, "_service_watcher_started", True)
    ne._on_connectivity_refreshed()
    ne._on_connectivity_refreshed()  # already queued
    assert queued == [ne._poll_after_connectivity_refresh] and ne._connectivity_repoll_queued is True


def test_start_service_watcher_timer(ne_env, monkeypatch):
    from modules import connection_monitor as cm

    listeners, seen = [], []
    monkeypatch.setattr(cm, "add_background_refresh_listener", listeners.append)
    monkeypatch.setattr(ne, "start_alert_trim_footer_poll", lambda: seen.append("poll"))
    monkeypatch.setattr(ne, "flush_pending_toasts", lambda: seen.append("flush"))
    monkeypatch.setattr(ne, "_ensure_toast_flush_timer", lambda: seen.append("timer"))
    ne.start_service_watcher_timer()
    assert listeners == [ne._on_connectivity_refreshed]
    assert ne_env.sched.calls == [(2.0, ne.poll_service_status_changes, {"active": True})]
    assert seen == ["poll", "flush"]
    ne.start_service_watcher_timer()
    assert len(ne_env.sched.calls) == 1


def test_start_service_watcher_timer_listener_failure_and_pending(ne_env, monkeypatch):
    from modules import connection_monitor as cm

    def boom(cb):
        raise RuntimeError("x")

    monkeypatch.setattr(cm, "add_background_refresh_listener", boom)
    seen = []
    monkeypatch.setattr(ne, "start_alert_trim_footer_poll", lambda: None)
    monkeypatch.setattr(ne, "flush_pending_toasts", lambda: ne._pending_toasts.append(("x", {})))
    monkeypatch.setattr(ne, "_ensure_toast_flush_timer", lambda: seen.append("timer"))
    ne.start_service_watcher_timer()
    assert seen == ["timer"] and ne._service_watcher_started is True


# --------------------------------------------------------------------------- entry actions / toast events / assets

def test_run_entry_action(ne_env, monkeypatch):
    ran = []
    monkeypatch.setattr(ne, "run_action", lambda a, message, entry: ran.append((a, message, entry["id"])))
    ne._history.append({"id": "e", "message": "m", "actions": [{"kind": "copy"}]})
    ne.run_entry_action("missing", 0)
    ne.run_entry_action("e", 5)
    ne.run_entry_action("e", -1)
    ne.run_entry_action("e", 0)
    assert ran == [({"kind": "copy"}, "m", "e")]
    fn_action = {"kind": "call", "fn": lambda: None}
    ne._call_actions["e"] = [fn_action]
    ne.run_entry_action("e", 0)
    assert ran[-1][0] is fn_action
    ne._history.append({"id": "bare"})
    ne.run_entry_action("bare", 0)
    assert len(ran) == 2


def test_on_toast_action_event(monkeypatch):
    seen = []
    monkeypatch.setattr(ne, "run_entry_action", lambda i, idx: seen.append((i, idx)))
    ne._on_toast_action_event(SimpleNamespace(args={"id": 7, "index": "2"}))
    ne._on_toast_action_event(SimpleNamespace(args={"id": "x"}))
    ne._on_toast_action_event(SimpleNamespace(args={"id": "x", "index": "bad"}))
    ne._on_toast_action_event(SimpleNamespace(args=None))
    ne._on_toast_action_event(SimpleNamespace(args=["not", "dict"]))
    ne._on_toast_action_event(object())
    assert seen == [("7", 2), ("x", 0)]


def test_inject_notification_ui_assets(ne_env, monkeypatch):
    heads, handlers = [], []
    monkeypatch.setattr(ui, "add_head_html", lambda html, shared=False: heads.append(shared))
    monkeypatch.setattr(ui, "on", lambda event, handler: handlers.append((event, handler)))
    monkeypatch.setattr(ne, "_toast_head_html", lambda: "<script></script>")
    ne.inject_notification_ui_assets()
    ne.inject_notification_ui_assets()
    assert heads == [True] and handlers == [("mycelian-toast-action", ne._on_toast_action_event)]
    monkeypatch.setattr(ne, "_notification_leave_script_injected", False)
    ne.inject_notification_ui_assets()
    assert heads == [True, True] and len(handlers) == 1  # handler registered once


def test_create_notification_tray_button_delegates(monkeypatch):
    from modules import notify_center

    seen = []
    monkeypatch.setattr(notify_center, "create_notification_tray_button", lambda: seen.append(1))
    ne.create_notification_tray_button()
    assert seen == [1]

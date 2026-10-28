# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alertsettings.py, part 1: UI state, notices, unsaved-change
tracking, the tab-switch / selection guards and the unsaved-changes prompt."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tests"))

import _alertsettings_env as E  # noqa: E402
from nicegui import ui  # noqa: E402
from ui_sim import run_ui  # noqa: E402

from modules import web_engine  # noqa: E402
from modules.uiwindows import alertsettings as al  # noqa: E402
from modules.uiwindows import media_preview  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = E.install(monkeypatch, tmp_path)
    yield e
    E.restore()


def run(build_types, fn):
    """Build alert panels, then run ``fn(user)`` inside the client context."""

    def build():
        for t in build_types:
            if t == "points":
                al.create_points_alert_panel()
            else:
                al.create_alert_type_panel(t)

    async def check(user):
        with user:
            result = fn(user)
            if hasattr(result, "__await__"):
                await result

    run_ui(build, check)


# ======================================================================= state object

def test_alert_settings_state_defaults_and_accessors():
    s = al.AlertSettingsState()
    assert s.active_tab_name == "Bits" and s.current_tab is None
    assert s.suppress_selection_handler is False
    els = s.get_elements("bits")
    assert els == {} and s.get_elements("bits") is els
    orig = s.get_original_values("bits")
    assert orig == {} and s.get_original_values("bits") is orig
    s.set_current_tab("subs")
    assert s.current_tab == "subs"
    s.suppress_selection_handler_depth = 2
    assert s.suppress_selection_handler is True


def test_suppress_alert_selection_handler_nests(env):
    with al.suppress_alert_selection_handler():
        with al.suppress_alert_selection_handler():
            assert env.state.suppress_selection_handler_depth == 2
        assert env.state.suppress_selection_handler
    assert env.state.suppress_selection_handler_depth == 0
    with pytest.raises(RuntimeError):
        with al.suppress_alert_selection_handler():
            raise RuntimeError("x")
    assert env.state.suppress_selection_handler_depth == 0


def test_ensure_lazy_alert_panel(env):
    calls = []
    al._LAZY_ALERT_PANEL_BUILDERS["Raids"] = lambda: calls.append(1)
    al._ensure_lazy_alert_panel("Raids")
    al._ensure_lazy_alert_panel("Raids")
    al._ensure_lazy_alert_panel("Nope")
    assert calls == [1]


def test_tab_type_maps():
    assert al._ALERT_TYPE_TO_TAB["points"] == "Channel Points"
    assert set(al._ALERT_TYPE_TO_TAB) == set(al.ALERT_TAB_TO_TYPE.values())


def test_alerts_nav(monkeypatch):
    seen = []
    monkeypatch.setattr(al, "nav_alerts", lambda tab: seen.append(tab) or [tab])
    assert al._alerts_nav("raids") == ["Raids"]
    assert al._alerts_nav("unknown") == ["Bits"]


def test_alert_name(env, monkeypatch):
    assert al._alert_name("bits", "bits100") == "100 Bits"
    monkeypatch.setattr(env.mgr, "get_display_name", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    assert al._alert_name("bits", "bits100") == "bits100"


def test_alerts_paused_and_overlay_running(env, monkeypatch):
    assert al._alerts_paused() is False
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    assert al._alerts_paused() is True
    monkeypatch.delattr(web_engine, "ALERTS_PAUSED")
    assert al._alerts_paused() is False
    assert al._overlay_running() is False
    monkeypatch.setattr(web_engine, "web_engine_instance", object())
    assert al._overlay_running() is True
    monkeypatch.delattr(web_engine, "web_engine_instance")
    assert al._overlay_running() is False


def test_notify_write_failed(env):
    env.mgr.last_write_error = "unreadable"
    al._notify_write_failed("T", "Msg.", "subs", "more")
    kw = env.last()
    assert kw["title"] == "T" and kw["type"] == "error"
    assert "couldn't read your saved alerts" in kw["message"]
    assert kw["details"] == "unreadable\nmore"
    env.mgr.last_write_error = ""
    al._notify_write_failed("T", "Msg.", "subs")
    assert env.last()["message"] == "Msg." and env.last()["details"] is None


def test_notify_tab_not_ready(env):
    al._notify_tab_not_ready("raids")
    assert env.last()["dedupe_key"] == "alerts:not_ready:raids"


def test_set_resub_fallback(env):
    al._set_resub_fallback(True)
    assert env.notices == [] and env.mgr.get_resub_fallback_enabled() is True
    env.db.write_fail.add("Alerts/Settings/resub_fallback_enabled")
    al._set_resub_fallback(False)
    assert env.titles() == ["Couldn't save the Resub Fallback setting"]


# ======================================================================= dirty tracking

@pytest.mark.parametrize("orig,cur,expected", [
    (1, 1, False), (None, "", False), ("", None, False), (1, 1.0, False), (1, 2, True),
    (True, 1, False), (True, 2, True), ("a", "b", True), (None, 0, True), (3.5, 3.50, False),
])
def test_alert_values_differ(orig, cur, expected):
    assert al._alert_values_differ(orig, cur) is expected


def test_alert_values_differ_float_error():
    class Weird(float):
        def __float__(self):
            raise ValueError("no")

    assert al._alert_values_differ(Weird(1.0), 2) is True


def test_form_values_differ_and_dirty(env):
    els = env.el("bits")
    assert al._alert_form_values_differ("bits") is False  # no originals
    els.update({
        "duration_input": E.FakeEl(3.0), "alert_select": E.FakeEl("x"), "btn": E.Button(1),
        "noval": object(), "none": None, "extra": E.FakeEl("new field"),
    })
    env.state.get_original_values("bits").update({"duration_input": 3.0, "alert_select": "y", "btn": 2})
    assert al._alert_form_values_differ("bits") is False
    assert al.alert_form_is_dirty("bits") is False
    els["duration_input"].value = 4
    assert al._alert_form_values_differ("bits") is True
    els["duration_input"].value = 3
    env.state.dirty_tabs.add("bits")
    assert al.alert_form_is_dirty("bits") is True


def test_alerts_have_unsaved_changes(env):
    assert al.alerts_have_unsaved_changes() is False
    env.state.dirty_tabs.add("raids")
    assert al.alerts_have_unsaved_changes() is True
    env.state.dirty_tabs.clear()
    env.state.active_tab_name = "Follows"
    env.state.dirty_tabs.add("follows")
    assert al.alerts_have_unsaved_changes() is True


def test_save_active_alert(env, monkeypatch):
    done = []
    env.state.active_tab_name = "Unknown"
    al.save_active_alert(done.append)
    assert done == [True]
    calls = []
    monkeypatch.setattr(al, "_save_alert_type", lambda t, cb: calls.append(t) or cb("ok"))
    env.state.active_tab_name = "Raids"
    al.save_active_alert(done.append)
    assert calls == ["raids"] and done[-1] == "ok"


def test_discard_active_alert(env, monkeypatch):
    calls = []
    monkeypatch.setattr(al, "_discard_alert_form", calls.append)
    env.state.active_tab_name = None
    al.discard_active_alert()
    env.state.active_tab_name = "Donations"
    al.discard_active_alert()
    assert calls == ["donations"]


def test_refresh_alert_dirty_label(env):
    al._refresh_alert_dirty_label("bits")  # no label: nothing to do
    label = E.FakeEl()
    env.el("bits")["unsaved_label"] = label
    env.state.dirty_tabs.add("bits")
    al._refresh_alert_dirty_label("bits")
    assert label.visible is True
    env.state.dirty_tabs.clear()
    al._refresh_alert_dirty_label("bits")
    assert label.visible is False

    class Broken(E.FakeEl):
        def __setattr__(self, k, v):
            if k == "visible" and hasattr(self, "text"):
                raise RuntimeError("deleted element")
            super().__setattr__(k, v)

    env.el("bits")["unsaved_label"] = Broken()
    al._refresh_alert_dirty_label("bits")  # logged, not raised


def test_mount_unsaved_label_is_hidden(env):
    def fn(user):
        label = env.el("bits")["unsaved_label"]
        assert label.visible is False
        assert "Unsaved changes" in label.content

    run(["bits"], fn)


def test_remember_loaded_alert(env):
    al._remember_loaded_alert("bits")
    assert env.state.loaded_alert_ids == {}
    env.el("bits")["alert_select"] = E.FakeEl("bits5")
    al._remember_loaded_alert("bits")
    assert env.state.loaded_alert_ids == {"bits": "bits5"}


def test_resolve_alert_tab_name(monkeypatch):
    from modules import ui_tab_transitions

    assert al._resolve_alert_tab_name(SimpleNamespace(value="Raids")) == "Raids"
    assert al._resolve_alert_tab_name(SimpleNamespace(value=None, args="Follows")) == "Follows"
    assert al._resolve_alert_tab_name(SimpleNamespace(args={"value": "Bits"})) == "Bits"
    assert al._resolve_alert_tab_name(SimpleNamespace()) == ""
    monkeypatch.setattr(ui_tab_transitions, "_tab_label", lambda tab: "From Tab")
    assert al._resolve_alert_tab_name(SimpleNamespace(value=object())) == "From Tab"


def test_activate_alert_tab(env, monkeypatch):
    inits, builds = [], []
    monkeypatch.setattr(al, "initialize_tab_values", inits.append)
    al._activate_alert_tab("")
    assert inits == []
    tabs = E.FakeEl("Bits")
    env.state.tabs_component = tabs
    al._LAZY_ALERT_PANEL_BUILDERS["Raids"] = lambda: builds.append(env.state.tab_switch_guard)
    al._activate_alert_tab("Raids")
    assert builds == [False] and tabs.value == "Raids"
    assert env.state.active_tab_name == "Raids" and env.state.tab_switch_guard is False
    assert inits == ["Raids"]
    env.state.tabs_component = None
    al._activate_alert_tab("Follows")
    assert inits[-1] == "Follows"


@pytest.fixture
def form_spy(env, monkeypatch):
    calls = []
    for name in ("load_point_reward_settings", "set_default_values_for_new_point_reward", "store_original_values",
                 "clear_changed_styling", "load_alert_settings", "set_default_values_for_new_alert"):
        monkeypatch.setattr(al, name, lambda *a, _n=name: calls.append((_n,) + a))
    return calls


@pytest.mark.parametrize("alert_type,value,expected", [
    ("points", "reward-123", [("load_point_reward_settings", "points", "reward-123")]),
    ("points", "loading", [("set_default_values_for_new_point_reward", "points"),
                           ("store_original_values", "points"), ("clear_changed_styling", "points")]),
    ("points", None, [("set_default_values_for_new_point_reward", "points"),
                      ("store_original_values", "points"), ("clear_changed_styling", "points")]),
    ("bits", "bits5", [("load_alert_settings", "bits", "bits5")]),
    ("bits", "new", [("set_default_values_for_new_alert", "bits"), ("store_original_values", "bits"),
                     ("clear_changed_styling", "bits")]),
])
def test_discard_alert_form(env, form_spy, alert_type, value, expected):
    if value is not None:
        env.el(alert_type)["alert_select"] = E.FakeEl(value)
    env.state.dirty_tabs.add(alert_type)
    al._discard_alert_form(alert_type)
    assert form_spy == expected
    assert alert_type not in env.state.dirty_tabs


def test_save_alert_type(env, monkeypatch):
    done = []
    monkeypatch.setattr(al, "save_point_alert", lambda on_complete: on_complete("points-done"))
    al._save_alert_type("points", done.append)
    assert done == ["points-done"]
    monkeypatch.setattr(al, "save_alert", lambda t: None)
    al._save_alert_type("bits", done.append)
    assert done[-1] is True
    env.state.dirty_tabs.add("bits")
    al._save_alert_type("bits", done.append)
    assert done[-1] is False
    monkeypatch.setattr(al, "save_alert", lambda t: (_ for _ in ()).throw(RuntimeError()))
    al._save_alert_type("raids", done.append)
    assert done[-1] is False


# ======================================================================= unsaved prompt dialog

def test_prompt_save_is_busy_until_finished(env, monkeypatch):
    log = []
    pending = {}
    monkeypatch.setattr(al, "_save_alert_type", lambda t, cb: pending.setdefault("cb", cb))

    def fn(user):
        env.state.unsaved_prompt_open = True
        al._prompt_unsaved_alert("bits", "Save first?", on_discard=lambda: log.append("discard"),
                                 on_saved=lambda: log.append("saved"))
        assert user.find("Unsaved changes").elements and user.find("Save first?").elements
        user.find("Save and switch").click()
        assert "cb" in pending
        # While saving, the other buttons and a second save do nothing.
        user.find("Stay").click()
        user.find("Discard and switch").click()
        user.find("Save and switch").click()
        assert log == [] and env.state.unsaved_prompt_open is True
        pending["cb"](False)  # save failed: dialog stays, buttons work again
        assert log == []
        pending.clear()
        user.find("Save and switch").click()
        pending["cb"](True)
        assert log == ["saved"]

    run([], fn)


def test_prompt_discard(env):
    log = []

    def fn(user):
        al._prompt_unsaved_alert("bits", "Again?", on_discard=lambda: log.append("discard"),
                                 on_saved=lambda: log.append("saved"))
        user.find("Discard and switch").click()
        assert log == ["discard"]

    run([], fn)


def test_prompt_stay(env):
    log = []

    def fn(user):
        env.state.unsaved_prompt_open = True
        al._prompt_unsaved_alert("bits", "Keep editing?", on_discard=lambda: log.append("x"), on_saved=lambda: log.append("y"))
        user.find("Stay").click()
        assert env.state.unsaved_prompt_open is False and log == []

    run([], fn)


# ======================================================================= tab switching

def test_request_tab_change_guards(env, monkeypatch):
    acts = []
    monkeypatch.setattr(al, "_activate_alert_tab", acts.append)
    env.state.tab_switch_guard = True
    al.request_alert_tab_change("Raids")
    env.state.tab_switch_guard = False
    tabs = E.FakeEl("Raids")
    env.state.tabs_component = tabs
    env.state.unsaved_prompt_open = True
    al.request_alert_tab_change("Raids")  # prompt open: snap the tabs back
    assert tabs.value == "Bits" and env.state.tab_switch_guard is False
    tabs.value = "Bits"
    al.request_alert_tab_change("Bits")  # same as active: nothing
    env.state.tabs_component = None
    al.request_alert_tab_change("Raids")
    env.state.unsaved_prompt_open = False
    al.request_alert_tab_change("")
    al.request_alert_tab_change("Bits")
    assert acts == []
    al.request_alert_tab_change("Raids")
    assert acts == ["Raids"]


def test_request_tab_change_dirty_discard(env, monkeypatch):
    acts, discards, prompts = [], [], []
    monkeypatch.setattr(al, "_activate_alert_tab", lambda n: acts.append((n, env.state.ignore_field_dirty)))
    monkeypatch.setattr(al, "_discard_alert_form", discards.append)
    monkeypatch.setattr(al, "_prompt_unsaved_alert",
                        lambda t, msg, on_discard, on_saved: prompts.append((t, on_discard, on_saved)))
    tabs = E.FakeEl("Raids")
    env.state.tabs_component = tabs
    env.state.dirty_tabs.add("bits")
    al.request_alert_tab_change("Raids")
    assert tabs.value == "Bits" and env.state.unsaved_prompt_open is True
    (t, on_discard, on_saved), = prompts
    assert t == "bits" and acts == []
    on_discard()
    assert discards == ["bits"] and "bits" not in env.state.dirty_tabs
    assert acts == [("Raids", True)]
    assert env.state.unsaved_prompt_open is False and env.state.ignore_field_dirty is False
    assert env.state.tab_switch_guard is False


def test_request_tab_change_dirty_save(env, monkeypatch):
    acts, prompts = [], []
    monkeypatch.setattr(al, "_activate_alert_tab", acts.append)
    monkeypatch.setattr(al, "_prompt_unsaved_alert",
                        lambda t, msg, on_discard, on_saved: prompts.append(on_saved))
    env.state.dirty_tabs.add("bits")
    al.request_alert_tab_change("Follows")
    prompts[0]()
    assert acts == ["Follows"] and env.state.unsaved_prompt_open is False


# ======================================================================= selection guard

def test_guard_alert_selection(env):
    assert al._guard_alert_selection("bits", "bits5") is False  # never loaded
    env.state.loaded_alert_ids["bits"] = "bits1"
    assert al._guard_alert_selection("bits", "bits1") is False  # same
    assert al._guard_alert_selection("bits", "bits5") is False  # not dirty
    env.state.dirty_tabs.add("bits")
    assert al._guard_alert_selection("bits", "bits5") is True  # no select element
    seen = []

    class Sel(E.FakeEl):
        def __setattr__(self, k, v):
            if k == "value":
                seen.append((v, al.alert_settings_state.suppress_selection_handler))
            super().__setattr__(k, v)

    env.el("bits")["alert_select"] = Sel("bits5")
    assert al._guard_alert_selection("bits", "bits5") is True
    assert seen[-1] == ("bits1", True)


def test_discard_then_select_and_programmatic(env, monkeypatch):
    discards, handled = [], []
    monkeypatch.setattr(al, "_discard_alert_form", lambda t: discards.append((t, env.state.ignore_field_dirty)))
    sel = E.FakeEl("bits1")
    env.el("bits")["alert_select"] = sel
    al._discard_then_select("bits", "bits9", lambda e, t: handled.append((e.value, t)))
    assert discards == [("bits", True)] and env.state.ignore_field_dirty is False
    assert sel.value == "bits9" and handled == [("bits9", "bits")]
    assert env.el("raids") == {}
    al._select_alert_programmatically("raids", "raids2", lambda e, t: handled.append((e.value, t)))
    assert handled[-1] == ("raids2", "raids")


def test_discard_then_select_resets_flag_on_error(env, monkeypatch):
    monkeypatch.setattr(al, "_discard_alert_form", lambda t: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        al._discard_then_select("bits", "x", lambda e, t: None)
    assert env.state.ignore_field_dirty is False


# ======================================================================= small helpers

def test_element_value():
    els = {"a": E.FakeEl(5), "b": E.FakeEl(None), "c": object()}
    assert al._element_value(els, "a") == 5
    assert al._element_value(els, "b", 7) == 7
    assert al._element_value(els, "c", 9) == 9
    assert al._element_value(els, "missing", "d") == "d"


def test_constants():
    assert "new" in al.POINTS_REWARD_SELECT_PLACEHOLDERS
    assert len(al.TWITCH_REWARD_UI_FIELD_NAMES) == 12
    assert "duration_input" in al._TEST_ALERT_REQUIRED_ELEMENTS
    assert al.TTS_SOURCE_OPTIONS["custom_message"] == "Custom Message"


def test_test_alert_button_calls_test_alert(env, monkeypatch):
    calls = []
    monkeypatch.setattr(al, "test_alert", calls.append)

    def fn(user):
        user.find("Test Alert").click()
        assert calls == ["bits"]

    run(["bits"], fn)


def test_update_volume_value(env):
    vol, label = E.FakeEl(10), E.FakeEl()
    env.el("bits").update(volume_input=vol, volume_value_label=label)
    al.update_volume_value("bits", 55)
    assert vol.value == 55 and label.text == "55%"
    env.el("raids").update(volume_input=None, volume_value_label=None)
    al.update_volume_value("raids", 1)

    class Bad(E.FakeEl):
        def set_text(self, t):
            raise RuntimeError("x")

    env.el("bits")["volume_value_label"] = Bad()
    al.update_volume_value("bits", 3)  # error logged


def test_refresh_tts_preview(env, monkeypatch):
    data = []
    monkeypatch.setattr(media_preview, "set_button_data", lambda b, **kw: data.append(kw))
    al._refresh_tts_preview("bits")  # no button yet
    assert data == []
    button = E.FakeEl()
    al._view("bits")["tts_preview"] = button
    env.el("bits").update(tts_source_select=E.FakeEl("custom_message"), tts_custom_message_input=E.FakeEl("  "))
    al.update_tts_custom_message_visibility("bits")
    assert data[-1]["texts"] == [] and button.enabled is False
    env.el("bits")["tts_custom_message_input"].value = "Thanks!"
    al._refresh_tts_preview("bits")
    assert data[-1] == {"texts": ["Thanks!"], "rate": 1.0, "pitch": 1.0, "tts_vol": 1.0}
    assert button.enabled is True


@pytest.mark.parametrize("value,expected", [(40, 40), (None, 100), ("loud", 100)])
def test_sync_preview_volume(env, monkeypatch, value, expected):
    data = []
    monkeypatch.setattr(media_preview, "set_button_data", lambda b, **kw: data.append((b, kw)))
    env.el("bits")["volume_input"] = E.FakeEl(value)
    al._view("bits")["preview_buttons"] = ["b1", "b2"]
    al._sync_preview_volume("bits")
    assert data == [("b1", {"vol": expected}), ("b2", {"vol": expected})]


def test_sync_preview_volume_no_slider(env, monkeypatch):
    data = []
    monkeypatch.setattr(media_preview, "set_button_data", lambda b, **kw: data.append(kw))
    al._view("bits")["preview_buttons"] = ["b"]
    al._sync_preview_volume("bits")
    assert data == [{"vol": 100}]

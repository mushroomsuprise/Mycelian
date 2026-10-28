# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alertsettings.py, part 3: tab initialization, alert selection,
defaults, loading an alert into the form, Browse and Test Alert."""

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

from modules import web_engine  # noqa: E402
from modules.uiwindows import alertsettings as al  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = E.install(monkeypatch, tmp_path)
    yield e
    E.restore()


# ======================================================================= initialize_tab_values

@pytest.mark.parametrize("tab,alert_type", list(al.ALERT_TAB_TO_TYPE.items()) + [("Bogus", "bits")])
def test_initialize_tab_values_sets_current_tab(env, monkeypatch, tab, alert_type):
    calls = []
    monkeypatch.setattr(al, "refresh_alert_dropdowns", lambda: calls.append("refresh"))
    monkeypatch.setattr(al, "load_twitch_point_rewards", lambda: calls.append("rewards"))
    al.initialize_tab_values(tab)
    assert env.state.current_tab == alert_type and calls == ["refresh"]
    assert env.state.suppress_selection_handler_depth == 0
    if alert_type == "points":
        env.run_scheduled()
        assert calls == ["refresh", "rewards"]


def test_initialize_tab_values_points_loader_error(env, monkeypatch):
    monkeypatch.setattr(al, "refresh_alert_dropdowns", lambda: None)
    monkeypatch.setattr(al, "load_twitch_point_rewards", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    al.initialize_tab_values("Channel Points")
    env.run_scheduled()  # logged, not raised


def test_initialize_tab_values_reloads_selected_alert(env, monkeypatch):
    loads = []
    monkeypatch.setattr(al, "refresh_alert_dropdowns", lambda: None)
    monkeypatch.setattr(al, "load_alert_settings", lambda *a, **k: loads.append((a, k)))
    E.make_form("raids", "raids10")
    al.initialize_tab_values("Raids")
    assert loads == [(("raids", "raids10"), {"quiet": True})]
    assert env.el("raids")["delete_btn"].visible is True
    monkeypatch.setattr(al, "load_alert_settings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    al.initialize_tab_values("Raids")  # reload error is logged
    env.el("raids")["alert_select"].value = "new"
    al.initialize_tab_values("Raids")


def test_initialize_tab_values_outer_error(env, monkeypatch):
    monkeypatch.setattr(al, "refresh_alert_dropdowns", lambda: (_ for _ in ()).throw(RuntimeError()))
    al.initialize_tab_values("Bits")
    assert env.state.suppress_selection_handler_depth == 0


# ======================================================================= handle_alert_selection

def test_handle_alert_selection_suppressed(env, monkeypatch):
    monkeypatch.setattr(al, "load_alert_settings", lambda *a: pytest.fail("no"))
    with al.suppress_alert_selection_handler():
        al.handle_alert_selection(SimpleNamespace(value="bits1"), "bits")


def test_handle_alert_selection_new_and_existing(env, monkeypatch):
    loads = []
    monkeypatch.setattr(al, "load_alert_settings", lambda t, v: loads.append(v))
    els = E.make_form("bits", "new")
    els["duration_input"].value = 9
    al.handle_alert_selection(SimpleNamespace(value="new"), "bits")
    assert els["duration_input"].value == 3.0 and els["amount_input"].value == 100
    assert env.state.get_original_values("bits")["duration_input"] == 3.0
    assert els["delete_btn"].visible is False
    els["alert_select"].value = "bits5"
    al.handle_alert_selection(SimpleNamespace(value="bits5"), "bits")
    assert loads == ["bits5"] and els["delete_btn"].visible is True
    assert els["range_toggle"].visible is True


def test_handle_alert_selection_fallback(env, monkeypatch):
    loads = []
    monkeypatch.setattr(al, "load_alert_settings", lambda t, v: loads.append(v))
    els = E.make_form("subs", "subs_fallback")
    al._view("subs")["trigger"] = E.FakeEl()
    al.handle_alert_selection(SimpleNamespace(value="subs_fallback"), "subs")
    assert loads == [] and els["amount_input"].value == 1  # defaults for a new fallback
    assert els["amount_input"].visible is False and al._view("subs")["trigger"].visible is False
    env.save("subs", "subs_fallback", {"duration": 4})
    al.handle_alert_selection(SimpleNamespace(value="subs_fallback"), "subs")
    assert loads == ["subs_fallback"]


def test_handle_alert_selection_error(env, monkeypatch):
    monkeypatch.setattr(al, "load_alert_settings", lambda t, v: (_ for _ in ()).throw(ValueError("bad")))
    al.handle_alert_selection(SimpleNamespace(value="bits1"), "bits")
    assert env.last()["title"] == "Couldn't open this alert"
    assert env.last()["details"] == "ValueError: bad"


def test_handle_alert_selection_guarded_prompt(env, monkeypatch):
    prompts, discards, handled = [], [], []
    monkeypatch.setattr(al, "_prompt_unsaved_alert", lambda t, m, on_discard, on_saved: prompts.append((on_discard, on_saved)))
    monkeypatch.setattr(al, "_discard_then_select", lambda t, v, h: discards.append((t, v, h)))
    monkeypatch.setattr(al, "_select_alert_programmatically", lambda t, v, h: handled.append((t, v, h)))
    E.make_form("bits", "bits1")
    env.state.loaded_alert_ids["bits"] = "bits1"
    env.state.dirty_tabs.add("bits")
    al.handle_alert_selection(SimpleNamespace(value="bits2"), "bits")
    on_discard, on_saved = prompts[0]
    on_discard()
    on_saved()
    assert discards == [("bits", "bits2", al.handle_alert_selection)]
    assert handled == [("bits", "bits2", al.handle_alert_selection)]


def test_notify_load_failed(env):
    al._notify_load_failed("raids", KeyError("k"))
    assert env.last()["dedupe_key"] == "alerts:load_failed"


def test_update_amount_inputs_visibility_tolerates_errors(env):
    class NoVisible(E.FakeEl):
        parent_slot = None

        def __setattr__(self, k, v):
            if k == "visible" and hasattr(self, "handlers"):
                raise RuntimeError("deleted")
            super().__setattr__(k, v)

    els = env.el("bits")
    els.update(amount_input=NoVisible(), min_input=NoVisible(), max_input=NoVisible(), range_toggle=NoVisible())
    al._update_amount_inputs_visibility("bits", True)  # every failure is swallowed
    els.clear()
    al._update_amount_inputs_visibility("bits", False)


# ======================================================================= defaults / clear

@pytest.mark.parametrize("alert_type,amount,low,high", [
    ("bits", 100, 100, 500), ("subs", 1, 1, 3), ("streaks", 2, 2, 5), ("giftsubs", 1, 1, 5),
    ("donations", 5, 5, 25), ("raids", 10, 10, 50),
])
def test_set_default_values_for_new_alert_amounts(env, alert_type, amount, low, high):
    els = E.make_form(alert_type)
    els["range_toggle"].value = True
    for name in E.FIELD_NAMES:
        els[name].value = "dirty"
    al.set_default_values_for_new_alert(alert_type)
    assert els["range_toggle"].value is False
    assert els["amount_input"].value == amount
    assert (els["min_input"].value, els["max_input"].value) == (low, high)
    assert els["duration_input"].value == 3.0 and els["tts_source_select"].value == "alert_message"
    assert els["volume_input"].value == 100 and els["volume_value_label"].text == "100%"
    assert els["gif_dir_input"].value == "" and els["randomized_extra_switch"].value is False


def test_set_default_values_for_follows(env):
    els = E.make_form("follows")
    al.set_default_values_for_new_alert("follows")
    assert "range_toggle" not in els and els["stackable_switch"].value is False


def test_clear_alert_inputs(env):
    els = E.make_form("raids")
    for name in E.FIELD_NAMES:
        els[name].value = "x"
    al.clear_alert_inputs("raids")
    assert els["duration_input"].value == 3.0 and els["primary_dir_input"].value == ""
    assert els["random_chance_input"].value == 0 and els["tts_custom_message_input"].value == ""
    assert els["volume_input"].value == 100


# ======================================================================= load_alert_settings

def test_load_alert_settings_missing_alert(env):
    al.load_alert_settings("bits", "bits9", quiet=True)
    assert env.notices == []
    al.load_alert_settings("bits", "bits9")
    assert env.last()["title"] == "That alert no longer exists"


def test_load_alert_settings_form_not_ready(env, monkeypatch):
    env.save("bits", "bits1", {})
    al.load_alert_settings("bits", "bits1", quiet=True)
    assert env.scheduled == []
    al.load_alert_settings("bits", "bits1")
    assert len(env.scheduled) == 1
    E.make_form("bits")
    env.run_scheduled()
    assert env.el("bits")["amount_input"].value == 1


FULL = {
    "duration": 6.5, "stackable": True, "fade_in": 100, "fade_out": 200, "volume": 70, "tts_enabled": True,
    "tts_source": None, "tts_custom_message": None, "audio_only": True, "single_audio_dir": "/a/",
    "single_audio_name": "s.mp3", "randomized": True, "randomized_dir": None, "randomized_chance": 30,
    "randomized_extra": True, "randomized_extra_dir": "/x/", "randomized_extra_chance": 5,
    "gif_dir": "/g/", "gif_name": None,
}


def test_load_alert_settings_exact(env):
    env.save("raids", "raids25", FULL)
    els = E.make_form("raids", "raids25")
    els["audio_only_switch"] = E.Switch(False)
    al.load_alert_settings("raids", "raids25")
    assert els["duration_input"].value == 6.5 and els["stackable_switch"].value is True
    assert (els["fade_in_input"].value, els["fade_out_input"].value) == (100, 200)
    assert els["volume_input"].value == 70 and els["volume_value_label"].text == "70%"
    assert els["tts_source_select"].value == "alert_message" and els["tts_custom_message_input"].value == ""
    assert els["audio_only_switch"].value is True
    assert els["primary_dir_input"].value == "/a/" and els["random_dir_input"].value == ""
    assert els["extra_dir_input"].value == "/x/" and els["gif_file_input"].value == ""
    assert els["range_toggle"].value is False and els["amount_input"].value == 25
    assert all(e.updates for n, e in els.items() if n in E.FIELD_NAMES)
    assert env.state.get_original_values("raids")["duration_input"] == 6.5
    assert els["delete_btn"].visible is True
    assert env.state.loaded_alert_ids["raids"] == "raids25"
    # the delayed re-render keeps the values
    env.run_scheduled()
    assert els["gif_dir_input"].value == "/g/"


def test_load_alert_settings_range_and_bad_ids(env):
    els = E.make_form("bits")
    env.save("bits", "bits10-20", {})
    al.load_alert_settings("bits", "bits10-20")
    assert els["range_toggle"].value is True and (els["min_input"].value, els["max_input"].value) == (10, 20)
    env.mgr._alert_state["bit_ranges"]["bitsa-b"] = {"duration": 1}
    al.load_alert_settings("bits", "bitsa-b")
    assert (els["min_input"].value, els["max_input"].value) == (0, 0)
    env.mgr._alert_state["bits"]["bitsxyz"] = {"duration": 1}
    al.load_alert_settings("bits", "bitsxyz")
    assert els["amount_input"].value == 0
    env.mgr._alert_state["bits"]["42"] = {"duration": 1}  # id without the type prefix
    al.load_alert_settings("bits", "42")
    assert els["amount_input"].value == 42


def test_load_alert_settings_fallback_hides_amount(env):
    els = E.make_form("subs")
    env.save("subs", "subs_fallback", {})
    al.load_alert_settings("subs", "subs_fallback")
    assert els["amount_input"].visible is False and els["range_toggle"].visible is False


def test_load_alert_settings_follows(env):
    els = E.make_form("follows")
    env.save("follows", "follows1", {"duration": 2})
    al.load_alert_settings("follows", "follows1")
    assert els["duration_input"].value == 2.0


def test_load_alert_settings_update_and_refresh_errors(env):
    class BadUpdate(E.FakeEl):
        def update(self):
            raise RuntimeError("gone")

    els = E.make_form("follows")
    els["gif_dir_input"] = BadUpdate("")
    env.save("follows", "follows1", {"gif_dir": "/g/"})
    al.load_alert_settings("follows", "follows1")  # update errors logged
    assert els["gif_dir_input"].value == "/g/"
    env.run_scheduled()  # refresh error logged


def test_load_alert_settings_error_notifies_unless_quiet(env):
    E.make_form("follows")
    env.save("follows", "follows1", {"duration": "not a number"})
    al.load_alert_settings("follows", "follows1", quiet=True)
    assert env.notices == []
    al.load_alert_settings("follows", "follows1")
    assert env.last()["title"] == "Couldn't open this alert"


# ======================================================================= Browse

def test_handle_browse_guards(env):
    al.handle_browse("gif")  # no tab
    E.make_form("bits")
    al.handle_browse("nope", "bits")
    assert env.picker_calls == [] and env.notices == []
    del env.el("bits")["gif_file_input"]
    al.handle_browse("gif", "bits")
    assert env.last()["title"] == "This alert tab is still loading"


def test_handle_browse_file(env, tmp_path):
    els = E.make_form("bits")
    env.state.current_tab = "bits"
    els["gif_dir_input"].value = "/assets/gifs/"
    els["gif_file_input"].value = "old.gif"
    env.state.get_original_values("bits").update(gif_dir_input="/assets/gifs/", gif_file_input="old.gif")
    al.handle_browse("gif")
    kw = env.picker_calls[0]
    assert kw["mode"] == "file" and kw["kind"] == "image" and kw["volume"] is None
    assert kw["start"] == "/assets/gifs/old.gif" and kw["within"] is None
    assert kw["places"][0] == ("campaign", "Alerts", tmp_path / "alerts")
    assert kw["places"][1] == ("diamond", "Bits", tmp_path / "bits")
    kw["on_pick"](SimpleNamespace(is_dir=False, web_dir="/assets/new/", filename="n.gif"))
    assert (els["gif_dir_input"].value, els["gif_file_input"].value) == ("/assets/new/", "n.gif")
    assert "bits" in env.state.dirty_tabs


def test_handle_browse_folder_and_volume(env, tmp_path):
    els = E.make_form("raids")
    els["volume_input"].value = 35
    al.handle_browse("random", "raids")
    kw = env.picker_calls[-1]
    assert kw["mode"] == "folder" and kw["within"] == tmp_path / "alerts" and kw["volume"] == 35
    assert kw["start"] is None
    kw["on_pick"](SimpleNamespace(is_dir=True, web_path="/assets/alerts/r/"))
    assert els["random_dir_input"].value == "/assets/alerts/r/"
    els["volume_input"].value = "loud"
    al.handle_browse("extra", "raids")
    assert env.picker_calls[-1]["volume"] is None
    els["volume_input"] = None
    al.handle_browse("primary", "raids")
    assert env.picker_calls[-1]["volume"] is None


def test_handle_browse_unknown_tab_name_and_error(env, monkeypatch):
    from modules.uiwindows import file_picker

    E.make_form("custom")
    al.handle_browse("gif", "custom")
    assert env.picker_calls[-1]["places"][1][0] == "folder_special"
    monkeypatch.setattr(file_picker, "open_file_picker", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    al.handle_browse("gif", "custom")
    assert env.last()["title"] == "Couldn't open the file picker"


# ======================================================================= Test Alert

@pytest.mark.parametrize("alert_type,field,range_value,amount_value,default", [
    ("bits", "amt_cheered", 7, 9, 100),
    ("subs", "resub_month", 3, 4, 1),
    ("streaks", "streak_count", 3, 6, 2),
    ("giftsubs", "gift_qty", 5, 1, 1),
    ("donations", "donation_amount", 2.0, 8.0, 5.0),
    ("raids", "raider_count", 11, 12, 10),
])
def test_collect_test_alert_data_amounts(env, alert_type, field, range_value, amount_value, default):
    els = E.make_form(alert_type)
    els["range_toggle"].value = True
    els["min_input"].value = range_value
    assert al._collect_test_alert_data(alert_type, els)[field] == range_value
    els["range_toggle"].value = False
    els["amount_input"].value = amount_value
    data = al._collect_test_alert_data(alert_type, els)
    assert data[field] == amount_value
    els["amount_input"].value = None
    assert al._collect_test_alert_data(alert_type, els)[field] == default
    if alert_type == "giftsubs":
        assert data["recipient"] == "TestRecipient"
    if alert_type in ("subs", "giftsubs"):
        assert data["tier"] == 1
    if alert_type == "streaks":
        assert data["message"] == "Test watch streak message"


def test_collect_test_alert_data_common_and_points(env):
    els = E.make_form("points", "loading")
    els["randomized_extra_switch"].value = True  # ignored while random is off
    els["enable_alert_switch"].value = True
    data = al._collect_test_alert_data("points", els)
    assert data["alert_type"] == "point" and data["stackable"] is True
    assert data["randomized_extra"] is False and data["enable_alert"] is True
    assert data["twitch_reward_id"] == "test_reward" and data["point_cost"] == 1000
    assert data["username"] == "TestUser" and data["alert_id"].startswith("TestAlert")
    els["alert_select"].value = "reward-abc"
    assert al._collect_test_alert_data("points", els)["twitch_reward_id"] == "reward-abc"
    data = al._collect_test_alert_data("follows", E.make_form("follows"))
    assert data["alert_type"] == "follow" and "enable_alert" not in data


def test_test_alert_not_ready(env):
    al.test_alert("bits")
    assert env.titles() == ["This alert tab is still loading"]
    env.el("bits")["duration_input"] = E.FakeEl(1)
    al.test_alert("bits")
    assert env.titles() == ["This alert tab is still loading"] * 2
    assert env.queued == []


def test_test_alert_queues_and_reports(env, monkeypatch):
    E.make_form("bits")
    al.test_alert("bits")
    (alert,) = env.queued
    assert alert.is_test and alert.alert_type == "bit" and alert.amt_cheered == 1
    assert env.notices == []  # overlay not running and not paused: the queue reports it
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(instant_alert=lambda p: None))
    al.test_alert("bits")
    assert env.titles()[-1] == "Test alert sent (Bits)"
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    al.test_alert("bits")
    assert env.titles()[-1] == "Alerts are paused"


def test_test_alert_follow_instant(env, monkeypatch):
    sent = []
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(instant_alert=sent.append))
    E.make_form("follows")
    al.test_alert("follows")
    assert sent[0]["type"] == "follow" and sent[0]["username"] == "TestUser"

    def boom(payload):
        raise RuntimeError("socket")

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(instant_alert=boom))
    al.test_alert("follows")
    assert env.titles()[-1] == "Couldn't send the follow test"


def test_test_alert_error(env, monkeypatch):
    els = E.make_form("bits")
    els["duration_input"].value = "abc"
    al.test_alert("bits")
    assert env.titles() == ["Couldn't send the test alert"]

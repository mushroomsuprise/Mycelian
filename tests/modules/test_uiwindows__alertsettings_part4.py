# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alertsettings.py, part 4: saving an alert from the form and the
change tracking helpers (track_field_change, originals, styling, refresh)."""

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

from modules.uiwindows import alertsettings as al  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = E.install(monkeypatch, tmp_path)
    yield e
    E.restore()


# ======================================================================= save_alert

FIELDS = {"bits": "amt_cheered", "subs": "resub_month", "streaks": "streak_count", "giftsubs": "gift_qty",
          "donations": "donation_amount", "raids": "raider_count"}


@pytest.mark.parametrize("alert_type", list(FIELDS))
def test_save_new_exact_alert(env, alert_type):
    els = E.make_form(alert_type, "new")
    els["amount_input"].value = 12
    els["duration_input"].value = 4.5
    els["randomized_extra_switch"].value = True  # random is off, so extra is saved off
    al.save_alert(alert_type)
    aid = f"{alert_type}12"
    saved = env.mgr.get_alert_by_id(alert_type, aid)
    assert saved[FIELDS[alert_type]] == 12 and saved["duration"] == 4.5
    assert saved["randomized_extra"] is False
    assert saved["alert_name"] == env.mgr.get_display_name(alert_type, aid)
    assert env.titles() == [f"Saved “{env.mgr.get_display_name(alert_type, aid, saved)}”"]
    sel = els["alert_select"]
    assert sel.value == aid and aid in sel.options and sel.options["new"] == "+ Create New Alert"
    if alert_type == "subs":
        assert sel.options["subs_fallback"] == "Resub Fallback"
    assert env.state.get_original_values(alert_type)["amount_input"] == 12
    assert alert_type not in env.state.dirty_tabs


@pytest.mark.parametrize("alert_type", list(FIELDS))
def test_save_new_range_alert(env, alert_type):
    els = E.make_form(alert_type, "new")
    els["range_toggle"].value = True
    els["min_input"].value = 3
    els["max_input"].value = 9
    al.save_alert(alert_type)
    aid = f"{alert_type}3-9"
    saved = env.mgr.get_alert_by_id(alert_type, aid)
    assert saved[FIELDS[alert_type]] == 3
    assert saved["alert_name"] == env.mgr.get_display_name(alert_type, aid)
    assert els["alert_select"].value == aid


def test_save_existing_alert_keeps_name_and_list(env):
    els = E.make_form("bits", "bits50", options={"new": "+", "bits50": "50 Bits"})
    els["amount_input"].value = 50
    els["randomized_switch"].value = True
    els["randomized_extra_switch"].value = True
    els["tts_source_select"].value = None
    al.save_alert("bits")
    saved = env.mgr.get_alert_by_id("bits", "bits50")
    assert saved["alert_name"] == "bits50" and saved["randomized_extra"] is True
    assert saved["tts_source"] == "alert_message"
    assert els["alert_select"].options == {"new": "+", "bits50": "50 Bits"}  # unchanged


def test_save_existing_range_keeps_name(env):
    els = E.make_form("raids", "raids1-2")
    els["range_toggle"].value = True
    els["min_input"].value, els["max_input"].value = 1, 2
    al.save_alert("raids")
    assert env.mgr.get_alert_by_id("raids", "raids1-2")["alert_name"] == "raids1-2"


def test_save_resub_fallback(env):
    els = E.make_form("subs", "subs_fallback")
    els["amount_input"].value = "ignored"
    al.save_alert("subs")
    saved = env.mgr.get_alert_by_id("subs", "subs_fallback")
    assert saved["alert_name"] == "Resub Fallback" and saved["resub_month"] == 0
    assert els["alert_select"].value == "subs_fallback"
    assert "subs_fallback" in els["alert_select"].options


@pytest.mark.parametrize("alert_type", ["follows", "points"])
def test_save_non_amount_alerts(env, alert_type):
    els = E.make_form(alert_type, "new")
    al.save_alert(alert_type)
    saved = env.mgr.get_alert_by_id(alert_type, f"{alert_type}1")
    assert saved["alert_name"] == env.mgr.get_display_name(alert_type, f"{alert_type}1")
    if alert_type == "points":
        assert saved["twitch_reward_id"] == "" and saved["point_cost"] == 0 and saved["audio_only"] is False
    assert els["alert_select"].value == f"{alert_type}1"


def test_save_existing_follow(env):
    E.make_form("follows", "follows1")
    al.save_alert("follows")
    assert env.mgr.get_alert_by_id("follows", "follows1")["alert_name"] == "follows1"


def test_save_new_alert_list_refresh_skipped_when_empty(env, monkeypatch):
    els = E.make_form("raids", "new")
    monkeypatch.setattr(al, "get_alerts_for_type", lambda t: {})
    al.save_alert("raids")
    assert els["alert_select"].value == "new"
    assert env.titles() == ["Saved “1 Raiders”"]


def test_save_alert_write_failure(env):
    E.make_form("bits", "new")
    env.db.write_fail.add("Alerts/BitAlerts/bits1")
    al.save_alert("bits")
    kw = env.last()
    assert kw["title"] == "Couldn't save the alert" and kw["message"] == "Your changes are still in the form."


@pytest.mark.parametrize("amount", ["", None, "abc"])
def test_save_alert_invalid_amount(env, amount):
    E.make_form("bits", "new")["amount_input"].value = amount
    al.save_alert("bits")
    assert env.titles() == ["Check the amount"]
    assert env.mgr.get_alerts_by_type("bits") == {}


def test_save_alert_unexpected_error(env):
    al.save_alert("bits")  # no form at all: KeyError
    assert env.last()["title"] == "Couldn't save the alert" and env.last()["type"] == "error"


def test_save_alert_list_refresh_error(env, monkeypatch):
    E.make_form("bits", "new")
    monkeypatch.setattr(al, "sort_alert_ids", lambda *a: (_ for _ in ()).throw(RuntimeError("sort")))
    al.save_alert("bits")
    assert env.titles()[-1] == "Alert list out of date"
    assert env.mgr.get_alert_by_id("bits", "bits1") is not None


# ======================================================================= track_field_change

def test_track_field_change_without_tab(env):
    el = E.FakeEl(1)
    al.track_field_change("duration_input", el, 2)
    assert env.state.dirty_tabs == set()


def test_track_field_change_marks_and_clears(env):
    els = E.make_form("bits")
    label = els["unsaved_label"]
    env.state.current_tab = "bits"
    env.state.get_original_values("bits")["duration_input"] = 3.0
    els["duration_input"].value = 5
    al.track_field_change("duration_input", els["duration_input"], SimpleNamespace(value=5))
    assert "bits" in env.state.dirty_tabs and label.visible is True
    els["duration_input"].value = 3.0
    al.track_field_change("duration_input", els["duration_input"], 3.0, "bits")
    assert "bits" not in env.state.dirty_tabs and label.visible is False


def test_track_field_change_other_field_keeps_dirty(env):
    els = E.make_form("bits")
    al.store_original_values("bits")
    els["gif_dir_input"].value = "/changed/"
    al.track_field_change("duration_input", els["duration_input"], 3.0, "bits")
    assert "bits" in env.state.dirty_tabs


def test_track_field_change_null_input_and_ignore(env):
    els = E.make_form("bits")
    el = E.Input(None)
    env.state.get_original_values("bits")["gif_dir_input"] = ""
    al.track_field_change("gif_dir_input", el, None, "bits")
    assert env.el("bits")["gif_dir_input"] is el and "bits" not in env.state.dirty_tabs
    env.state.ignore_field_dirty = True
    el.value = "x"
    al.track_field_change("gif_dir_input", el, "x", "bits")
    assert "bits" not in env.state.dirty_tabs
    assert els is env.el("bits")


def test_track_field_change_error_is_logged(env, monkeypatch):
    monkeypatch.setattr(env.state, "get_original_values", lambda t: (_ for _ in ()).throw(RuntimeError()))
    al.track_field_change("x", E.FakeEl(), 1, "bits")


# ======================================================================= originals / styling

def test_store_original_values(env):
    els = E.make_form("raids", "raids3")
    els["gif_dir_input"].value = None
    els["missing"] = None
    els["plain"] = object()
    env.state.dirty_tabs.add("raids")
    al.store_original_values("raids")
    orig = env.state.get_original_values("raids")
    assert orig["gif_dir_input"] == "" and orig["duration_input"] == 3.0 and "missing" not in orig
    assert "plain" not in orig
    assert "raids" not in env.state.dirty_tabs and env.state.loaded_alert_ids["raids"] == "raids3"


def test_store_original_values_error(env, monkeypatch):
    monkeypatch.setattr(env.state, "get_original_values", lambda t: (_ for _ in ()).throw(RuntimeError()))
    al.store_original_values("raids")


def test_initialize_alert_values(env, monkeypatch):
    E.make_form("bits")
    al.initialize_alert_values("bits")
    assert env.state.get_original_values("bits")
    monkeypatch.setattr(al, "store_original_values", lambda t: (_ for _ in ()).throw(RuntimeError()))
    al.initialize_alert_values("bits")


def test_update_all_fields_styling_and_clear(env):
    els = E.make_form("bits")
    els["none"] = None
    al.store_original_values("bits")
    els["stackable_switch"].value = True
    al.update_all_fields_styling("bits")
    assert els["unsaved_label"].visible is True
    els["stackable_switch"].value = False
    al.clear_changed_styling("bits")
    assert els["unsaved_label"].visible is False


def test_refresh_ui_from_current_values(env, monkeypatch):
    class BadUpdate(E.FakeEl):
        def update(self):
            raise RuntimeError("x")

    els = E.make_form("bits")
    els["bad"] = BadUpdate(1)
    els["none"] = None
    els["plain"] = object()
    al.refresh_ui_from_current_values("bits")
    assert els["duration_input"].updates == 1
    monkeypatch.setattr(env.state, "get_elements", lambda t: (_ for _ in ()).throw(RuntimeError()))
    al.refresh_ui_from_current_values("bits")

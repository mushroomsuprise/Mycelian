# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alertsettings.py, part 6: saving Channel Points rewards (Twitch +
local settings), deleting alerts, refreshing the alert lists and the Delete button."""

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
from ui_sim import run_ui  # noqa: E402

from modules import twitch  # noqa: E402
from modules.uiwindows import alertsettings as al  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = E.install(monkeypatch, tmp_path)
    yield e
    E.restore()


def points_form(selected="new", title="Spin", cost=100):
    els = E.make_form("points", selected)
    els["twitch_title_input"].value = title
    els["twitch_cost_input"].value = cost
    return els


# ======================================================================= save_point_alert: validation

def test_save_point_requires_twitch(env, monkeypatch):
    points_form()
    done = []
    monkeypatch.setattr(twitch, "twitch_api", None)
    al.save_point_alert(done.append)
    monkeypatch.setattr(twitch, "twitch_api", SimpleNamespace(is_connected=False))
    al.save_point_alert(done.append)
    assert done == [False, False]
    assert env.titles() == ["Connect Twitch to save this reward"] * 2


def test_save_point_requires_reward(env):
    points_form("loading")
    done = []
    al.save_point_alert(done.append)
    assert done == [False] and env.titles() == ["Pick a reward first"]


@pytest.mark.parametrize("title,cost,expected", [
    ("  ", 100, "Enter a reward title"), (None, 100, "Enter a reward title"),
    ("Spin", 0, "Point cost must be at least 1"), ("Spin", None, "Point cost must be at least 1"),
    ("Spin", "abc", "Point cost must be at least 1"),
])
def test_save_point_new_validation(env, title, cost, expected):
    points_form("new", title, cost)
    done = []
    al.save_point_alert(done.append)
    assert done == [False] and env.titles() == [expected] and env.twitch.created == []


# ======================================================================= save_point_alert: Twitch work

def test_save_point_new_creates_reward_and_reselects(env):
    els = points_form("new", " Spin ", 250)
    els["enable_alert_switch"].value = True
    els["randomized_extra_switch"].value = True
    env.twitch.rewards["new-reward-id"] = {"title": "Spin", "cost": 250}
    al.save_point_alert()
    (work,) = env.works
    assert work.title == "Saving “Spin”" and work.updates == ["Creating the reward on Twitch…"]
    assert env.twitch.created[0]["title"] == "Spin" and env.twitch.created[0]["cost"] == 250
    saved = env.mgr.get_alert_by_id("points", "new-reward-id")
    assert saved["title"] == "Spin" and saved["enable_alert"] is True and saved["alert_name"] == "Spin"
    assert saved["twitch_reward_id"] == "new-reward-id" and saved["randomized_extra"] is False
    assert work.done_calls[0][0] == "Saved “Spin”"
    assert "LoadPointRewards" in env.threads  # forced list reload
    env.run_scheduled()
    assert els["alert_select"].value == "new-reward-id"
    assert els["twitch_cost_input"].value == 250


def test_save_point_with_callback_finishes_once(env):
    points_form("new")
    done = []
    al.save_point_alert(done.append)
    assert done == [True]
    assert env.state.get_original_values("points")["twitch_title_input"] == "Spin"
    assert env.scheduled == [] or all(d != 0.85 for d, *_ in env.scheduled)


def test_save_point_callback_error_is_logged(env):
    points_form("new")

    def bad(ok):
        raise RuntimeError("callback")

    al.save_point_alert(bad)
    assert env.works[0].done_calls


def test_save_point_existing_unchanged_skips_twitch(env):
    els = points_form("reward-1")
    al.store_original_values("points")
    els["duration_input"].value = 9
    done = []
    al.save_point_alert(done.append)
    assert done == [True] and env.twitch.updated == [] and env.twitch.created == []
    assert env.mgr.get_alert_by_id("points", "reward-1")["duration"] == 9


def test_save_point_existing_changed_updates_twitch(env):
    els = points_form("reward-1")
    al.store_original_values("points")
    els["twitch_cost_input"].value = 300
    done = []
    al.save_point_alert(done.append)
    assert env.twitch.updated[0][0] == "reward-1" and env.twitch.updated[0][1]["cost"] == 300
    assert env.works[0].updates == ["Updating the reward on Twitch…"] and done == [True]


def test_save_point_update_rejected(env):
    els = points_form("reward-1")
    al.store_original_values("points")
    els["twitch_title_input"].value = "Renamed"
    env.twitch.update_result = False
    done = []
    al.save_point_alert(done.append)
    assert done == [False]
    assert env.works[0].fail_calls[0][0] == "Twitch didn't accept the reward changes"
    assert env.mgr.get_alert_by_id("points", "reward-1") is None


@pytest.mark.parametrize("status,title", [("not_unlocked", "Channel Points aren't available"),
                                          ("error", "Couldn't reach Twitch Channel Points")])
def test_save_point_fetch_status(env, status, title):
    points_form("new")
    env.twitch.fetch_result = {"status": status}
    done = []
    al.save_point_alert(done.append)
    assert done == [False]
    (t, _msg, kw), = env.works[0].done_calls
    assert t == title and kw["type"] == "warning"


@pytest.mark.parametrize("result", [None, {}, {"id": ""}])
def test_save_point_create_failed(env, result):
    points_form("new")
    env.twitch.create_result = result
    done = []
    al.save_point_alert(done.append)
    assert done == [False] and env.works[0].fail_calls[0][0] == "Twitch didn't create the reward"


def test_save_point_worker_exception(env):
    points_form("new")
    env.twitch.fetch_error = RuntimeError("helix")
    done = []
    al.save_point_alert(done.append)
    assert done == [False]
    assert env.works[0].fail_calls[0][2]["details"] == "RuntimeError: helix"


@pytest.mark.parametrize("selected,unreadable,expected", [
    ("new", False, "The reward was saved on Twitch, but the alert settings weren't saved in Mycelian."),
    ("reward-1", False, "Your changes are still in the form."),
    ("reward-1", True, "couldn't read your saved alerts"),
])
def test_save_point_local_save_failed(env, selected, unreadable, expected):
    points_form(selected)
    if selected != "new":
        al.store_original_values("points")
    if unreadable:
        env.mgr._unreadable_state_keys.add("points")
        env.db.read_fail.add("Alerts/PointAlerts")
    else:
        env.db.write_fail.add("Alerts/PointAlerts")
    done = []
    al.save_point_alert(done.append)
    assert done == [False]
    title, message, kw = env.works[0].fail_calls[0]
    assert title == "Couldn't save the alert" and expected in message


def test_save_point_local_save_exception(env, monkeypatch):
    points_form("new")
    monkeypatch.setattr(env.mgr, "save_alert", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    done = []
    al.save_point_alert(done.append)
    assert done == [False] and env.works[0].fail_calls[0][2]["details"] == "RuntimeError: disk"


def test_save_point_reselect_error(env, monkeypatch):
    points_form("new")
    al.save_point_alert()
    monkeypatch.setattr(al, "load_point_reward_settings", lambda *a: (_ for _ in ()).throw(RuntimeError("tw")))
    env.run_scheduled()
    assert env.titles()[-1] == "Reward list out of date"


def test_save_point_reselect_without_select(env):
    points_form("new")
    al.save_point_alert()
    del env.el("points")["alert_select"]
    env.twitch.rewards["new-reward-id"] = {"title": "Spin"}
    env.run_scheduled()


def test_save_point_unexpected_errors(env, monkeypatch):
    done = []
    al.save_point_alert(done.append)  # no form: KeyError before any progress notice
    assert done == [False] and env.titles() == ["Couldn't save the reward"]
    points_form("new")

    class NoThread:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError("no threads")

    monkeypatch.setattr(al, "threading", SimpleNamespace(Thread=NoThread))
    al.save_point_alert(done.append)
    assert done[-1] is False and env.works[-1].fail_calls[0][0] == "Couldn't save the reward"
    al.save_point_alert()  # no callback: nothing to report back


# ======================================================================= delete

def test_show_delete_confirmation_guards(env):
    E.make_form("bits", None)
    al.show_delete_confirmation("bits")
    E.make_form("points", "loading")
    al.show_delete_confirmation("points")
    E.make_form("raids", "raids5")
    al.show_delete_confirmation("raids")
    assert env.titles() == ["Select an alert to delete", "Select an alert to delete", "That alert no longer exists"]
    al.show_delete_confirmation("follows")  # no form: error notice
    assert env.titles()[-1] == "Couldn't open the delete dialog"


def _open_delete_dialog(env):
    async def opener(user):
        with user:
            E.make_form("raids", "raids5")
            al.show_delete_confirmation("raids")
        await user.should_see("Confirm Delete Alert")
        await user.should_see("Name: 5 Raiders")
        await user.should_see("ID: raids5")

    return opener


def test_delete_dialog_cancel(env):
    from nicegui import ui

    env.save("raids", "raids5", {})

    async def check(user):
        await _open_delete_dialog(env)(user)
        with user:
            user.find(kind=ui.button, content="Cancel").click()
        assert env.mgr.get_alert_by_id("raids", "raids5") is not None

    run_ui(lambda: None, check)


def test_delete_dialog_confirm(env):
    from nicegui import ui

    env.save("raids", "raids5", {})
    env.save("raids", "raids9", {})

    async def check(user):
        await _open_delete_dialog(env)(user)
        with user:
            user.find(kind=ui.button, content="Delete Alert").click()
            assert env.mgr.get_alert_by_id("raids", "raids5") is None
            sel = env.el("raids")["alert_select"]
            assert sel.value == "new" and list(sel.options) == ["new", "raids9"]
            assert env.titles()[-1] == "Deleted “5 Raiders”"

    run_ui(lambda: None, check)


def test_confirm_delete_subs_keeps_fallback(env):
    env.save("subs", "subs3", {})
    E.make_form("subs", "subs3")
    dialog = SimpleNamespace(closed=False, close=lambda: setattr(dialog, "closed", True))
    al.confirm_delete_alert(dialog, "subs", "subs3")
    assert dialog.closed
    assert env.el("subs")["alert_select"].options == {"new": "+ Create New Alert", "subs_fallback": "Resub Fallback"}
    assert env.el("subs")["amount_input"].value == 1


def test_confirm_delete_points(env):
    env.save("points", "reward-1", {"title": "Spin"})
    els = E.make_form("points", "reward-1")
    al.confirm_delete_alert(SimpleNamespace(close=lambda: None), "points", "reward-1")
    assert "still exists on Twitch" in env.notices[-1][1]["message"]
    assert els["alert_select"].value == "new" and els["twitch_title_input"].value == "New Point Reward"
    assert "LoadPointRewards" in env.threads


def test_confirm_delete_failures(env, monkeypatch):
    env.save("bits", "bits1", {})
    E.make_form("bits", "bits1")
    env.db.write_fail.add("Alerts/BitAlerts")
    al.confirm_delete_alert(SimpleNamespace(close=lambda: None), "bits", "bits1")
    assert env.titles()[-1] == "Couldn't delete the alert" and "is still saved" in env.last()["message"]
    env.db.write_fail.clear()
    monkeypatch.setattr(al, "sort_alert_ids", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    al.confirm_delete_alert(SimpleNamespace(close=lambda: None), "bits", "bits1")
    assert env.titles()[-1] == "Deleted “1 Bits”"  # list refresh error only logged

    def bad_close():
        raise RuntimeError("closed twice")

    al.confirm_delete_alert(SimpleNamespace(close=bad_close), "bits", "bits1")
    assert env.last()["details"] == "RuntimeError: closed twice"


# ======================================================================= refresh / delete button

def test_refresh_alert_dropdowns(env, monkeypatch):
    loads = []
    monkeypatch.setattr(al, "load_twitch_point_rewards", lambda: loads.append(1))
    env.save("bits", "bits5", {})
    bits = E.make_form("bits", "bits404")
    subs = E.make_form("subs", "subs_fallback")
    E.make_form("points", "reward-1")
    al.refresh_alert_dropdowns()
    assert list(bits["alert_select"].options) == ["new", "bits5"] and bits["alert_select"].value == "new"
    assert subs["alert_select"].value == "subs_fallback"
    assert loads == [1] and env.state.suppress_selection_handler_depth == 0
    monkeypatch.setattr(al, "get_alerts_for_type", lambda t: (_ for _ in ()).throw(RuntimeError()))
    al.refresh_alert_dropdowns()  # logged


def test_update_delete_button_visibility(env):
    al.update_delete_button_visibility("bits")  # no button
    els = E.make_form("bits", "bits1")
    al.update_delete_button_visibility("bits")
    assert els["delete_btn"].visible is True
    for value in ("new", None, "error"):
        els["alert_select"].value = value
        al.update_delete_button_visibility("bits")
        assert els["delete_btn"].visible is False
    del els["alert_select"]
    al.update_delete_button_visibility("bits")  # logged

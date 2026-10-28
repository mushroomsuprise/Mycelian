# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alertsettings.py, part 5: the Channel Points panel, loading
Twitch rewards, reward selection, loading a reward into the form and the Twitch payload."""

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

from modules.uiwindows import alertsettings as al  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = E.install(monkeypatch, tmp_path)
    yield e
    E.restore()


REWARD = {
    "id": "reward-1", "title": "Hydrate", "cost": 500, "is_enabled": False, "is_user_input_required": True,
    "prompt": "Why?", "max_per_stream": {"is_enabled": True, "max_per_stream": 3},
    "max_per_user_per_stream": {"is_enabled": True, "max_per_user_per_stream": 2},
    "global_cooldown": {"is_enabled": True, "global_cooldown_seconds": 90},
    "should_redemptions_skip_request_queue": False,
}


# ======================================================================= panel

def test_points_panel_auto_load(env, monkeypatch):
    loads = []
    monkeypatch.setattr(al, "load_twitch_point_rewards", lambda **kw: loads.append(kw))

    async def check(user):
        with user:
            assert env.state.current_tab == "points"
            assert env.el("points")["alert_select"].options == {"loading": "Loading Twitch rewards..."}
            auto = next(f for d, f, kw in env.scheduled if d == 1.0)
            auto()
            assert loads == [{}]
            env.state.current_tab = "bits"
            auto()
            assert loads == [{}]
            env.state.current_tab = "points"
            monkeypatch.setattr(al, "load_twitch_point_rewards", lambda **kw: (_ for _ in ()).throw(RuntimeError()))
            auto()  # logged
            # the Save button runs save_point_alert
            saves = []
            monkeypatch.setattr(al, "save_point_alert", lambda on_complete=None: saves.append(on_complete))
            user.find("Save").click()
            assert saves == [None]

    run_ui(al.create_points_alert_panel, check)


def test_points_panel_end_to_end_reward_list(env):
    env.twitch.fetch_result = {"status": "ok", "rewards": [{"id": "reward-1", "title": "Hydrate", "cost": 500},
                                                          {"id": "reward-2", "cost": 5}]}

    async def check(user):
        with user:
            al.load_twitch_point_rewards()
        await user.should_see("Hydrate")
        await user.should_see("Unnamed Reward")
        with user:
            opts = env.el("points")["alert_select"].options
            assert list(opts) == ["new", "reward-1", "reward-2"]
            assert opts["reward-1"] == "Hydrate (500 points)"
            env.run_scheduled()  # forced select refreshes

    run_ui(al.create_points_alert_panel, check)


# ======================================================================= selection

def test_point_selection_suppressed_and_invalid(env, monkeypatch):
    loads = []
    monkeypatch.setattr(al, "load_point_reward_settings", lambda t, v: loads.append(v))
    E.make_form("points")
    with al.suppress_alert_selection_handler():
        al.handle_point_reward_selection(SimpleNamespace(value="reward-1"), "points")
    for value in (None, "", "   ", 42, "abc"):
        al.handle_point_reward_selection(SimpleNamespace(value=value), "points")
    al.handle_point_reward_selection(SimpleNamespace(value="loading"), "points")
    assert loads == []
    al.handle_point_reward_selection(SimpleNamespace(value="reward-1"), "points")
    assert loads == ["reward-1"]


def test_point_selection_new(env):
    els = E.make_form("points", "new")
    els["twitch_title_input"].value = "old"
    al.handle_point_reward_selection(SimpleNamespace(value="new"), "points")
    assert els["twitch_title_input"].value == "New Point Reward"
    assert env.state.get_original_values("points")["twitch_cost_input"] == 100
    assert els["delete_btn"].visible is False


def test_point_selection_error(env, monkeypatch):
    E.make_form("points")
    monkeypatch.setattr(al, "load_point_reward_settings", lambda t, v: (_ for _ in ()).throw(RuntimeError("tw")))
    al.handle_point_reward_selection(SimpleNamespace(value="reward-1"), "points")
    assert env.last()["dedupe_key"] == "alerts:points:reward_load_failed"


def test_point_selection_guarded(env, monkeypatch):
    prompts, discards, handled = [], [], []
    monkeypatch.setattr(al, "_prompt_unsaved_alert", lambda t, m, on_discard, on_saved: prompts.append((on_discard, on_saved)))
    monkeypatch.setattr(al, "_discard_then_select", lambda t, v, h: discards.append(v))
    monkeypatch.setattr(al, "_select_alert_programmatically", lambda t, v, h: handled.append(v))
    E.make_form("points", "reward-1")
    env.state.loaded_alert_ids["points"] = "reward-1"
    env.state.dirty_tabs.add("points")
    al.handle_point_reward_selection(SimpleNamespace(value="reward-2"), "points")
    prompts[0][0]()
    prompts[0][1]()
    assert discards == ["reward-2"] and handled == ["reward-2"]


def test_schedule_point_reward_select_refresh(env):
    al._schedule_point_reward_select_refresh(None)
    assert env.scheduled == []
    sel = E.FakeEl()
    al._schedule_point_reward_select_refresh(sel)
    assert [d for d, *_ in env.scheduled] == [0.1, 0.3, 0.5]
    env.run_scheduled()
    assert sel.updates == 3

    class Bad(E.FakeEl):
        def update(self):
            raise RuntimeError("gone")

    al._schedule_point_reward_select_refresh(Bad())
    env.run_scheduled()  # errors logged


# ======================================================================= unavailable notices

def test_notify_rewards_unavailable(env, monkeypatch):
    al._notify_rewards_unavailable("not_unlocked", False)
    al._notify_rewards_unavailable("not_connected", False)
    assert env.notices == []
    al._notify_rewards_unavailable("not_unlocked", True)
    al._notify_rewards_unavailable("not_connected", True)
    assert env.titles() == ["Channel Points aren't available", "Twitch isn't connected"]
    now = [1000.0]
    monkeypatch.setattr(al.time, "monotonic", lambda: now[0])
    al._notify_rewards_unavailable("error", False)
    al._notify_rewards_unavailable("error", False)  # within cooldown
    assert env.titles().count("Couldn't load your Channel Points rewards") == 1
    now[0] += al._POINTS_AUTO_ERROR_COOLDOWN_SEC
    al._notify_rewards_unavailable("error", False)
    al._notify_rewards_unavailable("weird", True)  # user-initiated: always
    assert env.titles().count("Couldn't load your Channel Points rewards") == 3
    assert env.last()["details"] == "status=weird"


# ======================================================================= load_twitch_point_rewards

def test_load_rewards_without_select(env):
    al.load_twitch_point_rewards()
    assert env.threads == []


def test_load_rewards_skips_while_inflight(env):
    E.make_form("points")
    al._points_load_state["inflight"] = True
    al.load_twitch_point_rewards()
    assert env.threads == []
    al.load_twitch_point_rewards(force=True)
    assert env.threads == ["LoadPointRewards"]


def test_load_rewards_ok(env):
    els = E.make_form("points")
    els["save_btn"].enabled = False
    els["channel_points_locked_banner"].visible = True
    env.twitch.fetch_result = {"status": "ok", "rewards": [{"id": "b", "title": "beta", "cost": 2},
                                                          {"id": "a", "title": "Alpha", "cost": 1}]}
    al.load_twitch_point_rewards()
    assert list(els["alert_select"].options) == ["new", "a", "b"]
    assert els["save_btn"].enabled is True and els["channel_points_locked_banner"].visible is False
    assert al._points_load_state["inflight"] is False
    assert env.state.suppress_selection_handler_depth == 0


def test_load_rewards_ok_empty(env):
    els = E.make_form("points")
    env.twitch.fetch_result = {"status": "ok", "rewards": []}
    al.load_twitch_point_rewards()
    assert "no_rewards" in els["alert_select"].options


@pytest.mark.parametrize("status,key,banner", [
    ("not_unlocked", "no_channel_points", True), ("not_connected", "not_connected", False), ("error", "error", False),
])
def test_load_rewards_unavailable(env, status, key, banner):
    els = E.make_form("points")
    env.twitch.fetch_result = {"status": status, "rewards": None}
    al.load_twitch_point_rewards(user_initiated=True)
    assert list(els["alert_select"].options) == [key]
    assert els["save_btn"].enabled is False and els["channel_points_locked_banner"].visible is banner
    assert env.notices  # user asked, so it explains


def test_load_rewards_no_save_button_or_banner(env):
    els = E.make_form("points")
    del els["save_btn"], els["channel_points_locked_banner"]
    for status in ("not_unlocked", "ok"):
        env.twitch.fetch_result = {"status": status, "rewards": []}
        al.load_twitch_point_rewards()


def test_load_rewards_fetch_exception(env):
    els = E.make_form("points")
    env.twitch.fetch_error = RuntimeError("helix down")
    al.load_twitch_point_rewards()
    assert list(els["alert_select"].options) == ["error"]
    assert al._points_load_state["inflight"] is False


def test_load_rewards_thread_start_failure(env, monkeypatch):
    els = E.make_form("points")

    class NoThread:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(al, "threading", SimpleNamespace(Thread=NoThread))
    al.load_twitch_point_rewards(user_initiated=True)
    assert al._points_load_state["inflight"] is False
    assert list(els["alert_select"].options) == ["error"]
    assert els["save_btn"].enabled is False
    assert env.titles()[-1] == "Couldn't load your Channel Points rewards"


def test_load_rewards_outer_error_without_select(env, monkeypatch):
    from modules import twitch

    calls = {"n": 0}
    real_get = env.state.get_elements

    def flaky(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_get(t)

    monkeypatch.setattr(env.state, "get_elements", flaky)
    al.load_twitch_point_rewards()
    assert env.titles() == ["Couldn't load your Channel Points rewards"]
    assert twitch is not None


# ======================================================================= defaults

def test_point_reward_defaults(env):
    els = E.make_form("points")
    for name in E.TWITCH_FIELDS:
        els[name].value = "x"
    els["enable_alert_switch"].value = True
    al.set_default_values_for_new_point_reward("points")
    assert els["twitch_title_input"].value == "New Point Reward" and els["twitch_cost_input"].value == 100
    assert els["twitch_skip_queue_switch"].value is True and els["twitch_cooldown_input"].value == 60
    assert els["enable_alert_switch"].value is False
    for name in E.TWITCH_FIELDS:
        els[name].value = "keep"
    al.set_default_values_for_new_point_reward_non_twitch("points")
    assert els["twitch_title_input"].value == "keep" and els["duration_input"].value == 3.0


# ======================================================================= load_point_reward_settings

def test_load_point_reward_missing(env):
    E.make_form("points")
    al.load_point_reward_settings("points", "gone")
    assert env.last()["dedupe_key"] == "alerts:points:missing:gone"


def test_load_point_reward_with_local_settings(env):
    els = E.make_form("points", "reward-1")
    env.twitch.rewards["reward-1"] = REWARD
    env.save("points", "reward-1", {"enable_alert": True, "duration": 8, "stackable": True, "fade_in": 1,
                                    "fade_out": 2, "volume": 30, "tts_enabled": True, "tts_source": None,
                                    "tts_custom_message": None, "audio_only": True, "single_audio_dir": None,
                                    "single_audio_name": "a.mp3", "randomized": True, "randomized_dir": "/r/",
                                    "randomized_chance": 10, "randomized_extra": True,
                                    "randomized_extra_dir": None, "randomized_extra_chance": 4,
                                    "gif_dir": "/g/", "gif_name": "g.gif"})
    al.load_point_reward_settings("points", "reward-1")
    assert els["twitch_title_input"].value == "Hydrate" and els["twitch_cost_input"].value == 500
    assert els["twitch_enabled_switch"].value is False and els["twitch_user_input_switch"].value is True
    assert els["twitch_max_per_stream_input"].value == 3 and els["twitch_max_per_user_input"].value == 2
    assert els["twitch_cooldown_input"].value == 90 and els["twitch_skip_queue_switch"].value is False
    assert els["enable_alert_switch"].value is True and els["duration_input"].value == 8.0
    assert els["volume_value_label"].text == "30%" and els["tts_source_select"].value == "alert_message"
    assert els["audio_only_switch"].value is True and els["primary_dir_input"].value == ""
    assert els["gif_file_input"].value == "g.gif" and els["extra_dir_input"].value == ""
    assert env.state.get_original_values("points")["twitch_title_input"] == "Hydrate"
    assert els["delete_btn"].visible is True
    env.run_scheduled()  # forced input refreshes
    assert els["gif_dir_input"].value == "/g/"


def test_load_point_reward_defaults_without_local(env):
    els = E.make_form("points", "reward-2")
    env.twitch.rewards["reward-2"] = {"title": "Plain"}
    els["enable_alert_switch"].value = True
    els["twitch_max_per_stream_switch"].value = True
    al.load_point_reward_settings("points", "reward-2")
    assert els["enable_alert_switch"].value is False and els["twitch_title_input"].value == "Plain"
    assert els["twitch_max_per_stream_switch"].value is False and els["twitch_cooldown_input"].value == 60
    assert els["twitch_cost_input"].value == 100


def test_load_point_reward_sparse_form(env):
    """Local data with most form fields missing: each field is skipped, GIF fields warn."""
    els = E.make_form("points", "reward-1")
    env.twitch.rewards["reward-1"] = REWARD
    env.save("points", "reward-1", {"duration": 1})
    for name in list(els):
        if name not in E.TWITCH_FIELDS and name not in ("alert_select", "delete_btn", "unsaved_label"):
            els[name] = None
    al.load_point_reward_settings("points", "reward-1")
    assert els["twitch_title_input"].value == "Hydrate"


def test_load_point_reward_refresh_error_and_failure(env):
    class BadUpdate(E.FakeEl):
        def update(self):
            raise RuntimeError("x")

    els = E.make_form("points", "reward-1")
    env.twitch.rewards["reward-1"] = REWARD
    els["gif_dir_input"] = BadUpdate("")
    al.load_point_reward_settings("points", "reward-1")
    env.run_scheduled()  # logged
    env.twitch.rewards["reward-1"] = RuntimeError("helix")
    al.load_point_reward_settings("points", "reward-1")
    assert env.last()["title"] == "Couldn't load this reward from Twitch"


# ======================================================================= Twitch payload / change detection

@pytest.mark.parametrize("orig,cur,expected", [
    (1, 1.0, True), (1, "1", True), (1, 2, False), ("x", None, False), (None, "", True), (" a ", "a", True),
    (True, True, True), (None, None, True), ([1], [1], True), (1, "abc", False),
])
def test_twitch_ui_values_equal(orig, cur, expected):
    assert al._twitch_ui_values_equal(orig, cur) is expected


def test_twitch_reward_fields_changed(env):
    els = E.make_form("points")
    els["twitch_prompt_missing"] = None
    al.store_original_values("points")
    assert al.twitch_reward_fields_changed("points") is False
    els["twitch_cost_input"].value = 101
    assert al.twitch_reward_fields_changed("points") is True
    els["twitch_cost_input"].value = 100
    els["twitch_title_input"] = None
    assert al.twitch_reward_fields_changed("points") is False


def test_build_twitch_reward_payload(env):
    els = E.make_form("points")
    els["twitch_title_input"].value = "  Spin  "
    els["twitch_cost_input"].value = "250"
    payload = al.build_twitch_reward_payload_from_ui("points")
    assert payload == {
        "title": "Spin", "cost": 250, "is_enabled": True, "is_user_input_required": False, "prompt": "",
        "should_redemptions_skip_request_queue": True, "max_per_stream": {"is_enabled": False},
        "max_per_user_per_stream": {"is_enabled": False}, "global_cooldown": {"is_enabled": False},
    }
    for name in ("twitch_max_per_stream_switch", "twitch_max_per_user_switch", "twitch_cooldown_switch"):
        els[name].value = True
    els["twitch_title_input"].value = None
    payload = al.build_twitch_reward_payload_from_ui("points")
    assert payload["title"] == ""
    assert payload["max_per_stream"] == {"is_enabled": True, "max_per_stream": 1}
    assert payload["max_per_user_per_stream"] == {"is_enabled": True, "max_per_user_per_stream": 1}
    assert payload["global_cooldown"] == {"is_enabled": True, "global_cooldown_seconds": 60}

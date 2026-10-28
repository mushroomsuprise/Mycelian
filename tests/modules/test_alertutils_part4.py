# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alertutils.py, part 4: AlertObj, the fetch_*_alert matchers, replay
(build/payload/emit/replay_alert), chat media resolution and initialize_alert_state*."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _alertutils_fakes as fakes  # noqa: E402
from modules import alert_processor, template_config_parser as tcp, web_engine  # noqa: E402
from modules import alertutils as au  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    db, mgr = fakes.install(monkeypatch)
    yield db, mgr
    fakes.restore_collections()


def _cfg(name, **extra):
    d = {"alert_name": name, "gif_dir": f"/g/{name}", "gif_name": f"{name}.gif", "duration": 3}
    d.update(extra)
    return d


# ======================================================================= AlertObj

def test_alert_obj_defaults():
    a = au.AlertObj()
    assert a.duration == 0.0 and a.currency == "USD" and a.tts_source == "alert_message"
    assert a.message is None and a.hold_queue_only is False


def test_alert_obj_from_db(env):
    a = au._alert_obj_from_db({"alert_name": "x", "nonsense": 1})
    assert a.alert_name == "x"
    assert au._alert_obj_from_db(None).alert_name == ""
    assert au._alert_obj_from_db(["list"]).alert_name == ""


# ======================================================================= amount matchers

AMOUNT_FETCHERS = [
    # (fetcher, ui type, range key, prefix, default id)
    (au.fetch_bits_alert, "bits", "bit_ranges", "bits", "bit1"),
    (au.fetch_donation_alert, "donations", "donation_ranges", "donations", "donation1"),
    (au.fetch_raid_alert, "raids", "raid_ranges", "raids", "raid1"),
    (au.fetch_streak_alert, "streaks", "streak_ranges", "streaks", "streaks1"),
    (au.fetch_sub_alert, "subs", "sub_ranges", "subs", "sub1"),
    (au.fetch_giftsub_alert, "giftsubs", "giftsub_ranges", "giftsubs", "giftsub1"),
]


@pytest.mark.parametrize("fetch,t,r,prefix,default_id", AMOUNT_FETCHERS)
def test_amount_fetchers_exact_range_default_first(env, fetch, t, r, prefix, default_id):
    _, mgr = env
    mgr._alert_state[t] = {f"{prefix}100": _cfg("exact"), default_id: _cfg("default")}
    mgr._alert_state[r] = {f"{prefix}x-y": _cfg("bad-range"), f"{prefix}10-20": _cfg("range"),
                           "30-40": _cfg("unprefixed")}
    assert fetch(100).alert_name == "exact"
    assert fetch(15).alert_name == "range"
    assert fetch(35).alert_name == "unprefixed"
    assert fetch(999).alert_name == "default"
    del mgr._alert_state[t][default_id]
    first = fetch(999)
    assert first.alert_name == "exact"  # first available


@pytest.mark.parametrize("fetch,t,r,prefix,default_id", [f for f in AMOUNT_FETCHERS if f[0] is not au.fetch_giftsub_alert])
def test_amount_fetchers_nothing_configured(env, fetch, t, r, prefix, default_id):
    assert fetch(5) is None


def test_fetch_giftsub_loads_paths_then_fallback(env, monkeypatch):
    _, mgr = env
    loads = []
    monkeypatch.setattr(mgr, "ensure_alert_paths_loaded", lambda keys: loads.append(list(keys)))
    fb = au.fetch_giftsub_alert(3)
    assert loads == [["giftsubs", "giftsub_ranges"]]
    assert fb.alert_type == "giftsub" and fb.alert_name == "Default Giftsub"


def test_fetch_cheer_alias(env):
    _, mgr = env
    mgr._alert_state["bits"] = {"bits5": _cfg("five")}
    assert au.fetch_cheer_alert(5).alert_name == "five"


def test_fetch_follow_alert(env):
    _, mgr = env
    assert au.fetch_follow_alert() is None
    mgr._alert_state["follows"] = {"other": _cfg("other"), "follow1": _cfg("default")}
    assert au.fetch_follow_alert().alert_name == "default"
    del mgr._alert_state["follows"]["follow1"]
    assert au.fetch_follow_alert().alert_name == "other"


def test_fetch_point_alert(env):
    _, mgr = env
    mgr._alert_state["points"] = {"rw1": _cfg("reward")}
    assert au.fetch_point_alert("rw1").alert_name == "reward"
    assert au.fetch_point_alert("rw2") is None


def test_fetch_resub_alert(env):
    db, mgr = env
    assert au.fetch_resub_alert(3) is None
    mgr._alert_state["subs"] = {"subs6": _cfg("six"), "subs_fallback": _cfg("fallback"), "sub1": _cfg("default")}
    mgr._alert_state["sub_ranges"] = {"subsa-b": _cfg("bad"), "subs10-20": _cfg("range")}
    assert au.fetch_resub_alert(6).alert_name == "six"
    assert au.fetch_resub_alert(12).alert_name == "range"
    mgr._resub_fallback_enabled = False
    assert au.fetch_resub_alert(7).alert_name == "default"
    mgr._resub_fallback_enabled = True
    assert au.fetch_resub_alert(7).alert_name == "fallback"
    mgr._resub_fallback_enabled = False
    del mgr._alert_state["subs"]["sub1"]
    assert au.fetch_resub_alert(7).alert_name == "six"


# ======================================================================= replay helpers

@pytest.mark.parametrize("value,expected", [("3", 3), (None, 0), ("x", 0), (2.9, 2)])
def test_replay_int(value, expected):
    assert au._replay_int(value) == expected


@pytest.mark.parametrize("value,expected", [("3.5", 3.5), (None, 0.0), ("x", 0.0)])
def test_replay_float(value, expected):
    assert au._replay_float(value) == expected


def test_replay_int_float_custom_default():
    assert au._replay_int(None, 7) == 7 and au._replay_float("?", 1.5) == 1.5


@pytest.fixture
def fetch_spy(monkeypatch):
    calls = []
    for name in ("fetch_bits_alert", "fetch_sub_alert", "fetch_resub_alert", "fetch_giftsub_alert",
                 "fetch_donation_alert", "fetch_raid_alert", "fetch_streak_alert", "fetch_point_alert"):
        monkeypatch.setattr(au, name, lambda *a, _n=name: calls.append((_n, a)) or _n)
    monkeypatch.setattr(au, "fetch_follow_alert", lambda: calls.append(("fetch_follow_alert", ())) or "follow")
    return calls


@pytest.mark.parametrize("stored,expected", [
    ({"alert_type": "bit", "amt_cheered": "500"}, ("fetch_bits_alert", (500,))),
    ({"alert_type": "Bits"}, ("fetch_bits_alert", (0,))),
    ({"alert_type": "sub", "resub_month": 0}, ("fetch_sub_alert", (1,))),
    ({"alert_type": "sub", "resub_month": 4}, ("fetch_sub_alert", (4,))),
    ({"alert_type": "resub", "resub_month": "x"}, ("fetch_resub_alert", (1,))),
    ({"alert_type": "resub", "resub_month": -2}, ("fetch_resub_alert", (1,))),
    ({"alert_type": "giftsubs", "gift_qty": 0}, ("fetch_giftsub_alert", (1,))),
    ({"alert_type": "giftsub", "gift_qty": 5}, ("fetch_giftsub_alert", (5,))),
    ({"alert_type": "donation", "donation_amount": "7.5"}, ("fetch_donation_alert", (8,))),
    ({"alert_type": "raids", "raider_count": 0}, ("fetch_raid_alert", (1,))),
    ({"alert_type": "raid", "raider_count": 9}, ("fetch_raid_alert", (9,))),
    ({"alert_type": "watch_streak", "streak_count": None}, ("fetch_streak_alert", (1,))),
    ({"alert_type": "streak", "streak_count": 3}, ("fetch_streak_alert", (3,))),
    ({"alert_type": "follow"}, ("fetch_follow_alert", ())),
    ({"alert_type": "point", "twitch_reward_id": 42}, ("fetch_point_alert", ("42",))),
])
def test_fetch_alert_config_for_stored(fetch_spy, stored, expected):
    au.fetch_alert_config_for_stored(stored)
    assert fetch_spy == [expected]


@pytest.mark.parametrize("stored", [None, "x", {}, {"alert_type": "points"}, {"alert_type": "hype_train"}])
def test_fetch_alert_config_for_stored_none(fetch_spy, stored):
    assert au.fetch_alert_config_for_stored(stored) is None
    assert fetch_spy == []


def test_replay_config_for_donation_matches_exact_alert(env):
    _, mgr = env
    mgr._alert_state["donations"] = {"donation1": _cfg("default"), "donations5": _cfg("five")}
    stored = mgr._normalize_alert_data_for_storage({"alert_type": "donation", "donation_amount": 5})
    assert au.fetch_alert_config_for_stored(stored).alert_name == "five"


@pytest.mark.parametrize("value,expected", [("point", True), (" Points ", True), ("bit", False), (None, False)])
def test_is_point_type(value, expected):
    assert au._is_point_type(value) is expected


@pytest.fixture
def dedicated(monkeypatch):
    state = {"value": {"queued": True, "duration_seconds": "7"}, "calls": []}

    def match(title):
        state["calls"].append(title)
        v = state["value"]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(tcp, "match_point_reward_dedicated_template", match)
    return state


def test_apply_point_dedicated_hold_sets_hold(dedicated):
    r = au.AlertObj(alert_name="Spin", duration=2)
    au._apply_point_dedicated_hold(r)
    assert r.hold_queue_only is True and r.duration == 7.0
    assert dedicated["calls"] == ["Spin"]


def test_apply_point_dedicated_hold_bad_duration(dedicated):
    dedicated["value"] = {"queued": True}
    r = au.AlertObj(duration=2)
    au._apply_point_dedicated_hold(r)
    assert r.hold_queue_only is True and r.duration == 2


@pytest.mark.parametrize("value", [None, {}, {"queued": False}, RuntimeError("x")])
def test_apply_point_dedicated_hold_no_template(dedicated, value):
    dedicated["value"] = value
    r = au.AlertObj()
    au._apply_point_dedicated_hold(r)
    assert r.hold_queue_only is False


@pytest.mark.parametrize("media", [{"gif_dir": "d", "gif_name": "g"}, {"single_audio_dir": "d", "single_audio_name": "a"},
                                   {"randomized": True}])
def test_apply_point_dedicated_hold_with_media(dedicated, media):
    r = au.AlertObj(**media)
    au._apply_point_dedicated_hold(r)
    assert r.hold_queue_only is False


def test_build_replay_alert_uses_config_and_stored_fields(env, monkeypatch):
    monkeypatch.setattr(au.time, "time", lambda: 50.0)
    config = au.AlertObj(alert_type="raid", duration=9, gif_dir="/new", username="placeholder", played=True,
                         skip_alert=True, deleted=True, is_test=True)
    stored = {"alert_type": "raid", "username": "Raider", "raider_count": 12, "message": None,
              "gif_dir": "/old"}
    r = au.build_replay_alert(stored, config)
    assert r is not config
    assert r.username == "Raider" and r.raider_count == 12 and r.message is None
    assert r.gif_dir == "/new" and r.duration == 9
    assert r.alert_id.startswith("Replay") and r.timestamp == 50.0
    assert (r.played, r.deleted, r.skip_alert, r.is_replay, r.is_test, r.stackable) == \
        (False, False, False, True, False, True)
    assert config.username == "placeholder"


def test_build_replay_alert_without_config_uses_snapshot(env, monkeypatch):
    monkeypatch.setattr(au, "fetch_alert_config_for_stored", lambda s: None)
    r = au.build_replay_alert({"alert_type": "follow", "username": "F", "gif_name": "s.gif", "junk": 1})
    assert r.gif_name == "s.gif" and r.username == "F" and r.is_replay


def test_build_replay_alert_non_dict(env, monkeypatch):
    monkeypatch.setattr(au, "fetch_alert_config_for_stored", lambda s: None)
    r = au.build_replay_alert("garbage")
    assert r.alert_type == "" and r.is_replay


def test_build_replay_alert_point_keeps_title_and_holds(env, dedicated):
    config = au.AlertObj(alert_type="point", alert_name="Config Name", hold_queue_only=True)
    r = au.build_replay_alert({"alert_type": "point", "alert_name": "Hydrate"}, config)
    assert r.alert_name == "Hydrate"
    assert r.hold_queue_only is True and r.duration == 7.0  # no media -> dedicated hold


def test_build_replay_alert_point_without_config_skips_hold(env, dedicated, monkeypatch):
    r = au.build_replay_alert({"alert_type": "points"}, None)
    assert r.hold_queue_only is False and dedicated["calls"] == []


def test_replay_point_hold(env, dedicated, monkeypatch):
    monkeypatch.setattr(au.time, "time", lambda: 9.0)
    assert au._replay_point_hold({}) is None
    hold = au._replay_point_hold({"alert_name": "Spin", "username": "U", "message": None})
    assert hold.alert_type == "point" and hold.alert_name == "Spin" and hold.username == "U"
    assert hold.duration == 7.0 and hold.hold_queue_only and not hold.enable_alert
    assert hold.is_replay and hold.stackable and hold.timestamp == 9.0
    dedicated["value"] = {"queued": True, "duration_seconds": None}
    assert au._replay_point_hold({"alert_name": "Spin"}).duration == 0.0
    for v in (None, {"queued": False}, RuntimeError("x")):
        dedicated["value"] = v
        assert au._replay_point_hold({"alert_name": "Spin"}) is None


def test_build_replay_instant_payload(monkeypatch):
    monkeypatch.setattr(au.time, "time", lambda: 77.0)
    replay = au.AlertObj(alert_name="Conf", duration=4, gif_dir="", gif_name="g.gif", single_audio_dir=None)
    p = au.build_replay_instant_payload(
        {"alert_type": "bit", "username": "U", "amt_cheered": 100, "source": "twitch", "alert_id": "OLD",
         "alert_name": "Stored", "message": None}, "NEW", replay)
    assert p["type"] == "cheer" and p["alert_type"] == "bit"
    assert p["username"] == "U" and p["amt_cheered"] == 100 and p["source"] == "twitch"
    assert "message" not in p
    assert p["alert_name"] == "Stored"  # stored wins over replay config
    assert p["duration"] == 4 and p["gif_name"] == "g.gif" and "gif_dir" not in p
    assert p["alert_id"] == "NEW" and p["original_alert_id"] == "OLD"
    assert p["timestamp"] == 77.0 and p["is_replay"] is True and p["is_test"] is False


def test_build_replay_instant_payload_hype_train_and_non_dict():
    p = au.build_replay_instant_payload({"alert_type": "hype_train_end", "hype_train_level": 3,
                                         "username": "Conductor"}, "N")
    assert p["type"] == "hype_train_end" and p["level"] == 3 and p["conductor_name"] == "Conductor"
    p = au.build_replay_instant_payload({"alert_type": "hype_train_start", "level": 2}, "N")
    assert p["level"] == 2 and "conductor_name" not in p
    p = au.build_replay_instant_payload(None, "N")
    assert p["type"] == "" and p["original_alert_id"] == ""
    assert au.build_replay_instant_payload({"alert_type": "point"}, "N")["type"] == "points"


def test_emit_replay_instant(monkeypatch):
    sent = []
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    assert au._emit_replay_instant({"a": 1}) is False
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(safe_emit=lambda ev, p: sent.append((ev, p)) or 1), raising=False)
    assert au._emit_replay_instant({"a": 1}) is True
    assert sent == [(au.REPLAY_INSTANT_EVENT, {"a": 1})]
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(safe_emit=lambda ev, p: (_ for _ in ()).throw(RuntimeError())),
                        raising=False)
    assert au._emit_replay_instant({}) is False


# ======================================================================= replay_alert

@pytest.fixture
def replay_env(env, monkeypatch):
    db, mgr = env
    queued, emitted = [], []
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: queued.append(a))
    monkeypatch.setattr(au, "_emit_replay_instant", lambda p: emitted.append(p) or True)
    return SimpleNamespace(db=db, mgr=mgr, queued=queued, emitted=emitted)


def test_replay_alert_missing_and_not_found(replay_env):
    assert au.replay_alert("  ") == {"ok": False, "reason": "missing_id", "new_alert_id": None}
    assert au.replay_alert(None)["reason"] == "missing_id"
    assert au.replay_alert("nope")["reason"] == "not_found"


def test_replay_alert_lookup_error(replay_env, monkeypatch):
    monkeypatch.setattr(replay_env.mgr, "get_stored_alert_by_id", lambda a: (_ for _ in ()).throw(RuntimeError()))
    assert au.replay_alert("x")["reason"] == "error"


def test_replay_alert_instant_only_type(replay_env):
    replay_env.mgr._alert_storage["H1"] = {"alert_type": "hype_train_progress", "hype_train_level": 2}
    r = au.replay_alert("H1")
    assert r["ok"] and r["reason"] == "instant_only" and r["new_alert_id"].startswith("Replay")
    assert replay_env.queued == []
    assert replay_env.emitted[0]["original_alert_id"] == "H1"


def test_replay_alert_config_error(replay_env, monkeypatch):
    replay_env.mgr._alert_storage["B1"] = {"alert_type": "bit"}
    monkeypatch.setattr(au, "fetch_alert_config_for_stored", lambda s: (_ for _ in ()).throw(RuntimeError()))
    assert au.replay_alert("B1")["reason"] == "error"


def test_replay_alert_no_config(replay_env):
    replay_env.mgr._alert_storage["F1"] = {"alert_type": "follow", "username": "x"}
    assert au.replay_alert("F1") == {"ok": False, "reason": "no_config", "new_alert_id": None}


def test_replay_alert_queued(replay_env):
    replay_env.mgr._alert_state["follows"] = {"follow1": _cfg("Follow")}
    replay_env.mgr._alert_storage["F1"] = {"alert_type": "follow", "username": "Fan"}
    r = au.replay_alert("F1")
    assert r["ok"] and r["reason"] == "queued"
    (replay,) = replay_env.queued
    assert replay.username == "Fan" and replay.alert_id == r["new_alert_id"]
    assert replay_env.emitted[0]["gif_name"] == "Follow.gif"


def test_replay_alert_point_paths(replay_env, dedicated):
    mgr = replay_env.mgr
    # unconfigured reward with a queued dedicated template -> hold queued
    mgr._alert_storage["P1"] = {"alert_type": "point", "twitch_reward_id": "rw", "alert_name": "Spin"}
    r = au.replay_alert("P1")
    assert r["reason"] == "queued" and replay_env.queued[-1].hold_queue_only
    # unconfigured, no template -> instant only
    dedicated["value"] = None
    r = au.replay_alert("P1")
    assert r["reason"] == "instant_only" and len(replay_env.queued) == 1
    # configured but alert disabled -> instant only
    mgr._alert_state["points"] = {"rw": _cfg("Spin", enable_alert=False)}
    r = au.replay_alert("P1")
    assert r["reason"] == "instant_only" and len(replay_env.queued) == 1
    # configured and enabled -> queued
    mgr._alert_state["points"] = {"rw": _cfg("Spin", enable_alert=True)}
    r = au.replay_alert("P1")
    assert r["reason"] == "queued" and len(replay_env.queued) == 2


# ======================================================================= chat media

@pytest.mark.parametrize("data,expected", [
    ({"badge_type": "Gift Sub", "alert_type": "x"}, "giftsub"),
    ({"badge_type": "", "alert_type": "Follow"}, "follow"),
    ({"badge_type": None, "type": "RAID"}, "raid"),
    ({}, ""),
])
def test_normalize_chat_alert_type(data, expected):
    assert au._normalize_chat_alert_type(data) == expected


def test_chat_alert_lookup():
    data = {"a": None, "b": 2, "stored_alert_data": {"a": 1, "c": None, "d": 4}}
    assert au._chat_alert_lookup(data, "a") == 1
    assert au._chat_alert_lookup(data, "b", "a") == 2
    assert au._chat_alert_lookup(data, "c", "d") == 4
    assert au._chat_alert_lookup({"stored_alert_data": "x"}, "a") is None


def test_alert_obj_media():
    assert au._alert_obj_media(None) is None
    assert au._alert_obj_media(au.AlertObj(gif_dir=" d ", gif_name="g")) == {"gif_dir": "d", "gif_name": "g"}
    assert au._alert_obj_media(au.AlertObj(gif_dir="d")) is None


def test_media_from_alert_data_fields():
    assert au._media_from_alert_data_fields({"chat_media": {"gif_dir": "c", "gif_name": "c.gif"}}) == \
        {"gif_dir": "c", "gif_name": "c.gif"}
    assert au._media_from_alert_data_fields({"chat_media": {"gif_dir": "c"},
                                             "stored_alert_data": {"gif_dir": "s", "gif_name": "s.gif"}}) == \
        {"gif_dir": "s", "gif_name": "s.gif"}
    assert au._media_from_alert_data_fields({"stored_alert_data": {"gif_dir": "s"}, "gif_dir": "t",
                                             "gif_name": "t.gif"}) == {"gif_dir": "t", "gif_name": "t.gif"}
    assert au._media_from_alert_data_fields({"gif_dir": "t"}) is None


def test_preview_placeholder_media(monkeypatch):
    import random

    monkeypatch.setattr(random, "choice", lambda seq: seq[1])
    assert au._preview_placeholder_media() == {"gif_dir": au._CHAT_PREVIEW_PLACEHOLDER_DIR,
                                               "gif_name": "cheer1000.gif"}


def test_find_range_amount_alert():
    alerts = {"bits5": _cfg("exact"), "bitsx-y": _cfg("bad"), "bits1-3": _cfg("pref"), "10-20": _cfg("plain"),
              "bits": None}
    assert au._find_range_amount_alert(alerts, 2, prefix="bits", prefix_strip_len=4).alert_name == "pref"
    assert au._find_range_amount_alert(alerts, 15.5, prefix="bits", prefix_strip_len=4).alert_name == "plain"
    assert au._find_range_amount_alert(alerts, 99, prefix="bits", prefix_strip_len=4) is None


def test_exact_fetchers(env, monkeypatch):
    _, mgr = env
    mgr._alert_state.update({
        "subs": {"subs3": _cfg("s3"), "sub1": _cfg("default")},
        "bits": {"bits100": _cfg("b100")}, "bit_ranges": {"bits1-50": _cfg("brange")},
        "giftsubs": {"giftsubs5": _cfg("g5")}, "giftsub_ranges": {"giftsubs10-20": _cfg("grange")},
        "donations": {"donations5": _cfg("d5")}, "donation_ranges": {"donations10-20": _cfg("drange")},
        "raids": {"raids7": _cfg("r7")}, "raid_ranges": {"raids10-20": _cfg("rrange")},
        "streaks": {"streaks3": _cfg("st3")}, "streak_ranges": {"streaks5-9": _cfg("strange")},
    })
    assert au._fetch_sub_alert_exact(3).alert_name == "s3"
    assert au._fetch_sub_alert_exact(4) is None
    assert au._fetch_bits_alert_exact(100).alert_name == "b100"
    assert au._fetch_bits_alert_exact(25).alert_name == "brange"
    assert au._fetch_bits_alert_exact(75) is None
    assert au._fetch_giftsub_alert_exact(5).alert_name == "g5"
    assert au._fetch_giftsub_alert_exact(15).alert_name == "grange"
    assert au._fetch_donation_alert_exact(5.0).alert_name == "d5"
    assert au._fetch_donation_alert_exact(12.5).alert_name == "drange"
    assert au._fetch_donation_alert_exact(5.5) is None
    assert au._fetch_raid_alert_exact(7).alert_name == "r7"
    assert au._fetch_raid_alert_exact(15).alert_name == "rrange"
    assert au._fetch_streak_alert_exact(3).alert_name == "st3"
    assert au._fetch_streak_alert_exact(6).alert_name == "strange"


def test_giftsub_exact_loads_paths_when_empty(env, monkeypatch):
    _, mgr = env
    loads = []
    monkeypatch.setattr(mgr, "ensure_alert_paths_loaded", lambda keys: loads.append(keys))
    assert au._fetch_giftsub_alert_exact(1) is None
    assert loads == [["giftsubs", "giftsub_ranges"]]


@pytest.fixture
def exact_spy(monkeypatch):
    calls = []
    for name in ("_fetch_sub_alert_exact", "_fetch_giftsub_alert_exact", "_fetch_bits_alert_exact",
                 "_fetch_donation_alert_exact", "_fetch_raid_alert_exact", "_fetch_streak_alert_exact"):
        monkeypatch.setattr(au, name, lambda v, _n=name: calls.append((_n, v)) or _n)
    monkeypatch.setattr(au, "fetch_follow_alert", lambda: calls.append(("follow", None)) or "follow")
    return calls


@pytest.mark.parametrize("alert_type,data,expected", [
    ("follow", {}, ("follow", None)),
    ("sub", {}, ("_fetch_sub_alert_exact", 1)),
    ("resub", {"months": "6"}, ("_fetch_sub_alert_exact", 6)),
    ("resub", {}, ("_fetch_sub_alert_exact", 2)),
    ("resub", {"resub_month": "x"}, ("_fetch_sub_alert_exact", 2)),
    ("resub", {"resub_month": 1}, ("_fetch_sub_alert_exact", 2)),
    ("giftsub", {"quantity": 4}, ("_fetch_giftsub_alert_exact", 4)),
    ("giftsub", {}, ("_fetch_giftsub_alert_exact", 1)),
    ("giftsub", {"gift_qty": "x"}, ("_fetch_giftsub_alert_exact", 1)),
    ("giftsub", {"gift_qty": -3}, ("_fetch_giftsub_alert_exact", 1)),
    ("bits", {"bits": "250"}, ("_fetch_bits_alert_exact", 250)),
    ("donation", {"amount": "4.6"}, ("_fetch_donation_alert_exact", 5)),
    ("donation", {"donation_amount": 0.2}, ("_fetch_donation_alert_exact", 1)),
    ("raid", {"viewers": 30}, ("_fetch_raid_alert_exact", 30)),
    ("watchstreak", {"streak_count": "5"}, ("_fetch_streak_alert_exact", 5)),
])
def test_fetch_chat_alert_for_media_dispatch(exact_spy, alert_type, data, expected):
    au._fetch_chat_alert_for_media(alert_type, data)
    assert exact_spy == [expected]


@pytest.mark.parametrize("alert_type,data", [
    ("bit", {}), ("bit", {"amount": "x"}), ("bit", {"amount": 0}),
    ("donation", {}), ("donation", {"amount": "x"}), ("donation", {"amount": -1}),
    ("raid", {}), ("raid", {"raider_count": "x"}),
    ("streak", {}), ("streak", {"streak_count": "x"}),
    ("unknown", {}),
])
def test_fetch_chat_alert_for_media_none(exact_spy, alert_type, data):
    assert au._fetch_chat_alert_for_media(alert_type, data) is None
    assert exact_spy == []


def test_resolve_chat_alert_media(env, monkeypatch):
    _, mgr = env
    assert au.resolve_chat_alert_media("x") is None
    assert au.resolve_chat_alert_media({}) is None
    assert au.resolve_chat_alert_media({"alert_type": "points"}) is None
    assert au.resolve_chat_alert_media({"alert_type": "chat"}) is None
    mgr._alert_state["bits"] = {"bits100": _cfg("hundred")}
    assert au.resolve_chat_alert_media({"alert_type": "bits", "amt_cheered": 100}) == \
        {"gif_dir": "/g/hundred", "gif_name": "hundred.gif"}
    assert au.resolve_chat_alert_media({"alert_type": "bits", "amt_cheered": 5}) is None
    monkeypatch.setattr(au, "_preview_placeholder_media", lambda: {"gif_dir": "p", "gif_name": "p.gif"})
    assert au.resolve_chat_alert_media({"alert_type": "bits", "amt_cheered": 5}, preview_placeholder=True) == \
        {"gif_dir": "p", "gif_name": "p.gif"}


# ======================================================================= initialize_alert_state*

def test_initialize_alert_state_functions(env, monkeypatch):
    calls = []

    class FakeMgr:
        def initialize(self):
            calls.append("init")

        def initialize_with_data(self, data):
            calls.append(("data", data))

        async def initialize_async(self):
            calls.append("async")

    monkeypatch.setattr(au, "alert_state_manager", None)
    monkeypatch.setattr(au, "AlertStateManager", FakeMgr)
    au.initialize_alert_state()
    assert isinstance(au.alert_state_manager, FakeMgr)
    monkeypatch.setattr(au, "alert_state_manager", None)
    au.initialize_alert_state_with_data({"a": 1})
    monkeypatch.setattr(au, "alert_state_manager", None)
    asyncio.run(au.initialize_alert_state_async())
    assert calls == ["init", ("data", {"a": 1}), "async"]
    # existing manager is reused
    existing = au.alert_state_manager
    au.initialize_alert_state()
    au.initialize_alert_state_with_data({})
    asyncio.run(au.initialize_alert_state_async())
    assert au.alert_state_manager is existing and len(calls) == 6

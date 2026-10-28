# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alert_playback_session.py: route matching, holders, session handshake branches."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alert_playback_session as aps  # noqa: E402
from modules import template_config_parser as tcp  # noqa: E402


@pytest.fixture(autouse=True)
def reset_session(monkeypatch):
    monkeypatch.setattr(aps, "_current_session", None)
    yield


def _session(**kw):
    defaults = dict(seq=1, holders=("alerts",), duration_sec=5.0, delay_between_sec=0.5,
                    started_at=100.0, registration_deadline=101.5)
    defaults.update(kw)
    return aps.AlertPlaybackSession(**defaults)


# ---------------------------------------------------------------- pure helpers

@pytest.mark.parametrize("value,expected", [
    ("Alerts", "alerts"), (" My-Route_1 ", "myroute1"), (None, ""), ("", ""), ("!!", ""), (12, "12"),
])
def test_normalize_holder_route(value, expected):
    assert aps.normalize_holder_route(value) == expected


@pytest.mark.parametrize("route,holders,expected", [
    ("", ["alerts"], False),
    (None, ["alerts"], False),
    ("/alerts", ["alerts"], True),
    ("alerts", ["/alerts"], True),
    ("My%20Route", ["My Route"], True),
    ("my-route", ["MyRoute"], True),
    ("---", ["---"], True),           # exact match before normalization
    ("---", ["alerts"], False),       # normalizes to empty
    ("other", ["alerts", " ", ""], False),
])
def test_holder_route_allowed(route, holders, expected):
    assert aps.holder_route_allowed(route, holders) is expected


@pytest.mark.parametrize("value,expected", [
    (3, 3.0), ("2.5", 2.5), (0, 5.0), (-1, 5.0), ("x", 5.0), (None, 5.0),
])
def test_coerce_duration_seconds(value, expected):
    assert aps.coerce_duration_seconds(value) == expected


def test_coerce_duration_custom_default():
    assert aps.coerce_duration_seconds(None, default=9.0) == 9.0


def test_queue_holder_routes_dict_without_hold():
    assert aps.queue_holder_routes_for_alert({"alert_name": "x"}) == ["alerts"]


def test_queue_holder_routes_dict_hold_only(monkeypatch):
    monkeypatch.setattr(tcp, "find_matching_template_routes_for_reward_title",
                        lambda name: ["wheel", "", "alerts", "wheel2"])
    assert aps.queue_holder_routes_for_alert({"hold_queue_only": True, "alert_name": "Spin"}) == \
        ["alerts", "wheel", "wheel2"]


def test_queue_holder_routes_object(monkeypatch):
    seen = []
    monkeypatch.setattr(tcp, "find_matching_template_routes_for_reward_title",
                        lambda name: seen.append(name) or ["r1"])
    alert = SimpleNamespace(hold_queue_only=True, alert_name="Reward")
    assert aps.queue_holder_routes_for_alert(alert) == ["alerts", "r1"]
    assert seen == ["Reward"]


def test_queue_holder_routes_hold_only_blank_name(monkeypatch):
    monkeypatch.setattr(tcp, "find_matching_template_routes_for_reward_title",
                        lambda name: pytest.fail("should not look up"))
    assert aps.queue_holder_routes_for_alert(SimpleNamespace(hold_queue_only=True, alert_name="  ")) == ["alerts"]
    assert aps.queue_holder_routes_for_alert(object()) == ["alerts"]


def test_queue_holder_routes_lookup_error(monkeypatch):
    def boom(name):
        raise RuntimeError("bad templates")

    monkeypatch.setattr(tcp, "find_matching_template_routes_for_reward_title", boom)
    assert aps.queue_holder_routes_for_alert({"hold_queue_only": True, "alert_name": "x"}) == ["alerts"]


def test_delay_between_alerts(monkeypatch):
    monkeypatch.setattr(tcp, "delay_between_alerts_seconds", lambda: 2.25)
    assert aps.delay_between_alerts_seconds() == 2.25

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tcp, "delay_between_alerts_seconds", boom)
    assert aps.delay_between_alerts_seconds() == aps.DEFAULT_DELAY_BETWEEN_SEC


# ---------------------------------------------------------------- session internals

def test_mark_finished_only_once():
    s = _session()
    assert s._mark_finished_unlocked("a") is True
    assert s._mark_finished_unlocked("b") is False
    assert s.finish_reason == "a"


def test_drop_silent_uses_started_at_and_skips_completed():
    s = _session(required_sids={"a", "b", "c"}, completed_sids={"c"}, last_progress_at={"b": 102.5})
    s._drop_silent_unlocked(103.0)  # cutoff 100.0: a (started_at=100) dropped, b kept
    assert s.completed_sids == {"a", "c"}


def test_maybe_finish_already_finished():
    s = _session(finished=True)
    assert s._maybe_finish_unlocked(1e9) is False


def test_maybe_finish_all_holders_done():
    s = _session(required_sids={"a"}, completed_sids={"a"})
    assert s.tick(now=100.1) is True and s.finish_reason == "all_holders_done"


def test_maybe_finish_absolute_timeout():
    s = _session(required_sids={"a"})
    s.last_progress_at["a"] = 100 + aps.ABSOLUTE_CAP_SEC
    assert s.tick(now=100 + aps.ABSOLUTE_CAP_SEC) is True
    assert s.finish_reason == "absolute_timeout"


def test_maybe_finish_no_ack_fallback_timing():
    s = _session()
    assert s.tick(now=101.0) is False  # within registration window
    fallback = 100.0 + 5.0 + 0.5 + aps.NO_ACK_GRACE_SEC
    assert s.tick(now=fallback - 0.01) is False
    assert s.tick(now=fallback) is True and s.finish_reason == "no_ack_fallback"


def test_maybe_finish_pending_holder_keeps_open():
    s = _session(required_sids={"a"}, last_progress_at={"a": 105.0})
    assert s.tick(now=106.0) is False


def test_tick_default_now(monkeypatch):
    monkeypatch.setattr(aps.time, "monotonic", lambda: 100.2)
    s = _session()
    assert s.tick() is False


def test_force_finish():
    s = _session()
    assert s.force_finish("x") is True
    assert s.force_finish("y") is False


# ---------------------------------------------------------------- note_* methods

def test_note_playing_rejections(monkeypatch):
    monkeypatch.setattr(aps.time, "monotonic", lambda: 100.0)
    s = _session()
    assert s.note_playing("sid", "alerts", is_preview=True) is False
    assert s.note_playing("", "alerts") is False
    assert s.note_playing("sid", "spectator") is False
    s.finished = True
    assert s.note_playing("sid", "alerts") is False
    assert s.required_sids == set()


def test_note_playing_accepts():
    s = _session()
    assert s.note_playing("sid", "/Alerts", now=100.3) is True
    assert s.required_sids == {"sid"} and s.last_progress_at["sid"] == 100.3


def test_note_playing_finished_inside_lock(monkeypatch):
    s = _session()

    def allowed(route):
        s.finished = True  # finishes between the fast check and the lock
        return True

    monkeypatch.setattr(s, "_route_allowed", allowed)
    assert s.note_playing("sid", "alerts", now=1.0) is False


def test_note_progress_paths(monkeypatch):
    monkeypatch.setattr(aps.time, "monotonic", lambda: 104.0)
    s = _session()
    assert s.note_progress("sid", is_preview=True) is False
    assert s.note_progress("") is False
    assert s.note_progress("unknown") is False
    s.note_playing("sid", "alerts", now=100.5)
    assert s.note_progress("sid") is True
    assert s.last_progress_at["sid"] == 104.0
    s.finished = True
    assert s.note_progress("sid") is False


def test_note_progress_finished_inside_lock():
    s = _session(required_sids={"sid"})

    class FlipLock:
        def __enter__(self):
            s.finished = True

        def __exit__(self, *a):
            return False

    s._lock = FlipLock()
    assert s.note_progress("sid", now=1.0) is False


def test_note_complete_paths(monkeypatch):
    monkeypatch.setattr(aps.time, "monotonic", lambda: 100.4)
    s = _session()
    assert s.note_complete("sid", "alerts", is_preview=True) == (False, False)
    assert s.note_complete("", "alerts") == (False, False)
    assert s.note_complete("sid", "spectator") == (False, False)
    s.note_playing("other", "alerts")
    assert s.note_complete("sid", "alerts") == (True, False)  # "other" still pending
    assert s.note_complete("other", "alerts") == (True, True)
    assert s.note_complete("late", "alerts") == (False, False)


def test_note_disconnect_paths(monkeypatch):
    monkeypatch.setattr(aps.time, "monotonic", lambda: 100.5)
    s = _session()
    assert s.note_disconnect("") is False
    assert s.note_disconnect("stranger") is False
    s.note_playing("a", "alerts")
    s.note_playing("b", "alerts")
    assert s.note_disconnect("a") is False
    assert s.note_disconnect("b") is True
    assert s.note_disconnect("b") is False  # finished


# ---------------------------------------------------------------- module session API

def test_begin_session_cleans_holders(monkeypatch):
    monkeypatch.setattr(aps.time, "monotonic", lambda: 50.0)
    s = aps.begin_session("7", [" a ", "a", "", "b"], "x", None)
    assert s.seq == 7 and s.holders == ("a", "b")
    assert s.duration_sec == aps.DEFAULT_DURATION_SEC
    assert s.delay_between_sec == 0.0
    assert s.started_at == 50.0 and s.registration_deadline == 50.0 + aps.REGISTRATION_WINDOW_SEC
    assert aps.get_session() is s


def test_begin_session_empty_holders_and_negative_delay():
    s = aps.begin_session(1, [" ", ""], 3, -4, now=1.0)
    assert s.holders == ("alerts",) and s.delay_between_sec == 0.0 and s.duration_sec == 3.0


def test_begin_session_supersedes_unfinished_only():
    first = aps.begin_session(1, ["alerts"], 3, now=1.0)
    second = aps.begin_session(2, ["alerts"], 3, now=2.0)
    assert first.finished and first.finish_reason == "superseded"
    second.force_finish("done")
    third = aps.begin_session(3, ["alerts"], 3, now=3.0)
    assert second.finish_reason == "done"
    assert aps.get_session() is third


def test_abort_session():
    assert aps.abort_session() is False
    s = aps.begin_session(1, ["alerts"], 3, now=1.0)
    assert aps.abort_session("stop") is True
    assert s.finish_reason == "stop"
    assert aps.abort_session() is False


def test_reset_session_for_tests():
    aps.begin_session(1, ["alerts"], 3, now=1.0)
    aps.reset_session_for_tests()
    assert aps.get_session() is None

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.service_alerts`` (which status changes deserve a notification)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import service_alerts as sa


def test_tables_line_up():
    assert set(sa.SOURCES) == set(sa.LABELS)
    assert set(sa.SETTINGS_TABS) <= set(sa.LABELS)
    assert set(sa.AUTH_HINTS) <= set(sa.HINTS)


@pytest.mark.parametrize(
    "key,status,expected",
    [
        ("twitch", None, "off"), ("twitch", "   ", "off"),
        ("internet", "Online", "ok"), ("internet", "offline", "problem"), ("internet", "No internet", "problem"),
        ("internet", "unreachable host", "problem"), ("internet", "checking", "busy"),
        ("webengine", "running", "ok"), ("webengine", "crashed", "problem"), ("webengine", "stopped", "problem"),
        ("webengine", "Frozen (no heartbeat)", "problem"), ("webengine", "starting", "busy"),
        ("psn", "Token Expired", "off"), ("psn", "expired", "problem"),
        ("obs", "Auth Failed", "off"),
        ("twitch", "Connected as bob", "ok"), ("twitch", "online", "ok"), ("spotify", "ready", "ok"),
        ("youtube", "live", "ok"), ("discord", "running", "ok"),
        ("twitch", "Not configured", "off"), ("twitch", "disabled", "off"), ("twitch", "Off", "off"),
        ("twitch", "not configured token expired", "problem"),
        ("twitch", "Reconnecting", "busy"), ("twitch", "connecting error", "problem"),
        ("twitch", "Error", "problem"), ("obs", "Can't connect", "problem"), ("twitch", "Disconnected", "problem"),
        ("twitch", "lost", "problem"),
        ("twitch", "authenticated? not", "problem"),
        ("twitch", "something odd", "busy"),
    ],
)
def test_classify(key, status, expected):
    assert sa.classify(key, status) == expected


def feed(tracker, statuses, times):
    out = []
    for _ in range(times):
        out += tracker.observe(statuses)
    return out


def test_new_service_ok_settles_immediately_and_never_notifies():
    t = sa.ServiceAlertTracker()
    assert t.observe({"twitch": "Connected"}) == []
    assert t.tracks["twitch"].stable == "ok"
    assert t.tracks["twitch"].raw == "Connected"


def test_busy_states_never_settle():
    t = sa.ServiceAlertTracker()
    assert feed(t, {"twitch": "Reconnecting"}, 30) == []
    assert t.tracks["twitch"].stable == "unknown"


def test_problem_from_startup_needs_startup_polls():
    t = sa.ServiceAlertTracker()
    assert feed(t, {"discord": "Error"}, sa.STARTUP_POLLS - 1) == []
    (notice,) = t.observe({"discord": "Error"})
    assert notice.kind == "problem" and notice.type == "warning"
    assert notice.title == "Discord disconnected"
    assert notice.message == "Status: Error"
    assert notice.hint == sa.HINTS["discord"]
    assert notice.source == "discord" and notice.settings_tab == "Discord"
    assert notice.dedupe_key == "svc:discord:problem"
    assert t.tracks["discord"].warned is True


def test_problem_after_ok_needs_stable_polls_then_recovery_notice():
    t = sa.ServiceAlertTracker()
    t.observe({"spotify": "Connected"})
    assert feed(t, {"spotify": "Disconnected"}, sa.STABLE_POLLS - 1) == []
    # flapping back to ok resets the candidate
    assert t.observe({"spotify": "Connected"}) == []
    assert t.tracks["spotify"].candidate == "" and t.tracks["spotify"].count == 0
    notes = feed(t, {"spotify": "Disconnected"}, sa.STABLE_POLLS)
    assert [n.kind for n in notes] == ["problem"]
    notes = feed(t, {"spotify": "Connected"}, sa.STABLE_POLLS)
    assert len(notes) == 1
    rec = notes[0]
    assert rec.kind == "recovered" and rec.type == "success" and rec.title == "Spotify reconnected"
    assert rec.dedupe_key == "svc:spotify:recovered" and rec.settings_tab == "Spotify"
    assert t.tracks["spotify"].warned is False


def test_candidate_switch_resets_count():
    t = sa.ServiceAlertTracker()
    t.observe({"twitch": "Connected"})
    feed(t, {"twitch": "Error"}, 2)
    assert t.tracks["twitch"].count == 2
    t.observe({"twitch": "Not configured"})
    assert t.tracks["twitch"].candidate == "off" and t.tracks["twitch"].count == 1


def test_recovery_without_warning_is_silent():
    t = sa.ServiceAlertTracker()
    t.observe({"twitch": "Connected"})
    t.tracks["twitch"].stable = "problem"
    assert feed(t, {"twitch": "Connected"}, sa.STABLE_POLLS) == []


def test_auth_problem_title_and_hint():
    t = sa.ServiceAlertTracker()
    t.observe({"twitch": "Connected"})
    (n,) = feed(t, {"twitch": "Token expired"}, sa.STABLE_POLLS)
    assert n.title == "Twitch needs you to sign in again"
    assert n.type == "error" and n.hint == sa.AUTH_HINTS["twitch"]


def test_authenticated_but_disconnected_is_not_auth():
    t = sa.ServiceAlertTracker()
    t.observe({"twitch": "Connected"})
    (n,) = feed(t, {"twitch": "Authenticated but Disconnected"}, sa.STABLE_POLLS)
    assert n.title == "Twitch disconnected" and n.type == "warning"


def test_auth_word_for_service_without_auth_hint():
    t = sa.ServiceAlertTracker()
    t.observe({"psn": "Connected"})
    (n,) = feed(t, {"psn": "invalid credentials"}, sa.STABLE_POLLS)
    assert n.title == "PlayStation disconnected" and n.hint == sa.HINTS["psn"]


def test_obs_cant_connect_has_special_copy():
    t = sa.ServiceAlertTracker()
    t.observe({"obs": "Connected"})
    (n,) = feed(t, {"obs": "Can't connect"}, sa.STABLE_POLLS)
    assert n.title == "Mycelian can't connect to OBS"
    assert n.hint == sa.OBS_OPEN_HINT
    assert "WebSocket" in n.message
    assert n.settings_tab == "OBS"


def test_webengine_problem_titles():
    t = sa.ServiceAlertTracker()
    t.observe({"webengine": "running"})
    (n,) = feed(t, {"webengine": "Crashed"}, sa.STABLE_POLLS)
    assert n.title == "Overlay server crashed" and n.type == "error"
    assert n.source == "overlay_server" and n.settings_tab is None


def test_webengine_problem_with_empty_raw_says_stopped():
    t = sa.ServiceAlertTracker()
    t.tracks["webengine"] = sa._Track(stable="ok")
    # Force a problem whose raw text is empty by stepping manually
    t._step = lambda key, status: (setattr(t.tracks[key], "raw", ""), "problem")[1]
    (n,) = t._observe_service("webengine", "")
    assert n.title == "Overlay server stopped" and n.message == ""


def test_unknown_key_uses_key_as_label_and_app_source():
    t = sa.ServiceAlertTracker()
    t.observe({"widget": "Connected"})
    (n,) = feed(t, {"widget": "Error"}, sa.STABLE_POLLS)
    assert n.title == "widget disconnected" and n.source == "app" and n.settings_tab is None


def test_internet_offline_then_online_with_grace():
    t = sa.ServiceAlertTracker()
    t.observe({"internet": "online", "twitch": "Connected"})
    notes = feed(t, {"internet": "offline", "twitch": "Error"}, sa.STABLE_POLLS)
    assert [n.kind for n in notes] == ["offline"]
    off = notes[0]
    assert off.title == "You're offline" and off.dedupe_key == "svc:internet:offline" and off.type == "warning"
    assert t.offline and t.offline_warned
    # while offline, service samples never notify and reset candidates
    assert feed(t, {"internet": "offline", "twitch": "Error"}, 20) == []
    assert t.tracks["twitch"].count == 0 and t.tracks["twitch"].raw == "Error"
    notes = feed(t, {"internet": "online"}, sa.STABLE_POLLS)
    assert [n.kind for n in notes] == ["online"]
    assert notes[0].title == "Back online" and notes[0].type == "success"
    assert not t.offline and not t.offline_warned
    assert t.grace == sa.RECOVERY_GRACE_POLLS - 1  # one poll of grace used by the same observe
    # During grace a stable problem is held back
    assert feed(t, {"twitch": "Error"}, sa.STABLE_POLLS) == []
    assert t.tracks["twitch"].stable == "ok"
    assert t.tracks["twitch"].candidate == "problem"


def test_offline_resets_other_candidates():
    t = sa.ServiceAlertTracker()
    t.observe({"internet": "online", "twitch": "Connected"})
    feed(t, {"twitch": "Error"}, 2)
    feed(t, {"internet": "offline"}, sa.STABLE_POLLS)
    assert t.tracks["twitch"].candidate == "" and t.tracks["twitch"].count == 0


def test_back_online_without_prior_warning_is_silent():
    t = sa.ServiceAlertTracker()
    t.tracks["internet"] = sa._Track(stable="problem")
    t.offline = True
    assert feed(t, {"internet": "online"}, sa.STABLE_POLLS) == []
    assert t.offline is False and t.grace == sa.RECOVERY_GRACE_POLLS - 1


def test_internet_ok_from_unknown_is_silent():
    t = sa.ServiceAlertTracker()
    assert t.observe({"internet": "online"}) == []
    assert t.tracks["internet"].stable == "ok"


def test_grace_counts_down_each_observe():
    t = sa.ServiceAlertTracker(grace=2)
    t.observe({})
    assert t.grace == 1
    t.observe({})
    t.observe({})
    assert t.grace == 0


def test_forget():
    t = sa.ServiceAlertTracker()
    t.observe({"twitch": "Connected"})
    t.forget("twitch")
    t.forget("never-seen")
    assert "twitch" not in t.tracks


def test_notice_defaults():
    n = sa.Notice("k", "problem", "warning", "T")
    assert (n.message, n.hint, n.source, n.settings_tab, n.dedupe_key) == ("", "", "app", None, "")

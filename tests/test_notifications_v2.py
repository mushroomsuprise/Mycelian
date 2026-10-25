# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Notification settings, the engine's repeat/mute/progress/action behaviour, and connection notices."""

from __future__ import annotations

import pytest

from modules import notification_engine as ne
from modules import notify_settings as ns
from modules.service_alerts import ServiceAlertTracker, classify


@pytest.fixture
def engine(monkeypatch, tmp_path):
    prefs = ns.NotifyPrefs()
    monkeypatch.setattr(ne, "current_prefs", lambda: prefs)
    monkeypatch.setattr(ne, "_history_path", lambda: tmp_path / "history.json")
    monkeypatch.setattr(ne, "_history", [])
    monkeypatch.setattr(ne, "_history_loaded", True)
    monkeypatch.setattr(ne, "_repeat_index", {})
    monkeypatch.setattr(ne, "_flush_history_soon", lambda: None)
    monkeypatch.setattr(ne, "_trigger_history_refresh", lambda: None)
    shown = []
    monkeypatch.setattr(ne, "_deliver_toast", lambda payload, opts=None: shown.append(payload) or True)
    monkeypatch.setattr(ne, "dismiss_notification", lambda entry_id: shown.append({"dismissed": entry_id}))
    return prefs, shown


def test_prefs_normalize_and_filter():
    prefs = ns.NotifyPrefs(levels={"info": False, "bogus": False}, durations={"warning": "6", "error": -3},
                           position="nowhere", max_visible=99, muted={"twitch": ["warning", "nope"], "unknown": ["error"]})
    assert prefs.levels == {"error": True, "warning": True, "info": False, "success": True}
    assert prefs.durations["warning"] == 6 and prefs.durations["error"] == 0
    assert prefs.position == "top-right" and prefs.max_visible == 10
    assert prefs.muted == {"twitch": ["warning"]}
    assert not prefs.show_popup("info", "chatbot") and not prefs.show_popup("ongoing", "chatbot")
    assert not prefs.show_popup("warning", "twitch") and prefs.show_popup("error", "twitch")
    assert prefs.duration_ms("error") == 0 and prefs.duration_ms("warning") == 6000 and prefs.duration_ms("ongoing") == 0
    assert ns.level_of("negative") == "error" and ns.level_of("positive") == "success" and ns.level_of("whatever") == "info"
    assert ns.normalize_source("nope") == "app" and ns.source_label("psn") == "PlayStation"


def test_repeat_updates_one_entry_and_keeps_actions(engine):
    _prefs, shown = engine
    first = ne.notify("Paused until it reconnects", type="error", source="twitch", title="Twitch disconnected",
                      actions=ne.nav_actions_settings("Twitch"), details="401")
    again = ne.notify("Paused until it reconnects", type="error", source="twitch", title="Twitch disconnected")
    assert first == again
    history = ne.get_history()
    assert len(history) == 1 and history[0]["count"] == 2
    assert history[0]["actions"][0]["settings_subtab"] == "Twitch" and history[0]["details"] == "401"
    assert shown[-1]["count"] == 2 and [a["label"] for a in shown[-1]["actions"]] == ["Open Twitch settings", "Copy details"]
    assert shown[-1]["timeout"] == 0  # errors stay until dismissed by default


def test_muted_is_recorded_but_not_shown(engine):
    prefs, shown = engine
    prefs.muted = {"chatbot": ["success"]}
    eid = ne.notify("Saved", type="success", source="chatbot")
    assert eid and ne.get_history()[-1]["muted"] is True and shown == []
    prefs.record_muted = False
    assert ne.notify("Saved again", type="success", source="chatbot") is None
    prefs.popups = False
    assert ne.notify("Boom", type="error", source="app") and shown == []  # master switch: centre only


def test_progress_turns_into_the_result_in_place(engine):
    _prefs, shown = engine
    notice = ne.notify_progress("Copying templates", source="files", message="1 of 4", progress=25)
    notice.update("3 of 4", progress=75)
    notice.done("Templates copied", "4 files")
    history = ne.get_history()
    assert len(history) == 1 and history[0]["level"] == "success" and history[0]["title"] == "Templates copied"
    assert [p.get("level") for p in shown] == ["ongoing", "ongoing", "success"]
    assert len({p["id"] for p in shown}) == 1 and shown[1]["progress"] == 75
    with ne.notify_progress("Checking", source="app") as work:
        work.update("halfway")
    assert ne._find_entry(work.id) is None  # closed without a result: removed


def test_copy_and_call_actions(engine, monkeypatch):
    copied, called = [], []
    monkeypatch.setattr(ne, "copy_text_to_clipboard", lambda text: copied.append(text) or True)
    eid = ne.notify("Couldn't post", type="error", source="discord", title="Discord failed", details="Missing Access (50001)",
                    actions=[{"kind": "call", "label": "Try again", "fn": lambda: called.append(1)}])
    ne.run_entry_action(eid, 0)
    ne.run_entry_action(eid, 1)
    assert called == [1] and copied == ["Discord failed - Couldn't post\n\nMissing Access (50001)"]
    assert "fn" not in ne._find_entry(eid)["actions"][0]


def test_classify_and_service_tracker():
    assert classify("twitch", "Connected") == "ok" and classify("discord", "Reconnecting") == "busy"
    assert classify("obs", "Disconnected") == "problem" and classify("psn", "Token expired") == "off"
    assert classify("webengine", "Overloaded") == "busy" and classify("webengine", "Crashed") == "problem"
    tracker = ServiceAlertTracker()
    seq = [{"internet": "Online", "twitch": "Connected", "discord": "Connected"}] * 2
    seq += [{"internet": "Online", "twitch": "Connected", "discord": "Reconnecting"}] * 5  # busy: quiet
    notices = [n for sample in seq for n in tracker.observe(sample)]
    assert notices == []
    offline = [n for _ in range(4) for n in tracker.observe({"internet": "Offline", "twitch": "Disconnected", "discord": "Disconnected"})]
    assert [n.kind for n in offline] == ["offline"]
    back = [n for _ in range(4) for n in tracker.observe({"internet": "Online", "twitch": "Connected", "discord": "Connected"})]
    assert [n.kind for n in back] == ["online"]
    problems = [n for _ in range(4) for n in tracker.observe({"internet": "Online", "twitch": "Auth failed", "discord": "Connected"})]
    assert problems == []  # still inside the post-outage grace period
    for _ in range(20):
        problems += tracker.observe({"internet": "Online", "twitch": "Auth failed", "discord": "Connected"})
    assert [n.title for n in problems] == ["Twitch needs you to sign in again"] and problems[0].settings_tab == "Twitch"
    recovered = [n for _ in range(4) for n in tracker.observe({"internet": "Online", "twitch": "Connected", "discord": "Connected"})]
    assert [n.kind for n in recovered] == ["recovered"]


def test_tracker_leaves_obs_password_and_twitch_network_drops_alone():
    assert classify("obs", "Auth Failed") == "off"  # obs_service shows its own password notice
    tracker = ServiceAlertTracker()
    tracker.observe({"internet": "Online", "twitch": "Connected"})
    notices = [n for _ in range(4) for n in tracker.observe({"internet": "Online", "twitch": "Authenticated but Disconnected"})]
    assert [n.title for n in notices] == ["Twitch disconnected"]

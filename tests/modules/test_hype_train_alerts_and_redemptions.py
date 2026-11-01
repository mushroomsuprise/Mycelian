# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Hype Train level alerts (Alerts → Hype Train) and completing channel point redemptions
after their alert plays: matching, queueing from the live handlers, replay, the settings tab
helpers and the alert processor hook."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
import threading  # noqa: E402
from collections import deque  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import alert_playback_session as aps  # noqa: E402
from modules import alert_processor as ap  # noqa: E402
from modules import alertutils  # noqa: E402
from modules import twitch as tw  # noqa: E402
from modules.alertutils import AlertObj, AlertSettings  # noqa: E402
from modules.uiwindows import alertsettings as al  # noqa: E402

RECORD = AlertSettings.HYPE_RECORD_ALERT_ID


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- matching


@pytest.fixture
def hype_alerts(monkeypatch):
    store = {}
    monkeypatch.setattr(alertutils.alert_state_manager, "initialize", lambda: None)
    monkeypatch.setattr(alertutils.alert_state_manager, "get_alerts_by_type",
                        lambda t, include_ranges=True: dict(store) if t == "hypetrain" else {})
    return store


def test_fetch_hype_train_alert_order(hype_alerts):
    assert alertutils.fetch_hype_train_alert(1) is None
    hype_alerts[RECORD] = {"alert_name": "record"}
    assert alertutils.fetch_hype_train_alert(1) is None  # the record never stands in for a level
    alert = alertutils.fetch_hype_train_alert(4, record=True)
    assert alert.alert_name == "record" and alert.hype_train_record is True
    hype_alerts["hypetrain7"] = {"alert_name": "any"}
    assert alertutils.fetch_hype_train_alert(2).alert_name == "any"  # first level alert
    hype_alerts["hypetrain1"] = {"alert_name": "default"}
    assert alertutils.fetch_hype_train_alert(2).alert_name == "default"
    hype_alerts["hypetrain2-4"] = {"alert_name": "range"}
    assert alertutils.fetch_hype_train_alert(3).alert_name == "range"
    assert alertutils.fetch_hype_train_alert(7).alert_name == "any"  # exact beats range
    del hype_alerts[RECORD]
    assert alertutils.fetch_hype_train_alert(3, record=True).alert_name == "range"  # no record alert set up


def test_hype_train_alert_type_is_registered():
    mgr = alertutils.alert_state_manager
    assert mgr._alert_paths["hypetrain"] == "Alerts/HypeTrainAlerts"
    assert mgr._alert_paths["hypetrain_ranges"] == "Alerts/HypeTrainRangeAlerts"
    assert mgr._get_state_and_firebase_keys("hypetrain", "hypetrain2-4")[0] == "hypetrain_ranges"
    assert mgr._get_state_and_firebase_keys("hypetrain", "hypetrain3")[0] == "hypetrain"
    assert mgr._parse_alert_id("hypetrain3") == ("hypetrain", "3")
    assert mgr.get_display_name("hypetrain", "hypetrain3") == "Level 3"
    assert mgr.get_display_name("hypetrain", "hypetrain2-4") == "Levels 2-4"
    assert mgr.get_display_name("hypetrain", RECORD) == "New all-time high"
    assert alertutils.ui_tab_to_runtime_alert_type("hypetrain") == "hype_train_level"
    assert "hype_train_level" in AlertSettings.alert_types
    obj = alertutils._alert_obj_from_db({"hype_train_record": True, "hype_train_type": "treasure",
                                         "hype_train_total": 900, "redemption_id": "r", "complete_redemption": False})
    assert (obj.hype_train_record, obj.hype_train_type, obj.hype_train_total) == (True, "treasure", 900)
    assert (obj.redemption_id, obj.complete_redemption) == ("r", False)
    assert AlertObj().complete_redemption is True


def test_replay_of_a_level_alert_is_queued_with_the_record_flag(hype_alerts):
    hype_alerts["hypetrain3"] = {"alert_name": "three", "duration": 4}
    hype_alerts[RECORD] = {"alert_name": "record"}
    assert "hype_train_level" not in alertutils._REPLAY_INSTANT_ONLY_TYPES
    stored = {"alert_type": "hype_train_level", "hype_train_level": 3, "hype_train_record": False}
    assert alertutils.fetch_alert_config_for_stored(stored).alert_name == "three"
    replay = alertutils.build_replay_alert(dict(stored, hype_train_record=True, hype_train_type="treasure"))
    assert replay.alert_name == "record" and replay.hype_train_record and replay.hype_train_type == "treasure"
    # an unreadable level replays as level 1, which falls back to any level alert
    assert alertutils.fetch_alert_config_for_stored({"alert_type": "hype_train_level", "hype_train_level": "x"}).alert_name == "three"


# --------------------------------------------------------------------------- live handlers


@pytest.fixture
def pipe(monkeypatch):
    rec = SimpleNamespace(queued=[], stored=[])
    rec.engine = MagicMock()
    monkeypatch.setattr(tw, "web_engine", SimpleNamespace(web_engine_instance=rec.engine))
    monkeypatch.setattr(tw, "_twitch_alerts_enabled", lambda: True)
    monkeypatch.setattr(alertutils.alert_state_manager, "store_completed_alert",
                        lambda aid, payload: rec.stored.append((aid, payload)))
    monkeypatch.setattr(ap, "enqueue_alert", lambda a: rec.queued.append(a))
    monkeypatch.setattr(tw, "_add_twitch_alert_to_feed", lambda *a, **k: None)
    monkeypatch.setattr(tw, "_notify_chatbot_event", lambda *a, **k: None)
    monkeypatch.setattr(tw.statistics_manager, "get_statistics_manager", lambda: MagicMock())
    rec.wanted = []

    def fetch(level, record=False):
        rec.wanted.append((level, record))
        return AlertObj(alert_name=f"{'record' if record else 'level'}{level}")

    monkeypatch.setattr(alertutils, "fetch_hype_train_alert", fetch)
    return rec


@pytest.fixture
def api():
    a = tw.Twitch_API()
    a.user = SimpleNamespace(id="42")
    a.user_id = "42"
    return a


def _hype(level, *, train="t1", record=3, total=100, **kw):
    return SimpleNamespace(event=SimpleNamespace(id=train, level=level, all_time_high_level=record, total=total,
                                                 progress=10, goal=100, type="regular", conductor_user_name="Ann",
                                                 **kw))


def test_levels_queue_once_each_and_the_record_plays_once(api, pipe):
    run(api.on_hype_train_start(_hype(1)))
    assert [a.alert_name for a in pipe.queued] == ["level1"]
    a = pipe.queued[0]
    assert (a.alert_type, a.hype_train_level, a.hype_train_record, a.hype_train_total) == ("hype_train_level", 1, False, 100)
    run(api.on_hype_train_progress(_hype(1)))  # every contribution: same level, nothing new
    assert len(pipe.queued) == 1
    run(api.on_hype_train_progress(_hype(5, record=5)))  # jumped 1 -> 5; record read at begin (3)
    assert [a.alert_name for a in pipe.queued] == ["level1", "level2", "level3", "record4", "level5"]
    assert [a.hype_train_record for a in pipe.queued] == [False, False, False, True, False]
    assert len({a.alert_id for a in pipe.queued}) == 5
    run(api.on_hype_train_end(SimpleNamespace(event=SimpleNamespace(id="t1", level=5, total=500, top_contributions=[],
                                                                   type="regular"))))
    run(api.on_hype_train_start(_hype(1)))  # the same id after the end starts over
    assert pipe.queued[-1].alert_name == "level1"


def test_joining_mid_train_and_trains_without_a_record(api, pipe):
    # Joined at level 3: levels 1-2 were before Mycelian started. Twitch's record (2) is below
    # this level, so level 3 is a new all-time high.
    run(api.on_hype_train_progress(_hype(3, train="t2", record=2)))
    assert [a.alert_name for a in pipe.queued] == ["record3"]
    # Joined where Twitch already counts this train in the record: no record alert.
    run(api.on_hype_train_progress(_hype(4, train="t4", record=4)))
    assert pipe.queued[-1].alert_name == "level4" and pipe.queued[-1].hype_train_record is False
    pipe.wanted.clear()
    run(api.on_hype_train_start(_hype(1, train="t3", record=0)))  # no earlier train: nothing to beat
    run(api.on_hype_train_progress(_hype(2, train="t3", record=0)))
    assert all(not r for _l, r in pipe.wanted)


def test_bad_or_missing_levels_and_no_alert_configured(api, pipe, monkeypatch):
    api._queue_hype_train_level_alerts(SimpleNamespace(level="x"))
    api._queue_hype_train_level_alerts(SimpleNamespace(level=0))
    api._queue_hype_train_level_alerts(SimpleNamespace(level=1, all_time_high_level="?", total="?"), begin=True)
    assert pipe.queued[-1].hype_train_total == 0 and pipe.queued[-1].hype_train_level == 1
    count = len(pipe.queued)
    monkeypatch.setattr(alertutils, "fetch_hype_train_alert", lambda level, record=False: None)
    api._queue_hype_train_level_alerts(SimpleNamespace(level=4, id="t9"), begin=True)
    assert len(pipe.queued) == count
    monkeypatch.setattr(api, "_queue_hype_train_level_alerts", MagicMock(side_effect=RuntimeError("x")))
    run(api.on_hype_train_start(_hype(1)))  # logged, the rest of the handler still runs
    run(api.on_hype_train_progress(_hype(2)))
    pipe.engine.hype_train_status_update.assert_called()


def _points(status="unfulfilled", rid="red-1"):
    reward = SimpleNamespace(title="Hydrate", id="rw1", cost=100)
    return SimpleNamespace(event=SimpleNamespace(user_name="Pat", user_login="pat", user_id="6", user_input="",
                                                 reward=reward, id=rid, status=SimpleNamespace(value=status)))


def test_point_alert_keeps_the_redemption(api, pipe, monkeypatch):
    monkeypatch.setattr(alertutils, "fetch_point_alert", lambda rid: AlertObj(enable_alert=True))
    monkeypatch.setattr(tw, "match_point_reward_dedicated_template", lambda name: None)
    run(api.on_points(_points()))
    alert = pipe.queued[0]
    assert (alert.redemption_id, alert.redemption_status) == ("red-1", "unfulfilled")
    run(api.on_points(SimpleNamespace(event=SimpleNamespace(user_name="Pat", user_input="", reward=_points().event.reward))))
    assert (pipe.queued[1].redemption_id, pipe.queued[1].redemption_status) == ("", "")


# --------------------------------------------------------------------------- completing redemptions


@pytest.fixture
def helix(monkeypatch):
    rec = SimpleNamespace(calls=[], error=None, notes=[])
    api = SimpleNamespace(is_connected=True, user_id="42")

    async def call(url, method, json_data=None):
        rec.calls.append((url, method, json_data))
        if rec.error:
            raise rec.error
        return {"data": []}

    api.generic_api_call = call
    monkeypatch.setattr(tw, "twitch_api", api)
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda message, **kw: rec.notes.append(dict(kw, message=message)))
    rec.api = api
    return rec


def test_complete_point_redemption(helix, monkeypatch):
    assert tw.complete_point_redemption("rw1", "red-1") == "ok"
    url, method, body = helix.calls[0]
    assert method == "PATCH" and body == {"status": "FULFILLED"}
    assert "broadcaster_id=42" in url and "reward_id=rw1" in url and "id=red-1" in url
    assert tw.complete_point_redemption("", "red-1") == "error"
    helix.error = tw.TwitchPermissionError("403 not owned")
    assert tw.complete_point_redemption("rw1", "red-2") == "not_manageable"
    assert helix.notes[-1]["dedupe_key"] == "twitch:redemption_not_manageable:rw1"
    helix.error = RuntimeError("404 not unfulfilled")
    assert tw.complete_point_redemption("rw1", "red-3") == "error"
    assert helix.notes[-1]["dedupe_key"] == "twitch:redemption_complete_failed"
    helix.api.is_connected = False
    assert tw.complete_point_redemption("rw1", "red-4") == "not_connected"
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", MagicMock(side_effect=RuntimeError("ui gone")))
    helix.api.is_connected = True
    assert tw.complete_point_redemption("rw1", "red-5") == "error"  # a failing notice never escapes


class _Engine:
    """Minimal web_engine stand-in for process_alert (see test_alert_processor.FakeEngine)."""

    def __init__(self, reason):
        self.reason = reason
        self.ALERT_PLAYING = False
        self.ALERTS_PAUSED = False
        self.web_engine_instance = self
        self.seq = 0

    def broadcast_alert_queue_state(self):
        pass

    def assign_next_alert_queue_seq(self):
        self.seq += 1
        return self.seq

    def begin_alert_playback_session(self, seq, holders, duration, delay):
        aps.begin_session(seq, holders, duration, delay)

    def set_alert_playing(self, value):
        self.ALERT_PLAYING = value

    def abort_alert_playback(self, reason):
        aps.abort_session(reason)

    def emit_alert_skip(self, *a):
        pass

    def _apply_playback_session_finished(self, flag):
        self.ALERT_PLAYING = False

    def next_alert(self, data):
        aps.get_session().force_finish(self.reason)
        return True


@pytest.fixture
def processor(monkeypatch):
    done = []
    finished = threading.Event()

    def complete(reward_id, redemption_id):
        done.append((reward_id, redemption_id))
        finished.set()
        return "ok"

    monkeypatch.setattr(tw, "complete_point_redemption", complete)
    monkeypatch.setattr(ap, "web_engine_instance", None)
    monkeypatch.setattr(ap, "ALERT_QUEUE", deque())
    monkeypatch.setattr(ap, "_current_alert", None)
    monkeypatch.setattr(ap, "is_shutdown_in_progress", lambda: False)
    monkeypatch.setattr(ap, "notify", lambda *a, **k: None)
    monkeypatch.setattr(aps, "_current_session", None)
    import modules.template_config_parser as tcp

    monkeypatch.setattr(tcp, "delay_between_alerts_seconds", lambda: 0.0)

    def play(alert, reason="all_holders_done", expect=True):
        monkeypatch.setattr(ap, "web_engine", _Engine(reason))
        finished.clear()
        ap.process_alert(alert)
        finished.wait(2 if expect else 0.05)
        return list(done)

    return play


def _point_alert(**kw):
    a = AlertObj(alert_id="P1", alert_type="point", twitch_reward_id="rw1", redemption_id="red-1",
                 redemption_status="unfulfilled", enable_alert=True, gif_dir="g", gif_name="x.gif")
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_redemption_completes_after_a_real_play(processor):
    assert processor(_point_alert()) == [("rw1", "red-1")]


def test_no_ack_fallback_counts_as_played(processor):
    assert processor(_point_alert(), reason="no_ack_fallback") == [("rw1", "red-1")]


@pytest.mark.parametrize("change,reason", [
    ({}, "skip"),
    ({}, "superseded"),
    ({"is_test": True}, "all_holders_done"),
    ({"is_replay": True}, "all_holders_done"),
    ({"complete_redemption": False}, "all_holders_done"),
    ({"redemption_status": "fulfilled"}, "all_holders_done"),
    ({"redemption_id": ""}, "all_holders_done"),
    ({"twitch_reward_id": ""}, "all_holders_done"),
    ({"alert_type": "follow"}, "all_holders_done"),
])
def test_redemption_left_alone(processor, change, reason):
    assert processor(_point_alert(**change), reason=reason, expect=False) == []


def test_finish_reason_unknown_without_a_matching_session(monkeypatch):
    monkeypatch.setattr(ap, "_current_alert", None)
    assert ap._finish_reason_of_current() == ""
    monkeypatch.setattr(ap, "_current_alert", {"queue_seq": 5})
    monkeypatch.setattr(aps, "_current_session", None)
    assert ap._finish_reason_of_current() == ""
    ap._after_alert_finished(SimpleNamespace(alert_type=object()))  # odd data: ignored


# --------------------------------------------------------------------------- settings tab helpers


def test_settings_tab_registers_hype_train():
    assert al.ALERT_TAB_TO_TYPE["Hype Train"] == "hypetrain"
    assert al._ALERT_TAB_ICONS["Hype Train"] == "train"
    assert al._TRIGGER_UNITS["hypetrain"] == "level"
    assert "hypetrain" in al._QUANTITY_ALERT_TYPES
    assert al._special_alert_id("hypetrain") == RECORD
    assert al._special_alert_id("subs") == AlertSettings.FALLBACK_ALERT_ID
    assert al._special_alert_id("bits") is None
    items = [("hypetrain3", {}), (RECORD, {}), ("hypetrain1", {})]
    assert [i for i, _ in al.sort_alert_ids("hypetrain", items)] == ["hypetrain1", "hypetrain3", RECORD]


def _els(**values):
    return {k: SimpleNamespace(value=v) for k, v in values.items()}


def test_test_alert_data_for_hype_train_levels_and_the_record():
    data = al._collect_test_alert_data("hypetrain", _els(amount_input=3, alert_select="hypetrain3"))
    assert data["alert_type"] == "hype_train_level"
    assert (data["hype_train_level"], data["hype_train_record"]) == (3, False)
    data = al._collect_test_alert_data("hypetrain", _els(range_toggle=True, min_input=2, alert_select="hypetrain2-4"))
    assert data["hype_train_level"] == 2
    data = al._collect_test_alert_data("hypetrain", _els(amount_input=0, alert_select=RECORD))
    assert (data["hype_train_level"], data["hype_train_record"]) == (5, True)
    data = al._collect_test_alert_data("hypetrain", _els())
    assert data["hype_train_level"] == 1

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Alert pipeline: unique ids, skip from every entry point, replay, queue state."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, request  # noqa: E402

from modules import alert_processor, alertutils, web_engine  # noqa: E402
from modules.alert_playback_session import get_session, reset_session_for_tests  # noqa: E402
from modules.alertutils import AlertObj  # noqa: E402
from modules.web_engine import WebEngine  # noqa: E402


class _FakeSocketIO:
    def __init__(self) -> None:
        self.handlers = {}
        self.emitted = []

    def on(self, name, namespace=None):
        def deco(fn):
            self.handlers[name] = fn
            return fn

        return deco

    def emit(self, event, data=None, to=None, **_kw):
        self.emitted.append((event, data))

    def sleep(self, _s):
        pass

    def start_background_task(self, *a, **k):
        pass


class _FakeEngine:
    """Stand-in for web_engine_instance: records every safe_emit."""

    def __init__(self) -> None:
        self.emitted = []
        self.socketio = object()

    def safe_emit(self, event, data=None, to=None):
        self.emitted.append((event, data))
        return True

    def events(self, name):
        return [d for e, d in self.emitted if e == name]


def _alert(alert_id, alert_type="sub", username="viewer", **fields) -> AlertObj:
    obj = AlertObj()
    obj.alert_id = alert_id
    obj.alert_type = alert_type
    obj.username = username
    for key, value in fields.items():
        setattr(obj, key, value)
    return obj


class _PipelineCase(unittest.TestCase):
    """Isolates the module-level queue / session / overlay state."""

    def setUp(self) -> None:
        reset_session_for_tests()
        with alert_processor._ALERT_QUEUE_LOCK:
            alert_processor.ALERT_QUEUE.clear()
        alert_processor._set_current_alert(None)
        self.engine = _FakeEngine()
        patches = [
            patch.object(web_engine, "web_engine_instance", self.engine),
            patch.object(web_engine, "ALERT_PLAYING", False),
            patch.object(web_engine, "ALERTS_PAUSED", False),
            patch.object(web_engine, "ALERTS_MUTED", False),
            patch.object(web_engine, "_last_skip_at", 0.0),
            patch.object(web_engine, "_queue_state_last_sig", None),
            patch.object(web_engine, "_sync_pause_button_state", lambda: None),
            patch.object(web_engine, "_sync_mute_button_state", lambda: None),
            # web_engine imports flask lazily (server start); socket handlers read it.
            patch.object(web_engine, "request", request),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(reset_session_for_tests)
        self.addCleanup(alert_processor._set_current_alert, None)

        def _clear_queue():
            with alert_processor._ALERT_QUEUE_LOCK:
                alert_processor.ALERT_QUEUE.clear()

        self.addCleanup(_clear_queue)

    def start_playing(self, alert_id="AlertPlaying", seq=41, **fields):
        """What process_alert does up to the next_alert emit."""
        alert = _alert(alert_id, **fields)
        web_engine.begin_alert_playback_session(seq, ["alerts"], 5.0, 0.5)
        alert_processor._set_current_alert(
            {
                "alert_id": alert_id,
                "type": alert.alert_type,
                "username": alert.username,
                "started_at_ms": 1_000,
                "duration_ms": 5_000,
                "queue_seq": seq,
                "is_replay": False,
            }
        )
        web_engine.set_alert_playing(True)
        return get_session()

    def skip_emits(self):
        return self.engine.events("alerts_skip_alert")


class UniqueAlertIdTests(unittest.TestCase):
    def test_ids_unique_within_one_second(self) -> None:
        with patch("modules.alertutils.time.time", return_value=1_761_400_000.5):
            ids = [alertutils.new_alert_id() for _ in range(2500)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith("Alert") for i in ids))
        self.assertNotIn("-", "".join(ids))

    def test_prefix_and_increasing(self) -> None:
        a = alertutils.new_alert_id("Replay")
        b = alertutils.new_alert_id("Replay")
        self.assertTrue(a.startswith("Replay"))
        self.assertLess(int(a[len("Replay"):]), int(b[len("Replay"):]))


class SkipEntryPointTests(_PipelineCase):
    """Every entry point aborts the playing alert exactly once."""

    def _socket_engine(self) -> WebEngine:
        eng = object.__new__(WebEngine)
        eng.socketio = _FakeSocketIO()
        eng._server_thread_ident = None
        eng._preview_iframe_has_sid = lambda sid: False
        eng.register_socket_events()
        return eng

    def _call_socket(self, eng, event, data, remote="127.0.0.1"):
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": remote}):
            request.sid = "sid-test"
            return eng.socketio.handlers[event](data)

    def assert_skipped_once(self, session, result=None) -> None:
        self.assertTrue(session.finished)
        self.assertEqual(session.finish_reason, "skip")
        self.assertFalse(web_engine.ALERT_PLAYING)
        emits = self.skip_emits()
        self.assertEqual(len(emits), 1, emits)
        self.assertEqual(emits[0]["queue_seq"], session.seq)
        self.assertEqual(emits[0]["alert_id"], "AlertPlaying")
        if result is not None:
            self.assertTrue(result.get("ok"))

    def _double_press(self, fn) -> None:
        session = self.start_playing()
        fn()
        self.assert_skipped_once(session)
        # Same press again: the alert is already gone, nothing else is aborted.
        fn()
        self.assertEqual(len(self.skip_emits()), 1)

    def test_app_tab_skip(self) -> None:
        from modules.uiwindows import activity_feed

        with patch.object(activity_feed, "notify"):
            self._double_press(lambda: activity_feed.skip_alert({"type": "current"}))

    def test_app_tab_row_skip_by_id(self) -> None:
        from modules.uiwindows import activity_feed

        with patch.object(activity_feed, "notify"):
            self._double_press(
                lambda: activity_feed.skip_alert({"type": "sub", "alert_id": "AlertPlaying"})
            )

    def test_dock_toolbar_and_row_skip(self) -> None:
        eng = self._socket_engine()
        session = self.start_playing()
        result = self._call_socket(eng, "activity_feed_skip_alert", {"alert_id": "AlertPlaying"})
        self.assert_skipped_once(session, result)
        self.assertTrue(result["success"])
        again = self._call_socket(eng, "activity_feed_skip_alert", {})
        self.assertFalse(again["ok"])
        self.assertEqual(len(self.skip_emits()), 1)

    def test_spore_skip_control_socket(self) -> None:
        eng = self._socket_engine()
        self._double_press(lambda: self._call_socket(eng, "skip_alert", {}))

    def test_skip_socket_rejects_lan_clients(self) -> None:
        eng = self._socket_engine()
        session = self.start_playing()
        result = self._call_socket(eng, "activity_feed_skip_alert", {}, remote="192.168.1.50")
        self.assertFalse(result["ok"])
        self.assertFalse(session.finished)
        self.assertEqual(self.skip_emits(), [])

    def test_streamdeck_socket_action(self) -> None:
        eng = self._socket_engine()
        self._double_press(
            lambda: self._call_socket(
                eng,
                "streamdeck_template_action",
                {"templateName": "alerts", "actionName": "skip_alert"},
            )
        )

    def test_streamdeck_http_dispatch(self) -> None:
        eng = object.__new__(WebEngine)
        eng._load_streamdeck_template_config = lambda name: {}
        eng._emit_streamdeck_template_events = MagicMock()
        self._double_press(
            lambda: eng._streamdeck_http_dispatch_emits("alerts", "skip_alert", "", {})
        )
        eng._emit_streamdeck_template_events.assert_not_called()

    def test_streamdeck_options_event(self) -> None:
        eng = object.__new__(WebEngine)
        self._double_press(
            lambda: eng._execute_streamdeck_action(
                "alerts", "skip", {"event": "alerts_skip_alert"}, {}
            )
        )

    def test_connector_template_control(self) -> None:
        from modules.connector_actions import TemplateControlAction

        from modules.connector_core import ActionType

        action = TemplateControlAction(
            action_id="a1", action_type=ActionType.TEMPLATE_CONTROL, name="Skip",
            template_name="alerts", control_action="skip_alert",
        )
        self._double_press(
            lambda: asyncio.run(action.execute({"trigger_id": "t"}, {"event_type": "x"}))
        )

    def test_connector_websocket_emit(self) -> None:
        from modules.connector_actions import WebSocketEmitAction

        from modules.connector_core import ActionType

        action = WebSocketEmitAction(
            action_id="a2", action_type=ActionType.WEBSOCKET_EMIT, name="Skip",
            event_name="alerts_skip_alert", event_data={},
        )
        self._double_press(lambda: asyncio.run(action.execute({"trigger_id": "t"}, {})))
        self.assertEqual(self.engine.events("alerts_skip_alert").__len__(), 1)

    def test_other_template_actions_are_not_skips(self) -> None:
        self.assertFalse(web_engine.is_alert_skip_template_action("alerts", "play_alert"))
        self.assertFalse(web_engine.is_alert_skip_template_action("subbar", "reset"))
        self.assertTrue(web_engine.is_alert_skip_template_action("Alerts", "skip"))
        self.assertTrue(web_engine.is_alert_skip_template_action("poll", "skip_alert"))


class SkipTargetingTests(_PipelineCase):
    def test_stale_id_does_not_abort_next_alert(self) -> None:
        session = self.start_playing("AlertNext", seq=7)
        result = web_engine.skip_current_alert("AlertPrevious", source="test")
        self.assertEqual(result, {"ok": False, "skipped_alert_id": None, "reason": "not_playing"})
        self.assertFalse(session.finished)
        self.assertEqual(self.skip_emits(), [])

    def test_stale_queue_seq_does_not_abort_next_alert(self) -> None:
        session = self.start_playing("AlertNext", seq=7)
        result = web_engine.skip_alert_from_payload({"queue_seq": 6}, source="test")
        self.assertFalse(result["ok"])
        self.assertFalse(session.finished)

    def test_matching_queue_seq_skips(self) -> None:
        session = self.start_playing("AlertNext", seq=7)
        result = web_engine.skip_alert_from_payload({"queue_seq": 7}, source="test")
        self.assertTrue(result["ok"])
        self.assertTrue(session.finished)

    def test_idless_double_press_after_next_alert_started_is_ignored(self) -> None:
        self.start_playing("AlertOne", seq=1)
        self.assertTrue(web_engine.skip_current_alert(None)["ok"])
        nxt = self.start_playing("AlertTwo", seq=2)
        result = web_engine.skip_current_alert(None)
        self.assertEqual(result["reason"], "double_press")
        self.assertFalse(nxt.finished)
        # An explicit id is never treated as a double press.
        self.assertTrue(web_engine.skip_current_alert("AlertTwo")["ok"])

    def test_queued_id_skip_removes_it(self) -> None:
        session = self.start_playing("AlertNow", seq=3)
        alert_processor.enqueue_alert(_alert("AlertQueued"))
        alert_processor.enqueue_alert(_alert("AlertLater"))
        result = web_engine.skip_current_alert("AlertQueued", source="dock")
        self.assertEqual(result["reason"], "removed_from_queue")
        self.assertEqual(result["skipped_alert_id"], "AlertQueued")
        ids = [a.alert_id for a in alert_processor.snapshot_alert_queue()]
        self.assertEqual(ids, ["AlertLater"])
        self.assertFalse(session.finished)
        self.assertEqual(self.skip_emits(), [])

    def test_nothing_playing(self) -> None:
        self.assertEqual(web_engine.skip_current_alert()["reason"], "not_playing")

    def test_skip_before_emit_is_resent_to_overlays(self) -> None:
        """A skip landing between begin_session and next_alert still reaches overlays."""

        class _Eng(_FakeEngine):
            def next_alert(eng_self, data):
                eng_self.emitted.append(("next_alert", data))
                return True

        eng = _Eng()
        real_set = alert_processor._set_current_alert

        def set_then_press_skip(info):
            real_set(info)
            if info:
                web_engine.skip_current_alert(None, source="race")

        with patch.object(web_engine, "web_engine_instance", eng), patch.object(
            alert_processor, "_set_current_alert", side_effect=set_then_press_skip
        ):
            alert_processor.process_alert(_alert("AlertRace"))
        skips = [d for e, d in eng.emitted if e == "alerts_skip_alert"]
        nexts = [d for e, d in eng.emitted if e == "next_alert"]
        self.assertEqual(len(nexts), 1)
        self.assertEqual(len(skips), 2)  # at the press, and again after the late emit
        self.assertTrue(all(s["queue_seq"] == nexts[0]["queue_seq"] for s in skips))
        self.assertFalse(web_engine.ALERT_PLAYING)
        self.assertIsNone(alert_processor.current_alert_info())


class ReplayTests(_PipelineCase):
    def setUp(self) -> None:
        super().setUp()
        self.stored = {}
        mgr = alertutils.alert_state_manager
        for name, fn in (
            ("initialize", lambda: None),
            ("get_stored_alert_by_id", lambda aid: dict(self.stored[aid]) if aid in self.stored else None),
            ("store_completed_alert", MagicMock(name="store")),
        ):
            p = patch.object(mgr, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.store_mock = mgr.store_completed_alert
        p = patch.object(alert_processor, "wake_alert_queue", lambda: None)
        p.start()
        self.addCleanup(p.stop)

    def queued(self):
        return alert_processor.snapshot_alert_queue()

    def test_bits_replay_keeps_content_and_refetches_media(self) -> None:
        self.stored["Alert1"] = {
            "alert_id": "Alert1",
            "alert_type": "bit",
            "username": "CheerUser",
            "amt_cheered": 500,
            "message": "pog Cheer500",
            "fragments": [{"type": "text", "text": "pog "}],
            "gif_dir": "/old",
            "gif_name": "old.gif",
            "alert_name": "Old bits",
            "duration": 3.0,
        }
        config = _alert("", "bit", "Placeholder", gif_dir="/new", gif_name="new.gif",
                        single_audio_dir="/snd", single_audio_name="new.mp3",
                        duration=8.0, alert_name="Bits 500", tts_enabled=True)
        with patch.object(alertutils, "fetch_bits_alert", return_value=config) as fetch:
            result = alertutils.replay_alert("Alert1")
        fetch.assert_called_once_with(500)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "queued")
        (replay,) = self.queued()
        self.assertEqual(replay.alert_id, result["new_alert_id"])
        self.assertTrue(replay.alert_id.startswith("Replay"))
        self.assertTrue(replay.is_replay)
        self.assertFalse(replay.is_test)
        self.assertEqual(replay.username, "CheerUser")
        self.assertEqual(replay.message, "pog Cheer500")
        self.assertEqual(replay.amt_cheered, 500)
        self.assertEqual(replay.fragments, [{"type": "text", "text": "pog "}])
        self.assertEqual((replay.gif_dir, replay.gif_name), ("/new", "new.gif"))
        self.assertEqual(replay.single_audio_name, "new.mp3")
        self.assertEqual(replay.duration, 8.0)
        self.assertEqual(replay.alert_name, "Bits 500")
        self.assertTrue(replay.tts_enabled)
        # The fetched config object itself is never mutated.
        self.assertEqual(config.username, "Placeholder")

    def test_replay_emits_instant_mirror(self) -> None:
        self.stored["Alert2"] = {
            "alert_id": "Alert2",
            "alert_type": "giftsub",
            "username": "Gifter",
            "gift_qty": 1,
            "tier": 2,
            "recipient": "Lucky",
        }
        with patch.object(alertutils, "fetch_giftsub_alert", return_value=_alert("", "giftsub")):
            result = alertutils.replay_alert("Alert2")
        mirrors = self.engine.events(alertutils.REPLAY_INSTANT_EVENT)
        self.assertEqual(len(mirrors), 1)
        m = mirrors[0]
        self.assertEqual(m["type"], "giftsub")
        self.assertEqual(m["username"], "Gifter")
        self.assertEqual(m["recipient"], "Lucky")
        self.assertEqual(m["tier"], 2)
        self.assertEqual(m["alert_id"], result["new_alert_id"])
        self.assertEqual(m["original_alert_id"], "Alert2")
        self.assertTrue(m["is_replay"])
        # Counting consumers listen to instant_alert: a replay never reaches them.
        self.assertEqual(self.engine.events("instant_alert"), [])

    def test_point_replay_keeps_reward_title_and_dedicated_hold(self) -> None:
        self.stored["Alert3"] = {
            "alert_id": "Alert3",
            "alert_type": "point",
            "username": "Redeemer",
            "alert_name": "Boo Game",
            "twitch_reward_id": "rw1",
            "message": "boo!",
            "point_cost": 300,
        }
        config = _alert("", "point", alert_name="Config title", enable_alert=True, duration=4.0)
        with patch.object(alertutils, "fetch_point_alert", return_value=config), patch(
            "modules.template_config_parser.match_point_reward_dedicated_template",
            return_value={"queued": True, "duration_seconds": 12.0},
        ):
            result = alertutils.replay_alert("Alert3")
        self.assertEqual(result["reason"], "queued")
        (replay,) = self.queued()
        self.assertEqual(replay.alert_name, "Boo Game")
        self.assertTrue(replay.hold_queue_only)
        self.assertEqual(replay.duration, 12.0)
        self.assertTrue(replay.enable_alert)
        self.assertEqual(replay.message, "boo!")
        mirror = self.engine.events(alertutils.REPLAY_INSTANT_EVENT)[0]
        self.assertEqual(mirror["type"], "points")
        self.assertEqual(mirror["alert_name"], "Boo Game")

    def test_unconfigured_point_with_queued_template_holds_queue(self) -> None:
        self.stored["Alert4"] = {"alert_id": "Alert4", "alert_type": "point",
                                 "username": "R", "alert_name": "Boo Game"}
        with patch.object(alertutils, "fetch_point_alert", return_value=None), patch(
            "modules.template_config_parser.match_point_reward_dedicated_template",
            return_value={"queued": True, "duration_seconds": 9.0},
        ):
            result = alertutils.replay_alert("Alert4")
        self.assertEqual(result["reason"], "queued")
        (hold,) = self.queued()
        self.assertTrue(hold.hold_queue_only)
        self.assertEqual(hold.duration, 9.0)
        self.assertEqual(hold.alert_name, "Boo Game")
        self.assertTrue(hold.is_replay)

    def test_errors(self) -> None:
        self.assertEqual(alertutils.replay_alert("")["reason"], "missing_id")
        self.assertEqual(alertutils.replay_alert("Nope")["reason"], "not_found")
        self.stored["Alert5"] = {"alert_id": "Alert5", "alert_type": "follow", "username": "F"}
        with patch.object(alertutils, "fetch_follow_alert", return_value=None):
            result = alertutils.replay_alert("Alert5")
        self.assertEqual(result, {"ok": False, "reason": "no_config", "new_alert_id": None})
        self.assertEqual(self.queued(), [])

    def test_hype_train_is_instant_only(self) -> None:
        self.stored["Alert6"] = {"alert_id": "Alert6", "alert_type": "hype_train_start",
                                 "username": "Conductor", "hype_train_level": 2}
        result = alertutils.replay_alert("Alert6")
        self.assertEqual(result["reason"], "instant_only")
        self.assertEqual(self.queued(), [])
        mirror = self.engine.events(alertutils.REPLAY_INSTANT_EVENT)[0]
        self.assertEqual(mirror["level"], 2)
        self.assertEqual(mirror["conductor_name"], "Conductor")

    def test_replay_never_touches_stats_counters_storage_feed_or_connectors(self) -> None:
        self.stored["Alert7"] = {"alert_id": "Alert7", "alert_type": "sub",
                                 "username": "S", "resub_month": 1, "tier": 1}
        stats = MagicMock(name="stats_manager")
        with patch.object(alertutils, "fetch_sub_alert", return_value=_alert("", "sub")), patch(
            "modules.statistics_manager.get_statistics_manager", return_value=stats
        ) as get_stats, patch(
            "modules.uiwindows.activity_feed.add_alert_to_feed"
        ) as feed, patch(
            "modules.connector_manager.get_manager"
        ) as connectors:
            result = alertutils.replay_alert("Alert7")
            (replay,) = self.queued()
            alert_processor.remove_queued_alert(replay)
            # Play it through the queue too (emit fails -> aborted, nothing else runs).
            engine = MagicMock()
            engine.next_alert.return_value = False
            with patch.object(web_engine, "web_engine_instance", engine), patch.object(
                alert_processor, "notify"
            ):
                alert_processor.process_alert(replay)
        self.assertTrue(result["ok"])
        get_stats.assert_not_called()
        stats.assert_not_called()
        feed.assert_not_called()
        connectors.assert_not_called()
        self.store_mock.assert_not_called()
        sent = engine.next_alert.call_args[0][0]
        self.assertTrue(sent["is_replay"])
        self.assertEqual(sent["username"], "S")

    def test_dock_replay_socket(self) -> None:
        self.stored["Alert8"] = {"alert_id": "Alert8", "alert_type": "raid",
                                 "username": "Raider", "raider_count": 42}
        eng = object.__new__(WebEngine)
        eng.socketio = _FakeSocketIO()
        eng._server_thread_ident = None
        eng.register_socket_events()
        app = Flask(__name__)
        with patch.object(alertutils, "fetch_raid_alert", return_value=_alert("", "raid")):
            with app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
                request.sid = "s"
                ok = eng.socketio.handlers["activity_feed_replay_alert"]({"alert_id": "Alert8"})
                missing = eng.socketio.handlers["activity_feed_replay_alert"]({"alert_id": "Gone"})
        self.assertTrue(ok["ok"] and ok["success"])
        self.assertTrue(ok["new_alert_id"].startswith("Replay"))
        self.assertEqual(missing["reason"], "not_found")
        self.assertFalse(missing["success"])
        (replay,) = self.queued()
        self.assertEqual(replay.raider_count, 42)


class QueueStateTests(_PipelineCase):
    def setUp(self) -> None:
        super().setUp()
        p = patch.object(alert_processor, "wake_alert_queue", lambda: None)
        p.start()
        self.addCleanup(p.stop)

    def states(self):
        return self.engine.events(web_engine.ALERT_QUEUE_STATE_EVENT)

    def test_payload_shape(self) -> None:
        self.start_playing("AlertNow", seq=9, username="Streamer")
        alert_processor.enqueue_alert(_alert("AlertA", "follow", "a"))
        alert_processor.enqueue_alert(_alert("AlertB", "raid", "b", stackable=True, is_replay=True))
        state = web_engine.get_alert_queue_state()
        self.assertEqual(
            set(state), {"paused", "muted", "playing", "queue", "server_now_ms", "revision"}
        )
        self.assertEqual(
            state["playing"],
            {"alert_id": "AlertNow", "type": "sub", "username": "Streamer",
             "started_at_ms": 1_000, "duration_ms": 5_000, "queue_seq": 9},
        )
        # Play order: stackable (replays) first.
        self.assertEqual(
            state["queue"],
            [
                {"alert_id": "AlertB", "type": "raid", "username": "b", "is_replay": True},
                {"alert_id": "AlertA", "type": "follow", "username": "a"},
            ],
        )
        self.assertIsInstance(state["server_now_ms"], int)
        self.assertFalse(state["paused"])

    def test_broadcast_on_each_change_only(self) -> None:
        alert_processor.enqueue_alert(_alert("AlertA"))
        self.assertEqual([a["alert_id"] for a in self.states()[-1]["queue"]], ["AlertA"])
        rev = self.states()[-1]["revision"]
        n = len(self.states())
        self.assertIsNone(web_engine.broadcast_alert_queue_state())  # unchanged -> silent
        self.assertEqual(len(self.states()), n)

        web_engine.set_alerts_paused(True)
        self.assertTrue(self.states()[-1]["paused"])
        self.assertGreater(self.states()[-1]["revision"], rev)

        with patch.object(web_engine, "ALERTS_MUTED", True):
            WebEngine.mute_status_update(MagicMock(safe_emit=self.engine.safe_emit))
        self.assertTrue(self.states()[-1]["muted"])

        session = self.start_playing("AlertNow", seq=4)
        self.assertEqual(self.states()[-1]["playing"]["alert_id"], "AlertNow")
        web_engine.skip_current_alert(None)
        self.assertTrue(session.finished)
        self.assertIsNone(self.states()[-1]["playing"])

        alert_processor.remove_queued_alert_by_id("AlertA")
        self.assertEqual(self.states()[-1]["queue"], [])

    def test_overlay_reported_duration(self) -> None:
        self.start_playing("AlertNow", seq=5)
        self.assertTrue(alert_processor.note_current_alert_duration(5, 7250))
        self.assertEqual(self.states()[-1]["playing"]["duration_ms"], 7250)
        self.assertFalse(alert_processor.note_current_alert_duration(4, 9000))  # stale seq
        self.assertFalse(alert_processor.note_current_alert_duration(5, 6000))  # shorter
        self.assertEqual(web_engine.get_alert_queue_state()["playing"]["duration_ms"], 7250)

    def test_get_alert_queue_state_socket(self) -> None:
        eng = object.__new__(WebEngine)
        eng.socketio = _FakeSocketIO()
        eng._server_thread_ident = None
        eng._preview_iframe_has_sid = lambda sid: False
        eng.register_socket_events()
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            request.sid = "s"
            state = eng.socketio.handlers["get_alert_queue_state"]({})
        self.assertIn("queue", state)
        self.assertEqual(eng.socketio.emitted[-1][0], "alert_queue_state")


class OverlayReplayGuardTests(unittest.TestCase):
    """Compiled Spore runtimes never count replays; new boilerplates replay visuals."""

    def test_runtime_and_boilerplates(self) -> None:
        bp = ROOT / "templates" / "_boilerplates"
        runtime = (bp / "spore_data_runtime.js").read_text(encoding="utf-8")
        adjust = runtime[runtime.index("function sporeCounterAdjust("):]
        self.assertIn("if (sporeIsReplay(payload)) { return; }", adjust[:400])
        self.assertEqual(runtime.count("if (sporeIsReplay(data)) { return; }"), 3)
        instant = (bp / "instant_alert.html").read_text(encoding="utf-8")
        self.assertIn("socket.on('instant_alert_replay'", instant)
        self.assertEqual(alertutils.REPLAY_INSTANT_EVENT, "instant_alert_replay")
        queue = (bp / "queue_alert.html").read_text(encoding="utf-8")
        self.assertIn("sporeHideShownNow();", queue)
        self.assertIn("alertQueueClient.started(seq, hold * 1000)", queue)
        aq = (ROOT / "assets" / "default_assets" / "alert_queue.js").read_text(encoding="utf-8")
        self.assertIn("skipMatches: skipMatches", aq)


class StoredAlertPagingTests(unittest.TestCase):
    def _mgr(self, n):
        mgr = alertutils.AlertStateManager()
        mgr._initialized = True
        mgr._alert_storage = {f"id{i}": {"timestamp": 1000 + i} for i in range(n)}
        mgr._alert_storage_loaded = True
        return mgr

    def test_page_is_clamped_and_args_coerced(self) -> None:
        mgr = self._mgr(30)
        last = mgr.get_stored_alerts_paginated(page=5, limit=25)
        self.assertEqual((last["page"], last["total_pages"], len(last["alerts"])), (2, 2, 5))
        first = mgr.get_stored_alerts_paginated(page=-1, limit="10")
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["alerts"][0]["alert_id"], "id29")
        junk = mgr.get_stored_alerts_paginated(page="x", limit=None)
        self.assertEqual((junk["page"], junk["limit"]), (1, 25))
        lim = mgr.get_limited_stored_alerts_paginated(page=9, limit=10, max_total_alerts=15)
        self.assertEqual((lim["page"], lim["total_count"], len(lim["alerts"])), (2, 15, 5))

    def test_sorted_index_updates_on_store(self) -> None:
        mgr = self._mgr(3)
        self.assertEqual(mgr.get_stored_alerts_paginated(1, 1)["alerts"][0]["alert_id"], "id2")
        with patch("modules.alertutils.database_manager.set_data", return_value=True), patch.object(
            mgr, "schedule_auto_trim_stored_alerts"
        ):
            mgr.store_completed_alert("newest", {"timestamp": 5000})
        self.assertEqual(mgr.get_stored_alerts_paginated(1, 1)["alerts"][0]["alert_id"], "newest")

    def test_since_window(self) -> None:
        mgr = self._mgr(10)
        got = mgr.get_stored_alerts_since(1007)
        self.assertEqual([a["alert_id"] for a in got], ["id9", "id8", "id7"])
        self.assertEqual(len(mgr.get_stored_alerts_since(0, limit=4)), 4)

    def test_missing_timestamp_is_now(self) -> None:
        mgr = alertutils.AlertStateManager()
        with patch("modules.alertutils.time.time", return_value=1234.0):
            norm = mgr._normalize_alert_data_for_storage({"username": "u", "timestamp": 0})
        self.assertEqual(norm["timestamp"], 1234.0)


if __name__ == "__main__":
    unittest.main()

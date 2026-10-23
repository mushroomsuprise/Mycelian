#!/usr/bin/env python3
"""safe_emit never hangs a producer, keeps order, and restarts don't clobber state."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine as we  # noqa: E402
from modules.web_engine import WebEngine, _SafeEmitQueue  # noqa: E402


class _RecordingSocketIO:
    def __init__(self) -> None:
        self.emitted = []
        self.start_background_task = MagicMock()

    def emit(self, event, data=None, to=None):
        self.emitted.append((event, data, to))

    def sleep(self, seconds):
        time.sleep(seconds)


def _engine(maxsize: int = 4, *, drain_live: bool = False) -> WebEngine:
    eng = object.__new__(WebEngine)
    eng._server_thread_ident = None
    eng._safe_emit_priority_queue = _SafeEmitQueue(maxsize)
    eng._safe_emit_queue = _SafeEmitQueue(maxsize)
    eng._emit_wake_write = None
    eng._emit_wake_read = None
    eng._socket_emit_error_times = deque()
    eng._overload_lock = threading.Lock()
    eng._emit_delivery_lags_ms = deque(maxlen=64)
    eng.app = MagicMock()
    eng.app.app_context = contextlib.nullcontext
    eng.socketio = _RecordingSocketIO()
    eng.is_running = drain_live
    eng._template_control_worker_alive = drain_live
    eng._emit_drain_last_tick = time.monotonic() if drain_live else 0.0
    return eng


def _emit_from_thread(fn, timeout: float = 3.0) -> bool:
    """Run fn on a plain OS thread; True if it returned within timeout."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(timeout)
    return not t.is_alive()


class SafeEmitNoHangTests(unittest.TestCase):
    def test_game_hook_snapshots_coalesce_when_server_is_down(self) -> None:
        eng = _engine(maxsize=4)

        def produce() -> None:
            for i in range(5000):
                eng.safe_emit("game_hook_payload", {"i": i})

        self.assertTrue(_emit_from_thread(produce))
        self.assertEqual(eng._safe_emit_queue.qsize(), 1)
        self.assertEqual(eng._drain_safe_emit_queue(), 1)
        self.assertEqual(eng.socketio.emitted, [("game_hook_payload", {"i": 4999}, None)])

    def test_full_queue_with_no_drain_does_not_block_the_producer(self) -> None:
        eng = _engine(maxsize=3)
        results = []

        def produce() -> None:
            for i in range(10):
                results.append(eng.safe_emit("new-message", {"i": i}))

        with self.assertLogs("modules.web_engine", level="WARNING") as logs:
            self.assertTrue(_emit_from_thread(produce))
        self.assertEqual(results, [True] * 10)
        self.assertTrue(any("dropping oldest new-message" in m for m in logs.output))
        eng._drain_safe_emit_queue()
        # Newest backlog survives, still in order.
        self.assertEqual(
            [d["i"] for _e, d, _t in eng.socketio.emitted], [7, 8, 9]
        )

    def test_coalescing_keeps_latest_snapshot_and_order_of_other_events(self) -> None:
        eng = _engine(maxsize=100)
        eng.safe_emit("new-message", {"n": 1})
        eng.safe_emit("game_hook_payload", {"ts": 1})
        eng.safe_emit("new-message", {"n": 2})
        eng.safe_emit("game_hook_payload", {"ts": 2})
        eng.safe_emit("theme_update", {"t": "x"})
        eng.safe_emit("game_hook_payload", {"ts": 3}, to="sid-1")
        eng.safe_emit("new-message", {"n": 3})
        eng._drain_safe_emit_queue()
        self.assertEqual(
            eng.socketio.emitted,
            [
                ("new-message", {"n": 1}, None),
                ("game_hook_payload", {"ts": 2}, None),
                ("new-message", {"n": 2}, None),
                ("theme_update", {"t": "x"}, None),
                ("game_hook_payload", {"ts": 3}, "sid-1"),
                ("new-message", {"n": 3}, None),
            ],
        )
        # After delivery the next snapshot queues again.
        eng.safe_emit("game_hook_payload", {"ts": 4})
        self.assertEqual(eng._safe_emit_queue.qsize(), 1)

    def test_non_snapshot_events_are_never_coalesced(self) -> None:
        eng = _engine(maxsize=100)
        for i in range(5):
            eng.safe_emit("new-message", {"i": i})
            eng.safe_emit("next_alert", {"i": i})
        eng._drain_safe_emit_queue()
        names = [e for e, _d, _t in eng.socketio.emitted]
        # Priority (alerts) first, then the rest; each in call order.
        self.assertEqual(names, ["next_alert"] * 5 + ["new-message"] * 5)
        self.assertEqual(
            [d["i"] for e, d, _t in eng.socketio.emitted if e == "new-message"],
            list(range(5)),
        )

    def test_live_drain_still_applies_backpressure_without_dropping(self) -> None:
        eng = _engine(maxsize=2, drain_live=True)
        done = threading.Event()

        def produce() -> None:
            for i in range(4):
                eng.safe_emit("new-message", {"i": i})
            done.set()

        threading.Thread(target=produce, daemon=True).start()
        time.sleep(0.2)
        self.assertFalse(done.is_set(), "producer should wait for room")
        deadline = time.monotonic() + 3.0
        while not done.is_set() and time.monotonic() < deadline:
            eng._emit_drain_last_tick = time.monotonic()
            eng._drain_safe_emit_queue()
            time.sleep(0.01)
        self.assertTrue(done.is_set())
        eng._drain_safe_emit_queue()
        self.assertEqual([d["i"] for _e, d, _t in eng.socketio.emitted], [0, 1, 2, 3])

    def test_event_loop_thread_never_blocks_and_nothing_is_lost(self) -> None:
        eng = _engine(maxsize=2, drain_live=True)

        async def produce() -> None:
            for i in range(6):
                eng.safe_emit("new-message", {"i": i})

        self.assertTrue(_emit_from_thread(lambda: asyncio.run(produce()), timeout=1.0))
        eng._drain_safe_emit_queue()
        self.assertEqual(
            [d["i"] for _e, d, _t in eng.socketio.emitted], list(range(6))
        )

    def test_waiting_producer_stops_waiting_when_drain_dies(self) -> None:
        eng = _engine(maxsize=1, drain_live=True)
        eng.safe_emit("new-message", {"i": 0})

        def produce() -> None:
            eng.safe_emit("new-message", {"i": 1})

        t = threading.Thread(target=produce, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertTrue(t.is_alive())
        eng.is_running = False  # server stopped: nothing will drain
        t.join(3.0)
        self.assertFalse(t.is_alive())

    def test_stalled_drain_is_not_live(self) -> None:
        eng = _engine(drain_live=True)
        self.assertTrue(eng._safe_emit_drain_live())
        eng._emit_drain_last_tick = time.monotonic() - we._SAFE_EMIT_DRAIN_STALL_SEC - 1
        self.assertFalse(eng._safe_emit_drain_live())


class EmitWakeInGreenletTests(unittest.TestCase):
    def test_wake_reaches_a_greenlet_waiting_on_a_hub_thread(self) -> None:
        import gevent

        eng = object.__new__(WebEngine)
        eng._open_emit_wake_pipe()
        self.addCleanup(eng._close_emit_wake_pipe)
        eng.socketio = MagicMock()
        result = {}

        def hub_thread() -> None:
            def waiter() -> None:
                t0 = time.monotonic()
                result["woke"] = eng._wait_for_emit_wake(2.0)
                result["elapsed"] = time.monotonic() - t0

            gevent.spawn(waiter).join()

        t = threading.Thread(target=hub_thread, daemon=True)
        t.start()
        time.sleep(0.1)
        signalled_at = time.monotonic()
        eng._signal_emit_wake()
        t.join(3.0)
        self.assertTrue(result.get("woke"))
        self.assertLess(time.monotonic() - signalled_at, 0.5)
        eng.socketio.sleep.assert_not_called()


def _run_engine() -> WebEngine:
    """Bare engine with just what run() touches."""
    eng = object.__new__(WebEngine)
    eng.host = "127.0.0.1"
    eng.port = 5999
    eng.is_running = False
    eng._server_thread_ident = None
    eng._last_run_error = None
    eng.server_thread = None
    eng._heartbeat_lock = threading.Lock()
    eng._startup_client_sync_lock = threading.Lock()
    eng._template_control_emit_worker_lock = threading.Lock()
    eng._heartbeat_task_started = False
    eng._startup_client_sync_started = False
    eng._template_control_emit_worker_started = False
    eng._template_control_worker_alive = False
    eng._get_socket_connected_count = lambda: 0
    eng.ensure_web_engine_heartbeat = MagicMock()
    eng.ensure_startup_client_sync = MagicMock()
    eng.ensure_template_control_emit_worker = MagicMock()
    eng.request_restart = MagicMock()
    eng.socketio = MagicMock()
    eng.app = MagicMock()
    return eng


class RunGenerationTests(unittest.TestCase):
    def test_stale_run_finally_does_not_clobber_the_new_run(self) -> None:
        eng = _run_engine()
        release_a = threading.Event()
        release_b = threading.Event()
        a_started = threading.Event()
        b_started = threading.Event()
        idents = {}

        def fake_run(*_a, **_k):
            name = threading.current_thread().name
            idents[name] = threading.get_ident()
            if name == "A":
                a_started.set()
                release_a.wait(5)
                raise OSError("listener closed")  # stale serve_forever unblocks
            b_started.set()
            release_b.wait(5)

        eng.socketio.run.side_effect = fake_run
        ta = threading.Thread(target=eng.run, name="A", daemon=True)
        ta.start()
        self.assertTrue(a_started.wait(2))
        tb = threading.Thread(target=eng.run, name="B", daemon=True)
        tb.start()
        self.assertTrue(b_started.wait(2))

        release_a.set()
        ta.join(2)
        self.assertFalse(ta.is_alive())
        self.assertTrue(eng.is_running)
        self.assertEqual(eng._server_thread_ident, idents["B"])
        self.assertIsNone(eng._last_run_error)
        eng.request_restart.assert_not_called()
        self.assertTrue(eng._run_is_current(eng._run_generation))

        release_b.set()
        tb.join(2)
        self.assertFalse(eng.is_running)
        self.assertIsNone(eng._server_thread_ident)

    def test_current_run_error_still_schedules_recovery(self) -> None:
        eng = _run_engine()
        eng.socketio.run.side_effect = RuntimeError("boom")
        t = threading.Thread(target=eng.run, daemon=True)
        t.start()
        t.join(2)
        self.assertFalse(eng.is_running)
        self.assertEqual(eng._last_run_error, "boom")
        eng.request_restart.assert_called_once()

    def test_stale_loops_exit_and_do_not_reset_new_run_flags(self) -> None:
        eng = _run_engine()
        eng.is_running = True
        eng._run_generation = 2
        eng._template_control_emit_worker_started = True
        eng._template_control_worker_alive = True
        eng._heartbeat_task_started = True
        eng._run_thread_generations = {threading.get_ident(): 1}
        eng._template_control_queue = MagicMock()
        eng._web_engine_heartbeat_loop()
        eng._template_control_emit_loop()
        self.assertTrue(eng._template_control_emit_worker_started)
        self.assertTrue(eng._template_control_worker_alive)
        self.assertTrue(eng._heartbeat_task_started)
        eng.socketio.sleep.assert_not_called()

    def test_run_rearms_workers_left_latched_by_a_failed_bind(self) -> None:
        eng = _run_engine()
        del eng.ensure_web_engine_heartbeat
        del eng.ensure_startup_client_sync
        del eng.ensure_template_control_emit_worker
        eng._watchdog_thread = MagicMock()
        eng._watchdog_thread.is_alive.return_value = True
        eng._last_gevent_heartbeat = 1.0  # seed from the failed first run
        # First run's bind failed after arming: flags stay latched.
        eng._heartbeat_task_started = True
        eng._startup_client_sync_started = True
        eng._template_control_emit_worker_started = True
        eng.socketio.run.return_value = None
        t = threading.Thread(target=eng.run, daemon=True)
        t.start()
        t.join(2)
        spawned = [c.args[0].__name__ for c in eng.socketio.start_background_task.call_args_list]
        self.assertEqual(
            spawned,
            [
                "_web_engine_heartbeat_loop",
                "_startup_client_sync_loop",
                "_template_control_emit_loop",
            ],
        )
        self.assertGreater(eng._last_gevent_heartbeat, 1.0)


class HeartbeatGuardTests(unittest.TestCase):
    def test_exception_in_a_tick_does_not_stop_the_heartbeat(self) -> None:
        eng = object.__new__(WebEngine)
        eng.is_running = True
        eng._heartbeat_lock = threading.Lock()
        eng._heartbeat_task_started = True
        eng._HEARTBEAT_EXPECTED_INTERVAL_SEC = 0
        eng._last_heartbeat_mono = None
        eng._last_gevent_heartbeat = None
        eng._get_socket_connected_count = lambda: 0
        eng._maybe_log_bottleneck_calibration_watch = lambda _m: None
        calls = {"n": 0}
        stamps = []

        def diag():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad metric")
            return {"metrics": {}, "bottlenecked": False}

        eng._get_bottleneck_diagnostics = diag

        def fake_sleep(_s):
            stamps.append(eng._last_gevent_heartbeat)
            if len(stamps) >= 4:
                eng.is_running = False

        eng.socketio = MagicMock()
        eng.socketio.sleep.side_effect = fake_sleep
        with patch.object(we.time, "time", side_effect=lambda: 1000.0 + len(stamps)):
            eng._web_engine_heartbeat_loop()
        self.assertGreaterEqual(calls["n"], 3)
        # Heartbeat kept advancing across (and after) the failed tick.
        self.assertEqual(stamps[1:], sorted(set(stamps[1:])))
        self.assertIsNotNone(stamps[-1])
        self.assertFalse(eng._heartbeat_task_started)


if __name__ == "__main__":
    unittest.main()

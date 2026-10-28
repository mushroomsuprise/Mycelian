# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alert_processor.py: queue helpers, process_alert handshake, queue loop, init/cleanup.

The overlay server (web_engine) is replaced by a fake; no threads or sockets are started.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alert_playback_session as aps  # noqa: E402
from modules import alert_processor as ap  # noqa: E402
from modules import alertutils, template_config_parser as tcp  # noqa: E402
from modules.alertutils import AlertObj  # noqa: E402


class FakeEngine:
    """Stands in for the web_engine module (module-level functions + flags)."""

    def __init__(self):
        self.ALERTS_PAUSED = False
        self.ALERT_PLAYING = False
        self.web_engine_instance = None
        self.calls = []
        self.seq = 0
        self.broadcast_error = None

    def broadcast_alert_queue_state(self):
        self.calls.append(("broadcast",))
        if self.broadcast_error:
            raise self.broadcast_error

    def assign_next_alert_queue_seq(self):
        self.seq += 1
        return self.seq

    def begin_alert_playback_session(self, seq, holders, duration, delay):
        self.calls.append(("begin", seq, tuple(holders), duration, delay))
        aps.begin_session(seq, holders, duration, delay)

    def set_alert_playing(self, value):
        self.calls.append(("playing", value))
        self.ALERT_PLAYING = value

    def abort_alert_playback(self, reason):
        self.calls.append(("abort", reason))
        aps.abort_session(reason)
        self.ALERT_PLAYING = False

    def emit_alert_skip(self, seq, alert_id, why):
        self.calls.append(("skip", seq, alert_id, why))

    def _apply_playback_session_finished(self, flag):
        self.calls.append(("finished", flag))
        self.ALERT_PLAYING = False

    def set_alerts_paused(self, value):
        self.calls.append(("paused", value))
        self.ALERTS_PAUSED = value

    def names(self):
        return [c[0] for c in self.calls]


class FakeOverlay:
    def __init__(self, ok=True, on_emit=None):
        self.ok = ok
        self.on_emit = on_emit
        self.emitted = []

    def next_alert(self, data):
        self.emitted.append(data)
        if self.on_emit:
            self.on_emit(data)
        return self.ok


@pytest.fixture
def eng(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(ap, "web_engine", fake)
    monkeypatch.setattr(ap, "web_engine_instance", None)
    monkeypatch.setattr(ap, "ALERT_QUEUE", deque())
    monkeypatch.setattr(ap, "_current_alert", None)
    monkeypatch.setattr(ap, "_queue_error_state", {"streak": 0, "last_notified": None, "escalated": False})
    monkeypatch.setattr(ap, "is_shutdown_in_progress", lambda: False)
    monkeypatch.setattr(aps, "_current_session", None)
    monkeypatch.setattr(tcp, "delay_between_alerts_seconds", lambda: 0.0)
    notices = []
    monkeypatch.setattr(ap, "notify", lambda *a, **k: notices.append(("notify", a, k)))
    monkeypatch.setattr(ap, "notify_critical", lambda *a, **k: notices.append(("critical", a, k)))
    fake.notices = notices
    wake, complete = ap._ALERT_WAKE, ap._ALERT_COMPLETE
    wake.clear()
    complete.clear()
    yield fake
    wake.clear()
    complete.clear()


def _alert(aid="A1", **kw):
    a = AlertObj(alert_id=aid, alert_type=kw.pop("alert_type", "follow"), username=kw.pop("username", "Ann"))
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# ------------------------------------------------------------------ _LazyWebEngine

def test_lazy_web_engine_loads_real_module_once():
    from modules import web_engine as real

    lazy = ap._LazyWebEngine()
    assert object.__getattribute__(lazy, "_mod") is None
    assert lazy._load() is real
    assert object.__getattribute__(lazy, "_mod") is real
    assert lazy.set_alerts_paused is real.set_alerts_paused


def test_lazy_web_engine_get_and_set_forward():
    target = SimpleNamespace(x=1)
    lazy = ap._LazyWebEngine()
    object.__setattr__(lazy, "_mod", target)
    assert lazy.x == 1
    lazy.y = 5
    assert target.y == 5


# ------------------------------------------------------------------ wake / wait / notify

def test_wake_and_wait_idle():
    ap._ALERT_WAKE.clear()
    ap.wake_alert_queue()
    ap.wait_for_alert_queue(playing=False, paused=False)  # returns immediately (set)
    assert not ap._ALERT_WAKE.is_set()


@pytest.mark.parametrize("playing,paused,timeout", [(False, True, 0.5), (True, False, 0.2)])
def test_wait_uses_timeouts(monkeypatch, playing, paused, timeout):
    seen = []
    fake = SimpleNamespace(wait=lambda timeout=None: seen.append(timeout), clear=lambda: seen.append("clear"))
    monkeypatch.setattr(ap, "_ALERT_WAKE", fake)
    ap.wait_for_alert_queue(playing=playing, paused=paused)
    assert seen == [timeout, "clear"]


def test_notify_alert_playing():
    ap._ALERT_COMPLETE.set()
    ap.notify_alert_playing(True)
    assert not ap._ALERT_COMPLETE.is_set() and ap._ALERT_WAKE.is_set()
    ap._ALERT_WAKE.clear()
    ap.notify_alert_playing(False)
    assert ap._ALERT_COMPLETE.is_set() and ap._ALERT_WAKE.is_set()


# ------------------------------------------------------------------ queue helpers

def test_broadcast_swallows_errors(eng):
    eng.broadcast_error = RuntimeError("socket")
    ap._broadcast_queue_state()
    assert eng.names() == ["broadcast"]


def test_enqueue_snapshot_and_remove(eng):
    a, b = _alert("A"), _alert("B")
    ap.enqueue_alert(a)
    ap.enqueue_alert(b)
    assert ap._ALERT_WAKE.is_set()
    snap = ap.snapshot_alert_queue()
    assert snap == [a, b] and snap is not ap.ALERT_QUEUE
    assert ap.remove_queued_alert(a) is True
    assert ap.remove_queued_alert(a) is False
    assert ap.snapshot_alert_queue() == [b]
    assert eng.names().count("broadcast") == 3


def test_play_order_snapshot(eng):
    a = _alert("A")
    s = _alert("S", stackable=True)
    k = _alert("K", skip_alert=True)
    b = _alert("B")
    for x in (a, s, k, b):
        ap.ALERT_QUEUE.append(x)
    assert ap.play_order_snapshot() == [s, a, b]


def test_remove_queued_alert_by_id(eng):
    a, b = _alert("A"), _alert("B")
    ap.ALERT_QUEUE.extend([a, b])
    assert ap.remove_queued_alert_by_id("") is None
    assert ap.remove_queued_alert_by_id(None) is None
    assert ap.remove_queued_alert_by_id("zzz") is None
    assert eng.calls == []
    assert ap.remove_queued_alert_by_id(" B ") is b
    assert list(ap.ALERT_QUEUE) == [a]
    assert eng.names() == ["broadcast"]


def test_current_alert_info_copy(eng):
    assert ap.current_alert_info() is None
    ap._set_current_alert({"a": 1})
    info = ap.current_alert_info()
    info["a"] = 2
    assert ap.current_alert_info() == {"a": 1}
    ap._set_current_alert({})
    assert ap.current_alert_info() is None


def test_note_current_alert_duration(eng):
    assert ap.note_current_alert_duration(1, "abc") is False
    assert ap.note_current_alert_duration(1, 0) is False
    assert ap.note_current_alert_duration(1, 3_600_001) is False
    assert ap.note_current_alert_duration(1, 1000) is False  # nothing playing
    ap._set_current_alert({"queue_seq": 1, "duration_ms": None})
    assert ap.note_current_alert_duration(2, 1000) is False  # other seq
    assert ap.note_current_alert_duration(1, 1500.4) is True
    assert ap.current_alert_info()["duration_ms"] == 1500
    assert ap.note_current_alert_duration(1, 1200) is False  # keeps the longest
    assert ap.note_current_alert_duration(1, 2000) is True
    assert ap.current_alert_info()["overlay_duration_ms"] == 2000
    assert eng.names().count("broadcast") == 2


@pytest.mark.parametrize("duration,expected", [(0, None), (None, None), ("x", None), (-1, None),
                                               (2.5, 2500), ("1.0004", 1000)])
def test_alert_duration_ms(duration, expected):
    assert ap._alert_duration_ms(SimpleNamespace(duration=duration)) == expected


def test_overlay_engine_prefers_module_singleton(eng, monkeypatch):
    assert ap._overlay_engine() is None
    local = object()
    monkeypatch.setattr(ap, "web_engine_instance", local)
    assert ap._overlay_engine() is local
    canonical = object()
    eng.web_engine_instance = canonical
    assert ap._overlay_engine() is canonical


@pytest.mark.parametrize("alert,label", [
    (SimpleNamespace(alert_type="gift_sub", username="Bo"), "gift sub alert from Bo"),
    (SimpleNamespace(alert_type="", username=""), "an alert"),
    (SimpleNamespace(), "an alert"),
    (SimpleNamespace(alert_type="raid", username="  "), "raid alert"),
])
def test_alert_label(alert, label):
    assert ap._alert_label(alert) == label


def test_notify_alert_dropped(eng):
    ap._notify_alert_dropped(_alert("X", alert_type="raid", username="Cy"), "no_engine")
    kind, args, kwargs = eng.notices[0]
    assert kind == "notify"
    assert "raid alert from Cy was skipped" in args[0]
    assert kwargs["details"] == "reason=no_engine alert_id=X"
    assert kwargs["dedupe_key"] == "alert:dropped"


# ------------------------------------------------------------------ process_alert

def test_process_alert_no_engine(eng):
    ap.process_alert(_alert("N"))
    assert ("abort", "no_engine") in eng.calls
    assert eng.notices[0][2]["dedupe_key"] == "alert:dropped"
    assert ap.current_alert_info() is None
    assert eng.calls[-1] == ("broadcast",)


def test_process_alert_emit_failed(eng):
    eng.web_engine_instance = FakeOverlay(ok=False)
    ap.process_alert(_alert("E"))
    assert ("playing", True) in eng.calls and ("abort", "emit_failed") in eng.calls
    assert "reason=emit_failed" in eng.notices[0][2]["details"]


def test_process_alert_all_holders_complete(eng, monkeypatch):
    infos = []

    def on_emit(data):
        infos.append(ap.current_alert_info())
        s = aps.get_session()
        s.note_complete("sid1", "alerts")

    overlay = FakeOverlay(on_emit=on_emit)
    eng.web_engine_instance = overlay
    monkeypatch.setattr(ap.time, "time", lambda: 10.0)
    a = _alert("OK", duration=2, is_replay=True)
    ap.process_alert(a)
    data = overlay.emitted[0]
    assert data["queue_seq"] == 1 and data["queue_holders"] == ["alerts"]
    assert data["alert_id"] == "OK"
    assert not hasattr(a, "queue_seq")  # the AlertObj itself is unchanged
    begin = [c for c in eng.calls if c[0] == "begin"][0]
    assert begin == ("begin", 1, ("alerts",), 2.0, 0.0)
    info = infos[0]
    assert info == {"alert_id": "OK", "type": "follow", "username": "Ann", "started_at_ms": 10000,
                    "duration_ms": 2000, "queue_seq": 1, "is_replay": True}
    # session finished during emit -> set_alert_playing(False), no wait loop
    assert eng.calls.count(("playing", False)) == 1
    assert ("finished", True) not in eng.calls
    assert ap.current_alert_info() is None


def test_process_alert_skip_before_emit_resends_skip(eng):
    def on_emit(data):
        aps.get_session().force_finish("skip")

    eng.web_engine_instance = FakeOverlay(on_emit=on_emit)
    ap.process_alert(_alert("SK"))
    assert ("skip", 1, "SK", "late") in eng.calls


def test_process_alert_session_replaced_during_emit(eng):
    def on_emit(data):
        aps.begin_session(99, ["alerts"], 1)

    eng.web_engine_instance = FakeOverlay(on_emit=on_emit)
    ap.process_alert(_alert("R"))
    assert ("playing", False) in eng.calls
    assert not any(c[0] == "skip" for c in eng.calls)


def test_process_alert_waits_until_tick_finishes(eng, monkeypatch):
    eng.web_engine_instance = FakeOverlay()
    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        if len(waits) == 2:
            # The holder reports done; the next tick() notices and finishes the session.
            session = aps.get_session()
            session.required_sids.add("sid")
            session.completed_sids.add("sid")
        return False

    monkeypatch.setattr(ap, "_ALERT_COMPLETE", SimpleNamespace(wait=fake_wait, set=lambda: None,
                                                               clear=lambda: None))
    ap.process_alert(_alert("W"))
    assert waits == [0.2, 0.2]
    assert ("finished", True) in eng.calls


@pytest.mark.parametrize("reason", ["absolute_timeout", "no_ack_fallback"])
def test_process_alert_fallback_reasons_logged(eng, monkeypatch, caplog, reason):
    eng.web_engine_instance = FakeOverlay()

    def fake_tick(self, now=None):
        self.finished = True
        self.finish_reason = reason
        return True

    monkeypatch.setattr(aps.AlertPlaybackSession, "tick", fake_tick)
    with caplog.at_level("WARNING", logger=ap.logger.name):
        ap.process_alert(_alert("T"))
    assert f"finished via {reason}" in caplog.text
    assert ("finished", True) in eng.calls


def test_process_alert_shutdown_aborts_wait(eng, monkeypatch):
    eng.web_engine_instance = FakeOverlay()
    monkeypatch.setattr(ap, "is_shutdown_in_progress", lambda: True)
    ap.process_alert(_alert("S"))
    assert ("abort", "shutdown") in eng.calls


def test_process_alert_session_superseded_while_waiting(eng, monkeypatch):
    eng.web_engine_instance = FakeOverlay()

    def fake_wait(timeout=None):
        aps.reset_session_for_tests()

    monkeypatch.setattr(ap, "_ALERT_COMPLETE", SimpleNamespace(wait=fake_wait))
    ap.process_alert(_alert("S2"))
    assert eng.calls.count(("playing", False)) == 1
    assert eng.ALERT_PLAYING is False


def test_process_alert_exception_notifies(eng):
    def boom():
        raise RuntimeError("seq fail")

    eng.assign_next_alert_queue_seq = boom
    ap.process_alert(_alert("X", alert_type="sub"))
    assert ("abort", "process_failed") in eng.calls
    kind, args, kwargs = eng.notices[0]
    assert "sub alert from Ann couldn't play" in args[0]
    assert kwargs["details"] == "RuntimeError: seq fail"
    assert kwargs["dedupe_key"] == "alert:process_failed"


# ------------------------------------------------------------------ point alert template duration

@pytest.fixture
def point_tpl(monkeypatch):
    state = {"silent": True, "cfg": {"route": "wheel"}, "lookups": []}
    monkeypatch.setattr(tcp, "point_alert_silent_no_media_enabled", lambda: state["silent"])
    monkeypatch.setattr(tcp, "find_template_config_for_reward_title",
                        lambda name: state["lookups"].append(name) or state["cfg"])
    monkeypatch.setattr(tcp, "point_reward_template_duration_seconds", lambda cfg: 12.0)
    return state


def _run_point(eng, alert):
    overlay = FakeOverlay(on_emit=lambda d: aps.get_session().force_finish("done"))
    eng.web_engine_instance = overlay
    ap.process_alert(alert)
    return overlay.emitted[0]


def test_point_without_media_uses_template_duration(eng, point_tpl):
    data = _run_point(eng, _alert("P", alert_type="point", alert_name="Spin", duration=3))
    assert data["duration"] == 12.0
    assert point_tpl["lookups"] == ["Spin"]


def test_point_without_media_no_template(eng, point_tpl):
    point_tpl["cfg"] = None
    data = _run_point(eng, _alert("P", alert_type="point", alert_name="", duration=3))
    assert data["duration"] == 3 and point_tpl["lookups"] == [""]


def test_point_without_media_silent_disabled(eng, point_tpl):
    point_tpl["silent"] = False
    data = _run_point(eng, _alert("P", alert_type="point", alert_name="Spin", duration=3))
    assert data["duration"] == 3 and point_tpl["lookups"] == []


def test_point_template_lookup_error_is_ignored(eng, monkeypatch, point_tpl):
    monkeypatch.setattr(tcp, "point_alert_silent_no_media_enabled",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    data = _run_point(eng, _alert("P", alert_type="point", alert_name="Spin", duration=3))
    assert data["duration"] == 3


@pytest.mark.parametrize("extra", [
    {"gif_dir": "d", "gif_name": "g.gif"},
    {"single_audio_dir": "d", "single_audio_name": "a.mp3"},
    {"randomized": True},
    {"hold_queue_only": True},
])
def test_point_with_media_or_hold_skips_template(eng, point_tpl, extra):
    data = _run_point(eng, _alert("P", alert_type="point", alert_name="Spin", duration=3, **extra))
    assert data["duration"] == 3 and point_tpl["lookups"] == []


def test_point_blank_gif_strings_count_as_missing(eng, point_tpl):
    data = _run_point(eng, _alert("P", alert_type="point", alert_name="Spin", duration=3,
                                  gif_dir="  ", gif_name="g"))
    assert data["duration"] == 12.0


# ------------------------------------------------------------------ queue error notices

def test_note_queue_error_cooldown_blocks_second_streak(eng, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ap.time, "monotonic", lambda: now[0])
    ap._note_queue_error(RuntimeError("a"))
    ap._note_queue_ok()
    now[0] += 10
    ap._note_queue_error(RuntimeError("b"))
    assert len(eng.notices) == 1
    ap._note_queue_ok()
    now[0] += ap._QUEUE_ERROR_NOTIFY_COOLDOWN_SEC
    ap._note_queue_error(RuntimeError("c"))
    assert len(eng.notices) == 2
    assert eng.notices[-1][2]["details"] == "RuntimeError: c"


def test_note_queue_error_escalates_once(eng, monkeypatch):
    monkeypatch.setattr(ap, "_QUEUE_ERROR_ESCALATE_AFTER", 3)
    for _ in range(5):
        ap._note_queue_error(ValueError("stuck"))
    kinds = [n[0] for n in eng.notices]
    assert kinds == ["notify", "critical"]
    assert eng.notices[1][2]["dedupe_key"] == "alert:queue_stuck"
    assert ap._queue_error_state["escalated"] is True


def test_note_queue_ok_noop_without_streak(eng):
    ap._queue_error_state["escalated"] = True
    ap._note_queue_ok()
    assert ap._queue_error_state["escalated"] is True


# ------------------------------------------------------------------ alert_queue loop

class LoopDriver:
    """Lets alert_queue run a fixed number of passes, then reports shutdown."""

    def __init__(self, monkeypatch, passes, on_wait=None):
        self.passes = passes
        self.checks = 0
        self.waits = []
        self.on_wait = on_wait
        monkeypatch.setattr(ap, "is_shutdown_in_progress", self.shutdown)
        monkeypatch.setattr(ap, "wait_for_alert_queue", self.wait)
        self.sleeps = []
        monkeypatch.setattr(ap.time, "sleep", lambda s: self.sleeps.append(s))

    def shutdown(self):
        self.checks += 1
        return self.checks > self.passes

    def wait(self, *, playing, paused):
        self.waits.append((playing, paused))
        if self.on_wait:
            self.on_wait(len(self.waits))


def test_alert_queue_stops_on_shutdown(eng, monkeypatch):
    LoopDriver(monkeypatch, 0)
    ap.alert_queue()
    assert ap.alert_queue_active is False


def test_alert_queue_paused_then_resumed(eng, monkeypatch):
    def on_wait(n):
        if n == 2:
            eng.ALERTS_PAUSED = False

    eng.ALERTS_PAUSED = True
    drv = LoopDriver(monkeypatch, 3, on_wait)
    ap.alert_queue()
    assert drv.waits == [(False, True), (False, True), (False, False)]


def test_alert_queue_plays_skips_and_stackables(eng, monkeypatch):
    played = []
    monkeypatch.setattr(ap, "process_alert", lambda a: played.append(a.alert_id))
    skip = _alert("SKIP", skip_alert=True)
    normal = _alert("N1")
    stack = _alert("ST", stackable=True)
    normal2 = _alert("N2")
    ap.ALERT_QUEUE.extend([skip, normal, stack, normal2])
    drv = LoopDriver(monkeypatch, 3)
    ap.alert_queue()
    assert played == ["ST", "N1", "N2"]
    assert list(ap.ALERT_QUEUE) == []
    # pass 3: empty, nothing stacked/playing -> idle wait
    assert drv.waits == [(False, False)]


def test_alert_queue_stackable_already_removed(eng, monkeypatch):
    played = []
    monkeypatch.setattr(ap, "process_alert", lambda a: played.append(a.alert_id))
    stack = _alert("ST", stackable=True)
    ap.ALERT_QUEUE.append(stack)
    monkeypatch.setattr(ap, "remove_queued_alert", lambda a: False)
    LoopDriver(monkeypatch, 1)
    ap.alert_queue()
    # The removal "failed" (someone else took it) so it isn't processed as stackable,
    # but popleft still takes it as a normal alert.
    assert played == ["ST"]


def test_alert_queue_waits_while_playing(eng, monkeypatch):
    eng.ALERT_PLAYING = True
    ap.ALERT_QUEUE.append(_alert("Q"))
    drv = LoopDriver(monkeypatch, 2)
    ap.alert_queue()
    assert drv.waits == [(True, False), (True, False)]
    assert ap.alert_queue_active is False  # set False at shutdown


def test_alert_queue_skip_only_pass_waits_with_playing_flag(eng, monkeypatch):
    ap.ALERT_QUEUE.append(_alert("K", skip_alert=True))
    drv = LoopDriver(monkeypatch, 1)
    ap.alert_queue()
    assert drv.waits == [(False, False)]
    assert list(ap.ALERT_QUEUE) == []


def test_alert_queue_error_sleeps_and_notifies(eng, monkeypatch):
    calls = {"n": 0}

    def bad_snapshot():
        calls["n"] += 1
        raise RuntimeError("broken")

    monkeypatch.setattr(ap, "snapshot_alert_queue", bad_snapshot)
    drv = LoopDriver(monkeypatch, 2)
    ap.alert_queue()
    assert drv.sleeps == [1.0, 1.0]
    assert ap._queue_error_state["streak"] == 2
    assert len(eng.notices) == 1


def test_alert_queue_success_resets_error_streak(eng, monkeypatch):
    ap._queue_error_state.update(streak=5, escalated=True)
    LoopDriver(monkeypatch, 1)
    ap.alert_queue()
    assert ap._queue_error_state["streak"] == 0 and ap._queue_error_state["escalated"] is False


# ------------------------------------------------------------------ initialize / cleanup

class FakeThread:
    started = []

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.name = name
        self.alive = True
        self.joined = []

    def start(self):
        FakeThread.started.append(self.name)
        if self.name == "SporeSidecarMigrate":
            self.target()

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joined.append(timeout)


class FakeWebEngineInstance:
    def __init__(self, template_dir):
        self.template_dir = template_dir
        self.calls = []
        self.port = 5000
        self.port_open = False
        self.fail = set()

    def prepare_port_for_startup(self):
        self.calls.append("prepare")
        if "prepare" in FakeWebEngineInstance.fail_all:
            raise RuntimeError("port busy")

    def run(self):
        pass

    def start_supervisor(self):
        self.calls.append("supervisor")
        if "supervisor" in FakeWebEngineInstance.fail_all:
            raise RuntimeError("no supervisor")

    def force_stop(self):
        self.calls.append("force_stop")

    def _port_is_open(self):
        return self.port_open


FakeWebEngineInstance.fail_all = set()


@pytest.fixture
def init_env(eng, monkeypatch, tmp_path):
    from modules import game_hooks_service as ghs, path_utils, spore_studio

    FakeThread.started = []
    FakeWebEngineInstance.fail_all = set()
    monkeypatch.setattr(ap, "threading", SimpleNamespace(Thread=FakeThread))
    monkeypatch.setattr(ap, "_initialized", False)
    monkeypatch.setattr(ap, "alert_thread", None)
    monkeypatch.setattr(ap, "web_thread", None)
    monkeypatch.setattr(ap, "alert_queue_active", False)
    eng.WebEngine = FakeWebEngineInstance
    monkeypatch.setattr(path_utils, "get_template_path", lambda: str(tmp_path))
    state = {"init_calls": 0, "loaded_missing": 0, "prefetch": 0, "hooks": [], "migrated": 0}

    def init_state():
        state["init_calls"] += 1

    monkeypatch.setattr(ap, "initialize_alert_state", init_state)
    manager = SimpleNamespace(
        _initialized=True,
        load_missing_alert_paths=lambda: state.__setitem__("loaded_missing", state["loaded_missing"] + 1),
        prefetch_alert_storage=lambda: state.__setitem__("prefetch", state["prefetch"] + 1),
    )
    monkeypatch.setattr(alertutils, "alert_state_manager", manager)
    hooks = SimpleNamespace(start=lambda: state["hooks"].append("start"),
                            stop=lambda: state["hooks"].append("stop"))
    monkeypatch.setattr(ghs, "game_hooks_service", hooks)
    monkeypatch.setattr(spore_studio, "ensure_sidecar_migration",
                        lambda: state.__setitem__("migrated", state["migrated"] + 1))
    state["manager"] = manager
    state["hooks_obj"] = hooks
    return state


def test_initialize_full(eng, init_env, tmp_path):
    ap.initialize()
    inst = eng.web_engine_instance
    assert isinstance(inst, FakeWebEngineInstance) and ap.web_engine_instance is inst
    assert inst.template_dir == str(tmp_path)
    assert inst.calls == ["prepare", "supervisor"]
    assert inst.server_thread is ap.web_thread
    assert FakeThread.started == ["WebEngine", "AlertProcessor", "SporeSidecarMigrate"]
    assert ap.alert_thread.target is ap.alert_queue
    assert init_env["loaded_missing"] == 1 and init_env["init_calls"] == 0
    assert init_env["prefetch"] == 1 and init_env["hooks"] == ["start"] and init_env["migrated"] == 1
    assert ap._initialized is True and ap.alert_queue_active is True
    # second call is a no-op
    ap.initialize()
    assert FakeThread.started.count("WebEngine") == 1


@pytest.mark.parametrize("manager", [None, SimpleNamespace(_initialized=False, prefetch_alert_storage=lambda: None)])
def test_initialize_inits_state_when_needed(eng, init_env, monkeypatch, manager):
    monkeypatch.setattr(alertutils, "alert_state_manager", manager)
    ap.initialize()
    assert init_env["init_calls"] == 1 and init_env["loaded_missing"] == 0


def test_initialize_tolerates_failures(eng, init_env, monkeypatch):
    from modules import game_hooks_service as ghs, spore_studio

    FakeWebEngineInstance.fail_all = {"prepare", "supervisor"}
    monkeypatch.setattr(alertutils, "alert_state_manager", None)  # prefetch -> AttributeError
    monkeypatch.setattr(ghs, "game_hooks_service",
                        SimpleNamespace(start=lambda: (_ for _ in ()).throw(RuntimeError("hook"))))
    monkeypatch.setattr(spore_studio, "ensure_sidecar_migration",
                        lambda: (_ for _ in ()).throw(RuntimeError("spore")))

    class DeadThread(FakeThread):
        def is_alive(self):
            return False

    monkeypatch.setattr(ap, "threading", SimpleNamespace(Thread=DeadThread))
    ap.initialize()
    assert ap._initialized is True
    crit = [n for n in eng.notices if n[0] == "critical"]
    assert crit and crit[0][2]["dedupe_key"] == "game_hooks:start_failed"
    assert "RuntimeError: hook" in crit[0][2]["details"]


def test_cleanup_full(eng, init_env, monkeypatch):
    ap.initialize()
    inst = eng.web_engine_instance
    inst.port_open = True
    web_thread, alert_thread = ap.web_thread, ap.alert_thread
    ap._ALERT_WAKE.clear()
    ap.cleanup()
    assert ("paused", True) in eng.calls
    assert ap._ALERT_WAKE.is_set()
    assert init_env["hooks"] == ["start", "stop"]
    assert inst.calls[-1] == "force_stop"
    assert web_thread.joined == [15.0] and alert_thread.joined == [1]
    assert ap._initialized is False and ap.alert_queue_active is False


def test_cleanup_without_engine_or_threads(eng, init_env, monkeypatch):
    from modules import game_hooks_service as ghs

    monkeypatch.setattr(ghs, "game_hooks_service",
                        SimpleNamespace(stop=lambda: (_ for _ in ()).throw(RuntimeError("x"))))
    monkeypatch.setattr(ap, "_initialized", True)
    ap.cleanup()
    assert ap._initialized is False


def test_cleanup_error_is_logged(eng, monkeypatch, caplog):
    def boom(v):
        raise RuntimeError("pause fail")

    eng.set_alerts_paused = boom
    monkeypatch.setattr(ap, "_initialized", True)
    with caplog.at_level("ERROR", logger=ap.logger.name):
        ap.cleanup()
    assert "Error during alert processor cleanup" in caplog.text
    assert ap._initialized is True

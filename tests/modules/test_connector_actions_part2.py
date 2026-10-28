# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_actions: AudioChangeEntry and AudioControlAction (stacking registry, restoration,
every Windows / macOS / Linux branch) with fake pycaw, comtypes, pulsectl and osascript."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import platform
import subprocess
import types
from types import SimpleNamespace

import pytest

import modules
from modules import connector_actions as ca, connector_core as core
from modules.connector_actions import AudioChangeEntry, AudioControlAction
from modules.connector_core import ActionType

TRIGGER = {"trigger_id": "t1", "triggered_at": 1.0}
REAL_SLEEP = asyncio.sleep


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr(AudioControlAction, "_active_restoration_tasks", {})
    monkeypatch.setattr(AudioControlAction, "_audio_change_registry", {})


@pytest.fixture
def slot():
    data = {"connector_id": "cid", "connector_name": "Conn"}
    token = core._action_problem.set(data)
    yield data
    core._action_problem.reset(token)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []

    async def fake_sleep(seconds, *a, **k):
        calls.append(seconds)
        await REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


def on(monkeypatch, system):
    monkeypatch.setattr(platform, "system", lambda: system)


def audio(**kw):
    return ca.create_action(ActionType.AUDIO_CONTROL, "aid", "n", **kw)


# --------------------------------------------------------------------------- fakes


class Volume:
    """IAudioEndpointVolume / ISimpleAudioVolume stand-in."""

    def __init__(self, level=0.5, muted=0):
        self.level, self.muted = level, muted
        self.calls = []

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.calls.append(("level", v))
        self.level = v

    def GetMasterVolume(self):
        return self.level

    def SetMasterVolume(self, v, ctx):
        self.calls.append(("level", v))
        self.level = v

    def GetMute(self):
        return self.muted

    def SetMute(self, m, ctx):
        self.calls.append(("mute", m))
        self.muted = m


class Pycaw:
    def __init__(self, monkeypatch, sessions=None, error=None):
        self.speaker = Volume()
        self.mic = Volume(level=0.3)
        self.error = error
        outer = self

        class Device:
            def __init__(self, vol):
                self.vol = vol

            def Activate(self, iid, ctx, x):
                if outer.error:
                    raise outer.error
                return SimpleNamespace(QueryInterface=lambda iface: self.vol)

        class AudioUtilities:
            @staticmethod
            def GetSpeakers():
                return Device(outer.speaker)

            @staticmethod
            def GetMicrophone():
                return Device(outer.mic)

            @staticmethod
            def GetAllSessions():
                if outer.error:
                    raise outer.error
                return outer.sessions

        self.sessions = sessions or []
        pkg = types.ModuleType("pycaw")
        mod = types.ModuleType("pycaw.pycaw")
        mod.AudioUtilities = AudioUtilities
        mod.IAudioEndpointVolume = SimpleNamespace(_iid_="iid")
        mod.ISimpleAudioVolume = object()
        pkg.pycaw = mod
        comtypes = types.ModuleType("comtypes")
        comtypes.CLSCTX_ALL = 23
        monkeypatch.setitem(sys.modules, "pycaw", pkg)
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", mod)
        monkeypatch.setitem(sys.modules, "comtypes", comtypes)


def session(name, vol):
    proc = SimpleNamespace(name=lambda: name) if name else None
    return SimpleNamespace(Process=proc, _ctl=SimpleNamespace(QueryInterface=lambda iface: vol))


class Sink:
    def __init__(self, name="sink", vol=0.5, mute=False):
        self.name = name
        self.volume = SimpleNamespace(value_flat=vol)
        self.mute = mute


class Pulse:
    def __init__(self, monkeypatch, sinks=None, sources=None, error=None):
        self.sinks = [Sink()] if sinks is None else sinks
        self.sources = [Sink("mic")] if sources is None else sources
        self.calls = []
        self.error = error
        outer = self

        class P:
            def __init__(self, name):
                outer.calls.append(("open", name))

            def __enter__(self):
                if outer.error:
                    raise outer.error
                return self

            def __exit__(self, *a):
                return False

            def sink_list(self):
                return outer.sinks

            def source_list(self):
                return outer.sources

            def volume_set_all_chans(self, sink, v):
                outer.calls.append(("vol", sink.name, round(v, 4)))

            def mute(self, sink, m):
                outer.calls.append(("mute", sink.name, m))

        mod = types.ModuleType("pulsectl")
        mod.Pulse = P
        monkeypatch.setitem(sys.modules, "pulsectl", mod)


class Osa:
    """subprocess.run stand-in for osascript."""

    def __init__(self, monkeypatch, outputs=None, returncode=0, error=None):
        self.cmds = []
        self.outputs = outputs or {}
        self.returncode = returncode
        self.error = error

        def fake_run(cmd, **kw):
            self.cmds.append(cmd)
            if self.error:
                raise self.error
            out = ""
            for key, value in self.outputs.items():
                if key in cmd:
                    out = value
            return SimpleNamespace(returncode=self.returncode, stdout=out, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)


# --------------------------------------------------------------------------- AudioChangeEntry / registry helpers


def test_audio_change_entry_durations(monkeypatch):
    import time

    monkeypatch.setattr(time, "time", lambda: 99.0)
    e = AudioChangeEntry(original_values={}, current_values={}, remaining_duration=5)
    e.update_remaining_duration(3)
    assert e.remaining_duration == 8 and e.last_update == 99.0
    e.reset_duration(2)
    assert e.remaining_duration == 2


def test_post_init_creates_class_registries(monkeypatch):
    monkeypatch.setattr(AudioControlAction, "_active_restoration_tasks", None)
    monkeypatch.setattr(AudioControlAction, "_audio_change_registry", None)
    a = audio()
    assert a.action_type == ActionType.AUDIO_CONTROL
    assert AudioControlAction._active_restoration_tasks == {}
    assert AudioControlAction._audio_change_registry == {}


class FakeTask:
    def __init__(self, done=False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


def test_cancel_all_restoration_tasks():
    running, finished = FakeTask(), FakeTask(done=True)
    AudioControlAction._active_restoration_tasks.update(a=running, b=finished)
    AudioControlAction.cancel_all_restoration_tasks()
    assert running.cancelled and not finished.cancelled
    assert AudioControlAction._active_restoration_tasks == {}
    AudioControlAction.cancel_all_restoration_tasks()  # empty: no-op


def test_cleanup_audio_change_registry():
    running = FakeTask()
    entries = {
        "a": AudioChangeEntry({}, {}, 0, restoration_task=running),
        "b": AudioChangeEntry({}, {}, 0, restoration_task=FakeTask(done=True)),
        "c": AudioChangeEntry({}, {}, 0),
    }
    AudioControlAction._audio_change_registry.update(entries)
    AudioControlAction.cleanup_audio_change_registry()
    assert running.cancelled and AudioControlAction._audio_change_registry == {}
    AudioControlAction.cleanup_audio_change_registry()


def test_add_and_remove_restoration_task(monkeypatch):
    a = audio()
    monkeypatch.setattr(AudioControlAction, "_active_restoration_tasks", None)
    a.remove_restoration_task("x")  # None registry: no-op
    t = FakeTask()
    a.add_restoration_task("x", t)
    assert AudioControlAction._active_restoration_tasks == {"x": t}
    a.remove_restoration_task("x")
    a.remove_restoration_task("missing")
    assert AudioControlAction._active_restoration_tasks == {}


def test_get_audio_source_key():
    dev = audio()
    assert dev.get_audio_source_key("app", "dev", "name") == "device:dev"
    assert dev.get_audio_source_key("app", "", "name") == "device:name"
    assert dev.get_audio_source_key("app", "", "") == "device:default"
    app = audio(control_type="application")
    assert app.get_audio_source_key("game.exe", "dev", "") == "app:game.exe"


def test_get_or_create_registry_entry(monkeypatch):
    a = audio()

    async def values(dev, out):
        out.update(volume=40.0, muted=False)
        return True

    monkeypatch.setattr(a, "_get_current_device_values", values)
    entry = run(a.get_or_create_registry_entry("device:x", "", "x", ""))
    assert entry.original_values == {"volume": 40.0, "muted": False}
    assert entry.current_values == entry.original_values and entry.current_values is not entry.original_values
    assert run(a.get_or_create_registry_entry("device:x", "", "x", "")) is entry


def test_get_or_create_registry_entry_race_and_failure(monkeypatch):
    app = audio(control_type="application")
    existing = AudioChangeEntry({}, {}, 0)

    async def values(name, out):
        # Another task fills the registry while the baseline is fetched
        AudioControlAction._audio_change_registry["app:g"] = existing
        out["volume"] = 1.0
        return True

    monkeypatch.setattr(app, "_get_current_app_values", values)
    assert run(app.get_or_create_registry_entry("app:g", "g", "", "")) is existing

    async def fail(*a):
        return False

    monkeypatch.setattr(app, "_get_current_app_values", fail)
    assert run(app.get_or_create_registry_entry("app:h", "h", "", "")) is None
    other = audio(control_type="other")
    assert run(other.get_or_create_registry_entry("x", "", "", "")) is None


def test_update_registry_entry(monkeypatch):
    a = audio()
    a.update_registry_entry("missing", {"volume": 1}, 5)  # no entry: no-op
    entry = AudioChangeEntry({}, {"volume": 10}, 3)
    AudioControlAction._audio_change_registry["k"] = entry
    a.update_registry_entry("k", {"volume": 20}, 4)
    assert entry.current_values == {"volume": 20} and entry.remaining_duration == 7
    a.update_registry_entry("k", {"muted": True}, 2, is_random=True)
    assert entry.remaining_duration == 2 and entry.current_values["muted"] is True


def test_values_differ():
    a = audio()
    assert not a._values_differ({"volume": 1}, {"volume": 1, "muted": True})
    assert a._values_differ({"volume": 1}, {"volume": 2})
    assert a._values_differ({"muted": False}, {"muted": True})
    assert not a._values_differ({}, {"volume": 3})


# --------------------------------------------------------------------------- execute (permanent changes)


def test_execute_permanent_device(monkeypatch):
    a = audio(target_device="{dev}")
    seen = []

    async def control(dev):
        seen.append(dev)
        return True

    monkeypatch.setattr(a, "_control_device", control)
    running = FakeTask()
    AudioControlAction._audio_change_registry["device:spk"] = AudioChangeEntry({}, {}, 5, restoration_task=running)
    assert run(a.execute(TRIGGER, {"dev": "spk"}))
    assert seen == ["spk"] and running.cancelled
    assert "device:spk" not in AudioControlAction._audio_change_registry


def test_execute_permanent_registry_entry_without_running_task(monkeypatch):
    a = audio(device_name="mic")

    async def control(dev):
        return dev == "mic"

    monkeypatch.setattr(a, "_control_device", control)
    AudioControlAction._audio_change_registry["device:mic"] = AudioChangeEntry({}, {}, 5, restoration_task=FakeTask(True))
    assert run(a.execute(TRIGGER, {}))
    assert AudioControlAction._audio_change_registry == {}


def test_execute_permanent_application_and_unknown(monkeypatch):
    a = audio(control_type="application", target_application="{app}")

    async def control(app):
        return app == "game.exe"

    monkeypatch.setattr(a, "_control_application_volume", control)
    assert run(a.execute(TRIGGER, {"app": "game.exe"}))
    assert not run(audio(control_type="bogus").execute(TRIGGER, {}))


def test_execute_random_sets_level_and_uses_stacking(monkeypatch):
    import random

    monkeypatch.setattr(random, "uniform", lambda lo, hi: 42.0)
    a = audio(action_mode="set_random", duration=5)
    seen = []

    async def stacking(*args):
        seen.append(args)
        return True

    monkeypatch.setattr(a, "_execute_with_stacking", stacking)
    assert run(a.execute(TRIGGER, {}))
    assert a.volume_level == 42.0 and seen == [("", "", "", "device:default", True)]


def test_execute_error_returns_false(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ca, "build_connector_placeholder_context", boom)
    assert not run(audio().execute(TRIGGER, {}))


# --------------------------------------------------------------------------- stacking


def _stack_env(monkeypatch, a, current=None, apply_ok=True):
    current = {"volume": 50.0, "muted": False} if current is None else current
    applied, scheduled = [], []

    async def get_values(dev, out):
        out.update(current)
        return True

    async def apply(values, *args):
        applied.append(dict(values))
        return apply_ok

    async def schedule(entry, key, *args):
        scheduled.append((key, entry.remaining_duration))

    monkeypatch.setattr(a, "_get_current_device_values", get_values)
    monkeypatch.setattr(a, "_apply_calculated_values", apply)
    monkeypatch.setattr(a, "_schedule_restoration_task", schedule)
    return applied, scheduled


def test_stacking_extends_duration_and_applies_changes(monkeypatch):
    a = audio(action_mode="set", volume_level=80, duration=10)
    applied, scheduled = _stack_env(monkeypatch, a)
    assert run(a.execute(TRIGGER, {}))
    entry = AudioControlAction._audio_change_registry["device:default"]
    assert applied == [{"volume": 80, "muted": False}]
    assert entry.original_values == {"volume": 50.0, "muted": False}
    assert entry.current_values["volume"] == 80 and scheduled == [("device:default", 10)]
    # Same values again: nothing applied, duration stacks
    applied.clear()
    a2 = audio(action_mode="set", volume_level=50, duration=4)
    _stack_env(monkeypatch, a2, current={"volume": 50.0, "muted": False})
    entry.current_values["volume"] = 50.0
    assert run(a2._execute_with_stacking("", "", "", "device:default", False))
    assert entry.remaining_duration == 14


def test_stacking_apply_failure(monkeypatch):
    a = audio(action_mode="set", volume_level=80, duration=10)
    _stack_env(monkeypatch, a, apply_ok=False)
    assert not run(a.execute(TRIGGER, {}))


def test_stacking_random_resets_and_cancels(monkeypatch):
    a = audio(action_mode="set_random", duration=3)
    applied, scheduled = _stack_env(monkeypatch, a)
    running = FakeTask()
    entry = AudioChangeEntry({"volume": 10}, {"volume": 10}, 30, restoration_task=running)
    AudioControlAction._audio_change_registry["device:default"] = entry
    assert run(a._execute_with_stacking("", "", "", "device:default", True))
    assert running.cancelled and entry.remaining_duration == 3 and len(applied) == 1
    entry.restoration_task = FakeTask(done=True)
    assert run(a._execute_with_stacking("", "", "", "device:default", True))


def test_stacking_random_apply_failure(monkeypatch):
    a = audio(action_mode="set_random", duration=3)
    _stack_env(monkeypatch, a, apply_ok=False)
    assert not run(a._execute_with_stacking("", "", "", "device:default", True))


def test_stacking_no_entry_or_no_values(monkeypatch):
    a = audio(duration=2)

    async def none(*args):
        return None

    monkeypatch.setattr(a, "get_or_create_registry_entry", none)
    assert not run(a._execute_with_stacking("", "", "", "k", False))

    async def entry(*args):
        return AudioChangeEntry({}, {}, 0)

    monkeypatch.setattr(a, "get_or_create_registry_entry", entry)
    monkeypatch.setattr(a, "_calculate_new_values", none)
    assert not run(a._execute_with_stacking("", "", "", "k", False))

    async def boom(*args):
        raise RuntimeError("x")

    monkeypatch.setattr(a, "get_or_create_registry_entry", boom)
    assert not run(a._execute_with_stacking("", "", "", "k", False))


@pytest.mark.parametrize(
    "mode,current,expected",
    [
        ("set", {"volume": 10}, {"volume": 70}),
        ("set_random", {"volume": 10}, {"volume": 70}),
        ("increase", {"volume": 95}, {"volume": 100.0}),
        ("increase", {}, {"volume": 60}),
        ("decrease", {"volume": 5}, {"volume": 0.0}),
        ("decrease", {}, {"volume": 40}),
        ("mute", {}, {"muted": True}),
        ("unmute", {"muted": True}, {"muted": False}),
        ("toggle_mute", {"muted": True}, {"muted": False}),
        ("toggle_mute", {}, {"muted": True}),
        ("other", {"volume": 1}, {"volume": 1}),
    ],
)
def test_calculate_new_values(monkeypatch, mode, current, expected):
    a = audio(action_mode=mode, volume_level=70, volume_step=10)

    async def values(dev, out):
        out.update(current)
        return True

    monkeypatch.setattr(a, "_get_current_device_values", values)
    assert run(a._calculate_new_values("", "", "")) == expected


def test_calculate_new_values_app_and_failures(monkeypatch):
    app = audio(control_type="application", action_mode="mute")

    async def values(name, out):
        out["volume"] = 5
        return True

    monkeypatch.setattr(app, "_get_current_app_values", values)
    assert run(app._calculate_new_values("g", "", "")) == {"volume": 5, "muted": True}
    assert run(audio(control_type="nope")._calculate_new_values("", "", "")) is None

    async def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(app, "_get_current_app_values", boom)
    assert run(app._calculate_new_values("g", "", "")) is None


def test_apply_calculated_values_routes(monkeypatch):
    dev = audio(target_device="d")
    seen = []

    async def dev_vals(values, ident):
        seen.append(("dev", ident))
        return True

    async def app_vals(values, name):
        seen.append(("app", name))
        return True

    monkeypatch.setattr(dev, "_apply_device_values", dev_vals)
    assert run(dev._apply_calculated_values({}, "", "", "name"))
    app = audio(control_type="application")
    monkeypatch.setattr(app, "_apply_app_values", app_vals)
    assert run(app._apply_calculated_values({}, "g", "", ""))
    assert seen == [("dev", "name"), ("app", "g")]
    assert not run(audio(control_type="x")._apply_calculated_values({}, "", "", ""))

    async def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(app, "_apply_app_values", boom)
    assert not run(app._apply_calculated_values({}, "g", "", ""))


# --------------------------------------------------------------------------- restoration scheduling


def test_schedule_restoration_without_loop_or_off_connector_loop(monkeypatch):
    a = audio()
    entry = AudioChangeEntry({}, {}, 1)
    import modules.connector_manager as cm

    monkeypatch.setattr(cm, "get_connector_loop", lambda: None)
    run(a._schedule_restoration_task(entry, "k", "", "", ""))
    assert entry.restoration_task is None

    # Called outside any running loop (coroutine driven by hand)
    coro = a._schedule_restoration_task(entry, "k", "", "", "")
    with pytest.raises(StopIteration):
        coro.send(None)
    assert entry.restoration_task is None


def test_schedule_restoration_error_is_logged(monkeypatch):
    a = audio()
    import modules.connector_manager as cm

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(cm, "get_connector_loop", boom)
    run(a._schedule_restoration_task(AudioChangeEntry({}, {}, 1), "k", "", "", ""))


def test_schedule_and_restore_end_to_end(monkeypatch, sleeps):
    import modules.connector_manager as cm

    a = audio(target_device="spk")
    restored = []

    async def restore(values, dev):
        restored.append((dict(values), dev))
        return True

    monkeypatch.setattr(a, "_restore_device_values", restore)

    async def go():
        monkeypatch.setattr(cm, "get_connector_loop", lambda: asyncio.get_running_loop())
        entry = AudioChangeEntry({"volume": 30}, {"volume": 80}, 2.5)
        AudioControlAction._audio_change_registry["device:spk"] = entry
        old = FakeTask()
        entry.restoration_task = old
        await a._schedule_restoration_task(entry, "device:spk", "", "spk", "")
        assert old.cancelled
        task = entry.restoration_task
        assert AudioControlAction._active_restoration_tasks[entry.task_id] is task
        await task
        return entry

    entry = run(go())
    assert restored == [({"volume": 30}, "spk")] and 2.5 in sleeps
    assert AudioControlAction._audio_change_registry == {}
    assert AudioControlAction._active_restoration_tasks == {}


def test_restore_with_stacking_app_failure_keeps_other_task_entry(monkeypatch, sleeps):
    a = audio(control_type="application")

    async def restore(values, app):
        return False

    monkeypatch.setattr(a, "_restore_app_values", restore)
    entry = AudioChangeEntry({}, {}, 1, task_id="newer")
    AudioControlAction._audio_change_registry["app:g"] = entry
    run(a._restore_with_stacking(entry, "app:g", "g", "", "", "older"))
    assert AudioControlAction._audio_change_registry["app:g"] is entry
    # Failed restore of the current task: entry is dropped in cleanup
    run(a._restore_with_stacking(entry, "app:g", "g", "", "", "newer"))
    assert "app:g" not in AudioControlAction._audio_change_registry


def test_restore_with_stacking_cancel_and_error(monkeypatch):
    a = audio()

    async def cancelled(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", cancelled)
    entry = AudioChangeEntry({}, {}, 1, task_id="t")
    AudioControlAction._active_restoration_tasks["t"] = FakeTask()
    run(a._restore_with_stacking(entry, "k", "", "", "", "t"))
    assert "t" not in AudioControlAction._active_restoration_tasks

    async def boom(seconds):
        raise RuntimeError("x")

    monkeypatch.setattr(asyncio, "sleep", boom)
    run(a._restore_with_stacking(entry, "k", "", "", "", "t"))


@pytest.mark.xfail(strict=True, reason="BUG: _execute_with_duration uses undefined name trigger_data, so it "
                                        "always fails after applying the change and never schedules a restore")
def test_execute_with_duration_schedules_restore(monkeypatch, sleeps):
    a = audio(duration=1)

    async def store(*args):
        return {"volume": 10}

    async def control(dev):
        return True

    monkeypatch.setattr(a, "_store_original_values", store)
    monkeypatch.setattr(a, "_control_device", control)
    assert run(a._execute_with_duration("", "", ""))


def test_execute_with_duration_failures(monkeypatch):
    a = audio(duration=1)

    async def none(*args):
        return None

    monkeypatch.setattr(a, "_store_original_values", none)
    assert not run(a._execute_with_duration("", "", ""))

    async def store(*args):
        return {"volume": 10}

    async def fail(*args):
        return False

    monkeypatch.setattr(a, "_store_original_values", store)
    monkeypatch.setattr(a, "_control_device", fail)
    assert not run(a._execute_with_duration("", "", ""))
    app = audio(control_type="application", duration=1)
    monkeypatch.setattr(app, "_store_original_values", store)
    monkeypatch.setattr(app, "_control_application_volume", fail)
    assert not run(app._execute_with_duration("g", "", ""))


def test_store_original_values(monkeypatch):
    a = audio()

    async def ok(dev, out):
        out["volume"] = 9
        return True

    async def fail(*a):
        return False

    monkeypatch.setattr(a, "_get_current_device_values", ok)
    assert run(a._store_original_values("", "d", "")) == {"volume": 9}
    monkeypatch.setattr(a, "_get_current_device_values", fail)
    assert run(a._store_original_values("", "d", "")) is None
    app = audio(control_type="application")
    monkeypatch.setattr(app, "_get_current_app_values", ok)
    assert run(app._store_original_values("g", "", "")) == {"volume": 9}

    async def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(app, "_get_current_app_values", boom)
    assert run(app._store_original_values("g", "", "")) is None


@pytest.mark.parametrize("control_type", ["device", "application", "other"])
def test_restore_audio_values_variants(monkeypatch, sleeps, control_type):
    a = audio(control_type=control_type, duration=2)
    seen = []

    async def restore(values, target):
        seen.append(target)
        return True

    monkeypatch.setattr(a, "_restore_device_values", restore)
    monkeypatch.setattr(a, "_restore_app_values", restore)
    run(a._restore_audio_values({}, "g", "d", "n"))
    AudioControlAction._active_restoration_tasks["tid"] = FakeTask()
    run(a._restore_audio_values_with_cleanup({}, "g", "", "n", "tid"))
    expected = {"device": ["d", "n"], "application": ["g", "g"], "other": []}[control_type]
    assert seen == expected and sleeps == [2, 2]
    assert "tid" not in AudioControlAction._active_restoration_tasks


def test_restore_audio_values_errors(monkeypatch):
    a = audio(duration=1)

    async def boom(seconds):
        raise RuntimeError("x")

    monkeypatch.setattr(asyncio, "sleep", boom)
    run(a._restore_audio_values({}, "", "", ""))
    run(a._restore_audio_values_with_cleanup({}, "", "", "", "t"))

    async def cancelled(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", cancelled)
    run(a._restore_audio_values_with_cleanup({}, "", "", "", "t"))


# --------------------------------------------------------------------------- platform dispatchers


DISPATCH = [
    ("_get_current_device_values", ("d", {}), {"windows": "_get_windows_device_values",
                                              "darwin": "_get_macos_device_values",
                                              "linux": "_get_linux_device_values"}),
    ("_restore_device_values", ({}, "d"), {"windows": "_restore_windows_device_values",
                                          "darwin": "_restore_macos_device_values",
                                          "linux": "_restore_linux_device_values"}),
    ("_apply_device_values", ({}, "d"), {"windows": "_apply_windows_device_values",
                                        "darwin": "_apply_macos_device_values",
                                        "linux": "_apply_linux_device_values"}),
    ("_control_device", ("d",), {"windows": "_control_windows_device", "darwin": "_control_macos_device",
                                 "linux": "_control_linux_device"}),
    ("_control_system_volume", (), {"windows": "_control_windows_volume", "darwin": "_control_macos_volume",
                                    "linux": "_control_linux_volume"}),
    ("_control_microphone", (), {"windows": "_control_windows_microphone",
                                 "darwin": "_control_macos_microphone",
                                 "linux": "_control_linux_microphone"}),
    ("_get_current_app_values", ("g", {}), {"windows": "_get_windows_app_values"}),
    ("_restore_app_values", ({}, "g"), {"windows": "_restore_windows_app_values"}),
    ("_apply_app_values", ({}, "g"), {"windows": "_apply_windows_app_values"}),
    ("_control_application_volume", ("g",), {"windows": "_control_windows_app_volume"}),
]


@pytest.mark.parametrize("method,args,routes", DISPATCH, ids=[d[0] for d in DISPATCH])
def test_platform_dispatch(monkeypatch, method, args, routes):
    a = audio()
    for system in ("Windows", "Darwin", "Linux", "Plan9"):
        on(monkeypatch, system)
        target = routes.get(system.lower())
        called = []

        async def impl(*a, _name=target):
            called.append(_name)
            return True

        if target:
            monkeypatch.setattr(a, target, impl)
            assert run(getattr(a, method)(*args)) is True
            assert called == [target]
        else:
            assert run(getattr(a, method)(*args)) is False

    def boom():
        raise RuntimeError("no platform")

    monkeypatch.setattr(platform, "system", boom)
    assert run(getattr(a, method)(*args)) is False


# --------------------------------------------------------------------------- Windows (fake pycaw)


def test_windows_device_value_helpers(monkeypatch):
    pc = Pycaw(monkeypatch)
    pc.speaker.level, pc.speaker.muted = 0.25, 1
    a = audio()
    out = {}
    assert run(a._get_windows_device_values("", out)) and out == {"volume": 25.0, "muted": 1}
    assert run(a._restore_windows_device_values({"volume": 60, "muted": 0}, ""))
    assert pc.speaker.calls == [("level", 0.6), ("mute", 0)]
    pc.speaker.calls.clear()
    assert run(a._apply_windows_device_values({"volume": 10}, ""))
    assert run(a._apply_windows_device_values({"muted": True}, ""))
    assert run(a._restore_windows_device_values({}, ""))
    assert pc.speaker.calls == [("level", 0.1), ("mute", True)]
    pc.error = OSError("com")
    assert not run(a._get_windows_device_values("", {}))
    assert not run(a._restore_windows_device_values({}, ""))
    assert not run(a._apply_windows_device_values({}, ""))


def test_windows_app_value_helpers(monkeypatch):
    vol = Volume(level=0.4, muted=0)
    pc = Pycaw(monkeypatch, sessions=[session(None, Volume()), session("Other.exe", Volume()),
                                      session("Game.exe", vol)])
    a = audio(control_type="application")
    out = {}
    assert run(a._get_windows_app_values("game.exe", out)) and out == {"volume": 40.0, "muted": 0}
    assert not run(a._get_windows_app_values("missing.exe", {}))
    assert run(a._restore_windows_app_values({"volume": 50, "muted": 1}, "GAME.EXE"))
    assert not run(a._restore_windows_app_values({}, "missing.exe"))
    assert run(a._apply_windows_app_values({"volume": 20, "muted": 0}, "game.exe"))
    assert run(a._apply_windows_app_values({}, "game.exe"))
    assert run(a._restore_windows_app_values({}, "game.exe"))
    assert not run(a._apply_windows_app_values({}, "missing.exe"))
    assert vol.calls == [("level", 0.5), ("mute", 1), ("level", 0.2), ("mute", 0)]
    pc.error = OSError("x")
    assert not run(a._get_windows_app_values("g", {}))
    assert not run(a._restore_windows_app_values({}, "g"))
    assert not run(a._apply_windows_app_values({}, "g"))


WIN_MODES = [
    ("set", 0.5, 0, [("level", 0.7)]),
    ("set_random", 0.5, 0, [("level", 0.7)]),
    ("increase", 0.95, 0, [("level", 1.0)]),
    ("decrease", 0.05, 0, [("level", 0.0)]),
    ("mute", 0.5, 0, [("mute", 1)]),
    ("unmute", 0.5, 1, [("mute", 0)]),
    ("toggle_mute", 0.5, 1, [("mute", 0)]),
    ("toggle_mute", 0.5, 0, [("mute", 1)]),
    ("other", 0.5, 0, []),
]


@pytest.mark.parametrize("mode,level,muted,expected", WIN_MODES)
def test_control_windows_device_and_volume(monkeypatch, mode, level, muted, expected):
    pc = Pycaw(monkeypatch)
    for method in ("_control_windows_device", "_control_windows_volume"):
        pc.speaker = Volume(level=level, muted=muted)
        a = audio(action_mode=mode, volume_level=70, volume_step=10)
        args = ("",) if method == "_control_windows_device" else ()
        assert run(getattr(a, method)(*args))
        assert [(k, round(v, 4)) for k, v in pc.speaker.calls] == expected


def test_control_windows_import_errors(monkeypatch, slot):
    monkeypatch.setitem(sys.modules, "pycaw", None)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", None)
    a = audio()
    assert not run(a._control_windows_device(""))
    assert slot["reason"] == "audio control isn't available on this system"
    assert not run(a._control_windows_volume())
    assert not run(a._control_windows_microphone())
    assert not run(a._control_windows_app_volume("g"))


def test_control_windows_device_permission_and_other_errors(monkeypatch, slot):
    pc = Pycaw(monkeypatch, error=OSError("Access is denied"))
    a = audio()
    assert not run(a._control_windows_device(""))
    assert slot["reason"] == "Windows didn't allow the audio change"
    slot.clear()
    pc.error = OSError("boom")
    assert not run(a._control_windows_device(""))
    assert "reason" not in slot
    assert not run(a._control_windows_volume())
    assert not run(a._control_windows_microphone())
    assert not run(a._control_windows_app_volume("g"))


@pytest.mark.parametrize(
    "mode,muted,expected",
    [("mute", 0, [("mute", 1)]), ("unmute", 1, [("mute", 0)]), ("toggle_mute", 1, [("mute", 0)]),
     ("toggle_mute", 0, [("mute", 1)]), ("set", 0, [("level", 0.7)]), ("set_random", 0, [("level", 0.7)]),
     ("increase", 0, [])],
)
def test_control_windows_microphone(monkeypatch, mode, muted, expected):
    pc = Pycaw(monkeypatch)
    pc.mic = Volume(muted=muted)
    assert run(audio(action_mode=mode, volume_level=70)._control_windows_microphone())
    assert pc.mic.calls == expected


@pytest.mark.parametrize(
    "mode,muted,expected",
    [("set", 0, [("level", 0.7)]), ("set_random", 0, [("level", 0.7)]), ("mute", 0, [("mute", 1)]),
     ("unmute", 1, [("mute", 0)]), ("toggle_mute", 1, [("mute", 0)]), ("toggle_mute", 0, [("mute", 1)]),
     ("increase", 0, [])],
)
def test_control_windows_app_volume(monkeypatch, mode, muted, expected):
    vol = Volume(muted=muted)
    Pycaw(monkeypatch, sessions=[session(None, Volume()), session("game.exe", vol)])
    assert run(audio(action_mode=mode, volume_level=70)._control_windows_app_volume("Game.exe"))
    assert vol.calls == expected


def test_control_windows_app_volume_not_found(monkeypatch):
    Pycaw(monkeypatch, sessions=[session("other.exe", Volume())])
    assert not run(audio()._control_windows_app_volume("game.exe"))


# --------------------------------------------------------------------------- macOS (fake osascript)


def test_macos_get_device_values(monkeypatch):
    osa = Osa(monkeypatch, outputs={"output volume of": "35\n", "output muted of": "true\n"})
    out = {}
    assert run(audio()._get_macos_device_values("", out))
    assert out == {"volume": 35.0, "muted": True} and len(osa.cmds) == 2
    osa.returncode = 1
    osa.outputs = {"output muted of": "false"}
    out = {}
    assert run(audio()._get_macos_device_values("", out))
    assert out == {"volume": 50.0, "muted": False}
    osa.error = OSError("x")
    assert not run(audio()._get_macos_device_values("", {}))


def test_macos_apply_and_restore(monkeypatch):
    osa = Osa(monkeypatch)
    a = audio()
    assert run(a._apply_macos_device_values({"volume": 33.9, "muted": True}, ""))
    assert run(a._restore_macos_device_values({"volume": 12, "muted": False}, ""))
    assert run(a._apply_macos_device_values({}, ""))
    assert osa.cmds == [
        "osascript -e 'set volume output volume 33'",
        "osascript -e 'set volume output muted true'",
        "osascript -e 'set volume output volume 12'",
        "osascript -e 'set volume output muted false'",
    ]
    osa.error = OSError("x")
    assert not run(a._apply_macos_device_values({"volume": 1}, ""))
    assert not run(a._restore_macos_device_values({"volume": 1}, ""))


MAC_MODES = [
    ("set", {}, ["set volume output volume 70"]),
    ("set_random", {}, ["set volume output volume 70"]),
    ("increase", {"output volume of": "95"}, ["output volume of", "set volume output volume 100"]),
    ("decrease", {"output volume of": "5"}, ["output volume of", "set volume output volume 0"]),
    ("mute", {}, ["set volume output muted true"]),
    ("unmute", {}, ["set volume output muted false"]),
    ("toggle_mute", {"output muted of": "true"}, ["output muted of", "set volume output muted False"]),
    ("toggle_mute", {"output muted of": "false"}, ["output muted of", "set volume output muted True"]),
    ("other", {}, []),
]


@pytest.mark.parametrize("mode,outputs,expected", MAC_MODES)
def test_control_macos_device_and_volume(monkeypatch, mode, outputs, expected):
    for method, args in (("_control_macos_device", ("",)), ("_control_macos_volume", ())):
        osa = Osa(monkeypatch, outputs=outputs)
        assert run(getattr(audio(action_mode=mode, volume_level=70.6, volume_step=10), method)(*args))
        assert len(osa.cmds) == len(expected)
        for cmd, part in zip(osa.cmds, expected):
            assert part in cmd


def test_control_macos_device_errors(monkeypatch, slot):
    osa = Osa(monkeypatch, error=PermissionError("Operation not permitted: denied"))
    assert not run(audio()._control_macos_device(""))
    assert slot["reason"] == "macOS didn't allow the audio change"
    slot.clear()
    osa.error = OSError("broken")
    assert not run(audio()._control_macos_device(""))
    assert "reason" not in slot
    assert not run(audio()._control_macos_volume())
    assert not run(audio(action_mode="set")._control_macos_microphone())


@pytest.mark.parametrize(
    "mode,outputs,expected",
    [("mute", {}, ["set volume input volume 0"]),
     ("unmute", {}, ["set volume input volume 50"]),
     ("toggle_mute", {"input volume of": "0"}, ["input volume of", "set volume input volume 50"]),
     ("toggle_mute", {"input volume of": "40"}, ["input volume of", "set volume input volume 0"]),
     ("set", {}, ["set volume input volume 70"]),
     ("set_random", {}, ["set volume input volume 70"]),
     ("increase", {}, [])],
)
def test_control_macos_microphone(monkeypatch, mode, outputs, expected):
    osa = Osa(monkeypatch, outputs=outputs)
    assert run(audio(action_mode=mode, volume_level=70)._control_macos_microphone())
    assert len(osa.cmds) == len(expected)
    for cmd, part in zip(osa.cmds, expected):
        assert part in cmd


# --------------------------------------------------------------------------- Linux (fake pulsectl)


def test_linux_value_helpers(monkeypatch):
    pulse = Pulse(monkeypatch, sinks=[Sink("s0", vol=0.4, mute=True)])
    a = audio()
    out = {}
    assert run(a._get_linux_device_values("", out)) and out == {"volume": 40.0, "muted": True}
    assert run(a._restore_linux_device_values({"volume": 50, "muted": False}, ""))
    assert run(a._apply_linux_device_values({"volume": 20, "muted": True}, ""))
    assert run(a._apply_linux_device_values({}, ""))
    assert run(a._restore_linux_device_values({}, ""))
    calls = [c for c in pulse.calls if c[0] != "open"]
    assert calls == [("vol", "s0", 0.5), ("mute", "s0", False), ("vol", "s0", 0.2), ("mute", "s0", True)]
    pulse.sinks = []
    assert not run(a._get_linux_device_values("", {}))
    assert not run(a._restore_linux_device_values({}, ""))
    assert not run(a._apply_linux_device_values({}, ""))
    pulse.error = OSError("x")
    assert not run(a._get_linux_device_values("", {}))
    assert not run(a._restore_linux_device_values({}, ""))
    assert not run(a._apply_linux_device_values({}, ""))


LINUX_MODES = [
    ("set", 0.5, False, [("vol", 0.7)]),
    ("set_random", 0.5, False, [("vol", 0.7)]),
    ("increase", 0.95, False, [("vol", 1.0)]),
    ("decrease", 0.05, False, [("vol", 0.0)]),
    ("mute", 0.5, False, [("mute", True)]),
    ("unmute", 0.5, True, [("mute", False)]),
    ("toggle_mute", 0.5, True, [("mute", False)]),
    ("other", 0.5, False, []),
]


@pytest.mark.parametrize("mode,vol,muted,expected", LINUX_MODES)
def test_control_linux_device_and_volume(monkeypatch, mode, vol, muted, expected):
    for method, args in (("_control_linux_device", ("",)), ("_control_linux_volume", ())):
        pulse = Pulse(monkeypatch, sinks=[Sink("s0", vol=vol, mute=muted)])
        assert run(getattr(audio(action_mode=mode, volume_level=70, volume_step=10), method)(*args))
        assert [(c[0], c[2]) for c in pulse.calls if c[0] != "open"] == expected


@pytest.mark.parametrize(
    "ident,expected_name",
    [("sink:1", "s1"), ("sink:9", "s0"), ("sink:x", "s0"), ("source:0", "m0"), ("source:7", "s0"),
     ("source:y", "s0"), ("", "s0"), ("other", "s0")],
)
def test_control_linux_device_identifier(monkeypatch, ident, expected_name):
    pulse = Pulse(monkeypatch, sinks=[Sink("s0"), Sink("s1")], sources=[Sink("m0")])
    assert run(audio(action_mode="mute")._control_linux_device(ident))
    assert pulse.calls[-1] == ("mute", expected_name, True)


@pytest.mark.parametrize("ident", ["", "sink:3", "source:3"])
def test_control_linux_device_no_sink(monkeypatch, ident):
    Pulse(monkeypatch, sinks=[], sources=[])
    assert not run(audio()._control_linux_device(ident))


def test_control_linux_errors(monkeypatch, slot):
    monkeypatch.setitem(sys.modules, "pulsectl", None)
    a = audio()
    assert not run(a._control_linux_device(""))
    assert slot["reason"] == "audio control isn't available on this system"
    assert not run(a._control_linux_volume())
    assert not run(a._control_linux_microphone())
    pulse = Pulse(monkeypatch, error=PermissionError("access denied"))
    slot.clear()
    assert not run(a._control_linux_device(""))
    assert slot["reason"] == "Linux didn't allow the audio change"
    pulse.error = OSError("other")
    slot.clear()
    assert not run(a._control_linux_device(""))
    assert "reason" not in slot
    assert not run(a._control_linux_volume())
    assert not run(a._control_linux_microphone())


def test_control_linux_volume_no_sinks(monkeypatch):
    Pulse(monkeypatch, sinks=[])
    assert not run(audio()._control_linux_volume())


@pytest.mark.parametrize(
    "mode,expected",
    [("mute", [("mute", "mic", True)]), ("unmute", [("mute", "mic", False)]),
     ("toggle_mute", [("mute", "mic", False)]), ("set", [("vol", "mic", 0.7)]),
     ("set_random", [("vol", "mic", 0.7)]), ("increase", [])],
)
def test_control_linux_microphone(monkeypatch, mode, expected):
    pulse = Pulse(monkeypatch, sources=[Sink("out.monitor"), Sink("mic", mute=True)])
    assert run(audio(action_mode=mode, volume_level=70)._control_linux_microphone())
    assert [c for c in pulse.calls if c[0] != "open"] == expected


def test_control_linux_microphone_none_found(monkeypatch):
    Pulse(monkeypatch, sources=[])
    assert not run(audio()._control_linux_microphone())
    Pulse(monkeypatch, sources=[Sink("a.monitor")])
    assert not run(audio()._control_linux_microphone())


def test_replace_placeholders_none():
    assert audio()._replace_placeholders(None, {}) == ""
    assert audio()._replace_placeholders("{x}", {"x": "y"}) == "y"

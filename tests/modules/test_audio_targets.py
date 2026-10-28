# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/audio_targets.py: per-platform audio device / app listing and permission checks.

Every OS facility (platform, subprocess, pycaw/comtypes, pulsectl, psutil, ctypes.windll, pwd/grp)
is replaced with a fake, so nothing is enumerated, launched or played for real.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ctypes
import platform
import subprocess

import pytest

from modules import audio_targets as at


# --------------------------------------------------------------------------- helpers


def _done(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class _RunRecorder:
    """Fake subprocess.run: maps argv[0] to a result (or exception) and records calls."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        res = self.results.get(argv[0])
        if isinstance(res, BaseException) or (isinstance(res, type) and issubclass(res, BaseException)):
            raise res
        if res is None:
            raise FileNotFoundError(argv[0])
        return res


@pytest.fixture
def run(monkeypatch):
    def install(results):
        rec = _RunRecorder(results)
        monkeypatch.setattr(subprocess, "run", rec)
        return rec

    return install


def _set_system(monkeypatch, name):
    monkeypatch.setattr(platform, "system", lambda: name)


# --------------------------------------------------------------------------- dispatchers


@pytest.mark.parametrize(
    "system, target",
    [("Windows", "get_windows_audio_devices"), ("Darwin", "get_macos_audio_devices"), ("Linux", "get_linux_audio_devices")],
)
def test_get_available_audio_devices_dispatches_by_platform(monkeypatch, system, target):
    _set_system(monkeypatch, system)
    monkeypatch.setattr(at, target, lambda: {"x": system})
    assert at.get_available_audio_devices() == {"x": system}


def test_get_available_audio_devices_unknown_platform(monkeypatch):
    _set_system(monkeypatch, "Plan9")
    assert at.get_available_audio_devices() == {"": "Default Device"}


def test_get_available_audio_devices_error_falls_back(monkeypatch):
    _set_system(monkeypatch, "Windows")

    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(at, "get_windows_audio_devices", boom)
    assert at.get_available_audio_devices() == {"": "Default Device"}


@pytest.mark.parametrize(
    "system, target",
    [("Windows", "get_windows_applications"), ("Darwin", "get_macos_applications"), ("Linux", "get_linux_applications")],
)
def test_get_available_applications_dispatches_by_platform(monkeypatch, system, target):
    _set_system(monkeypatch, system)
    monkeypatch.setattr(at, target, lambda: {"app": system})
    assert at.get_available_applications() == {"app": system}


def test_get_available_applications_unknown_and_error(monkeypatch):
    _set_system(monkeypatch, "BeOS")
    assert at.get_available_applications() == {"default": "Default Application"}

    def boom():
        raise RuntimeError("platform exploded")

    monkeypatch.setattr(platform, "system", boom)
    assert at.get_available_applications() == {"default": "Default Application"}


@pytest.mark.parametrize(
    "system, target",
    [("Darwin", "check_macos_audio_permissions"), ("Windows", "check_windows_audio_permissions"),
     ("Linux", "check_linux_audio_permissions")],
)
def test_check_audio_permissions_dispatches(monkeypatch, system, target):
    _set_system(monkeypatch, system)
    monkeypatch.setattr(at, target, lambda: {"has_permissions": system})
    assert at.check_audio_permissions() == {"has_permissions": system}


def test_check_audio_permissions_unknown_and_error(monkeypatch):
    _set_system(monkeypatch, "Haiku")
    assert at.check_audio_permissions() == {
        "has_permissions": True, "message": "Unknown operating system", "can_request": False,
    }

    def boom():
        raise RuntimeError("bad")

    monkeypatch.setattr(platform, "system", boom)
    res = at.check_audio_permissions()
    assert res["has_permissions"] is False
    assert res["can_request"] is False
    assert "bad" in res["message"]


@pytest.mark.parametrize(
    "system, target",
    [("Darwin", "request_macos_audio_permissions"), ("Windows", "request_windows_audio_permissions"),
     ("Linux", "request_linux_audio_permissions")],
)
def test_request_audio_permissions_dispatches(monkeypatch, system, target):
    _set_system(monkeypatch, system)
    monkeypatch.setattr(at, target, lambda: "called-" + system)
    assert at.request_audio_permissions() == "called-" + system


def test_request_audio_permissions_unknown_and_error(monkeypatch):
    _set_system(monkeypatch, "Amiga")
    assert at.request_audio_permissions() is False

    def boom():
        raise RuntimeError("bad")

    monkeypatch.setattr(platform, "system", boom)
    assert at.request_audio_permissions() is False


# --------------------------------------------------------------------------- Windows devices (pycaw)


class _Dev:
    def __init__(self, id, name, state="active", name_error=None):
        self.id = id
        self._name = name
        self.state = state
        self._name_error = name_error

    @property
    def FriendlyName(self):
        if self._name_error is not None:
            raise self._name_error
        return self._name


class _BadStateDev:
    id = "bad"

    @property
    def state(self):
        raise RuntimeError("state read failed")


class _ComErrorDev:
    id = "com"

    @property
    def state(self):
        raise RuntimeError("COMError -2147023728")


def _install_pycaw(monkeypatch, devices=None, get_all_error=None):
    comtypes = types.ModuleType("comtypes")
    comtypes.CLSCTX_ALL = 23
    constants = types.ModuleType("pycaw.constants")
    constants.AudioDeviceState = SimpleNamespace(Active="active", Disabled="disabled")
    pycaw_pycaw = types.ModuleType("pycaw.pycaw")

    class AudioUtilities:
        @staticmethod
        def GetAllDevices():
            if get_all_error is not None:
                raise get_all_error
            return list(devices or [])

    pycaw_pycaw.AudioUtilities = AudioUtilities
    pycaw = types.ModuleType("pycaw")
    pycaw.constants = constants
    pycaw.pycaw = pycaw_pycaw
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "pycaw", pycaw)
    monkeypatch.setitem(sys.modules, "pycaw.constants", constants)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw_pycaw)


def test_windows_devices_classifies_dedupes_and_skips(monkeypatch, caplog):
    _install_pycaw(monkeypatch, devices=[
        _Dev("{1}", "Speakers (Realtek)"),
        _Dev("{2}", "Speakers (Realtek)"),  # duplicate name -> skipped
        _Dev("{3}", "Headphones (USB)"),
        _Dev("{4}", "LG HDMI Display"),
        _Dev("{5}", "Microphone Array"),
        _Dev("{6}", "Line-In (Behringer)"),
        _Dev("{7}", "Disabled Speaker", state="disabled"),  # not active -> skipped
        _Dev("{8}", ""),  # empty name -> skipped
        _Dev("{9}", "Headset", name_error=OSError("no prop")),  # name read fails -> skipped
        _Dev("{10}", "Virtual Cable"),  # neither playback nor recording keyword -> not listed
        _BadStateDev(),  # generic error -> warning logged, skipped
        _ComErrorDev(),  # COMError -> skipped silently
    ])
    with caplog.at_level("WARNING", logger=at.logger.name):
        devices = at.get_windows_audio_devices()
    assert devices == {
        "playback:{1}": "[OUT] Speakers (Realtek)",
        "playback:{3}": "[OUT] Headphones (USB)",
        "playback:{4}": "[OUT] LG HDMI Display",
        "recording:{5}": "[MIC] Microphone Array",
        "recording:{6}": "[MIC] Line-In (Behringer)",
    }
    msgs = [r.getMessage() for r in caplog.records]
    assert any("state read failed" in m for m in msgs)
    assert not any("COMError" in m for m in msgs)


def test_windows_devices_none_found_falls_back(monkeypatch):
    _install_pycaw(monkeypatch, devices=[_Dev("{1}", "Virtual Cable")])
    assert at.get_windows_audio_devices() == {"default": "Default Device"}


def test_windows_devices_get_all_error_is_logged_and_falls_back(monkeypatch, caplog):
    _install_pycaw(monkeypatch, get_all_error=RuntimeError("enum failed"))
    with caplog.at_level("WARNING", logger=at.logger.name):
        assert at.get_windows_audio_devices() == {"default": "Default Device"}
    assert any("enum failed" in r.getMessage() for r in caplog.records)


def test_windows_devices_without_pycaw(monkeypatch):
    monkeypatch.setitem(sys.modules, "comtypes", None)  # import -> ImportError
    assert at.get_windows_audio_devices() == {"default": "Default Device (pycaw not installed)"}


def test_windows_devices_unexpected_error(monkeypatch):
    _install_pycaw(monkeypatch, devices=[])
    import warnings

    def boom(*a, **k):
        raise RuntimeError("warnings broke")

    monkeypatch.setattr(warnings, "catch_warnings", boom)
    assert at.get_windows_audio_devices() == {"default": "Default Device (enumeration failed)"}


# --------------------------------------------------------------------------- macOS devices


PROFILER_MIXED = """Audio:

    Devices:

        Studio Headphones:

          Manufacturer: Sony
          Output Channels: 2
          Transport: USB
          Output Source:

        Focusrite:

          Default Input Device: Yes
          Input Channels: 2
          Output Channels: 2
          Transport: USB

        LG Display:

          Output Channels: 2
          Transport: DisplayPort

        Loopback:

          Default Output Device: Yes
          Output Channels: 2
          Transport: Virtual

        Aggregate:

          Manufacturer: Apple Inc.
          Transport: Aggregate
"""


def test_macos_devices_parses_profiler_blocks(run):
    rec = run({"system_profiler": _done(0, PROFILER_MIXED)})
    devices = at.get_macos_audio_devices()
    assert devices == {
        "output:Studio Headphones": "[HEAD] Studio Headphones",  # blank "Output Source:" stays a property
        "output:Focusrite": "[OUT] Focusrite",
        "input:Focusrite": "[MIC] Focusrite (Default)",  # in+out interface listed both ways
        "output:LG Display": "[DSP] LG Display",
        "output:Loopback": "[OUT] Loopback (Default)",
        # "Aggregate" has no channels: skipped, like the "Audio:"/"Devices:" headers
    }
    argv, kwargs = rec.calls[0]
    assert argv == ["system_profiler", "SPAudioDataType"]
    assert kwargs["timeout"] == 10 and kwargs["capture_output"] is True


def test_macos_devices_profiler_failure_uses_fallback(run, monkeypatch):
    run({"system_profiler": _done(1, "")})
    monkeypatch.setattr(at, "get_macos_audio_devices_fallback", lambda: {"fb": "fallback"})
    assert at.get_macos_audio_devices() == {"fb": "fallback"}


def test_macos_devices_nothing_parsed_uses_fallback(run, monkeypatch):
    run({"system_profiler": _done(0, "Audio:\n\n    nothing here\n")})
    monkeypatch.setattr(at, "get_macos_audio_devices_fallback", lambda: {"fb": "fallback"})
    assert at.get_macos_audio_devices() == {"fb": "fallback"}


def test_macos_devices_exception_uses_fallback(run, monkeypatch):
    run({"system_profiler": subprocess.TimeoutExpired("system_profiler", 10)})
    monkeypatch.setattr(at, "get_macos_audio_devices_fallback", lambda: {"fb": "fallback"})
    assert at.get_macos_audio_devices() == {"fb": "fallback"}


REAL_PROFILER = """Audio:

    Devices:

        MacBook Air Microphone:

          Default Input Device: Yes
          Input Channels: 1
          Manufacturer: Apple Inc.
          Transport: Built-in
          Input Source: MacBook Air Microphone

        MacBook Air Speakers:

          Default Output Device: Yes
          Default System Output Device: Yes
          Manufacturer: Apple Inc.
          Output Channels: 2
          Transport: Built-in
          Output Source: MacBook Air Speakers
"""


def test_macos_devices_parses_real_system_profiler_format(run, monkeypatch):
    run({"system_profiler": _done(0, REAL_PROFILER)})
    monkeypatch.setattr(at, "get_macos_audio_devices_fallback", lambda: {"default": "Default Device"})
    assert at.get_macos_audio_devices() == {
        "input:MacBook Air Microphone": "[MIC] MacBook Air Microphone (Default)",
        "output:MacBook Air Speakers": "[SPK] MacBook Air Speakers (Default)",
    }


def test_macos_fallback_combines_both_tools(run):
    rec = run({
        "audiodevice": _done(0, "Device: Built-in Output\nnoise\nDevice: USB DAC\n"),
        "SwitchAudioSource": _done(0, "MacBook Speakers (output)\n(header)\n\n   \nAirPods\n"),
    })
    devices = at.get_macos_audio_devices_fallback()
    assert devices == {
        "default": "Default Device",
        "Built-in Output": "Built-in Output",
        "USB DAC": "USB DAC",
        "MacBook Speakers": "MacBook Speakers",
        "AirPods": "AirPods",
    }
    assert [c[0][0] for c in rec.calls] == ["audiodevice", "SwitchAudioSource"]


def test_macos_fallback_line_with_empty_name_before_paren(run):
    run({"audiodevice": _done(1, ""), "SwitchAudioSource": _done(0, " x(y)\n")})
    # " x(y)" -> "x"; a line like "  (foo)" starts with "(" after strip and is skipped
    assert at.get_macos_audio_devices_fallback() == {"default": "Default Device", "x": "x"}


def test_macos_fallback_tools_missing_or_failing(run):
    run({"audiodevice": FileNotFoundError("audiodevice"),
         "SwitchAudioSource": subprocess.SubprocessError("boom")})
    assert at.get_macos_audio_devices_fallback() == {"default": "Default Device"}


def test_macos_fallback_nonzero_exit_codes(run):
    run({"audiodevice": _done(2, "Device: X"), "SwitchAudioSource": _done(3, "Y")})
    assert at.get_macos_audio_devices_fallback() == {"default": "Default Device"}


def test_macos_fallback_unexpected_error(run):
    run({"audiodevice": RuntimeError("weird")})  # not caught by the inner handler
    assert at.get_macos_audio_devices_fallback() == {"default": "Default Device"}


# --------------------------------------------------------------------------- Linux devices (pulsectl)


def _install_pulsectl(monkeypatch, sinks=(), sources=(), error=None):
    mod = types.ModuleType("pulsectl")
    seen = {}

    class Pulse:
        def __init__(self, name):
            seen["name"] = name
            if error is not None:
                raise error

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            seen["closed"] = True
            return False

        def sink_list(self):
            return list(sinks)

        def source_list(self):
            return list(sources)

    mod.Pulse = Pulse
    monkeypatch.setitem(sys.modules, "pulsectl", mod)
    return seen


def _sink(index, desc):
    return SimpleNamespace(index=index, description=desc, name=f"sink{index}")


def _source(index, desc, name=None):
    return SimpleNamespace(index=index, description=desc, name=name or f"src{index}")


def test_linux_devices_icons_and_monitor_skip(monkeypatch):
    seen = _install_pulsectl(
        monkeypatch,
        sinks=[_sink(0, "USB Headphones"), _sink(1, "Headset Audio"), _sink(2, "Built-in Speaker"),
               _sink(3, "HDMI Output"), _sink(4, "External Display"), _sink(5, "Line Out"), _sink(6, "")],
        sources=[_source(10, "Built-in Microphone"), _source(11, "USB Mic"), _source(12, "Webcam Audio"),
                 _source(13, "Line In"), _source(14, None), _source(15, "Monitor of X", name="x.monitor")],
    )
    devices = at.get_linux_audio_devices()
    assert devices == {
        "sink:0": "[HEAD] USB Headphones",
        "sink:1": "[HEAD] Headset Audio",
        "sink:2": "[SPK] Built-in Speaker",
        "sink:3": "[DSP] HDMI Output",
        "sink:4": "[DSP] External Display",
        "sink:5": "[OUT] Line Out",
        "sink:6": "[OUT] Sink 6",
        "source:10": "[MIC] Built-in Microphone",
        "source:11": "[MIC] USB Mic",
        "source:12": "[CAM] Webcam Audio",
        "source:13": "[MIC] Line In",
        "source:14": "[MIC] Source 14",
    }
    assert seen == {"name": "device-enumeration", "closed": True}


def test_linux_devices_empty_and_errors(monkeypatch):
    _install_pulsectl(monkeypatch)
    assert at.get_linux_audio_devices() == {"default": "Default Device"}

    _install_pulsectl(monkeypatch, error=RuntimeError("no server"))
    assert at.get_linux_audio_devices() == {"default": "Default Device (enumeration failed)"}

    monkeypatch.setitem(sys.modules, "pulsectl", None)
    assert at.get_linux_audio_devices() == {"default": "Default Device (pulsectl not installed)"}


# --------------------------------------------------------------------------- applications (psutil)


class _Proc:
    def __init__(self, name, error=None):
        self._name = name
        self._error = error

    @property
    def info(self):
        if self._error is not None:
            raise self._error
        return {"pid": 1, "name": self._name}


def _install_psutil(monkeypatch, procs=None, iter_error=None):
    import psutil as real

    mod = types.ModuleType("psutil")
    mod.NoSuchProcess = real.NoSuchProcess
    mod.AccessDenied = real.AccessDenied
    calls = []

    def process_iter(attrs):
        calls.append(attrs)
        if iter_error is not None:
            raise iter_error
        return iter(procs or [])

    mod.process_iter = process_iter
    monkeypatch.setitem(sys.modules, "psutil", mod)
    return mod, calls


@pytest.mark.parametrize(
    "func, system_names, kept",
    [
        ("get_windows_applications", ["svchost.exe", "System", "winlogon.exe", "csrss.exe", "smss.exe"], "obs64.exe"),
        ("get_macos_applications", ["kernel_task", "launchd", "SystemUIServer", "WindowServer"], "OBS"),
        ("get_linux_applications", ["systemd", "init", "kthreadd", "ksoftirqd/0", "migration/1"], "obs"),
    ],
)
def test_applications_filter_system_processes(monkeypatch, func, system_names, kept):
    import psutil as real

    procs = [_Proc(n) for n in system_names] + [
        _Proc(kept), _Proc(kept), _Proc(None), _Proc(""),
        _Proc("gone", error=real.NoSuchProcess(1)), _Proc("locked", error=real.AccessDenied(2)),
        _Proc("Zed"), _Proc("Alpha"),
    ]
    _, calls = _install_psutil(monkeypatch, procs)
    apps = getattr(at, func)()
    assert apps == {"Alpha": "Alpha", kept: kept, "Zed": "Zed"}
    assert list(apps) == sorted(apps)
    assert calls == [["pid", "name"]]


@pytest.mark.parametrize("func", ["get_windows_applications", "get_macos_applications", "get_linux_applications"])
def test_applications_limited_to_50_sorted(monkeypatch, func):
    names = [f"app{i:03d}" for i in range(80, 0, -1)]
    _install_psutil(monkeypatch, [_Proc(n) for n in names])
    apps = getattr(at, func)()
    assert len(apps) == 50
    assert list(apps) == [f"app{i:03d}" for i in range(1, 51)]


@pytest.mark.parametrize("func", ["get_windows_applications", "get_macos_applications", "get_linux_applications"])
def test_applications_errors(monkeypatch, func):
    _install_psutil(monkeypatch, iter_error=RuntimeError("ps failed"))
    assert getattr(at, func)() == {"default": "Default Application"}

    monkeypatch.setitem(sys.modules, "psutil", None)
    assert getattr(at, func)() == {"default": "Default Application"}


# --------------------------------------------------------------------------- permission checks


def test_check_macos_permissions(run):
    rec = run({"system_profiler": _done(0, "Microphone:\n  Built-in")})
    res = at.check_macos_audio_permissions()
    assert res == {"has_permissions": True, "message": "Microphone access available", "can_request": False}
    assert rec.calls[0][0] == ["system_profiler", "SPMicrophoneDataType"]

    run({"system_profiler": _done(0, "No microphone found")})
    res = at.check_macos_audio_permissions()
    assert res["has_permissions"] is False and "System Preferences" in res["message"]

    run({"system_profiler": _done(1, "")})
    assert at.check_macos_audio_permissions()["has_permissions"] is False

    run({"system_profiler": OSError("gone")})
    assert at.check_macos_audio_permissions() == {
        "has_permissions": False, "message": "Unable to check microphone permissions", "can_request": False,
    }


def _install_windll(monkeypatch, admin_value=None, error=None):
    def is_admin():
        if error is not None:
            raise error
        return admin_value

    windll = SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=is_admin))
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)


def test_check_windows_permissions_admin(monkeypatch):
    _install_windll(monkeypatch, admin_value=1)
    res = at.check_windows_audio_permissions()
    assert res["has_permissions"] is True and res["is_admin"] is True and res["can_request"] is False


def test_check_windows_permissions_not_admin(monkeypatch):
    _install_windll(monkeypatch, admin_value=0)
    res = at.check_windows_audio_permissions()
    assert res["has_permissions"] is False and res["is_admin"] is False
    assert res["can_request"] is True and "administrator" in res["request_message"]


def test_check_windows_permissions_error(monkeypatch):
    _install_windll(monkeypatch, error=OSError("no shell32"))
    assert at.check_windows_audio_permissions() == {
        "has_permissions": False, "message": "Unable to check administrator status", "can_request": False,
    }


def _install_pwd_grp(monkeypatch, user="streamer", members=None, group_missing=False, pwd_error=None):
    pwd = types.ModuleType("pwd")

    def getpwuid(uid):
        if pwd_error is not None:
            raise pwd_error
        return SimpleNamespace(pw_name=user)

    pwd.getpwuid = getpwuid
    grp = types.ModuleType("grp")

    def getgrnam(name):
        assert name == "audio"
        if group_missing:
            raise KeyError(name)
        return SimpleNamespace(gr_mem=list(members or []))

    grp.getgrnam = getgrnam
    monkeypatch.setitem(sys.modules, "pwd", pwd)
    monkeypatch.setitem(sys.modules, "grp", grp)


def test_check_linux_permissions_in_group(monkeypatch):
    _install_pwd_grp(monkeypatch, members=["streamer"])
    res = at.check_linux_audio_permissions()
    assert res["has_permissions"] is True and res["can_request"] is False


def test_check_linux_permissions_not_in_group(monkeypatch):
    _install_pwd_grp(monkeypatch, members=["someone"])
    res = at.check_linux_audio_permissions()
    assert res["has_permissions"] is False and res["can_request"] is True
    assert "sudo usermod -a -G audio streamer" in res["request_message"]


def test_check_linux_permissions_no_audio_group(monkeypatch):
    _install_pwd_grp(monkeypatch, group_missing=True)
    res = at.check_linux_audio_permissions()
    assert res["has_permissions"] is False and res["can_request"] is True


def test_check_linux_permissions_error(monkeypatch):
    _install_pwd_grp(monkeypatch, pwd_error=KeyError("uid"))
    assert at.check_linux_audio_permissions() == {
        "has_permissions": False, "message": "Unable to check audio group membership", "can_request": False,
    }


# --------------------------------------------------------------------------- permission requests


def test_request_macos_permissions(run):
    rec = run({"osascript": _done(0)})
    assert at.request_macos_audio_permissions() is True
    argv = rec.calls[0][0]
    assert argv[:2] == ["osascript", "-e"] and "Privacy_Microphone" in argv[2]

    run({"osascript": _done(1, stderr="denied")})
    assert at.request_macos_audio_permissions() is False

    run({"osascript": OSError("no osascript")})
    assert at.request_macos_audio_permissions() is False


def test_request_windows_permissions_only_logs(monkeypatch, run, caplog):
    rec = run({})
    with caplog.at_level("INFO", logger=at.logger.name):
        assert at.request_windows_audio_permissions() is True
    assert rec.calls == []  # nothing is launched
    assert any("Run as administrator" in r.getMessage() for r in caplog.records)


def test_request_windows_permissions_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("log broke")

    monkeypatch.setattr(at.logger, "info", boom)
    assert at.request_windows_audio_permissions() is False


def test_request_linux_permissions(monkeypatch, run):
    _install_pwd_grp(monkeypatch, user="dj")
    rec = run({"sudo": _done(0)})
    assert at.request_linux_audio_permissions() is True
    assert rec.calls[0][0] == ["sudo", "usermod", "-a", "-G", "audio", "dj"]

    run({"sudo": _done(1, stderr="password required")})
    assert at.request_linux_audio_permissions() is False

    run({"sudo": OSError("no sudo")})
    assert at.request_linux_audio_permissions() is False

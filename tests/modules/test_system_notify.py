# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for ``modules.system_notify`` (OS desktop notifications, all backends faked)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import subprocess
from types import SimpleNamespace

import pytest

from modules import system_notify as sn
from modules import tray_controller


@pytest.fixture(autouse=True)
def _no_real_processes(monkeypatch):
    """Nothing here may start a real process or show a real notification."""

    def refuse(*a, **k):
        raise AssertionError("real subprocess.run called")

    monkeypatch.setattr(sn.subprocess, "run", refuse)
    monkeypatch.setattr(tray_controller, "notify_via_tray", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tray")))


def _settings(monkeypatch, value):
    from modules import dataobjects

    if isinstance(value, Exception):
        def get():
            raise value
    else:
        def get():
            return value
    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=get))


@pytest.mark.parametrize(
    "settings,expected",
    [
        (None, True),
        (SimpleNamespace(), True),
        (SimpleNamespace(notifications_enabled=False), False),
        (SimpleNamespace(notifications_enabled=1), True),
        (RuntimeError("x"), True),
    ],
)
def test_notifications_enabled(monkeypatch, settings, expected):
    _settings(monkeypatch, settings)
    assert sn._notifications_enabled() is expected


class FakeRun:
    def __init__(self, returncode=0, stderr=b"", exc=None):
        self.calls = []
        self.returncode, self.stderr, self.exc = returncode, stderr, exc

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.exc:
            raise self.exc
        return SimpleNamespace(returncode=self.returncode, stderr=self.stderr)


def test_run_success(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(sn.subprocess, "run", fake)
    monkeypatch.setattr(sn.sys, "platform", "darwin")
    assert sn._run(["osascript", "-e", "x"]) is True
    cmd, kwargs = fake.calls[0]
    assert cmd == ["osascript", "-e", "x"]
    assert kwargs == {"capture_output": True, "timeout": sn._SUBPROCESS_TIMEOUT_SEC, "check": False}


@pytest.mark.parametrize("stderr", [b"bad thing", b"", None])
def test_run_nonzero_exit(monkeypatch, stderr):
    monkeypatch.setattr(sn.subprocess, "run", FakeRun(returncode=1, stderr=stderr))
    assert sn._run(["x"]) is False


def test_run_exception(monkeypatch):
    monkeypatch.setattr(sn.subprocess, "run", FakeRun(exc=OSError("missing")))
    assert sn._run(["x"]) is False


def test_no_window_kwargs(monkeypatch):
    monkeypatch.setattr(sn.sys, "platform", "linux")
    assert sn._no_window_kwargs() == {}
    monkeypatch.setattr(sn.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert sn._no_window_kwargs() == {"creationflags": 0x08000000}


def test_escape_and_quote():
    assert sn._escape_applescript('a "b" \\c') == 'a \\"b\\" \\\\c'
    assert sn._powershell_quote("it's") == "it''s"


def test_notify_macos_builds_osascript(monkeypatch):
    seen = []
    monkeypatch.setattr(sn, "_run", lambda cmd: seen.append(cmd) or True)
    assert sn._notify_macos('T"', "M") is True
    assert seen == [["osascript", "-e", 'display notification "M" with title "T\\""']]


def test_notify_windows_uses_tray_first(monkeypatch):
    monkeypatch.setattr(tray_controller, "notify_via_tray", lambda t, m: True)
    monkeypatch.setattr(sn, "_run", lambda cmd: pytest.fail("should not run powershell"))
    assert sn._notify_windows("T", "M") is True


def test_notify_windows_powershell_fallback(monkeypatch):
    monkeypatch.setattr(tray_controller, "notify_via_tray", lambda t, m: False)
    monkeypatch.setattr(sn.shutil, "which", lambda name: "C:/ps.exe" if name == "pwsh" else None)
    seen = []
    monkeypatch.setattr(sn, "_run", lambda cmd: seen.append(cmd) or True)
    assert sn._notify_windows("Tit'le", "Msg") is True
    cmd = seen[0]
    assert cmd[:4] == ["C:/ps.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Tit''le" in cmd[4] and "'Msg'" in cmd[4] and "'Mycelian'" in cmd[4]


def test_notify_windows_no_powershell(monkeypatch):
    monkeypatch.setattr(tray_controller, "notify_via_tray", lambda t, m: False)
    monkeypatch.setattr(sn.shutil, "which", lambda name: None)
    assert sn._notify_windows("T", "M") is False


def test_notify_linux_notify_send(monkeypatch):
    monkeypatch.setattr(sn.shutil, "which", lambda name: "/usr/bin/notify-send")
    seen = []
    monkeypatch.setattr(sn, "_run", lambda cmd: seen.append(cmd) or True)
    assert sn._notify_linux("T", "M") is True
    assert seen == [["notify-send", "-a", "Mycelian", "T", "M"]]


@pytest.mark.parametrize("which,run_ok", [(None, True), ("/usr/bin/notify-send", False)])
def test_notify_linux_falls_back_to_tray(monkeypatch, which, run_ok):
    monkeypatch.setattr(sn.shutil, "which", lambda name: which)
    monkeypatch.setattr(sn, "_run", lambda cmd: run_ok)
    tray = []
    monkeypatch.setattr(tray_controller, "notify_via_tray", lambda t, m: tray.append((t, m)) or True)
    assert sn._notify_linux("T", "M") is True
    assert tray == [("T", "M")]


@pytest.mark.parametrize("platform,backend", [("darwin", "_notify_macos"), ("win32", "_notify_windows"),
                                              ("linux", "_notify_linux"), ("linux2", "_notify_linux")])
def test_dispatch_routes_by_platform(monkeypatch, platform, backend):
    monkeypatch.setattr(sn.sys, "platform", platform)
    seen = []
    for name in ("_notify_macos", "_notify_windows", "_notify_linux"):
        monkeypatch.setattr(sn, name, lambda t, m, _n=name: seen.append(_n) or True)
    assert sn._dispatch("T", "M") is True
    assert seen == [backend]


def test_dispatch_unknown_platform(monkeypatch):
    monkeypatch.setattr(sn.sys, "platform", "sunos5")
    assert sn._dispatch("T", "M") is False


def test_notify_respects_setting_and_force(monkeypatch):
    seen = []
    monkeypatch.setattr(sn, "_dispatch", lambda t, m: seen.append((t, m)) or True)
    monkeypatch.setattr(sn, "_notifications_enabled", lambda: False)
    assert sn.notify("hi") is False
    assert seen == []
    assert sn.notify("hi", title="X", force=True) is True
    assert seen == [("X", "hi")]
    monkeypatch.setattr(sn, "_notifications_enabled", lambda: True)
    assert sn.notify("yo") is True
    assert seen[-1] == ("Mycelian", "yo")


def test_notify_never_raises(monkeypatch):
    monkeypatch.setattr(sn, "_notifications_enabled", lambda: True)

    def boom(t, m):
        raise RuntimeError("x")

    monkeypatch.setattr(sn, "_dispatch", boom)
    assert sn.notify("hi") is False


def test_notify_async_starts_daemon_thread(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs)

    monkeypatch.setattr(sn.threading, "Thread", FakeThread)
    assert sn.notify_async("msg", title="T", force=True) is None
    (kw,) = started
    assert kw["target"] is sn.notify and kw["args"] == ("msg",)
    assert kw["kwargs"] == {"title": "T", "force": True}
    assert kw["daemon"] is True and kw["name"] == "mycelian-system-notify"

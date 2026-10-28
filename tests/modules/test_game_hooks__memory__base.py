# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/memory/base.py (PID scan with backoff, AttachedProcess)."""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks.memory import base  # noqa: E402


class _NoSuch(Exception):
    pass


class _Denied(Exception):
    pass


class _Proc:
    def __init__(self, info):
        self.info = info


def _fake_psutil(procs):
    calls = []

    def process_iter(attrs):
        calls.append(attrs)
        return iter(procs)

    mod = types.SimpleNamespace(process_iter=process_iter, NoSuchProcess=_NoSuch, AccessDenied=_Denied)
    return mod, calls


class _RaisingInfo(dict):
    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    def get(self, *a, **k):
        raise self._exc


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(base, "_pid_scan_next_ok", {})
    clock = {"t": 100.0}
    monkeypatch.setattr(base.time, "monotonic", lambda: clock["t"])
    return clock


def test_attached_process_is_frozen():
    p = base.AttachedProcess(pid=1, handle=2, module_base=3)
    assert (p.pid, p.handle, p.module_base) == (1, 2, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.pid = 5  # type: ignore[misc]


def test_find_pid_matches_case_insensitive(monkeypatch):
    mod, calls = _fake_psutil([_Proc({"pid": 7, "name": "other.exe"}), _Proc({"pid": "42", "name": "FF7_EN.exe"})])
    monkeypatch.setitem(sys.modules, "psutil", mod)
    assert base.find_process_pid(("ff7_en.EXE",)) == 42
    assert calls == [["pid", "name"]]
    assert base._pid_scan_next_ok == {}


def test_find_pid_miss_sets_backoff_and_skips_scan(monkeypatch, _clean):
    mod, calls = _fake_psutil([_Proc({"pid": 1, "name": None})])
    monkeypatch.setitem(sys.modules, "psutil", mod)
    assert base.find_process_pid(("Game.exe",)) is None
    assert base._pid_scan_next_ok[("game.exe",)] == pytest.approx(101.0)
    # within backoff: no scan at all
    assert base.find_process_pid(("game.exe",)) is None
    assert len(calls) == 1
    _clean["t"] = 101.5
    assert base.find_process_pid(("game.exe",)) is None
    assert len(calls) == 2


def test_find_pid_success_clears_backoff(monkeypatch, _clean):
    base._pid_scan_next_ok[("game.exe",)] = 50.0
    mod, _ = _fake_psutil([_Proc({"pid": 9, "name": "game.exe"})])
    monkeypatch.setitem(sys.modules, "psutil", mod)
    assert base.find_process_pid(("game.exe",)) == 9
    assert ("game.exe",) not in base._pid_scan_next_ok


def test_find_pid_skips_vanished_and_denied(monkeypatch):
    procs = [_Proc(_RaisingInfo(_NoSuch())), _Proc(_RaisingInfo(_Denied())), _Proc({"pid": 3, "name": "g.exe"})]
    mod, _ = _fake_psutil(procs)
    monkeypatch.setitem(sys.modules, "psutil", mod)
    assert base.find_process_pid(("g.exe",)) == 3


def test_find_pid_without_psutil(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)  # import raises ImportError
    assert base.find_process_pid(("g.exe",)) is None
    assert base._pid_scan_next_ok == {}


def test_is_process_running(monkeypatch):
    monkeypatch.setattr(base, "find_process_pid", lambda names: 5 if names == ("a",) else None)
    assert base.is_process_running(("a",)) is True
    assert base.is_process_running(("b",)) is False


def test_protocol_stub_bodies_return_none():
    # The Protocol methods are documentation stubs; calling them on a dummy returns None.
    pm = base.ProcessMemory
    dummy = object()
    for name in ("is_available", "is_attached", "close"):
        assert getattr(pm, name)(dummy) is None
    assert pm.is_process_running(dummy, ("x",)) is None
    assert pm.attach(dummy, ("x",)) is None
    assert pm.ensure_attached(dummy, ("x",)) is None
    assert pm.read(dummy, 0, 1) is None
    assert pm.write(dummy, 0, b"") is None
    assert pm.protect_write(dummy, 0, b"") is None
    assert pm.run_remote_shellcode(dummy, b"", "l") is None
    assert pm.rebase(dummy, 0) is None
    for prop in ("pid", "handle", "module_base"):
        assert getattr(pm, prop).fget(dummy) is None

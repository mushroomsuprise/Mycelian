# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.startup_profiler (startup timing, milestones, summaries)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import startup_profiler as sp


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _restore_globals(monkeypatch):
    monkeypatch.setattr(sp, "_startup_timings", {})
    monkeypatch.setattr(sp, "_total_startup_time", 0.0)
    monkeypatch.setattr(sp, "_startup_profiling_enabled", False)
    monkeypatch.setattr(sp, "_process_start", sp._process_start)
    monkeypatch.setattr(sp, "_last_import_mark", 0.0)
    monkeypatch.setattr(sp, "_milestones_seen", set())
    monkeypatch.delenv(sp._PROFILE_OWNER_ENV, raising=False)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(sp.time, "perf_counter", c)
    return c


@pytest.fixture
def enabled(monkeypatch):
    sp.configure_startup_profiling(True)


def test_configure_and_query():
    assert sp.is_startup_profiling_enabled() is False
    sp.configure_startup_profiling(True)
    assert sp.is_startup_profiling_enabled() is True
    sp.configure_startup_profiling(False)
    assert sp.is_startup_profiling_enabled() is False


def test_profiling_owner(monkeypatch):
    assert sp._is_profiling_owner() is True
    monkeypatch.setenv(sp._PROFILE_OWNER_ENV, str(os.getpid()))
    assert sp._is_profiling_owner() is True
    monkeypatch.setenv(sp._PROFILE_OWNER_ENV, "999999999")
    assert sp._is_profiling_owner() is False


def test_should_emit(monkeypatch):
    assert sp._should_emit_startup_profile() is False
    sp.configure_startup_profiling(True)
    assert sp._should_emit_startup_profile() is True
    monkeypatch.setenv(sp._PROFILE_OWNER_ENV, "1")
    assert sp._should_emit_startup_profile() is (os.getpid() == 1)


def test_claim_profiling_process_first_wins(monkeypatch):
    sp.claim_profiling_process()
    assert os.environ[sp._PROFILE_OWNER_ENV] == str(os.getpid())
    monkeypatch.setenv(sp._PROFILE_OWNER_ENV, "42")
    sp.claim_profiling_process()
    assert os.environ[sp._PROFILE_OWNER_ENV] == "42"


def test_mark_process_start_resets_baseline_quietly(clock, capsys, monkeypatch):
    monkeypatch.setattr(sp, "_last_import_mark", 5.0)
    monkeypatch.setattr(sp, "_print_process_age", lambda: pytest.fail("profiling disabled"))
    sp.mark_process_start()
    assert sp._process_start == 100.0 and sp._last_import_mark == 0.0
    clock.t = 101.5
    assert sp.get_elapsed_since_baseline() == pytest.approx(1.5)
    assert capsys.readouterr().out == ""


def test_mark_process_start_prints_age_when_enabled(clock, enabled, monkeypatch):
    called = []
    monkeypatch.setattr(sp, "_print_process_age", lambda: called.append(True))
    sp.mark_process_start()
    assert called == [True]


class FakeProc:
    def __init__(self, created, exe="/x/app", parent=None):
        self._created = created
        self._exe = exe
        self._parent = parent

    def create_time(self):
        return self._created

    def exe(self):
        return self._exe

    def parent(self):
        return self._parent


@pytest.fixture
def fake_psutil(monkeypatch):
    holder = SimpleNamespace(proc=None)
    mod = SimpleNamespace(Process=lambda: holder.proc)
    monkeypatch.setitem(sys.modules, "psutil", mod)
    monkeypatch.setattr(sp.time, "time", lambda: 1000.0)
    return holder


def test_print_process_age_dev(fake_psutil, monkeypatch, capsys):
    monkeypatch.delattr(sys, "frozen", raising=False)
    fake_psutil.proc = FakeProc(998.5)
    sp._print_process_age()
    assert capsys.readouterr().out == "[startup] OS process age at baseline: 1.500s\n"


def test_print_process_age_frozen_with_bootloader(fake_psutil, monkeypatch, capsys):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/x/app")
    fake_psutil.proc = FakeProc(999.0, parent=FakeProc(997.0, exe="/x/app"))
    sp._print_process_age()
    out = capsys.readouterr().out
    assert "OS process age at baseline: 1.000s" in out
    assert "bootloader process age at baseline: 3.000s" in out


@pytest.mark.parametrize("parent", [None, FakeProc(990.0, exe="/other/launcher")])
def test_print_process_age_frozen_other_parent(fake_psutil, monkeypatch, capsys, parent):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/x/app")
    fake_psutil.proc = FakeProc(999.0, parent=parent)
    sp._print_process_age()
    assert "bootloader" not in capsys.readouterr().out


def test_print_process_age_error(monkeypatch, capsys):
    def boom():
        raise RuntimeError("no access")

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=boom))
    sp._print_process_age()
    assert capsys.readouterr().out == "[startup] process age unavailable: no access\n"


def test_milestone_once_and_only_when_enabled(clock, capsys, monkeypatch):
    monkeypatch.setattr(sp, "_process_start", 100.0)
    sp.mark_startup_milestone("ui")
    assert capsys.readouterr().out == ""
    assert "ui" not in sp._milestones_seen
    sp.configure_startup_profiling(True)
    clock.t = 102.25
    sp.mark_startup_milestone("ui")
    sp.mark_startup_milestone("ui")
    assert capsys.readouterr().out == "[startup] +2.250s milestone: ui\n"


def test_print_startup_message(capsys):
    sp.print_startup_message("hidden")
    assert capsys.readouterr().out == ""
    sp.configure_startup_profiling(True)
    sp.print_startup_message("hello")
    assert capsys.readouterr().out == "[startup] hello\n"


def test_print_timing(clock, capsys, monkeypatch):
    monkeypatch.setattr(sp, "_process_start", 99.0)
    sp.print_timing("x", 0.5)
    assert capsys.readouterr().out == ""
    sp.configure_startup_profiling(True)
    sp.print_timing("x", 0.5)
    assert capsys.readouterr().out == "[startup] +1.000s (+0.500s) x\n"


def test_print_import_timing(clock, capsys, monkeypatch, enabled):
    monkeypatch.setattr(sp, "_process_start", 100.0)
    clock.t = 100.2
    sp.print_import_timing("process start")
    assert sp._startup_timings["import:process start"] == 0.0
    assert sp._last_import_mark == pytest.approx(0.2)
    clock.t = 100.7
    sp.print_import_timing("nicegui")
    assert sp._startup_timings["import:nicegui"] == pytest.approx(0.5)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "[startup] +0.200s (+0.000s) process start"
    assert out[1] == "[startup] +0.700s (+0.500s) nicegui"


def test_print_import_timing_negative_segment_clamped(clock, monkeypatch, enabled, capsys):
    monkeypatch.setattr(sp, "_process_start", 100.0)
    monkeypatch.setattr(sp, "_last_import_mark", 5.0)
    clock.t = 101.0
    sp.print_import_timing("late")
    assert sp._startup_timings["import:late"] == 0.0


def test_print_import_timing_disabled(clock):
    sp.print_import_timing("x")
    assert sp._startup_timings == {}


def test_timed_success_records_when_enabled(clock, enabled, caplog, capsys):
    @sp.timed
    def work(a, b=1):
        """doc"""
        clock.t += 0.25
        return a + b

    assert work.__name__ == "work" and work.__doc__ == "doc"
    with caplog.at_level(logging.INFO, logger=sp.logger.name):
        assert work(2, b=3) == 5
    assert sp.get_startup_timings() == {"work": pytest.approx(0.25)}
    assert "work completed in 0.250s" in caplog.text
    assert "(+0.250s) work" in capsys.readouterr().out


def test_timed_success_disabled_logs_only(clock, caplog):
    @sp.timed
    def work():
        return "ok"

    with caplog.at_level(logging.INFO, logger=sp.logger.name):
        assert work() == "ok"
    assert sp._startup_timings == {}
    assert "work completed" in caplog.text


def test_timed_failure_logs_and_reraises(clock, caplog):
    @sp.timed
    def bad():
        clock.t += 1
        raise ValueError("nope")

    with caplog.at_level(logging.ERROR, logger=sp.logger.name), pytest.raises(ValueError):
        bad()
    assert "bad failed after 1.000s: nope" in caplog.text
    assert sp._startup_timings == {}


def test_get_startup_timings_is_copy():
    sp._startup_timings["a"] = 1.0
    copy = sp.get_startup_timings()
    copy["b"] = 2.0
    assert "b" not in sp._startup_timings


def test_total_startup_time():
    assert sp.get_total_startup_time() == 0.0
    sp.set_total_startup_time(3.5)
    assert sp.get_total_startup_time() == 3.5


def test_filtered_timings():
    sp._startup_timings.update({"import:a": 0.1, "import:b": 0.3, "x": 0.2, "neg": -1.0})
    assert sp._filtered_timings() == [("import:b", 0.3), ("x", 0.2), ("import:a", 0.1)]
    assert sp._filtered_timings("import:") == [("import:b", 0.3), ("import:a", 0.1)]


def test_log_startup_summary_disabled(capsys):
    sp._startup_timings["x"] = 1.0
    sp.log_startup_summary()
    assert capsys.readouterr().out == ""


def test_log_startup_summary_empty(enabled, capsys):
    sp.log_startup_summary("T")
    assert capsys.readouterr().out == "\n=== T ===\n  (no timings recorded)\n\n"


def test_log_startup_summary_full(enabled, capsys):
    sp._startup_timings.update({"a": 3.0, "b": 1.0})
    sp.set_total_startup_time(5.0)
    sp.log_startup_summary()
    out = capsys.readouterr().out
    assert "=== STARTUP TIMING SUMMARY ===" in out
    assert "  a: 3.000s (75.0%)" in out
    assert "  b: 1.000s (25.0%)" in out
    assert "Critical path (__main__ only): 5.000s" in out
    assert "Sum of timed blocks: 4.000s" in out
    assert out.rstrip().endswith("=== END STARTUP TIMING SUMMARY ===")


def test_log_startup_summary_prefix_hides_critical_path_and_zero_total(enabled, capsys):
    sp._startup_timings.update({"import:a": 0.0, "other": 2.0})
    sp.set_total_startup_time(5.0)
    sp.log_startup_summary("IMPORTS", prefix="import:")
    out = capsys.readouterr().out
    assert "  import:a: 0.000s (0.0%)" in out
    assert "other" not in out
    assert "Critical path" not in out


def test_startup_timer_success(clock, enabled, caplog, capsys):
    with caplog.at_level(logging.INFO, logger=sp.logger.name):
        with sp.StartupTimer("block", log_start=True) as t:
            assert t.start_time == 100.0
            clock.t += 0.5
    assert sp._startup_timings["block"] == pytest.approx(0.5)
    assert "Starting block..." in caplog.text
    assert "block completed in 0.500s" in caplog.text
    assert "(+0.500s) block\n" in capsys.readouterr().out


def test_startup_timer_failure(clock, enabled, caplog, capsys):
    with caplog.at_level(logging.INFO, logger=sp.logger.name):
        with pytest.raises(KeyError):
            with sp.StartupTimer("blk"):
                clock.t += 1
                raise KeyError("k")
    assert "Starting" not in caplog.text
    assert "blk failed after 1.000s" in caplog.text
    assert "blk (failed)" in capsys.readouterr().out


def test_startup_timer_disabled_and_not_entered(clock, caplog):
    t = sp.StartupTimer("never")
    assert t.__exit__(None, None, None) is None  # start_time None: no-op
    with sp.StartupTimer("quiet"):
        pass
    assert sp._startup_timings == {}

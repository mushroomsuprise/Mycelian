# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.log_parser (log paths, clearing, tail reads, parsing and de-duplication)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import log_parser as lp

TS1 = "2026-01-01 10:00:00,000"
TS2 = "2026-01-01 11:00:00,000"
TS3 = "2026-01-02 09:00:00,500"


def line(ts, logger, level, msg):
    return f"{ts} - {logger} - {level} - {msg}"


def test_log_paths_follow_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert lp.get_log_dir() == tmp_path / "logs"
    assert lp.get_log_file_path() == tmp_path / "logs" / "mycelian.log"


def test_get_log_file_size(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert lp.get_log_file_size() == 0
    f = tmp_path / "x.log"
    f.write_bytes(b"12345")
    assert lp.get_log_file_size(f) == 5
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "mycelian.log").write_bytes(b"abc")
    assert lp.get_log_file_size() == 3


def test_same_file(tmp_path, monkeypatch):
    f = tmp_path / "a.log"
    assert lp._same_file(str(f), f) is True
    assert lp._same_file(str(tmp_path / "b.log"), f) is False

    def bad_resolve(self, *a, **k):
        raise OSError("x")

    monkeypatch.setattr(Path, "resolve", bad_resolve)
    assert lp._same_file(str(f), f) is False


def test_clear_log_file_through_open_handler(tmp_path):
    target = tmp_path / "logs" / "app.log"
    target.parent.mkdir()
    handler = logging.FileHandler(target, encoding="utf-8")
    other = logging.FileHandler(tmp_path / "other.log", encoding="utf-8")
    lg = logging.getLogger("test.log_parser.clear")
    lg.propagate = False
    lg.addHandler(handler)
    lg.addHandler(other)
    root = logging.getLogger()
    root.addHandler(handler)  # same handler on two loggers: handled once
    try:
        lg.setLevel(logging.INFO)
        lg.info("before clear")
        assert "before clear" in target.read_text()
        assert lp.clear_log_file(target) is True
        text = target.read_text()
        assert "before clear" not in text
        assert text.endswith("- modules.log_parser - WARNING - Log cleared from App Settings\n")
        assert handler.stream is None
        assert other.stream is not None  # untouched
        lg.info("after clear")
        assert "after clear" in target.read_text()
        # lock is released: another clear works
        assert lp.clear_log_file(target) is True
    finally:
        root.removeHandler(handler)
        lg.removeHandler(handler)
        lg.removeHandler(other)
        handler.close()
        other.close()


def test_clear_log_file_handler_without_stream(tmp_path):
    target = tmp_path / "app.log"
    handler = logging.FileHandler(target, delay=True)
    lg = logging.getLogger("test.log_parser.delay")
    lg.addHandler(handler)
    try:
        assert handler.stream is None
        assert lp.clear_log_file(target) is True
        assert target.read_text().count("\n") == 1
    finally:
        lg.removeHandler(handler)
        handler.close()


def test_clear_log_file_default_path_creates_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert lp.clear_log_file() is True
    assert (tmp_path / "logs" / "mycelian.log").is_file()


def test_clear_log_file_write_error_returns_false(tmp_path):
    target = tmp_path / "dir_not_file"
    target.mkdir()
    assert lp.clear_log_file(target) is False


def test_read_log_tail_cases(tmp_path):
    assert lp._read_log_tail(tmp_path / "missing.log", 100) == ""
    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    assert lp._read_log_tail(empty, 100) == ""
    small = tmp_path / "small.log"
    small.write_bytes("a\nbé\n".encode())
    assert lp._read_log_tail(small, 100) == "a\nbé\n"
    big = tmp_path / "big.log"
    big.write_bytes(b"first line\nsecond\nthird\n")
    assert lp._read_log_tail(big, 12) == "third\n"
    nonl = tmp_path / "nonl.log"
    nonl.write_bytes(b"x" * 50)
    assert lp._read_log_tail(nonl, 10) == ""
    bad = tmp_path / "bad.log"
    bad.write_bytes(b"\xff\xfe ok")
    assert lp._read_log_tail(bad, 100) == "�� ok"


def test_parse_log_text_groups_continuations():
    text = "\n".join([
        "garbage before any entry",
        "",
        line(TS1, "a", "ERROR", "boom"),
        "Traceback (most recent call last):",
        "  File x",
        line(TS2, "b.c", "INFO", "fine - with - dashes"),
    ])
    entries = lp._parse_log_text(text)
    assert len(entries) == 2
    assert entries[0].timestamp == TS1 and entries[0].logger == "a" and entries[0].level == "ERROR"
    assert entries[0].raw_lines[1:] == ["Traceback (most recent call last):", "  File x"]
    assert entries[1].logger == "b.c" and entries[1].message == "fine - with - dashes"
    assert lp._parse_log_text("") == []
    assert lp._parse_log_text("no entries\nhere") == []


def test_normalize_message():
    assert lp._normalize_message(f"{TS1} - x - ERROR -   inner   msg\t here") == "inner msg here"
    assert lp._normalize_message("  a\n b ") == "a b"


@pytest.mark.parametrize(
    "level, extra, expected",
    [
        ("ERROR", "", True),
        ("CRITICAL", "", True),
        ("WARNING", "", False),
        ("INFO", "", False),
        ("ERROR", "NPSSO token expired", False),
        ("ERROR", "ConnectionState.CLOSED", False),
        ("CRITICAL", "Benign websocket close", False),
    ],
)
def test_is_actionable_error(level, extra, expected):
    entry = lp.LogEntry(TS1, "x", level, "msg", raw_lines=["first", extra])
    assert lp.is_actionable_error(entry) is expected


def test_dedupe_counts_promotes_critical_and_sorts():
    e1 = lp.LogEntry(TS1, "mod.a", "ERROR", "same  error", ["l", "tb1"])
    e2 = lp.LogEntry(TS3, "mod.a", "CRITICAL", "same error", ["l", "tb2", "tb3"])
    e3 = lp.LogEntry(TS2, "mod.b", "ERROR", "other", ["l"])
    out = lp._dedupe_errors([e1, e3, e2])
    assert [u.message for u in out] == ["same  error", "other"]
    first = out[0]
    assert first.count == 2 and first.last_seen == TS3 and first.level == "CRITICAL"
    assert first.source == "mod.a" and first.details == ["tb2", "tb3"]
    assert out[1].count == 1 and out[1].details == []


def test_dedupe_does_not_downgrade_critical():
    e1 = lp.LogEntry(TS1, "m", "CRITICAL", "x", ["l"])
    e2 = lp.LogEntry(TS2, "m", "ERROR", "x", ["l"])
    (u,) = lp._dedupe_errors([e1, e2])
    assert u.level == "CRITICAL" and u.count == 2


def test_dedupe_caps_detail_lines():
    raw = ["head"] + [f"tb{i}" for i in range(100)]
    (u,) = lp._dedupe_errors([lp.LogEntry(TS1, "m", "ERROR", "x", raw)])
    assert len(u.details) == lp._MAX_DETAIL_LINES
    assert u.details[0] == "tb0"


def test_get_actionable_errors_end_to_end(tmp_path):
    f = tmp_path / "m.log"
    f.write_text("\n".join([
        line(TS1, "a", "ERROR", "e1"),
        line(TS2, "a", "ERROR", "e2"),
        line(TS3, "a", "ERROR", "e1"),
        line(TS3, "a", "INFO", "info"),
        line(TS3, "a", "ERROR", "npsso code has expired"),
    ]) + "\n")
    s = lp.get_actionable_errors(f)
    assert s.total_count == 3
    assert [u.message for u in s.unique_errors] == ["e1", "e2"]
    s1 = lp.get_actionable_errors(f, max_unique=1)
    assert s1.total_count == 3 and len(s1.unique_errors) == 1
    s0 = lp.get_actionable_errors(f, max_unique=0)
    assert len(s0.unique_errors) == 2


def test_get_actionable_errors_default_path_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    s = lp.get_actionable_errors()
    assert s.total_count == 0 and s.unique_errors == []

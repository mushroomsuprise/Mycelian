# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.log_trim.trim_log_file (edge cases and error handling)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import log_trim
from modules.log_trim import trim_log_file


def test_missing_file_is_noop(tmp_path):
    trim_log_file(tmp_path / "none.log", 10)
    assert not (tmp_path / "none.log").exists()


def test_directory_is_noop(tmp_path):
    trim_log_file(tmp_path, 1)
    assert tmp_path.is_dir()


def test_under_and_at_cap_untouched(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"0123456789")
    mtime = p.stat().st_mtime_ns
    trim_log_file(p, 10)
    assert p.read_bytes() == b"0123456789"
    assert p.stat().st_mtime_ns == mtime


def test_stat_error_is_swallowed(tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"x" * 100)
    real_stat = Path.stat

    def failing_stat(self, *a, **k):
        if self == p:
            raise OSError("gone")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", failing_stat)
    trim_log_file(p, 10)
    monkeypatch.undo()
    assert p.read_bytes() == b"x" * 100


def test_read_error_leaves_file(tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"line\n" * 100)

    def bad_open(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr("builtins.open", bad_open)
    trim_log_file(p, 50)
    monkeypatch.undo()
    assert p.read_bytes() == b"line\n" * 100


def test_trims_to_newest_whole_lines(tmp_path):
    p = tmp_path / "a.log"
    lines = [f"line {i:04d}\n".encode() for i in range(200)]
    p.write_bytes(b"".join(lines))
    trim_log_file(p, 100)
    data = p.read_bytes()
    assert len(data) <= 100
    assert data.endswith(lines[-1])
    assert data.startswith(b"line ")
    assert not (tmp_path / "a.log.tmp").exists()


def test_keep_bytes_larger_than_max_uses_max(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"".join(f"{i:09d}\n".encode() for i in range(100)))  # 1000 bytes
    trim_log_file(p, 200, keep_bytes=5000)
    assert len(p.read_bytes()) <= 200


def test_keep_bytes_smaller_trims_further(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"".join(f"{i:09d}\n".encode() for i in range(100)))
    trim_log_file(p, 500, keep_bytes=100)
    data = p.read_bytes()
    assert len(data) <= 100
    assert data.endswith(b"000000099\n")


def test_no_newlines_cuts_raw_bytes(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"a" * 50 + b"b" * 50)
    trim_log_file(p, 30)
    assert p.read_bytes() == b"b" * 30


def test_single_newline_then_long_tail_without_newline(tmp_path):
    # After dropping the partial first line, the remaining chunk is longer than the cap
    # and has no newline after the cut: falls back to a raw byte cut.
    p = tmp_path / "a.log"
    p.write_bytes(b"head\n" + b"z" * 100)
    trim_log_file(p, 40)
    assert p.read_bytes() == b"z" * 40


def test_large_file_reads_only_tail(tmp_path):
    p = tmp_path / "big.log"
    body = b"".join(f"row {i:06d}\n".encode() for i in range(5000))
    p.write_bytes(body)
    trim_log_file(p, 1000)
    data = p.read_bytes()
    assert len(data) <= 1000
    assert data.endswith(b"row 004999\n")


def test_write_failure_removes_tmp_and_keeps_original(tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    original = b"".join(f"{i:04d}\n".encode() for i in range(100))
    p.write_bytes(original)

    def bad_replace(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", bad_replace)
    trim_log_file(p, 50)
    monkeypatch.undo()
    assert p.read_bytes() == original
    assert not (tmp_path / "a.log.tmp").exists()


def test_write_failure_and_unlink_failure_swallowed(tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"x\n" * 100)
    monkeypatch.setattr(Path, "replace", lambda self, t: (_ for _ in ()).throw(OSError("no")))
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("no")))
    trim_log_file(p, 10)  # must not raise
    monkeypatch.undo()
    assert (tmp_path / "a.log.tmp").exists()
    assert p.read_bytes() == b"x\n" * 100


def test_open_for_write_failure_without_tmp(tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"x\n" * 100)
    real_open = open

    def selective_open(file, mode="r", *a, **k):
        if "w" in mode:
            raise OSError("disk full")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", selective_open)
    trim_log_file(p, 10)
    monkeypatch.undo()
    assert not (tmp_path / "a.log.tmp").exists()
    assert p.read_bytes() == b"x\n" * 100

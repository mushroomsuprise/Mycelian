#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Regression tests for byte-safe mycelian.log trimming (Windows CRLF)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.log_trim import trim_log_file


def _sample_line(index: int) -> bytes:
    return (
        f"2026-06-02 12:00:00,000 - modules.test - WARNING - event {index:08d}\r\n"
    ).encode("utf-8")


class LogTrimTests(unittest.TestCase):
    def test_trim_keeps_file_under_cap(self) -> None:
        max_bytes = 8192
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            path.write_bytes(_sample_line(0) * 2000)
            self.assertGreater(path.stat().st_size, max_bytes)

            trim_log_file(path, max_bytes)

            self.assertLessEqual(path.stat().st_size, max_bytes)

    def test_trim_does_not_amplify_cr_on_repeated_passes(self) -> None:
        max_bytes = 16384
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            path.write_bytes(_sample_line(0) * 4000)

            for _ in range(25):
                trim_log_file(path, max_bytes)
                path.write_bytes(_sample_line(1))
                trim_log_file(path, max_bytes)

            data = path.read_bytes()
            self.assertNotIn(b"\r\r", data)
            self.assertLessEqual(len(data), max_bytes)

            line_count = data.count(b"\n")
            avg_line_bytes = len(data) / max(line_count, 1)
            self.assertGreater(avg_line_bytes, 40)

    def test_trim_skips_partial_first_line(self) -> None:
        max_bytes = 256
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            full_line = _sample_line(42)
            # File must exceed max_bytes so trim runs; leading bytes simulate mid-line seek.
            path.write_bytes(b"partial" + full_line * 20)

            trim_log_file(path, max_bytes)

            data = path.read_bytes()
            self.assertFalse(data.startswith(b"partial"))
            self.assertIn(b"event 00000042", data)

    def test_trim_output_is_valid_utf8(self) -> None:
        max_bytes = 4096
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            line = "2026-06-02 12:00:00,000 - test - WARNING - emoji \U0001f680\r\n".encode(
                "utf-8"
            )
            path.write_bytes(line * 500)

            trim_log_file(path, max_bytes)

            path.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

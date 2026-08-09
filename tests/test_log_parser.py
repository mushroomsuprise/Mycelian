#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for mycelian.log parsing and actionable error filtering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.log_parser import (  # noqa: E402
    LogEntry,
    _parse_log_text,
    _read_log_tail,
    get_actionable_errors,
    is_actionable_error,
)


def _write_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class LogParserTests(unittest.TestCase):
    def test_parse_single_line_error(self) -> None:
        text = (
            "2026-06-30 20:13:33,894 - modules.psn_service - ERROR - "
            "Failed to connect PSNClient with the stored NPSSO code during initialization.\n"
        )
        entries = _parse_log_text(text)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.logger, "modules.psn_service")
        self.assertEqual(entry.level, "ERROR")
        self.assertIn("Failed to connect PSNClient", entry.message)

    def test_parse_attaches_traceback_continuation_lines(self) -> None:
        text = (
            "2026-07-01 04:40:40,080 - nicegui - ERROR - dictionary changed size during iteration\n"
            "Traceback (most recent call last):\n"
            '  File "nicegui\\outbox.py", line 105, in loop\n'
            "RuntimeError: dictionary changed size during iteration\n"
        )
        entries = _parse_log_text(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0].raw_lines), 4)
        self.assertIn("Traceback", entries[0].raw_lines[1])

    def test_filters_npsso_noise_even_if_error_level(self) -> None:
        entry = LogEntry(
            timestamp="2026-06-30 20:13:33,827",
            logger="modules.psnapi",
            level="ERROR",
            message="connect: NPSSO token expired or invalid: Your npsso code has expired",
            raw_lines=[
                "2026-06-30 20:13:33,827 - modules.psnapi - ERROR - connect: NPSSO token expired"
            ],
        )
        self.assertFalse(is_actionable_error(entry))

    def test_filters_nicegui_benign_errors(self) -> None:
        for message in (
            "dictionary changed size during iteration",
            "Event listeners changed after initial definition",
        ):
            entry = LogEntry(
                timestamp="2026-07-01 04:40:40,080",
                logger="nicegui",
                level="ERROR",
                message=message,
                raw_lines=[message],
            )
            self.assertFalse(is_actionable_error(entry), message)

    def test_keeps_psn_service_init_errors(self) -> None:
        entry = LogEntry(
            timestamp="2026-06-30 20:13:33,894",
            logger="modules.psn_service",
            level="ERROR",
            message="Failed to connect PSNClient with the stored NPSSO code during initialization.",
            raw_lines=[
                "2026-06-30 20:13:33,894 - modules.psn_service - ERROR - Failed to connect PSNClient"
            ],
        )
        self.assertTrue(is_actionable_error(entry))

    def test_deduplicates_repeated_errors_with_counts(self) -> None:
        with self.subTest("temp log"):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "mycelian.log"
                _write_log(
                    path,
                    (
                        "2026-06-30 20:13:33,894 - modules.psn_service - ERROR - "
                        "PSNClient authenticated status: False\n"
                        "2026-06-30 20:13:34,010 - modules.psn_service - ERROR - "
                        "PSNClient authenticated status: False\n"
                        "2026-06-30 20:13:35,935 - modules.psn_service - ERROR - "
                        "Failed to connect PSNClient with the stored NPSSO code during initialization.\n"
                    ),
                )
                summary = get_actionable_errors(path)
                self.assertEqual(summary.total_count, 3)
                self.assertEqual(len(summary.unique_errors), 2)
                by_message = {item.message: item.count for item in summary.unique_errors}
                self.assertEqual(by_message["PSNClient authenticated status: False"], 2)
                self.assertEqual(
                    by_message[
                        "Failed to connect PSNClient with the stored NPSSO code during initialization."
                    ],
                    1,
                )

    def test_missing_or_empty_log_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.log"
            summary = get_actionable_errors(missing)
            self.assertEqual(summary.total_count, 0)
            self.assertEqual(summary.unique_errors, [])

            empty = Path(tmp) / "empty.log"
            empty.write_text("", encoding="utf-8")
            summary = get_actionable_errors(empty)
            self.assertEqual(summary.total_count, 0)
            self.assertEqual(summary.unique_errors, [])

    def test_tail_read_returns_recent_errors_only(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            old_line = (
                "2020-01-01 00:00:00,000 - __main__ - ERROR - Old startup failure\n"
            )
            new_line = (
                "2026-06-30 20:13:33,894 - modules.psn_service - ERROR - "
                "Recent actionable error\n"
            )
            filler = ("x" * 200 + "\n") * 3000
            _write_log(path, old_line + filler + new_line)

            tail = _read_log_tail(path, tail_bytes=512 * 1024)
            summary = get_actionable_errors(path, tail_bytes=512 * 1024)
            self.assertIn("Recent actionable error", tail)
            self.assertNotIn("Old startup failure", tail)
            self.assertEqual(summary.total_count, 1)
            self.assertEqual(summary.unique_errors[0].message, "Recent actionable error")


if __name__ == "__main__":
    unittest.main()

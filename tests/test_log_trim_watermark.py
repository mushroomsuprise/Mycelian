#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""mycelian.log at its cap is trimmed to a lower watermark, not on every record."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.log_trim import trim_log_file  # noqa: E402


def _line(index: int) -> bytes:
    return f"2026-06-02 12:00:00,000 - modules.test - WARNING - event {index:08d}\n".encode()


class KeepBytesTests(unittest.TestCase):
    def test_over_cap_trims_down_to_keep_bytes_on_a_line_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            data = b"".join(_line(i) for i in range(3000))
            path.write_bytes(data)
            trim_log_file(path, 100_000, 60_000)
            kept = path.read_bytes()
            self.assertLessEqual(len(kept), 60_000)
            self.assertGreater(len(kept), 60_000 - len(_line(0)))
            self.assertTrue(data.endswith(kept))
            self.assertTrue(kept.startswith(b"2026-"))

    def test_under_cap_is_untouched_even_above_keep_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mycelian.log"
            data = b"".join(_line(i) for i in range(1000))
            path.write_bytes(data)
            trim_log_file(path, len(data), len(data) // 2)
            self.assertEqual(path.read_bytes(), data)

    def test_default_keep_bytes_matches_previous_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.log"
            b = Path(tmp) / "b.log"
            data = b"".join(_line(i) for i in range(3000))
            a.write_bytes(data)
            b.write_bytes(data)
            trim_log_file(a, 50_000)
            trim_log_file(b, 50_000, 50_000)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertLessEqual(len(a.read_bytes()), 50_000)


_HANDLER_PROBE = textwrap.dedent(
    """
    import os, runpy, sys, logging
    sys.path.insert(0, {root!r})
    sys.argv = ["main.py"]
    ns = runpy.run_path({main!r}, run_name="__mp_main__")
    import atexit
    atexit.unregister(ns["emergency_stats_save"])
    handler = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, ns["CappedFileHandler"])
    )
    handler.max_bytes = {cap}
    handler.keep_bytes = {keep}
    log = logging.getLogger("probe")
    path = handler.baseFilename
    drops = 0
    last = os.path.getsize(path)
    for i in range({records}):
        log.warning("record %08d %s", i, "x" * 60)
        size = os.path.getsize(path)
        if size < last:
            drops += 1
        last = size
        assert size <= {cap}, size
    print("DROPS", drops)
    """
)


class CappedHandlerTests(unittest.TestCase):
    def test_handler_rewrites_rarely_and_keeps_the_newest_records(self) -> None:
        cap, keep, records = 200_000, 160_000, 6000
        with tempfile.TemporaryDirectory() as tmp:
            main_copy = Path(tmp) / "main.py"
            shutil.copyfile(ROOT / "main.py", main_copy)
            code = _HANDLER_PROBE.format(
                root=str(ROOT), main=str(main_copy), cap=cap, keep=keep, records=records
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            drops = int(result.stdout.split("DROPS", 1)[1].split()[0])
            # ~130 bytes/record * 6000 records ~= 780 KB of output: with 40 KB of
            # headroom that is ~20 trims (it was one trim per record past the cap).
            self.assertGreater(drops, 5)
            self.assertLess(drops, 40)

            text = (Path(tmp) / "logs" / "mycelian.log").read_text(encoding="utf-8")
            lines = [ln for ln in text.splitlines() if " - probe - WARNING - record " in ln]
            numbers = [int(ln.split("record ")[1].split()[0]) for ln in lines]
            # Newest records survive, contiguous and in order, no torn lines.
            self.assertEqual(numbers[-1], records - 1)
            self.assertEqual(numbers, list(range(numbers[0], records)))
            self.assertTrue(text.splitlines()[0].startswith("20"))


if __name__ == "__main__":
    unittest.main()

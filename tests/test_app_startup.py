#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for process-wide startup guard."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import app_startup  # noqa: E402


class AppStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        app_startup._critical_startup_done = False

    def test_startup_flag_defaults_false(self) -> None:
        self.assertFalse(app_startup.critical_startup_done())

    def test_mark_startup_done_persists(self) -> None:
        app_startup.mark_critical_startup_done()
        self.assertTrue(app_startup.critical_startup_done())


if __name__ == "__main__":
    unittest.main()

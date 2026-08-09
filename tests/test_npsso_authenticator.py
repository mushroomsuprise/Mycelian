#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for NPSSO subprocess command/env construction."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.npsso_authenticator import (  # noqa: E402
    NPSSO_HELPER_FLAG,
    _npsso_capture_command_and_env,
    _project_root,
)


class NpssoCaptureCommandTests(unittest.TestCase):
    def test_dev_mode_uses_python_module_flag(self) -> None:
        with mock.patch.object(sys, "executable", "/usr/bin/python3"):
            with mock.patch.object(sys, "frozen", False, create=True):
                cmd, env, root = _npsso_capture_command_and_env()

        self.assertEqual(cmd, ["/usr/bin/python3", "-m", "modules.npsso_webview_capture"])
        self.assertEqual(root, _project_root())
        self.assertIn(str(root), env.get("PYTHONPATH", ""))

    def test_frozen_mode_uses_helper_flag(self) -> None:
        with mock.patch.object(sys, "executable", "/Applications/Mycelian.app/Contents/MacOS/Mycelian"):
            with mock.patch.object(sys, "frozen", True, create=True):
                cmd, env, _root = _npsso_capture_command_and_env()

        self.assertEqual(
            cmd,
            [
                "/Applications/Mycelian.app/Contents/MacOS/Mycelian",
                NPSSO_HELPER_FLAG,
            ],
        )

    def test_frozen_mode_strips_pyinstaller_bootstrap_env(self) -> None:
        polluted = {
            "_MEIPASS2": "/tmp/_MEI123",
            "PYTHONHOME": "/bad",
            "PYTHONPATH": "/bad",
            "_PYI_BOOTSTRAP": "1",
            "HOME": "/Users/test",
        }
        with mock.patch.object(sys, "executable", "/Mycelian.exe"):
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.dict(os.environ, polluted, clear=False):
                    _cmd, env, _root = _npsso_capture_command_and_env()

        for var in ("_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"):
            self.assertNotIn(var, env)
        self.assertEqual(env.get("HOME"), "/Users/test")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""web_engine module import must not load Flask/gevent until WebEngine is constructed."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebEngineLazyImportTests(unittest.TestCase):
    def test_import_web_engine_does_not_load_flask_stack(self) -> None:
        script = r"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(%r).resolve()))
import modules.web_engine as we
loaded = [n for n in ("flask", "flask_socketio", "gevent") if n in sys.modules]
assert not loaded, loaded
assert we.Flask is None
assert we.SocketIO is None
print("ok")
""" % str(ROOT)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()

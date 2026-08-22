#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""_deliver_toast must not flip NiceGUI into script_mode before ui.run."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nicegui import core  # noqa: E402

from modules import notification_engine as ne  # noqa: E402


class DeliverToastScriptModeTests(unittest.TestCase):
    def setUp(self) -> None:
        core.script_mode = False
        core.script_client = None
        self._pending = list(ne._pending_toasts)
        ne._pending_toasts.clear()

    def tearDown(self) -> None:
        core.script_mode = False
        core.script_client = None
        ne._pending_toasts[:] = self._pending

    def test_deliver_before_app_started_queues_without_script_mode(self) -> None:
        with patch.object(ne, "inject_notification_ui_assets"):
            ok = ne._deliver_toast("overlay recovered", {"type": "positive"})
        self.assertFalse(ok)
        self.assertFalse(core.script_mode)
        self.assertIsNone(core.script_client)

    def test_deliver_does_not_call_ui_notify(self) -> None:
        with (
            patch.object(ne, "inject_notification_ui_assets"),
            patch.object(ne.ui, "notify") as ui_notify,
            patch("nicegui.core.app") as app,
        ):
            app.is_started = False
            ok = ne._deliver_toast("hello", {"type": "info"})
        self.assertFalse(ok)
        ui_notify.assert_not_called()
        self.assertFalse(core.script_mode)


if __name__ == "__main__":
    unittest.main()

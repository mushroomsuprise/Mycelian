#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the NiceGUI outbox snapshot patch version gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.nicegui_outbox_patch import (  # noqa: E402
    _PATCH_ATTR,
    _SUPPORTED_VERSIONS,
)


class _FakeDeleted:
    pass


def _make_outbox_module() -> SimpleNamespace:
    deleted_sentinel = _FakeDeleted()

    class Outbox:
        loop = None

    return SimpleNamespace(
        Outbox=Outbox,
        Deleted=_FakeDeleted,
        deleted=deleted_sentinel,
    )


class NiceguiOutboxPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_nicegui = ModuleType("nicegui")
        self.fake_nicegui.__version__ = "3.13.0"
        self.outbox_mod = _make_outbox_module()
        self.outbox_cls = self.outbox_mod.Outbox
        setattr(self.outbox_cls, _PATCH_ATTR, False)

        self.core_mod = ModuleType("nicegui.core")
        self.core_mod.app = SimpleNamespace(handle_exception=MagicMock())
        self.js_component_mod = ModuleType("nicegui.dependencies")
        self.js_component_mod.JsComponent = type("JsComponent", (), {})

        patcher = patch.dict(
            "sys.modules",
            {
                "nicegui": self.fake_nicegui,
                "nicegui.core": self.core_mod,
                "nicegui.outbox": self.outbox_mod,
                "nicegui.dependencies": self.js_component_mod,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("modules.nicegui_outbox_patch", None)

    def test_applies_for_supported_version_3_13_0(self) -> None:
        self.fake_nicegui.__version__ = "3.13.0"
        from modules.nicegui_outbox_patch import ensure_outbox_snapshot_patch

        ensure_outbox_snapshot_patch()

        self.assertTrue(getattr(self.outbox_cls, _PATCH_ATTR, False))
        self.assertIsNotNone(self.outbox_cls.loop)

    def test_applies_for_supported_version_3_12_1(self) -> None:
        self.fake_nicegui.__version__ = "3.12.1"
        from modules.nicegui_outbox_patch import ensure_outbox_snapshot_patch

        ensure_outbox_snapshot_patch()

        self.assertTrue(getattr(self.outbox_cls, _PATCH_ATTR, False))

    def test_skips_untested_version(self) -> None:
        self.fake_nicegui.__version__ = "3.14.0"
        from modules.nicegui_outbox_patch import ensure_outbox_snapshot_patch

        ensure_outbox_snapshot_patch()

        self.assertFalse(getattr(self.outbox_cls, _PATCH_ATTR, False))
        self.assertIsNone(self.outbox_cls.loop)

    def test_supported_versions_include_current_lockfile(self) -> None:
        self.assertIn("3.13.0", _SUPPORTED_VERSIONS)


if __name__ == "__main__":
    unittest.main()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/spore_studio/__init__.py (package re-exports)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.uiwindows.spore_studio as pkg
from modules.uiwindows.spore_studio import editor_view


def test_reexports_editor_entry_points():
    assert pkg.__all__ == ["create_spore_studio_tab", "open_template"]
    assert pkg.create_spore_studio_tab is editor_view.create_spore_studio_tab
    assert pkg.open_template is editor_view.open_template

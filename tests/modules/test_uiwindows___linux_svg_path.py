# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/_linux_svg_path.py (Tux path data)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows import _linux_svg_path  # noqa: E402


def test_path_is_a_nonempty_svg_path_string():
    d = _linux_svg_path.LINUX_SVG_PATH_D
    assert isinstance(d, str) and len(d) > 100
    assert d[0] in "Mm"
    # SVG path data only: commands, numbers, separators.
    assert re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9eE.,\- ]+", d)
    assert '"' not in d and "<" not in d

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/os_brand_icons.py (OS marks: colored vs grey)."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows import os_brand_icons as obi  # noqa: E402
from modules.uiwindows._linux_svg_path import LINUX_SVG_PATH_D  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"


def _fills(svg: str):
    root = ET.fromstring(svg)
    return [p.get("fill") for p in root.iter(SVG_NS + "path")]


def test_svg_single_path_builds_parseable_svg():
    svg = obi._svg_single_path("#123456", "M0 0h1v1z")
    root = ET.fromstring(svg)
    assert root.get("viewBox") == "0 0 24 24"
    assert root.get("aria-hidden") == "true"
    (path,) = list(root.iter(SVG_NS + "path"))
    assert path.get("fill") == "#123456"
    assert path.get("d") == "M0 0h1v1z"


def test_os_brand_row_keys_and_labels():
    assert [(k, label) for _c, _g, k, label in obi.OS_BRAND_ROW] == [
        ("windows", "Windows"),
        ("linux", "Linux"),
        ("darwin", "macOS"),
    ]


@pytest.mark.parametrize("color,grey,key,_label", obi.OS_BRAND_ROW)
def test_colored_and_grey_variants(color, grey, key, _label):
    color_fills = _fills(color)
    grey_fills = _fills(grey)
    assert color_fills and len(color_fills) == len(grey_fills)
    assert set(grey_fills) == {obi._GREY_FLAT}
    assert obi._GREY_FLAT not in color_fills


def test_specific_brand_colors():
    assert _fills(obi._SVG_WINDOWS_COLOR) == ["#F25022", "#7FBA00", "#00A4EF", "#FFB900"]
    assert _fills(obi._SVG_APPLE_COLOR) == [obi._APPLE_LIT_FILL]
    assert _fills(obi._SVG_LINUX_COLOR) == [obi._LINUX_LIT_FILL]
    assert LINUX_SVG_PATH_D in obi._SVG_LINUX_COLOR
    assert obi._APPLE_PATH_D in obi._SVG_APPLE_GREY

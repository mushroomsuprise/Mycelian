# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/service_brand_icons.py (service marks for Settings tabs)."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows import service_brand_icons as sbi  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"

EXPECTED_FILLS = {
    "internet": "#38BDF8",
    "webengine": "#A78BFA",
    "twitch": "#9146FF",
    "youtube": "#FF0000",
    "spotify": "#1DB954",
    "psn": "#0070CC",
    "obs": "#302E31",
    "discord": "#5865F2",
    "streamlabs": "#80F5D2",
    "streamelements": "#2700FF",
}


def test_every_expected_service_is_present():
    assert set(sbi.SERVICE_BRAND_SVG) == set(EXPECTED_FILLS)


@pytest.mark.parametrize("key,fill", sorted(EXPECTED_FILLS.items()))
def test_svgs_parse_and_use_brand_fill(key, fill):
    root = ET.fromstring(sbi.SERVICE_BRAND_SVG[key])
    assert root.tag == SVG_NS + "svg"
    assert root.get("width") == "1.25em" and root.get("height") == "1.25em"
    fills = [p.get("fill") for p in root.iter(SVG_NS + "path")]
    assert fill in fills


def test_obs_mark_has_halo():
    assert sbi.OBS_HALO_ATTRS in sbi.SERVICE_BRAND_SVG["obs"]
    root = ET.fromstring(sbi.SERVICE_BRAND_SVG["obs"])
    first = next(root.iter(SVG_NS + "path"))
    assert first.get("stroke") == "#FFFFFF"
    assert first.get("paint-order") == "stroke"
    # Only the OBS mark carries the halo.
    assert all(sbi.OBS_HALO_ATTRS not in v for k, v in sbi.SERVICE_BRAND_SVG.items() if k != "obs")


@pytest.mark.parametrize("key", sorted(EXPECTED_FILLS))
def test_service_tab_icon_is_quasar_data_uri(key):
    icon = sbi.service_tab_icon(key)
    prefix = "img:data:image/svg+xml;charset=UTF-8,"
    assert icon.startswith(prefix)
    encoded = icon[len(prefix):]
    # Fully percent-encoded (safe=""): no raw markup characters or spaces.
    assert not any(ch in encoded for ch in '<>" /#')
    assert unquote(encoded) == sbi.SERVICE_BRAND_SVG[key]


def test_service_tab_icon_unknown_key_raises():
    with pytest.raises(KeyError):
        sbi.service_tab_icon("myspace")

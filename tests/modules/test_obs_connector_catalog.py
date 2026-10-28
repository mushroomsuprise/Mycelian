# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""obs_connector_catalog: catalog entries are well formed and indexed by id."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import obs_connector_catalog as cat


def test_catalog_entries_well_formed():
    ids = [e["id"] for e in cat.OBS_CONNECTOR_CATALOG]
    assert ids and len(ids) == len(set(ids))
    for entry in cat.OBS_CONNECTOR_CATALOG:
        assert entry["label"].startswith("OBS") and isinstance(entry["description"], str)
        assert isinstance(entry["args"], list)
        for arg in entry["args"]:
            assert arg["name"] and arg["type"] and "label" in arg


def test_catalog_by_id():
    by_id = cat.obs_connector_catalog_by_id()
    assert set(by_id) == {e["id"] for e in cat.OBS_CONNECTOR_CATALOG}
    assert by_id["toggle_record"]["label"] == "OBS — Toggle recording"
    assert by_id["stop_record"]["args"] == []

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/data_source_registry.py (counter/data sources)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spore_studio import data_source_registry as dsr


def test_src_defaults_and_flags():
    assert dsr._src("a", "A", category="C") == {
        "id": "a", "label": "A", "category": "C", "value_type": "number",
        "description": "", "delta_only": False, "display_only": False,
    }
    row = dsr._src("b", "B", category="C", value_type="string", description="d",
                   delta_only=True, display_only=True)
    assert row["value_type"] == "string" and row["delta_only"] and row["display_only"]


def test_registry_shape():
    reg = dsr.get_data_source_registry()
    json.dumps(reg)
    assert set(reg) == {"sources", "categories", "alert_payload_keys", "chat_payload_keys",
                        "alert_types", "session_stat_keys"}
    ids = [s["id"] for s in reg["sources"]]
    assert len(ids) == len(set(ids)) == len(dsr._DATA_SOURCES)
    # categories are unique, in first-seen order
    expected = []
    for s in dsr._DATA_SOURCES:
        if s["category"] not in expected:
            expected.append(s["category"])
    assert reg["categories"] == expected
    assert reg["chat_payload_keys"]["chat.username"] == "username"
    assert [r["id"] for r in reg["alert_types"]][0] == "follow"


def test_sources_annotated_with_alert_types():
    rows = {r["id"]: r for r in dsr._annotated_sources()}
    assert rows["alert.amount"]["alert_types"] == ["donation"]
    for sid, row in rows.items():
        key = dsr.ALERT_PAYLOAD_KEYS.get(sid)
        if not key or key not in dsr.ALERT_FIELD_TYPES:
            assert "alert_types" not in row
    # annotation must not leak into the module constants
    assert all("alert_types" not in s for s in dsr._DATA_SOURCES)


def test_registry_category_fallback(monkeypatch):
    monkeypatch.setattr(dsr, "_DATA_SOURCES", [{"id": "x", "category": ""}, {"id": "y"}])
    assert dsr.get_data_source_registry()["categories"] == ["Other"]


def test_registry_returns_copies():
    reg = dsr.get_data_source_registry()
    reg["alert_payload_keys"]["new"] = "x"
    reg["alert_types"][0]["label"] = "mutated"
    assert "new" not in dsr.ALERT_PAYLOAD_KEYS
    assert dsr.ALERT_TYPES[0]["label"] == "Follow"


def test_get_data_source():
    row = dsr.get_data_source("alert.amount")
    assert row["category"] == "Alert payload"
    row["label"] = "x"
    assert dsr.get_data_source("alert.amount")["label"] == "Donation amount"
    assert dsr.get_data_source("nope") == {}

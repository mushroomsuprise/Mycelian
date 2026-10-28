# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/event_registry.py (binding picker events/actions)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json

import pytest

from modules.spore_studio import data_source_registry as dsr
from modules.spore_studio import event_registry as er
from modules.spore_studio import template_outputs


@pytest.fixture
def no_outputs(monkeypatch):
    monkeypatch.setattr(template_outputs, "merge_into_events", lambda events: events)


def test_registry_shape_and_json_serializable(no_outputs):
    reg = er.get_event_registry()
    assert set(reg) == {"events", "actions"}
    json.dumps(reg)
    names = [e["event"] for e in reg["events"]]
    assert names[0] == "next_alert" and "instant_alert" in names
    assert len(names) == len(set(names))
    actions = [a["action"] for a in reg["actions"]]
    assert {"show_for", "wheel_spin", "list_clear"} <= set(actions)
    assert len(actions) == len(set(actions))
    for ev in reg["events"]:
        assert ev["label"] and isinstance(ev.get("payload", []), list)


def test_registry_merges_template_outputs(monkeypatch):
    seen = {}

    def fake_merge(events):
        seen["n"] = len(events)
        return events + [{"event": "wheel_result", "origin": "template"}]

    monkeypatch.setattr(template_outputs, "merge_into_events", fake_merge)
    reg = er.get_event_registry()
    assert seen["n"] == len(er._EVENTS)
    assert reg["events"][-1]["event"] == "wheel_result"


def test_registry_returns_copies(no_outputs):
    reg = er.get_event_registry()
    reg["events"][0]["label"] = "mutated"
    reg["actions"][0]["label"] = "mutated"
    assert er._EVENTS[0]["label"] != "mutated"
    assert er._ACTIONS[0]["label"] != "mutated"


def test_alert_fields_annotated_with_types():
    ev = er.get_event("next_alert")
    by_key = {f["key"]: f for f in ev["payload"]}
    assert by_key["tier"]["alert_types"] == dsr.ALERT_FIELD_TYPES["tier"]
    assert "alert_types" not in by_key["username"]
    other = [e for e in er._EVENTS if e["event"] not in ("next_alert", "instant_alert")]
    for ev in other:
        for f in ev.get("payload", []):
            if f.get("key") == "tier":
                assert "alert_types" not in f or ev["event"] in ("next_alert", "instant_alert")


def test_annotate_is_idempotent():
    before = json.dumps(er._EVENTS, sort_keys=True)
    er._annotate_alert_fields()
    assert json.dumps(er._EVENTS, sort_keys=True) == before


def test_get_event_and_action():
    assert er.get_event("next_alert")["alert_system"] == "queue"
    assert er.get_event("nope") == {}
    act = er.get_action("show_for")
    assert act["action"] == "show_for"
    act["label"] = "x"
    assert er.get_action("show_for")["label"] != "x"
    assert er.get_action("nope") == {}

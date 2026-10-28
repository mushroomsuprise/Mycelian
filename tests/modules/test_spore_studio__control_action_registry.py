# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/control_action_registry.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spore_studio import control_action_registry as car


def test_act_defaults():
    row = car._act("x", "X", handler="h")
    assert row == {"action": "x", "label": "X", "handler": "h", "description": "", "params": []}


def test_act_with_params():
    params = [{"key": "k"}]
    row = car._act("x", "X", handler="h", description="d", params=params)
    assert row["params"] is params
    assert row["description"] == "d"


def test_registry_shape_and_copies():
    reg = car.get_control_action_registry()
    assert set(reg) == {"actions", "control_types"}
    names = [a["action"] for a in reg["actions"]]
    assert "counter_adjust" in names and "websocket_emit" in names
    assert len(names) == len(set(names))
    types = [t["type"] for t in reg["control_types"]]
    assert types[0] == "button" and "timer_control" in types
    reg["actions"][0]["label"] = "mutated"
    reg["control_types"][0]["label"] = "mutated"
    again = car.get_control_action_registry()
    assert again["actions"][0]["label"] != "mutated"
    assert again["control_types"][0]["label"] == "Button"


def test_every_action_has_known_handler():
    handlers = {a["handler"] for a in car.get_control_action_registry()["actions"]}
    assert handlers <= {
        "global_socket", "counter_adjust", "element_binding", "set_config_value",
        "twitch_api", "websocket_emit", "streamdeck_forward", "template_socket",
    }


def test_get_control_action_found_is_copy():
    row = car.get_control_action("counter_adjust")
    assert row["handler"] == "counter_adjust"
    assert row["params"][1]["options"] == ["increment", "decrement", "set", "reset"]
    row["handler"] = "x"
    assert car.get_control_action("counter_adjust")["handler"] == "counter_adjust"


def test_get_control_action_unknown():
    assert car.get_control_action("nope") == {}
    assert car.get_control_action("") == {}

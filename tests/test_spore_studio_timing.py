# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Spore Studio duration calculation."""

from modules.spore_studio.timing import (
    compute_min_duration_seconds,
    effective_duration_seconds,
    element_timeline_ms,
)


def test_empty_model_defaults_to_five_seconds():
    assert compute_min_duration_seconds({}) == 5.0


def test_show_for_binding_sets_minimum():
    model = {
        "elements": [
            {
                "id": "box",
                "type": "text",
                "bindings": [
                    {
                        "event": "next_alert",
                        "action": "show_for",
                        "args": {"seconds": 3, "anim_in": "none", "anim_out": "none"},
                    }
                ],
            }
        ]
    }
    assert compute_min_duration_seconds(model) == 3.0


def test_element_animations_contribute():
    model = {
        "elements": [
            {
                "id": "box",
                "type": "text",
                "animations": {
                    "anim_in": "fade",
                    "anim_out": "fade",
                    "anim_in_ms": 500,
                    "anim_out_ms": 400,
                    "anim_delay_ms": 100,
                },
            }
        ]
    }
    assert element_timeline_ms(model["elements"][0]) == 1000
    assert compute_min_duration_seconds(model) == 1.0


def test_effective_duration_clamps_below_minimum():
    model = {
        "duration_seconds": 1,
        "elements": [
            {
                "id": "box",
                "type": "text",
                "bindings": [
                    {
                        "action": "show_for",
                        "args": {"seconds": 8},
                    }
                ],
            }
        ],
    }
    assert effective_duration_seconds(model) == 8.0


def test_effective_duration_keeps_user_override_above_minimum():
    model = {
        "duration_seconds": 12,
        "elements": [
            {
                "id": "box",
                "type": "text",
                "animations": {"anim_in": "none", "anim_out": "none"},
            }
        ],
    }
    assert effective_duration_seconds(model) == 12.0

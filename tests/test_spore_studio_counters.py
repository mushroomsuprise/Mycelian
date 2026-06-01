#!/usr/bin/env python3
"""Tests for Spore Studio counters, data displays, and registries."""

from __future__ import annotations

import unittest

from modules.spore_studio import (
    control_action_registry,
    data_source_registry,
    spore_data_codegen,
    template_codegen,
)
from modules.spore_studio.behavior_blocks import compile_bindings


class SporeStudioDataFeaturesTests(unittest.TestCase):
    def test_data_source_registry_has_alert_sources(self):
        reg = data_source_registry.get_data_source_registry()
        ids = {s["id"] for s in reg["sources"]}
        self.assertIn("alert.quantity", ids)
        self.assertIn("fixed", ids)

    def test_control_action_registry_has_pause(self):
        reg = control_action_registry.get_control_action_registry()
        actions = {a["action"] for a in reg["actions"]}
        self.assertIn("pause_alerts", actions)
        self.assertIn("counter_adjust", actions)

    def test_compile_counter_and_display_js(self):
        model = {
            "template_name": "test_counter_tpl",
            "alert_system": "instant",
            "design": {"width": 400, "height": 100},
            "elements": [
                {
                    "id": "subs",
                    "type": "text",
                    "category": "Elements",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"text": "0", "font_size": 24, "color": "#fff"},
                    "bindings": [],
                    "counter": {
                        "counter_id": "subs",
                        "initial_value": 0,
                        "format": "Subs: {value}",
                        "persist": True,
                        "database_path": "test_counter_tpl/counters",
                        "database_key": "subs",
                        "rules": [
                            {
                                "trigger": "event",
                                "event": "instant_alert",
                                "filter": {"alert_type": "sub"},
                                "operation": "increment",
                                "delta": {"kind": "fixed", "value": 1},
                            }
                        ],
                    },
                },
                {
                    "id": "name",
                    "type": "text",
                    "category": "Elements",
                    "text_mode": "data_display",
                    "position": {"x": 0, "y": 50},
                    "size": {"w": 200, "h": 40},
                    "props": {"text": "—", "font_size": 20, "color": "#fff"},
                    "bindings": [],
                    "data_display": {
                        "source": {"kind": "data_source", "source": "alert.username"},
                        "format": "@{value}",
                        "refresh_on": ["instant_alert"],
                        "default_text": "—",
                    },
                },
            ],
            "dynamic_controls": {
                "elements": [
                    {
                        "type": "button",
                        "id": "pause_btn",
                        "label": "Pause",
                        "action": "pause_alerts",
                        "button_text": "Pause",
                    }
                ]
            },
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn("sporeCounterMeta", js)
        self.assertIn("sporeDataDisplays", js)
        self.assertIn("test_counter_tpl_pause_alerts", js)
        self.assertIn("instant_alert", js)

        html, cfg = template_codegen.compile_model(model)
        self.assertIn('data-spore-text-mode="counter"', html)
        self.assertIn('data-spore-text-mode="data_display"', html)
        self.assertIn("sporeResolveSource", html)
        self.assertIn("dynamic_controls", cfg)
        self.assertEqual(len(cfg["dynamic_controls"]["elements"]), 1)

    def test_counter_adjust_binding_compiles(self):
        elements = [
            {
                "id": "btn",
                "type": "text",
                "bindings": [
                    {
                        "event": "instant_alert",
                        "filter": {},
                        "action": "counter_adjust",
                        "args": {
                            "counter_id": "subs",
                            "operation": "increment",
                            "delta_kind": "fixed",
                            "delta_value": 2,
                        },
                    }
                ],
            }
        ]
        out = compile_bindings(elements)
        self.assertIn("sporeCounterAdjust", out["js"])


if __name__ == "__main__":
    unittest.main()

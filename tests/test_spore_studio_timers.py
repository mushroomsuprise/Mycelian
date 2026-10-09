#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Spore Studio timer bounds, formats, controls, and connector actions."""

from __future__ import annotations

import unittest

from modules.spore_studio import spore_data_codegen, template_codegen
from modules.spore_studio.control_action_registry import get_control_action_registry


def _timer_model(**props):
    base_props = {
        "mode": "count_down",
        "start_seconds": 90,
        "limit_seconds": 0,
        "auto_start": True,
        "format": "{mm}:{ss}",
        "font_size": 32,
        "color": "#ffffff",
    }
    base_props.update(props)
    return {
        "template_name": "race_timer",
        "alert_system": "queue",
        "title": "Race timer",
        "design": {"width": 800, "height": 200},
        "elements": [
            {
                "id": "subathon",
                "type": "timer",
                "category": "Timers",
                "position": {"x": 0, "y": 0},
                "size": {"w": 160, "h": 48},
                "props": base_props,
                "bindings": [],
            }
        ],
        "dynamic_controls": {
            "elements": [
                {
                    "type": "timer_control",
                    "id": "subathon_timer_controls",
                    "label": "Timer subathon",
                    "action": "timer_control",
                    "target_timer_id": "subathon",
                    "step": 4,
                    "button_text": "Go",
                }
            ]
        },
    }


class SporeStudioTimerTests(unittest.TestCase):
    def test_start_falls_back_to_duration_and_limit_defaults_open(self):
        self.assertEqual(
            spore_data_codegen.timer_start_seconds({"duration_seconds": 45}),
            45,
        )
        self.assertEqual(
            spore_data_codegen.timer_start_seconds({"start_seconds": 0, "duration_seconds": 45}),
            0,
        )
        self.assertEqual(spore_data_codegen.timer_limit_seconds({}), 0)
        self.assertEqual(
            spore_data_codegen.timer_limit_seconds({"limit_seconds": 120}),
            120,
        )
        self.assertEqual(spore_data_codegen.timer_mode({"mode": "nope"}), "count_down")

    def test_codegen_embeds_bounds_format_and_socket_events(self):
        down = _timer_model()
        js = spore_data_codegen.compile_spore_data_features(down)
        self.assertIn("subathon_start_seconds", js)
        self.assertIn("subathon_limit_seconds", js)
        self.assertIn("subathon_format", js)
        self.assertIn("race_timer_subathon_start", js)
        self.assertIn("race_timer_subathon_pause", js)
        self.assertIn("race_timer_subathon_reset", js)
        self.assertIn("sporeTimerStart", js)
        self.assertNotIn("sporeDispatchControlAction('timer_control'", js)

        up = _timer_model(mode="count_up", limit_seconds=600, format="{total}")
        up_js = spore_data_codegen.compile_spore_data_features(up)
        self.assertIn('"limit_seconds": {{ subathon_limit_seconds|default(600)|tojson }}', up_js)
        self.assertIn("{total}", up_js)

    def test_public_config_has_settings_controls_and_connector_actions(self):
        _html, cfg = template_codegen.compile_model(_timer_model())
        ids = {
            el["id"]
            for el in cfg.get("elements", [])
            if isinstance(el, dict) and "id" in el
        }
        self.assertIn("subathon_mode", ids)
        self.assertIn("subathon_start_seconds", ids)
        self.assertIn("subathon_limit_seconds", ids)
        self.assertIn("subathon_format", ids)
        self.assertIn("subathon_auto_start", ids)

        start = next(el for el in cfg["elements"] if el.get("id") == "subathon_start_seconds")
        self.assertEqual(start["value"], 90)
        limit = next(el for el in cfg["elements"] if el.get("id") == "subathon_limit_seconds")
        self.assertEqual(limit["value"], 0)

        controls = cfg["dynamic_controls"]["elements"]
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["type"], "timer_control")
        self.assertEqual(controls[0]["target_timer_id"], "subathon")
        self.assertEqual(controls[0]["action"], "timer_control")
        self.assertNotIn("step", controls[0])
        self.assertNotIn("button_text", controls[0])

        actions = cfg["connector_actions"]
        self.assertEqual(
            set(actions),
            {"subathon_start", "subathon_pause", "subathon_reset"},
        )
        self.assertEqual(actions["subathon_start"]["action_name"], "Start subathon")
        self.assertEqual(actions["subathon_pause"]["trigger"], "subathon_pause")
        self.assertEqual(actions["subathon_reset"]["action_name"], "Reset subathon")

    def test_timer_control_is_a_source_control_type(self):
        types = {row["type"] for row in get_control_action_registry()["control_types"]}
        self.assertIn("timer_control", types)

    def test_html_uses_jinja_defaults_for_timer_fields(self):
        html, _cfg = template_codegen.compile_model(
            _timer_model(mode="count_up", start_seconds=15, limit_seconds=40, format="{h}:{mm}:{ss}")
        )
        self.assertIn("data-spore-type=\"timer\"", html)
        self.assertIn("subathon_mode|default('count_up')", html)
        self.assertIn("subathon_start_seconds|default(15)", html)
        self.assertIn("subathon_limit_seconds|default(40)", html)
        self.assertIn("subathon_format|default('{h}:{mm}:{ss}')", html)

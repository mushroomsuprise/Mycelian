#!/usr/bin/env python3
"""Spore Studio: multi-binding codegen and streamdeck_options in derived JSON.

Run from repo root: python tests/test_spore_studio_streamdeck.py
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.spore_studio import template_codegen
from modules.spore_studio.behavior_blocks import compile_bindings


class TestCompileBindings(unittest.TestCase):
    def test_multiple_bindings_same_element(self):
        elements = [
            {
                "id": "box",
                "bindings": [
                    {
                        "event": "pause_status_update",
                        "filter": {"paused": True},
                        "action": "show",
                        "args": {},
                    },
                    {
                        "event": "pause_status_update",
                        "filter": {"paused": False},
                        "action": "hide",
                        "args": {},
                    },
                ],
            }
        ]
        out = compile_bindings(elements)
        js = out["js"]
        self.assertIn("pause_status_update", js)
        self.assertIn("sporeShow", js)
        self.assertIn("sporeHide", js)
        self.assertIn("paused", js)

    def test_derived_json_includes_streamdeck(self):
        model = {
            "template_name": "t1",
            "alert_system": "queue",
            "design": {"width": 800, "height": 200},
            "elements": [],
            "streamdeck_options": {
                "description": "d",
                "actions": {
                    "go": {
                        "name": "Go",
                        "description": "",
                        "event": "my_ev",
                        "default_data": {"x": 1},
                    }
                },
            },
        }
        _html, cfg = template_codegen.compile_model(model, existing_html=None)
        self.assertIn("streamdeck_options", cfg)
        self.assertEqual(cfg["streamdeck_options"]["description"], "d")
        self.assertIn("go", cfg["streamdeck_options"]["actions"])
        self.assertEqual(cfg["streamdeck_options"]["actions"]["go"]["event"], "my_ev")


if __name__ == "__main__":
    unittest.main()

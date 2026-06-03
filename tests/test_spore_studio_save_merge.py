#!/usr/bin/env python3
"""Tests for Spore Studio save-time template config merge behavior."""

from __future__ import annotations

import unittest

from modules.spore_studio.save_pipeline import _merge_config


class SporeStudioSaveMergeTests(unittest.TestCase):
    def test_merge_preserves_user_values_except_canvas_dims(self):
        new_config = {
            "template_name": "demo",
            "elements": [
                {
                    "type": "number",
                    "id": "DesignWidth",
                    "value": 320,
                },
                {
                    "type": "number",
                    "id": "DesignHeight",
                    "value": 240,
                },
                {
                    "type": "text",
                    "id": "titleText",
                    "value": "New title",
                },
            ],
        }
        old_config = {
            "template_name": "demo",
            "elements": [
                {
                    "type": "number",
                    "id": "DesignWidth",
                    "value": 1920,
                },
                {
                    "type": "number",
                    "id": "DesignHeight",
                    "value": 1080,
                },
                {
                    "type": "text",
                    "id": "titleText",
                    "value": "User customized",
                },
            ],
        }
        merged = _merge_config(new_config, old_config)
        by_id = {el["id"]: el for el in merged["elements"]}
        self.assertEqual(by_id["DesignWidth"]["value"], 320)
        self.assertEqual(by_id["DesignHeight"]["value"], 240)
        self.assertEqual(by_id["titleText"]["value"], "User customized")


if __name__ == "__main__":
    unittest.main()

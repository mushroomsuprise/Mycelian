#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Spore Studio save-time template config merge behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.spore_studio.save_pipeline import _merge_config, save_template


class SporeStudioSaveMergeTests(unittest.TestCase):
    def test_merge_preserves_user_values_for_standalone_tool(self):
        """_merge_config is still used by merge_template_configs.py (legacy workflow)."""
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

    def test_merge_updates_counter_format_from_codegen(self):
        new_config = {
            "elements": [
                {
                    "type": "text",
                    "id": "counter1_format",
                    "value": "{value}/{max}",
                },
            ],
        }
        old_config = {
            "elements": [
                {
                    "type": "text",
                    "id": "counter1_format",
                    "value": "{value}",
                },
            ],
        }
        merged = _merge_config(new_config, old_config)
        self.assertEqual(merged["elements"][0]["value"], "{value}/{max}")

    def test_save_template_overwrites_stale_source_settings_values(self):
        model = {
            "template_name": "save_overwrite_test",
            "alert_system": "instant",
            "design": {"width": 400, "height": 100},
            "elements": [
                {
                    "id": "cnt",
                    "type": "text",
                    "category": "Counter",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"color": "#ff00ff", "font_size": 48},
                    "bindings": [],
                    "counter": {
                        "counter_id": "cnt",
                        "initial_value": 0,
                        "format": "{value}/{max}",
                        "max": 75,
                        "min": 0,
                        "rules": [],
                    },
                    "source_settings_expose": {"color": True, "font_size": True},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            configs = templates / "template_configs"
            spore = templates / "_spore"
            configs.mkdir(parents=True)
            spore.mkdir(parents=True)
            (templates / "save_overwrite_test.html").write_text(
                "<html><!-- SPORE_STUDIO:dom-begin --><!-- SPORE_STUDIO:dom-end -->"
                "<!-- SPORE_STUDIO:auto-begin --><!-- SPORE_STUDIO:auto-end -->"
                "<!-- SPORE_STUDIO:user-begin --><!-- SPORE_STUDIO:user-end -->"
                "<!-- SPORE_STUDIO:styles-begin --><!-- SPORE_STUDIO:styles-end -->"
                "<!-- SPORE_STUDIO:data-runtime-begin --><!-- SPORE_STUDIO:data-runtime-end -->"
                "</html>",
                encoding="utf-8",
            )
            stale_config = {
                "template_name": "save_overwrite_test",
                "spore_studio": True,
                "elements": [
                    {"type": "separator", "label": "Canvas"},
                    {
                        "type": "number",
                        "id": "DesignWidth",
                        "value": 1920,
                    },
                    {
                        "type": "color",
                        "id": "cnt_color",
                        "value": "#000000",
                    },
                    {
                        "type": "number",
                        "id": "cnt_font_size",
                        "value": 12,
                    },
                    {
                        "type": "text",
                        "id": "cnt_format",
                        "value": "{value}",
                    },
                ],
            }
            config_path = configs / "save_overwrite_test.json"
            config_path.write_text(json.dumps(stale_config), encoding="utf-8")

            def fake_get_template_path(name=None):
                if name is None:
                    return str(templates)
                return str(templates / name)

            def fake_get_data_path(rel_path):
                if rel_path == "templates/template_configs":
                    return str(configs)
                return str(templates / rel_path)

            with mock.patch(
                "modules.path_utils.get_template_path",
                side_effect=fake_get_template_path,
            ), mock.patch(
                "modules.spore_studio.save_pipeline.get_template_path",
                side_effect=fake_get_template_path,
            ), mock.patch(
                "modules.spore_studio.template_codegen.get_template_path",
                side_effect=fake_get_template_path,
            ), mock.patch(
                "modules.spore_studio.template_parser_back.get_template_path",
                side_effect=fake_get_template_path,
            ), mock.patch(
                "modules.path_utils.get_data_path",
                side_effect=fake_get_data_path,
            ), mock.patch(
                "modules.template_config_parser.get_data_path",
                side_effect=fake_get_data_path,
            ), mock.patch(
                "modules.spore_studio.save_pipeline.ensure_template_assets_folder",
            ):
                save_template(model)

            written = json.loads(config_path.read_text(encoding="utf-8"))
            by_id = {
                el["id"]: el
                for el in written.get("elements", [])
                if isinstance(el, dict) and el.get("id")
            }
            self.assertEqual(by_id["DesignWidth"]["value"], 400)
            self.assertEqual(by_id["cnt_color"]["value"], "#ff00ff")
            self.assertEqual(by_id["cnt_font_size"]["value"], 48)
            self.assertEqual(by_id["cnt_format"]["value"], "{value}/{max}")


if __name__ == "__main__":
    unittest.main()

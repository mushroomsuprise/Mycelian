#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for template preview settings persistence and OBS URL matching."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from modules import obs_browser_source_match as obs_match
from modules import template_preview_settings as tps


class TemplatePreviewSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._path = os.path.join(self._tmpdir.name, "template_preview_settings.json")
        self._patcher = mock.patch.object(tps, "settings_path", return_value=self._path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_clamp_preview_resolution(self):
        self.assertEqual(tps.clamp_preview_resolution(1280, 720), (1280, 720))
        self.assertEqual(
            tps.clamp_preview_resolution(10, 10),
            (tps.PREVIEW_WIDTH_MIN, tps.PREVIEW_HEIGHT_MIN),
        )
        self.assertEqual(
            tps.clamp_preview_resolution(9000, 5000),
            (tps.PREVIEW_WIDTH_MAX, tps.PREVIEW_HEIGHT_MAX),
        )
        self.assertIsNone(tps.clamp_preview_resolution("x", 720))
        self.assertIsNone(tps.clamp_preview_resolution(0, 720))

    def test_get_missing_returns_none(self):
        self.assertIsNone(tps.get_template_preview_resolution("ff7"))

    def test_set_and_get_merge_per_key(self):
        self.assertTrue(tps.set_template_preview_resolution("ff7", 1280, 720))
        self.assertTrue(tps.set_template_preview_resolution("chat", 800, 600))
        self.assertEqual(tps.get_template_preview_resolution("ff7"), (1280, 720))
        self.assertEqual(tps.get_template_preview_resolution("chat"), (800, 600))
        with open(self._path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertIn("ff7", raw["resolutions"])
        self.assertIn("chat", raw["resolutions"])

    def test_clear_one_key(self):
        tps.set_template_preview_resolution("ff7", 1280, 720)
        tps.set_template_preview_resolution("chat", 800, 600)
        self.assertTrue(tps.clear_template_preview_resolution("ff7"))
        self.assertIsNone(tps.get_template_preview_resolution("ff7"))
        self.assertEqual(tps.get_template_preview_resolution("chat"), (800, 600))


class ObsBrowserSourceMatchTests(unittest.TestCase):
    def test_path_match_localhost_port(self):
        self.assertTrue(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5000/ff7", "ff7", overlay_port=5000
            )
        )
        self.assertTrue(
            obs_match.browser_url_matches_route(
                "http://localhost:5000/ff7?__preview_token=x", "ff7", overlay_port=5000
            )
        )
        self.assertTrue(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5000/ff7/", "ff7", overlay_port=5000
            )
        )
        self.assertTrue(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5000/ff7.html", "ff7", overlay_port=5000
            )
        )
        self.assertTrue(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5000/FF7", "ff7", overlay_port=5000
            )
        )

    def test_wrong_path_or_port(self):
        self.assertFalse(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5000/chat", "ff7", overlay_port=5000
            )
        )
        self.assertFalse(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5001/ff7", "ff7", overlay_port=5000
            )
        )
        self.assertTrue(
            obs_match.browser_url_matches_route(
                "http://127.0.0.1:5001/ff7",
                "ff7",
                overlay_port=5000,
                require_port=False,
            )
        )

    def test_coerce_browser_wh_with_defaults(self):
        self.assertEqual(
            obs_match.coerce_browser_wh({"url": "http://x"}, {"width": 800, "height": 600}),
            (800, 600),
        )
        self.assertEqual(
            obs_match.coerce_browser_wh(
                {"width": 1920, "height": 1080}, {"width": 800, "height": 600}
            ),
            (1920, 1080),
        )

    def test_pick_same_size(self):
        matches = [
            {"source_name": "A", "width": 800, "height": 600},
            {"source_name": "B", "width": 800, "height": 600},
        ]
        picked = obs_match.pick_browser_source_size(matches)
        self.assertEqual(picked["source_name"], "A")

    def test_pick_program_scene_when_conflict(self):
        matches = [
            {"source_name": "A", "width": 800, "height": 600},
            {"source_name": "B", "width": 1920, "height": 1080},
        ]
        picked = obs_match.pick_browser_source_size(
            matches, program_source_names={"B"}
        )
        self.assertEqual(picked["source_name"], "B")

    def test_pick_ambiguous_returns_none(self):
        matches = [
            {"source_name": "A", "width": 800, "height": 600},
            {"source_name": "B", "width": 1920, "height": 1080},
        ]
        self.assertIsNone(obs_match.pick_browser_source_size(matches))


if __name__ == "__main__":
    unittest.main()

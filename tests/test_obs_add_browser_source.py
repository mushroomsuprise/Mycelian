#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""OBS browser-source create payload and duplicate-name avoidance."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.obs_browser_source_match import (  # noqa: E402
    browser_urls_same_template,
    scene_has_template_url,
)
from modules.obs_service import (  # noqa: E402
    ObsServiceImpl,
    adjusted_obs_input_name,
    browser_source_input_settings,
    unique_obs_input_name,
)


class BrowserSourceSettingsTests(unittest.TestCase):
    def test_css_is_empty_and_fps_is_omitted_unless_custom(self) -> None:
        settings = browser_source_input_settings(
            "http://127.0.0.1:5000/alerts",
            1920,
            1080,
            reroute_audio=False,
            fps_custom=False,
            fps=60,
            shutdown=True,
            restart_when_active=False,
        )
        self.assertEqual(settings["css"], "")
        self.assertEqual(settings["url"], "http://127.0.0.1:5000/alerts")
        self.assertEqual(settings["width"], 1920)
        self.assertEqual(settings["height"], 1080)
        self.assertFalse(settings["reroute_audio"])
        self.assertFalse(settings["fps_custom"])
        self.assertTrue(settings["shutdown"])
        self.assertFalse(settings["restart_when_active"])
        self.assertNotIn("fps", settings)

    def test_custom_frame_rate_includes_fps(self) -> None:
        settings = browser_source_input_settings(
            "http://127.0.0.1:5000/chat",
            1280,
            720,
            reroute_audio=True,
            fps_custom=True,
            fps=60,
            shutdown=False,
            restart_when_active=True,
        )
        self.assertEqual(settings["css"], "")
        self.assertTrue(settings["reroute_audio"])
        self.assertTrue(settings["fps_custom"])
        self.assertEqual(settings["fps"], 60)
        self.assertFalse(settings["shutdown"])
        self.assertTrue(settings["restart_when_active"])


class TemplateUrlMatchTests(unittest.TestCase):
    def test_same_path_and_port_match_across_local_hosts(self) -> None:
        card = "http://127.0.0.1:5000/alerts"
        obs = "http://localhost:5000/alerts?refresh=1"
        self.assertTrue(browser_urls_same_template(card, obs))
        self.assertTrue(scene_has_template_url([obs, "http://127.0.0.1:5000/chat"], card))

    def test_different_path_or_port_does_not_match(self) -> None:
        card = "http://127.0.0.1:5000/alerts"
        self.assertFalse(browser_urls_same_template(card, "http://127.0.0.1:5000/chat"))
        self.assertFalse(browser_urls_same_template(card, "http://127.0.0.1:5001/alerts"))
        self.assertFalse(scene_has_template_url(["http://127.0.0.1:5000/chat"], card))


class UniqueInputNameTests(unittest.TestCase):
    def test_uses_base_when_free(self) -> None:
        self.assertEqual(unique_obs_input_name("Alerts", ["Chat"]), "Alerts")

    def test_appends_next_free_suffix(self) -> None:
        existing = ["Alerts", "Alerts 2", "Chat"]
        self.assertEqual(unique_obs_input_name("Alerts", existing), "Alerts 3")

    def test_blank_base_uses_mycelian(self) -> None:
        self.assertEqual(unique_obs_input_name("  ", ["Mycelian"]), "Mycelian 2")

    def test_adjustment_explains_the_suffix(self) -> None:
        name, message = adjusted_obs_input_name("Alerts", ["Alerts", "Alerts 2"])
        self.assertEqual(name, "Alerts 3")
        self.assertIn('"Alerts"', message)
        self.assertIn('"Alerts 3"', message)
        self.assertIn("already exists", message)

    def test_adjustment_is_silent_when_the_name_is_free(self) -> None:
        name, message = adjusted_obs_input_name("Chat", ["Alerts"])
        self.assertEqual(name, "Chat")
        self.assertEqual(message, "")


class _FakeObsClient:
    def __init__(self, names):
        self.names = list(names)
        self.created = None
        self.sent = None

    def get_input_list(self, kind=None):
        return {"inputs": [{"inputName": name} for name in self.names]}

    def create_input(self, scene_name, input_name, input_kind, input_settings, enabled):
        self.created = (scene_name, input_name, input_kind, input_settings, enabled)
        self.names.append(input_name)


class _KeywordOnlyClient(_FakeObsClient):
    def create_input(self, *args, **kwargs):
        if args:
            raise TypeError("keyword only")
        self.created = kwargs


class _ScanClient:
    def get_input_list(self, kind=None):
        rows = [
            {"inputName": "Alerts", "inputKind": "browser_source"},
            {"inputName": "Webcam", "inputKind": "av_capture"},
        ]
        if kind == "browser_source":
            return {
                "inputs": [
                    row
                    for row in rows
                    if str(row["inputKind"]).startswith("browser_source")
                ]
            }
        return {"inputs": rows}

    def get_scene_list(self):
        return {"scenes": [{"sceneName": "Gameplay"}, {"sceneName": "BRB"}]}

    def get_input_settings(self, name):
        urls = {"Alerts": "http://127.0.0.1:5000/alerts"}
        return {"inputSettings": {"url": urls.get(name, "")}}

    def get_scene_item_list(self, scene):
        if scene == "Gameplay":
            return {
                "sceneItems": [
                    {"sourceName": "Alerts"},
                    {"sourceName": "Webcam"},
                ]
            }
        return {"sceneItems": [{"sourceName": "Webcam"}]}


class SceneBrowserUrlTests(unittest.TestCase):
    def test_urls_are_grouped_by_scene_not_by_source_name(self) -> None:
        service = ObsServiceImpl()
        service._req_client = _ScanClient()
        payload = service._scene_browser_urls_locked()
        self.assertIn("Alerts", payload["taken_names"])
        self.assertIn("Gameplay", payload["taken_names"])
        self.assertEqual(
            payload["urls_by_scene"]["Gameplay"],
            ["http://127.0.0.1:5000/alerts"],
        )
        self.assertEqual(payload["urls_by_scene"]["BRB"], [])
        self.assertTrue(
            scene_has_template_url(
                payload["urls_by_scene"]["Gameplay"],
                "http://localhost:5000/alerts",
            )
        )
        self.assertFalse(
            scene_has_template_url(
                payload["urls_by_scene"]["BRB"],
                "http://localhost:5000/alerts",
            )
        )


class CreateBrowserSourceWorkerTests(unittest.TestCase):
    def _service(self, client) -> ObsServiceImpl:
        service = ObsServiceImpl()
        service._req_client = client
        return service

    def test_skips_an_existing_source_name(self) -> None:
        client = _FakeObsClient(["Alerts", "Webcam"])
        service = self._service(client)
        payload = service._create_browser_source_locked(
            {
                "scene_name": "Gameplay",
                "input_name": "Alerts",
                "url": "http://127.0.0.1:5000/alerts",
                "width": 1920,
                "height": 1080,
                "reroute_audio": False,
                "fps_custom": False,
                "fps": None,
                "shutdown": True,
                "restart_when_active": False,
            }
        )
        self.assertEqual(payload["input_name"], "Alerts 2")
        self.assertEqual(payload["scene_name"], "Gameplay")
        scene, name, kind, settings, enabled = client.created
        self.assertEqual(scene, "Gameplay")
        self.assertEqual(name, "Alerts 2")
        self.assertEqual(kind, "browser_source")
        self.assertTrue(enabled)
        self.assertEqual(settings["css"], "")
        self.assertNotIn("fps", settings)
        self.assertEqual(settings["url"], "http://127.0.0.1:5000/alerts")

    def test_scene_name_counts_as_taken(self) -> None:
        client = _FakeObsClient([])
        client.get_scene_list = lambda: {
            "scenes": [{"sceneName": "Alerts"}]
        }
        service = self._service(client)
        payload = service._create_browser_source_locked(
            {
                "scene_name": "Gameplay",
                "input_name": "Alerts",
                "url": "http://127.0.0.1:5000/alerts",
                "width": 1920,
                "height": 1080,
                "fps_custom": False,
            }
        )
        self.assertEqual(payload["input_name"], "Alerts 2")
        self.assertEqual(client.created[1], "Alerts 2")

    def test_custom_fps_is_sent_only_when_enabled(self) -> None:
        client = _FakeObsClient([])
        service = self._service(client)
        service._create_browser_source_locked(
            {
                "scene_name": "Starting",
                "input_name": "Chat",
                "url": "http://127.0.0.1:5000/chat",
                "width": 1920,
                "height": 1080,
                "reroute_audio": True,
                "fps_custom": True,
                "fps": 60,
                "shutdown": False,
                "restart_when_active": True,
            }
        )
        settings = client.created[3]
        self.assertEqual(settings["fps"], 60)
        self.assertTrue(settings["fps_custom"])
        self.assertTrue(settings["reroute_audio"])
        self.assertFalse(settings["shutdown"])
        self.assertTrue(settings["restart_when_active"])
        self.assertEqual(settings["css"], "")

    def test_rejects_fps_out_of_range_without_creating(self) -> None:
        client = _FakeObsClient([])
        service = self._service(client)
        with self.assertRaises(ValueError):
            service._create_browser_source_locked(
                {
                    "scene_name": "Starting",
                    "input_name": "Chat",
                    "url": "http://127.0.0.1:5000/chat",
                    "width": 1920,
                    "height": 1080,
                    "fps_custom": True,
                    "fps": 0,
                }
            )
        self.assertIsNone(client.created)

    def test_keyword_create_input_fallback(self) -> None:
        client = _KeywordOnlyClient([])
        service = self._service(client)
        service._create_browser_source_locked(
            {
                "scene_name": "BRB",
                "input_name": "Alerts",
                "url": "http://127.0.0.1:5000/alerts",
                "width": 800,
                "height": 600,
                "fps_custom": False,
            }
        )
        self.assertEqual(client.created["sceneName"], "BRB")
        self.assertEqual(client.created["inputName"], "Alerts")
        self.assertEqual(client.created["inputKind"], "browser_source")
        self.assertTrue(client.created["sceneItemEnabled"])
        self.assertEqual(client.created["inputSettings"]["css"], "")

    def test_disconnected_blocking_call_does_not_create(self) -> None:
        service = ObsServiceImpl()
        ok, message = service.create_browser_source_blocking(
            scene_name="Gameplay",
            input_name="Alerts",
            url="http://127.0.0.1:5000/alerts",
            width=1920,
            height=1080,
        )
        self.assertFalse(ok)
        self.assertIn("not connected", message.lower())


if __name__ == "__main__":
    unittest.main()

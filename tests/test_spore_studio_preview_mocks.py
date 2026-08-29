#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Spore Studio preview mock payloads and preview_mocks derivation."""

from __future__ import annotations

import unittest

from modules.spore_studio import preview_mocks, template_codegen


class SporeStudioPreviewMocksTests(unittest.TestCase):
    def test_typed_bit_queue_alert_has_amt_cheered(self):
        spec = preview_mocks.build_mock_payload("next_alert", alert_type="bit")
        self.assertIsNotNone(spec)
        socket_event, body = spec
        self.assertEqual(socket_event, "next_alert")
        self.assertEqual(body.get("alert_type"), "bit")
        self.assertIn("amt_cheered", body)
        self.assertIn("queue_seq", body)

    def test_preview_queue_seq_does_not_steal_live_counter(self):
        from unittest.mock import patch

        with patch(
            "modules.web_engine.assign_next_alert_queue_seq",
            side_effect=AssertionError("live seq must not be used for preview"),
        ):
            spec = preview_mocks.build_mock_payload("next_alert", alert_type="follow")
            payload = preview_mocks._next_alert_queue_payload(
                preview_mocks._demo_pools()
            )
        self.assertIsNotNone(spec)
        self.assertLess(spec[1]["queue_seq"], 0)
        self.assertLess(payload["queue_seq"], 0)

    def test_typed_giftsub_instant_alert(self):
        spec = preview_mocks.build_mock_payload("instant_alert", alert_type="giftsub")
        self.assertIsNotNone(spec)
        socket_event, body = spec
        self.assertEqual(socket_event, "instant_alert")
        self.assertEqual(body.get("alert_type"), "giftsub")
        self.assertIn("gift_qty", body)
        self.assertNotIn("queue_seq", body)

    def test_derive_preview_mocks_instant_template(self):
        model = {
            "alert_system": "instant",
            "elements": [
                {
                    "counter": {
                        "rules": [{"event": "instant_alert"}],
                    },
                },
            ],
        }
        mocks = preview_mocks.derive_preview_mocks(model)
        self.assertTrue(any(m.get("label") == "Random instant alert" for m in mocks))
        bit_entries = [m for m in mocks if m.get("alert_type") == "bit"]
        self.assertEqual(len(bit_entries), 1)
        self.assertEqual(bit_entries[0]["event"], "instant_alert")

    def test_derive_includes_bound_non_alert_events(self):
        model = {
            "alert_system": "queue",
            "elements": [
                {
                    "bindings": [{"event": "new-message"}],
                },
            ],
        }
        mocks = preview_mocks.derive_preview_mocks(model)
        events = {m["event"] for m in mocks}
        self.assertIn("new-message", events)

    def test_derived_json_config_includes_preview_mocks(self):
        model = {
            "template_name": "test_preview_mocks",
            "alert_system": "instant",
            "design": {"width": 100, "height": 50},
            "title": "test",
            "elements": [],
            "streamdeck_options": {"description": "", "actions": {}},
        }
        cfg = template_codegen._derived_json_config(model)
        self.assertIn("preview_mocks", cfg)
        self.assertIsInstance(cfg["preview_mocks"], list)
        self.assertGreater(len(cfg["preview_mocks"]), 1)


if __name__ == "__main__":
    unittest.main()

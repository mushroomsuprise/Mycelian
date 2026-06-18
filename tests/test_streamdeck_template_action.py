#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Stream Deck HTTP template_action dispatch (multi-action events)."""

from __future__ import annotations

import unittest

from modules.streamdeck_template_dispatch import plan_streamdeck_template_action_emit


class StreamdeckHttpDispatchTests(unittest.TestCase):
    def test_second_action_emits_configured_event_not_stale_client_event(self) -> None:
        cfg = {
            "streamdeck_options": {
                "actions": {
                    "action_1": {
                        "name": "One",
                        "event": "event_one",
                        "default_data": {},
                    },
                    "action_2": {
                        "name": "Two",
                        "event": "event_two",
                        "default_data": {"delta": 1},
                    },
                }
            }
        }
        compat_key, resolved, merged = plan_streamdeck_template_action_emit(
            template_name="demo",
            action_name="action_2",
            event_name_req="event_one",
            action_data_raw={},
            template_config=cfg,
            use_client_event_name=True,
        )
        self.assertEqual(compat_key, "action_2")
        self.assertEqual(resolved, "event_two")
        self.assertEqual(merged.get("delta"), 1)

    def test_merges_default_data_from_config(self) -> None:
        cfg = {
            "streamdeck_options": {
                "actions": {
                    "action_1": {
                        "name": "One",
                        "event": "ev",
                        "default_data": {"from_template": True},
                    },
                }
            }
        }
        _, _, merged = plan_streamdeck_template_action_emit(
            template_name="demo",
            action_name="action_1",
            event_name_req="",
            action_data_raw={"from_client": True},
            template_config=cfg,
            use_client_event_name=False,
        )
        self.assertTrue(merged.get("from_template"))
        self.assertTrue(merged.get("from_client"))

    def test_resolve_by_display_name(self) -> None:
        cfg = {
            "streamdeck_options": {
                "actions": {
                    "action_1": {
                        "name": "Show overlay",
                        "event": "show_overlay",
                        "default_data": {},
                    },
                }
            }
        }
        _, resolved, _ = plan_streamdeck_template_action_emit(
            template_name="demo",
            action_name="Show overlay",
            event_name_req="wrong",
            action_data_raw={},
            template_config=cfg,
            use_client_event_name=True,
        )
        self.assertEqual(resolved, "show_overlay")


if __name__ == "__main__":
    unittest.main()

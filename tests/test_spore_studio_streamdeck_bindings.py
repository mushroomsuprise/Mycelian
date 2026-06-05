#!/usr/bin/env python3
"""Tests that multiple Stream Deck bindings compile distinct socket listeners."""

from __future__ import annotations

import unittest

from modules.spore_studio.behavior_blocks import compile_bindings


class SporeStudioStreamdeckBindingsTests(unittest.TestCase):
    def test_two_streamdeck_events_register_two_listeners(self) -> None:
        elements = [
            {
                "id": "el1",
                "type": "text",
                "bindings": [
                    {
                        "trigger": "streamdeck",
                        "streamdeck_action": "action_1",
                        "event": "event_one",
                        "action": "show",
                        "args": {},
                    },
                    {
                        "trigger": "streamdeck",
                        "streamdeck_action": "action_2",
                        "event": "event_two",
                        "action": "hide",
                        "args": {},
                    },
                ],
            }
        ]
        out = compile_bindings(elements)
        js = out["js"]
        self.assertIn('if (eventName === "event_one")', js)
        self.assertIn('if (eventName === "event_two")', js)
        self.assertIn('socket.on("event_one"', js)
        self.assertIn('socket.on("event_two"', js)
        self.assertIn("__sporeBoundEvents", js)


if __name__ == "__main__":
    unittest.main()

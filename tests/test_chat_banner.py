#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chat overlay banner payload, chatbot output mode, and Twitch announcements."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.chat_banner import normalize_chat_banner_payload  # noqa: E402
from modules.chatbot import deliver_chatbot_output  # noqa: E402
from modules.chatbot_core import ChatCommand, ChatEvent  # noqa: E402
from modules.twitch import (  # noqa: E402
    emit_announcement_banner_from_notice,
    is_chat_announcement_notice,
)


class ChatBannerPayloadTests(unittest.TestCase):
    def test_permanent_flag_and_zero_duration(self) -> None:
        flagged = normalize_chat_banner_payload(
            {
                "message": "Hi",
                "duration": 12,
                "permanent": True,
                "source": "connector",
            }
        )
        self.assertTrue(flagged["permanent"])
        self.assertEqual(flagged["duration"], 0)
        self.assertEqual(flagged["source"], "connector")

        zero = normalize_chat_banner_payload({"message": "Hi", "duration": 0})
        self.assertTrue(zero["permanent"])
        self.assertEqual(zero["duration"], 0)

    def test_omitted_duration_uses_default(self) -> None:
        payload = normalize_chat_banner_payload(
            {"message": "Hi"}, default_duration=15
        )
        self.assertEqual(payload["duration"], 15)
        self.assertFalse(payload["permanent"])
        self.assertEqual(payload["action"], "show")

    def test_style_keeps_safe_fields_only(self) -> None:
        payload = normalize_chat_banner_payload(
            {
                "message": "Hi",
                "style": {
                    "background": "#112233",
                    "color": "not a color",
                    "font": "../etc/passwd",
                    "font_size": 28,
                    "animation": "explode",
                },
            }
        )
        style = payload["style"]
        self.assertEqual(style["background"], "#112233")
        self.assertEqual(style["font_size"], 28)
        self.assertNotIn("color", style)
        self.assertNotIn("font", style)
        self.assertNotIn("animation", style)

    def test_clear_drops_message(self) -> None:
        payload = normalize_chat_banner_payload(
            {"action": "clear", "message": "gone", "source": "manual"}
        )
        self.assertEqual(payload, {"action": "clear", "source": "manual"})


class ChatbotBannerOutputTests(unittest.TestCase):
    def test_banner_mode_does_not_dispatch_chat(self) -> None:
        with (
            patch("modules.chat_banner.emit_chat_banner", return_value=True) as emit,
            patch("modules.chatbot.dispatch_chatbot_response") as dispatch,
        ):
            ok = deliver_chatbot_output(
                "hello there", ["twitch"], output_mode="banner"
            )
        self.assertTrue(ok)
        emit.assert_called_once()
        self.assertEqual(emit.call_args.args[0], "hello there")
        self.assertEqual(emit.call_args.kwargs["source"], "chatbot")
        dispatch.assert_not_called()

    def test_chat_mode_still_dispatches(self) -> None:
        with (
            patch("modules.chat_banner.emit_chat_banner", return_value=True) as emit,
            patch(
                "modules.chatbot.dispatch_chatbot_response", return_value=True
            ) as dispatch,
        ):
            ok = deliver_chatbot_output("hello", ["youtube"], output_mode="chat")
        self.assertTrue(ok)
        emit.assert_not_called()
        dispatch.assert_called_once()

    def test_command_and_event_default_to_chat(self) -> None:
        command = ChatCommand(
            command_id="c1",
            name="Hi",
            command_name="hi",
            response_text="hello",
        )
        event = ChatEvent(event_id="e1", name="Follow", response_text="welcome")
        self.assertEqual(command.output_mode, "chat")
        self.assertEqual(event.output_mode, "chat")
        banner = ChatCommand(
            command_id="c2",
            name="Hi",
            command_name="hi",
            response_text="hello",
            output_mode="banner",
        )
        self.assertEqual(banner.output_mode, "banner")
        self.assertEqual(banner.to_dict()["output_mode"], "banner")
        self.assertEqual(event.to_dict()["output_mode"], "chat")


class TwitchAnnouncementBannerTests(unittest.TestCase):
    def test_notice_types(self) -> None:
        self.assertTrue(is_chat_announcement_notice("announcement"))
        self.assertTrue(is_chat_announcement_notice("shared_chat_announcement"))
        self.assertTrue(is_chat_announcement_notice("Announcement"))
        self.assertFalse(is_chat_announcement_notice("sub"))
        self.assertFalse(is_chat_announcement_notice("modiversary"))
        self.assertFalse(is_chat_announcement_notice(""))

    def test_announcement_notice_emits_banner(self) -> None:
        ev = SimpleNamespace(
            message=SimpleNamespace(text="Starting soon", fragments=None),
            system_message="",
        )
        with patch(
            "modules.chat_banner.emit_chat_banner", return_value=True
        ) as emit:
            ok = emit_announcement_banner_from_notice(ev)
        self.assertTrue(ok)
        self.assertEqual(emit.call_args.args[0], "Starting soon")
        self.assertEqual(emit.call_args.kwargs["source"], "twitch_announcement")


if __name__ == "__main__":
    unittest.main()

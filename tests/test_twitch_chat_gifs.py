#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Twitch chat GIF EventSub patching and fragment serialization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from twitchAPI.object.eventsub import ChatMessageFragment  # noqa: E402

from modules.twitch import serialize_chat_message_fragments  # noqa: E402
from modules.twitch_eventsub_patch import (  # noqa: E402
    ChatMessageFragmentGifMetadata,
    ensure_channel_chat_message_gif_patch,
)


GIF_URL = "https://media.example.com/gifs/catdance.gif?token=abc&w=400"


class ChatMessageGifPatchTests(unittest.TestCase):
    def test_patch_attaches_gif_annotation(self) -> None:
        ensure_channel_chat_message_gif_patch()
        annotations = getattr(ChatMessageFragment, "__annotations__", {})
        self.assertEqual(
            annotations.get("gif"), Optional[ChatMessageFragmentGifMetadata]
        )

    def test_patch_round_trips_gif_url_and_id(self) -> None:
        ensure_channel_chat_message_gif_patch()
        frag = ChatMessageFragment(
            type="gif",
            text="catdance",
            gif={"gif_id": "abc123", "url": GIF_URL},
        )
        self.assertEqual(getattr(frag, "type", None), "gif")
        self.assertIsNotNone(frag.gif)
        self.assertEqual(frag.gif.url, GIF_URL)
        self.assertEqual(frag.gif.gif_id, "abc123")

        serialized, gif_url = serialize_chat_message_fragments([frag])
        self.assertEqual(gif_url, GIF_URL)
        self.assertEqual(
            serialized,
            [
                {
                    "text": "catdance",
                    "type": "gif",
                    "url": GIF_URL,
                    "gif_id": "abc123",
                }
            ],
        )


class ChatMessageGifSerializeTests(unittest.TestCase):
    def test_empty_fragments_return_none(self) -> None:
        self.assertEqual(serialize_chat_message_fragments(None), (None, None))
        self.assertEqual(serialize_chat_message_fragments([]), (None, None))

    def test_dict_gif_fragment_keeps_unmodified_url(self) -> None:
        fragments = [
            {
                "type": "gif",
                "text": "catdance",
                "gif": {"gif_id": "g1", "url": GIF_URL},
            }
        ]
        serialized, gif_url = serialize_chat_message_fragments(fragments)
        self.assertEqual(gif_url, GIF_URL)
        self.assertEqual(serialized[0]["url"], GIF_URL)
        self.assertEqual(serialized[0]["type"], "gif")
        self.assertEqual(serialized[0]["gif_id"], "g1")

    def test_gif_id_falls_back_to_id(self) -> None:
        fragments = [
            SimpleNamespace(
                type="gif",
                text="wave",
                gif=SimpleNamespace(id="legacy-id", url=GIF_URL),
            )
        ]
        serialized, gif_url = serialize_chat_message_fragments(fragments)
        self.assertEqual(gif_url, GIF_URL)
        self.assertEqual(serialized[0]["gif_id"], "legacy-id")

    def test_first_gif_url_is_top_level(self) -> None:
        fragments = [
            SimpleNamespace(type="text", text="look "),
            SimpleNamespace(
                type="gif",
                text="first",
                gif=SimpleNamespace(gif_id="a", url="https://example.com/a.gif"),
            ),
            SimpleNamespace(
                type="gif",
                text="second",
                gif=SimpleNamespace(gif_id="b", url="https://example.com/b.gif"),
            ),
        ]
        serialized, gif_url = serialize_chat_message_fragments(fragments)
        self.assertEqual(gif_url, "https://example.com/a.gif")
        self.assertEqual(serialized[0]["type"], "text")
        self.assertEqual(serialized[1]["url"], "https://example.com/a.gif")
        self.assertEqual(serialized[2]["url"], "https://example.com/b.gif")

    def test_non_gif_fragments_still_serialize(self) -> None:
        fragments = [
            SimpleNamespace(type="text", text="hi "),
            SimpleNamespace(
                type="emote",
                text="Kappa",
                emote=SimpleNamespace(id="25", name="Kappa"),
            ),
            SimpleNamespace(
                type="cheermote",
                text="Cheer100",
                cheermote=SimpleNamespace(bits=100, tier=100),
            ),
            SimpleNamespace(
                type="mention",
                text="@bob",
                mention=SimpleNamespace(user_id="9", user_name="bob"),
            ),
        ]
        serialized, gif_url = serialize_chat_message_fragments(fragments)
        self.assertIsNone(gif_url)
        self.assertEqual(serialized[0], {"text": "hi ", "type": "text"})
        self.assertEqual(
            serialized[1],
            {"text": "Kappa", "type": "emote", "emote_id": "25", "emote_name": "Kappa"},
        )
        self.assertEqual(
            serialized[2],
            {"text": "Cheer100", "type": "cheermote", "bits": 100, "tier": 100},
        )
        self.assertEqual(
            serialized[3],
            {"text": "@bob", "type": "mention", "user_id": "9", "user_name": "bob"},
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch IRC/EventSub slash-command filter for chat overlay display."""

from __future__ import annotations

# Official Twitch chat commands (case-insensitive first token after "/").
TWITCH_SLASH_COMMANDS = frozenset(
    {
        # Everyone
        "mods",
        "vips",
        "color",
        "block",
        "unblock",
        "disconnect",
        "whisper",
        # Broadcaster + moderators
        "user",
        "timeout",
        "ban",
        "unban",
        "slow",
        "slowoff",
        "followers",
        "followersoff",
        "subscribers",
        "subscribersoff",
        "clear",
        "uniquechat",
        "uniquechatoff",
        "emoteonly",
        "emoteonlyoff",
        # Channel editor + broadcaster
        "commercial",
        "host",
        "unhost",
        "raid",
        "unraid",
        "marker",
        # Broadcaster only
        "mod",
        "unmod",
        "vip",
        "unvip",
    }
)


def is_allowed_slash_message(message: str) -> bool:
    """Return True if message should appear in the chat overlay.

    Official Twitch slash commands (e.g. /ban, /mods) are hidden. Messages that
  are not commands—including /me and any unknown /prefix text—are shown.
    """
    text = (message or "").lstrip()
    if not text.startswith("/"):
        return True
    cmd = text[1:].split(None, 1)[0].lower()
    return cmd not in TWITCH_SLASH_COMMANDS

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_chat_commands.py (slash-command overlay filter)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import twitch_chat_commands as tcc  # noqa: E402


@pytest.mark.parametrize("msg", ["hello", "", None, "  hi /ban", "me too"])
def test_non_command_messages_are_shown(msg):
    assert tcc.is_allowed_slash_message(msg) is True


@pytest.mark.parametrize("msg", ["/ban someone", "/BAN x", "  /mods", "/timeout user 10", "/vip a", "/Marker"])
def test_official_commands_are_hidden(msg):
    assert tcc.is_allowed_slash_message(msg) is False


@pytest.mark.parametrize("msg", ["/me dances", "/shrug", "/unknown thing"])
def test_unknown_or_me_commands_are_shown(msg):
    assert tcc.is_allowed_slash_message(msg) is True


def test_command_set_contains_expected_entries():
    assert {"ban", "unban", "mod", "raid", "whisper"} <= tcc.TWITCH_SLASH_COMMANDS
    assert "me" not in tcc.TWITCH_SLASH_COMMANDS


@pytest.mark.xfail(strict=True, reason="BUG: a bare '/' (or '/ ' ) raises IndexError instead of being shown")
def test_bare_slash_does_not_crash():
    assert tcc.is_allowed_slash_message("/") is True

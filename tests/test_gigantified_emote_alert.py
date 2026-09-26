#!/usr/bin/env python3
"""Tests for gigantified-emote bit alert identity."""

from __future__ import annotations

import sys
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alertutils
from modules.twitch import _apply_gigantified_emote_to_alert


class _PowerUpType(Enum):
    GIGANTIFY = "gigantify_an_emote"
    CELEBRATION = "celebration"


class GigantifiedEmoteAlertTests(unittest.TestCase):
    def test_enum_power_up_sets_flag_and_message_fragment(self) -> None:
        alert = alertutils.AlertObj()
        power_up = SimpleNamespace(
            type=_PowerUpType.GIGANTIFY,
            emote=SimpleNamespace(id="emotesv2_123", name="PogChamp"),
        )

        _apply_gigantified_emote_to_alert(alert, power_up)

        self.assertTrue(alert.is_gigantified_emote)
        self.assertEqual(alert.gigantified_emote_id, "emotesv2_123")
        self.assertEqual(alert.gigantified_emote_name, "PogChamp")
        self.assertEqual(alert.message, "PogChamp")
        self.assertEqual(
            alert.fragments,
            [{"type": "emote", "text": "PogChamp", "emote_id": "emotesv2_123"}],
        )

    def test_existing_emote_fragment_is_kept(self) -> None:
        alert = alertutils.AlertObj()
        alert.message = "PogChamp hello"
        alert.fragments = [
            {"type": "emote", "text": "PogChamp", "emote_id": "emotesv2_123"},
            {"type": "text", "text": " hello"},
        ]
        power_up = {
            "type": "gigantify_an_emote",
            "emote": {"id": "emotesv2_123", "name": "PogChamp"},
        }

        _apply_gigantified_emote_to_alert(alert, power_up)

        self.assertTrue(alert.is_gigantified_emote)
        self.assertEqual(alert.message, "PogChamp hello")
        self.assertEqual(len(alert.fragments), 2)

    def test_other_power_up_stays_a_normal_bit_alert(self) -> None:
        alert = alertutils.AlertObj()
        alert.message = "celebrate"
        power_up = SimpleNamespace(type=_PowerUpType.CELEBRATION, emote=None)

        _apply_gigantified_emote_to_alert(alert, power_up)

        self.assertFalse(alert.is_gigantified_emote)
        self.assertEqual(alert.gigantified_emote_id, "")
        self.assertEqual(alert.message, "celebrate")
        self.assertIsNone(alert.fragments)

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for PSN presence/trophy title normalization and fuzzy matching."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.psnapi import (  # noqa: E402
    find_best_fuzzy_game_name_match,
    normalize_game_name_key,
)


class NormalizeGameNameKeyTests(unittest.TestCase):
    def test_curly_vs_straight_apostrophe(self) -> None:
        curly = "ASTRO\u2019s PLAYROOM"
        straight = "ASTRO's PLAYROOM"
        self.assertEqual(
            normalize_game_name_key(curly),
            normalize_game_name_key(straight),
        )

    def test_tony_hawk_apostrophe(self) -> None:
        curly = "Tony Hawk\u2019s Pro Skater 1 + 2"
        straight = "Tony Hawk's Pro Skater 1 + 2"
        self.assertEqual(
            normalize_game_name_key(curly),
            normalize_game_name_key(straight),
        )

    def test_trademark_and_trophies_suffix(self) -> None:
        presence = "STAR WARS: Squadrons"
        trophy = "STAR WARS\u2122: Squadrons Trophies"
        self.assertEqual(
            normalize_game_name_key(presence),
            normalize_game_name_key(trophy),
        )

    def test_registered_mark_stripped(self) -> None:
        self.assertEqual(
            normalize_game_name_key("Call of Duty\u00ae: Black Ops 4"),
            normalize_game_name_key("Call of Duty Black Ops 4"),
        )

    def test_empty_and_none(self) -> None:
        self.assertEqual(normalize_game_name_key(""), "")
        self.assertEqual(normalize_game_name_key(None), "")


class FuzzyGameNameMatchTests(unittest.TestCase):
    def test_exact_normalized_scores_as_unique_winner(self) -> None:
        candidates = [
            {
                "name": "ASTRO's PLAYROOM",
                "np_communication_id": "NPWR20188_00",
                "platform": "PS5",
            },
            {
                "name": "Stray",
                "np_communication_id": "NPWR22008_00",
                "platform": "PS5",
            },
        ]
        hit = find_best_fuzzy_game_name_match(
            "ASTRO\u2019s PLAYROOM",
            candidates,
            platform="PS5",
            name_fields=("name",),
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["np_communication_id"], "NPWR20188_00")

    def test_ambiguous_near_ties_return_none(self) -> None:
        # Same normalized name on two candidates => both score 1.0 => ambiguous
        candidates = [
            {"name": "Horizon Forbidden West", "platform": "PS5", "id": "a"},
            {"name": "Horizon Forbidden West", "platform": "PS4", "id": "b"},
        ]
        hit = find_best_fuzzy_game_name_match(
            "Horizon Forbidden West",
            candidates,
            platform=None,
            name_fields=("name",),
        )
        self.assertIsNone(hit)

    def test_platform_prefers_matching_duplicate_title(self) -> None:
        candidates = [
            {
                "name": "Horizon Forbidden West",
                "np_communication_id": "NPWR21008_00",
                "platform": "PS5",
            },
            {
                "name": "Horizon Forbidden West",
                "np_communication_id": "NPWR23593_00",
                "platform": "PS4",
            },
        ]
        hit = find_best_fuzzy_game_name_match(
            "Horizon Forbidden West",
            candidates,
            platform="PS5",
            name_fields=("name",),
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["np_communication_id"], "NPWR21008_00")

    def test_does_not_match_expansion_title(self) -> None:
        candidates = [
            {
                "name": "Monster Hunter: World",
                "np_communication_id": "NPWR11631_00",
                "platform": "PS4",
            },
            {
                "name": "Monster Hunter World: Iceborne",
                "np_communication_id": "NPWR15240_00",
                "platform": "PS4",
            },
        ]
        hit = find_best_fuzzy_game_name_match(
            "Monster Hunter: World",
            candidates,
            platform="PS4",
            name_fields=("name",),
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["np_communication_id"], "NPWR11631_00")

        # Base presence name must not fuzzy-accept Iceborne alone (below threshold)
        self.assertIsNone(
            find_best_fuzzy_game_name_match(
                "Monster Hunter: World",
                [candidates[1]],
                platform="PS4",
                name_fields=("name",),
            )
        )

    def test_short_query_rejected(self) -> None:
        candidates = [{"name": "Stray", "platform": "PS5"}]
        self.assertIsNone(
            find_best_fuzzy_game_name_match(
                "Hi", candidates, name_fields=("name",)
            )
        )

    def test_trophies_suffix_fuzzy_or_exact(self) -> None:
        candidates = [
            {
                "name": "Apex Legends Trophies",
                "np_communication_id": "NPWR15848_00",
                "platform": "PS4",
            }
        ]
        hit = find_best_fuzzy_game_name_match(
            "Apex Legends",
            candidates,
            platform="PS4",
            name_fields=("name",),
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["np_communication_id"], "NPWR15848_00")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""KERNEL.BIN item-id mapping used by the FF7 overlay recent-item display."""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks.ff7_hook import (  # noqa: E402
    _ff7_unified_item_name,
    _parse_inv_items_multiset,
)

_ITEM_NAMES_PATH = ROOT / "assets" / "ff7" / "item_names_en.json"

# KERNEL.BIN item names (savemap 9-bit item ids). These were historically swapped.
_KERNEL_ITEM_NAMES = {
    49: "Dragon Scales",
    59: "Dazers",
    60: "Dragon Fang",
    93: "Chaos",
    95: "1/35 Soldier",
    103: "Earth Harp",
    104: "Guide Book",
}


class Ff7ItemNamesJsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.names = json.loads(_ITEM_NAMES_PATH.read_text(encoding="utf-8"))

    def test_kernel_ids_match_english_names(self) -> None:
        for iid, expected in _KERNEL_ITEM_NAMES.items():
            self.assertEqual(self.names.get(str(iid)), expected, f"id {iid}")

    def test_no_invented_item_names(self) -> None:
        values = set(self.names.values())
        self.assertNotIn("Dynamite", values)
        self.assertNotIn("Dragon Suplex", values)

    def test_guide_book_is_last_named_item(self) -> None:
        self.assertEqual(self.names.get("104"), "Guide Book")
        self.assertNotIn("105", self.names)


class Ff7ItemNameLookupTests(unittest.TestCase):
    def test_unified_name_uses_kernel_ids(self) -> None:
        for iid, expected in _KERNEL_ITEM_NAMES.items():
            self.assertEqual(_ff7_unified_item_name(iid), expected, f"id {iid}")

    def test_savemap_word_for_dragon_scales_names_correctly(self) -> None:
        qty = 1
        word = (qty << 9) | 49
        counts = _parse_inv_items_multiset(struct.pack("<H", word))
        self.assertEqual(len(counts), 1)
        iid, parsed_qty = next(iter(counts.items()))
        self.assertEqual(parsed_qty, qty)
        self.assertEqual(_ff7_unified_item_name(iid), "Dragon Scales")


if __name__ == "__main__":
    unittest.main()

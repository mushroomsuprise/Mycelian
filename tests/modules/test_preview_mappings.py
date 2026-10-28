# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/preview_mappings.py (built-in preview variable examples)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import preview_mappings as pm  # noqa: E402


def test_builtin_mappings_are_strings_with_known_examples():
    m = pm.get_builtin_variable_mappings()
    assert m["username"] == "TestUser"
    assert m["formatted_amount"] == "$25.00"
    assert m["random_emote"] == "Kappa"
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in m.items())


def test_builtin_mappings_return_fresh_dict():
    a = pm.get_builtin_variable_mappings()
    a["username"] = "changed"
    assert pm.get_builtin_variable_mappings()["username"] == "TestUser"


def test_categories_cover_every_builtin_variable_exactly_once():
    mappings = pm.get_builtin_variable_mappings()
    cats = pm.get_variable_categories()
    seen = {}
    for cat, values in cats.items():
        for key, value in values.items():
            assert key not in seen, f"{key} in {seen.get(key)} and {cat}"
            seen[key] = cat
            assert value == mappings[key]
    assert set(seen) == set(mappings)


@pytest.mark.parametrize(
    "category,key,value",
    [
        ("basic", "username", "TestUser"),
        ("time_date", "day", "Monday"),
        ("statistics", "total_bits", "89,012"),
        ("generic", "source", "Twitch"),
        ("hype_train", "level", "3"),
    ],
)
def test_category_variables_lookup(category, key, value):
    assert pm.get_category_variables(category)[key] == value


def test_unknown_category_returns_empty_dict():
    assert pm.get_category_variables("nope") == {}

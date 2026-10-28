# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/ff7_boss_tracker.py (boss defeat log and config match sets)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import ff7_boss_tracker as bt  # noqa: E402


def test_parse_line_patterns():
    assert bt.parse_line_patterns("  Reno \n\n RUDE\n  ") == frozenset({"reno", "rude"})
    assert bt.parse_line_patterns(None) == frozenset()
    assert bt.parse_line_patterns(5) == frozenset()
    assert bt.parse_line_patterns("") == frozenset()


def test_element_string_value():
    cfg = {"elements": ["junk", {"id": "other", "value": "x"}, {"id": "A", "value": "hello"}]}
    assert bt._element_string_value(cfg, "A") == "hello"
    assert bt._element_string_value({"elements": [{"id": "A", "value": None}]}, "A") == ""
    assert bt._element_string_value({"elements": [{"id": "A", "value": 12}]}, "A") == "12"
    assert bt._element_string_value({"elements": None}, "A") == ""
    assert bt._element_string_value({}, "A") == ""


def test_match_sets_from_config():
    cfg = {"elements": [
        {"id": bt.FF7_BOSS_SUBSTRINGS_ELEMENT_ID, "value": "Reno\nRude"},
        {"id": bt.FF7_BOSS_EXCLUDES_ELEMENT_ID, "value": "reno-opt"},
    ]}
    assert bt.ff7_boss_match_sets_from_config(cfg) == (frozenset({"reno", "rude"}), frozenset({"reno-opt"}))


def test_match_sets_default_when_empty(caplog):
    with caplog.at_level(logging.WARNING, logger=bt.__name__):
        sub, exc = bt.ff7_boss_match_sets_from_config({"elements": []})
    assert sub is bt._DEFAULT_BOSS_SUBSTR
    assert exc == frozenset()
    assert "built-in defaults" in caplog.text


def _tracker():
    return bt.Ff7BossTracker(frozenset({"reno", "air buster"}), frozenset({"opt"}))


def _snap(*enemies, battle=True):
    return {"battle": battle, "enemies": [dict(slot=s, hp=h, name=n) for s, h, n in enemies]}


def test_initial_state():
    assert _tracker().bosses_dict() == {"names": [], "last": "", "count": 0}


def test_is_boss_actor():
    t = _tracker()
    assert t._is_boss_actor({"name": "RENO"})
    assert not t._is_boss_actor({"name": "Reno opt"})
    assert not t._is_boss_actor({"name": "Grunt"})
    assert not t._is_boss_actor({"name": None})
    assert not t._is_boss_actor({})


def test_records_boss_defeat_on_hp_transition():
    t = _tracker()
    assert t.update_from_snapshot(_snap((4, 100, "Reno"), (5, 30, "Grunt"))) is False
    assert t.update_from_snapshot(_snap((4, 0, " Reno "), (5, 0, "Grunt"))) is True
    assert t.bosses_dict() == {"names": ["Reno"], "last": "Reno", "count": 1}
    # still dead next tick: no duplicate
    assert t.update_from_snapshot(_snap((4, 0, "Reno"))) is False


def test_same_boss_twice_in_a_row_not_duplicated():
    t = _tracker()
    t.update_from_snapshot(_snap((4, 10, "Reno")))
    t.update_from_snapshot(_snap((4, 0, "Reno")))
    t.update_from_snapshot(_snap((4, 10, "Reno")))
    assert t.update_from_snapshot(_snap((4, 0, "Reno"))) is False
    t.update_from_snapshot(_snap((5, 10, "Air Buster")))
    assert t.update_from_snapshot(_snap((5, -1, "Air Buster"))) is True
    assert t.bosses_dict()["names"] == ["Air Buster", "Reno"]


def test_first_seen_dead_not_counted_and_unnamed_boss():
    t = bt.Ff7BossTracker(frozenset({"unknown"}), frozenset())
    assert t.update_from_snapshot(_snap((1, 0, "Unknown thing"))) is False  # no prior hp
    t.update_from_snapshot({"battle": True, "enemies": [{"slot": 2, "hp": 5}]})
    # name missing -> "Unknown" is not a boss for _is_boss_actor (name ""), so nothing recorded
    assert t.update_from_snapshot({"battle": True, "enemies": [{"slot": 2, "hp": 0}]}) is False


def test_slots_dropped_and_battle_end_clears_prev():
    t = _tracker()
    t.update_from_snapshot(_snap((4, 10, "Reno"), (5, 10, "Reno")))
    t.update_from_snapshot(_snap((5, 10, "Reno")))
    assert t._prev_battle_enemies == {5: 10}
    assert t.update_from_snapshot({"battle": False}) is False
    assert t._prev_battle_enemies == {}
    # enemies None while battle
    assert t.update_from_snapshot({"battle": True, "enemies": None}) is False


def test_set_match_sets():
    t = _tracker()
    t.set_match_sets(frozenset({"grunt"}), frozenset())
    t.update_from_snapshot(_snap((1, 5, "Grunt")))
    assert t.update_from_snapshot(_snap((1, 0, "Grunt"))) is True


def test_clear():
    t = _tracker()
    t.restore(["A"], "A")
    t._prev_battle_enemies[1] = 3
    t.clear()
    assert t.bosses_dict() == {"names": [], "last": "", "count": 0}
    assert t._prev_battle_enemies == {}


@pytest.mark.parametrize(
    "names,last,exp_names,exp_last",
    [
        ([" B ", "", 3, None, "A"], "  L ", ["B", "A"], "L"),
        (["B", "A"], "", ["B", "A"], "B"),
        (["B"], 5, ["B"], "B"),
        ([], "", [], ""),
        ([], "  ", [], ""),
    ],
)
def test_restore(names, last, exp_names, exp_last):
    t = _tracker()
    t.restore(names, last)
    d = t.bosses_dict()
    assert d["names"] == exp_names and d["last"] == exp_last and d["count"] == len(exp_names)

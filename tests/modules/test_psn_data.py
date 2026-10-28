# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/psn_data.py: PSN state dataclasses."""

from __future__ import annotations

import sys
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.psn_data import PSNData, PSNGameMismatch  # noqa: E402


def test_psn_data_defaults():
    d = PSNData()
    assert d.current_game_name is None
    assert d.current_game_art_url is None
    assert d.current_game_np_comm_id is None
    assert d.trophy_counts == {} and d.current_game_trophies == {}
    assert d.current_game_trophies_all == {} and d.all_games_data == {}
    assert d.current_game_progress is None
    assert d.npsso_code is None and d.online_id is None and d.account_id is None
    assert d.is_online is False
    assert d.connection_status == "Disconnected"
    assert d.presence == {}
    assert d.current_game_mismatch is None


def test_psn_data_mutable_defaults_are_not_shared():
    a, b = PSNData(), PSNData()
    a.trophy_counts["gold"] = 1
    a.presence["x"] = 1
    a.all_games_data["g"] = {}
    a.current_game_trophies["bronze"] = 2
    a.current_game_trophies_all["earned"] = {}
    assert b.trophy_counts == {} and b.presence == {} and b.all_games_data == {}
    assert b.current_game_trophies == {} and b.current_game_trophies_all == {}


def test_mismatch_defaults_and_nesting():
    m = PSNGameMismatch("Game", "CUSA1", "PS5", "2026-01-01T00:00:00")
    assert m.notified is False
    d = PSNData(current_game_name="Game", current_game_mismatch=m, is_online=True)
    dumped = asdict(d)
    assert dumped["current_game_mismatch"] == {
        "presence_name": "Game", "np_title_id": "CUSA1", "platform": "PS5",
        "detected_at": "2026-01-01T00:00:00", "notified": False,
    }
    assert dumped["is_online"] is True


def test_field_order_is_stable():
    assert [f.name for f in fields(PSNGameMismatch)] == [
        "presence_name", "np_title_id", "platform", "detected_at", "notified"]
    assert len(fields(PSNData)) == 15
    assert PSNData() == PSNData()

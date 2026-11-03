# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The "A game's progress" chatbot lookup (Games Tracker): !progress <game>, !haveyouplayed <game>."""

from __future__ import annotations

import asyncio
import time

import pytest

from modules import chatbot_lookups as lk
from modules.chatbot_core import choose_lookup_outcome
from modules.game_tracker import service as svc
from modules.game_tracker.models import PSN, STEAM, TrackedGame
from modules.game_tracker.store import GameTrackerStore

NOW = time.time()


@pytest.fixture
def library(tmp_path):
    store = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    store.upsert_games([
        TrackedGame(platform=PSN, game_id="NPWR00001_00", title="ELDEN RING™", platform_label="PS5",
                    earned={"bronze": 22, "silver": 9, "gold": 3, "platinum": 1},
                    defined={"bronze": 28, "silver": 9, "gold": 4, "platinum": 1}, progress=100, last_played=NOW - 2.5 * 86400),
        TrackedGame(platform=STEAM, game_id="1086940", title="Baldur's Gate 3", platform_label="Steam",
                    unlocked_ids=["a"] * 19, total_achievements=54, progress=35, playtime_min=142 * 60, last_played=NOW - 3600 * 5),
        TrackedGame(platform=PSN, game_id="NPWR00002_00", title="Secret Game", hidden=True, last_played=NOW),
    ])
    svc.set_service(svc.GameTrackerService(store, psn_client_fn=lambda: None, steam_client_fn=lambda: None))
    yield store
    svc.set_service(None)


def lookup(text, **config):
    return asyncio.run(lk.run_lookup("game_progress", {"id": "game_progress", **config}, {"command_message": text}))


def test_catalog_entry_needs_no_twitch(monkeypatch):
    entry = lk.get_lookup("game_progress")
    assert entry["group"] == "Games" and entry["needs_twitch"] is False and entry["scopes"] == []
    monkeypatch.setattr(lk, "_api", lambda: None)  # Twitch not connected
    assert lk.lookup_available("game_progress") == (True, "")
    keys = {v["key"] for v in lk.lookup_variable_specs("game_progress")}
    assert {"game_played", "game_title", "game_progress", "game_trophies", "game_query"} <= keys
    assert lk.example_variables("game_progress")["game_trophies"] == "1 platinum, 3 gold, 9 silver, 22 bronze"


def test_progress_by_name_is_forgiving(library):
    for typed in ("elden ring", "ELDEN RING", "eldenring", "elden", "eldn ring"):
        out = lookup(typed)
        assert out["lookup_ok"] and out["game_title"] == "ELDEN RING™", typed
    out = lookup("elden ring")
    assert out["game_played"] == "yes" and out["game_progress"] == "100" and out["game_completed"] == "yes"
    assert out["game_trophies"] == "1 platinum, 3 gold, 9 silver, 22 bronze"
    assert out["game_earned"] == "35" and out["game_total"] == "42"
    assert out["game_platform"] == "PlayStation" and out["game_console"] == "PS5"
    assert out["game_last_played"] == "2 days ago" and out["game_playtime"] == "" and out["game_query"] == "elden ring"


def test_steam_game_and_blank_means_last_played(library):
    out = lookup("baldurs gate")
    assert out["game_trophies"] == "19 of 54 achievements" and out["game_playtime"] == "142 hours"
    assert out["game_platform"] == "Steam" and out["game_completed"] == "no"
    assert lookup("")["game_title"] == "Baldur's Gate 3"  # the hidden game is skipped


def test_not_played_still_answers(library):
    out = lookup("Halo Infinite")
    assert out["lookup_ok"] is False and out["lookup_reason"] == "not_played"
    assert out["game_played"] == "no" and out["game_title"] == "Halo Infinite" and out["game_query"] == "Halo Infinite"
    assert "“Halo Infinite”" in out["lookup_error"]
    # No fallback text: the normal reply still goes out (it can say {game_played}); with one, the fallback.
    assert choose_lookup_outcome(out, "") == "normal"
    assert choose_lookup_outcome(out, "Not yet!") == "fallback"
    assert lookup("secret game")["game_played"] == "no"  # hidden games stay hidden from chat


def test_platform_option(library):
    assert lookup("baldur", platform="psn")["game_played"] == "no"
    assert lookup("baldur", platform="steam")["game_played"] == "yes"
    assert lookup("baldur", platform="junk")["game_played"] == "yes"


def test_text_from_a_custom_template(library):
    out = asyncio.run(lk.run_lookup("game_progress", {"id": "game_progress", "text": "{arg1}"}, {"arg1": "elden"}))
    assert out["game_title"] == "ELDEN RING™"


def test_variables_for_small_numbers():
    g = TrackedGame(platform=STEAM, game_id="1", title="Tiny", playtime_min=45, last_played=NOW - 60)
    out = lk.game_lookup_variables(g, "tiny")
    assert out["game_trophies"] == "no achievements" and out["game_playtime"] == "45 minutes" and out["game_last_played"] == "just now"
    one_hour = TrackedGame(platform=STEAM, game_id="2", title="One", playtime_min=60)
    assert lk.game_lookup_variables(one_hour, "")["game_playtime"] == "1 hour"
    assert lk.game_lookup_variables(one_hour, "")["game_last_played"] == ""
    fresh = TrackedGame(platform=PSN, game_id="N", title="New")
    assert lk.game_lookup_variables(fresh, "")["game_trophies"] == "no trophies yet"

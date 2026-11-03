# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker data shapes: titles, totals, completion, settings clean-up."""

from modules.game_tracker.models import (
    PSN,
    STEAM,
    GameEvent,
    TrackedGame,
    TrackerSettings,
    clean_milestones,
    grade_counts,
)


def psn(**kw):
    base = dict(platform=PSN, game_id="NPWR00001_00", title="ELDEN RING",
                earned={"bronze": 2, "silver": 1, "gold": 0, "platinum": 0},
                defined={"bronze": 3, "silver": 1, "gold": 1, "platinum": 1}, progress=40)
    base.update(kw)
    return TrackedGame(**base)


def test_grade_counts_fills_missing_grades_and_ignores_junk():
    assert grade_counts({"bronze": "3", "gold": None, "total": 99}) == {"bronze": 3, "silver": 0, "gold": 0, "platinum": 0}
    assert grade_counts(None) == {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}


def test_display_title_prefers_override_then_presence_name():
    g = psn()
    assert g.display_title == "ELDEN RING"
    g.extra["presence_name"] = "Elden Ring"
    assert g.display_title == "Elden Ring"
    g.title_override = "  My Ring  "
    assert g.display_title == "My Ring"
    assert TrackedGame(platform=STEAM, game_id="480").display_title == "480"


def test_psn_totals_and_completion():
    g = psn()
    assert (g.earned_total, g.defined_total, g.is_complete) == (3, 6, False)
    g.earned["platinum"] = 1
    assert g.has_platinum and g.is_complete


def test_psn_list_without_platinum_completes_at_100_percent():
    g = psn(defined={"bronze": 2}, earned={"bronze": 2}, progress=100)
    assert g.is_complete
    g.progress = 99
    assert not g.is_complete


def test_steam_totals_and_completion():
    g = TrackedGame(platform=STEAM, game_id="1", unlocked_ids=["a", "b"], total_achievements=2)
    assert (g.earned_total, g.defined_total, g.is_complete) == (2, 2, True)
    assert not TrackedGame(platform=STEAM, game_id="2").is_complete  # no achievements at all


def test_round_trip_and_from_dict_cleans_values():
    g = psn(hidden=True, extra={"presence_name": "Elden Ring"})
    back = TrackedGame.from_dict({**g.to_dict(), "unknown_field": 1, "progress": 140})
    assert back.hidden and back.extra["presence_name"] == "Elden Ring"
    assert back.progress == 100
    assert back.earned == g.earned


def test_public_payload_has_display_fields():
    p = psn(title_override="X").public()
    assert p["title"] == "X" and p["source_title"] == "ELDEN RING"
    assert p["earned"] == 3 and p["total"] == 6 and p["grades_total"]["platinum"] == 1
    steam = TrackedGame(platform=STEAM, game_id="1").public()
    assert steam["grades_earned"] == {}


def test_event_fields_are_flat_and_blank_safe():
    e = GameEvent(kind="unlock", platform=PSN, game_id="N", game_title="T", unlock_grade="gold")
    f = e.fields_for_events()
    assert f["platform_name"] == "PlayStation" and f["unlock_rarity"] == "" and f["milestone"] == ""
    e2 = GameEvent(kind="milestone", platform=STEAM, game_id="1", game_title="T", milestone=50, unlock_rarity=3.2)
    assert e2.fields_for_events()["milestone"] == 50 and e2.fields_for_events()["unlock_rarity"] == 3.2


def test_settings_from_dict_clamps_and_cleans():
    s = TrackerSettings.from_dict({"sync_minutes": 1, "cover_ttl_days": 999, "cover_missing_hours": 0,
                                   "milestones": [75, "25", 0, 100, 25, "x"], "export": "junk", "nope": 1})
    assert s.sync_minutes == 5 and s.cover_ttl_days == 90 and s.cover_missing_hours == 6
    assert s.milestones == [25, 75] and s.export == {}
    assert TrackerSettings.from_dict(None).milestones == [25, 50, 75]
    assert s.platform_enabled(PSN) and s.platform_enabled(STEAM)
    s.steam_enabled = False
    assert not s.platform_enabled(STEAM)


def test_clean_milestones_rejects_non_lists():
    assert clean_milestones("50") == []
    assert clean_milestones({10, 10, 99}) == [10, 99]

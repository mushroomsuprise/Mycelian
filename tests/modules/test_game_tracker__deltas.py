# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""What counts as a change: nothing before the first import; unlocks, milestones, completion after."""

from modules.game_tracker.deltas import diff
from modules.game_tracker.models import PSN, STEAM, TrackedGame


def psn(earned, progress, defined=None, **kw):
    return TrackedGame(platform=PSN, game_id="NPWR00001_00", title="Game", earned=dict(earned),
                       defined=defined or {"bronze": 10, "silver": 4, "gold": 2, "platinum": 1}, progress=progress, **kw)


def steam(unlocked, total=4, **kw):
    ids = list(unlocked)
    return TrackedGame(platform=STEAM, game_id="10", title="Steam Game", unlocked_ids=ids, total_achievements=total,
                       progress=round(len(ids) * 100 / total) if total else 0, **kw)


def kinds(events):
    return [(e.kind, e.unlock_grade or e.milestone) for e in events]


def test_nothing_before_the_first_import():
    assert diff(None, psn({"bronze": 5}, 30), baseline_done=False, milestones=[25]) == []
    assert diff(psn({}, 0), psn({"bronze": 5}, 30), baseline_done=False) == []


def test_unbaselined_game_reads_silently():
    old = psn({"bronze": 1}, 5, baselined=False)
    assert diff(old, psn({"bronze": 9, "platinum": 1}, 100), baseline_done=True, milestones=[25, 50]) == []


def test_psn_unlocks_one_event_per_trophy_bronze_first():
    old = psn({"bronze": 1}, 5)
    new = psn({"bronze": 3, "gold": 1}, 20)
    assert kinds(diff(old, new, baseline_done=True)) == [("unlock", "bronze"), ("unlock", "bronze"), ("unlock", "gold")]


def test_platinum_unlock_then_completed():
    old = psn({"bronze": 10, "silver": 4, "gold": 2}, 98)
    new = psn({"bronze": 10, "silver": 4, "gold": 2, "platinum": 1}, 100)
    assert kinds(diff(old, new, baseline_done=True)) == [("unlock", "platinum"), ("completed", None)]


def test_milestones_fire_once_each_even_in_one_jump():
    old = psn({"bronze": 1}, 10)
    new = psn({"bronze": 8}, 60)
    events = diff(old, new, baseline_done=True, milestones=[75, 25, 50, 50, 0, 100])
    assert [e.milestone for e in events if e.kind == "milestone"] == [25, 50]
    # Already past 25: only 50.
    assert [e.milestone for e in diff(psn({"bronze": 3}, 30), new, baseline_done=True, milestones=[25, 50]) if e.kind == "milestone"] == [50]


def test_new_game_after_import_is_started_plus_its_unlocks():
    events = diff(None, psn({"bronze": 2}, 12), baseline_done=True, milestones=[10])
    assert kinds(events) == [("started", None), ("unlock", "bronze"), ("unlock", "bronze"), ("milestone", 10)]
    assert events[0].game_title == "Game" and events[0].earned == 2 and events[0].total == 17


def test_steam_unlocks_by_name_and_perfect():
    old = steam(["a", "b"])
    new = steam(["a", "b", "c", "d"])
    events = diff(old, new, baseline_done=True, milestones=[75])
    assert kinds(events) == [("unlock", "achievement"), ("unlock", "achievement"), ("milestone", 75), ("completed", None)]
    assert [e.unlock_name for e in events[:2]] == ["c", "d"]


def test_no_change_no_events():
    g = steam(["a"])
    assert diff(g, steam(["a"]), baseline_done=True, milestones=[25]) == []


def test_completed_only_once():
    done = psn({"platinum": 1, "bronze": 10, "silver": 4, "gold": 2}, 100)
    assert diff(done, done, baseline_done=True) == []

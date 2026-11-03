# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""What changed between two reads of a game, as :class:`GameEvent` objects.

Nothing is a change until the platform's first import is done (``baseline_done``) and
the game itself has been read once (``old.baselined``). A game that first shows up
after the import is new play: it gets a ``started`` event and every unlock it has.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .models import COMPLETED, GRADES, MILESTONE, PSN, STARTED, UNLOCK, GameEvent, TrackedGame, grade_counts


def _event(kind: str, game: TrackedGame, **extra) -> GameEvent:
    return GameEvent(
        kind=kind,
        platform=game.platform,
        game_id=game.game_id,
        game_title=game.display_title,
        platform_label=game.platform_label,
        progress=game.progress,
        earned=game.earned_total,
        total=game.defined_total,
        **extra,
    )


def diff(
    old: Optional[TrackedGame],
    new: TrackedGame,
    *,
    baseline_done: bool,
    milestones: Sequence[int] = (),
) -> List[GameEvent]:
    """Events for the change from ``old`` to ``new`` (``old`` None: a game seen for the first time)."""
    if not baseline_done:
        return []
    if old is not None and not old.baselined:
        return []

    events: List[GameEvent] = []
    if old is None:
        events.append(_event(STARTED, new))

    if new.platform == PSN:
        before = grade_counts(old.earned) if old else {g: 0 for g in GRADES}
        after = grade_counts(new.earned)
        # Bronze first, platinum last: the order they're usually earned in.
        for grade in GRADES:
            for _ in range(max(0, after[grade] - before[grade])):
                events.append(_event(UNLOCK, new, unlock_grade=grade))
    else:
        known = set(old.unlocked_ids) if old else set()
        for api_name in new.unlocked_ids:
            if api_name not in known:
                events.append(_event(UNLOCK, new, unlock_grade="achievement", unlock_name=api_name))

    before_pct = old.progress if old else 0
    for m in sorted(set(int(x) for x in milestones)):
        if 0 < m < 100 and before_pct < m <= new.progress:
            events.append(_event(MILESTONE, new, milestone=m))

    was_complete = old.is_complete if old else False
    if new.is_complete and not was_complete:
        events.append(_event(COMPLETED, new))
    return events

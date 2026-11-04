# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Per-game goals: what the user wants to get done in a game, and how far along it is.

A goal is either typed in and counted by hand (``manual``: "Get all endings", 2 of 5) or
read from the game's progress: the platinum / every achievement (``complete``), N trophies
or achievements, optionally of one PSN grade (``count``), a completion percent
(``percent``) or one named trophy or achievement (``unlock``).

Everything here is pure: the service stores goals and sends the events this builds.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import GOAL_COMPLETED, GOAL_PROGRESS, GRADES, PSN, GameEvent, TrackedGame, grade_counts

MANUAL = "manual"
COMPLETE = "complete"
COUNT = "count"
PERCENT = "percent"
UNLOCK = "unlock"
GOAL_KINDS = (MANUAL, COMPLETE, COUNT, PERCENT, UNLOCK)
AUTO_KINDS = (COMPLETE, COUNT, PERCENT, UNLOCK)
GOAL_KIND_LABELS = {
    MANUAL: "Manual",
    COMPLETE: "Platinum / 100%",
    COUNT: "Trophies / achievements",
    PERCENT: "Completion %",
    UNLOCK: "Specific trophy / achievement",
}
MAX_TARGET = 100000


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def new_goal_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class GameGoal:
    id: str = field(default_factory=new_goal_id)
    platform: str = ""
    game_id: str = ""
    kind: str = MANUAL
    name: str = ""  # the user's label; blank means default_name()
    target: int = 1  # manual: count to reach; count: trophies; percent: percent; complete / unlock: 1
    current: int = 0  # manual only (auto goals read the game)
    grade: str = ""  # count on PSN: "" (any) or bronze / silver / gold / platinum
    unlock_id: str = ""  # unlock: Steam API name or PSN trophy ID
    unlock_name: str = ""
    unlock_icon: str = ""
    unlocked: bool = False  # unlock on PSN: last read of the trophy (Steam reads unlocked_ids)
    order: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    last_current: int = 0  # progress when last evaluated (progress events fire on a change)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.game_id}"

    @property
    def is_auto(self) -> bool:
        return self.kind in AUTO_KINDS

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "GameGoal":
        known = {f.name for f in fields(cls)}
        goal = cls(**{k: v for k, v in (raw or {}).items() if k in known})
        return clean_goal(goal)


def clean_goal(goal: GameGoal) -> GameGoal:
    """Clamp a goal's numbers and drop settings that don't fit its kind."""
    goal.id = str(goal.id or new_goal_id())
    goal.kind = goal.kind if goal.kind in GOAL_KINDS else MANUAL
    goal.name = str(goal.name or "").strip()
    goal.grade = str(goal.grade or "").lower()
    if goal.grade not in GRADES or goal.kind != COUNT or goal.platform != PSN:
        goal.grade = ""
    if goal.kind in (COMPLETE, UNLOCK):
        goal.target = 1
    elif goal.kind == PERCENT:
        goal.target = max(1, min(100, _int(goal.target, 100)))
    else:
        goal.target = max(1, min(MAX_TARGET, _int(goal.target, 1)))
    goal.current = max(0, min(goal.target, _int(goal.current))) if goal.kind == MANUAL else 0
    goal.unlock_id = str(goal.unlock_id or "") if goal.kind == UNLOCK else ""
    goal.unlock_name = str(goal.unlock_name or "") if goal.kind == UNLOCK else ""
    goal.unlock_icon = str(goal.unlock_icon or "") if goal.kind == UNLOCK else ""
    goal.unlocked = bool(goal.unlocked) if goal.kind == UNLOCK else False
    goal.order = _int(goal.order)
    goal.created_at = float(goal.created_at or 0) or time.time()
    goal.completed_at = float(goal.completed_at or 0)
    goal.last_current = max(0, _int(goal.last_current))
    return goal


def default_name(goal: GameGoal) -> str:
    psn = goal.platform == PSN
    if goal.kind == COMPLETE:
        return "Platinum" if psn else "All achievements"
    if goal.kind == COUNT:
        if psn:
            noun = "trophy" if goal.target == 1 else "trophies"
        else:
            noun = "achievement" if goal.target == 1 else "achievements"
        grade = f"{goal.grade.title()} " if goal.grade else ""
        return f"{goal.target:,} {grade}{noun}"
    if goal.kind == PERCENT:
        return f"Reach {goal.target}%"
    if goal.kind == UNLOCK:
        return goal.unlock_name or ("Unlock a trophy" if psn else "Unlock an achievement")
    return "Goal"


def display_name(goal: GameGoal) -> str:
    return goal.name or default_name(goal)


@dataclass
class GoalState:
    current: int
    target: int
    pct: int
    done: bool


def _pct(current: int, target: int, done: bool) -> int:
    if done:
        return 100
    if target <= 0:
        return 0
    return max(0, min(99, current * 100 // target))


def goal_state(goal: GameGoal, game: Optional[TrackedGame]) -> GoalState:
    """How far a goal is: ``current`` of ``target``, a percent (100 only when done) and done."""
    if goal.kind == MANUAL:
        done = goal.current >= goal.target
        return GoalState(goal.current, goal.target, _pct(goal.current, goal.target, done), done)
    if game is None:
        done = bool(goal.completed_at)
        current = goal.last_current
        return GoalState(current, goal.target, _pct(current, goal.target, done), done)
    if goal.kind == COMPLETE:
        done = game.is_complete
        current = 100 if done else max(0, min(99, game.progress))
        return GoalState(current, 100, current, done)
    if goal.kind == COUNT:
        if game.platform == PSN and goal.grade:
            current = grade_counts(game.earned).get(goal.grade, 0)
        else:
            current = game.earned_total
        done = current >= goal.target
        return GoalState(current, goal.target, _pct(current, goal.target, done), done)
    if goal.kind == PERCENT:
        current = max(0, min(100, game.progress))
        done = current >= goal.target
        return GoalState(current, goal.target, _pct(current, goal.target, done), done)
    # UNLOCK
    if game.platform == PSN:
        done = goal.unlocked
    else:
        done = bool(goal.unlock_id) and goal.unlock_id in game.unlocked_ids
    return GoalState(1 if done else 0, 1, 100 if done else 0, done)


def sorted_goals(goals: Iterable[GameGoal]) -> List[GameGoal]:
    return sorted(goals, key=lambda g: (g.order, g.created_at))


def goal_public(goal: GameGoal, game: Optional[TrackedGame]) -> Dict[str, Any]:
    """JSON for one goal (overlays, the API, chat and connectors)."""
    s = goal_state(goal, game)
    return {
        "id": goal.id,
        "kind": goal.kind,
        "kind_label": GOAL_KIND_LABELS.get(goal.kind, goal.kind),
        "auto": goal.is_auto,
        "name": display_name(goal),
        "current": s.current,
        "target": s.target,
        "progress": s.pct,
        "completed": s.done,
        "grade": goal.grade,
        "unlock_id": goal.unlock_id,
        "unlock_name": goal.unlock_name,
        "unlock_icon": goal.unlock_icon,
        "order": goal.order,
        "created_at": goal.created_at,
        "completed_at": goal.completed_at,
    }


def goals_summary(goals: Sequence[GameGoal], game: Optional[TrackedGame]) -> Dict[str, Any]:
    """Counts over one game's goals plus the next one to finish (the furthest along that isn't done)."""
    rows = [goal_public(g, game) for g in sorted_goals(goals)]
    done = sum(1 for r in rows if r["completed"])
    open_rows = [r for r in rows if not r["completed"]]
    nxt = max(open_rows, key=lambda r: r["progress"], default=None) if open_rows else None
    return {
        "goals": rows,
        "goals_total": len(rows),
        "goals_done": done,
        "goals_progress": round(sum(r["progress"] for r in rows) / len(rows)) if rows else 0,
        "goals_complete": bool(rows) and done == len(rows),
        "next_goal": nxt,
    }


def cover_path(platform: str, game_id: str) -> str:
    return f"/api/game-tracker/cover/{platform}/{game_id}.jpg"


def game_payload(game: TrackedGame, goals: Sequence[GameGoal] = (), speedruns: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """A game as overlays, the API and lookups see it: ``TrackedGame.public()`` plus its goals and its
    speedrun info, one row per split file (``speedrun.speedruns_public``)."""
    out = game.public()
    out["key"] = game.key
    out["cover"] = cover_path(game.platform, game.game_id)
    out.update(goals_summary(goals, game))
    out["speedruns"] = list(speedruns)
    return out


def goals_text(rows: Sequence[Dict[str, Any]]) -> str:
    """"Get all endings 2/5 (40%), Platinum (100%)" for chat."""
    parts = []
    for r in rows:
        if r["kind"] in (MANUAL, COUNT):
            parts.append(f"{r['name']} {r['current']:,}/{r['target']:,} ({r['progress']}%)")
        else:
            parts.append(f"{r['name']} ({r['progress']}%)")
    return ", ".join(parts)


def evaluate(
    game: Optional[TrackedGame], goals: Sequence[GameGoal], *, emit: bool, now: Optional[float] = None
) -> Tuple[List[GameGoal], List[GameEvent]]:
    """Bring each goal's bookkeeping up to date with the game.

    Returns the goals that changed (to store) and, when ``emit``, a ``goal_completed`` event for
    each goal that just got done and a ``goal_progress`` event for each other one that moved.
    """
    now = time.time() if now is None else now
    ordered = sorted_goals(goals)
    states = [(g, goal_state(g, game)) for g in ordered]
    total = len(states)
    done_count = sum(1 for _, s in states if s.done)
    changed: List[GameGoal] = []
    events: List[GameEvent] = []
    for goal, s in states:
        moved = s.current != goal.last_current
        finished = s.done and not goal.completed_at
        reopened = not s.done and bool(goal.completed_at)
        if not (moved or finished or reopened):
            continue
        goal.last_current = s.current
        if finished:
            goal.completed_at = now
        elif reopened:
            goal.completed_at = 0.0
        changed.append(goal)
        # A goal that was already done stays quiet while its count keeps climbing (15 of 10 trophies).
        if emit and game is not None and (finished or (moved and not s.done)):
            events.append(goal_event(GOAL_COMPLETED if finished else GOAL_PROGRESS, game, goal, s, done_count, total, now))
    return changed, events


def goal_event(kind: str, game: TrackedGame, goal: GameGoal, s: GoalState, done: int, total: int, ts: float) -> GameEvent:
    return GameEvent(
        kind=kind,
        platform=game.platform,
        game_id=game.game_id,
        game_title=game.display_title,
        platform_label=game.platform_label,
        progress=game.progress,
        earned=game.earned_total,
        total=game.defined_total,
        goal_id=goal.id,
        goal_name=display_name(goal),
        goal_kind=goal.kind,
        goal_current=s.current,
        goal_target=s.target,
        goal_progress=s.pct,
        goals_done=done,
        goals_total=total,
        ts=ts,
    )


def adjust(goal: GameGoal, op: str, amount: Any = 1) -> bool:
    """Change a manual goal's count: ``add``, ``subtract``, ``set``, ``complete`` or ``reset``.

    Returns False for an auto goal or an unknown operation (nothing changes).
    """
    if goal.kind != MANUAL:
        return False
    n = _int(amount, 1)
    op = str(op or "").lower()
    if op == "add":
        value = goal.current + n
    elif op == "subtract":
        value = goal.current - n
    elif op == "set":
        value = n
    elif op == "complete":
        value = goal.target
    elif op == "reset":
        value = 0
    else:
        return False
    goal.current = max(0, min(goal.target, value))
    return True

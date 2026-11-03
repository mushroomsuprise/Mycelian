# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Data shapes for the Games Tracker: tracked games, change events and settings."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

PSN = "psn"
STEAM = "steam"
PLATFORMS = (PSN, STEAM)
PLATFORM_LABELS = {PSN: "PlayStation", STEAM: "Steam"}

GRADES = ("bronze", "silver", "gold", "platinum")

# GameEvent kinds
UNLOCK = "unlock"
COMPLETED = "completed"
STARTED = "started"
MILESTONE = "milestone"
EVENT_KINDS = (UNLOCK, COMPLETED, STARTED, MILESTONE)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def grade_counts(raw: Any) -> Dict[str, int]:
    """{bronze, silver, gold, platinum} from a dict (missing grades count as 0)."""
    raw = raw if isinstance(raw, dict) else {}
    return {g: _int(raw.get(g)) for g in GRADES}


@dataclass
class TrackedGame:
    """One game in the library. PSN games keep per-grade counts; Steam games keep unlocked achievement names."""

    platform: str
    game_id: str  # NPWR..._00 for PSN, the app ID for Steam
    title: str = ""  # name from the platform (trophy list name / Steam store name)
    title_override: str = ""  # display title the user chose
    platform_label: str = ""  # PS5, PS4, ... or Steam
    np_title_id: str = ""
    cover_url: str = ""
    earned: Dict[str, int] = field(default_factory=dict)  # PSN: per grade
    defined: Dict[str, int] = field(default_factory=dict)  # PSN: per grade
    unlocked_ids: List[str] = field(default_factory=list)  # Steam: unlocked achievement API names
    total_achievements: int = 0  # Steam
    progress: int = 0  # 0-100 (PSN's own weighted value; Steam unlocked/total)
    playtime_min: int = 0  # Steam
    last_played: float = 0.0
    first_seen: float = 0.0
    updated_at: float = 0.0
    hidden: bool = False
    has_achievements: bool = True
    # False until this game's progress has been read once; the first read is never a change.
    baselined: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.game_id}"

    @property
    def display_title(self) -> str:
        """The user's title, else the PSN presence name (nicer casing than most trophy lists), else the platform's."""
        return (
            (self.title_override or "").strip()
            or str(self.extra.get("presence_name") or "").strip()
            or self.title
            or self.game_id
        )

    @property
    def earned_total(self) -> int:
        if self.platform == PSN:
            return sum(grade_counts(self.earned).values())
        return len(self.unlocked_ids)

    @property
    def defined_total(self) -> int:
        if self.platform == PSN:
            return sum(grade_counts(self.defined).values())
        return self.total_achievements

    @property
    def has_platinum(self) -> bool:
        return self.platform == PSN and grade_counts(self.earned)["platinum"] > 0

    @property
    def is_complete(self) -> bool:
        """Platinum (PSN), every achievement (Steam), or 100% for lists without a platinum."""
        if self.platform == PSN:
            if self.has_platinum:
                return True
            return grade_counts(self.defined)["platinum"] == 0 and self.defined_total > 0 and self.progress >= 100
        return self.total_achievements > 0 and len(self.unlocked_ids) >= self.total_achievements

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "TrackedGame":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in (raw or {}).items() if k in known}
        game = cls(**data)
        game.earned = grade_counts(game.earned) if game.platform == PSN else dict(game.earned or {})
        game.defined = grade_counts(game.defined) if game.platform == PSN else dict(game.defined or {})
        game.unlocked_ids = [str(x) for x in (game.unlocked_ids or [])]
        game.progress = max(0, min(100, _int(game.progress)))
        return game

    def public(self) -> Dict[str, Any]:
        """JSON for the API, overlays and placeholders (no internal bookkeeping)."""
        return {
            "platform": self.platform,
            "id": self.game_id,
            "title": self.display_title,
            "source_title": self.title,
            "platform_label": self.platform_label,
            "np_title_id": self.np_title_id,
            "progress": self.progress,
            "earned": self.earned_total,
            "total": self.defined_total,
            "grades_earned": grade_counts(self.earned) if self.platform == PSN else {},
            "grades_total": grade_counts(self.defined) if self.platform == PSN else {},
            "completed": self.is_complete,
            "playtime_min": self.playtime_min,
            "last_played": self.last_played,
            "first_seen": self.first_seen,
            "hidden": self.hidden,
            "has_achievements": self.has_achievements,
        }


@dataclass
class GameEvent:
    """One change after the silent first import: an unlock, a completion, a new game or a milestone."""

    kind: str
    platform: str
    game_id: str
    game_title: str
    platform_label: str = ""
    progress: int = 0
    earned: int = 0
    total: int = 0
    unlock_name: str = ""
    unlock_grade: str = ""  # bronze/silver/gold/platinum, or "achievement"
    unlock_description: str = ""
    unlock_rarity: Optional[float] = None  # percent of players
    unlock_icon: str = ""
    milestone: Optional[int] = None
    ts: float = field(default_factory=time.time)

    def fields_for_events(self) -> Dict[str, Any]:
        """Flat fields shared by chatbot events, connector triggers and overlays."""
        return {
            "kind": self.kind,
            "platform": self.platform,
            "platform_name": PLATFORM_LABELS.get(self.platform, self.platform),
            "platform_label": self.platform_label,
            "game_id": self.game_id,
            "game_title": self.game_title,
            "progress": self.progress,
            "earned": self.earned,
            "total": self.total,
            "unlock_name": self.unlock_name,
            "unlock_grade": self.unlock_grade,
            "unlock_description": self.unlock_description,
            "unlock_rarity": "" if self.unlock_rarity is None else self.unlock_rarity,
            "unlock_icon": self.unlock_icon,
            "milestone": "" if self.milestone is None else self.milestone,
            "timestamp": self.ts,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_MILESTONES = [25, 50, 75]


@dataclass
class TrackerSettings:
    psn_enabled: bool = True
    steam_enabled: bool = True
    sync_minutes: int = 10
    cover_ttl_days: int = 7
    cover_missing_hours: int = 6
    milestones: List[int] = field(default_factory=lambda: list(DEFAULT_MILESTONES))
    record_stats: bool = True
    send_events: bool = True
    export: Dict[str, Any] = field(default_factory=dict)  # remembered export-image options

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "TrackerSettings":
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in (raw or {}).items() if k in known})
        s.sync_minutes = max(5, min(120, _int(s.sync_minutes) or 10))
        s.cover_ttl_days = max(1, min(90, _int(s.cover_ttl_days) or 7))
        s.cover_missing_hours = max(1, min(72, _int(s.cover_missing_hours) or 6))
        s.milestones = clean_milestones(s.milestones)
        s.export = dict(s.export or {}) if isinstance(s.export, dict) else {}
        return s

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def platform_enabled(self, platform: str) -> bool:
        return self.psn_enabled if platform == PSN else self.steam_enabled


def clean_milestones(values: Any) -> List[int]:
    """Whole percents between 1 and 99, sorted, no repeats."""
    out = set()
    for v in values if isinstance(values, (list, tuple, set)) else []:
        n = _int(v)
        if 1 <= n <= 99:
            out.add(n)
    return sorted(out)

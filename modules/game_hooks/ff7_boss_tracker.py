"""In-memory FF7 boss defeat tracking for game hook payloads (session-only)."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Set

_BOSS_SCENE_IDS: Set[int] = {
    128,
    256,
    384,
    400,
    416,
    432,
    448,
    464,
    480,
    496,
    512,
    528,
    544,
    560,
}
_BOSS_NAME_SUBSTR = (
    "weapon",
    "jenova",
    "safer",
    "sephiroth",
    "ruby",
    "emerald",
    "diamond",
    "hell house",
    "rapps",
    "hundred",
    "palmer",
    "reno",
    "rude",
    "elena",
    "tseng",
    "hojo",
    "sample",
)


def is_boss_actor(actor: Dict[str, Any]) -> bool:
    sid = int(actor.get("scene_id") or 0)
    if sid in _BOSS_SCENE_IDS:
        return True
    name = (actor.get("name") or "").lower()
    return any(s in name for s in _BOSS_NAME_SUBSTR)


class Ff7BossTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boss_names: List[str] = []
        self._boss_last: str = ""
        self._prev_battle_enemies: Dict[int, int] = {}

    def clear(self) -> None:
        with self._lock:
            self._boss_names.clear()
            self._boss_last = ""
            self._prev_battle_enemies.clear()

    def bosses_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "names": list(self._boss_names),
                "last": self._boss_last,
                "count": len(self._boss_names),
            }

    def update_from_snapshot(self, snap: Dict[str, Any]) -> None:
        if not snap.get("battle"):
            with self._lock:
                self._prev_battle_enemies.clear()
            return
        enemies = snap.get("enemies") or []
        with self._lock:
            current: Dict[int, int] = {}
            for e in enemies:
                slot = int(e.get("slot", -1))
                hp = int(e.get("hp", 0))
                current[slot] = hp
                prev = self._prev_battle_enemies.get(slot)
                if prev is not None and prev > 0 and hp <= 0 and is_boss_actor(e):
                    name = (e.get("name") or "Unknown").strip()
                    if name and (not self._boss_names or self._boss_names[-1] != name):
                        self._boss_names.append(name)
                        self._boss_last = name
                self._prev_battle_enemies[slot] = hp
            for slot in list(self._prev_battle_enemies.keys()):
                if slot not in current:
                    del self._prev_battle_enemies[slot]

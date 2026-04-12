"""FF7 boss defeat tracking for game hook payloads (newest-first name list)."""

from __future__ import annotations

import threading
from typing import Any, Dict, List

# Lowercase substrings matched against battle enemy names (FFVII PC / common ports).
# Curated from story bosses; extras removed. Variants cover punctuation/spacing in RAM.
_BOSS_NAME_SUBSTR: frozenset[str] = frozenset(
    {
        # --- Guard Scorpion through Gi Nattak ---
        "guard scorpion",
        "air buster",
        "airbuster",
        "aps",
        "reno",
        "sample:h0512",
        "sample h0512",
        "hundred gunner",
        "heli gunner",
        "heligunner",
        "rufus",
        "motor ball",
        "motorball",
        "bottomswell",
        "jenova birth",
        "dyne",
        "gi nattak",
        "materia keeper",
        "palmer",
        "red dragon",
        "demons gate",
        "demon's gate",
        "demons' gate",
        "jenova life",
        "schizo",
        "jenova death",
        "ultimate weapon",
        "carry armor",
        "diamond weapon",
        "rude",
        "elena",
        "proud clod",
        # Lifeform-Hojo N: hyphen, space, or ASCII lookalikes in dumps
        "lifeform-hojo n",
        "lifeform-hojo",
        "lifeform hojo n",
        "lifeform hojo",
        "lifeformhojo",
        "jenova synthesis",
        "jenova syn",
        # Katakana middle dot (some builds / fonts between Jenova and SYNTHESIS)
        "jenova\u30fbsynthesis",
        "bizarro sephiroth",
        "bizarro",
        "safer sephiroth",
        "safer",
    }
)

# Names that contain a boss substring but are adds/minions, not the boss kill credit.
_BOSS_NAME_EXCLUDE: frozenset[str] = frozenset(
    {
        "sample:h0512-opt",
        "sample h0512-opt",
        "h0512-opt",
    }
)


def is_boss_actor(actor: Dict[str, Any]) -> bool:
    name = (actor.get("name") or "").lower()
    if any(ex in name for ex in _BOSS_NAME_EXCLUDE):
        return False
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

    def restore(self, names: List[str], last: str = "") -> None:
        """Replace log from persisted storage (names newest-first)."""
        clean: List[str] = []
        for n in names:
            if isinstance(n, str) and n.strip():
                clean.append(n.strip())
        with self._lock:
            self._boss_names = clean
            if last and isinstance(last, str) and last.strip():
                self._boss_last = last.strip()
            elif clean:
                self._boss_last = clean[0]
            else:
                self._boss_last = ""

    def update_from_snapshot(self, snap: Dict[str, Any]) -> bool:
        """Update from battle snapshot. Returns True if a new boss defeat was recorded."""
        if not snap.get("battle"):
            with self._lock:
                self._prev_battle_enemies.clear()
            return False
        enemies = snap.get("enemies") or []
        added = False
        with self._lock:
            current: Dict[int, int] = {}
            for e in enemies:
                slot = int(e.get("slot", -1))
                hp = int(e.get("hp", 0))
                current[slot] = hp
                prev = self._prev_battle_enemies.get(slot)
                if prev is not None and prev > 0 and hp <= 0 and is_boss_actor(e):
                    name = (e.get("name") or "Unknown").strip()
                    if name and (not self._boss_names or self._boss_names[0] != name):
                        self._boss_names.insert(0, name)
                        self._boss_last = name
                        added = True
                self._prev_battle_enemies[slot] = hp
            for slot in list(self._prev_battle_enemies.keys()):
                if slot not in current:
                    del self._prev_battle_enemies[slot]
        return added

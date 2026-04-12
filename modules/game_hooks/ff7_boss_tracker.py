"""In-memory FF7 boss defeat tracking for game hook payloads (session-only)."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Set

# Enemy scene indices that often correspond to boss formations (heuristic).
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

# Distinctive lowercase name fragments (FFVII PC). Avoid very short/generic tokens.
_BOSS_NAME_SUBSTR: frozenset[str] = frozenset(
    {
        "guard scorpion",
        "airbuster",
        "air buster",
        "aps",
        "materia keeper",
        "midgar zolom",
        "motor ball",
        "motorball",
        "hundred gunner",
        "heli gunner",
        "heligunner",
        "dark nation",
        "rufus",
        "bottomswell",
        "jenova birth",
        "jenova life",
        "jenova death",
        "jenova syn",
        "lifeform",
        "hojo",
        "sample",
        "palmer",
        "schizo",
        "blue dragon",
        "gas duct",
        "carry armor",
        "reno",
        "rude",
        "elena",
        "tseng",
        "turks",
        "diamond weapon",
        "ruby weapon",
        "emerald weapon",
        "ultimate weapon",
        "hell house",
        "rapps",
        "gi nattak",
        "stilva",
        "magic pot",
        "magic pots",
        "ho-chu",
        "hochu",
        "tonberry king",
        "master tonberry",
        "mark vi",
        "mark v",
        "snow witch",
        "gorkii",
        "shake",
        "chekhov",
        "staniv",
        "godo",
        "godoh",
        "proud clod",
        "mobile armor",
        "bizarro",
        "safer sephiroth",
        "safer",
        "sephiroth",
        "jenova",
        "lost number",
        "demons gate",
        "demon's gate",
        "demons' gate",
        "red dragon",
        "dragon zombie",
        "mystery ninja",
        "cargo ship",
        "cp47",
        "cmd grand horn",
        "grand horn",
        "grenade combatant",
        "raptor",
        "eagle gun",
        "adamantaimai",
        "jamar armor",
        "serpent",
        "stinger",
        "valkyrie",
        "ghost ship",
        "ironite",
        "stingray",
        "max ray",
        "spencer",
        "blobra",
        "evilhead",
        "evil head",
        "headbonk",
        "gospa",
        "trickplay",
        "doorbull",
        "cannon",
        "proto machinegun",
        "machinegun",
        "pincer",
        "scotch",
        "type-",
        "type 0",
        "left arm",
        "right arm",
        "hundred",
        "palmer",
        "sample:101",
        "sample:102",
        "sample:103",
        "vincent",
        "yuffie",
        "bossanova",
        "sinspawn",
        "sin spawn",
        "gears",
        "a mystery",
        "ancient dragon",
        "underwater",
        "life-form",
        "life form",
        "president shinra",
        "shinra beta",
        "midgardsormr",
    }
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

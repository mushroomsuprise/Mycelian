"""FF7 boss defeat tracking for game hook payloads (newest-first name list)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Template config element ids (templates/template_configs/ff7.json).
FF7_BOSS_SUBSTRINGS_ELEMENT_ID = "BossMatchSubstrings"
FF7_BOSS_EXCLUDES_ELEMENT_ID = "BossMatchExcludes"

# Defaults when config is missing or include list parses empty (do not silently disable detection).
_DEFAULT_BOSS_SUBSTR: frozenset[str] = frozenset(
    {
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
        "jenova",
        "jenova birth",
        "jenova\u30fbbirth",
        "dyne",
        "gi nattak",
        "materia keeper",
        "palmer",
        "red dragon",
        "demons gate",
        "demon's gate",
        "demons' gate",
        "jenova life",
        "jenova\u30fblife",
        "schizo",
        "jenova death",
        "jenova\u30fbdeath",
        "ultimate weapon",
        "carry armor",
        "diamond weapon",
        "rude",
        "elena",
        "proud clod",
        "lifeform-hojo n",
        "lifeform-hojo",
        "lifeform hojo n",
        "lifeform hojo",
        "lifeformhojo",
        "sephiroth",
        "jenova synthesis",
        "jenova syn",
        "jenova\u30fbsynthesis",
        "bizarro sephiroth",
        "bizarro",
        "safer sephiroth",
        "safer",
    }
)

_DEFAULT_BOSS_EXCLUDE: frozenset[str] = frozenset(
    {
        "sample:h0512-opt",
        "sample h0512-opt",
        "h0512-opt",
    }
)


def parse_line_patterns(text: Any) -> frozenset[str]:
    """Split multiline template text into lowercase stripped non-empty lines."""
    if not isinstance(text, str):
        return frozenset()
    out: set[str] = set()
    for line in text.splitlines():
        s = line.strip().lower()
        if s:
            out.add(s)
    return frozenset(out)


def _element_string_value(config: Dict[str, Any], element_id: str) -> str:
    for el in config.get("elements") or []:
        if not isinstance(el, dict) or el.get("id") != element_id:
            continue
        v = el.get("value")
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return str(v)
    return ""


def ff7_boss_match_sets_from_config(config: Dict[str, Any]) -> Tuple[frozenset[str], frozenset[str]]:
    """
    Read match substrings and excludes from template config elements.
    If the include set is empty after parsing, use built-in defaults and log a warning.
    """
    substr = parse_line_patterns(
        _element_string_value(config, FF7_BOSS_SUBSTRINGS_ELEMENT_ID)
    )
    exclude = parse_line_patterns(
        _element_string_value(config, FF7_BOSS_EXCLUDES_ELEMENT_ID)
    )
    if not substr:
        logger.warning(
            "FF7 boss match substrings empty or missing in template config; using built-in defaults"
        )
        substr = _DEFAULT_BOSS_SUBSTR
    return substr, exclude


class Ff7BossTracker:
    def __init__(self, name_substrings: frozenset[str], name_excludes: frozenset[str]) -> None:
        self._lock = threading.Lock()
        self._substr = name_substrings
        self._exclude = name_excludes
        self._boss_names: List[str] = []
        self._boss_last: str = ""
        self._prev_battle_enemies: Dict[int, int] = {}

    def set_match_sets(
        self, name_substrings: frozenset[str], name_excludes: frozenset[str]
    ) -> None:
        with self._lock:
            self._substr = name_substrings
            self._exclude = name_excludes

    def _is_boss_actor(self, actor: Dict[str, Any]) -> bool:
        name = (actor.get("name") or "").lower()
        if any(ex in name for ex in self._exclude):
            return False
        return any(s in name for s in self._substr)

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
                if prev is not None and prev > 0 and hp <= 0 and self._is_boss_actor(e):
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

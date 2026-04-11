"""
Broadcast live game data to all Socket.IO clients on a fixed cadence.

Event: ``game_hook_payload`` — envelope::

    { "v": 1, "ts": <unix_ms>, "hooks": { "ff7": { ... } } }

Commands from templates (see web_engine ``game_hook_command``)::

    { "hook": "ff7", "action": "clear_bosses" }

Boss defeat log for FF7 is kept **in memory only** (cleared on Mycelian restart).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

from .database_manager import database_manager

logger = logging.getLogger(__name__)

_FF7_DB_PATH = "GameHooks/ff7_enabled"
_INTERVAL = 0.25

# Curated boss-ish names / scene ids (expand as needed).
_BOSS_SCENE_IDS: Set[int] = {
    128, 256, 384, 400, 416, 432, 448, 464, 480, 496, 512, 528, 544, 560,
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


def _ff7_enabled() -> bool:
    try:
        raw = database_manager.get_data(_FF7_DB_PATH)
        if isinstance(raw, dict) and "enabled" in raw:
            return bool(raw["enabled"])
        if isinstance(raw, bool):
            return raw
    except Exception as e:
        logger.debug("game_hooks ff7 flag: %s", e)
    return False


def _is_boss_actor(actor: Dict[str, Any]) -> bool:
    sid = int(actor.get("scene_id") or 0)
    if sid in _BOSS_SCENE_IDS:
        return True
    name = (actor.get("name") or "").lower()
    return any(s in name for s in _BOSS_NAME_SUBSTR)


class GameHooksServiceImpl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._ff7_reader = None
        self._boss_names: List[str] = []
        self._boss_last: str = ""
        self._prev_battle_enemies: Dict[int, int] = {}
        self._emit_count = 0

    def clear_ff7_bosses(self) -> None:
        with self._lock:
            self._boss_names.clear()
            self._boss_last = ""
            self._prev_battle_enemies.clear()

    def _update_boss_tracking(self, snap: Dict[str, Any]) -> None:
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
                if prev is not None and prev > 0 and hp <= 0 and _is_boss_actor(e):
                    name = (e.get("name") or "Unknown").strip()
                    if name and (not self._boss_names or self._boss_names[-1] != name):
                        self._boss_names.append(name)
                        self._boss_last = name
                self._prev_battle_enemies[slot] = hp
            for slot in list(self._prev_battle_enemies.keys()):
                if slot not in current:
                    del self._prev_battle_enemies[slot]

    def _emit(self, payload: Dict[str, Any]) -> None:
        try:
            from . import web_engine

            inst = getattr(web_engine, "web_engine_instance", None)
            if inst and getattr(inst, "socketio", None):
                app = getattr(inst, "app", None)
                if app is not None:
                    with app.app_context():
                        inst.socketio.emit(
                            "game_hook_payload", payload, namespace="/"
                        )
                else:
                    inst.socketio.emit(
                        "game_hook_payload", payload, namespace="/"
                    )
        except Exception as e:
            logger.warning("game_hook_payload emit failed: %s", e, exc_info=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            hooks: Dict[str, Any] = {}
            if _ff7_enabled():
                if self._ff7_reader is None:
                    from .ff7_reader import FF7Reader

                    self._ff7_reader = FF7Reader()
                try:
                    snap = self._ff7_reader.snapshot()
                    self._update_boss_tracking(snap)
                    with self._lock:
                        snap["bosses"] = {
                            "names": list(self._boss_names),
                            "last": self._boss_last,
                            "count": len(self._boss_names),
                        }
                    hooks["ff7"] = snap
                except Exception as e:
                    logger.warning("FF7 snapshot error: %s", e, exc_info=True)
                    hooks["ff7"] = {
                        "hook": "ff7",
                        "attached": False,
                        "error": str(e),
                        "battle": False,
                        "current_module": 0,
                        "party": [],
                        "enemies": [],
                        "gil": 0,
                        "playtime_seconds": 0,
                        "playtime_text": "--:--:--",
                        "bosses": {"names": [], "last": "", "count": 0},
                        "debug": {"stage": "snapshot_exception", "message": str(e)},
                    }
            else:
                if self._ff7_reader is not None:
                    try:
                        self._ff7_reader.close()
                    except Exception:
                        pass
                    self._ff7_reader = None
                hooks["ff7"] = {
                    "hook": "ff7",
                    "attached": False,
                    "error": None,
                    "disabled": True,
                    "battle": False,
                    "current_module": 0,
                    "party": [],
                    "enemies": [],
                    "gil": 0,
                    "playtime_seconds": 0,
                    "playtime_text": "--:--:--",
                    "bosses": {"names": [], "last": "", "count": 0},
                    "debug": {"stage": "ff7_disabled_in_settings"},
                }

            payload = {
                "v": 1,
                "ts": int(time.time() * 1000),
                "hooks": hooks,
            }
            self._emit_count += 1
            ff = hooks.get("ff7") or {}
            if self._emit_count <= 3 or self._emit_count % 20 == 0:
                logger.info(
                    "[GameHooks] emit #%s ts=%s ff7 attached=%s disabled=%s err=%s "
                    "battle=%s gil=%s party=%s debug=%s",
                    self._emit_count,
                    payload["ts"],
                    ff.get("attached"),
                    ff.get("disabled"),
                    ff.get("error"),
                    ff.get("battle"),
                    ff.get("gil"),
                    len(ff.get("party") or []),
                    ff.get("debug"),
                )
            self._emit(payload)

            elapsed = time.time() - t0
            wait = max(0.0, _INTERVAL - elapsed)
            if self._stop.wait(wait):
                break

        if self._ff7_reader is not None:
            try:
                self._ff7_reader.close()
            except Exception:
                pass
            self._ff7_reader = None
        logger.debug("Game hooks service thread exit")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="GameHooksService", daemon=True
        )
        self._thread.start()
        logger.info("Game hooks service started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("Game hooks service stopped")


game_hooks_service = GameHooksServiceImpl()


def handle_game_hook_command(data: Any) -> None:
    """Handle client command dict."""
    if not isinstance(data, dict):
        return
    hook = data.get("hook")
    action = data.get("action")
    if hook == "ff7" and action == "clear_bosses":
        game_hooks_service.clear_ff7_bosses()

"""
Broadcast live game data to all Socket.IO clients on a fixed cadence.

Event: ``game_hook_payload`` — envelope::

    { "v": 1, "ts": <unix_ms>, "hooks": { "ff7": { ... } } }

Commands from templates (see web_engine ``game_hook_command``)::

    { "hook": "ff7", "action": "clear_bosses" }

Boss defeat log for FF7 is kept **in memory only** (cleared on Mycelian restart).

Connector and template writes are serialized on the hook polling thread via a queue.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional, Tuple

from .database_manager import database_manager
from .game_hooks import Ff7BossTracker, create_hook_instance
from .game_hooks.ff7_hook import FF7Hook

logger = logging.getLogger(__name__)

_FF7_DB_PATH = "GameHooks/ff7_enabled"
_INTERVAL = 0.25


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


class GameHooksServiceImpl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ff7_hook: Optional[FF7Hook] = None
        self._ff7_boss_tracker = Ff7BossTracker()
        self._emit_count = 0
        self._write_queue: "queue.Queue[Tuple[concurrent.futures.Future[Any], str, str, Dict[str, Any]]]" = (
            queue.Queue()
        )

    def clear_ff7_bosses(self) -> None:
        self._ff7_boss_tracker.clear()

    def enqueue_game_hook_write(
        self, game_id: str, operation: str, arguments: Optional[Dict[str, Any]] = None
    ) -> concurrent.futures.Future:
        """Schedule a write on the hook thread. Returns a concurrent.futures.Future."""
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._write_queue.put((fut, game_id, operation, dict(arguments or {})))
        return fut

    def _drain_write_queue(self) -> None:
        while True:
            try:
                fut, game_id, op, kwargs = self._write_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if game_id != "ff7" or self._ff7_hook is None:
                    fut.set_result((False, f"No active hook for '{game_id}'"))
                    continue
                result = self._ff7_hook.execute_operation(op, kwargs)
                fut.set_result(result)
            except Exception as e:
                logger.warning("game hook write failed: %s", e, exc_info=True)
                fut.set_result((False, str(e)))

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
            self._drain_write_queue()

            hooks: Dict[str, Any] = {}
            if _ff7_enabled():
                if self._ff7_hook is None:
                    inst = create_hook_instance("ff7")
                    assert isinstance(inst, FF7Hook)
                    self._ff7_hook = inst
                try:
                    snap = self._ff7_hook.snapshot()
                    self._ff7_boss_tracker.update_from_snapshot(snap)
                    snap["bosses"] = self._ff7_boss_tracker.bosses_dict()
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
                if self._ff7_hook is not None:
                    try:
                        self._ff7_hook.close()
                    except Exception:
                        pass
                    self._ff7_hook = None
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

        if self._ff7_hook is not None:
            try:
                self._ff7_hook.close()
            except Exception:
                pass
            self._ff7_hook = None
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

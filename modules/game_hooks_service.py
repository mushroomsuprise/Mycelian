"""
Broadcast live game data to all Socket.IO clients on a fixed cadence.

Event: ``game_hook_payload`` — envelope::

    { "v": 1, "ts": <unix_ms>, "hooks": { "ff7": { ... } } }

Commands from templates (see web_engine ``game_hook_command``)::

    { "hook": "ff7", "action": "clear_bosses" }

Boss defeat log for FF7 is persisted under ``GameHooks/ff7_boss_log`` (SQLite via
``database_manager``). Reset only via template ``clear_bosses`` command.

Connector and template writes are serialized on the hook polling thread via a queue.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
import weakref
from typing import Any, Dict, Optional, Tuple

from .database_manager import database_manager
from .game_hooks import Ff7BossTracker, create_hook_instance
from .game_hooks.ff7_boss_tracker import ff7_boss_match_sets_from_config
from .game_hooks.ff7_hook import FF7Hook
from .template_config_parser import TemplateConfigParser

logger = logging.getLogger(__name__)

_FF7_DB_PATH = "GameHooks/ff7_enabled"
_FF7_BOSS_LOG_PATH = "GameHooks/ff7_boss_log"
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
        self._ui_state_lock = threading.Lock()
        self._last_ff7_ui: Optional[Dict[str, Any]] = None
        self._ff7_hook: Optional[FF7Hook] = None
        self._template_config_parser = TemplateConfigParser()
        sub, excl = ff7_boss_match_sets_from_config(
            self._template_config_parser.load_config("ff7")
        )
        self._ff7_boss_tracker = Ff7BossTracker(sub, excl)
        self._emit_count = 0
        self._write_queue: "queue.Queue[Tuple[concurrent.futures.Future[Any], str, str, Dict[str, Any]]]" = (
            queue.Queue()
        )
        self._weak_self = weakref.ref(self)
        self._load_ff7_boss_log()

    def _load_ff7_boss_log(self) -> None:
        try:
            raw = database_manager.get_data(_FF7_BOSS_LOG_PATH)
            if not isinstance(raw, dict):
                return
            names = raw.get("names")
            if not isinstance(names, list):
                return
            last = raw.get("last") if isinstance(raw.get("last"), str) else ""
            self._ff7_boss_tracker.restore(names, last)
        except Exception as e:
            logger.warning("FF7 boss log load failed: %s", e, exc_info=True)

    def _persist_ff7_boss_log(self) -> None:
        try:
            database_manager.set_data(
                _FF7_BOSS_LOG_PATH, self._ff7_boss_tracker.bosses_dict()
            )
        except Exception as e:
            logger.warning("FF7 boss log persist failed: %s", e, exc_info=True)

    def clear_ff7_bosses(self) -> None:
        self._ff7_boss_tracker.clear()
        self._persist_ff7_boss_log()

    def reload_ff7_boss_match_sets(self) -> None:
        """Reload boss substring/exclude sets from the ff7 template config on disk."""
        try:
            cfg = self._template_config_parser.load_config("ff7")
            sub, excl = ff7_boss_match_sets_from_config(cfg)
            self._ff7_boss_tracker.set_match_sets(sub, excl)
        except Exception as e:
            logger.warning("FF7 boss match set reload failed: %s", e, exc_info=True)

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
                ok, _err = result
                if (
                    ok
                    and op == "set_game_speed"
                    and int(kwargs.get("duration_sec") or 0) > 0
                ):
                    delay = float(kwargs["duration_sec"])
                    svc = self._weak_self()

                    def _revert() -> None:
                        s = svc
                        if s is None or s._ff7_hook is None:
                            return
                        s._write_queue.put(
                            (
                                concurrent.futures.Future(),
                                "ff7",
                                "restore_game_speed",
                                {},
                            )
                        )

                    threading.Timer(delay, _revert).start()
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
                    if self._ff7_boss_tracker.update_from_snapshot(snap):
                        self._persist_ff7_boss_log()
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
                        "bosses": self._ff7_boss_tracker.bosses_dict(),
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
                    "bosses": self._ff7_boss_tracker.bosses_dict(),
                    "debug": {"stage": "ff7_disabled_in_settings"},
                }

            ff7_ui = hooks.get("ff7")
            if isinstance(ff7_ui, dict):
                with self._ui_state_lock:
                    self._last_ff7_ui = dict(ff7_ui)
            else:
                with self._ui_state_lock:
                    self._last_ff7_ui = None

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

    def get_ff7_ui_snapshot(self) -> Dict[str, Any]:
        """Thread-safe last FF7 hook payload for settings UI (shallow copy)."""
        running = self._thread is not None and self._thread.is_alive()
        with self._ui_state_lock:
            snap = dict(self._last_ff7_ui) if self._last_ff7_ui is not None else None
        return {"service_running": running, "ff7": snap}

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

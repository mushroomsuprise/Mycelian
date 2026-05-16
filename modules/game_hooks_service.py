"""
Broadcast live game data to all Socket.IO clients on a fixed cadence.

Event: ``game_hook_payload`` — envelope::

    { "v": 1, "ts": <unix_ms>, "hooks": { "ff7": { ... } } }

Commands from templates (see web_engine ``game_hook_command``)::

    { "hook": "ff7", "action": "clear_bosses" }

Boss defeat log for FF7 is persisted under ``GameHooks/ff7_boss_log`` (SQLite via
``database_manager``). Reset only via template ``clear_bosses`` command.

Connector and template writes are serialized on the hook polling thread via a queue.
Timed connector restores (game speed, menu words, battle speed, …) share one scheduler
that pauses remaining delay while the FF7 process is not attached.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .database_manager import database_manager
from .game_hooks import Ff7BossTracker, create_hook_instance
from .game_hooks.ff7_boss_tracker import ff7_boss_match_sets_from_config
from .game_hooks.ff7_hook import FF7_CONNECTOR_PERSIST_ALLOWLIST, FF7Hook
from .template_config_parser import TemplateConfigParser

logger = logging.getLogger(__name__)

_FF7_DB_PATH = "GameHooks/ff7_enabled"
_FF7_BOSS_LOG_PATH = "GameHooks/ff7_boss_log"
_FF7_CONNECTOR_OVERRIDES_PATH = "GameHooks/ff7_connector_overrides"
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


def _persist_override_key(op: str, kwargs: Dict[str, Any]) -> str:
    mn = str(kwargs.get("menu_name", "")).strip().lower()
    if op in ("set_menu_row_access", "set_menu_visibility", "set_menu_lock") and mn:
        return f"{op}:{mn}"
    return op


def _query_inventory_outputs(
    iq: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """Map FF7 last inventory query snapshot to string keys for connector {actionN.*} placeholders."""
    if not isinstance(iq, dict):
        return None
    return {
        "item_name": str(iq.get("item_name", "") or ""),
        "quantity": "" if iq.get("quantity") is None else str(iq["quantity"]),
        "resolved_name": ""
        if iq.get("resolved_name") is None
        else str(iq["resolved_name"]),
        "kind": "" if iq.get("kind") is None else str(iq["kind"]),
        "error": "" if iq.get("error") is None else str(iq["error"]),
    }


def _json_safe_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in kwargs.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


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
        # (deadline_monotonic, restore_operation, kwargs)
        self._timed_jobs: List[Tuple[float, str, Dict[str, Any]]] = []
        self._paused_timed_jobs: List[Tuple[float, str, Dict[str, Any]]] = []
        self._last_ff7_attached: Optional[bool] = None
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

    def _schedule_timed_job(
        self, delay_sec: int, restore_op: str, restore_kwargs: Dict[str, Any]
    ) -> None:
        if delay_sec <= 0:
            return
        self._timed_jobs.append(
            (
                time.monotonic() + float(delay_sec),
                restore_op,
                dict(restore_kwargs),
            )
        )
        logger.debug(
            "timed hook restore: %s in %s s kwargs=%s",
            restore_op,
            delay_sec,
            restore_kwargs,
        )

    def _maybe_fire_timed_jobs(self) -> None:
        if not self._timed_jobs:
            return
        now = time.monotonic()
        remain: List[Tuple[float, str, Dict[str, Any]]] = []
        for deadline, op, kw in self._timed_jobs:
            if now >= deadline:
                self._write_queue.put(
                    (concurrent.futures.Future(), "ff7", op, dict(kw))
                )
                logger.debug("timed hook restore fired: %s", op)
            else:
                remain.append((deadline, op, kw))
        self._timed_jobs = remain

    def _pause_timed_jobs_for_detach(self) -> None:
        now = time.monotonic()
        for deadline, op, kw in self._timed_jobs:
            rem = max(0.0, float(deadline) - now)
            self._paused_timed_jobs.append((rem, op, dict(kw)))
        if self._timed_jobs:
            logger.debug(
                "FF7 detached: paused %s timed restore(s)",
                len(self._timed_jobs),
            )
        self._timed_jobs.clear()

    def _resume_timed_jobs_after_attach(self) -> None:
        if not self._paused_timed_jobs:
            return
        base = time.monotonic()
        n = 0
        for rem, op, kw in self._paused_timed_jobs:
            if rem > 0:
                self._timed_jobs.append((base + rem, op, dict(kw)))
                n += 1
        self._paused_timed_jobs.clear()
        if n:
            logger.debug("FF7 reattached: resumed %s timed restore(s)", n)

    def _persist_connector_override(self, op: str, kwargs: Dict[str, Any]) -> None:
        if op not in FF7_CONNECTOR_PERSIST_ALLOWLIST:
            return
        try:
            raw = database_manager.get_data(_FF7_CONNECTOR_OVERRIDES_PATH)
            data: Dict[str, Any] = (
                dict(raw) if isinstance(raw, dict) else {"version": 1, "entries": []}
            )
            entries = data.get("entries")
            if not isinstance(entries, list):
                entries = []
            key = _persist_override_key(op, kwargs)
            now_ms = int(time.time() * 1000)
            dur_raw = kwargs.get("duration_sec", 0)
            try:
                dur = max(0, int(float(dur_raw)))
            except (TypeError, ValueError):
                dur = 0
            expires_at = (now_ms + dur * 1000) if dur > 0 else None
            row = {
                "key": key,
                "op": op,
                "kwargs": _json_safe_kwargs(dict(kwargs)),
                "expires_at_ms": expires_at,
            }
            replaced = False
            for i, ent in enumerate(entries):
                if isinstance(ent, dict) and ent.get("key") == key:
                    entries[i] = row
                    replaced = True
                    break
            if not replaced:
                entries.append(row)
            data["entries"] = entries
            database_manager.set_data(_FF7_CONNECTOR_OVERRIDES_PATH, data)
        except Exception as e:
            logger.warning("FF7 connector persist failed: %s", e, exc_info=True)

    def _replay_persisted_overrides(self) -> None:
        try:
            raw = database_manager.get_data(_FF7_CONNECTOR_OVERRIDES_PATH)
            if not isinstance(raw, dict):
                return
            entries = raw.get("entries")
            if not isinstance(entries, list):
                return
            now_ms = int(time.time() * 1000)
            kept: List[Dict[str, Any]] = []
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                op = str(ent.get("op") or "")
                kw = dict(ent.get("kwargs") or {})
                exp = ent.get("expires_at_ms")
                if exp is not None:
                    try:
                        exp_i = int(exp)
                    except (TypeError, ValueError):
                        continue
                    if exp_i <= now_ms:
                        continue
                    remaining_sec = max(1, (exp_i - now_ms) // 1000)
                    kw["duration_sec"] = int(remaining_sec)
                self._write_queue.put(
                    (concurrent.futures.Future(), "ff7", op, dict(kw))
                )
                kept.append(ent)
                logger.info("FF7 persist replay: op=%s kwargs=%s", op, kw)
            database_manager.set_data(
                _FF7_CONNECTOR_OVERRIDES_PATH, {"version": 1, "entries": kept}
            )
        except Exception as e:
            logger.warning("FF7 connector replay failed: %s", e, exc_info=True)

    def _drain_write_queue(self) -> None:
        while True:
            try:
                fut, game_id, op, kwargs = self._write_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if game_id != "ff7" or self._ff7_hook is None:
                    fut.set_result((False, f"No active hook for '{game_id}'", None))
                    continue
                result = self._ff7_hook.execute_operation(op, kwargs)
                out: Optional[Dict[str, str]] = None
                if op == "query_inventory":
                    iq = self._ff7_hook._last_inventory_query
                    if iq is not None:
                        with self._ui_state_lock:
                            if isinstance(self._last_ff7_ui, dict):
                                merged = dict(self._last_ff7_ui)
                            else:
                                merged = {}
                            merged["inventory_query"] = dict(iq)
                            self._last_ff7_ui = merged
                    out = _query_inventory_outputs(iq)
                ok, err = result[0], result[1] if len(result) > 1 else None
                fut.set_result((ok, err, out))
                if ok:
                    for t_op, t_kw, t_dur in self._ff7_hook.consume_timed_schedules():
                        self._schedule_timed_job(int(t_dur), t_op, t_kw)
                    self._persist_connector_override(op, kwargs)
                else:
                    self._ff7_hook.consume_timed_schedules()
            except Exception as e:
                logger.warning("game hook write failed: %s", e, exc_info=True)
                fut.set_result((False, str(e), None))

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
            self._maybe_fire_timed_jobs()
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
                    try:
                        pending = self._ff7_hook.consume_pending_battle()
                        if pending is not None:
                            cm = int(snap.get("current_module") or 0)
                            if cm in (1, 3):
                                ok, err = self._ff7_hook._start_battle_now(int(pending))
                                if not ok:
                                    self._ff7_hook._pending_battle_id = int(pending)
                                    logger.debug(
                                        "queued start_battle deferred: %s", err
                                    )
                            else:
                                self._ff7_hook._pending_battle_id = int(pending)
                    except Exception as e:
                        logger.debug("queued start_battle watcher: %s", e)
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
                        "battle_log": [],
                        "bosses": self._ff7_boss_tracker.bosses_dict(),
                        "debug": {"stage": "snapshot_exception", "message": str(e)},
                    }
            else:
                self._timed_jobs.clear()
                self._paused_timed_jobs.clear()
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
            attached_now: Optional[bool] = None
            if isinstance(ff7_ui, dict):
                attached_now = bool(ff7_ui.get("attached"))
                if attached_now and self._last_ff7_attached is not True:
                    self._resume_timed_jobs_after_attach()
                    self._replay_persisted_overrides()
                elif not attached_now and self._last_ff7_attached is True:
                    self._pause_timed_jobs_for_detach()
                self._last_ff7_attached = attached_now

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

        self._timed_jobs.clear()
        self._paused_timed_jobs.clear()
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

    def is_game_hook_ready(self, game_id: str) -> bool:
        """True when settings enable this hook and the service reports attached to the game."""
        gid = str(game_id or "").strip().lower()
        if gid == "ff7":
            if not _ff7_enabled():
                return False
            info = self.get_ff7_ui_snapshot()
            if not info.get("service_running"):
                return False
            snap = info.get("ff7")
            if not isinstance(snap, dict):
                return False
            if bool(snap.get("disabled")):
                return False
            return bool(snap.get("attached"))
        return False

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

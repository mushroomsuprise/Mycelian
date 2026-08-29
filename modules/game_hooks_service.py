# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Broadcast live game data to all Socket.IO clients on a fixed cadence.

Event: ``game_hook_payload`` — envelope::

    { "v": 1, "ts": <unix_ms>, "hooks": { "ff7": { ... } } }

Commands from templates (see web_engine ``game_hook_command``)::

    { "hook": "ff7", "action": "clear_bosses" }
    { "hook": "ff7", "action": "resync" }

Per-game logic lives under ``modules/game_hooks/``; this module coordinates workers,
process discovery, and Socket.IO broadcast only.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional, Tuple

from .game_hooks.base import GameHook
from .game_hooks.registry import (
    create_hook,
    is_hook_enabled,
    refresh_hook_enabled_cache,
    registered_hook_ids,
)

logger = logging.getLogger(__name__)

_INTERVAL = 0.25
_WRITE_QUEUE_MAXSIZE = 256


class _HookWorker:
    """Dedicated thread for one active game hook while its process is running."""

    def __init__(self, hook: GameHook) -> None:
        self.hook = hook
        self.hook_id = hook.hook_id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._snapshot_lock = threading.Lock()
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._write_queue: "queue.Queue[Tuple[concurrent.futures.Future[Any], str, Dict[str, Any]]]" = (
            queue.Queue(maxsize=_WRITE_QUEUE_MAXSIZE)
        )

        def _enqueue(op: str, kwargs: Dict[str, Any]) -> None:
            self._enqueue_or_drop(
                concurrent.futures.Future(), op, dict(kwargs)
            )

        hook.set_write_enqueue(_enqueue)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"GameHook-{self.hook_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "Game hook worker %s still alive; not closing handle",
                    self.hook_id,
                )
                return False
            self._thread = None
        try:
            self.hook.close()
        except Exception:
            pass
        return True

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._snapshot_lock:
            return dict(self._last_snapshot) if self._last_snapshot else None

    def _enqueue_or_drop(
        self,
        fut: concurrent.futures.Future,
        op: str,
        kwargs: Dict[str, Any],
    ) -> None:
        try:
            self._write_queue.put_nowait((fut, op, kwargs))
        except queue.Full:
            logger.warning(
                "Game hook %s write queue full; dropping %s", self.hook_id, op
            )
            if not fut.done():
                fut.set_result((False, "write queue full", None))

    def enqueue(
        self, operation: str, arguments: Dict[str, Any]
    ) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._enqueue_or_drop(fut, operation, dict(arguments))
        return fut

    def _drain_writes(self) -> None:
        while True:
            try:
                fut, op, kwargs = self._write_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if op == "__command__":
                    self.hook.handle_command(
                        str(kwargs.get("action") or ""),
                        kwargs.get("data") or {},
                    )
                    fut.set_result((True, None, None))
                    continue
                ok, err, out = self.hook.execute_operation(op, kwargs)
                fut.set_result((ok, err, out))
            except Exception as e:
                logger.warning(
                    "game hook write failed (%s): %s",
                    self.hook_id,
                    e,
                    exc_info=True,
                )
                fut.set_result((False, str(e), None))

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            self._drain_writes()
            self.hook.drain_timed_jobs(
                lambda op, kw: self._enqueue_or_drop(
                    concurrent.futures.Future(), op, dict(kw)
                )
            )
            try:
                snap = self.hook.tick()
            except Exception as e:
                logger.warning(
                    "%s tick error: %s", self.hook_id, e, exc_info=True
                )
                snap = self.hook.idle_snapshot(disabled=False)
                snap["error"] = str(e)
            with self._snapshot_lock:
                self._last_snapshot = snap
            elapsed = time.time() - t0
            wait = max(0.0, _INTERVAL - elapsed)
            if self._stop.wait(wait):
                break
        logger.debug("Game hook worker exit: %s", self.hook_id)


class GameHooksServiceImpl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ui_state_lock = threading.Lock()
        self._ui_snapshots: Dict[str, Optional[Dict[str, Any]]] = {}
        self._hooks: Dict[str, GameHook] = {}
        self._workers: Dict[str, _HookWorker] = {}
        self._emit_count = 0
        self._orphan_writes: "queue.Queue[Tuple[concurrent.futures.Future[Any], str, str, Dict[str, Any]]]" = (
            queue.Queue()
        )

    def _get_hook(self, hook_id: str) -> Optional[GameHook]:
        hid = str(hook_id or "").strip().lower()
        if hid not in self._hooks:
            inst = create_hook(hid)
            if inst is not None:
                self._hooks[hid] = inst
        return self._hooks.get(hid)

    def enqueue_game_hook_write(
        self, game_id: str, operation: str, arguments: Optional[Dict[str, Any]] = None
    ) -> concurrent.futures.Future:
        """Schedule a write on the hook worker thread. Returns a concurrent.futures.Future."""
        gid = str(game_id or "").strip().lower()
        worker = self._workers.get(gid)
        if worker and worker.is_alive():
            return worker.enqueue(operation, dict(arguments or {}))
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result((False, f"No active hook for '{gid}'", None))
        return fut

    def reload_hook_config(self, hook_id: str) -> None:
        hook = self._get_hook(hook_id)
        if hook is not None:
            hook.on_config_reloaded()

    def reload_ff7_boss_match_sets(self) -> None:
        self.reload_hook_config("ff7")

    def clear_ff7_bosses(self) -> None:
        worker = self._workers.get("ff7")
        if worker is not None and worker.is_alive():
            worker.enqueue("__command__", {"action": "clear_bosses", "data": {}})
            return
        hook = self._get_hook("ff7")
        if hook is not None:
            hook.handle_command("clear_bosses", {})

    def _stop_worker(self, hook_id: str) -> None:
        worker = self._workers.get(hook_id)
        if worker is None:
            return
        if worker.stop():
            self._workers.pop(hook_id, None)

    def _ensure_worker(self, hook: GameHook) -> _HookWorker:
        worker = self._workers.get(hook.hook_id)
        if worker is not None and worker.is_alive():
            return worker
        if worker is not None:
            worker.stop()
        worker = _HookWorker(hook)
        self._workers[hook.hook_id] = worker
        worker.start()
        return worker

    def _emit(self, payload: Dict[str, Any]) -> None:
        try:
            from . import web_engine

            inst = getattr(web_engine, "web_engine_instance", None)
            if inst is not None:
                # safe_emit: coordinator thread, not the gevent hub thread.
                inst.safe_emit("game_hook_payload", payload)
        except Exception as e:
            logger.warning("game_hook_payload emit failed: %s", e, exc_info=True)

    def emit_last_payload(self) -> None:
        """Re-broadcast the last per-hook snapshots (overlay reconnect / resync)."""
        hooks: Dict[str, Any] = {}
        with self._ui_state_lock:
            for hid, snap in self._ui_snapshots.items():
                if isinstance(snap, dict):
                    hooks[str(hid)] = dict(snap)
        if not hooks:
            return
        self._emit({
            "v": 1,
            "ts": int(time.time() * 1000),
            "hooks": hooks,
        })

    def _coordinator_loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            hooks: Dict[str, Any] = {}

            for hook_id in registered_hook_ids():
                hook = self._get_hook(hook_id)
                if hook is None:
                    continue

                if not is_hook_enabled(hook_id):
                    self._stop_worker(hook_id)
                    snap = hook.idle_snapshot(disabled=True)
                elif not hook.is_process_running():
                    self._stop_worker(hook_id)
                    snap = hook.idle_snapshot(disabled=False)
                else:
                    worker = self._ensure_worker(hook)
                    snap = worker.get_snapshot()
                    if snap is None:
                        snap = hook.idle_snapshot(disabled=False)

                hooks[hook_id] = snap
                with self._ui_state_lock:
                    self._ui_snapshots[hook_id] = dict(snap)

            payload = {
                "v": 1,
                "ts": int(time.time() * 1000),
                "hooks": hooks,
            }
            self._emit_count += 1
            if self._emit_count <= 3 or self._emit_count % 20 == 0:
                for hid, h in hooks.items():
                    if isinstance(h, dict):
                        logger.info(
                            "[GameHooks] emit #%s ts=%s %s attached=%s disabled=%s "
                            "err=%s battle=%s",
                            self._emit_count,
                            payload["ts"],
                            hid,
                            h.get("attached"),
                            h.get("disabled"),
                            h.get("error"),
                            h.get("battle"),
                        )
                        break
            self._emit(payload)

            elapsed = time.time() - t0
            wait = max(0.0, _INTERVAL - elapsed)
            if self._stop.wait(wait):
                break

        for hook_id in list(self._workers.keys()):
            self._stop_worker(hook_id)
        logger.debug("Game hooks coordinator exit")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            refresh_hook_enabled_cache()
        except Exception as e:
            logger.debug("Could not preload hook enabled cache: %s", e)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._coordinator_loop,
            name="GameHooksCoordinator",
            daemon=True,
        )
        self._thread.start()
        logger.info("Game hooks service started")

    def get_hook_ui_snapshot(self, hook_id: str) -> Dict[str, Any]:
        """Thread-safe last hook payload for settings UI (shallow copy)."""
        hid = str(hook_id or "").strip().lower()
        running = self._thread is not None and self._thread.is_alive()
        with self._ui_state_lock:
            snap = (
                dict(self._ui_snapshots[hid])
                if isinstance(self._ui_snapshots.get(hid), dict)
                else None
            )
        return {"service_running": running, hid: snap}

    def get_ff7_ui_snapshot(self) -> Dict[str, Any]:
        return self.get_hook_ui_snapshot("ff7")

    def is_game_hook_ready(self, game_id: str) -> bool:
        """True when settings enable this hook and the service reports attached."""
        gid = str(game_id or "").strip().lower()
        if not is_hook_enabled(gid):
            return False
        if self._thread is None or not self._thread.is_alive():
            return False
        info = self.get_hook_ui_snapshot(gid)
        snap = info.get(gid)
        if not isinstance(snap, dict):
            return False
        if bool(snap.get("disabled")):
            return False
        return bool(snap.get("attached"))

    def stop(self) -> None:
        self._stop.set()
        for hook_id in list(self._workers.keys()):
            self._stop_worker(hook_id)
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("Game hooks service stopped")


game_hooks_service = GameHooksServiceImpl()


def handle_game_hook_command(data: Any) -> None:
    """Handle client command dict."""
    if not isinstance(data, dict):
        return
    hook_id = str(data.get("hook") or "").strip().lower()
    action = str(data.get("action") or "").strip()
    if not hook_id or not action:
        return
    if action == "resync":
        game_hooks_service.emit_last_payload()
        return
    worker = game_hooks_service._workers.get(hook_id)
    if worker is not None and worker.is_alive():
        worker.enqueue("__command__", {"action": action, "data": data})
        return
    hook = game_hooks_service._get_hook(hook_id)
    if hook is not None:
        hook.handle_command(action, data)

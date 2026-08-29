# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""OBS Studio WebSocket (v5) integration — ReqClient requests and EventClient subscriptions.

All obsws-python socket I/O runs on this module's daemon thread. Connector actions enqueue
requests; OBS events dispatch to ConnectorManager via ``asyncio.run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
from collections.abc import Mapping
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# When OBS is not running yet, reconnect with exponential backoff between attempts.
_CONNECT_ATTEMPT_FIRST_WAIT_SEC = 5.0
_CONNECT_ATTEMPT_MAX_WAIT_SEC = 45.0
_CONNECT_ATTEMPT_BACKOFF_FACTOR = 1.35
# Worker thread poll while OBS integration disabled in Settings.
_DISABLED_POLL_INTERVAL_SEC = 15.0
# After unexpected thread errors, pause before restarting the outer loop.
_THREAD_ERROR_COOLDOWN_SEC = 10.0
# Consecutive idle health-check failures (~queue_empty interval) → drop stale socket.
_IDLE_HEALTH_DROP_AFTER_FAILS = 4
# Socket timeouts (seconds) — keep connect/health probes short so the UI stays responsive.
_CONNECT_TIMEOUT_SEC = 3.0
_RPC_TIMEOUT_SEC = 5.0
_HEALTH_TIMEOUT_SEC = 2.0
_EVENT_DISCONNECT_JOIN_TIMEOUT_SEC = 2.0
_SNAPSHOT_YIELD_EVERY_N_SCENES = 3

_CONNECTOR_OPS = frozenset(
    {
        "set_program_scene",
        "set_preview_scene",
        "set_source_enabled",
        "toggle_source",
        "set_source_transform",
        "set_input_mute",
        "toggle_input_mute",
        "start_stream",
        "stop_stream",
        "toggle_stream",
        "start_record",
        "stop_record",
        "toggle_record",
    }
)

_TRANSFORM_SNAKE_TO_CAMEL = {
    "position_x": "positionX",
    "position_y": "positionY",
    "rotation": "rotation",
    "scale_x": "scaleX",
    "scale_y": "scaleY",
    "alignment": "alignment",
    "bounds_type": "boundsType",
    "bounds_alignment": "boundsAlignment",
    "bounds_width": "boundsWidth",
    "bounds_height": "boundsHeight",
    "crop_left": "cropLeft",
    "crop_top": "cropTop",
    "crop_right": "cropRight",
    "crop_bottom": "cropBottom",
    "width": "width",
    "height": "height",
}


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    return s in ("1", "true", "yes", "on")


def _coerce_numeric(val: Any) -> Any:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    try:
        s = str(val).strip()
        if "." in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return val


def _attr(data: Any, *names: str) -> Any:
    """Read an attribute/snake-case or camel-case key from a dataclass/object or nested dict."""

    if isinstance(data, Mapping) and not isinstance(data, (str, bytes, bytearray)):
        for n in names:
            if n in data:
                return data[n]
        return None
    for n in names:
        if hasattr(data, n):
            return getattr(data, n)
    return None


def _gather_transform_overrides(obs_args: Dict[str, Any]) -> Dict[str, Any]:
    """Map catalog snake_case args to OBS sceneItemTransform keys (camelCase)."""
    out: Dict[str, Any] = {}
    for snake, camel in _TRANSFORM_SNAKE_TO_CAMEL.items():
        if snake not in obs_args:
            continue
        raw = obs_args.get(snake)
        if raw is None or raw == "":
            continue
        coerced = _coerce_numeric(raw)
        out[camel] = coerced
    return out


def _is_transient_connect_error(exc: BaseException) -> bool:
    """Typical failures when OBS is off or restarting (avoid noisy logs)."""

    if isinstance(exc, (ConnectionRefusedError, BrokenPipeError, ConnectionResetError, TimeoutError)):
        return True
    if type(exc).__name__ == "OBSSDKError":
        return True
    msg = str(exc).lower()
    if "no password provided" in msg or "authentication enabled" in msg:
        return True
    return type(exc).__name__ in ("WebSocketTimeoutException", "WebSocketBadStatusException")


class ObsServiceImpl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._req_queue: queue.Queue[
            Tuple[Optional[concurrent.futures.Future[Any]], str, Dict[str, Any]]
        ] = queue.Queue(maxsize=500)
        self._cache_lock = threading.Lock()
        self._snapshot: Dict[str, Any] = {
            "scene_names": [],
            "input_names": [],
            "sources_by_scene": {},
            "stream_output_active": False,
            "stream_output_state": "",
            "record_output_active": False,
            "record_output_state": "",
        }
        self._req_client: Any = None
        self._ev_client: Any = None
        self._connector_loop: Optional[asyncio.AbstractEventLoop] = None
        self._obs_version: str = ""
        self._websocket_version: str = ""
        self._reconnect_wakeup = threading.Event()
        self._last_connect_attempt: float = 0.0
        self._retry_after_fail_sec = _CONNECT_ATTEMPT_FIRST_WAIT_SEC
        self._idle_health_failures = 0
        self._phase_lock = threading.Lock()
        self._phase = "disconnected"
        self._connect_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

    # --- Public API ---
    def set_connector_loop(self, loop: Optional[asyncio.AbstractEventLoop]) -> None:
        """Called from ConnectorProcessor when its asyncio loop is ready."""
        self._connector_loop = loop

    def start(self) -> None:
        th = self._thread
        if th is not None and th.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, name="ObsWebSocket", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._req_queue.put_nowait((None, "__shutdown__", {}))
        except queue.Full:
            pass
        self._reconnect_wakeup.set()
        th = self._thread
        if th is not None:
            th.join(timeout=8.0)
            if not th.is_alive():
                self._thread = None
        pool = self._connect_executor
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._connect_executor = None
        self._set_phase("disconnected")

    def enqueue_obs_request(
        self, operation: str, obs_arguments: Optional[Dict[str, Any]] = None
    ) -> concurrent.futures.Future[Any]:
        fut: concurrent.futures.Future[Any] = concurrent.futures.Future()
        try:
            self._req_queue.put_nowait((fut, operation, dict(obs_arguments or {})))
        except queue.Full:
            fut.set_result((False, "OBS request queue is full", None))
        return fut

    def apply_settings(self) -> None:
        """Ask the worker thread to reconnect using current ``state_manager`` settings."""
        self._reconnect_wakeup.set()
        try:
            self._req_queue.put_nowait((None, "__reconnect__", {}))
        except queue.Full:
            logger.warning("OBS reconnect signal dropped — queue full")

    def enqueue_refresh_snapshot(self) -> concurrent.futures.Future[Any]:
        """Queue a snapshot rebuild on the OBS worker thread (preferred for UI)."""
        return self.enqueue_obs_request("__refresh_snapshot__", {})

    def refresh_snapshot_blocking(self, timeout_s: float = 12.0) -> Tuple[bool, str]:
        """Wait for a snapshot rebuild. Do not call from the NiceGUI main thread."""
        fut = self.enqueue_refresh_snapshot()
        try:
            raw = fut.result(timeout=timeout_s)
            if isinstance(raw, tuple) and raw[0]:
                return True, ""
            msg = ""
            if isinstance(raw, tuple) and len(raw) > 1 and raw[1]:
                msg = str(raw[1])
            return False, msg or "snapshot failed"
        except Exception as e:
            return False, str(e)

    def get_connector_snapshot(self) -> Dict[str, Any]:
        with self._cache_lock:
            return {
                "scene_names": list(self._snapshot["scene_names"]),
                "input_names": list(self._snapshot["input_names"]),
                "sources_by_scene": {
                    k: dict(v)
                    for k, v in (self._snapshot.get("sources_by_scene") or {}).items()
                    if isinstance(v, dict)
                },
                "stream_output_active": bool(self._snapshot.get("stream_output_active")),
                "stream_output_state": str(self._snapshot.get("stream_output_state") or ""),
                "record_output_active": bool(self._snapshot.get("record_output_active")),
                "record_output_state": str(self._snapshot.get("record_output_state") or ""),
            }

    def get_connection_phase(self) -> str:
        """``disconnected`` | ``connecting`` | ``connected`` | ``disconnecting``."""
        with self._phase_lock:
            return self._phase

    def is_connected(self) -> bool:
        return self.get_connection_phase() == "connected" and self._req_client is not None

    def get_status_line(self) -> str:
        phase = self.get_connection_phase()
        if phase == "connected":
            return f"{self._obs_version or 'Connected'}"
        if phase == "connecting":
            return "Connecting"
        if phase == "disconnecting":
            return "Disconnecting"
        return "Disconnected"

    def connection_details(self) -> Tuple[bool, str, str]:
        """connected, OBS version/plugin string, WebSocket RPC version."""
        return (
            self.is_connected(),
            self._obs_version or "",
            self._websocket_version or "",
        )

    def _set_phase(self, phase: str) -> None:
        with self._phase_lock:
            self._phase = phase

    def _should_abort_connect(self) -> bool:
        return self._stop.is_set() or self._reconnect_wakeup.is_set()

    def _replace_connect_executor(self) -> None:
        pool = self._connect_executor
        self._connect_executor = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _get_connect_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        pool = self._connect_executor
        if pool is None:
            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="ObsConnect"
            )
            self._connect_executor = pool
        return pool

    # --- Worker loop ---
    def _thread_main(self) -> None:
        while not self._stop.is_set():
            try:
                self._disconnect_clients()
                if not self._load_enabled_settings():
                    self._retry_after_fail_sec = _CONNECT_ATTEMPT_FIRST_WAIT_SEC
                    idle = self._disconnect_idle_wait_or_drain_queue(
                        _DISABLED_POLL_INTERVAL_SEC
                    )
                    if idle == "shutdown":
                        break
                    continue

                self._connect_blocking()
                if self._req_client is None:
                    # Keep draining the queue (settings refresh, queued ops fail fast).
                    idle = self._disconnect_idle_wait_or_drain_queue(
                        self._retry_after_fail_sec
                    )
                    if idle == "shutdown":
                        break
                    if idle == "done":
                        self._retry_after_fail_sec = min(
                            _CONNECT_ATTEMPT_MAX_WAIT_SEC,
                            round(
                                self._retry_after_fail_sec
                                * _CONNECT_ATTEMPT_BACKOFF_FACTOR,
                                3,
                            ),
                        )
                    continue

                self._retry_after_fail_sec = _CONNECT_ATTEMPT_FIRST_WAIT_SEC
                self._idle_health_failures = 0
                self._run_loop_iteration()
            except Exception as e:
                logger.error("OBS service thread error: %s", e, exc_info=True)
                self._disconnect_clients()
                self._retry_after_fail_sec = _CONNECT_ATTEMPT_FIRST_WAIT_SEC
                idle = self._disconnect_idle_wait_or_drain_queue(_THREAD_ERROR_COOLDOWN_SEC)
                if idle == "shutdown":
                    break

    def _disconnect_idle_wait_or_drain_queue(self, wait_seconds: float) -> str:
        """Wait up to ``wait_seconds``, draining queue-safe ops.

        Returns ``shutdown``, ``wake`` (__reconnect__ short-circuit), or ``done`` after the wait."""

        deadline = time.monotonic() + max(0.0, wait_seconds)
        while time.monotonic() < deadline and not self._stop.is_set():
            if self._reconnect_wakeup.is_set():
                self._reconnect_wakeup.clear()
                self._retry_after_fail_sec = _CONNECT_ATTEMPT_FIRST_WAIT_SEC
                return "wake"

            remaining = deadline - time.monotonic()
            slice_s = max(0.01, min(2.0, remaining))
            try:
                item = self._req_queue.get(timeout=slice_s)
            except queue.Empty:
                continue
            fut, op, kw = item
            if op == "__shutdown__":
                return "shutdown"
            if op == "__reconnect__":
                self._reconnect_wakeup.clear()
                self._retry_after_fail_sec = _CONNECT_ATTEMPT_FIRST_WAIT_SEC
                return "wake"
            if op == "__refresh_snapshot__":
                if fut is not None:
                    fut.set_result((False, "Not connected"))
                continue
            if op == "__browser_source_size__":
                if fut is not None:
                    fut.set_result((False, "OBS is not connected", None))
                continue
            if fut is not None:
                fut.set_result((False, "OBS is not connected", None))

        return "shutdown" if self._stop.is_set() else "done"

    def _run_loop_iteration(self) -> None:
        """Drain request queue until shutdown or disconnect."""
        while not self._stop.is_set():
            try:
                item = self._req_queue.get(timeout=0.3)
            except queue.Empty:
                if self._reconnect_wakeup.is_set():
                    self._reconnect_wakeup.clear()
                    return
                # Keep snapshot fresh lightly (stream/record state)
                if self._req_client:
                    try:
                        self._update_output_flags_only(use_health_timeout=True)
                        self._idle_health_failures = 0
                    except Exception:
                        self._idle_health_failures += 1
                        if (
                            self._idle_health_failures >= _IDLE_HEALTH_DROP_AFTER_FAILS
                        ):
                            logger.info(
                                "OBS websocket appeared dead — reconnecting after idle probe failures",
                            )
                            self._disconnect_clients()
                            return
                ev = self._ev_client
                if ev is not None:
                    worker = getattr(ev, "worker", None)
                    if worker is not None and not worker.is_alive():
                        logger.info(
                            "OBS EventClient worker died — reconnecting"
                        )
                        self._disconnect_clients()
                        return
                continue
            fut, op, kw = item
            if op == "__shutdown__":
                break
            if op == "__reconnect__":
                return
            try:
                if op == "__refresh_snapshot__":
                    if self._req_client is None:
                        if fut:
                            fut.set_result((False, "Not connected"))
                    else:
                        self._refresh_snapshot_locked()
                        if fut:
                            fut.set_result((True, None))
                    continue
                if op == "__browser_source_size__":
                    if self._req_client is None:
                        if fut:
                            fut.set_result((False, "Not connected", None))
                    else:
                        try:
                            payload = self._lookup_browser_source_size_locked(
                                str(kw.get("route") or ""),
                                kw.get("port"),
                            )
                            if fut:
                                fut.set_result((True, None, payload))
                        except Exception as e:
                            if fut:
                                fut.set_result((False, str(e), None))
                    continue
                if op not in _CONNECTOR_OPS:
                    raise ValueError(f"Unknown OBS connector operation '{op}'")
                self._dispatch_connector_operation(op, kw or {})
                if fut:
                    fut.set_result((True, None, None))
            except Exception as e:
                msg = str(e)
                logger.warning("OBS connector op failed %s %s — %s", op, kw, msg)
                if fut:
                    fut.set_result((False, msg, None))

        self._disconnect_clients()

    def _load_enabled_settings(self) -> bool:
        """Return True when integration should remain active."""
        try:
            from . import dataobjects

            obs = dataobjects.state_manager.get_obs_data()
            if not obs.enabled:
                return False
            return True
        except Exception as e:
            logger.debug("OBS settings load: %s", e)
            return False

    def _connection_kwargs(self, mode: str = "rpc") -> Dict[str, Any]:
        from . import dataobjects

        if mode == "connect":
            timeout = _CONNECT_TIMEOUT_SEC
        elif mode == "health":
            timeout = _HEALTH_TIMEOUT_SEC
        else:
            timeout = _RPC_TIMEOUT_SEC

        obs = dataobjects.state_manager.get_obs_data()
        port = int(obs.port or 4455)
        pwd = getattr(obs, "password", "") or ""
        kwargs: Dict[str, Any] = {
            "host": (obs.host or "localhost").strip() or "localhost",
            "port": port,
            "password": pwd,
            "timeout": timeout,
        }
        return kwargs

    @staticmethod
    def _establish_req_session(
        kw: Dict[str, Any],
    ) -> Tuple[Any, str, str]:
        """Run on ObsConnect pool thread — blocking ReqClient connect + identify."""
        from obsws_python import ReqClient

        rc = ReqClient(**kw)
        resp = rc.get_version()
        plug = getattr(resp, "obs_version", getattr(resp, "obsVersion", "") or "") or ""
        ws_v = getattr(
            resp,
            "obs_web_socket_version",
            getattr(resp, "obsWebSocketVersion", "") or "",
        ) or ""
        return rc, str(plug), str(ws_v)

    def _run_connect_with_timeout(
        self, kw: Dict[str, Any]
    ) -> Optional[Tuple[Any, str, str]]:
        pool = self._get_connect_executor()
        fut = pool.submit(self._establish_req_session, kw)
        try:
            return fut.result(timeout=_CONNECT_TIMEOUT_SEC + 0.5)
        except concurrent.futures.TimeoutError:
            logger.debug("OBS ReqClient connect timed out")
            self._replace_connect_executor()
            return None
        except Exception as e:
            if _is_transient_connect_error(e):
                logger.debug("OBS unreachable: %s", e)
            else:
                logger.info("OBS connect failed: %s", e)
            return None

    def _connect_event_client(self, base_kw: Dict[str, Any]) -> None:
        from obsws_python import EventClient
        from obsws_python.subs import Subs

        ev_kw = dict(base_kw)
        ev_kw["timeout"] = _CONNECT_TIMEOUT_SEC
        ev_kw["subs"] = Subs.LOW_VOLUME

        def _open_events() -> Any:
            return EventClient(**ev_kw)

        pool = self._get_connect_executor()
        fut = pool.submit(_open_events)
        try:
            ev = fut.result(timeout=_CONNECT_TIMEOUT_SEC + 0.5)
            if self._should_abort_connect():
                self._disconnect_event_client_bounded(ev)
                return
            self._ev_client = ev
            self._register_callbacks()
        except concurrent.futures.TimeoutError:
            logger.warning("OBS EventClient connect timed out (triggers disabled)")
            self._replace_connect_executor()
        except Exception as e:
            logger.warning("OBS EventClient unavailable (triggers disabled): %s", e)

    def _connect_blocking(self) -> None:
        if self._should_abort_connect():
            return

        self._set_phase("connecting")
        self._disconnect_clients()
        self._set_phase("connecting")

        if self._should_abort_connect():
            return

        kw = self._connection_kwargs("connect")
        self._last_connect_attempt = time.monotonic()
        session = self._run_connect_with_timeout(kw)
        if session is None:
            self._disconnect_clients()
            return

        req_client, plug, ws_v = session
        if self._should_abort_connect():
            try:
                req_client.disconnect()
            except Exception:
                pass
            self._disconnect_clients()
            return

        self._req_client = req_client
        self._apply_ws_timeout(self._req_client, _RPC_TIMEOUT_SEC)
        self._obs_version = plug
        self._websocket_version = ws_v
        self._set_phase("connected")

        logger.info(
            "OBS WebSocket connected (%s)",
            self._obs_version or self._websocket_version or "?",
        )

        try:
            self._update_output_flags_only(use_health_timeout=True)
        except Exception:
            pass

        try:
            self._req_queue.put_nowait((None, "__refresh_snapshot__", {}))
        except queue.Full:
            logger.debug("OBS post-connect snapshot enqueue dropped — queue full")

        if not self._should_abort_connect():
            self._connect_event_client(kw)

    def _disconnect_event_client_bounded(self, ev: Any) -> None:
        try:
            cb = getattr(ev, "callback", None)
            if cb is not None:
                try:
                    cb.clear()
                except Exception:
                    pass
        except Exception:
            pass
        base = getattr(ev, "base_client", None)
        ws = getattr(base, "ws", None) if base is not None else None
        if ws is not None:
            try:
                ws.close()
            except Exception as e:
                logger.debug("OBS EventClient ws close: %s", e)
        worker = getattr(ev, "worker", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=_EVENT_DISCONNECT_JOIN_TIMEOUT_SEC)

    def _disconnect_clients(self) -> None:
        self._set_phase("disconnecting")
        ev = self._ev_client
        rc = self._req_client
        self._ev_client = None
        self._req_client = None

        if ev is not None:
            try:
                self._disconnect_event_client_bounded(ev)
            except Exception as e:
                logger.debug("OBS EventClient disconnect: %s", e)
        if rc is not None:
            try:
                rc.disconnect()
            except Exception as e:
                logger.debug("OBS ReqClient disconnect: %s", e)
        self._set_phase("disconnected")

    def _apply_ws_timeout(self, cl: Any, timeout: float) -> None:
        base = getattr(cl, "base_client", None)
        if base is None:
            return
        base.timeout = timeout
        ws = getattr(base, "ws", None)
        if ws is not None:
            try:
                ws.settimeout(timeout)
            except Exception:
                pass

    def _enqueue_connector_event(self, event_data: Dict[str, Any]) -> None:
        loop = self._connector_loop
        if loop is None or not loop.is_running():
            return
        try:
            from .connector_integration import get_integration

            asyncio.run_coroutine_threadsafe(get_integration().manager.add_event(event_data), loop)
        except Exception as e:
            logger.debug("OBS enqueue connector failed: %s", e)

    def _register_callbacks(self) -> None:
        cb = getattr(self._ev_client, "callback", None)
        if cb is None:
            return

        obs = self

        def on_current_program_scene_changed(data):
            scene = _attr(data, "scene_name", "sceneName") or ""
            prev = _attr(data, "previous_scene_name", "previousSceneName") or ""
            ts = time.time()
            obs._enqueue_connector_event(
                {
                    "event_type": "obs_scene_changed",
                    "source": "obs",
                    "timestamp": ts,
                    "scene_name": scene,
                    "previous_scene_name": prev,
                    "username": "",
                }
            )

        def on_stream_state_changed(data):
            active = bool(_attr(data, "output_active", "outputActive") or False)
            state_str = (
                _attr(data, "output_state", "outputState")
                or _attr(data, "state")
                or ""
            )
            state_str = str(state_str or "")
            ts = time.time()
            with obs._cache_lock:
                obs._snapshot["stream_output_active"] = active
                obs._snapshot["stream_output_state"] = state_str
            obs._enqueue_connector_event(
                {
                    "event_type": "obs_stream_state",
                    "source": "obs",
                    "timestamp": ts,
                    "output_active": active,
                    "output_state": state_str,
                    "username": "",
                }
            )

        def on_record_state_changed(data):
            active = bool(_attr(data, "output_active", "outputActive") or False)
            state_str = (
                _attr(data, "output_state", "outputState")
                or _attr(data, "state")
                or ""
            )
            state_str = str(state_str or "")
            ts = time.time()
            with obs._cache_lock:
                obs._snapshot["record_output_active"] = active
                obs._snapshot["record_output_state"] = state_str
            obs._enqueue_connector_event(
                {
                    "event_type": "obs_record_state",
                    "source": "obs",
                    "timestamp": ts,
                    "output_active": active,
                    "output_state": state_str,
                    "username": "",
                }
            )

        def on_input_mute_state_changed(data):
            inp = _attr(data, "input_name", "inputName") or ""
            muted = bool(_attr(data, "input_muted", "inputMuted") or False)
            ts = time.time()
            obs._enqueue_connector_event(
                {
                    "event_type": "obs_input_mute",
                    "source": "obs",
                    "timestamp": ts,
                    "input_name": inp,
                    "input_muted": muted,
                    "username": "",
                }
            )

        cb.register(
            [
                on_current_program_scene_changed,
                on_stream_state_changed,
                on_record_state_changed,
                on_input_mute_state_changed,
            ]
        )

    def _update_output_flags_only(self, *, use_health_timeout: bool = False) -> None:
        cl = self._req_client
        if cl is None:
            return
        timeout = _HEALTH_TIMEOUT_SEC if use_health_timeout else _RPC_TIMEOUT_SEC
        self._apply_ws_timeout(cl, timeout)
        ss = cl.get_stream_status()
        rs = cl.get_record_status()
        sav = bool(_attr(ss, "output_active", "outputActive") or False)
        s_state = _attr(ss, "output_state", "outputState") or ""
        rav = bool(_attr(rs, "output_active", "outputActive") or False)
        r_state = _attr(rs, "output_state", "outputState") or ""
        with self._cache_lock:
            self._snapshot["stream_output_active"] = sav
            self._snapshot["stream_output_state"] = str(s_state or "")
            self._snapshot["record_output_active"] = rav
            self._snapshot["record_output_state"] = str(r_state or "")

    def _scene_names_from_resp(self, scene_list_resp: Any) -> List[str]:
        names: List[str] = []
        scenes = _attr(scene_list_resp, "scenes", None) or getattr(
            scene_list_resp, "__dict__", {}
        ).get("scenes", [])
        if not isinstance(scenes, list):
            return names
        for s in scenes:
            nm = _attr(s, "scene_name", "sceneName")
            if nm:
                names.append(str(nm))
        return names

    def _sources_for_scene(self, scene_name: str) -> Dict[str, str]:
        """Label -> underlying source_name (best-effort uniqueness)."""
        cl = self._req_client
        if cl is None:
            return {}
        try:
            items = cl.get_scene_item_list(scene_name)
            lst = _attr(items, "scene_items", "sceneItems") or []
            if not isinstance(lst, list):
                return {}
            out: Dict[str, str] = {}
            for it in lst:
                src = _attr(it, "source_name", "sourceName") or ""
                sid = _attr(it, "scene_item_id", "sceneItemId")
                if not src:
                    continue
                label = f"{src}"
                key = (
                    label
                    if label not in out
                    else f"{src} (#{sid})"
                )
                out[key] = str(src)
            return out
        except Exception:
            return {}

    def _refresh_snapshot_locked(self) -> None:
        cl = self._req_client
        if cl is None:
            return
        sl = cl.get_scene_list()
        inp = cl.get_input_list()
        scene_names = self._scene_names_from_resp(sl)
        input_names: List[str] = []
        inputs_list = _attr(inp, "inputs", None) or []
        if isinstance(inputs_list, list):
            for inp_row in inputs_list:
                nm = _attr(inp_row, "input_name", "inputName")
                if nm:
                    input_names.append(str(nm))
        by_scene: Dict[str, Dict[str, str]] = {}
        for idx, sn in enumerate(scene_names):
            if self._should_abort_connect():
                return
            mapped = self._sources_for_scene(sn)
            if mapped:
                by_scene[sn] = mapped
            if idx % _SNAPSHOT_YIELD_EVERY_N_SCENES == _SNAPSHOT_YIELD_EVERY_N_SCENES - 1:
                time.sleep(0)

        try:
            self._update_output_flags_only(use_health_timeout=True)
        except Exception:
            pass

        with self._cache_lock:
            self._snapshot["scene_names"] = sorted(set(scene_names), key=lambda x: x.lower())
            self._snapshot["input_names"] = sorted(set(input_names), key=lambda x: x.lower())
            self._snapshot["sources_by_scene"] = by_scene

    def lookup_browser_source_size(
        self,
        route: str,
        port: Optional[int] = None,
        *,
        timeout_s: float = 8.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Find an OBS browser source whose URL matches ``/{route}`` and return
        ``{width, height, source_name}``, or ``None``.

        Safe to call from a worker / ``run.io_bound`` thread — never from the
        NiceGUI main thread (blocks on the OBS websocket worker queue).
        """
        if not self.is_connected():
            return None
        if not route or not str(route).strip():
            return None
        fut = self.enqueue_obs_request(
            "__browser_source_size__",
            {"route": str(route).strip(), "port": port},
        )
        try:
            raw = fut.result(timeout=timeout_s)
        except Exception as e:
            logger.debug("OBS browser source size lookup failed: %s", e)
            return None
        if not isinstance(raw, tuple) or not raw or not raw[0]:
            return None
        payload = raw[2] if len(raw) > 2 else None
        return payload if isinstance(payload, dict) else None

    def _lookup_browser_source_size_locked(
        self, route: str, port: Any
    ) -> Optional[Dict[str, Any]]:
        """Worker-thread: scan browser_source inputs for a URL matching *route*."""
        from .obs_browser_source_match import (
            browser_url_matches_route,
            coerce_browser_wh,
            is_browser_input_kind,
            pick_browser_source_size,
        )

        cl = self._req_client
        if cl is None:
            return None
        route = (route or "").strip()
        if not route:
            return None
        overlay_port: Optional[int] = None
        if port is not None and port != "":
            try:
                overlay_port = int(port)
            except (TypeError, ValueError):
                overlay_port = None

        defaults: Dict[str, Any] = {}
        try:
            def_resp = cl.get_input_default_settings("browser_source")
            raw_defs = _attr(def_resp, "default_input_settings", "defaultInputSettings")
            if isinstance(raw_defs, dict):
                defaults = raw_defs
        except Exception as e:
            logger.debug("OBS browser_source defaults skipped: %s", e)

        inputs_list: List[Any] = []
        try:
            inp = cl.get_input_list(kind="browser_source")
            inputs_list = list(_attr(inp, "inputs", None) or [])
        except Exception as e:
            logger.debug("OBS get_input_list(browser_source) failed: %s", e)
        if not inputs_list:
            # Some OBS builds ignore the kind filter; scan all inputs.
            try:
                inp = cl.get_input_list()
                raw_list = _attr(inp, "inputs", None) or []
                if isinstance(raw_list, list):
                    for row in raw_list:
                        kind = _attr(
                            row,
                            "input_kind",
                            "inputKind",
                            "unversioned_input_kind",
                            "unversionedInputKind",
                        )
                        if is_browser_input_kind(kind):
                            inputs_list.append(row)
            except Exception as e:
                logger.debug("OBS get_input_list() fallback failed: %s", e)

        if not isinstance(inputs_list, list) or not inputs_list:
            logger.info(
                "OBS browser source size: no browser_source inputs for route=%s",
                route,
            )
            return None

        def _collect(require_port: bool) -> List[Dict[str, Any]]:
            found: List[Dict[str, Any]] = []
            for row in inputs_list:
                name = _attr(row, "input_name", "inputName")
                if not name:
                    continue
                try:
                    settings_resp = cl.get_input_settings(str(name))
                except Exception:
                    continue
                settings = _attr(settings_resp, "input_settings", "inputSettings")
                if not isinstance(settings, dict):
                    settings = {}
                url = settings.get("url")
                if not browser_url_matches_route(
                    url,
                    route,
                    overlay_port=overlay_port,
                    require_port=require_port,
                ):
                    continue
                wh = coerce_browser_wh(settings, defaults)
                if wh is None:
                    continue
                found.append(
                    {
                        "source_name": str(name),
                        "width": int(wh[0]),
                        "height": int(wh[1]),
                        "url": str(url or ""),
                    }
                )
            return found

        matches = _collect(require_port=True)
        if not matches and overlay_port is not None:
            # Soft fallback: path match only (wrong/missing port in OBS URL).
            matches = _collect(require_port=False)
            if matches:
                logger.info(
                    "OBS browser source size: path-only match for route=%s "
                    "(ignored port %s)",
                    route,
                    overlay_port,
                )

        if not matches:
            sample_urls: List[str] = []
            for row in inputs_list[:8]:
                name = _attr(row, "input_name", "inputName")
                if not name:
                    continue
                try:
                    settings_resp = cl.get_input_settings(str(name))
                    settings = _attr(
                        settings_resp, "input_settings", "inputSettings"
                    )
                    if isinstance(settings, dict) and settings.get("url"):
                        sample_urls.append(str(settings.get("url")))
                except Exception:
                    continue
            logger.info(
                "OBS browser source size: no URL match for route=%s port=%s "
                "(browser_inputs=%s sample_urls=%s)",
                route,
                overlay_port,
                len(inputs_list),
                sample_urls,
            )
            return None

        program_names: Optional[set] = None
        try:
            prog = cl.get_current_program_scene()
            scene = (
                _attr(prog, "current_program_scene_name", "currentProgramSceneName")
                or _attr(prog, "scene_name", "sceneName")
                or ""
            )
            if scene:
                items = cl.get_scene_item_list(str(scene))
                lst = _attr(items, "scene_items", "sceneItems") or []
                if isinstance(lst, list):
                    program_names = set()
                    for it in lst:
                        src = _attr(it, "source_name", "sourceName")
                        if src:
                            program_names.add(str(src))
        except Exception as e:
            logger.debug("OBS program-scene filter skipped: %s", e)

        picked = pick_browser_source_size(matches, program_source_names=program_names)
        if picked:
            logger.info(
                "OBS browser source size: matched %s → %sx%s for route=%s",
                picked.get("source_name"),
                picked.get("width"),
                picked.get("height"),
                route,
            )
        return picked

    # --- Dispatcher ---
    def _dispatch_connector_operation(self, op: str, obs_args: Dict[str, Any]) -> None:
        cl = self._req_client
        if cl is None:
            raise RuntimeError("OBS is not connected")

        if op == "set_program_scene":
            nm = obs_args.get("scene_name") or obs_args.get("name")
            if not nm:
                raise ValueError("scene_name is required")
            cl.set_current_program_scene(str(nm))
            return
        if op == "set_preview_scene":
            nm = obs_args.get("scene_name") or obs_args.get("name")
            if not nm:
                raise ValueError("scene_name is required")
            cl.set_current_preview_scene(str(nm))
            return

        if op == "set_source_enabled":
            scene_name = obs_args.get("scene_name") or ""
            src = obs_args.get("source_name") or ""
            if not scene_name or not src:
                raise ValueError("scene_name and source_name are required")
            off_raw = obs_args.get("search_offset")
            offset = int(off_raw) if off_raw not in ("", None) else None
            item = cl.get_scene_item_id(str(scene_name), str(src), offset)
            sid = _attr(item, "scene_item_id", "sceneItemId")
            if sid is None:
                raise RuntimeError("Could not resolve scene item id")
            en_raw = obs_args.get("enabled", True)
            if isinstance(en_raw, bool):
                enabled = en_raw
            else:
                enabled = _truthy(en_raw)
            cl.set_scene_item_enabled(scene_name, int(sid), bool(enabled))
            return

        if op == "toggle_source":
            scene_name = obs_args.get("scene_name") or ""
            src = obs_args.get("source_name") or ""
            if not scene_name or not src:
                raise ValueError("scene_name and source_name are required")
            off_raw = obs_args.get("search_offset")
            offset = int(off_raw) if off_raw not in ("", None) else None
            item = cl.get_scene_item_id(str(scene_name), str(src), offset)
            sid = _attr(item, "scene_item_id", "sceneItemId")
            if sid is None:
                raise RuntimeError("Could not resolve scene item id")
            cur = cl.get_scene_item_enabled(scene_name, int(sid))
            prev = _attr(cur, "scene_item_enabled", "sceneItemEnabled")
            nv = not bool(prev)
            cl.set_scene_item_enabled(scene_name, int(sid), nv)
            return

        if op == "set_source_transform":
            scene_name = obs_args.get("scene_name") or ""
            src = obs_args.get("source_name") or ""
            if not scene_name or not src:
                raise ValueError("scene_name and source_name are required")
            off_raw = obs_args.get("search_offset")
            offset = int(off_raw) if off_raw not in ("", None) else None
            item = cl.get_scene_item_id(str(scene_name), str(src), offset)
            sid = _attr(item, "scene_item_id", "sceneItemId")
            if sid is None:
                raise RuntimeError("Could not resolve scene item id")
            mr = obs_args.get("merge_with_current_transform", "1")
            merge = str(mr).strip().lower() not in ("0", "false", "no")

            overrides = _gather_transform_overrides(obs_args)
            if merge:
                merged: Dict[str, Any] = {}
                raw_tr = cl.send(
                    "GetSceneItemTransform",
                    {"sceneName": str(scene_name), "sceneItemId": int(sid)},
                    raw=True,
                )
                sit = raw_tr.get("sceneItemTransform") if isinstance(raw_tr, dict) else None
                if isinstance(sit, dict):
                    merged = dict(sit)
                merged.update(overrides)
                cl.set_scene_item_transform(scene_name, int(sid), merged)
            else:
                if not overrides:
                    raise ValueError("No transform fields supplied")
                cl.set_scene_item_transform(scene_name, int(sid), overrides)
            return

        if op == "set_input_mute":
            inp = obs_args.get("input_name") or ""
            if not inp:
                raise ValueError("input_name is required")
            cl.set_input_mute(str(inp), _truthy(obs_args.get("muted", True)))
            return
        if op == "toggle_input_mute":
            inp = obs_args.get("input_name") or ""
            if not inp:
                raise ValueError("input_name is required")
            cl.toggle_input_mute(str(inp))
            return
        if op == "start_stream":
            cl.start_stream()
            return
        if op == "stop_stream":
            cl.stop_stream()
            return
        if op == "toggle_stream":
            cl.toggle_stream()
            return
        if op == "start_record":
            cl.start_record()
            return
        if op == "stop_record":
            cl.stop_record()
            return
        if op == "toggle_record":
            cl.toggle_record()
            return


obs_service = ObsServiceImpl()


def start_obs_service() -> None:
    # Avoid ERROR stack traces each attempt while OBS isn't running yet.
    logging.getLogger("obsws_python.baseclient.ObsClient").setLevel(logging.CRITICAL)
    logging.getLogger("obsws_python.reqs.ReqClient").setLevel(logging.CRITICAL)
    obs_service.start()

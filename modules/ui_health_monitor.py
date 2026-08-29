# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Watchdog for the NiceGUI native window: detects full-shell black screen and
socket/ping failures, then triggers a controlled page reload.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from nicegui import background_tasks

from .shutdown import is_shutdown_in_progress
from .ui_timer import app_schedule

logger = logging.getLogger(__name__)

_MONITOR_STARTED = False
_PING_SCRIPT_INJECTED = False
_SUSPENDED = False
_health_client: Any = None
_armed_mono: float = 0.0

_PING_INTERVAL_SEC = 60.0
_STARTUP_GRACE_SEC = 90.0
_RELOAD_COOLDOWN_SEC = 120.0
_MAX_RELOADS_PER_SESSION = 5

_consecutive_ping_failures = 0
_last_reload_mono: float = 0.0
_reload_count = 0

_CLIENT_WATCHDOG_SCRIPT = """
<script>
(function () {
    if (window.__mycelianUiHealthWired) { return; }
    window.__mycelianUiHealthWired = true;
    window.__mycelian_ui_ping = Date.now();
    setInterval(function () {
        window.__mycelian_ui_ping = Date.now();
    }, 30000);
})();
</script>
"""

_DOM_CHECK_JS = """
(function () {
    var tabs = document.querySelector('.mycelian-main-tabs');
    var appRoot = document.querySelector('#app, .nicegui-content');
    if (!tabs || !appRoot) { return null; }
    return typeof window.__mycelian_ui_ping === 'number';
})()
"""


def _inject_client_watchdog_script() -> None:
    global _PING_SCRIPT_INJECTED
    if _PING_SCRIPT_INJECTED:
        return
    from nicegui import ui

    ui.add_head_html(_CLIENT_WATCHDOG_SCRIPT, shared=True)
    _PING_SCRIPT_INJECTED = True


def _get_health_client() -> Any:
    client = _health_client
    if client is None:
        return None
    try:
        if getattr(client, "is_deleted", False):
            return None
    except Exception:
        return None
    return client


def _any_client_has_socket() -> bool:
    client = _get_health_client()
    if client is not None and getattr(client, "has_socket_connection", False):
        return True
    try:
        from nicegui import Client

        for inst in Client.instances.values():
            if getattr(inst, "has_socket_connection", False):
                return True
    except Exception:
        pass
    return False


def _in_startup_grace() -> bool:
    if _armed_mono <= 0:
        return True
    return (time.monotonic() - _armed_mono) < _STARTUP_GRACE_SEC


def set_monitor_suspended(suspended: bool) -> None:
    """Pause health checks while the app is minimised to the tray.

    Minimising parks the webview on a blank page on purpose, which looks identical to
    the black-screen failure this watchdog exists to catch. Without this the monitor
    would count failures against a window nobody is looking at.
    """
    global _SUSPENDED, _consecutive_ping_failures, _armed_mono, _reload_count
    if _SUSPENDED == suspended:
        return
    _SUSPENDED = suspended
    _consecutive_ping_failures = 0
    if not suspended:
        # Restoring rebuilds the UI from scratch; give it the same grace as a cold start
        # and a fresh reload budget so a long 24/7 session cannot permanently disable
        # the watchdog after five black-screen recoveries.
        _armed_mono = time.monotonic()
        _reload_count = 0
    logger.debug("ui_health: monitor %s", "suspended" if suspended else "resumed")


def _is_not_ready_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "slot stack" in msg
        or "slot cannot be determined" in msg
        or "background task" in msg
        or ("client" in msg and "deleted" in msg)
    )


def _trigger_reload(reason: str) -> None:
    global _last_reload_mono, _reload_count

    if is_shutdown_in_progress() or _SUSPENDED:
        return

    now = time.monotonic()
    if now - _last_reload_mono < _RELOAD_COOLDOWN_SEC:
        logger.debug("ui_health: reload skipped (cooldown) reason=%s", reason)
        return
    if _reload_count >= _MAX_RELOADS_PER_SESSION:
        logger.warning(
            "ui_health: reload cap reached (%d); not reloading (%s)",
            _MAX_RELOADS_PER_SESSION,
            reason,
        )
        return

    client = _get_health_client()
    if client is None or not getattr(client, "has_socket_connection", False):
        logger.debug("ui_health: reload skipped (no live client) reason=%s", reason)
        return

    _last_reload_mono = now
    _reload_count += 1
    logger.warning("ui_health: triggering_reload (%s)", reason)

    async def _reload() -> None:
        try:
            await client.run_javascript("window.location.reload()", timeout=5.0)
        except Exception as exc:
            logger.error("ui_health: reload failed: %s", exc, exc_info=True)

    background_tasks.create(_reload(), name="ui_health_reload")


def _on_ping_check_result(alive: Optional[Any]) -> None:
    global _consecutive_ping_failures

    if is_shutdown_in_progress() or _in_startup_grace():
        return

    socket_ok = _any_client_has_socket()

    if alive is False or not socket_ok:
        _consecutive_ping_failures += 1
        if not socket_ok:
            logger.warning(
                "ui_health: socket_dead (failures=%d)",
                _consecutive_ping_failures,
            )
        else:
            logger.warning(
                "ui_health: ping_failed (failures=%d)",
                _consecutive_ping_failures,
            )
        if _consecutive_ping_failures >= 3:
            _trigger_reload("ping_or_socket_dead")
        return

    if alive is None:
        _consecutive_ping_failures += 1
        logger.warning(
            "ui_health: shell_missing (failures=%d)",
            _consecutive_ping_failures,
        )
        if _consecutive_ping_failures >= 2:
            logger.warning("ui_health: full_black")
            _trigger_reload("full_black")
        return

    _consecutive_ping_failures = 0
    logger.debug("ui_health: ok socket=%s", socket_ok)


async def _run_health_check_async(client: Any) -> None:
    if is_shutdown_in_progress() or _in_startup_grace():
        return
    if not getattr(client, "has_socket_connection", False):
        logger.debug("ui_health: skip (client socket not connected)")
        return

    try:
        result = await client.run_javascript(_DOM_CHECK_JS, timeout=5.0)
        _on_ping_check_result(result)
    except Exception as exc:
        if _is_not_ready_error(exc):
            logger.debug("ui_health: skip (not ready): %s", exc)
            return
        logger.warning("ui_health: check failed: %s", exc)
        _on_ping_check_result(False)


def _run_health_check() -> None:
    if _SUSPENDED:
        logger.debug("ui_health: skip (minimized to tray)")
        return
    if is_shutdown_in_progress() or _in_startup_grace():
        logger.debug("ui_health: skip (startup grace)")
        return

    client = _get_health_client()
    if client is None:
        logger.debug("ui_health: skip (no health client)")
        return
    if not getattr(client, "has_socket_connection", False):
        logger.debug("ui_health: skip (socket not connected yet)")
        return

    background_tasks.create(
        _run_health_check_async(client),
        name="ui_health_check",
    )


def arm_ui_health_monitor(client: Any) -> None:
    """Bind the monitor to the native-window client and start periodic checks."""
    global _MONITOR_STARTED, _health_client, _armed_mono, _reload_count

    _health_client = client
    _armed_mono = time.monotonic()
    _reload_count = 0

    if _MONITOR_STARTED:
        return
    _MONITOR_STARTED = True
    _inject_client_watchdog_script()
    app_schedule(_PING_INTERVAL_SEC, _run_health_check, active=True)
    logger.debug(
        "ui_health: monitor armed (interval=%ss, grace=%ss)",
        _PING_INTERVAL_SEC,
        _STARTUP_GRACE_SEC,
    )


def start_ui_health_monitor() -> None:
    """Backward-compatible alias; prefer :func:`arm_ui_health_monitor`."""
    try:
        from nicegui import context

        arm_ui_health_monitor(context.client)
    except Exception as exc:
        logger.debug("ui_health: could not arm monitor: %s", exc)


def stop_ui_health_monitor() -> None:
    """Reset monitor state on shutdown (timers stop with the app)."""
    global _MONITOR_STARTED, _consecutive_ping_failures, _health_client, _armed_mono
    global _SUSPENDED
    _MONITOR_STARTED = False
    _consecutive_ping_failures = 0
    _health_client = None
    _armed_mono = 0.0
    _SUSPENDED = False
    logger.debug("ui_health: monitor stopped")

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Central application shutdown coordinator.

All exit paths (NiceGUI on_shutdown, signals, post-ui.run) should call
``shutdown_application()`` so background services are torn down consistently.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Per-step caps; daemon threads get short joins on exit.
_SHUTDOWN_JOIN_TIMEOUT = 1.5
_SHUTDOWN_INTEGRATION_PARALLEL_TIMEOUT = 2.5

_shutdown_in_progress = False
_shutdown_lock = threading.Lock()
_watchdog_timer: Optional[threading.Timer] = None
_native_close_registered = False


def is_shutdown_in_progress() -> bool:
    return _shutdown_in_progress


def mark_shutdown_in_progress() -> bool:
    """Mark shutdown started. Returns False if already in progress."""
    global _shutdown_in_progress
    with _shutdown_lock:
        if _shutdown_in_progress:
            return False
        _shutdown_in_progress = True
        return True


def _start_shutdown_watchdog() -> None:
    global _watchdog_timer
    raw = os.environ.get("MYCELIAN_SHUTDOWN_WATCHDOG_SEC", "20").strip()
    if not raw:
        raw = "20"
    try:
        seconds = float(raw)
    except ValueError:
        logger.warning("Invalid MYCELIAN_SHUTDOWN_WATCHDOG_SEC=%r; ignoring", raw)
        return
    if seconds <= 0:
        return

    def _fire() -> None:
        try:
            names = [t.name for t in threading.enumerate()]
            logger.error(
                "Shutdown watchdog fired after %.1fs; threads=%s",
                seconds,
                names,
            )
        except Exception:
            logger.error("Shutdown watchdog fired after %.1fs", seconds)
        os._exit(0)

    _watchdog_timer = threading.Timer(seconds, _fire)
    _watchdog_timer.daemon = True
    _watchdog_timer.start()
    logger.warning("Shutdown watchdog armed for %.1fs", seconds)


def _cancel_shutdown_watchdog() -> None:
    global _watchdog_timer
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
        _watchdog_timer = None


def _run_step(name: str, func: Callable[[], None], timeout: float = 4.0) -> None:
    t0 = time.perf_counter()
    logger.info("Shutdown: %s", name)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(func)
        try:
            fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("Shutdown timed out: %s (%.1fs)", name, timeout)
        except Exception as e:
            logger.error("Shutdown failed: %s — %s", name, e, exc_info=True)
        else:
            logger.info("Shutdown: %s (%.2fs)", name, time.perf_counter() - t0)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _run_parallel(
    steps: list[tuple[str, Callable[[], None]]], timeout: float
) -> None:
    if not steps:
        return
    t0 = time.perf_counter()
    names = ", ".join(n for n, _ in steps)
    logger.info("Shutdown (parallel): %s", names)

    def _safe(name: str, func: Callable[[], None]) -> None:
        try:
            func()
        except Exception as e:
            logger.debug("Shutdown %s: %s", name, e)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(steps))
    try:
        futs = {
            pool.submit(_safe, name, func): name for name, func in steps
        }
        done, not_done = concurrent.futures.wait(
            futs, timeout=timeout, return_when=concurrent.futures.ALL_COMPLETED
        )
        for fut in not_done:
            fut.cancel()
            logger.warning(
                "Shutdown timed out: %s (batch %.1fs)", futs[fut], timeout
            )
        for fut in done:
            try:
                fut.result()
            except Exception:
                pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    logger.info("Shutdown (parallel) done (%.2fs)", time.perf_counter() - t0)


def _pause_alerts_and_activity_feed() -> None:
    from . import web_engine
    from .uiwindows.activity_feed import stop_alert_processor

    try:
        web_engine.set_alerts_paused(True)
    except Exception:
        pass
    stop_alert_processor()


def _stop_alert_processor() -> None:
    from . import alert_processor

    alert_processor.cleanup()


def _stop_connector_system() -> None:
    import asyncio

    from .connector_manager import get_manager

    manager = get_manager()
    loop = manager.connector_loop

    async def _async_stop() -> None:
        await manager.stop()

    if loop is not None and loop.is_running():
        try:
            fut = asyncio.run_coroutine_threadsafe(_async_stop(), loop)
            fut.result(timeout=2.0)
        except Exception as e:
            logger.debug("Connector manager async stop: %s", e)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception as e:
            logger.debug("Connector loop stop schedule: %s", e)

    manager.is_running = False
    th = manager.connector_thread
    if th is not None and th.is_alive():
        th.join(timeout=1.0)

    try:
        from .hotkey_listener import cleanup as cleanup_hotkey_listener

        cleanup_hotkey_listener()
    except Exception as e:
        logger.debug("Hotkey listener cleanup: %s", e)


def _stop_obs() -> None:
    from .obs_service import obs_service as _obs

    _obs.stop()


def _stop_youtube() -> None:
    from . import youtube

    youtube.stop_youtube_service(join_timeout=_SHUTDOWN_JOIN_TIMEOUT)


def _stop_spotify() -> None:
    from . import spotify

    spotify.stop_spotify_service(join_timeout=_SHUTDOWN_JOIN_TIMEOUT)


def _stop_discord() -> None:
    from . import discord_service

    discord_service.disconnect()


def _stop_psn() -> None:
    from . import psn_service

    psn_service.stop_psn_data_updater_thread(join_timeout=_SHUTDOWN_JOIN_TIMEOUT)


def _stop_twitch() -> None:
    from . import twitch
    from .twitch_oauth import stop_active_oauth

    api = twitch.get_twitch_api()
    if api is not None:
        api.cancel_oauth()
        api.stop_connection()
    stop_active_oauth()


def _stop_chatbot() -> None:
    from . import chatbot
    from .twitch_oauth import stop_active_oauth

    api = chatbot.get_chatbot_api()
    if api is not None:
        api.cancel_oauth()
        api.stop_health_check()
    stop_active_oauth()


def _stop_connection_monitor() -> None:
    from .connection_monitor import stop as stop_connection_monitor

    stop_connection_monitor()


def _stop_deferred_services() -> None:
    try:
        from .service_manager import get_service_manager

        get_service_manager().shutdown()
    except Exception as e:
        logger.debug("Deferred service manager stop: %s", e)


def _save_statistics() -> None:
    from . import statistics_manager

    statistics_manager.shutdown_statistics()


# How long to let the webview child close its own window before we terminate it.
_NATIVE_WINDOW_EXIT_GRACE_SEC = 2.0


def _close_native_window() -> None:
    """Destroy the native window and reap the process that owns it.

    Under NiceGUI native mode the pywebview window lives in a spawn child, so
    ``webview.windows`` is empty here and the only way to reach the window is the proxy
    that marshals calls over NiceGUI's method queue. This never had to be explicit
    before: closing the window was what *started* shutdown. Quitting from the tray
    reverses that, and without this the child outlives the app as an orphan still
    showing the window.
    """
    import multiprocessing

    _cleanup_pywebview_windows()

    try:
        from nicegui import app as ng_app

        main_window = getattr(getattr(ng_app, "native", None), "main_window", None)
        if main_window is not None:
            main_window.destroy()
    except Exception as e:
        logger.debug("Native window destroy: %s", e)

    # Let the child act on the queued destroy, then make sure it is really gone.
    # The exit path ends in os._exit(), which skips the multiprocessing atexit hook
    # that would otherwise terminate daemon children for us.
    deadline = time.monotonic() + _NATIVE_WINDOW_EXIT_GRACE_SEC
    while time.monotonic() < deadline:
        if not multiprocessing.active_children():
            return
        time.sleep(0.05)

    for child in multiprocessing.active_children():
        logger.warning("Terminating surviving child process %s", child.name)
        try:
            child.terminate()
            child.join(timeout=1.0)
        except Exception as e:
            logger.debug("Could not terminate child %s: %s", child.name, e)


def _cleanup_pywebview_windows() -> None:
    import platform

    try:
        import webview
    except ImportError:
        return

    if not getattr(webview, "windows", None):
        return

    for window in webview.windows:
        try:
            if window and hasattr(window, "destroy"):
                window.destroy()
        except AttributeError as e:
            if platform.system() == "Windows" and (
                "BrowserProcessId" in str(e) or "NoneType" in str(e)
            ):
                logger.debug("WebView2 cleanup handled: %s", e)
            else:
                logger.error("WebView cleanup attribute error: %s", e)
        except Exception as e:
            logger.error("Error destroying WebView window: %s", e)


def cleanup_shared_memory() -> None:
    try:
        from multiprocessing import shared_memory  # type: ignore

        try:
            status_shm = shared_memory.SharedMemory(name="status_flags", create=False)
            status_shm.close()
            status_shm.unlink()
            logger.info("Shared memory status_flags cleaned up")
        except FileNotFoundError:
            logger.debug("Shared memory status_flags not found")
    except Exception as e:
        logger.debug("Shared memory cleanup: %s", e)


def _signal_nicegui_server_exit() -> None:
    try:
        from nicegui.server import Server

        if Server.instance is not None:
            Server.instance.should_exit = True
    except Exception as e:
        logger.debug("NiceGUI server should_exit: %s", e)

    try:
        from nicegui import app as ng_app

        mw = getattr(getattr(ng_app, "native", None), "main_window", None)
        if mw is not None and hasattr(mw, "signal_server_shutdown"):
            mw.signal_server_shutdown()
    except Exception as e:
        logger.debug("Native signal_server_shutdown: %s", e)


def register_native_window_close_handler() -> None:
    """Ensure parent process stops the NiceGUI server when the native window closes."""
    global _native_close_registered
    if _native_close_registered:
        return
    try:
        from nicegui.native.event_manager import event_manager
    except ImportError:
        logger.debug("NiceGUI native event_manager not available")
        return

    def _on_native_closed(_event) -> None:
        logger.info("Native window closed event received")
        _signal_nicegui_server_exit()

    event_manager.on("closed", _on_native_closed)
    _native_close_registered = True
    logger.debug("Registered native window closed shutdown handler")


def _stop_ui_health_monitor() -> None:
    try:
        from .ui_health_monitor import stop_ui_health_monitor

        stop_ui_health_monitor()
    except Exception as e:
        logger.debug("UI health monitor stop: %s", e)


def _stop_tray() -> None:
    try:
        from .tray_controller import allow_window_close, shutdown as shutdown_tray

        # Drop the close veto first, or the window will refuse to be destroyed below.
        allow_window_close()
        shutdown_tray()
    except Exception as e:
        logger.debug("Tray stop: %s", e)


def shutdown_application(*, reason: str, force: bool = False) -> None:
    """
    Idempotent full-application teardown.

    Args:
        reason: Short label for logs (e.g. ``nicegui_on_shutdown``).
        force: If True, call ``os._exit`` after cleanup (signals / watchdog).
    """
    if not mark_shutdown_in_progress():
        logger.debug("Shutdown already in progress (reason=%s)", reason)
        return

    logger.info("Application shutdown starting (reason=%s)", reason)
    _start_shutdown_watchdog()

    try:
        _run_step("ui_health_monitor", _stop_ui_health_monitor, timeout=1.0)
        _run_step("tray", _stop_tray, timeout=3.0)
        _run_step("pause_alerts", _pause_alerts_and_activity_feed, timeout=2.0)
        _run_step("connection_monitor", _stop_connection_monitor, timeout=3.0)
        # Stops web engine, game hooks, alert threads (no separate web_engine step).
        _run_step("alert_processor", _stop_alert_processor, timeout=6.0)
        _run_step("connectors", _stop_connector_system, timeout=4.0)
        _run_step("obs", _stop_obs, timeout=6.0)
        _run_parallel(
            [
                ("youtube", _stop_youtube),
                ("spotify", _stop_spotify),
                ("discord", _stop_discord),
                ("psn", _stop_psn),
                ("twitch", _stop_twitch),
                ("chatbot", _stop_chatbot),
            ],
            timeout=_SHUTDOWN_INTEGRATION_PARALLEL_TIMEOUT,
        )
        _run_step("deferred_services", _stop_deferred_services, timeout=2.0)
        _run_step("pywebview", _close_native_window, timeout=5.0)
        _run_step("statistics", _save_statistics, timeout=8.0)
    finally:
        _cancel_shutdown_watchdog()
        logger.info("Application shutdown finished (reason=%s)", reason)

    if force:
        from .updater import _force_application_exit

        _force_application_exit()


def request_native_window_shutdown() -> None:
    """Called when the native webview window closes without a full server shutdown."""
    _signal_nicegui_server_exit()

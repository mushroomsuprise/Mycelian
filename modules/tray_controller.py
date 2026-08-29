# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Main-process side of the system tray integration.

Owns the tray child process (see :mod:`modules.tray_process`) and drives the native
window across the pywebview process boundary.

Minimising does more than hide the window: after hiding it we point the webview at
``about:blank``. That tears down Vue, Quasar and the whole DOM, and it drops the
NiceGUI websocket so every client-bound ``ui.timer`` stops with it. Restoring loads
the application URL again, which produces a fresh client and rebuilds the UI through
``build_root_ui``.

Because the window lives in a spawn child, all window calls are asynchronous messages
on NiceGUI's ``method_queue``. Nothing here may assume the call has taken effect by
the time it returns.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

BLANK_URL = "about:blank"

# Give the page a moment to paint before revealing the window, otherwise the user sees
# a white flash while NiceGUI boots.
_RESTORE_PAINT_DELAY_SEC = 0.4

_SPAWN_CONTEXT = multiprocessing.get_context("spawn")

_state_lock = threading.RLock()
_process: Optional[Any] = None
_conn: Optional[Any] = None
_reader_thread: Optional[threading.Thread] = None
_minimized = False
_window_url: Optional[str] = None
_tray_unavailable_reason: Optional[str] = None
_native_handlers_registered = False
_database_warning_shown = False
_fallback_applied = False
_quit_requested = False


# ----- settings -----


def _app_settings() -> Any:
    from .dataobjects import state_manager

    return state_manager.get_app_settings()


def minimize_to_tray_enabled() -> bool:
    try:
        return bool(getattr(_app_settings(), "minimize_to_tray", False))
    except Exception:
        return False


def start_minimized_enabled() -> bool:
    try:
        return bool(getattr(_app_settings(), "start_minimized", False))
    except Exception:
        return False


def tray_wanted() -> bool:
    """The tray icon is only worth running if something can send us to it."""
    return minimize_to_tray_enabled() or start_minimized_enabled()


# ----- window plumbing -----


def set_window_url(url: str) -> None:
    """Record the real application URL, reported by the webview child on startup."""
    global _window_url
    if url and url != BLANK_URL:
        _window_url = url
        logger.debug("Tray: cached native window URL %s", url)


def get_window_url() -> Optional[str]:
    if _window_url:
        return _window_url
    # Fallback for the unlikely case the ready event was missed.
    try:
        from nicegui import core

        config = getattr(core.app, "config", None)
        host = getattr(config, "host", None) or "127.0.0.1"
        port = getattr(config, "port", None)
        if port:
            return f"http://{host}:{port}/"
    except Exception:
        pass
    return None


def _main_window() -> Any:
    try:
        from nicegui import app as ng_app

        return getattr(getattr(ng_app, "native", None), "main_window", None)
    except Exception:
        return None


def push_window_method(name: str, *args: Any) -> bool:
    """Invoke a window method in the webview child by name.

    NiceGUI's executor resolves the name with ``getattr(window, name)``, so this also
    reaches the helpers ``native_window_bridge`` attaches to the window instance.
    """
    try:
        from nicegui.native import native as ng_native

        queue = getattr(ng_native, "method_queue", None)
        if queue is None:
            return False
        queue.put((name, args, {}))
        return True
    except Exception as e:
        logger.debug("Tray: could not push window method %s: %s", name, e)
        return False


def push_tray_mode(enabled: bool) -> None:
    """Tell the webview child whether to veto the window close button."""
    push_window_method("mycelian_set_tray_mode", bool(enabled))


def allow_window_close() -> None:
    """Clear the close veto so shutdown can actually destroy the window."""
    push_window_method("mycelian_allow_close")


def set_dock_visible(visible: bool) -> None:
    """Show or hide the macOS Dock icon. No-op on other platforms."""
    push_window_method("mycelian_set_dock_visible", bool(visible))


def destroy_native_window() -> None:
    """Hide the window immediately, then ask the webview child to destroy it.

    The window belongs to that child, so this goes through NiceGUI's window proxy
    rather than ``webview.windows``, which is empty here. Hide is not subject to the
    close veto and takes effect on the next cocoa runloop; destroy is scheduled after
    that and can still be cancelled if the veto is still on, which is why
    ``mycelian_force_quit`` clears the veto in the same child-side turn.
    """
    window = _main_window()
    if window is not None:
        try:
            window.hide()
        except Exception as e:
            logger.debug("Tray: could not hide the native window: %s", e)
    push_window_method("mycelian_force_quit")


def _wait_for_native_window_exit(*, timeout: float = 2.0) -> None:
    """Reap the webview child so ``os._exit`` cannot leave the window on screen.

    ``os._exit`` skips the multiprocessing atexit hook that would otherwise terminate
    daemon children. The tray process is still needed until we stop it ourselves, so
    only the other children are waited on and, if necessary, killed.
    """
    import multiprocessing

    tray = _process
    tray_pid = getattr(tray, "pid", None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        others = [
            child
            for child in multiprocessing.active_children()
            if getattr(child, "pid", None) != tray_pid
        ]
        if not others:
            return
        time.sleep(0.05)

    for child in multiprocessing.active_children():
        if getattr(child, "pid", None) == tray_pid:
            continue
        logger.warning("Tray: terminating window process %s so quit can finish", child.name)
        try:
            child.terminate()
            child.join(timeout=1.0)
        except Exception as e:
            logger.debug("Tray: could not terminate window process %s: %s", child.name, e)


# ----- minimize / restore -----


def is_minimized() -> bool:
    return _minimized


def minimize_to_tray(*, reason: str = "user") -> bool:
    """Hide the window and unload the UI. Returns False if it could not be done."""
    global _minimized

    with _state_lock:
        if _minimized:
            return True

        window = _main_window()
        if window is None:
            logger.warning("Tray: no native window to minimize")
            return False

        if not is_tray_running() and not start_tray():
            logger.warning("Tray: refusing to minimize without a tray icon")
            return False

        try:
            # Order matters: drop the Dock icon before hiding, so macOS cannot order
            # the window back on screen from a Dock click and reveal the blank page.
            set_dock_visible(False)
            window.hide()
            push_window_method("mycelian_prepare_for_blank")
            window.load_url(BLANK_URL)
        except Exception as e:
            logger.error("Tray: failed to minimize window: %s", e, exc_info=True)
            return False

        _minimized = True

    _suspend_ui_health_monitor(True)
    _send_to_tray({"cmd": "set_state", "minimized": True})
    logger.warning("Tray: minimized to tray (reason=%s)", reason)
    return True


def restore_from_tray(*, reason: str = "user") -> bool:
    """Reload the UI and show the window again."""
    global _minimized

    with _state_lock:
        window = _main_window()
        if window is None:
            logger.warning("Tray: no native window to restore")
            return False

        url = get_window_url()
        if not url:
            logger.error("Tray: application URL unknown; cannot restore the window")
            return False

        try:
            set_dock_visible(True)
            if _minimized:
                window.load_url(url)
            window.show()
            window.restore()
        except Exception as e:
            logger.error("Tray: failed to restore window: %s", e, exc_info=True)
            return False

        _minimized = False

    _suspend_ui_health_monitor(False)
    _send_to_tray({"cmd": "set_state", "minimized": False})
    logger.warning("Tray: restored from tray (reason=%s)", reason)
    _flush_pending_update_prompt()
    return True


def _restore_with_paint_delay() -> None:
    """Restore from a worker thread, revealing the window once the page has loaded."""
    global _minimized

    window = _main_window()
    url = get_window_url()
    if window is None or not url:
        restore_from_tray(reason="tray_menu")
        return

    with _state_lock:
        was_minimized = _minimized
        try:
            set_dock_visible(True)
            if was_minimized:
                window.load_url(url)
        except Exception as e:
            logger.error("Tray: failed to reload the UI: %s", e, exc_info=True)
            return
        _minimized = False

    if was_minimized:
        time.sleep(_RESTORE_PAINT_DELAY_SEC)

    try:
        window.show()
        window.restore()
    except Exception as e:
        logger.error("Tray: failed to show the window: %s", e, exc_info=True)
        return

    _suspend_ui_health_monitor(False)
    _send_to_tray({"cmd": "set_state", "minimized": False})
    logger.warning("Tray: restored from tray (reason=tray_menu)")
    _flush_pending_update_prompt()


def _suspend_ui_health_monitor(suspended: bool) -> None:
    try:
        from .ui_health_monitor import set_monitor_suspended

        set_monitor_suspended(suspended)
    except Exception as e:
        logger.debug("Tray: UI health monitor suspend toggle failed: %s", e)


def _flush_pending_update_prompt() -> None:
    def _flush() -> None:
        try:
            from .updater import update_manager

            update_manager.flush_pending_prompt()
        except Exception as e:
            logger.debug("Tray: no pending update prompt to flush: %s", e)

    # Tray actions arrive on the pipe reader thread and flush_pending_prompt schedules
    # a NiceGUI timer, so it cannot run on the caller's thread.
    from .ui_timer import run_on_ui_loop

    run_on_ui_loop(_flush)


# ----- close interception -----


def handle_close_request() -> None:
    """Decide what the window close button means.

    Called from the ``mycelian_close_requested`` native event, which the webview child
    only emits after vetoing a close while tray mode is on.
    """
    if minimize_to_tray_enabled():
        if minimize_to_tray(reason="window_close"):
            return
        logger.warning("Tray: minimize failed on close; quitting instead")
    quit_application(reason="window_close")


def quit_application(*, reason: str) -> None:
    """Full application exit, from the tray menu or a failed minimize."""
    global _quit_requested
    from .shutdown import is_shutdown_in_progress

    if is_shutdown_in_progress() or _quit_requested:
        return
    # Quitting from the tray menu stops the icon, which closes the pipe. Record the
    # intent now so the reader thread does not mistake that for the tray crashing and
    # helpfully un-hide the window on the way out.
    _quit_requested = True
    threading.Thread(
        target=_quit_worker,
        args=(reason,),
        name="mycelian-tray-quit",
        daemon=True,
    ).start()


def _quit_worker(reason: str) -> None:
    from .shutdown import shutdown_application

    logger.warning("Tray: quitting application (reason=%s)", reason)
    # Hide + destroy before the multi-second service teardown, or the window sits
    # on screen the whole time. Wait for the webview child to actually die:
    # destroy() is async on cocoa, and os._exit will otherwise orphan it.
    destroy_native_window()
    _wait_for_native_window_exit()
    stop_tray()
    try:
        shutdown_application(reason=f"tray_{reason}", force=False)
    finally:
        from .updater import _force_application_exit

        _force_application_exit()


def register_native_event_handlers() -> None:
    """Subscribe to the custom events emitted by ``native_window_bridge``."""
    global _native_handlers_registered
    if _native_handlers_registered:
        return
    try:
        from nicegui.native.event_manager import event_manager
    except ImportError:
        logger.debug("Tray: NiceGUI native event_manager unavailable")
        return

    def _on_window_ready(event) -> None:
        url = (getattr(event, "args", None) or {}).get("url")
        if url:
            set_window_url(url)
        push_tray_mode(minimize_to_tray_enabled())

    def _on_close_requested(_event) -> None:
        handle_close_request()

    def _on_quit_requested(_event) -> None:
        # The window is already on its way out (Cmd+Q / Quit menu); tear the rest down.
        quit_application(reason="app_quit")

    def _on_minimized(_event) -> None:
        # The OS minimise button should also go to the tray when tray mode is on.
        if minimize_to_tray_enabled() and not is_minimized():
            minimize_to_tray(reason="window_minimize")

    def _on_window_shown(_event) -> None:
        # Dock / taskbar activation of a start-minimized blank window must restore.
        if is_minimized():
            restore_from_tray(reason="window_shown")

    event_manager.on("mycelian_window_ready", _on_window_ready)
    event_manager.on("mycelian_close_requested", _on_close_requested)
    event_manager.on("mycelian_quit_requested", _on_quit_requested)
    event_manager.on("minimized", _on_minimized)
    event_manager.on("mycelian_window_shown", _on_window_shown)
    event_manager.on("shown", _on_window_shown)
    event_manager.on("restored", _on_window_shown)
    _native_handlers_registered = True
    logger.debug("Tray: native event handlers registered")


# ----- tray child process -----


def _tray_icon_path() -> Optional[str]:
    from .path_utils import get_assets_path

    candidates = [
        get_assets_path(os.path.join("default_assets", "icons", "Mycelian.png")),
        get_assets_path(os.path.join("default_assets", "icons", "Mycelian.ico")),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    logger.error("Tray: no tray icon asset found (looked in %s)", candidates)
    return None


def is_tray_running() -> bool:
    process = _process
    return process is not None and process.is_alive()


def _strip_main_module(get_preparation_data):
    """Stop the spawned child from re-importing Mycelian's ``__main__``.

    ``spawn`` re-imports the parent's main module so the child can unpickle its target.
    Mycelian's ``main.py`` pulls in NiceGUI, Twitch, Discord and the whole UI at module
    scope, which cost the tray icon hundreds of megabytes and several seconds — for a
    process that only needs pystray. ``run_tray`` lives in an importable module, so the
    child can find it from ``sys_path`` alone and the main module is dead weight.
    """

    def patched(name):
        data = get_preparation_data(name)
        data.pop("init_main_from_name", None)
        data.pop("init_main_from_path", None)
        return data

    return patched


def start_tray() -> bool:
    """Spawn the tray child. Safe to call repeatedly."""
    global _process, _conn, _reader_thread, _tray_unavailable_reason

    with _state_lock:
        if is_tray_running():
            return True

        icon_path = _tray_icon_path()
        if icon_path is None:
            _tray_unavailable_reason = "tray icon asset missing"
            return False

        try:
            import multiprocessing.spawn as mp_spawn

            parent_conn, child_conn = _SPAWN_CONTEXT.Pipe(duplex=True)
            from . import tray_process

            process = _SPAWN_CONTEXT.Process(
                target=tray_process.run_tray,
                args=(child_conn, icon_path, _minimized),
                name="mycelian-tray",
                daemon=True,
            )
            original_prep = mp_spawn.get_preparation_data
            mp_spawn.get_preparation_data = _strip_main_module(original_prep)
            try:
                process.start()
            finally:
                mp_spawn.get_preparation_data = original_prep
            child_conn.close()  # the child owns its end now
        except Exception as e:
            logger.error("Tray: could not start the tray process: %s", e, exc_info=True)
            _tray_unavailable_reason = str(e)
            return False

        _process = process
        _conn = parent_conn
        _tray_unavailable_reason = None
        # A fresh tray earns a fresh chance to fall back if it later dies.
        globals()["_fallback_applied"] = False
        _reader_thread = threading.Thread(
            target=_read_from_tray,
            args=(parent_conn,),
            name="mycelian-tray-reader",
            daemon=True,
        )
        _reader_thread.start()

    logger.warning("Tray: tray process started (pid=%s)", getattr(_process, "pid", "?"))
    push_tray_mode(minimize_to_tray_enabled())
    return True


def stop_tray() -> None:
    """Ask the tray child to exit, then make sure it does."""
    global _process, _conn, _reader_thread

    with _state_lock:
        process, conn = _process, _conn
        _process, _conn, _reader_thread = None, None, None

    if conn is not None:
        try:
            conn.send({"cmd": "stop"})
        except Exception:
            pass

    if process is not None and process.is_alive():
        process.join(timeout=2.0)
        if process.is_alive():
            logger.warning("Tray: tray process did not exit; terminating")
            try:
                process.terminate()
                process.join(timeout=1.0)
            except Exception:
                pass

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    logger.debug("Tray: tray process stopped")


def _send_to_tray(payload: dict) -> bool:
    conn = _conn
    if conn is None or not is_tray_running():
        return False
    try:
        conn.send(payload)
        return True
    except Exception as e:
        logger.debug("Tray: send failed: %s", e)
        return False


def notify_via_tray(title: str, message: str) -> bool:
    """Best-effort balloon/notification through the tray icon."""
    return _send_to_tray({"cmd": "notify", "title": title, "message": message})


def _read_from_tray(conn: Any) -> None:
    """Drain tray actions until the child exits or the pipe closes."""
    global _tray_unavailable_reason

    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            break
        if not isinstance(message, dict):
            continue

        action = message.get("action")
        if action == "restore":
            threading.Thread(
                target=_restore_with_paint_delay,
                name="mycelian-tray-restore",
                daemon=True,
            ).start()
        elif action == "quit":
            quit_application(reason="menu")
        elif action == "ready":
            logger.warning("Tray: icon is visible")
        elif action == "unavailable":
            _tray_unavailable_reason = str(message.get("error", "unknown"))
            logger.error("Tray: unavailable — %s", _tray_unavailable_reason)
            _fall_back_to_plain_window(_tray_unavailable_reason)
            break

    logger.debug("Tray: reader thread finished")
    # A pipe that closed while we still own it means the child died on its own.
    # stop_tray() clears _conn first, so ordinary teardown does not land here, and a
    # replaced tray leaves _conn pointing at the newer connection.
    if _conn is conn:
        _fall_back_to_plain_window("the tray icon stopped unexpectedly")


def _fall_back_to_plain_window(reason: str) -> None:
    """Give up on the tray and put the app back into ordinary windowed behaviour.

    Without this a failed tray leaves the window hidden with its Dock icon gone and the
    close button vetoed, which is an application the user cannot reach or quit.
    """
    global _fallback_applied
    from .shutdown import is_shutdown_in_progress

    if is_shutdown_in_progress() or _quit_requested:
        return
    with _state_lock:
        if _fallback_applied:
            return
        _fallback_applied = True

    logger.error("Tray: falling back to a normal window — %s", reason)
    # Stop vetoing the close button so the window can be closed the usual way.
    push_tray_mode(False)
    if is_minimized():
        restore_from_tray(reason="tray_unavailable")
    else:
        set_dock_visible(True)

    try:
        from .notification_engine import notify

        notify(
            "Mycelian could not use the system tray, so it will keep using a normal "
            f"window. ({reason})",
            type="warning",
            timeout=10000,
        )
    except Exception:
        pass


# ----- orchestration -----


def apply_settings() -> None:
    """Reconcile the tray process and the close veto with the saved settings."""
    wanted = tray_wanted()
    if wanted:
        start_tray()
    elif is_tray_running():
        # Never strand the user with a hidden window and no way back to it.
        if is_minimized():
            restore_from_tray(reason="tray_disabled")
        stop_tray()
    push_tray_mode(minimize_to_tray_enabled())


def initialize() -> None:
    """Start the tray during deferred service init, if the settings ask for it."""
    global _minimized
    register_native_event_handlers()
    tray_ok = False
    if tray_wanted():
        tray_ok = start_tray()
    if start_minimized_enabled():
        if tray_ok or is_tray_running():
            _mark_started_minimized()
        else:
            # start_ui created a hidden about:blank window. Without a tray the
            # app would be unreachable — show it like the tray-crash fallback.
            logger.error(
                "Tray: start_minimized requested but the tray icon failed to start"
            )
            with _state_lock:
                _minimized = True
            restore_from_tray(reason="start_minimized_no_tray")
    if tray_wanted():
        warn_background_database_usage()

    try:
        from .autostart import sync_with_settings

        sync_with_settings()
    except Exception as e:
        logger.debug("Tray: autostart sync failed: %s", e)


def _mark_started_minimized() -> None:
    """Record that the window was created hidden and blank by ``start_ui``."""
    global _minimized
    with _state_lock:
        _minimized = True
    set_dock_visible(False)
    _suspend_ui_health_monitor(True)
    _send_to_tray({"cmd": "set_state", "minimized": True})
    logger.info("Tray: started minimized to tray")


def shutdown() -> None:
    """Called by the shutdown coordinator."""
    stop_tray()


# ----- background operation warnings -----


_FIREBASE_BANDWIDTH_WARNING = (
    "Running Mycelian in the background around the clock keeps your Firebase database "
    "in constant use. That raises bandwidth consumption and can exhaust your Firebase "
    "quota. Consider switching to the local SQLite database if you plan to leave "
    "Mycelian running."
)


def warn_background_database_usage() -> None:
    """Warn once per session that 24/7 operation costs Firebase bandwidth.

    Delivered as a desktop notification when there is no window to show a toast in,
    which is the exact situation this warning is about.
    """
    global _database_warning_shown
    if _database_warning_shown:
        return

    try:
        from .config_manager import get_database_type

        if get_database_type() != "firebase":
            return
    except Exception:
        return

    _database_warning_shown = True

    if is_minimized():
        from .system_notify import notify_async

        notify_async(_FIREBASE_BANDWIDTH_WARNING, title="Firebase bandwidth")
        return

    try:
        from .notification_engine import notify

        notify(_FIREBASE_BANDWIDTH_WARNING, type="warning", timeout=12000)
    except Exception as e:
        logger.debug("Tray: could not show the Firebase bandwidth warning: %s", e)

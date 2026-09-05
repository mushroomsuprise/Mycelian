# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Bridge NiceGUI native window_args into the webview subprocess (Windows spawn).

The native window runs in a separate process that imports a fresh ``nicegui.core.app``;
``app.native.window_args`` set in the main process is not visible there. This module
patches ``nicegui.native.native_mode._open_window`` so JSON in the environment
``MYCELIAN_NATIVE_WINDOW_ARGS`` is merged into ``core.app.native.window_args`` before
``webview.create_window`` runs.

It also owns the close-to-tray interception. NiceGUI deliberately does not bridge
pywebview's ``closing`` event across the process boundary, because vetoing a close
needs a synchronous answer. So the veto has to be decided inside the webview process:
we wrap ``webview.create_window`` to attach a ``closing`` handler that returns ``False``
(cancelling the close) whenever tray mode is on, and reports the click to the main
process as a custom native event. The main process then decides between hiding to the
tray and quitting.

Two helpers are attached to the window instance so the main process can drive that
state. NiceGUI's window method executor resolves calls with ``getattr(window, name)``,
so pushing ``mycelian_set_tray_mode`` or ``mycelian_allow_close`` onto its existing
``method_queue`` is enough to reach them; no extra IPC channel is needed.

The replacement must be a **module-level** function so multiprocessing spawn can pickle
``Process(target=native_mode._open_window, ...)`` on Windows (nested functions are not picklable).
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

_MYCELIAN_NATIVE_ENV = "MYCELIAN_NATIVE_WINDOW_ARGS"
_MYCELIAN_TRAY_MODE_ENV = "MYCELIAN_TRAY_MODE"
_PATCH_ATTR = "_mycelian_native_window_bridge"
_ORIG_ATTR = "_mycelian_orig_open_window"
_CREATE_PATCH_ATTR = "_mycelian_create_window_patched"
_ORIG_CREATE_ATTR = "_mycelian_orig_create_window"

# Subprocess-local state. Only ever touched inside the webview process.
_tray_mode = False
_allow_close = False
_event_sender = None
_app_url = ""


def _merge_window_args_from_env() -> None:
    raw = os.environ.get(_MYCELIAN_NATIVE_ENV, "")
    if not raw.strip():
        return
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid %s JSON; ignoring native window overrides", _MYCELIAN_NATIVE_ENV)
        return
    if not isinstance(extra, dict):
        return
    from nicegui import core

    for key, val in extra.items():
        if key == "min_size" and isinstance(val, list):
            val = tuple(val)
        core.app.native.window_args[key] = val


def _send_native_event(event_type: str, **args) -> None:
    """Push a custom event to the main process over NiceGUI's existing event pipe."""
    if _event_sender is None:
        return
    try:
        _event_sender.send({"type": event_type, "args": args})
    except (OSError, BrokenPipeError, ValueError):
        pass


def _is_app_quit_request() -> bool:
    """True when the pending close came from quitting the app, not the close button.

    On macOS the Quit menu item, Cmd+Q and Dock > Quit all land in
    ``BrowserView.AppDelegate.applicationShouldTerminate_``, which asks the very same
    ``closing`` event as the window's red X. Vetoing indiscriminately therefore makes
    the application impossible to quit. ``closing`` is emitted synchronously
    (``should_lock=True``), so the originating backend frame is still on our stack and
    tells the two apart.
    """
    try:
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code.co_name == "applicationShouldTerminate_":
                return True
            frame = frame.f_back
    except Exception:
        pass
    return False


def _on_window_closing():
    """pywebview ``closing`` handler. Returning ``False`` cancels the close.

    Every backend (cocoa, winforms, gtk, qt) treats a ``False`` from any handler as
    "cancel", so this is the one place that can stop the window from being destroyed.
    """
    if _allow_close or not _tray_mode:
        return None
    if _is_app_quit_request():
        # A real quit. Let it through and tell the main process to tear down, or the
        # window would vanish while every background service kept running.
        _send_native_event("mycelian_quit_requested")
        return None
    _send_native_event("mycelian_close_requested")
    return False


_MACOS_POLICY_REGULAR = 0  # NSApplicationActivationPolicyRegular
_MACOS_POLICY_ACCESSORY = 1  # NSApplicationActivationPolicyAccessory


def _set_macos_dock_visible(visible: bool) -> None:
    """Show or hide the Dock icon of the process that owns the window.

    Hiding the window alone leaves the Dock icon behind, and clicking it makes macOS
    order the window back on screen behind our back — showing the blank page we parked
    it on. Dropping to accessory policy removes the icon, so the tray becomes the only
    way back in and that path cannot be reached.
    """
    if sys.platform != "darwin":
        return
    try:
        import AppKit
        from PyObjCTools import AppHelper

        app = AppKit.NSApplication.sharedApplication()
        policy = _MACOS_POLICY_REGULAR if visible else _MACOS_POLICY_ACCESSORY

        def _apply() -> None:
            app.setActivationPolicy_(policy)
            if visible:
                app.activateIgnoringOtherApps_(True)

        # Activation policy is main-thread only, and this runs on NiceGUI's window
        # method executor thread.
        AppHelper.callAfter(_apply)
    except Exception as e:
        logger.warning("native_window_bridge: could not change Dock visibility: %s", e)


def _suppress_esm_warning(window) -> None:
    """Drop NiceGUI's "Vue failed to load" check.

    NiceGUI probes for Vue on every ``loaded`` event. Minimizing parks the window on
    ``about:blank`` deliberately, so that probe fails every time and logs an alarming
    error about an unsupported browser engine. The check has already served its purpose
    on the first real page load.
    """
    try:
        handlers = window.events.loaded._items
    except Exception:
        return
    for handler in list(handlers):
        if getattr(handler, "__name__", "") == "check":
            module = getattr(handler, "__module__", "")
            if module.startswith("nicegui"):
                handlers.remove(handler)


def _attach_tray_hooks(window) -> None:
    """Wire the close veto and the main-process control helpers onto the window."""

    def mycelian_set_tray_mode(enabled) -> None:
        global _tray_mode
        _tray_mode = bool(enabled)

    def mycelian_allow_close() -> None:
        global _allow_close
        _allow_close = True

    def mycelian_set_dock_visible(visible) -> None:
        _set_macos_dock_visible(bool(visible))

    def mycelian_prepare_for_blank() -> None:
        _suppress_esm_warning(window)

    def mycelian_force_quit() -> None:
        """Drop the close veto and tear the window down in this process.

        ``destroy()`` is scheduled onto the cocoa runloop and goes through the same
        ``closing`` handler as the red X. Clearing the veto here, in the same executor
        turn, is what makes a tray Quit actually close a visible window. Hide first so
        the window leaves the screen even if that scheduled close is delayed.
        """
        global _allow_close, _tray_mode
        _allow_close = True
        _tray_mode = False
        try:
            window.hide()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass

    try:
        window.events.closing += _on_window_closing
        window.mycelian_set_tray_mode = mycelian_set_tray_mode
        window.mycelian_allow_close = mycelian_allow_close
        window.mycelian_set_dock_visible = mycelian_set_dock_visible
        window.mycelian_prepare_for_blank = mycelian_prepare_for_blank
        window.mycelian_force_quit = mycelian_force_quit
    except Exception as e:
        logger.error("native_window_bridge: could not attach tray hooks: %s", e)
        return

    def _on_shown_or_restored() -> None:
        # Dock / taskbar / Alt-Tab can order a hidden start-minimized window on
        # screen while it still shows about:blank. Tell the main process to restore.
        _send_native_event("mycelian_window_shown")

    for _event_name in ("shown", "restored"):
        ev = getattr(getattr(window, "events", None), _event_name, None)
        if ev is not None:
            try:
                ev += _on_shown_or_restored
            except Exception as e:
                logger.debug(
                    "native_window_bridge: could not attach %s handler: %s",
                    _event_name,
                    e,
                )

    _send_native_event("mycelian_window_ready", url=_app_url)


def _create_window_with_tray_hooks(*args, **kwargs):
    import webview

    orig = getattr(webview, _ORIG_CREATE_ATTR, None)
    if orig is None:
        raise RuntimeError("native_window_bridge: original create_window not installed")
    window = orig(*args, **kwargs)
    if window is not None:
        _attach_tray_hooks(window)
    return window


def _patch_create_window() -> None:
    import webview

    if getattr(webview, _CREATE_PATCH_ATTR, False):
        return
    setattr(webview, _ORIG_CREATE_ATTR, webview.create_window)
    webview.create_window = _create_window_with_tray_hooks
    setattr(webview, _CREATE_PATCH_ATTR, True)


def _capture_open_window_context(orig, args, kwargs) -> None:
    """Pull ``event_sender`` and the real application URL out of the call.

    Bound by name rather than by position: the signature of ``_open_window`` has
    already changed once between NiceGUI majors.
    """
    global _event_sender, _app_url

    try:
        bound = inspect.signature(orig).bind(*args, **kwargs)
        bound.apply_defaults()
    except TypeError as e:
        logger.warning("native_window_bridge: could not bind _open_window args: %s", e)
        return

    _event_sender = bound.arguments.get("event_sender")

    protocol = bound.arguments.get("protocol") or "http"
    host = bound.arguments.get("host")
    port = bound.arguments.get("port")
    if host and port:
        try:
            from nicegui import helpers

            _app_url = helpers.format_url(protocol, host, port)
        except Exception:
            _app_url = f"{protocol}://{host}:{port}/"


def _open_window_with_mycelian_env(*args, **kwargs):
    """Wrapper for ``nicegui.native.native_mode._open_window`` (must stay module-level for pickle).

    The positional signature of ``_open_window`` changed between NiceGUI 2.x and 3.x
    (3.x prepends ``protocol`` and adds ``event_sender``/``favicon``). To stay robust
    across versions we accept ``*args``/``**kwargs`` and forward them unchanged to the
    original implementation after merging native window args from the environment.
    """
    global _tray_mode

    import nicegui.native.native_mode as nm

    # Parent calls install() before ui.run. The spawned webview child re-imports
    # this module without that call, so stash the original _open_window here too.
    _install_patch()

    _merge_window_args_from_env()
    orig = getattr(nm, _ORIG_ATTR, None)
    if orig is None:
        raise RuntimeError("native_window_bridge: original _open_window not installed")

    # Seed the veto from the environment so the close button behaves correctly even if
    # the window is closed before the main process pushes the current setting.
    _tray_mode = os.environ.get(_MYCELIAN_TRAY_MODE_ENV, "").strip() == "1"

    _capture_open_window_context(orig, args, kwargs)
    try:
        _patch_create_window()
    except Exception as e:
        logger.error("native_window_bridge: create_window patch failed: %s", e)

    return orig(*args, **kwargs)


def _install_patch() -> None:
    try:
        import nicegui.native.native_mode as nm
    except Exception as e:
        logger.debug("NiceGUI native_mode not importable; skipping native bridge: %s", e)
        return

    if getattr(nm, _PATCH_ATTR, False):
        return

    setattr(nm, _ORIG_ATTR, nm._open_window)
    nm._open_window = _open_window_with_mycelian_env
    setattr(nm, _PATCH_ATTR, True)


def install() -> None:
    """Patch NiceGUI native_mode before ``ui.run(native=True)`` spawns the webview."""
    _install_patch()

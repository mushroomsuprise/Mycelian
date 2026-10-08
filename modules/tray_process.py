# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""System tray icon child process.

The tray cannot live in either existing Mycelian process: the main process blocks
its main thread inside ``ui.run`` (uvicorn) and the pywebview subprocess blocks its
own main thread inside ``webview.start``. pystray's macOS backend requires the main
thread of whatever process it runs in, so the tray gets a dedicated spawn child whose
main thread does nothing but run the icon loop.

On Linux that loop is Qt's ``QSystemTrayIcon``. pystray's AppIndicator backend draws
an icon on KDE Wayland but never opens a menu or delivers a click, so the window
cannot be restored. macOS and Windows keep using pystray.

Everything in this module executes in that child. It talks to
:mod:`modules.tray_controller` over a duplex ``multiprocessing.Pipe``:

parent -> child
    ``{"cmd": "set_state", "minimized": bool}``
    ``{"cmd": "set_update", "available": bool}``
    ``{"cmd": "notify", "title": str, "message": str}``
    ``{"cmd": "stop"}``

child -> parent
    ``{"action": "ready"}``
    ``{"action": "restore"}``
    ``{"action": "quit"}``
    ``{"action": "unavailable", "error": str}``

A closed pipe means the parent died, which stops the icon so the child cannot outlive
the application.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any


APP_NAME = "Mycelian"

# Qt's ActivationReason names. Right click is Context: that opens the menu and must
# not also restore, or the window would appear under the menu.
_RESTORE_ACTIVATIONS = frozenset({"Trigger", "DoubleClick", "MiddleClick"})


def activation_restores(reason: Any) -> bool:
    """True when a tray click should bring the window back.

    ``reason`` is a ``QSystemTrayIcon.ActivationReason``. Only the name is read, so
    tests can pass any object with a ``name`` attribute and do not need a display.
    """
    name = getattr(reason, "name", None)
    return isinstance(name, str) and name in _RESTORE_ACTIVATIONS


def _set_macos_accessory_policy() -> None:
    """Keep the tray child out of the Dock and the app switcher.

    Without this the spawn child registers as a second application and macOS shows a
    duplicate Dock icon next to the real one.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication

        # NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        pass


def _load_icon_image(icon_path: str):
    from PIL import Image

    image = Image.open(icon_path)
    # .ico files carry several resolutions; normalise to one the platform backends
    # are happy to scale from.
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    return image.resize((64, 64), Image.LANCZOS)


def run_tray(conn: Any, icon_path: str, minimized: bool) -> None:
    """Child process entry point. Must stay module level so spawn can pickle it."""
    if sys.platform == "linux":
        _run_qt_tray(conn, icon_path, minimized)
        return
    _run_pystray(conn, icon_path, minimized)


def _run_qt_tray(conn: Any, icon_path: str, _minimized: bool) -> None:
    """StatusNotifier icon for KDE and other Linux desktops.

    ``_minimized`` is part of the spawn signature shared with pystray. Restore stays
    enabled either way: a menu item born disabled never becomes clickable on the
    AppIndicator backend this path replaces.
    """

    try:
        from PyQt6.QtCore import QObject, Qt, pyqtSignal
        from PyQt6.QtGui import QAction, QIcon
        from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except Exception as e:
        _send(conn, {"action": "unavailable", "error": f"Qt tray unavailable: {e}"})
        return

    if not os.path.isfile(icon_path):
        _send(conn, {"action": "unavailable", "error": "tray icon unreadable: missing file"})
        return

    app = QApplication([APP_NAME])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        _send(conn, {"action": "unavailable", "error": "no system tray available"})
        return

    icon_image = QIcon(icon_path)
    if icon_image.isNull():
        _send(conn, {"action": "unavailable", "error": "tray icon unreadable: empty image"})
        return

    class _TrayCommands(QObject):
        """Carry pipe messages onto the Qt thread."""

        received = pyqtSignal(object)

    tray = QSystemTrayIcon(icon_image)
    tray.setToolTip(APP_NAME)

    menu = QMenu()
    restore_action = QAction("Restore Mycelian", menu)
    update_action = QAction("Update available", menu)
    update_action.setVisible(False)
    quit_action = QAction("Quit Mycelian", menu)
    menu.addAction(restore_action)
    menu.addAction(update_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    # Set before show(). Plasma reads the menu from the StatusNotifier item, and a
    # menu attached later is the same bug as AppIndicator's snapshot.
    tray.setContextMenu(menu)

    def on_restore(*_args: object) -> None:
        _send(conn, {"action": "restore"})

    def on_quit(*_args: object) -> None:
        _send(conn, {"action": "quit"})
        tray.hide()
        app.quit()

    restore_action.triggered.connect(on_restore)
    update_action.triggered.connect(on_restore)
    quit_action.triggered.connect(on_quit)

    def on_activated(reason: Any) -> None:
        if activation_restores(reason):
            on_restore()

    tray.activated.connect(on_activated)

    commands = _TrayCommands()

    def handle(message: object) -> None:
        if not isinstance(message, dict):
            return
        command = message.get("cmd")
        if command == "stop":
            tray.hide()
            app.quit()
        elif command == "set_update":
            update_action.setVisible(bool(message.get("available", False)))
        elif command == "notify":
            try:
                tray.showMessage(
                    str(message.get("title", APP_NAME)),
                    str(message.get("message", "")),
                )
            except Exception:
                pass
        # set_state is accepted and ignored. Restore stays enabled.

    commands.received.connect(handle, Qt.ConnectionType.QueuedConnection)

    def reader() -> None:
        """Drain parent commands until the pipe closes."""
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                commands.received.emit({"cmd": "stop"})
                break
            if not isinstance(message, dict):
                continue
            commands.received.emit(message)
            if message.get("cmd") == "stop":
                break

    # Keep the menu and the bridge alive for the whole event loop. Qt does not
    # take ownership of the context menu.
    tray._mycelian_menu = menu  # type: ignore[attr-defined]
    tray._mycelian_commands = commands  # type: ignore[attr-defined]

    threading.Thread(target=reader, name="mycelian-tray-reader", daemon=True).start()
    tray.show()
    _send(conn, {"action": "ready"})
    app.exec()


def _run_pystray(conn: Any, icon_path: str, minimized: bool) -> None:
    """macOS and Windows tray. Not used on Linux."""
    try:
        import pystray
    except Exception as e:  # pragma: no cover - depends on the host platform
        _send(conn, {"action": "unavailable", "error": f"pystray unavailable: {e}"})
        return

    try:
        image = _load_icon_image(icon_path)
    except Exception as e:
        _send(conn, {"action": "unavailable", "error": f"tray icon unreadable: {e}"})
        return

    _set_macos_accessory_policy()

    state = {"minimized": bool(minimized), "update_available": False}

    def on_restore(_icon=None, _item=None) -> None:
        _send(conn, {"action": "restore"})

    def on_quit(icon, _item=None) -> None:
        _send(conn, {"action": "quit"})
        icon.stop()

    # Restore stays enabled. Gating it on the minimized flag builds the item
    # disabled, and AppIndicator never applies the later sensitivity change, which
    # strands the window. Showing an already-visible window is harmless.
    menu = pystray.Menu(
        pystray.MenuItem("Restore Mycelian", on_restore, default=True),
        pystray.MenuItem(
            "Update available",
            on_restore,
            visible=lambda _item: state["update_available"],
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Mycelian", on_quit),
    )

    icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)

    def reader() -> None:
        """Drain parent commands until the pipe closes."""
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                break  # parent is gone
            if not isinstance(message, dict):
                continue
            command = message.get("cmd")
            if command == "stop":
                break
            if command == "set_state":
                state["minimized"] = bool(message.get("minimized", False))
                try:
                    icon.update_menu()
                except Exception:
                    pass
            elif command == "set_update":
                state["update_available"] = bool(message.get("available", False))
                try:
                    icon.update_menu()
                except Exception:
                    pass
            elif command == "notify":
                try:
                    icon.notify(
                        str(message.get("message", "")),
                        str(message.get("title", APP_NAME)),
                    )
                except Exception:
                    pass
        try:
            icon.stop()
        except Exception:
            pass

    def setup(running_icon) -> None:
        running_icon.visible = True
        threading.Thread(target=reader, name="mycelian-tray-reader", daemon=True).start()
        _send(conn, {"action": "ready"})

    icon.run(setup=setup)


def _send(conn: Any, payload: dict) -> None:
    try:
        conn.send(payload)
    except (OSError, BrokenPipeError, ValueError):
        pass

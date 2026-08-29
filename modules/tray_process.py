# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""System tray icon child process.

The tray cannot live in either existing Mycelian process: the main process blocks
its main thread inside ``ui.run`` (uvicorn) and the pywebview subprocess blocks its
own main thread inside ``webview.start``. pystray's macOS backend requires the main
thread of whatever process it runs in, so the tray gets a dedicated spawn child whose
main thread does nothing but run the icon loop.

Everything in this module executes in that child. It talks to
:mod:`modules.tray_controller` over a duplex ``multiprocessing.Pipe``:

parent -> child
    ``{"cmd": "set_state", "minimized": bool}``
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

import sys
import threading
from typing import Any


APP_NAME = "Mycelian"


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

    state = {"minimized": bool(minimized)}

    def on_restore(_icon=None, _item=None) -> None:
        _send(conn, {"action": "restore"})

    def on_quit(icon, _item=None) -> None:
        _send(conn, {"action": "quit"})
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            "Restore Mycelian",
            on_restore,
            default=True,
            enabled=lambda _item: state["minimized"],
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

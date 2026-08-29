# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Launch Mycelian when the user logs in.

Each platform gets its native mechanism rather than a shared hack:

macOS
    A LaunchAgent plist at ``~/Library/LaunchAgents/com.mycelian.app.plist``.
Windows
    A value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.
Linux
    An XDG autostart desktop entry at ``~/.config/autostart/mycelian.desktop``.

All three are per-user, so none of them need elevation.

Enabling only registers the entry; it deliberately does not start a second copy of the
app right now. On macOS in particular, loading the agent with ``RunAtLoad`` set would
immediately launch a duplicate instance.

Only meaningful for an installed build. Running from source there is no stable
executable to point at, so :func:`is_supported` returns False and the settings toggle
is disabled.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

APP_NAME = "Mycelian"
BUNDLE_ID = "com.mycelian.app"

_WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_supported() -> bool:
    """Autostart can only be registered for a real installed executable."""
    if not is_frozen():
        return False
    return sys.platform in ("darwin", "win32") or sys.platform.startswith("linux")


def get_executable_path() -> Optional[str]:
    """The binary a login item should launch."""
    if not is_frozen():
        return None
    return os.path.abspath(sys.executable)


# ----- macOS -----


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _macos_is_enabled() -> bool:
    return _launch_agent_path().exists()


def _macos_set_enabled(enabled: bool, executable: str) -> bool:
    import plistlib

    path = _launch_agent_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": BUNDLE_ID,
        "ProgramArguments": [executable],
        "RunAtLoad": True,
        # Login item only: never resurrect the app after the user quits it.
        "KeepAlive": False,
        "ProcessType": "Interactive",
    }
    with open(path, "wb") as handle:
        plistlib.dump(payload, handle)
    return True


# ----- Windows -----


def _windows_is_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _windows_set_enabled(enabled: bool, executable: str) -> bool:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{executable}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    return True


# ----- Linux -----


def _desktop_entry_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "autostart" / "mycelian.desktop"


def _linux_is_enabled() -> bool:
    return _desktop_entry_path().exists()


def _linux_set_enabled(enabled: bool, executable: str) -> bool:
    path = _desktop_entry_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Custom alert and browser source tool for streamers\n"
        f'Exec="{executable}"\n'
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    path.write_text(entry, encoding="utf-8")
    return True


# ----- public API -----


def is_enabled() -> bool:
    """Whether a login item is currently registered."""
    if not is_supported():
        return False
    try:
        if sys.platform == "darwin":
            return _macos_is_enabled()
        if sys.platform == "win32":
            return _windows_is_enabled()
        if sys.platform.startswith("linux"):
            return _linux_is_enabled()
    except Exception as e:
        logger.warning("Autostart: could not read the login item: %s", e)
    return False


def set_enabled(enabled: bool) -> bool:
    """Register or remove the login item. Returns True when the state now matches.

    Raises the underlying OS error if the change was attempted and failed, so the
    caller can surface it; returns False when autostart is simply not supported.
    """
    if not is_supported():
        if enabled:
            logger.info("Autostart: not supported for this build; ignoring request")
        return False

    executable = get_executable_path()
    if not executable:
        return False

    if sys.platform == "darwin":
        result = _macos_set_enabled(enabled, executable)
    elif sys.platform == "win32":
        result = _windows_set_enabled(enabled, executable)
    elif sys.platform.startswith("linux"):
        result = _linux_set_enabled(enabled, executable)
    else:
        return False

    logger.info(
        "Autostart: login item %s (%s)",
        "registered" if enabled else "removed",
        executable,
    )
    return result


def sync_with_settings() -> None:
    """Make the OS login item agree with the saved setting.

    Run at startup so a login item that was removed behind Mycelian's back (a reinstall,
    a moved application folder) is put back, and a stale one is cleaned up.
    """
    if not is_supported():
        return
    try:
        from .dataobjects import state_manager

        wanted = bool(getattr(state_manager.get_app_settings(), "run_at_startup", False))
    except Exception:
        return

    try:
        if wanted != is_enabled():
            set_enabled(wanted)
        elif wanted:
            # Rewrite so the recorded path follows the application if it moved.
            set_enabled(True)
    except Exception as e:
        logger.warning("Autostart: could not sync the login item: %s", e)

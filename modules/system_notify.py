# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""OS-level desktop notifications.

:mod:`modules.notification_engine` delivers in-app Quasar toasts, which need a live
NiceGUI client. When Mycelian is sitting in the tray there is no client, so anything
worth interrupting the user for has to go through the desktop notification centre
instead.

Backends, in the order each platform prefers them:

macOS
    ``osascript`` (``display notification``). pystray's macOS notification support is
    built on NSUserNotification, which Apple removed, so the tray is not used here.
Windows
    The tray icon's balloon, falling back to a PowerShell toast when the tray is not
    running.
Linux
    ``notify-send``, falling back to the tray icon.

Every path honours the existing "Notifications" setting and never raises.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

APP_NAME = "Mycelian"

# Notifications are best-effort UX; never let a wedged helper hold a thread forever.
_SUBPROCESS_TIMEOUT_SEC = 10


def _notifications_enabled() -> bool:
    try:
        from .dataobjects import state_manager

        settings = state_manager.get_app_settings()
        if settings is None:
            return True
        return bool(getattr(settings, "notifications_enabled", True))
    except Exception:
        return True


def _run(command: list[str]) -> bool:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            check=False,
            **_no_window_kwargs(),
        )
        if completed.returncode != 0:
            logger.debug(
                "system_notify: %s exited %s: %s",
                command[0],
                completed.returncode,
                completed.stderr[:200] if completed.stderr else b"",
            )
            return False
        return True
    except Exception as e:
        logger.debug("system_notify: %s failed: %s", command[0], e)
        return False


def _no_window_kwargs() -> dict:
    """Keep helper processes from flashing a console window on Windows."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _notify_macos(title: str, message: str) -> bool:
    script = (
        f'display notification "{_escape_applescript(message)}" '
        f'with title "{_escape_applescript(title)}"'
    )
    return _run(["osascript", "-e", script])


def _notify_windows(title: str, message: str) -> bool:
    from .tray_controller import notify_via_tray

    if notify_via_tray(title, message):
        return True

    # No tray icon running: fall back to a WinRT toast driven from PowerShell.
    script = _WINDOWS_TOAST_SCRIPT.format(
        app_id=_powershell_quote(APP_NAME),
        title=_powershell_quote(title),
        message=_powershell_quote(message),
    )
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return False
    return _run([powershell, "-NoProfile", "-NonInteractive", "-Command", script])


def _powershell_quote(text: str) -> str:
    return text.replace("'", "''")


_WINDOWS_TOAST_SCRIPT = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode('{title}')) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode('{message}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def _notify_linux(title: str, message: str) -> bool:
    if shutil.which("notify-send"):
        if _run(["notify-send", "-a", APP_NAME, title, message]):
            return True

    from .tray_controller import notify_via_tray

    return notify_via_tray(title, message)


def _dispatch(title: str, message: str) -> bool:
    if sys.platform == "darwin":
        return _notify_macos(title, message)
    if sys.platform == "win32":
        return _notify_windows(title, message)
    if sys.platform.startswith("linux"):
        return _notify_linux(title, message)
    logger.debug("system_notify: no backend for platform %s", sys.platform)
    return False


def notify(message: str, *, title: str = APP_NAME, force: bool = False) -> bool:
    """Show a desktop notification. Never raises.

    Args:
        message: Body text.
        title: Notification title.
        force: Bypass the "Notifications" setting. Reserved for messages the user must
            not miss, such as a failed update install.

    Returns:
        True if a backend accepted the notification.
    """
    if not force and not _notifications_enabled():
        return False

    try:
        return _dispatch(title, message)
    except Exception as e:
        logger.debug("system_notify: delivery failed: %s", e)
        return False


def notify_async(message: str, *, title: str = APP_NAME, force: bool = False) -> None:
    """Fire and forget, for callers that must not block on a helper process."""
    threading.Thread(
        target=notify,
        args=(message,),
        kwargs={"title": title, "force": force},
        name="mycelian-system-notify",
        daemon=True,
    ).start()

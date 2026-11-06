#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Single-instance guard: focus the running copy, or replace a hung one.

Without a guard, relaunching while an earlier copy is still alive (for example
one whose window died while its UI loop was stalled) starts a second copy. The
two then fight over the overlay port and the hung one can kill the healthy one,
so users had to end Mycelian in Task Manager before it would start properly.

How it works:

* The running copy holds an OS lock (a named mutex on Windows, ``flock`` on a
  lock file elsewhere). The OS drops it when the process dies, however it dies.
* It also writes ``data/instance.json`` (pid, a localhost port and a random
  token) and answers ``focus`` requests on that port. The answer is sent only
  after the NiceGUI event loop has run a callback, so a reply proves the UI is
  alive, not just that some thread in the process is.
* A second launch that finds the lock held asks the running copy to focus. If
  it answers, the second launch exits. If it doesn't answer in time, the
  running copy is treated as hung: it is ended (with its child processes) and
  this launch takes over.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\Mycelian.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_INFO_FILENAME = "instance.json"
_LOCK_FILENAME = "instance.lock"

# How long a second launch keeps asking before it judges the running copy hung.
# Generous on purpose: a copy that is merely busy (slow startup, heavy tab build)
# must not be killed. The copies that needed Task Manager were stuck for minutes.
HUNG_CONFIRM_SEC = 25.0
# How long the running copy gets, per request, to prove its UI loop is alive.
FOCUS_TIMEOUT_SEC = 6.0
# A copy whose UI loop isn't running yet is still starting, not hung, for this long.
STARTUP_GRACE_SEC = 120.0
# After ending a hung copy, how long to wait for the OS to release its lock.
TAKEOVER_WAIT_SEC = 15.0

_lock_handle: Any = None
_server_socket: Optional[socket.socket] = None
_token: str = ""
_focus_handler: Optional[Callable[[], None]] = None
_lock_acquired_at = 0.0


# ----- paths -----------------------------------------------------------------


def _data_dir() -> Path:
    from .path_utils import get_data_path

    path = Path(get_data_path("data"))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _info_path() -> Path:
    return _data_dir() / _INFO_FILENAME


def read_instance_info() -> Optional[dict]:
    try:
        data = json.loads(_info_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_instance_info(port: int) -> None:
    info = {
        "pid": os.getpid(),
        "port": port,
        "token": _token,
        "started": time.time(),
        "exe": sys.executable,
    }
    try:
        tmp = _info_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, _info_path())
    except OSError as e:
        logger.warning("Single instance: could not write %s: %s", _info_path(), e)


def _remove_instance_info() -> None:
    info = read_instance_info()
    if info and info.get("pid") == os.getpid():
        try:
            _info_path().unlink()
        except OSError:
            pass


# ----- OS lock -----------------------------------------------------------------


def _try_acquire_lock() -> bool:
    """Take the single-instance lock without blocking. True when we now hold it."""
    global _lock_handle
    if _lock_handle is not None:
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        # CreateMutexW only sets ERROR_ALREADY_EXISTS; clear any stale value first.
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            logger.warning(
                "Single instance: CreateMutexW failed (%s); not enforcing",
                ctypes.get_last_error(),
            )
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _lock_handle = handle
        _mark_acquired()
        return True

    import fcntl

    lock_path = _data_dir() / _LOCK_FILENAME
    try:
        fh = open(lock_path, "a+")
    except OSError as e:
        logger.warning("Single instance: cannot open %s (%s); not enforcing", lock_path, e)
        return True
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _lock_handle = fh
    _mark_acquired()
    return True


def _mark_acquired() -> None:
    global _lock_acquired_at
    _lock_acquired_at = time.monotonic()


def release() -> None:
    """Drop the lock and stop answering focus requests (before an app restart)."""
    global _lock_handle, _server_socket
    _remove_instance_info()
    sock = _server_socket
    _server_socket = None
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    handle = _lock_handle
    _lock_handle = None
    if handle is None:
        return
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(handle))
        else:
            handle.close()
    except Exception:
        pass


# ----- focus server (running copy) ----------------------------------------------


def _ui_state(timeout: float) -> str:
    """"ok" once the NiceGUI loop runs a callback; "starting" before it exists; else "busy"."""
    loop = None
    try:
        from nicegui import core

        loop = getattr(core, "loop", None)
    except Exception:
        pass
    if loop is None or not loop.is_running():
        starting = time.monotonic() - _lock_acquired_at < STARTUP_GRACE_SEC
        return "starting" if starting else "busy"
    ran = threading.Event()
    try:
        loop.call_soon_threadsafe(ran.set)
    except RuntimeError:
        return "busy"
    return "ok" if ran.wait(timeout) else "busy"


def _window_process_alive() -> bool:
    """False only when the native webview process is known to be gone."""
    try:
        from .shutdown import _find_webview_process

        proc = _find_webview_process()
    except Exception:
        return True
    if proc is None:
        # Not spawned yet (or not native mode): don't claim it's gone.
        try:
            from . import shutdown

            return not getattr(shutdown, "_webview_process_seen", False)
        except Exception:
            return True
    return proc.is_alive()


def _default_focus_handler() -> None:
    try:
        from . import tray_controller

        if tray_controller.restore_from_tray(reason="second_instance"):
            return
    except Exception as e:
        logger.debug("Single instance: tray restore failed: %s", e)
    try:
        from nicegui import app

        window = getattr(getattr(app, "native", None), "main_window", None)
        if window is not None:
            window.show()
            window.restore()
    except Exception as e:
        logger.debug("Single instance: window show failed: %s", e)


def _handle_client(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(FOCUS_TIMEOUT_SEC)
        try:
            raw = conn.recv(1024).decode("utf-8", errors="replace").strip()
            request = json.loads(raw) if raw else {}
        except (OSError, ValueError):
            return
        if not isinstance(request, dict) or request.get("token") != _token:
            return
        if request.get("cmd") != "focus":
            return
        state = _ui_state(FOCUS_TIMEOUT_SEC - 1.0)
        if state == "ok" and not _window_process_alive():
            # UI loop runs but the window is gone: nothing to show. The window
            # watchdog (shutdown.py) is about to exit this copy; let the new
            # launch wait for that rather than exit with no window at all.
            state = "nowindow"
        if state == "ok":
            logger.warning("Single instance: another launch asked this copy to show its window")
            handler = _focus_handler or _default_focus_handler
            # Window calls go through pywebview's queue; keep them off this socket.
            threading.Thread(target=handler, name="SingleInstanceFocus", daemon=True).start()
        else:
            logger.warning("Single instance: another launch asked to focus (state=%s)", state)
        try:
            conn.sendall((state + "\n").encode())
        except OSError:
            pass


def _serve(sock: socket.socket) -> None:
    while True:
        try:
            conn, _addr = sock.accept()
        except OSError:
            return
        threading.Thread(
            target=_handle_client, args=(conn,), name="SingleInstanceClient", daemon=True
        ).start()


def _start_focus_server() -> None:
    global _server_socket, _token
    _token = secrets.token_hex(16)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(4)
    except OSError as e:
        logger.warning("Single instance: focus listener unavailable: %s", e)
        _write_instance_info(0)
        return
    _server_socket = sock
    _write_instance_info(sock.getsockname()[1])
    threading.Thread(target=_serve, args=(sock,), name="SingleInstanceServer", daemon=True).start()


def set_focus_handler(handler: Optional[Callable[[], None]]) -> None:
    global _focus_handler
    _focus_handler = handler


# ----- second launch -------------------------------------------------------------


def request_focus(info: dict, timeout: float = FOCUS_TIMEOUT_SEC) -> Optional[str]:
    """Ask the running copy to show its window.

    Returns its answer: "ok" (UI alive, window shown), "starting" (UI not up
    yet), "busy" (UI loop did not respond), "timeout" (no answer at all), or
    None when there is no listener to ask (no port recorded, connection refused).
    """
    port = int(info.get("port") or 0)
    if port <= 0:
        return None
    _allow_foreground(info.get("pid"))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            conn.settimeout(timeout)
            conn.sendall((json.dumps({"cmd": "focus", "token": info.get("token", "")}) + "\n").encode())
            reply = conn.recv(64).decode("utf-8", errors="replace").strip()
    except ConnectionRefusedError:
        return None
    except OSError:
        return "timeout"
    return reply or "timeout"


def _allow_foreground(pid: Any) -> None:
    """Let the running copy bring its window to the front (Windows foreground lock)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(int(pid) if pid else -1)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        from .shutdown import _pid_is_running

        return _pid_is_running(pid)
    except Exception:
        return False


def _pid_is_mycelian(pid: int) -> bool:
    try:
        from .web_engine import _cmdline_looks_like_mycelian, _process_cmdline

        return _cmdline_looks_like_mycelian(_process_cmdline(pid))
    except Exception:
        return False


def _terminate(pid: int) -> None:
    logger.warning("Single instance: ending unresponsive Mycelian process %s", pid)
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            import signal

            os.kill(pid, signal.SIGKILL)
    except Exception as e:
        logger.warning("Single instance: could not end process %s: %s", pid, e)


def _acquire_with_retry(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if _try_acquire_lock():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def ensure_single_instance() -> bool:
    """Call once at startup. True: continue starting. False: exit (focused the running copy).

    Never raises; any unexpected failure lets this launch start normally.
    """
    try:
        return _ensure_single_instance()
    except Exception as e:
        logger.warning("Single instance check failed; starting anyway: %s", e, exc_info=True)
        return True


def _ensure_single_instance() -> bool:
    if _try_acquire_lock():
        _start_focus_server()
        return True

    logger.warning("Single instance: another Mycelian is running; asking it to show its window")
    deadline = time.monotonic() + HUNG_CONFIRM_SEC
    while True:
        info = read_instance_info() or {}
        answer = request_focus(info) if info else None
        if answer in ("ok", "starting"):
            logger.warning(
                "Single instance: the running copy (pid=%s) answered %r; exiting",
                info.get("pid"),
                answer,
            )
            return False
        # It may have just exited (e.g. an update restart) or not opened its
        # listener yet; "busy"/"timeout" may be a slow moment. Keep asking.
        if _try_acquire_lock():
            _start_focus_server()
            return True
        if time.monotonic() >= deadline:
            logger.warning(
                "Single instance: the running copy (pid=%s) did not respond for %.0fs "
                "(last answer: %s)",
                info.get("pid"),
                HUNG_CONFIRM_SEC,
                answer,
            )
            break
        time.sleep(1.0)

    info = read_instance_info() or {}
    pid = int(info.get("pid") or 0)
    if pid and pid != os.getpid() and _pid_alive(pid) and _pid_is_mycelian(pid):
        logger.warning(
            "Single instance: the running copy (pid=%s) is not responding; replacing it",
            pid,
        )
        _terminate(pid)
    else:
        logger.warning(
            "Single instance: lock held but the holder (pid=%s) could not be identified",
            pid or "unknown",
        )

    if _acquire_with_retry(TAKEOVER_WAIT_SEC):
        _start_focus_server()
        return True
    # Could not take over (unknown holder). Starting a second copy is what caused
    # the original port fight, but refusing to start leaves the user with nothing;
    # start without the lock and let the overlay port logic sort it out.
    logger.error("Single instance: could not take over the lock; starting without it")
    return True

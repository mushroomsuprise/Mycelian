#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SEC = 600
NPSSO_HELPER_FLAG = "--mycelian-npsso-capture"

# NPSSO is ~64 chars, alphanumeric / base64url-style (not hex-only).
_NPSSO_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{64}$")

_PYINSTALLER_BOOTSTRAP_VARS = (
    "_MEIPASS2",
    "PYTHONHOME",
    "PYTHONPATH",
    "_PYI_BOOTSTRAP",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _npsso_capture_command_and_env() -> tuple[list[str], dict[str, str], Path]:
    """Build subprocess argv and env for the isolated NPSSO webview helper."""
    root = _project_root()
    env = os.environ.copy()

    if _is_frozen():
        cmd = [sys.executable, NPSSO_HELPER_FLAG]
        for var_name in _PYINSTALLER_BOOTSTRAP_VARS:
            env.pop(var_name, None)
    else:
        cmd = [sys.executable, "-m", "modules.npsso_webview_capture"]
        extra = str(root)
        sep = os.pathsep
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = f"{extra}{sep}{env['PYTHONPATH']}"
        else:
            env["PYTHONPATH"] = extra

    return cmd, env, root


def _run_npsso_capture_subprocess() -> tuple[bool, str, str]:
    """
    Run the isolated pywebview helper. Returns (ok, token_or_empty, error_message).
    """
    logger.debug(
        f"[NPSSO_TRACE] t={time.monotonic():.3f} "
        f"thread={threading.current_thread().name!r} "
        "npsso_authenticator: subprocess.run starting webview module"
    )
    cmd, env, root = _npsso_capture_command_and_env()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        logger.error("NPSSO webview subprocess timed out")
        return (
            False,
            "",
            "Timed out waiting for the sign-in window. Close it and try again.",
        )
    except OSError as e:
        logger.error("NPSSO webview subprocess failed to start: %s", e)
        return False, "", f"Could not start sign-in window: {e}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    logger.debug(
        f"[NPSSO_TRACE] t={time.monotonic():.3f} "
        f"thread={threading.current_thread().name!r} "
        f"npsso_authenticator: subprocess exited code={proc.returncode} "
        f"stdout_len={len(out)} stderr_len={len(err)}"
    )

    if proc.returncode == 0 and out:
        if _NPSSO_TOKEN_RE.fullmatch(out):
            logger.info("NPSSO token acquired via webview subprocess")
            return True, out, ""
        logger.error("NPSSO subprocess returned invalid token shape")
        return False, "", "Invalid token from sign-in window. Try again or paste manually."

    if err:
        return False, "", err

    logger.error(
        "NPSSO subprocess failed: code=%s stdout=%r stderr=%r",
        proc.returncode,
        out[:80],
        err[:200],
    )
    return (
        False,
        "",
        "NPSSO capture failed. Use Help for manual steps, or try again.",
    )


@dataclass
class NpssoResult:
    """Result of NPSSO acquisition attempt"""

    success: bool
    npsso_code: str = ""
    error_message: str = ""


def show_npsso_instruction_dialog(
    on_after_continue: Callable[[], None],
    on_cancel: Optional[Callable[[], None]] = None,
) -> None:
    """
    Show the countdown + instructions dialog. When the user clicks Continue,
    the dialog closes and ``on_after_continue`` runs.

    Callers should start subprocess capture and poll completion using a
    ``ui.timer`` anchored to a **persistent** tab element (not this dialog),
    so NiceGUI can flush UI updates to the client.
    """
    from nicegui import ui

    from .ui_timer import layout_schedule

    def on_cancel_click() -> None:
        dialog.close()
        if on_cancel is not None:
            on_cancel()

    def on_continue() -> None:
        logger.debug(
            f"[NPSSO_TRACE] t={time.monotonic():.3f} "
            f"thread={threading.current_thread().name!r} "
            "npsso_instruction_dialog: Continue clicked, closing dialog"
        )
        dialog.close()
        on_after_continue()
        logger.debug(
            f"[NPSSO_TRACE] t={time.monotonic():.3f} "
            f"thread={threading.current_thread().name!r} "
            "npsso_instruction_dialog: on_after_continue() returned"
        )

    def enable_continue() -> None:
        continue_btn.enable()
        continue_btn.text = "Continue"

    with ui.dialog() as dialog:
        with ui.card().classes("w-[600px] p-6"):
            ui.label("Connect PlayStation Network").classes("text-2xl font-bold mb-4")

            with ui.column().classes("gap-4"):
                ui.label(
                    "To connect your PlayStation Network account, follow these steps:"
                ).classes("text-lg mb-2")

                with ui.row().classes("gap-3 items-start"):
                    ui.badge("1", color="primary").classes("rounded-full mt-1")
                    with ui.column().classes("gap-1 grow"):
                        ui.label(
                            "Click Continue to open the dedicated PlayStation sign-in window"
                        ).classes("font-medium")
                        ui.label(
                            "A separate window will open for PlayStation Network sign-in."
                        ).classes("text-sm secondary-text")

                with ui.row().classes("gap-3 items-start"):
                    ui.badge("2", color="primary").classes("rounded-full mt-1")
                    with ui.column().classes("gap-1 grow"):
                        ui.label(
                            "Sign in to PlayStation Network in that window"
                        ).classes("font-medium")
                        ui.label(
                            "Wait until you are fully signed in, then keep the window open."
                        ).classes("text-sm secondary-text")

                with ui.row().classes("gap-3 items-start"):
                    ui.badge("3", color="primary").classes("rounded-full mt-1")
                    with ui.column().classes("gap-1 grow"):
                        ui.label(
                            "Use the menu: NPSSO → I am signed in — retrieve NPSSO token"
                        ).classes("font-medium")
                        ui.label(
                            "The window closes and Mycelian saves your token and reconnects PSN."
                        ).classes("text-sm secondary-text")

                ui.separator().classes("my-4")

                ui.label(
                    "Sign-in and token retrieval happen in the same window, "
                    "so your NPSSO token is captured automatically after the menu action."
                ).classes("text-sm text-orange-600 font-medium")

                with ui.row().classes("justify-end gap-2 mt-4"):
                    ui.button("Cancel", on_click=on_cancel_click).props("flat")

                    continue_btn = ui.button(
                        "Continue (10)",
                        on_click=on_continue,
                        color="primary",
                    )
                    continue_btn.disable()

                    def countdown_timer(remaining: int) -> None:
                        if getattr(continue_btn, "is_deleted", False):
                            return
                        if remaining > 0:
                            continue_btn.text = f"Continue ({remaining})"
                            layout_schedule(
                                1.0,
                                lambda: countdown_timer(remaining - 1),
                                once=True,
                            )
                        else:
                            enable_continue()

                    layout_schedule(0.1, lambda: countdown_timer(10), once=True)

    dialog.open()

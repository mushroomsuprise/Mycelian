# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Template copy/delete progress shared with the status footer.

This module stays free of NiceGUI and the overlay web engine so the footer
can read progress during first paint without importing the source editor.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "active": False,
    "phase": "idle",
    "copied": 0,
    "total": 0,
    "label": "",
    "title": "Copying",
}


def template_copy_is_active() -> bool:
    with _lock:
        return bool(_state["active"])


def get_template_copy_progress() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def format_template_copy_badge(
    progress: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str]]:
    """Return (badge text, tier) while a template copy is running."""
    if not progress or not progress.get("active"):
        return None
    phase = str(progress.get("phase") or "")
    title = str(progress.get("title") or "Copying")
    busy = "Deleting" if title == "Deleting" else "Copying"
    tier = "error" if title == "Deleting" else "warning"
    if phase == "preparing":
        return ("Preparing", "info")
    try:
        copied = max(0, int(progress.get("copied") or 0))
        total = max(0, int(progress.get("total") or 0))
    except (TypeError, ValueError):
        return (busy, tier)
    if total <= 0:
        return (busy, tier)
    return (f"{copied} of {total}", tier)


def set_template_copy_state(**fields: Any) -> None:
    with _lock:
        _state.update(fields)
    if fields.get("active"):
        try:
            from .notification_engine import start_alert_trim_footer_poll

            start_alert_trim_footer_poll()
        except Exception:
            pass


def mark_template_copy_idle() -> None:
    set_template_copy_state(
        active=False,
        phase="idle",
        copied=0,
        total=0,
        label="",
        title="Copying",
    )

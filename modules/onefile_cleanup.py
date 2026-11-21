# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Remove Mycelian's leftover one-file unpack folders after moving to the folder build.

A one-file Windows build unpacks itself to ``%TEMP%\\_MEIxxxxxx`` on every launch and
deletes the folder on a clean exit. A crash, a kill from Task Manager or a power loss
leaves it behind (~100 MB each). Once Mycelian runs from its ``runtime`` folder those
folders are never used again, so the first launches clean them up.

Only folders that hold Mycelian's own bundled files are touched; other PyInstaller apps
use the same ``_MEI`` prefix. Folders changed within the last hour are left alone.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# Bundled only by Mycelian (build.get_data_files), so a _MEI folder holding them is ours.
MARKERS = (
    os.path.join("modules", "help_system", "content"),
    os.path.join("assets", "default_assets", "icons"),
)
MIN_AGE_SEC = 3600.0


def runs_from_folder_build() -> bool:
    """True in a frozen Windows build whose runtime files sit in ``<install>\\runtime``."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not meipass or sys.platform != "win32":
        return False
    return os.path.normcase(os.path.dirname(os.path.abspath(meipass))) == os.path.normcase(
        os.path.dirname(os.path.abspath(sys.executable))
    )


def leftover_unpack_dirs(temp_dir: Optional[str] = None, now: Optional[float] = None) -> List[str]:
    """Mycelian ``_MEI*`` folders in the temp folder that are older than MIN_AGE_SEC."""
    temp_dir = temp_dir or tempfile.gettempdir()
    now = time.time() if now is None else now
    found = []
    try:
        names = os.listdir(temp_dir)
    except OSError:
        return []
    for name in names:
        if not name.startswith("_MEI"):
            continue
        path = os.path.join(temp_dir, name)
        try:
            if not os.path.isdir(path) or now - os.path.getmtime(path) < MIN_AGE_SEC:
                continue
        except OSError:
            continue
        if all(os.path.isdir(os.path.join(path, marker)) for marker in MARKERS):
            found.append(path)
    return found


def remove_leftovers(temp_dir: Optional[str] = None, now: Optional[float] = None) -> int:
    removed = 0
    for path in leftover_unpack_dirs(temp_dir, now):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed += 1
    if removed:
        logger.info("Removed %d leftover one-file unpack folder(s) from the temp folder", removed)
    return removed


def start_background_cleanup() -> bool:
    """Clean up in a background thread when running from the folder build."""
    if not runs_from_folder_build():
        return False
    threading.Thread(target=remove_leftovers, name="OneFileCleanup", daemon=True).start()
    return True

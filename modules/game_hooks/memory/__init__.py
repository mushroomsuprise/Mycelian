# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Platform-specific process memory backends."""

from __future__ import annotations

import sys
from typing import Optional

from .base import (
    AttachedProcess,
    ProcessMemory,
    find_process_pid,
    is_process_running,
)
from .windows import WindowsProcessMemory

__all__ = [
    "AttachedProcess",
    "ProcessMemory",
    "WindowsProcessMemory",
    "find_process_pid",
    "get_memory_backend",
    "is_process_running",
]


def get_memory_backend() -> Optional[ProcessMemory]:
    """Return the memory backend for the current OS, or None if unsupported."""
    if sys.platform == "win32":
        return WindowsProcessMemory()
    return None

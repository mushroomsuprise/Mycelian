# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Build metadata helpers (git commit, stamped build number)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_git_commit(project_root: Path | None = None) -> str | None:
    """Return the full git commit SHA for HEAD, or None if unavailable."""
    root = project_root or _project_root()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        commit = result.stdout.strip()
        return commit or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def resolve_build_number(stamped: str = "dev") -> str:
    """Return build number: stamped commit from release builds, else live git SHA."""
    if stamped and stamped != "dev":
        return stamped
    return get_git_commit() or stamped or "dev"

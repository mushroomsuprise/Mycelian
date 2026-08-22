#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Process-wide startup flags that survive NiceGUI script re-execution."""

_critical_startup_done = False


def critical_startup_done() -> bool:
    """True after Phase 1–3 of main.py have completed in this process."""
    return _critical_startup_done


def mark_critical_startup_done() -> None:
    global _critical_startup_done
    _critical_startup_done = True


def should_run_blocking_ui() -> bool:
    """False when NiceGUI re-executed the script while the UI is already running.

    After ``mark_critical_startup_done()``, a 404-driven ``runpy.run_path`` of
    ``main.py`` must not call ``start_ui()`` / ``sys.exit``.
    """
    return not _critical_startup_done

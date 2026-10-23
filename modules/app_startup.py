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


_MP_HELPER_COMMAND_PREFIXES = (
    "from multiprocessing.resource_tracker import main",
    "from multiprocessing.forkserver import main",
    "import sys; from multiprocessing.forkserver import main",
)


def is_spawn_child(argv=None) -> bool:
    """True when this process is a multiprocessing child, not the app itself.

    In dev, spawn renames the child (``SpawnProcess-N``, ``mycelian-tray``) before it
    re-imports ``main.py``. A frozen build runs ``main.py`` as ``__main__`` in every
    child until ``freeze_support()`` diverts it, and the name is still
    ``MainProcess`` there, so the command line is the only reliable signal.
    """
    import multiprocessing
    import multiprocessing.spawn
    import sys

    if multiprocessing.current_process().name != "MainProcess":
        return True
    argv = sys.argv if argv is None else argv
    if multiprocessing.spawn.is_forking(argv):
        return True
    # Frozen resource tracker / forkserver: ``<exe> <flags> -c "from multiprocessing…"``.
    if "-c" in argv:
        index = argv.index("-c")
        if index + 1 < len(argv) and str(argv[index + 1]).startswith(
            _MP_HELPER_COMMAND_PREFIXES
        ):
            return True
    return False

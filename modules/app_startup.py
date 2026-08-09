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

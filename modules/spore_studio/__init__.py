#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Mycelian Spore Studio package.

Server-side support for the visual HTML template editor exposed in the
NiceGUI "Spore Studio" tab. Public surface:

- :func:`event_registry.get_event_registry` — the curated list of
  websocket events that the binding picker can attach element actions to.
- :func:`template_codegen.compile_model` — convert an editor model into
  the (HTML, JSON config) pair Mycelian's renderer already understands.
- :func:`template_parser_back.parse_existing` — round-trip a previously
  saved template back into the editor model.
- :func:`save_pipeline.save_template` — orchestrates codegen, JSON merge,
  atomic write, and dynamic Flask route refresh.
- :func:`save_pipeline.create_template` — handles the "Create" button by
  stamping the queue/instant boilerplate (or a copy-from clone).
- :func:`assets_watcher.ensure_background_poller` — schedules the mtime poller
  (from the first Socket.IO connect) so ``spore_studio_assets_changed`` is
  emitted when files change under ``assets/{template_name}/``.
"""

import logging as _logging

from . import (
    assets_watcher,
    behavior_blocks,
    control_action_registry,
    data_source_registry,
    event_registry,
    save_pipeline,
    spore_data_codegen,
    template_codegen,
    template_parser_back,
)

_sidecars_migrated = False


def ensure_sidecar_migration() -> None:
    """Move leftover ``.spore.json`` files out of template_configs (once)."""
    global _sidecars_migrated
    if _sidecars_migrated:
        return
    _sidecars_migrated = True
    try:
        _migrated = template_parser_back.migrate_all_sidecars()
        if _migrated:
            _logging.getLogger(__name__).info(
                "Spore Studio: migrated %d sidecar(s) into templates/_spore/",
                _migrated,
            )
    except Exception:
        _logging.getLogger(__name__).exception(
            "Spore Studio sidecar migration failed (non-fatal)"
        )


__all__ = [
    "assets_watcher",
    "behavior_blocks",
    "control_action_registry",
    "data_source_registry",
    "ensure_sidecar_migration",
    "event_registry",
    "save_pipeline",
    "spore_data_codegen",
    "template_codegen",
    "template_parser_back",
]

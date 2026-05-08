#!/usr/bin/env python3
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
- :class:`assets_watcher.AssetsWatcher` — mtime-poll watcher that emits
  ``spore_studio_assets_changed`` over Socket.IO when files are added,
  removed, or modified inside ``assets/{template_name}/``.
"""

import logging as _logging

from . import (
    assets_watcher,
    behavior_blocks,
    event_registry,
    save_pipeline,
    template_codegen,
    template_parser_back,
)

# One-time migration: relocate any ``.spore.json`` sidecars still living
# inside ``templates/template_configs/`` (the original layout) to the new
# hidden ``templates/_spore/`` folder so they stop polluting the Source
# Settings dropdown. Safe to run on every import — idempotent and a no-op
# once the on-disk tree is already migrated.
try:
    _migrated = template_parser_back.migrate_all_sidecars()
    if _migrated:
        _logging.getLogger(__name__).info(
            "Spore Studio: migrated %d sidecar(s) into templates/_spore/",
            _migrated,
        )
except Exception:  # pragma: no cover - best-effort startup migration
    _logging.getLogger(__name__).exception(
        "Spore Studio sidecar migration failed (non-fatal)"
    )

__all__ = [
    "assets_watcher",
    "behavior_blocks",
    "event_registry",
    "save_pipeline",
    "template_codegen",
    "template_parser_back",
]

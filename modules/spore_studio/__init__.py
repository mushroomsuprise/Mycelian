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

from . import (
    assets_watcher,
    behavior_blocks,
    event_registry,
    save_pipeline,
    template_codegen,
    template_parser_back,
)

__all__ = [
    "assets_watcher",
    "behavior_blocks",
    "event_registry",
    "save_pipeline",
    "template_codegen",
    "template_parser_back",
]

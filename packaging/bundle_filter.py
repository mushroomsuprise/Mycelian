#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Drop bundle data files that nothing reads at runtime (loaded by the build spec).

Windows and Linux builds are onefile, so every data file is extracted to a temp
folder on every launch. Each rule below names files that were checked to be
unread by Mycelian and by the library code that owns them:

- ``nicegui/**/*.js.map``: source maps. NiceGUI serves them only when a browser
  asks for one (``/libraries/*.map`` and the ``/esm/`` route), which happens only
  with DevTools open; a missing map is a 404 the page never sees.
- ``pycountry/locales/**``: gettext catalogs. pycountry only builds the
  ``LOCALES_DIR`` path string; psnawp (its only user) calls
  ``countries.get(alpha_2=...)``, which reads ``pycountry/databases/*.json``.
- ``nicegui/**/__pycache__/*.pyc``: every NiceGUI module is in the PYZ, which the
  frozen importer uses; these caches are never consulted.
- ``nicegui/elements/<pkg>/{package.json,package-lock.json,rollup.config.mjs}``
  and ``nicegui/elements/<pkg>/src/**``: npm build inputs for the ``dist/``
  bundles. NiceGUI registers and serves ``dist/`` only.

Everything else is kept, including NiceGUI's ``.py`` copies (they give logged
tracebacks their source lines). This directory must not contain ``__init__.py``
(see payload.py).
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence

# Extension modules left out of the bundle (passed to Analysis(excludes=...)).
# PIL/_avif is the AVIF codec (~8 MB on Windows, plus libavif elsewhere). Mycelian
# opens only PNG files with Pillow (tray icon, About logo, Factorio icons) and
# writes PNG; without the codec Pillow still imports AvifImagePlugin and only
# AVIF images become unsupported.
UNUSED_EXTENSION_MODULES = ("PIL._avif",)

_RULES = (
    ("source-map", re.compile(r"^nicegui/.+\.js\.map$")),
    ("pycountry-locales", re.compile(r"^pycountry/locales/.+\.mo$")),
    ("nicegui-pycache", re.compile(r"^nicegui/(?:.+/)?__pycache__/[^/]+\.pyc$")),
    (
        "nicegui-npm-build-input",
        re.compile(
            r"^nicegui/elements/[^/]+/"
            r"(?:package\.json|package-lock\.json|rollup\.config\.mjs|src/.+)$"
        ),
    ),
)

# Files the running app reads; the build fails if a rule ever matches one of these
# or if they go missing from the filtered bundle.
REQUIRED_DATA = (
    "nicegui/static/vue.esm-browser.prod.js",
    "nicegui/static/quasar.umd.prod.js",
    "nicegui/static/socket.io.min.js",
    "nicegui/static/nicegui.js",
    "nicegui/static/dompurify.mjs",
    "nicegui/templates/index.html",
    "pycountry/databases/iso3166-1.json",
)
REQUIRED_DATA_PREFIXES = (
    "nicegui/elements/echart/dist/",
    "nicegui/elements/markdown.js",
)


def normalize_dest(dest: str) -> str:
    return str(dest).replace("\\", "/")


def unused_data_rule(dest: str) -> Optional[str]:
    """Name of the rule that drops this bundle destination, or None to keep it."""
    d = normalize_dest(dest)
    for name, pattern in _RULES:
        if pattern.match(d):
            return name
    return None


def filter_unused_data(datas: Iterable[Sequence]) -> tuple[list, dict]:
    """Split PyInstaller datas TOC entries; returns (kept, {rule: [count, bytes]})."""
    import os

    kept = []
    removed: dict = {}
    for entry in datas:
        rule = unused_data_rule(entry[0])
        if rule is None:
            kept.append(entry)
            continue
        stats = removed.setdefault(rule, [0, 0])
        stats[0] += 1
        try:
            stats[1] += os.path.getsize(entry[1])
        except (OSError, IndexError, TypeError):
            pass
    verify_filtered(kept)
    return kept, removed


def verify_filtered(kept: Iterable[Sequence]) -> None:
    """Raise if a file the app needs is missing after filtering."""
    dests = {normalize_dest(entry[0]) for entry in kept}
    missing = [d for d in REQUIRED_DATA if d not in dests]
    missing += [
        p for p in REQUIRED_DATA_PREFIXES if not any(d.startswith(p) for d in dests)
    ]
    if missing:
        raise RuntimeError(f"bundle filter: required data missing: {missing}")
    wrongly = [d for d in REQUIRED_DATA if unused_data_rule(d)]
    if wrongly:
        raise RuntimeError(f"bundle filter: rule matches required data: {wrongly}")

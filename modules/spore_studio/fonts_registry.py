#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""List fonts shipped under assets/default_assets/fonts for Spore Studio."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..path_utils import get_assets_path

FONT_EXTS = frozenset({".ttf", ".otf", ".woff", ".woff2"})


def list_default_font_files() -> List[str]:
    """Return sorted font filenames in assets/default_assets/fonts."""
    fonts_dir = os.path.join(get_assets_path(), "default_assets", "fonts")
    if not os.path.isdir(fonts_dir):
        return []
    names: List[str] = []
    try:
        for entry in os.listdir(fonts_dir):
            _, ext = os.path.splitext(entry)
            if ext.lower() in FONT_EXTS:
                names.append(entry)
    except OSError:
        return []
    names.sort(key=str.lower)
    return names


def is_font_filename(value: str) -> bool:
    """True when ``value`` looks like a font file basename we can load."""
    if not value or not isinstance(value, str):
        return False
    base = value.strip()
    if "/" in base or "\\" in base:
        base = os.path.basename(base)
    _, ext = os.path.splitext(base)
    return ext.lower() in FONT_EXTS


def resolve_font_filename(value: str) -> Optional[str]:
    """
    Match a picker label, family name, or filename to a file in default assets.

    Examples: ``Renogare`` → ``Renogare.ttf``, ``Renogare.ttf`` → ``Renogare.ttf``.
    """
    v = (value or "").strip()
    if not v:
        return None
    base = os.path.basename(v)
    files = list_default_font_files()
    if not files:
        return None
    by_lower = {name.lower(): name for name in files}
    if is_font_filename(base):
        exact = by_lower.get(base.lower())
        if exact:
            return exact
    stem = os.path.splitext(base)[0].lower()
    if not stem:
        return None
    matches = [
        by_lower[key]
        for key in by_lower
        if os.path.splitext(key)[0].lower() == stem
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    for name in matches:
        if name.lower().endswith(".ttf"):
            return name
    return matches[0]


def exposed_element_font_family(element_id: str) -> str:
    """CSS family for an element whose font file is driven by Source Settings."""
    slug = "".join(
        c if c.isalnum() or c in "_-" else "_"
        for c in str(element_id or "el")
    ).strip("_") or "el"
    return f"spore-el-{slug}"


def font_css_family_name(filename: str) -> str:
    """Stable CSS ``font-family`` identifier for a default-assets font file."""
    slug = filename.replace(".", "_")
    slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in slug)
    return f"spore-font-{slug.strip('_') or 'custom'}"


def get_font_registry() -> Dict[str, Any]:
    """Payload for ``GET /api/spore-studio/fonts``."""
    fonts = []
    for name in list_default_font_files():
        fonts.append(
            {
                "filename": name,
                "url": f"/assets/default_assets/fonts/{name}",
                "css_family": font_css_family_name(name),
                "label": os.path.splitext(name)[0],
            }
        )
    return {"fonts": fonts}

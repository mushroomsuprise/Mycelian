#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Match OBS browser sources to Mycelian overlay template routes (pure helpers)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

_LOCAL_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        "0.0.0.0",
    }
)


def normalize_overlay_path(path: str) -> str:
    """Strip trailing slash / ``.html``; ensure leading slash; empty → ``/``."""
    p = (path or "").strip() or "/"
    if not p.startswith("/"):
        p = "/" + p
    while len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    lower = p.lower()
    if lower.endswith(".html"):
        p = p[: -len(".html")]
    elif lower.endswith(".htm"):
        p = p[: -len(".htm")]
    while len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p or "/"


def is_local_overlay_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    return h in _LOCAL_HOSTS


def browser_url_matches_route(
    url: Any,
    route: str,
    *,
    overlay_port: Optional[int] = None,
    require_port: bool = True,
) -> bool:
    """
    True when *url* is a browser-source URL for ``/{route}``.

    Query/hash ignored. ``.html`` / ``.htm`` suffixes are stripped. Path compare
    is case-insensitive. When *overlay_port* is set and *require_port* is True,
    the URL port must match (default HTTP 80 / HTTPS 443 when omitted).
    """
    if not route or not str(route).strip():
        return False
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        return False

    expected = normalize_overlay_path("/" + str(route).strip().strip("/"))
    actual = normalize_overlay_path(parsed.path or "/")
    if actual.lower() != expected.lower():
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    if require_port and overlay_port is not None:
        try:
            want = int(overlay_port)
        except (TypeError, ValueError):
            return False
        port = parsed.port
        if port is None:
            port = 443 if (parsed.scheme or "").lower() == "https" else 80
        if int(port) != want:
            return False

    return True


def coerce_browser_wh(
    settings: Dict[str, Any],
    defaults: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[int, int]]:
    """Merge *settings* over *defaults* and return ``(width, height)`` if usable."""
    merged: Dict[str, Any] = {}
    if isinstance(defaults, dict):
        merged.update(defaults)
    if isinstance(settings, dict):
        merged.update(settings)
    try:
        w = int(float(merged.get("width")))
        h = int(float(merged.get("height")))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def pick_browser_source_size(
    matches: List[Dict[str, Any]],
    *,
    program_source_names: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """
    Choose one match from ``[{source_name, width, height}, ...]``.

    - Same W×H across all → first
    - Else prefer a source on the current program scene
    - Else ``None`` (ambiguous)
    """
    if not matches:
        return None
    sizes = {(int(m["width"]), int(m["height"])) for m in matches}
    if len(sizes) == 1:
        return matches[0]
    if program_source_names:
        on_program = [
            m
            for m in matches
            if str(m.get("source_name") or "") in program_source_names
        ]
        if on_program:
            prog_sizes = {(int(m["width"]), int(m["height"])) for m in on_program}
            if len(prog_sizes) == 1:
                return on_program[0]
            if len(on_program) == 1:
                return on_program[0]
    return None


def is_browser_input_kind(kind: Any) -> bool:
    """True for OBS browser source input kinds (versioned or not)."""
    s = str(kind or "").strip().lower()
    if not s:
        return False
    return s == "browser_source" or s.startswith("browser_source")

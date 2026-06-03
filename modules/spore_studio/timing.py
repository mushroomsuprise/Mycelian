#!/usr/bin/env python3
"""
Compute minimum overlay duration from a Spore Studio editor model.

Used on save (template_codegen) and mirrored in the editor UI so Duration
can be auto-filled with a floor the user may raise manually.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

_DEFAULT_DURATION_SECONDS = 5.0
_MIN_FLOOR_SECONDS = 0.1
_ANIM_TYPES = frozenset(
    {"none", "fade", "slideIn", "slideOut", "scaleIn", "scaleOut"}
)
_DEFAULT_ANIM_MS = 300


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _anim_ms_for_type(anim_type: str, duration_ms: Any) -> int:
    if anim_type not in _ANIM_TYPES or anim_type == "none":
        return 0
    ms = _coerce_int(duration_ms, _DEFAULT_ANIM_MS)
    return max(0, ms)


def _element_animations_timeline_ms(element: Dict[str, Any]) -> int:
    anims = element.get("animations")
    if not isinstance(anims, dict):
        return 0
    anim_in = str(anims.get("anim_in") or "none").strip()
    anim_out = str(anims.get("anim_out") or "none").strip()
    delay = _coerce_int(anims.get("anim_delay_ms"), 0)
    in_ms = _anim_ms_for_type(anim_in, anims.get("anim_in_ms"))
    out_ms = _anim_ms_for_type(anim_out, anims.get("anim_out_ms"))
    return max(0, delay) + in_ms + out_ms


def _binding_step_ms(action: str, args: Dict[str, Any]) -> int:
    args = args or {}
    if action == "show_for":
        try:
            seconds = float(args.get("seconds", 5))
        except (TypeError, ValueError):
            seconds = 5.0
        seconds = max(0.0, seconds)
        anim_in = str(args.get("anim_in", "fade") or "none")
        anim_out = str(args.get("anim_out", "fade") or "none")
        out_ms = _anim_ms_for_type(anim_out, _DEFAULT_ANIM_MS)
        in_ms = _anim_ms_for_type(anim_in, _DEFAULT_ANIM_MS)
        return int(seconds * 1000) + in_ms + out_ms
    if action == "flash_class":
        return _coerce_int(args.get("duration_ms"), 500)
    return 0


def _binding_chain_timeline_ms(binding: Dict[str, Any]) -> int:
    if not isinstance(binding, dict):
        return 0
    total = 0
    action = binding.get("action")
    if action and isinstance(action, str):
        args = binding.get("args")
        args_d: Dict[str, Any] = dict(args) if isinstance(args, dict) else {}
        total += _binding_step_ms(action, args_d)
    for row in binding.get("chain") or []:
        if not isinstance(row, dict):
            continue
        delay = _coerce_float(row.get("delay_ms"), 0.0)
        total += max(0, int(delay))
        act = row.get("action")
        if not act or not isinstance(act, str):
            continue
        ra = row.get("args")
        args_k: Dict[str, Any] = dict(ra) if isinstance(ra, dict) else {}
        total += _binding_step_ms(act, args_k)
    return total


def _element_bindings_timeline_ms(element: Dict[str, Any]) -> int:
    best = 0
    for binding in element.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        best = max(best, _binding_chain_timeline_ms(binding))
    return best


def _element_media_fade_ms(element: Dict[str, Any]) -> int:
    etype = (element.get("type") or "").lower()
    props = element.get("props") or {}
    if etype == "audio":
        return (
            _coerce_int(props.get("fade_in_ms"), 0)
            + _coerce_int(props.get("fade_out_ms"), 0)
        )
    if etype == "video":
        return (
            _coerce_int(props.get("audio_fade_in_ms"), 0)
            + _coerce_int(props.get("audio_fade_out_ms"), 0)
        )
    return 0


def element_timeline_ms(element: Dict[str, Any]) -> int:
    """Longest single-path timeline for one element, in milliseconds."""
    if not isinstance(element, dict):
        return 0
    return max(
        _element_animations_timeline_ms(element),
        _element_bindings_timeline_ms(element),
        _element_media_fade_ms(element),
    )


def compute_min_duration_seconds(model: Dict[str, Any]) -> float:
    """
    Minimum template duration in seconds (ceil to 0.1s).

    Uses the longest element timeline; returns 5.0 when the model has no
    timing signal (empty elements or all zero timelines).
    """
    if not isinstance(model, dict):
        return _DEFAULT_DURATION_SECONDS
    elements = model.get("elements") or []
    max_ms = 0
    for element in elements:
        if isinstance(element, dict):
            max_ms = max(max_ms, element_timeline_ms(element))
    if max_ms <= 0:
        return _DEFAULT_DURATION_SECONDS
    seconds = max_ms / 1000.0
    rounded = math.ceil(seconds * 10.0) / 10.0
    return max(_MIN_FLOOR_SECONDS, rounded)


def effective_duration_seconds(model: Dict[str, Any]) -> float:
    """User duration clamped to the computed minimum."""
    if not isinstance(model, dict):
        return _DEFAULT_DURATION_SECONDS
    raw = model.get("duration_seconds")
    try:
        user = float(raw) if raw is not None and raw != "" else _DEFAULT_DURATION_SECONDS
    except (TypeError, ValueError):
        user = _DEFAULT_DURATION_SECONDS
    minimum = compute_min_duration_seconds(model)
    return max(minimum, user)

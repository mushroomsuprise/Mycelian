#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Normalize and broadcast chat-overlay banner payloads."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CHAT_BANNER_EVENT = "chat_display_banner"
CHAT_BANNER_REQUEST_EVENT = "chat_banner_request"

_MESSAGE_LIMIT = 2000
_SOURCE_LIMIT = 64
_DEFAULT_DURATION = 10.0

_ANIMATIONS = frozenset(
    {
        "none",
        "fadeIn",
        "slideIn",
        "slideInLeft",
        "slideInRight",
        "slideInTop",
        "slideInBottom",
        "bounceIn",
        "scaleIn",
        "flipInX",
        "elasticIn",
    }
)
_FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_CSS_COLOR = re.compile(
    r"^(?:transparent|[a-zA-Z]{1,32}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?(?:\s*,\s*[\d.]+%?)?\s*\))$"
)


def chat_banner_default_duration() -> float:
    """Seconds from the chat template Banner Options, or 10."""
    try:
        from .template_config_parser import TemplateConfigParser

        config = TemplateConfigParser().load_config("chat")
    except Exception as exc:
        logger.debug("Could not read chat banner default duration: %s", exc)
        return _DEFAULT_DURATION
    elements = []
    if isinstance(config, dict):
        elements = config.get("elements") or []
    if not isinstance(elements, list):
        return _DEFAULT_DURATION
    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("id") != "BannerDefaultDuration":
            continue
        try:
            return max(0.0, float(element.get("value")))
        except (TypeError, ValueError):
            return _DEFAULT_DURATION
    return _DEFAULT_DURATION


def _clean_source(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_.:-]", "", text)
    return text[:_SOURCE_LIMIT]


def _clean_color(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return ""
    if _HEX_COLOR.match(text) or _CSS_COLOR.match(text):
        return text
    return ""


def _clean_font(value: Any) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        return ""
    if not _FONT_NAME.match(text):
        return ""
    return text


def _clean_font_size(value: Any) -> Optional[float]:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return None
    if size < 8 or size > 200:
        return None
    return size


def _clean_animation(value: Any) -> str:
    name = str(value or "").strip()
    if name in _ANIMATIONS:
        return name
    return ""


def _normalize_style(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    style: Dict[str, Any] = {}
    background = _clean_color(raw.get("background"))
    if background:
        style["background"] = background
    color = _clean_color(raw.get("color"))
    if color:
        style["color"] = color
    font = _clean_font(raw.get("font"))
    if font:
        style["font"] = font
    font_size = _clean_font_size(raw.get("font_size"))
    if font_size is not None:
        style["font_size"] = font_size
    animation = _clean_animation(raw.get("animation"))
    if animation:
        style["animation"] = animation
    if "animation_duration" in raw and raw.get("animation_duration") not in (None, ""):
        try:
            anim_dur = float(raw.get("animation_duration"))
        except (TypeError, ValueError):
            anim_dur = 0
        if 0.05 <= anim_dur <= 5:
            style["animation_duration"] = anim_dur
    return style


def _normalize_fragments(raw: Any) -> Optional[list]:
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for item in raw[:80]:
        if isinstance(item, dict):
            out.append(item)
    return out or None


def normalize_chat_banner_payload(
    data: Any, *, default_duration: float = _DEFAULT_DURATION
) -> Dict[str, Any]:
    """Return a display-ready banner payload.

    ``duration`` of 0, or ``permanent`` true, stays up until the next banner
    or an explicit clear. Omitted duration uses ``default_duration``.
    """
    raw = data if isinstance(data, dict) else {}
    source = _clean_source(raw.get("source"))
    action = str(raw.get("action") or "show").strip().lower()
    if action == "clear":
        payload: Dict[str, Any] = {"action": "clear"}
        if source:
            payload["source"] = source
        return payload

    message = str(raw.get("message") if raw.get("message") is not None else raw.get("text") or "")
    message = message.strip()[:_MESSAGE_LIMIT]
    fragments = _normalize_fragments(raw.get("fragments"))

    permanent = bool(raw.get("permanent"))
    duration_raw = raw.get("duration", None)
    duration_set = duration_raw not in (None, "")
    if duration_set:
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError):
            duration = float(default_duration)
    else:
        try:
            duration = float(default_duration)
        except (TypeError, ValueError):
            duration = _DEFAULT_DURATION
    if duration <= 0 or permanent:
        permanent = True
        duration = 0.0

    payload = {
        "action": "show",
        "message": message,
        "duration": duration,
        "permanent": permanent,
    }
    if source:
        payload["source"] = source
    style = _normalize_style(raw.get("style"))
    if style:
        payload["style"] = style
    if fragments:
        payload["fragments"] = fragments
    return payload


def emit_chat_banner(
    message: str = "",
    *,
    duration: Any = None,
    permanent: bool = False,
    source: str = "",
    style: Optional[Dict[str, Any]] = None,
    fragments: Any = None,
    action: str = "show",
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """Broadcast ``chat_display_banner``. Empty shows are dropped."""
    raw: Dict[str, Any] = dict(payload or {})
    if message:
        raw["message"] = message
    if duration is not None:
        raw["duration"] = duration
    if permanent:
        raw["permanent"] = True
    if source:
        raw["source"] = source
    if style is not None:
        raw["style"] = style
    if fragments is not None:
        raw["fragments"] = fragments
    if action:
        raw["action"] = action

    if raw.get("duration") in (None, ""):
        default_duration = chat_banner_default_duration()
    else:
        default_duration = _DEFAULT_DURATION
    normalized = normalize_chat_banner_payload(raw, default_duration=default_duration)
    if normalized.get("action") != "clear":
        if not normalized.get("message") and not normalized.get("fragments"):
            return False

    try:
        from . import web_engine

        engine = web_engine.web_engine_instance
        if not engine:
            logger.error("Web engine not available for chat banner")
            return False
        return bool(engine.safe_emit(CHAT_BANNER_EVENT, normalized))
    except Exception as exc:
        logger.error("Failed to emit chat banner: %s", exc, exc_info=True)
        return False

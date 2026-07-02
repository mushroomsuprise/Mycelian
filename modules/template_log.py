#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Client template log validation, rate limiting, and Python logger bridge."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_TEMPLATE_NAME_LEN = 128
MAX_MESSAGE_LEN = 2048
MAX_STACK_LEN = 4096
MAX_URL_LEN = 512
MAX_SOURCE_LEN = 64
VALID_LEVELS = frozenset({"error", "warn", "info"})
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW_SEC = 60.0

TEMPLATE_LOGGER_SCRIPT = (
    '<script src="/assets/default_assets/template_logger.js"></script>'
)


class TemplateLogRateLimiter:
    """Per-client sliding-window rate limiter for template log events."""

    def __init__(
        self,
        max_events: int = RATE_LIMIT_MAX,
        window_sec: float = RATE_LIMIT_WINDOW_SEC,
    ) -> None:
        self.max_events = max_events
        self.window_sec = window_sec
        self._buckets: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        bucket = self._buckets.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window_sec:
            bucket.popleft()
        if len(bucket) >= self.max_events:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._buckets.clear()


def _truncate(value: Any, max_len: int) -> str:
    text = str(value) if value is not None else ""
    if len(text) > max_len:
        return text[:max_len]
    return text


def normalize_template_log_payload(
    data: Any,
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    if not isinstance(data, dict):
        return None, "Invalid data format: must be a dictionary"

    template_name = data.get("template_name")
    if not isinstance(template_name, str) or not template_name.strip():
        return None, "template_name is required"
    template_name = template_name.strip()[:MAX_TEMPLATE_NAME_LEN]

    level = data.get("level", "error")
    if not isinstance(level, str):
        return None, "level must be a string"
    level = level.strip().lower()
    if level not in VALID_LEVELS:
        return None, f"level must be one of: {', '.join(sorted(VALID_LEVELS))}"

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return None, "message is required"

    normalized: Dict[str, str] = {
        "template_name": template_name,
        "level": level,
        "message": _truncate(message.strip(), MAX_MESSAGE_LEN),
    }

    stack = data.get("stack")
    if isinstance(stack, str) and stack.strip():
        normalized["stack"] = _truncate(stack.strip(), MAX_STACK_LEN)

    url = data.get("url")
    if isinstance(url, str) and url.strip():
        normalized["url"] = _truncate(url.strip(), MAX_URL_LEN)

    source = data.get("source")
    if isinstance(source, str) and source.strip():
        normalized["source"] = _truncate(source.strip(), MAX_SOURCE_LEN)

    return normalized, None


def write_template_log_entry(normalized: Dict[str, str]) -> None:
    template_name = normalized["template_name"]
    level = normalized["level"]
    message = normalized["message"]
    prefix = f"[template:{template_name}]"
    if normalized.get("source"):
        prefix = f"{prefix} ({normalized['source']})"
    if normalized.get("url"):
        prefix = f"{prefix} url={normalized['url']}"

    log_message = f"{prefix} {message}"
    stack = normalized.get("stack")
    if level == "error" and stack:
        log_message = f"{log_message}\n{stack}"

    if level == "error":
        logger.error(log_message)
    elif level == "warn":
        logger.warning(log_message)
    else:
        logger.info(log_message)


def process_template_log(
    data: Any,
    client_key: str,
    rate_limiter: TemplateLogRateLimiter,
) -> Tuple[bool, Optional[str]]:
    if not rate_limiter.allow(client_key):
        return False, "rate_limited"

    normalized, err = normalize_template_log_payload(data)
    if err:
        return False, err

    write_template_log_entry(normalized)
    return True, None


def inject_template_logger(html: str) -> str:
    """Ensure template_logger.js is loaded in rendered template HTML."""
    if "template_logger.js" in html:
        return html

    overlay_tag = '<script src="/assets/default_assets/overlay_recovery.js"></script>'
    if overlay_tag in html:
        return html.replace(
            overlay_tag,
            overlay_tag + "\n    " + TEMPLATE_LOGGER_SCRIPT,
            1,
        )

    if "</head>" in html:
        return html.replace("</head>", f"    {TEMPLATE_LOGGER_SCRIPT}\n</head>", 1)
    if "</body>" in html:
        return html.replace("</body>", f"    {TEMPLATE_LOGGER_SCRIPT}\n</body>", 1)
    return html + TEMPLATE_LOGGER_SCRIPT

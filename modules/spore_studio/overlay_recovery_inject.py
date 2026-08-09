#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Inject shared overlay reconnect sync into template HTML."""

from __future__ import annotations

import re

_OVERLAY_RECOVERY_SCRIPT = (
    '<script src="/assets/default_assets/overlay_recovery.js"></script>'
)
_TEMPLATE_LOGGER_SCRIPT = (
    '<script src="/assets/default_assets/template_logger.js"></script>'
)

_SOCKET_IO_TAG = re.compile(
    r'<script\s+src="https://cdnjs\.cloudflare\.com/ajax/libs/socket\.io/[^"]+/socket\.io\.js"[^>]*>\s*</script>',
    re.IGNORECASE,
)

_IO_CONNECT_PATTERNS = [
    # var/let socket = io.connect(...)
    (
        re.compile(
            r"(?P<prefix>\b(?:var|let|const)\s+socket\s*=\s*)"
            r"io\.connect\('http://' \+ document\.domain \+ ':' \+ location\.port"
            r"(?:,\s*\{[^}]*\})?\)"
        ),
        r"\g<prefix>MycelianOverlay.connect('http://' + document.domain + ':' + location.port)",
    ),
    # broadcastSocket = io.connect(...)
    (
        re.compile(
            r"(?P<prefix>broadcastSocket\s*=\s*)"
            r"io\.connect\('http://' \+ document\.domain \+ ':' \+ location\.port\)"
        ),
        r"\g<prefix>MycelianOverlay.connect('http://' + document.domain + ':' + location.port)",
    ),
]


def inject_overlay_recovery(html: str) -> str:
    """Ensure overlay_recovery.js is loaded and sockets use MycelianOverlay.connect."""
    if "overlay_recovery.js" not in html:

        def _add_script(match: re.Match[str]) -> str:
            return match.group(0) + "\n    " + _OVERLAY_RECOVERY_SCRIPT

        html, count = _SOCKET_IO_TAG.subn(_add_script, html, count=1)
        if count == 0 and "</head>" in html:
            html = html.replace("</head>", f"    {_OVERLAY_RECOVERY_SCRIPT}\n</head>", 1)

    if "template_logger.js" not in html:
        if _OVERLAY_RECOVERY_SCRIPT in html:
            html = html.replace(
                _OVERLAY_RECOVERY_SCRIPT,
                _OVERLAY_RECOVERY_SCRIPT + "\n    " + _TEMPLATE_LOGGER_SCRIPT,
                1,
            )
        elif "</head>" in html:
            html = html.replace(
                "</head>", f"    {_TEMPLATE_LOGGER_SCRIPT}\n</head>", 1
            )

    for pattern, replacement in _IO_CONNECT_PATTERNS:
        html = pattern.sub(replacement, html)

    return html

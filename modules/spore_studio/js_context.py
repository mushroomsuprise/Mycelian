# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Advanced JS editor support: template scripts for the linter and the pop-out page.

* :func:`template_scripts` lists the ``<script>`` code of a compiled template so the
  editor (``js_lint.js``) can catalogue every function Advanced JS can call and know
  which names the template already declares.
* :func:`popout_page_html` builds the stand-alone pop-out editor page from the dialog
  markup in ``editor.html`` (between the ``SS_JS_DIALOG`` markers), so both copies of
  the editor share one source.
* :func:`valid_channel` checks the relay channel tokens the editor and the pop-out
  use to find each other over Socket.IO.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional

USER_BEGIN = "// SPORE_STUDIO:user-begin"

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script\s*>", re.IGNORECASE)
_SRC_RE = re.compile(r"""\bsrc\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)
_TYPE_RE = re.compile(r"""\btype\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)
_DIALOG_RE = re.compile(
    r"<!--\s*SS_JS_DIALOG:begin[^>]*-->([\s\S]*?)<!--\s*SS_JS_DIALOG:end\s*-->"
)
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Script types that hold JavaScript; anything else (JSON, templates) is skipped.
_JS_TYPES = {"", "text/javascript", "application/javascript", "module"}
MAX_EXTERNAL_BYTES = 2 * 1024 * 1024

# Scripts the pop-out page loads, in order (paths under /assets/default_assets/).
_POPOUT_SCRIPTS = (
    "spore_studio/vendor/acorn.js",
    "spore_studio/js_lint.js",
    "spore_studio/spore_ui.js",
    "spore_studio/advanced_js_editor.js",
)


def valid_channel(channel: object) -> bool:
    """True for a relay channel token (8-64 URL-safe characters)."""
    return isinstance(channel, str) and bool(_CHANNEL_RE.match(channel))


def _read_asset_script(src: str, assets_root: str) -> Optional[str]:
    """Code of a same-origin ``/assets/...`` script, or None (outside assets, missing, too big)."""
    path = src.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/assets/"):
        return None
    rel = path[len("/assets/"):]
    root = os.path.realpath(assets_root)
    full = os.path.realpath(os.path.join(root, *rel.split("/")))
    if full != root and not full.startswith(root + os.sep):
        return None
    try:
        if not os.path.isfile(full) or os.path.getsize(full) > MAX_EXTERNAL_BYTES:
            return None
        with open(full, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def template_scripts(
    html: str,
    *,
    assets_root: str,
    read_asset: Optional[Callable[[str, str], Optional[str]]] = None,
) -> List[Dict[str, object]]:
    """List a compiled template's scripts in page order.

    Inline scripts come back as ``{"kind": "inline", "code", "main"}`` where ``main``
    marks the one holding the Advanced JS user block. Same-origin ``/assets/...``
    scripts come back as ``{"kind": "external", "src", "code"}``. CDN scripts
    (socket.io) are skipped; the editor knows their globals.
    """
    reader = read_asset or _read_asset_script
    scripts: List[Dict[str, object]] = []
    for match in _SCRIPT_RE.finditer(html or ""):
        attrs, body = match.group(1), match.group(2)
        type_match = _TYPE_RE.search(attrs)
        if type_match and type_match.group(2).strip().lower() not in _JS_TYPES:
            continue
        src_match = _SRC_RE.search(attrs)
        if src_match:
            src = src_match.group(2).strip()
            code = reader(src, assets_root)
            if code is not None:
                scripts.append({"kind": "external", "src": src, "code": code})
            continue
        if not body.strip():
            continue
        scripts.append({"kind": "inline", "code": body, "main": USER_BEGIN in body})
    return scripts


def popout_page_html(editor_html: str) -> Optional[str]:
    """Stand-alone pop-out editor page, or None when the dialog markers are missing."""
    match = _DIALOG_RE.search(editor_html or "")
    if not match:
        return None
    dialog = match.group(1).strip()
    # The pop-out shows the dialog straight away.
    dialog = dialog.replace('class="ss-js-dialog" hidden', 'class="ss-js-dialog"', 1)
    scripts = "\n".join(
        f'    <script src="/assets/default_assets/{path}"></script>' for path in _POPOUT_SCRIPTS
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '    <meta charset="utf-8">\n'
        "    <title>Advanced JS</title>\n"
        '    <link rel="stylesheet" href="/assets/default_assets/spore_studio/editor.css">\n'
        '    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"\n'
        '            crossorigin="anonymous"></script>\n'
        "</head>\n"
        '<body data-ss-js-popout="1">\n'
        f"    {dialog}\n"
        f"{scripts}\n"
        "    <script>if (window.SporeUI) { window.SporeUI.hydrate(); }</script>\n"
        "</body>\n"
        "</html>\n"
    )

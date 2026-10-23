# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Help page loading and rendering helpers.

Each help topic's body lives in ``content/<topic_id>.html`` as an HTML fragment.
Fragments may use two tokens that are expanded at render time:

* ``{{icon:name}}``  - a Material icon, e.g. ``{{icon:settings}}``
* ``{{brand:key}}``  - a service brand mark from ``service_brand_icons``
  (twitch, youtube, spotify, psn, obs, discord, streamlabs, streamelements)

Styling lives in ``assets/css/*.css`` (concatenated in filename order) and
interactive behaviour in ``assets/help.js``. Both use only the theme manager's
``--color-*`` variables so pages follow the active theme.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parent

TOKEN_RE = re.compile(r"\{\{\s*(icon|brand):([a-z0-9_]+)\s*\}\}")
HEADING_RE = re.compile(r"<h([23])(\s[^>]*)?>(.*?)</h\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _package_path(*parts: str) -> Path:
    """Resolve a path inside this package, falling back to the PyInstaller bundle root."""
    path = _PACKAGE_DIR.joinpath(*parts)
    if path.exists():
        return path
    from ..path_utils import get_resource_path

    return Path(get_resource_path(str(Path("modules", "help_system", *parts))))


def content_dir() -> Path:
    return _package_path("content")


@lru_cache(maxsize=None)
def load_raw_html(topic_id: str) -> str:
    """Return the unexpanded HTML fragment for a topic ('' if missing)."""
    path = content_dir() / f"{topic_id}.html"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Help content file missing: %s", path)
        return ""
    except Exception as e:
        logger.error("Error reading help content %s: %s", path, e)
        return ""


def _brand_svg(key: str) -> str:
    from ..uiwindows.service_brand_icons import SERVICE_BRAND_SVG

    svg = SERVICE_BRAND_SVG.get(key)
    if not svg:
        return ""
    return f'<span class="hx-brand">{svg}</span>'


def expand_tokens(fragment: str) -> str:
    def repl(m: re.Match) -> str:
        kind, name = m.group(1), m.group(2)
        if kind == "icon":
            return f'<span class="hx-mi" aria-hidden="true">{name}</span>'
        return _brand_svg(name)

    return TOKEN_RE.sub(repl, fragment)


def slugify(text: str) -> str:
    text = html_lib.unescape(TAG_RE.sub("", text)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "section"


def html_to_text(fragment: str) -> str:
    """Plain text for search indexing and reading-time estimates."""
    fragment = TOKEN_RE.sub(" ", fragment)
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = html_lib.unescape(TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _heading_text(inner: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(TAG_RE.sub("", TOKEN_RE.sub("", inner)))).strip()


@lru_cache(maxsize=None)
def render_topic_html(topic_id: str) -> Tuple[str, Tuple[Tuple[str, str, str], ...]]:
    """Return (expanded HTML, TOC) for a topic.

    TOC entries are ``(level, text, anchor_id)`` for every h2/h3. Headings get a
    stable ``id`` (prefixed ``hx-``) unless the author already set one.
    """
    raw = load_raw_html(topic_id)
    toc: List[Tuple[str, str, str]] = []
    seen: dict = {}

    def add_id(m: re.Match) -> str:
        level, attrs, inner = m.group(1), m.group(2) or "", m.group(3)
        text = _heading_text(inner)
        existing = re.search(r'\bid="([^"]+)"', attrs)
        if existing:
            anchor = existing.group(1)
        else:
            base = "hx-" + slugify(text)
            count = seen.get(base, 0)
            seen[base] = count + 1
            anchor = base if count == 0 else f"{base}-{count + 1}"
            attrs = f'{attrs} id="{anchor}"'
        # Headings inside guide steps or tab panels are still listed so readers can jump to them.
        toc.append((f"h{level}", text, anchor))
        return f"<h{level}{attrs}>{inner}</h{level}>"

    html = HEADING_RE.sub(add_id, raw)
    return expand_tokens(html), tuple(toc)


@lru_cache(maxsize=None)
def topic_plain_text(topic_id: str) -> str:
    return html_to_text(load_raw_html(topic_id))


def _read_asset(*parts: str) -> str:
    try:
        return _package_path("assets", *parts).read_text(encoding="utf-8")
    except Exception as e:
        logger.error("Error reading help asset %s: %s", "/".join(parts), e)
        return ""


# The app's light-mode stylesheet forces ``color: var(--color-text-primary) !important``
# on every p/span/div/label (ui_styles.BASE_CSS). Help selectors (``.help-doc .x``) are
# more specific, so promoting their color declarations to !important lets them win;
# unclassed children then inherit from their styled parent instead of the blanket rule.
_COLOR_DECL_RE = re.compile(r"(?<![-\w])(color\s*:\s*)([^;{}!]+?)(\s*)(?=[;}])")
_LIGHT_MODE_INHERIT = """
body:not(.body--dark) .help-doc :is(p, span, div, label):not([class]) { color: inherit !important; }
"""


def promote_color_importance(css: str) -> str:
    """Mark every ``color:`` declaration ``!important`` (see the note above)."""
    return _COLOR_DECL_RE.sub(lambda m: f"{m.group(1)}{m.group(2)} !important{m.group(3)}", css)


_promote_colors = promote_color_importance


@lru_cache(maxsize=1)
def help_page_css() -> str:
    css_dir = _package_path("assets", "css")
    try:
        files = sorted(css_dir.glob("*.css"))
    except Exception:
        files = []
    css = "\n".join(f.read_text(encoding="utf-8") for f in files)
    return _promote_colors(css) + _LIGHT_MODE_INHERIT


@lru_cache(maxsize=1)
def help_page_js() -> str:
    return _read_asset("help.js")


def clear_caches() -> None:
    """Drop cached pages and assets (used by the preview tool and tests)."""
    load_raw_html.cache_clear()
    render_topic_html.cache_clear()
    topic_plain_text.cache_clear()
    help_page_css.cache_clear()
    help_page_js.cache_clear()

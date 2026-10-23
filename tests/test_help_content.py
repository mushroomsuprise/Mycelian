# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Integrity checks for the HTML help pages in modules/help_system/content."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from modules.help_system.help_content import HELP_TOPICS
from modules.help_system.help_manager import HelpManager
from modules.help_system.help_render import (
    TOKEN_RE,
    clear_caches,
    content_dir,
    help_page_css,
    render_topic_html,
)
from modules.uiwindows.service_brand_icons import SERVICE_BRAND_SVG

CSS_DIR = Path(__file__).resolve().parent.parent / "modules" / "help_system" / "assets" / "css"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_caches()
    yield


def _raw(topic_id: str) -> str:
    return (content_dir() / f"{topic_id}.html").read_text(encoding="utf-8")


class _TagBalance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list = []
        self.errors: list = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"unexpected </{tag}> at line {self.getpos()[0]}")
            return
        self.stack.pop()


def test_every_topic_has_a_page_and_no_orphans():
    files = {p.stem for p in content_dir().glob("*.html")}
    missing = sorted(set(HELP_TOPICS) - files)
    orphans = sorted(files - set(HELP_TOPICS))
    assert not missing, f"topics without content/<id>.html: {missing}"
    assert not orphans, f"content files without a topic entry: {orphans}"
    for topic_id in HELP_TOPICS:
        assert _raw(topic_id).strip(), f"{topic_id}.html is empty"


def test_manager_validation_is_clean():
    assert HelpManager().validate_topics() == []


@pytest.mark.parametrize("topic_id", sorted(HELP_TOPICS))
def test_page_markup(topic_id):
    raw = _raw(topic_id)

    parser = _TagBalance()
    parser.feed(raw)
    parser.close()
    assert not parser.errors, f"{topic_id}: {parser.errors[:3]}"
    assert not parser.stack, f"{topic_id}: unclosed {[t for t, _ in parser.stack][:5]}"

    assert "<script" not in raw.lower(), f"{topic_id}: scripts do not run in ui.html; use help.js contracts"
    assert not re.search(r"<h1[\s>]", raw), f"{topic_id}: the browser renders the title; start at <h2>"
    assert not re.search(r'style="[^"]*(#[0-9a-fA-F]{3,8}\b|rgba?\()', raw), (
        f"{topic_id}: inline colors bypass the theme; use theme classes"
    )

    for m in TOKEN_RE.finditer(raw):
        if m.group(1) == "brand":
            assert m.group(2) in SERVICE_BRAND_SVG, f"{topic_id}: unknown brand {m.group(2)}"
    # Other {{...}} text is legitimate (template variables shown in examples).
    leftover = re.findall(r"\{\{\s*(?:icon|brand)\b[^}]*\}\}", TOKEN_RE.sub("", raw))
    assert not leftover, f"{topic_id}: malformed tokens {leftover[:3]}"

    for target in re.findall(r'href="help:([^"]+)"', raw):
        assert target in HELP_TOPICS, f"{topic_id}: broken help link -> {target}"

    markers = set(re.findall(r'class="hm-marker[^"]*"\s+data-n="(\d+)"', raw))
    notes = set(re.findall(r'<li data-n="(\d+)"', raw))
    assert notes <= markers, f"{topic_id}: annotations without a marker: {sorted(notes - markers)}"

    html, toc = render_topic_html(topic_id)
    anchors = [a for _, _, a in toc]
    assert len(anchors) == len(set(anchors)), f"{topic_id}: duplicate heading anchors"


def test_guide_and_checklist_keys_are_unique():
    seen: dict = {}
    for topic_id in HELP_TOPICS:
        for key in re.findall(r'data-(?:guide|checklist)="([^"]+)"', _raw(topic_id)):
            assert key not in seen, f"progress key {key!r} used in {seen[key]} and {topic_id}"
            seen[key] = topic_id


def test_related_topics_resolve():
    for topic_id, topic in HELP_TOPICS.items():
        for rel in topic.related_topics:
            assert rel in HELP_TOPICS, f"{topic_id}: related topic {rel} missing"


def test_help_css_only_uses_theme_colors():
    """Help pages must follow the theme manager: colors come from --color-* variables."""
    allowed = {"#fff"}  # text on primary/success buttons, same as Quasar in the app
    for css_file in sorted(CSS_DIR.glob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", css_file.read_text(encoding="utf-8"), flags=re.S)
        for hexcolor in re.findall(r"#[0-9a-fA-F]{3,8}\b", text):
            assert hexcolor.lower() in allowed, f"{css_file.name}: hardcoded color {hexcolor}"
        assert not re.search(r"\brgba?\(", text), f"{css_file.name}: hardcoded rgb()/rgba() color"
        assert not re.search(r"\bhsla?\(", text), f"{css_file.name}: hardcoded hsl() color"
    assert ".help-doc" in help_page_css()


def test_search_reads_html_text():
    results = HelpManager().search("redirect")
    assert any(t.id == "twitch_setup" for t in results)

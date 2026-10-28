# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/help_content.py: topic metadata, and that every help topic renders
with working help: links and resolvable {{icon:}}/{{brand:}} tokens."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.help_system import help_render  # noqa: E402
from modules.help_system.help_content import HELP_TOPICS, HelpCategory, HelpTopic  # noqa: E402
from modules.uiwindows.service_brand_icons import SERVICE_BRAND_SVG  # noqa: E402

ICON_NAME_RE = re.compile(r"^[a-z0-9_]+$")


@pytest.fixture(autouse=True)
def _fresh():
    help_render.clear_caches()
    yield
    help_render.clear_caches()


def test_categories_are_unique_lowercase_values():
    values = [c.value for c in HelpCategory]
    assert len(values) == len(set(values)) == 10
    assert all(v == v.lower() and " " not in v for v in values)


def test_help_topic_defaults_are_independent():
    a = HelpTopic(id="a", title="A", category=HelpCategory.ALERTS, summary="s")
    b = HelpTopic(id="b", title="B", category=HelpCategory.ALERTS, summary="s")
    a.keywords.append("x")
    assert b.keywords == [] and b.related_topics == []
    assert a.video_url is None and a.ui_context is None


def test_help_topic_content_and_plain_text_use_renderer():
    # Local patch context: the autouse cache fixture tears down after the shared monkeypatch.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(help_render, "load_raw_html", lambda tid: f"<p>raw {tid}</p>")
        mp.setattr(help_render, "topic_plain_text", lambda tid: f"plain {tid}")
        t = HelpTopic(id="zz", title="Z", category=HelpCategory.SETTINGS, summary="s")
        assert t.content == "<p>raw zz</p>"
        assert t.plain_text == "plain zz"


def test_topic_table_is_consistent():
    assert HELP_TOPICS
    contexts = {}
    for key, topic in HELP_TOPICS.items():
        assert isinstance(topic, HelpTopic)
        assert topic.id == key, f"{key}: id mismatch {topic.id}"
        assert isinstance(topic.category, HelpCategory)
        assert topic.title.strip() and topic.summary.strip()
        assert topic.keywords, f"{key}: no keywords"
        assert key not in topic.related_topics, f"{key}: relates to itself"
        assert len(topic.related_topics) == len(set(topic.related_topics)), f"{key}: duplicate related"
        for rel in topic.related_topics:
            assert rel in HELP_TOPICS, f"{key}: related topic {rel!r} missing"
        if topic.video_url:
            assert topic.video_url.startswith("https://")
        if topic.ui_context:
            contexts.setdefault(topic.ui_context, []).append(key)
    # Every category used by the browser sidebar has at least one topic.
    assert {t.category for t in HELP_TOPICS.values()} == set(HelpCategory)


def test_titles_are_unique():
    titles = [t.title for t in HELP_TOPICS.values()]
    dupes = {t for t in titles if titles.count(t) > 1}
    assert not dupes


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a" and d.get("href") is not None:
            self.hrefs.append(d["href"])
        if d.get("id"):
            self.ids.append(d["id"])


@pytest.mark.parametrize("topic_id", sorted(HELP_TOPICS))
def test_every_topic_renders(topic_id):
    """Each topic renders to non-empty HTML with all tokens expanded and all links valid."""
    raw = help_render.load_raw_html(topic_id)
    assert raw.strip(), f"{topic_id}: no content"
    html, toc = help_render.render_topic_html(topic_id)
    assert html.strip()

    # Every {{icon:}} / {{brand:}} token resolves: nothing is left after rendering and
    # each brand token produced its SVG.
    assert not re.search(r"\{\{\s*(icon|brand)\s*:", html), f"{topic_id}: unexpanded token"
    for kind, name in help_render.TOKEN_RE.findall(raw):
        if kind == "brand":
            assert name in SERVICE_BRAND_SVG, f"{topic_id}: unknown brand {name}"
            assert SERVICE_BRAND_SVG[name] in html
        else:
            assert ICON_NAME_RE.match(name)
            assert f'<span class="hx-mi" aria-hidden="true">{name}</span>' in html

    parser = _Links()
    parser.feed(html)
    for href in parser.hrefs:
        if href.startswith("help:"):
            target = href[len("help:"):]
            assert target in HELP_TOPICS, f"{topic_id}: help link to missing topic {target!r}"
        elif href.startswith("#"):
            continue  # checked in test_in_page_links_resolve
        else:
            assert href.startswith(("http://", "https://", "mailto:")), f"{topic_id}: unexpected href {href!r}"

    # TOC anchors exist in the rendered page and are unique.
    anchors = [a for _, _, a in toc]
    assert len(anchors) == len(set(anchors))
    for level, text, anchor in toc:
        assert level in ("h2", "h3")
        assert text, f"{topic_id}: empty heading"
        assert f'id="{anchor}"' in html

    plain = help_render.topic_plain_text(topic_id)
    assert plain.strip(), f"{topic_id}: no readable text"


@pytest.mark.parametrize("topic_id", sorted(HELP_TOPICS))
def test_in_page_links_resolve(topic_id):
    html, _ = help_render.render_topic_html(topic_id)
    parser = _Links()
    parser.feed(html)
    missing = [h for h in parser.hrefs if h.startswith("#") and h[1:] not in parser.ids]
    assert not missing, f"{topic_id}: in-page links to missing anchors {missing}"


def test_topic_content_property_matches_file():
    topic = HELP_TOPICS["getting_started_intro"]
    assert topic.content == (help_render.content_dir() / "getting_started_intro.html").read_text(encoding="utf-8")
    assert topic.plain_text == help_render.html_to_text(topic.content)

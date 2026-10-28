# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/help_manager.py: indexing, search, navigation helpers, validation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.help_system import help_manager as hm  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.help_system.help_content import HELP_TOPICS, HelpCategory, HelpTopic  # noqa: E402


def _topic(tid, title, cat=HelpCategory.ALERTS, summary="A summary", keywords=("kw",), related=(), ctx=None):
    return HelpTopic(id=tid, title=title, category=cat, summary=summary, keywords=list(keywords),
                     related_topics=list(related), ui_context=ctx)


@pytest.fixture
def fake_text(monkeypatch):
    """Plain text / content lookups for synthetic topics."""
    texts = {}
    monkeypatch.setattr(help_render, "topic_plain_text", lambda tid: texts.get(tid, ""))
    monkeypatch.setattr(help_render, "load_raw_html", lambda tid: texts.get(tid, ""))
    return texts


@pytest.fixture
def manager(fake_text):
    m = hm.HelpManager()
    m.topics = {
        "a1": _topic("a1", "Alert Basics Guide", keywords=["alerts", "Follow"], related=["a2", "ghost"], ctx="alerts.tab"),
        "a2": _topic("a2", "Alert Media", keywords=["gif", "alerts"], summary="Gifs and sounds"),
        "a3": _topic("a3", "On it", keywords=["xy"]),
        "s1": _topic("s1", "Settings Page", cat=HelpCategory.SETTINGS, keywords=["config"]),
        "t1": _topic("t1", "Fix stuff", cat=HelpCategory.TROUBLESHOOTING, keywords=["broken"]),
    }
    fake_text.update({"a1": "content about overlays", "a2": "media", "a3": "x", "s1": "y", "t1": "z"})
    m._build_search_index()
    return m


def test_singleton(monkeypatch):
    monkeypatch.setattr(hm.HelpManager, "_instance", None)
    first = hm.get_help_manager()
    assert first is hm.get_help_manager() is hm.HelpManager.get_instance()
    assert first.topics is HELP_TOPICS


def test_index_contains_keywords_title_words_and_categories(manager):
    idx = manager._search_index
    assert idx["alerts"] == ["a1", "a2", "a3"]
    assert idx["follow"] == ["a1"]  # lowercased keyword
    assert "basics" in idx and "on" not in idx and "it" not in idx  # short title words skipped
    assert set(idx["settings"]) >= {"s1"}
    # no duplicates even though "alerts" is both a keyword and category name for a1
    assert len(idx["alerts"]) == len(set(idx["alerts"]))


def test_index_rebuild_clears(manager):
    manager.topics = {}
    manager._build_search_index()
    assert manager._search_index == {}


def test_get_topic_and_category_helpers(manager):
    assert manager.get_topic("a1").title == "Alert Basics Guide"
    assert manager.get_topic("missing") is None
    assert [t.id for t in manager.get_topics_by_category(HelpCategory.ALERTS)] == ["a1", "a2", "a3"]
    assert manager.get_all_categories() == [HelpCategory.ALERTS, HelpCategory.SETTINGS, HelpCategory.TROUBLESHOOTING]
    assert manager.get_topic_count() == 5
    assert manager.get_category_count(HelpCategory.ALERTS) == 3
    assert manager.get_category_count(HelpCategory.CHATBOT) == 0


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_empty(manager, query):
    assert manager.search(query) == []


def test_search_keyword_beats_title_and_limit(manager):
    results = manager.search("gif")
    assert results[0].id == "a2"
    assert manager.search("alerts", limit=1) and len(manager.search("alerts", limit=1)) == 1


def test_search_skips_single_letter_words(manager):
    manager._search_index["q"] = ["a1"]  # would match if one-letter words were looked up
    assert manager.search("q") == []


def test_search_partial_matches_and_content(manager):
    # "overlay" is not indexed but appears in a1's content; "brok" partially matches keyword "broken"
    assert [t.id for t in manager.search("overlays")] == ["a1"]
    assert manager.search("brok")[0].id == "t1"


def test_search_summary_and_title_phrase(manager):
    assert manager.search("gifs and")[0].id == "a2"
    assert manager.search("settings page")[0].id == "s1"


def test_search_drops_ids_missing_from_topics(manager):
    # A stale index entry reached through a partial match must not produce a result.
    manager._search_index = {"ghostlike": ["ghost"]}
    assert manager.search("ghostlike_word") == []


def test_get_topic_for_context(manager):
    assert manager.get_topic_for_context("alerts.tab").id == "a1"
    assert manager.get_topic_for_context("nope") is None


def test_related_topics_skip_missing(manager):
    assert [t.id for t in manager.get_related_topics("a1")] == ["a2"]
    assert manager.get_related_topics("missing") == []


def test_adjacent_topics(manager):
    assert manager.get_adjacent_topics("missing") == (None, None)
    prev, nxt = manager.get_adjacent_topics("a1")
    assert prev is None and nxt.id == "a2"
    prev, nxt = manager.get_adjacent_topics("a2")
    assert prev.id == "a1" and nxt.id == "a3"
    prev, nxt = manager.get_adjacent_topics("a3")
    assert prev.id == "a2" and nxt is None


def test_adjacent_topics_id_mismatch(manager):
    # A topic stored under a key that differs from its id cannot be located in its category.
    manager.topics["odd"] = _topic("other_id", "Odd", cat=HelpCategory.CHATBOT)
    assert manager.get_adjacent_topics("odd") == (None, None)


def test_popular_topics_real_data():
    m = hm.HelpManager()
    popular = m.get_popular_topics()
    assert [t.id for t in popular] == ["getting_started_intro", "twitch_setup", "connection_guides_overview",
                                       "alerts_overview", "guide_spotify"]
    assert len(m.get_popular_topics(limit=100)) == 9
    assert len(m.get_popular_topics(limit=2)) == 2


def test_popular_topics_skip_missing(manager):
    assert manager.get_popular_topics() == []


def test_validate_topics_reports_every_problem(manager, fake_text):
    manager.topics["bad"] = HelpTopic(id="bad", title=" ", category=HelpCategory.ALERTS, summary="",
                                      keywords=[], related_topics=["nowhere"])
    fake_text["bad"] = "   "
    errors = manager.validate_topics()
    assert "Topic bad: Missing title" in errors
    assert "Topic bad: Missing content file content/bad.html" in errors
    assert "Topic bad: Missing summary" in errors
    assert "Topic bad: Related topic 'nowhere' not found" in errors
    assert "Topic bad: No keywords defined" in errors
    assert "Topic a1: Related topic 'ghost' not found" in errors


def test_validate_real_topics_clean():
    assert hm.HelpManager().validate_topics() == []


def test_real_search_finds_twitch():
    m = hm.HelpManager()
    ids = [t.id for t in m.search("twitch connect")]
    assert "twitch_setup" in ids[:3]

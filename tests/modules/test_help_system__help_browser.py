# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/help_browser.py: the help browser UI (page and dialog modes),
navigation/history, search, sidebar, link handling and the show_help_browser entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import core, ui  # noqa: E402
from nicegui.client import Client  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules.help_system import help_browser as hb  # noqa: E402
from modules.help_system import help_manager as hm  # noqa: E402
from modules.help_system import help_window  # noqa: E402
from modules.help_system.help_content import HELP_TOPICS, HelpCategory, HelpTopic  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# ----------------------------------------------------------------- fixtures / helpers

@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(hb, "_help_system_styles_injected", False)
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(hb, "notify", lambda msg, **k: calls.append((msg, k)))
    return calls


@pytest.fixture
def js(monkeypatch):
    calls = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, *a, **k: calls.append(code))
    return calls


def fire(user, el, event="click", args=None):
    UserInteraction(user, {el}, None).trigger(event, args)


def button(user, text):
    found = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == text]
    assert found, f"no button {text!r}"
    return found[0]


def by_class(user, cls):
    return sorted((e for e in user.client.elements.values() if cls in e.classes), key=lambda e: e.id)


def labels(container):
    return [d.text for d in container.descendants() if isinstance(d, ui.label)]


def _topic(tid, title, cat=HelpCategory.ALERTS, summary="Short summary", keywords=("k1",), related=()):
    return HelpTopic(id=tid, title=title, category=cat, summary=summary, keywords=list(keywords),
                     related_topics=list(related))


def make_manager(topics):
    m = hm.HelpManager()
    m.topics = {t.id: t for t in topics}
    m._build_search_index()
    return m


def page(monkeypatch, check, manager=None, initial=None, category=None):
    """Build the browser as a page, then run ``check(user, browser)`` inside the client context."""
    if manager is not None:
        monkeypatch.setattr(hb, "get_help_manager", lambda: manager)
    made = {}

    def build():
        b = hb.HelpBrowser()
        b.build_page(initial_topic=initial, category=category)
        made["b"] = b

    async def run(user):
        with user.client:
            await check(user, made["b"])

    run_ui(build, run)
    return made["b"]


# ----------------------------------------------------------------- styles + notify

def test_ensure_styles_with_loop_injects_once(js):
    def build():
        hb.ensure_help_system_styles()
        hb.ensure_help_system_styles()

    run_ui(build)
    assert hb._help_system_styles_injected is True
    assert 'id="mycelian-help-system"' in Client.shared_head_html
    assert 'id="mycelian-help-script"' in Client.shared_head_html
    assert len(js) == 2  # style insert + help.js, only on the first call
    assert "mycelian-help-system" in js[0]


def test_ensure_styles_without_loop(monkeypatch, js):
    monkeypatch.setattr(core, "loop", None)
    hb.ensure_help_system_styles()
    assert js == []
    assert hb._help_system_styles_injected is True


def test_notify_help_open_failed(notes):
    hb._notify_help_open_failed(ValueError("bad"))
    msg, kw = notes[0]
    assert kw["type"] == "error" and kw["title"] == "Couldn't open Help"
    assert kw["details"] == "ValueError: bad" and kw["dedupe_key"] == "help:open_failed"


def test_estimate_reading_time():
    assert hb.HelpBrowser._estimate_reading_time("") == 1
    assert hb.HelpBrowser._estimate_reading_time("word " * 1000) == 5


def test_category_icons():
    b = hb.HelpBrowser()
    assert {b._get_category_icon(c) for c in HelpCategory} >= {"rocket_launch", "power", "settings"}
    assert all(b._get_category_icon(c) != "article" for c in HelpCategory)
    assert b._get_category_icon("unknown") == "article"


# ----------------------------------------------------------------- page build + welcome

def test_build_page_welcome(monkeypatch, js, notes):
    async def check(user, b):
        await user.should_see("Welcome to Mycelian Help")
        await user.should_see("Quick Start")
        await user.should_see("Popular Topics")
        await user.should_see("Browse by Category")
        assert b._page_mode is True
        assert b.current_topic is None
        assert not b.back_btn.enabled and not b.forward_btn.enabled
        # no close button in page mode
        assert not [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.props.get("icon") == "close"]
        assert set(b.category_containers) == set(HelpCategory)
        assert len(b.topic_buttons) > 10
        assert labels(b.breadcrumb_container) == ["Home"]

        # quick-start steps navigate
        steps = by_class(user, "help-step-item")
        assert len(steps) == 4
        fire(user, steps[0])
        assert b.current_topic.id == "twitch_setup"

    page(monkeypatch, check)
    assert any("helpnav" in code for code in js)  # click handler installed


def test_welcome_cards_and_quick_actions(monkeypatch, js, notes):
    async def check(user, b):
        cards = by_class(user, "help-topic-card")
        popular = [c for c in cards if "help-category-card-accent" not in c.classes]
        category_cards = [c for c in cards if "help-category-card-accent" in c.classes]
        assert popular and category_cards
        fire(user, popular[0])
        assert b.current_topic.id == "getting_started_intro"
        b._show_welcome()
        fire(user, by_class(user, "help-category-card-accent")[0])
        await user.should_see("Getting Started Topics")
        b._show_welcome()
        fire(user, button(user, "Troubleshooting"))
        await user.should_see("Troubleshooting Topics")
        b._show_welcome()
        fire(user, button(user, "Browse All Topics"))
        await user.should_see("Help Categories")

    page(monkeypatch, check)


def test_welcome_truncates_long_popular_summary(monkeypatch, js, notes):
    long = "x" * 120
    m = make_manager([_topic("getting_started_intro", "Intro", summary=long)])

    async def check(user, b):
        texts = labels(b.content_container)
        assert "x" * 80 + "..." in texts

    page(monkeypatch, check, manager=m)


def test_welcome_without_popular_topics(monkeypatch, js, notes):
    m = make_manager([_topic("only", "Only One")])

    async def check(user, b):
        await user.should_not_see("Popular Topics")
        await user.should_see("Browse by Category")

    page(monkeypatch, check, manager=m)


def test_welcome_and_views_without_content_container(js):
    b = hb.HelpBrowser()
    assert b._show_welcome() is None
    assert b._show_category_overview() is None
    assert b._show_category_topics(HelpCategory.ALERTS) is None
    b._render_topic(HELP_TOPICS["twitch_setup"])
    b._render_toc([("h2", "x", "y")])
    b._hide_toc()
    b._update_breadcrumb([("Home", None)])
    b._on_search(SimpleNamespace(args="twitch"))
    b._on_search_result_click(HELP_TOPICS["twitch_setup"])  # navigates; renders nothing
    assert b.current_topic.id == "twitch_setup"


def test_welcome_error_is_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        monkeypatch.setattr(b.help_manager, "get_topic_count", lambda: 1 / 0)
        b._show_welcome()

    page(monkeypatch, check)
    assert "Error showing welcome" in caplog.text


# ----------------------------------------------------------------- topic rendering

def test_open_initial_topic_renders_everything(monkeypatch, js, notes):
    async def check(user, b):
        t = HELP_TOPICS["twitch_setup"]
        await user.should_see(t.title)
        await user.should_see("On This Page")
        await user.should_see("Related Topics")
        assert b.current_topic is t
        assert b.history == ["twitch_setup"] and b.history_index == 0
        assert b.back_btn.enabled and not b.forward_btn.enabled
        assert "help-sidebar-active" in b.topic_buttons["twitch_setup"].classes
        assert b.category_containers[t.category]["expanded"] is True
        crumbs = labels(b.breadcrumb_container)
        assert crumbs == ["Home", "›", "Connection Guides", "›", t.title]
        assert any("min read" in s for s in labels(b.content_container))
        htmls = [e for e in b.content_container.descendants() if isinstance(e, ui.html)]
        assert htmls and "help-doc" in htmls[0].classes
        assert any("enhanceSoon" in c for c in js)

        # TOC click scrolls via help.js
        toc_items = by_class(user, "help-toc-item")
        assert toc_items
        fire(user, toc_items[0])
        assert "MycelianHelp.scrollTo" in js[-1]

        # breadcrumb: category then home
        crumb_items = by_class(user, "help-breadcrumb-item")
        fire(user, crumb_items[1])
        await user.should_see("Connection Guides Topics")
        b.navigate_to("twitch_setup")
        fire(user, by_class(user, "help-breadcrumb-item")[0])
        await user.should_see("Welcome to Mycelian Help")

    page(monkeypatch, check, initial="twitch_setup")


def test_prev_next_and_related_cards_navigate(monkeypatch, js, notes):
    async def check(user, b):
        prev_next = by_class(user, "help-prev-next-btn")
        assert len(prev_next) == 2
        prev_id, next_id = [t.id for t in b.help_manager.get_adjacent_topics("twitch_setup")]
        fire(user, prev_next[1])
        assert b.current_topic.id == next_id
        fire(user, by_class(user, "help-prev-next-btn")[0])
        assert b.current_topic.id == "twitch_setup"
        fire(user, by_class(user, "help-prev-next-btn")[0])
        assert b.current_topic.id == prev_id
        b.navigate_to("twitch_setup")
        related = by_class(user, "help-related-card")
        fire(user, related[0])
        assert b.current_topic.id == HELP_TOPICS["twitch_setup"].related_topics[0]

    page(monkeypatch, check, initial="twitch_setup")


def test_single_topic_has_no_prev_next_and_truncated_related(monkeypatch, js, notes):
    m = make_manager([
        _topic("a", "Topic A", related=["b"], keywords=()),
        _topic("b", "Topic B", cat=HelpCategory.CHATBOT, summary="y" * 100),
    ])

    async def check(user, b):
        b.navigate_to("a")
        assert by_class(user, "help-prev-next-btn") == []
        assert "y" * 70 + "..." in labels(b.content_container)
        assert by_class(user, "help-keyword-pill") == []
        # missing content file -> no TOC
        assert b.toc_container.style["display"] == "none"

    page(monkeypatch, check, manager=m)


def test_first_and_last_topic_spacers(monkeypatch, js, notes):
    m = make_manager([_topic("a", "First"), _topic("b", "Second")])

    async def check(user, b):
        b.navigate_to("a")
        btns = by_class(user, "help-prev-next-btn")
        assert len(btns) == 1 and "Next" in labels(btns[0])
        b.navigate_to("b")
        btns = by_class(user, "help-prev-next-btn")
        assert len(btns) == 1 and "Previous" in labels(btns[0])

    page(monkeypatch, check, manager=m)


def test_render_topic_error_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        monkeypatch.setattr(hb, "render_topic_html", lambda tid: 1 / 0)
        b.navigate_to("twitch_setup")

    page(monkeypatch, check)
    assert "Error rendering topic twitch_setup" in caplog.text


def test_render_toc_empty_hides(monkeypatch, js, notes):
    async def check(user, b):
        b._render_toc([("h2", "Top", "a"), ("h3", "Sub", "b")])
        items = by_class(user, "help-toc-item")
        assert ["help-toc-item-h3" in i.classes for i in items] == [False, True]
        b._render_toc(())
        assert by_class(user, "help-toc-item") == []
        assert b.toc_container.style["display"] == "none"

    page(monkeypatch, check)


# ----------------------------------------------------------------- navigation + history

def test_navigate_to_missing_topic_warns(monkeypatch, js, notes):
    async def check(user, b):
        b.navigate_to("no_such_topic")
        assert b.current_topic is None and b.history == []

    page(monkeypatch, check)
    msg, kw = notes[-1]
    assert kw["type"] == "warning" and kw["dedupe_key"] == "help:missing_topic:no_such_topic"


def test_navigate_error_notifies(monkeypatch, js, notes):
    async def check(user, b):
        monkeypatch.setattr(b, "_render_topic", lambda t: 1 / 0)
        b.navigate_to("twitch_setup")

    page(monkeypatch, check)
    assert notes[-1][1]["title"] == "Couldn't load this help page"
    assert "ZeroDivisionError" in notes[-1][1]["details"]


def test_history_back_forward(monkeypatch, js, notes):
    async def check(user, b):
        b.navigate_to("twitch_setup")
        b.navigate_to("twitch_setup")  # same topic: no new history entry
        b.navigate_to("guide_psn")
        assert b.history == ["twitch_setup", "guide_psn"]
        fire(user, b.back_btn)
        assert b.current_topic.id == "twitch_setup" and b.history_index == 0
        assert b.forward_btn.enabled
        fire(user, b.forward_btn)
        assert b.current_topic.id == "guide_psn"
        assert not b.forward_btn.enabled
        b._go_forward()  # at end: nothing
        assert b.current_topic.id == "guide_psn"
        b._go_back()
        b.navigate_to("guide_spotify")  # truncates forward history
        assert b.history == ["twitch_setup", "guide_spotify"]
        b._go_back()
        b._go_back()  # index 0 with a topic -> welcome
        assert b.history_index == -1 and b.current_topic is None
        b._go_back()  # nothing to go back to -> welcome again
        await user.should_see("Welcome to Mycelian Help")
        b._go_forward()
        assert b.current_topic.id == "twitch_setup"

    page(monkeypatch, check)


def test_history_with_vanished_topics(monkeypatch, js, notes):
    async def check(user, b):
        b.navigate_to("twitch_setup")
        b.navigate_to("guide_psn")
        b.history = ["ghost", "guide_psn"]
        b._go_back()
        assert b.current_topic.id == "guide_psn"  # restored old topic
        b.history = ["ghost", "ghost2"]
        b.history_index = 0
        b._go_forward()
        assert b.history_index == 1 and b.current_topic.id == "guide_psn"

    page(monkeypatch, check)


def test_history_capped_at_50(monkeypatch, js, notes):
    async def check(user, b):
        b.history = [f"x{i}" for i in range(50)]
        b.history_index = 49
        b.navigate_to("twitch_setup")
        assert len(b.history) == 50 and b.history[-1] == "twitch_setup" and b.history_index == 49

    page(monkeypatch, check)


def test_back_forward_errors_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        b.history = ["twitch_setup", "guide_psn"]
        b.history_index = 1
        monkeypatch.setattr(b.help_manager, "get_topic", lambda tid: 1 / 0)
        b._go_back()
        b.history_index = 0
        b._go_forward()
        monkeypatch.setattr(b, "back_btn", SimpleNamespace(enable=lambda: 1 / 0, disable=lambda: 1 / 0))
        b._update_nav_buttons()

    page(monkeypatch, check)
    assert "Error going back" in caplog.text
    assert "Error going forward" in caplog.text
    assert "Error updating nav buttons" in caplog.text


# ----------------------------------------------------------------- sidebar + categories

def test_sidebar_toggle_and_topic_buttons(monkeypatch, js, notes):
    async def check(user, b):
        cat = HelpCategory.ALERTS
        c = b.category_containers[cat]
        fire(user, c["header"])
        assert c["expanded"] is True and "expanded" in c["header"].classes
        assert c["content"].style["display"] == "block"
        fire(user, c["header"])
        assert c["expanded"] is False and "expanded" not in c["header"].classes
        b._toggle_category("not a category")  # ignored
        first_alert = b.help_manager.get_topics_by_category(cat)[0]
        fire(user, b.topic_buttons[first_alert.id])
        assert b.current_topic is first_alert
        assert c["expanded"] is True
        b._expand_category_in_sidebar(cat)  # already expanded: unchanged
        assert c["expanded"] is True
        b._clear_sidebar_active()
        assert all("help-sidebar-active" not in btn.classes for btn in b.topic_buttons.values())
        b._highlight_active_sidebar_topic("missing")

    page(monkeypatch, check)


def test_sidebar_caps_topics_at_15(monkeypatch, js, notes):
    topics = [_topic(f"t{i}", f"Topic {i}") for i in range(17)]
    m = make_manager(topics)

    async def check(user, b):
        assert len(b.topic_buttons) == 15
        await user.should_see("+ 2 more...")

    page(monkeypatch, check, manager=m)


def test_toggle_and_expand_errors_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        b.category_containers[HelpCategory.ALERTS] = {"expanded": False}
        b._toggle_category(HelpCategory.ALERTS)
        b._expand_category_in_sidebar(HelpCategory.ALERTS)

    page(monkeypatch, check)
    assert "Error toggling category" in caplog.text
    assert "Error expanding category in sidebar" in caplog.text


def test_build_errors_logged(monkeypatch, js, notes, caplog):
    m = make_manager([_topic("a", "A")])
    monkeypatch.setattr(m, "get_all_categories", lambda: 1 / 0)
    monkeypatch.setattr(hb, "get_help_manager", lambda: m)

    def build():
        b = hb.HelpBrowser()
        orig = ui.scroll_area
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:  # second scroll area is in the content area
                raise RuntimeError("layout")
            return orig(*a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ui, "scroll_area", flaky)
            b.build_page()

    run_ui(build)
    assert "Error building sidebar" in caplog.text
    assert "Error building content area" in caplog.text


def test_category_overview_and_topics(monkeypatch, js, notes):
    topics = [_topic(f"a{i}", f"Alert {i}", keywords=("one", "two", "three", "four")) for i in range(8)]
    topics.append(_topic("s0", "Setting Zero", cat=HelpCategory.SETTINGS, keywords=()))
    m = make_manager(topics)

    async def check(user, b):
        b._show_category_overview()
        await user.should_see("Help Categories")
        await user.should_see("8 topics")
        assert labels(b.breadcrumb_container)[-1] == "All Categories"
        fire(user, button(user, "View all 8 topics"))
        await user.should_see("Alerts Topics")
        pills = by_class(user, "help-keyword-pill")
        assert len(pills) == 8 * 3
        fire(user, by_class(user, "help-topic-card")[0])
        assert b.current_topic.id == "a0"
        b._show_category_overview()
        fire(user, button(user, "Setting Zero"))
        assert b.current_topic.id == "s0"

    page(monkeypatch, check, manager=m)


def test_category_views_errors_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        monkeypatch.setattr(b.help_manager, "get_all_categories", lambda: 1 / 0)
        monkeypatch.setattr(b.help_manager, "get_topics_by_category", lambda c: 1 / 0)
        b._show_category_overview()
        b._show_category_topics(HelpCategory.ALERTS)

    page(monkeypatch, check)
    assert "Error showing category overview" in caplog.text
    assert "Error showing category topics" in caplog.text


@pytest.mark.parametrize(
    "target, expected",
    [
        ("alerts", "alerts_overview"),
        ("ALERTS", "alerts_overview"),
        ("guide_psn", "guide_psn"),
        ("no_such_topic", None),
        ("nonsense", None),
    ],
)
def test_navigate_to_category(monkeypatch, js, notes, target, expected):
    async def check(user, b):
        b._navigate_to_category(target)
        if expected is None:
            assert b.current_topic is None
            await user.should_see("Welcome to Mycelian Help")
        else:
            first = b.help_manager.get_topics_by_category(b.current_topic.category)[0] if "_" not in target else None
            assert b.current_topic.id == (first.id if first else expected)
            assert b.category_containers[b.current_topic.category]["expanded"]

    page(monkeypatch, check)


def test_navigate_to_category_via_initial_and_empty_category(monkeypatch, js, notes):
    m = make_manager([_topic("a", "A")])

    async def check(user, b):
        assert b.current_topic.id == "a"
        b._navigate_to_category("chatbot")  # mapped category with no topics
        assert b.current_topic is None

    page(monkeypatch, check, manager=m, category="alerts")


def test_navigate_to_category_errors(monkeypatch, js, notes):
    async def check(user, b):
        b._navigate_to_category(None)  # .lower() on None -> welcome
        assert b.current_topic is None
        # topic found but expand fails: swallowed
        monkeypatch.setattr(b, "_expand_category_in_sidebar", lambda c: 1 / 0)
        monkeypatch.setattr(b, "navigate_to", lambda tid: setattr(b, "current_topic", HELP_TOPICS[tid]))
        b._navigate_to_category("guide_psn")
        assert b.current_topic.id == "guide_psn"

    page(monkeypatch, check)


# ----------------------------------------------------------------- search

def test_search_results_and_click(monkeypatch, js, notes):
    async def check(user, b):
        search = user.find(ui.input).elements.pop()
        fire(user, search, "update:modelValue", "twitch")
        assert b.search_results_container.style["display"] == "block"
        await user.should_see('Results for "twitch"')
        results = by_class(user, "help-search-result")
        assert results
        fire(user, results[0])
        assert b.search_results_container.style["display"] == "none"
        assert b.current_topic is not None

        fire(user, search, "update:modelValue", "zzqqxxnothing")
        await user.should_see('No results for "zzqqxxnothing"')
        fire(user, search, "update:modelValue", "a")
        assert b.search_results_container.style["display"] == "none"
        fire(user, search, "update:modelValue", None)
        assert b.search_results_container.style["display"] == "none"

    page(monkeypatch, check)


def test_search_error_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        monkeypatch.setattr(b.help_manager, "search", lambda q, limit=10: 1 / 0)
        b._on_search(SimpleNamespace(args="twitch"))

    page(monkeypatch, check)
    assert "Error handling search" in caplog.text


# ----------------------------------------------------------------- links

def test_handle_help_link_variants(monkeypatch, js, notes):
    async def check(user, b):
        b._handle_help_link(SimpleNamespace(args={"detail": "guide_psn"}))
        assert b.current_topic.id == "guide_psn"
        b._handle_help_link(SimpleNamespace(args="twitch_setup"))
        assert b.current_topic.id == "twitch_setup"
        b._handle_help_link(SimpleNamespace(args=42))
        b._handle_help_link(SimpleNamespace(args={"detail": ""}))
        assert b.current_topic.id == "twitch_setup"
        # through the hidden bridge element
        fire(user, b._nav_bridge, "helpnav", "guide_spotify")
        assert b.current_topic.id == "guide_spotify"

    page(monkeypatch, check)


def test_handle_help_link_error_logged(caplog):
    b = hb.HelpBrowser()
    b.navigate_to = lambda tid: 1 / 0
    b._handle_help_link(SimpleNamespace(args="x"))
    assert "Error handling help link" in caplog.text


def test_external_links(monkeypatch, notes):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    b = hb.HelpBrowser()
    b._handle_external_link(SimpleNamespace(args={"detail": "https://example.com"}))
    b._handle_external_link(SimpleNamespace(args="http://example.org"))
    b._handle_external_link(SimpleNamespace(args="javascript:alert(1)"))
    b._handle_external_link(SimpleNamespace(args=None))
    assert opened == ["https://example.com", "http://example.org"]
    assert notes == []


def test_external_link_browser_refuses(monkeypatch, notes):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    hb.HelpBrowser()._handle_external_link(SimpleNamespace(args="https://example.com/x"))
    msg, kw = notes[0]
    assert kw["type"] == "warning"
    assert kw["actions"] == [{"kind": "copy", "text": "https://example.com/x", "label": "Copy link"}]
    assert "RuntimeError" in kw["details"]


def test_external_link_error_without_url(notes):
    class _Evt:
        @property
        def args(self):
            raise RuntimeError("no args")

    hb.HelpBrowser()._handle_external_link(_Evt())
    assert notes == []


def test_external_link_via_bridge(monkeypatch, js, notes):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    async def check(user, b):
        fire(user, b._nav_bridge, "externallink", "https://example.com/bridge")

    page(monkeypatch, check)
    assert opened == ["https://example.com/bridge"]


def test_link_handler_install_and_teardown(monkeypatch, js, notes):
    async def check(user, b):
        n = len(js)
        b._setup_help_link_handler()  # already installed
        assert len(js) == n
        b._teardown_help_link_handler()
        assert b._help_click_installed is False
        assert "removeEventListener" in js[-1]
        b._setup_help_link_handler()
        assert b._help_click_installed is True
        assert f"getHtmlElement({b._nav_bridge.id})" in js[-1]

    page(monkeypatch, check)


def test_link_handler_without_bridge_and_teardown_error(monkeypatch):
    b = hb.HelpBrowser()
    b._setup_help_link_handler()
    assert b._help_click_installed is False

    def boom(code):
        raise RuntimeError("no client")

    monkeypatch.setattr(ui, "run_javascript", boom)
    b._help_click_installed = True
    b._teardown_help_link_handler()
    assert b._help_click_installed is False


def test_breadcrumb_error_logged(monkeypatch, js, notes, caplog):
    async def check(user, b):
        b._update_breadcrumb([("only-one-item",)])

    page(monkeypatch, check)
    assert "Error updating breadcrumb" in caplog.text


# ----------------------------------------------------------------- dialog mode

def test_show_dialog_mode(monkeypatch, js, notes):
    made = {}

    def build():
        b = hb.HelpBrowser()
        b.show(initial_topic="guide_psn")
        made["b"] = b

    async def check(user):
        b = made["b"]
        await user.should_see(HELP_TOPICS["guide_psn"].title)
        assert b.dialog.value is True
        assert b.dialog.props.get("maximized") is True
        close = [e for e in user.client.elements.values()
                 if isinstance(e, ui.button) and e.props.get("icon") == "close"]
        assert len(close) == 1
        fire(user, close[0])
        assert b.dialog.value is False
        fire(user, b.dialog, "hide")
        assert b._help_click_installed is False

    run_ui(build, check)


def test_show_dialog_category_and_home_button(monkeypatch, js, notes):
    made = {}

    def build():
        b = hb.HelpBrowser()
        b.show(category="chatbot")
        made["b"] = b

    async def check(user):
        b = made["b"]
        assert b.current_topic.category is HelpCategory.CHATBOT
        home = [e for e in user.client.elements.values()
                if isinstance(e, ui.button) and e.props.get("icon") == "home"]
        fire(user, home[0])
        await user.should_see("Welcome to Mycelian Help")

    run_ui(build, check)


def test_show_failure_notifies(monkeypatch, notes):
    def boom():
        raise RuntimeError("styles")

    monkeypatch.setattr(hb, "ensure_help_system_styles", boom)
    hb.HelpBrowser().show()
    assert notes[0][1]["title"] == "Couldn't open Help"


def test_show_dialog_hide_hook_failure_ignored(monkeypatch, js, notes):
    made = {}

    def build():
        b = hb.HelpBrowser()
        real_dialog = ui.dialog

        class _Dialog(real_dialog):
            def on(self, type, *a, **k):
                if type == "hide":
                    raise RuntimeError("no events")
                return super().on(type, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ui, "dialog", _Dialog)
            b.show()
        made["b"] = b

    run_ui(build)
    assert made["b"].dialog.value is True
    assert notes == []


def test_close_without_dialog():
    b = hb.HelpBrowser()
    b._close()  # no dialog: no error
    closed = []
    b.dialog = SimpleNamespace(close=lambda: closed.append(1))
    b._close()
    assert closed == [1]


# ----------------------------------------------------------------- show_help_browser

def test_show_help_browser_prefers_window(monkeypatch):
    calls = []
    monkeypatch.setattr(help_window, "open_help_window", lambda t, c: calls.append((t, c)) or True)
    monkeypatch.setattr(hb.HelpBrowser, "show", lambda self, **k: calls.append(("dialog", k)))
    hb.show_help_browser("twitch_setup")
    assert calls == [("twitch_setup", None)]


def test_show_help_browser_falls_back_to_dialog(monkeypatch):
    calls = []
    monkeypatch.setattr(help_window, "open_help_window", lambda t, c: False)
    monkeypatch.setattr(hb.HelpBrowser, "show", lambda self, **k: calls.append(k))
    hb.show_help_browser(category="alerts")
    assert calls == [{"initial_topic": None, "category": "alerts"}]


def test_show_help_browser_window_error_then_dialog(monkeypatch, caplog):
    calls = []

    def boom(t, c):
        raise RuntimeError("no tray")

    monkeypatch.setattr(help_window, "open_help_window", boom)
    monkeypatch.setattr(hb.HelpBrowser, "show", lambda self, **k: calls.append(k))
    hb.show_help_browser("x")
    assert calls == [{"initial_topic": "x", "category": None}]
    assert "using dialog" in caplog.text


def test_show_help_browser_dialog_error_notifies(monkeypatch, notes):
    monkeypatch.setattr(help_window, "open_help_window", lambda t, c: False)

    def boom(self, **k):
        raise RuntimeError("ui gone")

    monkeypatch.setattr(hb.HelpBrowser, "show", boom)
    hb.show_help_browser()
    assert notes[0][1]["details"] == "RuntimeError: ui gone"

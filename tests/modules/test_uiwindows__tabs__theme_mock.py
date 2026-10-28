# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/theme_mock.py: the Theme tab's static mock app markup."""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import notification_engine as ne  # noqa: E402
from modules.uiwindows.service_brand_icons import SERVICE_BRAND_SVG  # noqa: E402
from modules.uiwindows.tabs import theme_mock as tm  # noqa: E402

VOID = {"br", "hr", "img", "input", "meta", "link", "path", "circle", "rect", "polygon", "ellipse", "line", "polyline", "use"}


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
        self.classes = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "class" and v:
                self.classes.extend(v.split())
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        for k, v in attrs:
            if k == "class" and v:
                self.classes.extend(v.split())

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(tag)
            return
        self.stack.pop()

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())


def _parse(markup):
    p = _Balance()
    p.feed(markup)
    p.close()
    assert not p.errors, p.errors[:5]
    assert not p.stack, p.stack[:5]
    return p


def test_icon():
    assert tm._icon("star") == '<span class="thm-icon" aria-hidden="true">star</span>'


def test_tabs_marks_settings_active():
    p = _parse(tm._tabs())
    assert p.text[: len(tm._MAIN_TABS)] == list(tm._MAIN_TABS)
    assert p.classes.count("is-active") == 1
    assert '<div class="thm-m-tab is-active">Settings</div>' in tm._tabs()
    assert "thm-m-bell-badge" in p.classes


def test_sub_tabs_use_brand_svgs_and_icons():
    markup = tm._sub_tabs()
    _parse(markup)
    for kind, key, label in tm._SUB_TABS:
        assert label in markup
        if kind == "brand":
            assert SERVICE_BRAND_SVG[key] in markup
        else:
            assert tm._icon(key) in markup
    assert '<div class="thm-m-subtab is-active">' in markup and markup.count("is-active") == 1


def test_sub_tabs_unknown_brand_has_no_glyph(monkeypatch):
    monkeypatch.setattr(tm, "_SUB_TABS", (("brand", "nope", "Nope"),))
    assert tm._sub_tabs() == '<div class="thm-m-subtabs"><div class="thm-m-subtab">Nope</div></div>'


def test_sub_tab_order_matches_settings_tabs():
    from modules.ui_tab_transitions import SETTINGS_TAB_ORDER

    labels = [label for _, _, label in tm._SUB_TABS]
    # same relative order as the real bar (the mock omits some tabs)
    positions = [SETTINGS_TAB_ORDER.index(l) for l in labels]
    assert positions == sorted(positions)


def test_card_and_row_helpers():
    head = tm._card_head("tune", "Title", "Sub")
    p = _parse(head)
    assert p.text == ["tune", "Title", "Sub"]
    row = tm._row("T", "D", "<b>c</b>")
    assert _parse(row).text == ["T", "D", "c"]


@pytest.mark.parametrize(
    "fn, needles",
    [
        (tm._settings_card, ["Notifications and status bar", "Minimal badges", "thm-m-switch is-on", "System default"]),
        (tm._feed_card, ["MossyMika", "is-hover", "is-active", "--thm-ev:var(--color-info)"]),
        (tm._stats, ["Followers", "1,284", "thm-m-delta is-down", "height:99%"]),
        (tm._toasts_card, ["is-success", "is-info", "is-warning", "is-error", "Spotify token expired"]),
        (tm._controls_card, ["thm-m-btn is-outline", "thm-m-caret", "is-link"]),
    ],
)
def test_cards_well_formed(fn, needles):
    markup = fn()
    _parse(markup)
    for n in needles:
        assert n in markup


@pytest.mark.parametrize("minimal", [False, True])
def test_footer_uses_live_footer_helpers(minimal):
    markup = tm._footer(minimal)
    p = _parse(markup)
    assert ne.footer_style_classes(minimal, "stacked", "tiles") in markup
    tiers = [ne.footer_status_tier(k, raw) for k, raw in tm._FOOTER_SAMPLES]
    summary_tier, summary_text = ne.footer_summary(tiers)
    assert f"service-status-summary service-status-tier-{summary_tier}" in markup
    assert summary_text.replace("'", "&#x27;") in markup or summary_text in markup
    assert p.classes.count("service-status-item--svc") == len(tm._FOOTER_SAMPLES)
    for key, raw in tm._FOOTER_SAMPLES:
        assert ne._SERVICE_LABELS.get(key, key) in markup
    # services without a brand mark (internet, web engine) render without the brand span
    assert markup.count("service-status-brand-icon") == sum(1 for k, _ in tm._FOOTER_SAMPLES if k in SERVICE_BRAND_SVG)


def test_footer_custom_classes_and_escaping(monkeypatch):
    monkeypatch.setattr(tm, "_FOOTER_SAMPLES", (("weird<svc>", "Online"),))
    markup = tm._footer(False, "my-classes")
    assert 'class="service-status-footer w-full my-classes"' in markup
    assert "weird&lt;svc&gt;" in markup and "weird<svc>" not in markup
    _parse(markup)


@pytest.mark.parametrize("tier, icon", [("success", "check_circle"), ("info", "sync"), ("warning", "error"), ("error", "error")])
def test_footer_summary_icon(monkeypatch, tier, icon):
    monkeypatch.setattr(ne, "footer_summary", lambda tiers: (tier, "text"))
    assert f'service-status-summary-icon">{icon}</span>' in tm._footer(False)


def test_build_mock_html():
    markup = tm.build_mock_html()
    p = _parse(markup)
    assert markup.startswith('<div class="thm-m">')
    for part in (tm._tabs(), tm._sub_tabs(), tm._settings_card(), tm._feed_card(), tm._stats(),
                 tm._toasts_card(), tm._controls_card(), tm._footer(False)):
        assert part in markup
    assert "thm-m-grid" in p.classes
    minimal = tm.build_mock_html(minimal_footer=True)
    assert ne.footer_style_classes(True, "stacked", "tiles") in minimal
    custom = tm.build_mock_html(footer_classes="x-y")
    assert "service-status-footer w-full x-y" in custom

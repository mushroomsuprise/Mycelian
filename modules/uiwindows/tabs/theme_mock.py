# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Static markup for the Theme tab's mock app.

Every color comes from CSS variables (see ``theme_styles``), so switching the
previewed theme only swaps those variables; the markup is built once.
"""

from __future__ import annotations

import html

from ..service_brand_icons import SERVICE_BRAND_SVG

_MAIN_TABS = ("Activity Feed", "Alerts", "Source Settings", "Source Controls", "Connectors", "Chatbot", "Spore Studio", "Settings")

# Same order and icons as the real Settings sub-tab bar.
_SUB_TABS = (
    ("brand", "twitch", "Twitch"),
    ("brand", "obs", "OBS"),
    ("brand", "psn", "PSN"),
    ("brand", "spotify", "Spotify"),
    ("brand", "youtube", "YouTube"),
    ("brand", "streamlabs", "Streamlabs"),
    ("brand", "streamelements", "StreamElements"),
    ("icon", "memory", "Game Hooks"),
    ("icon", "storage", "Database"),
    ("icon", "analytics", "Statistics"),
    ("icon", "palette", "Theme"),
    ("icon", "tune", "App Settings"),
    ("icon", "info", "About"),
)

# Raw statuses, so the footer uses the same labels and tiers as the live one.
_FOOTER_SAMPLES = (
    ("internet", "Online"),
    ("webengine", "Running"),
    ("twitch", "Connected"),
    ("obs", "Connecting"),
    ("psn", "Offline"),
    ("spotify", "Disconnected"),
    ("youtube", "Partial (1/2 channels)"),
    ("streamlabs", "Connected"),
    ("streamelements", "Token expired"),
)


def _icon(name: str) -> str:
    return f'<span class="thm-icon" aria-hidden="true">{name}</span>'


def _tabs() -> str:
    tabs = "".join(
        f'<div class="thm-m-tab{" is-active" if name == "Settings" else ""}">{name}</div>' for name in _MAIN_TABS
    )
    bell = f'<div class="thm-m-bell">{_icon("notifications")}<span class="thm-m-bell-badge">3</span></div>'
    return f'<div class="thm-m-tabs">{tabs}{bell}</div>'


def _sub_tabs() -> str:
    parts = []
    for kind, key, label in _SUB_TABS:
        glyph = SERVICE_BRAND_SVG.get(key, "") if kind == "brand" else _icon(key)
        active = " is-active" if label == "Theme" else ""
        parts.append(f'<div class="thm-m-subtab{active}">{glyph}{label}</div>')
    return f'<div class="thm-m-subtabs">{"".join(parts)}</div>'


def _card_head(icon: str, title: str, sub: str) -> str:
    return (
        f'<div class="thm-m-card-head"><div class="thm-m-card-icon">{_icon(icon)}</div>'
        f'<div><div class="thm-m-title">{title}</div><div class="thm-m-sub">{sub}</div></div></div>'
    )


def _row(title: str, desc: str, control: str) -> str:
    return (
        f'<div class="thm-m-row"><div class="thm-m-row-text"><div class="thm-m-row-title">{title}</div>'
        f'<div class="thm-m-row-desc">{desc}</div></div>{control}</div>'
    )


def _settings_card() -> str:
    on, off = '<div class="thm-m-switch is-on"></div>', '<div class="thm-m-switch"></div>'
    select = f'<div class="thm-m-select">System default{_icon("arrow_drop_down")}</div>'
    return (
        '<div class="thm-m-card">'
        + _card_head("notifications", "Notifications and status bar", "What Mycelian tells you while it runs")
        + _row("Notifications", "Pop-up messages in the corner of the window.", on)
        + _row("Minimal badges", "Only icons with a colored ring.", off)
        + _row("App font", "Used everywhere in this window.", select)
        + "</div>"
    )


def _feed_card() -> str:
    events = (
        ("", "favorite", "var(--color-primary)", "<b>MossyMika</b> followed", "just now"),
        (" is-hover", "diamond", "var(--color-info)", "<b>SporeLord</b> cheered 500 bits", "2m ago"),
        (" is-active", "star", "var(--color-warning)", "<b>Fungi_Fan</b> subscribed for 6 months", "5m ago"),
    )
    rows = "".join(
        f'<div class="thm-m-event{cls}"><div class="thm-m-event-icon" style="--thm-ev:{color}">{_icon(icon)}</div>'
        f'<div class="thm-m-event-text">{text}</div><div class="thm-m-time">{when}</div></div>'
        for cls, icon, color, text, when in events
    )
    return (
        '<div class="thm-m-card">'
        + _card_head("dynamic_feed", "Activity Feed", "Latest events from your stream")
        + f'<div class="thm-m-feed">{rows}</div></div>'
    )


def _stats() -> str:
    tiles = (
        ("group_add", "Followers", "1,284", "+12 today", "", (3, 5, 4, 6, 5, 8, 9)),
        ("visibility", "Peak viewers", "87", "+9%", "", (4, 6, 5, 7, 6, 6, 8)),
        ("diamond", "Bits", "4,250", "-3%", " is-down", (6, 5, 7, 4, 6, 5, 5)),
    )
    out = []
    for icon, label, value, delta, down, bars in tiles:
        bar_html = "".join(f'<i style="height:{h * 11}%"></i>' for h in bars)
        out.append(
            f'<div class="thm-m-stat"><div class="thm-m-stat-label">{_icon(icon)}{label}</div>'
            f'<div class="thm-m-stat-value">{value}</div><div class="thm-m-delta{down}">{delta}</div>'
            f'<div class="thm-m-bars">{bar_html}</div></div>'
        )
    return f'<div class="thm-m-stats">{"".join(out)}</div>'


def _toasts_card() -> str:
    toasts = (
        ("success", "check_circle", "Settings saved"),
        ("info", "info", "Update available"),
        ("warning", "warning", "OBS is reconnecting"),
        ("error", "error", "Spotify token expired"),
    )
    items = "".join(
        f'<div class="thm-m-toast is-{kind}">{_icon(icon)}<span>{text}</span></div>' for kind, icon, text in toasts
    )
    return (
        '<div class="thm-m-card">'
        + _card_head("campaign", "Notifications", "Pop-ups and status colors")
        + f'<div class="thm-m-toasts">{items}</div></div>'
    )


def _controls_card() -> str:
    buttons = (
        '<span class="thm-m-btn">Primary</span><span class="thm-m-btn is-hover">Hover</span>'
        '<span class="thm-m-btn is-outline">Outline</span><span class="thm-m-btn is-success">Success</span>'
        '<span class="thm-m-btn is-warning">Warning</span><span class="thm-m-btn is-error">Error</span>'
    )
    return (
        '<div class="thm-m-card">'
        + _card_head("tune", "Controls and text", "Inputs, buttons and text styles")
        + '<div class="thm-m-controls"><div><div class="thm-m-input-label">Stream title</div>'
        '<div class="thm-m-input">Building a mushroom farm<span class="thm-m-caret"></span></div></div>'
        f'<div class="thm-m-buttons">{buttons}</div>'
        '<div class="thm-m-type"><span class="is-primary">Heading</span><span class="is-secondary">Body text</span>'
        '<span class="is-muted">Hint text</span><span class="is-link">Link</span></div></div></div>'
    )


def _footer(minimal: bool) -> str:
    from ...notification_engine import _SERVICE_LABELS, footer_status_display, footer_status_tier

    items = []
    for key, raw in _FOOTER_SAMPLES:
        tier = footer_status_tier(key, raw)
        label = html.escape(footer_status_display(key, raw))
        svg = SERVICE_BRAND_SVG.get(key, "")
        brand = f'<span class="service-status-brand-icon">{svg}</span>' if svg else ""
        items.append(
            f'<div class="service-status-item service-status-tier-{tier}">{brand}'
            f'<span class="service-status-name">{html.escape(_SERVICE_LABELS.get(key, key))}</span>'
            f'<div class="service-status-status-cluster"><span class="service-status-dot {tier}"></span>'
            f'<div class="service-status-badge {tier}"><span class="service-status-badge-label">{label}</span></div></div></div>'
        )
    minimal_cls = " service-status-footer--minimal" if minimal else ""
    return (
        f'<div class="service-status-footer w-full{minimal_cls}">'
        f'<div class="service-status-footer-inner w-full">{"".join(items)}</div></div>'
    )


def build_mock_html(minimal_footer: bool = False) -> str:
    """The mock app: tab bars, a Settings page with sample cards, and the status footer."""
    return (
        '<div class="thm-m">'
        + _tabs()
        + '<div class="thm-m-shell">'
        + _sub_tabs()
        + '<div class="thm-m-panel"><div class="thm-m-grid">'
        + f'<div class="thm-m-col">{_settings_card()}{_feed_card()}</div>'
        + f'<div class="thm-m-col">{_stats()}{_toasts_card()}{_controls_card()}</div>'
        + "</div></div></div>"
        + _footer(minimal_footer)
        + "</div>"
    )

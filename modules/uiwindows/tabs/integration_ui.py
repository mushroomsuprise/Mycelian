# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared layout for the Settings integration tabs (Twitch, OBS, PSN, Spotify, YouTube,
Streamlabs, StreamElements, Discord).

Page structure: a header (brand mark, title, connection pills, unsaved tag, help, Discard,
Save) over a two-column body. The left column holds account cards (who is connected, what
to do next, folded credentials); the right column holds settings rows and lists. Theme
variables only; scoped under ``.itg``.
"""

from __future__ import annotations

import html
import re
import webbrowser
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from nicegui import ui

from ...ui_buttons import outline_button, primary_button
from ..service_brand_icons import SERVICE_BRAND_SVG

BRAND_COLORS = {
    "twitch": "#9146FF",
    "obs": "#8A8A96",
    "psn": "#0070CC",
    "spotify": "#1DB954",
    "youtube": "#FF0000",
    "streamlabs": "#80F5D2",
    "streamelements": "#5A6CFF",
    "discord": "#5865F2",
}

INTEGRATION_CSS = """
.itg { width: 100%; color: var(--color-text-primary); }
.itg * { box-sizing: border-box; }
.itg .q-btn.text-white :is(span, div, i) { color: #fff; }
body:not(.body--dark) .itg :is(p, span, div, label):not([class]) { color: inherit; }
@layer overrides { .itg .q-btn.disabled { opacity: .38 !important; } }
.itg-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
  text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.itg-shell { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 12px; overflow: hidden; }

/* Header */
.itg-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 12px; border-bottom: 1px solid var(--color-border-subtle); }
.itg-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.itg-brand { width: 34px; height: 34px; border-radius: 9px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
  color: var(--color-text-primary);
  background: color-mix(in srgb, var(--brand) 14%, transparent); border: 1px solid color-mix(in srgb, var(--brand) 38%, transparent); }
.itg-brand svg { width: 19px; height: 19px; }
.itg-title-name { font-size: .95rem; font-weight: 650; line-height: 1.15; color: var(--color-text-primary); }
.itg-title-sub { font-size: .72rem; color: var(--color-text-muted); }
.itg-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.itg-spacer { flex: 1 1 auto; }
.itg-head .q-btn { min-height: 30px; font-size: .8rem; }
.itg-head .q-btn .q-icon { font-size: 17px; }
.itg-head .help-inline-icon, .itg-head .help-inline-icon .q-icon { color: var(--color-text-muted); }

/* Pills */
.itg-pill { --st: var(--color-text-muted); display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px; border-radius: 999px;
  font-size: .7rem; font-weight: 650; white-space: nowrap; color: var(--st);
  background: color-mix(in srgb, var(--st) 12%, transparent); border: 1px solid color-mix(in srgb, var(--st) 30%, transparent); }
.itg-pill .itg-icon { font-size: 14px; color: var(--st); }
.itg-pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--st); }
.itg-pill.is-ok { --st: var(--color-success); }
.itg-pill.is-warn, .itg-pill.is-edit { --st: var(--color-warning); }
.itg-pill.is-error { --st: var(--color-error); }
.itg-pill.is-busy { --st: var(--color-info); }

/* Body */
.itg-body { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); }
.itg-col { min-width: 0; }
.itg-col + .itg-col { border-left: 1px solid var(--color-border-subtle); }
.itg-wide { grid-column: 1 / -1; border-top: 1px solid var(--color-border-subtle); }
.itg-wide.is-top { border-top: none; }
@media (max-width: 1050px) {
  .itg-body { grid-template-columns: 1fr; }
  .itg-col + .itg-col { border-left: none; border-top: 1px solid var(--color-border-subtle); }
}
.itg-sec { padding: 12px 14px; border-top: 1px solid var(--color-border-subtle); }
.itg-col > .itg-sec:first-child, .itg-wide > .itg-sec:first-child { border-top: none; }
.itg-sec-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; font-size: .68rem; font-weight: 650; letter-spacing: .06em;
  text-transform: uppercase; color: var(--color-text-muted); }
.itg-sec-head .itg-icon { font-size: 14px; color: var(--color-primary); }
.itg-sec-right { margin-left: auto; text-transform: none; letter-spacing: 0; font-weight: 500; color: var(--color-text-muted); }

/* Account card */
.itg-acct { border: 1px solid var(--color-border-subtle); border-radius: 10px; background: var(--color-bg-base); overflow: hidden; }
.itg-acct + .itg-acct { margin-top: 10px; }
.itg-acct-top { display: flex; align-items: center; gap: 11px; padding: 11px 12px; flex-wrap: wrap; }
.itg-acct-who { display: flex; align-items: center; gap: 11px; min-width: 0; flex: 1 1 260px; }
.itg-ava { width: 40px; height: 40px; border-radius: 50%; flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
  font-weight: 750; font-size: 1rem; color: #fff; background: linear-gradient(135deg, var(--brand), color-mix(in srgb, var(--brand) 55%, #000)); }
.itg-ava.is-empty { color: var(--color-text-primary); background: var(--color-bg-surface); border: 1px dashed var(--color-border-default); }
.itg-ava.is-empty svg { width: 18px; height: 18px; opacity: .55; }
.itg-acct-kicker { font-size: .64rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); }
.itg-acct-name { font-size: .92rem; font-weight: 650; color: var(--color-text-primary); line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.itg-acct-state { display: flex; align-items: center; gap: 6px; font-size: .74rem; color: var(--color-text-secondary); }
.itg-acct-state .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--st, var(--color-text-muted)); flex: 0 0 auto; }
.itg-acct-state.is-ok { --st: var(--color-success); }
.itg-acct-state.is-warn, .itg-acct-state.is-busy { --st: var(--color-warning); }
.itg-acct-state.is-error { --st: var(--color-error); }
.itg-acct-state.is-off { --st: var(--color-text-muted); }
.itg-acct-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.itg-acct-actions .q-btn { min-height: 30px; font-size: .78rem; }
.itg-ibtn.q-btn { color: var(--color-text-muted); }
.itg-ibtn.q-btn:hover { color: var(--color-text-primary); }

.itg-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); border-top: 1px solid var(--color-border-subtle); }
.itg-meta > div { padding: 8px 12px; border-left: 1px solid var(--color-border-subtle); min-width: 0; }
.itg-meta > div:first-child { border-left: none; }
.itg-meta .k { font-size: .62rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--color-text-muted); }
.itg-meta .v { font-size: .8rem; font-weight: 600; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.itg-meta .s { font-size: .68rem; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.itg-meta .v.is-warn { color: var(--color-warning); }
.itg-meta .v.is-error { color: var(--color-error); }
.itg-meta .v.is-ok { color: var(--color-success); }
.itg-extra { border-top: 1px solid var(--color-border-subtle); padding: 10px 12px; }
.itg-media { display: flex; gap: 11px; align-items: center; min-width: 0; }
.itg-media-art { width: 40px; height: 40px; border-radius: 6px; flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, var(--brand), color-mix(in srgb, var(--brand) 40%, #000)); }
.itg-media-art .itg-icon { font-size: 20px; color: #fff; }
.itg-media.is-muted .itg-media-art { background: var(--color-bg-surface); border: 1px dashed var(--color-border-default); }
.itg-media.is-muted .itg-media-art .itg-icon { color: var(--color-text-muted); }
.itg-media-text { min-width: 0; }
.itg-media-title { font-size: .84rem; font-weight: 650; color: var(--color-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.itg-media.is-muted .itg-media-title { font-weight: 500; color: var(--color-text-secondary); }
.itg-media-sub { font-size: .74rem; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Credentials fold */
.itg-fold { border-top: 1px solid var(--color-border-subtle); width: 100%; }
.itg-fold .q-expansion-item__container, .itg-fold .q-expansion-item__content { width: 100%; }
.itg-fold .q-expansion-item__container > .q-item { min-height: 40px; padding: 0 12px; align-items: center; }
.itg-fold .q-expansion-item__container > .q-item > *:first-child { align-self: center; }
.itg-fold .q-item .q-icon { font-size: 17px; color: var(--color-text-muted); }
.itg-fold-head { display: flex; align-items: center; gap: 8px; width: 100%; }
.itg-fold-head .itg-icon { font-size: 17px; color: var(--color-text-muted); }
.itg-fold-title { font-size: .78rem; font-weight: 600; color: var(--color-text-secondary); }
.itg-fold-tag { display: inline-flex; align-items: center; gap: 3px; font-size: .66rem; font-weight: 650; color: var(--color-success); }
.itg-fold-tag.is-missing { color: var(--color-warning); }
.itg-fold-tag .itg-icon { font-size: 13px; color: inherit; }
.itg-fold-body { padding: 4px 12px 12px; width: 100%; }

/* Fields */
.itg-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.itg-fields.is-one { grid-template-columns: 1fr; }
.itg-fields + .itg-fields { margin-top: 10px; }
.itg-row + .itg-fields, div + .itg-fields { margin-top: 8px; }
@media (max-width: 640px) { .itg-fields { grid-template-columns: 1fr; } }
.itg-field-label { display: flex; align-items: center; gap: 5px; margin-bottom: 4px; font-size: .72rem; font-weight: 600; color: var(--color-text-secondary); }
.itg-field-label .itg-icon { font-size: 14px; color: var(--color-text-muted); cursor: help; }
.itg .q-field--outlined .q-field__control { min-height: 34px; height: 34px; border-radius: 8px; padding: 0 10px; }
.itg .q-field--outlined .q-field__native, .itg .q-field--outlined .q-field__marginal { min-height: 34px; height: 34px; font-size: .82rem; }
.itg .q-field--outlined.q-textarea .q-field__control { height: auto; min-height: 76px; padding-top: 6px; padding-bottom: 6px; }
.itg .q-field--outlined.q-textarea .q-field__native { height: auto; min-height: 60px; padding-top: 2px; line-height: 1.5; }
.itg .q-field--outlined .q-chip { margin: 2px; }
.itg-hint { display: flex; align-items: flex-start; gap: 6px; margin-top: 8px; font-size: .72rem; line-height: 1.4; color: var(--color-text-muted); }
.itg-hint .itg-icon { font-size: 14px; margin-top: 1px; color: var(--color-text-muted); }
.itg-hint.is-warn, .itg-hint.is-warn .itg-icon { color: var(--color-warning); }
.itg-link { color: color-mix(in srgb, var(--color-primary) 60%, var(--color-text-primary)); font-weight: 600; cursor: pointer; text-decoration: none; }
.itg-link:hover { text-decoration: underline; }
.itg-copy { display: flex; align-items: center; gap: 8px; margin-top: 10px; padding: 4px 4px 4px 10px; border-radius: 8px; min-height: 36px;
  background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); font-size: .74rem; }
.itg-copy-k { color: var(--color-text-muted); white-space: nowrap; }
.itg-copy-v { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .76rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--color-text-primary); }
.itg-copy-v.is-empty { font-family: inherit; color: var(--color-text-muted); }

/* One-line rows */
.itg-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 40px; padding: 4px 0; }
.itg-row + .itg-row { border-top: 1px dashed color-mix(in srgb, var(--color-border-subtle) 70%, transparent); }
.itg-row-text { min-width: 0; }
.itg-row-title { font-size: .84rem; color: var(--color-text-primary); }
.itg-row-sub { font-size: .72rem; color: var(--color-text-muted); line-height: 1.35; }
.itg-row-ctrl { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; }
.itg-row.is-dim { opacity: .45; }

/* Steps */
.itg-steps { display: flex; flex-direction: column; gap: 8px; counter-reset: s; }
.itg-step { display: flex; gap: 10px; align-items: flex-start; font-size: .8rem; color: var(--color-text-secondary); line-height: 1.45; }
.itg-step::before { counter-increment: s; content: counter(s); flex: 0 0 auto; width: 20px; height: 20px; border-radius: 50%; display: flex;
  align-items: center; justify-content: center; font-size: .68rem; font-weight: 750; color: var(--color-primary);
  background: var(--color-primary-light); border: 1px solid var(--color-border-accent); }
.itg-step b { color: var(--color-text-primary); font-weight: 650; }
.itg-step.is-done::before { content: 'check'; font-family: 'Material Icons'; font-size: 13px; color: var(--color-success);
  background: color-mix(in srgb, var(--color-success) 14%, transparent); border-color: color-mix(in srgb, var(--color-success) 35%, transparent); }

/* Lists */
.itg-list { border: 1px solid var(--color-border-subtle); border-radius: 9px; overflow: hidden; }
.itg-list.is-scroll { max-height: 340px; overflow-y: auto; }
.itg-li { display: flex; align-items: center; gap: 10px; min-height: 38px; padding: 3px 6px 3px 10px; font-size: .8rem; background: var(--color-bg-base); }
.itg-li + .itg-li { border-top: 1px solid var(--color-border-subtle); }
.itg-li.is-click { cursor: pointer; }
.itg-li.is-click:hover { background: color-mix(in srgb, var(--color-primary) 6%, var(--color-bg-base)); }
.itg-li.is-active { background: color-mix(in srgb, var(--color-primary) 12%, var(--color-bg-base)); }
.itg-li-icon { font-size: 16px; color: var(--color-text-muted); }
.itg-li-main { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-primary); }
.itg-li-muted { color: var(--color-text-muted); font-size: .72rem; white-space: nowrap; }
.itg-empty { padding: 12px; font-size: .76rem; text-align: center; color: var(--color-text-muted); background: var(--color-bg-base); }
.itg-list-foot { padding: 6px 10px; font-size: .7rem; color: var(--color-text-muted); background: var(--color-bg-surface);
  border-top: 1px solid var(--color-border-subtle); }
.itg-add { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.itg-add > .q-field { flex: 1 1 auto; min-width: 0; }
.itg-tokens { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.itg-token { display: inline-flex; align-items: center; height: 22px; padding: 0 8px; border-radius: 6px; font-size: .72rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--color-text-secondary);
  background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); cursor: pointer; }
.itg-token:hover { color: var(--color-text-primary); border-color: var(--color-border-accent); }

/* Banner */
.itg-banner { display: flex; gap: 10px; align-items: flex-start; margin: 12px 14px 0; padding: 10px 12px; border-radius: 9px; font-size: .78rem;
  color: var(--color-text-secondary); background: color-mix(in srgb, var(--color-warning) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-warning) 35%, transparent); }
.itg-banner > .itg-icon { font-size: 18px; color: var(--color-warning); }
.itg-banner b { color: var(--color-text-primary); }

/* Detail panel (PSN game cache) */
.itg-detail { border: 1px solid var(--color-border-subtle); border-radius: 10px; background: var(--color-bg-base); padding: 12px; min-width: 0; }
.itg-kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 12px; font-size: .78rem; margin-bottom: 12px; }
.itg-kv .k { color: var(--color-text-muted); }
.itg-kv .v { color: var(--color-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.itg-kv .v.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .74rem; }
.itg-split { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 14px; align-items: start; }
@media (max-width: 900px) { .itg-split { grid-template-columns: 1fr; } }
.itg-actions-end { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
"""

_STYLE_ID = "mycelian-integrations"


def inject_integration_styles() -> None:
    from ...help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

    inject_page_stylesheet(_STYLE_ID, promote_color_importance(scope_selectors(INTEGRATION_CSS, ".itg")))


def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def icon_html(name: str) -> str:
    return f'<span class="itg-icon">{esc(name)}</span>'


def brand_svg(key: str) -> str:
    svg = re.sub(r'width="[^"]+" height="[^"]+"', "", SERVICE_BRAND_SVG.get(key, ""), count=1)
    # The OBS mark is near-black: draw it in the text color so it shows on dark themes.
    return svg.replace('fill="#302E31"', 'fill="currentColor"') if key == "obs" else svg


def pill_html(kind: str, text: str, icon: Optional[str] = None) -> str:
    """kind: ok | warn | error | busy | off | edit."""
    inner = icon_html(icon) if icon else '<span class="dot"></span>'
    return f'<span class="itg-pill is-{esc(kind)}">{inner}{esc(text)}</span>'


def open_url(url: str) -> None:
    """Open in the system browser, not inside the app window."""
    if url:
        webbrowser.open(url, new=2)


def link(label: str, url: str) -> Any:
    """Inline text link that opens the system browser."""
    el = ui.label(label).classes("itg-link")
    el.on("click", lambda _e, u=url: open_url(u))
    return el


def copy_to_clipboard(text: str, what: str = "Copied") -> None:
    from ...notification_engine import copy_text_to_clipboard, notify

    if copy_text_to_clipboard(text or ""):
        notify("", type="success", source="app", title=what)
    else:
        ui.run_javascript("navigator.clipboard.writeText(" + _js_string(text or "") + ")")
        notify("", type="success", source="app", title=what)


def _js_string(text: str) -> str:
    import json

    return json.dumps(text)


class IntegrationPage:
    """Header + two-column body for one integration tab.

    ``is_dirty`` is polled to show the "Unsaved changes" tag and enable Discard, so tabs
    only need to keep their own dirty flag up to date.
    """

    def __init__(
        self,
        parent: Any,
        key: str,
        title: str,
        subtitle: str,
        *,
        on_save: Callable[[], Any],
        on_discard: Callable[[], Any],
        is_dirty: Callable[[], bool],
        help_topic: str = "",
        help_tooltip: str = "",
    ) -> None:
        inject_integration_styles()
        self._is_dirty = is_dirty
        self._last_dirty: Optional[bool] = None
        self._status_html = ""
        with parent:
            self.root = ui.element("div").classes("itg").style(f"--brand:{BRAND_COLORS.get(key, 'var(--color-primary)')}")
            with self.root:
                with ui.element("div").classes("itg-shell"):
                    with ui.element("div").classes("itg-head"):
                        ui.html(
                            f'<span class="itg-brand">{brand_svg(key)}</span>'
                            f'<span><div class="itg-title-name">{esc(title)}</div>'
                            f'<div class="itg-title-sub">{esc(subtitle)}</div></span>',
                            sanitize=False,
                        ).classes("itg-title")
                        self._pills = ui.html("", sanitize=False).classes("itg-pills")
                        self._dirty_tag = ui.html("", sanitize=False)
                        ui.element("div").classes("itg-spacer")
                        if help_topic:
                            from ...help_system.contextual_help import help_button

                            help_button(topic_id=help_topic, tooltip=help_tooltip or f"{title} setup help", size="sm")
                        self.discard_button = outline_button("Discard", on_discard).props("dense").classes("px-3")
                        self.save_button = primary_button("Save", on_save, icon="check").props("dense").classes("px-4")
                    self.body = ui.element("div").classes("itg-body")
            self._timer = ui.timer(0.5, self.refresh_dirty)
        self.refresh_dirty()

    def column(self) -> Any:
        with self.body:
            return ui.element("div").classes("itg-col")

    def wide(self, *, top: bool = False) -> Any:
        with self.body:
            return ui.element("div").classes("itg-wide" + (" is-top" if top else ""))

    def set_status(self, pills: Sequence[Tuple[str, str]]) -> None:
        """Connection pills in the header: [(kind, text), ...]."""
        content = "".join(pill_html(kind, text) for kind, text in pills)
        if content != self._status_html:
            self._status_html = content
            self._pills.set_content(content)

    def refresh_dirty(self) -> None:
        try:
            dirty = bool(self._is_dirty())
        except Exception:
            dirty = False
        if dirty == self._last_dirty:
            return
        self._last_dirty = dirty
        self._dirty_tag.set_content(pill_html("edit", "Unsaved changes", "edit") if dirty else "")
        if dirty:
            self.discard_button.props(remove="disable")
        else:
            self.discard_button.props("disable")

    def set_active(self, active: bool) -> None:
        """Pause the dirty poll while the tab is hidden."""
        self._timer.active = active


def section(icon: str, title: str, right: str = "") -> Any:
    """A titled block in a column. ``right``: small HTML shown at the end of the heading."""
    sec = ui.element("div").classes("itg-sec")
    with sec:
        head = f'{icon_html(icon)}{esc(title)}'
        if right:
            head += f'<span class="itg-sec-right">{right}</span>'
        ui.html(head, sanitize=False).classes("itg-sec-head")
    return sec


class AccountCard:
    """Who is connected, its state, actions, details and folded credentials.

    Build order inside the card: top row (avatar, name, state, ``actions``), details
    (``set_meta``), then anything added with ``extra()`` or ``credentials()``.
    """

    def __init__(self, key: str, kicker: str) -> None:
        self.key = key
        self.kicker = kicker
        self._top = ""
        self._meta_content = ""
        self.card = ui.element("div").classes("itg-acct")
        with self.card:
            with ui.element("div").classes("itg-acct-top"):
                self._who = ui.html("", sanitize=False).classes("itg-acct-who")
                self.actions = ui.element("div").classes("itg-acct-actions")
            self._meta = ui.html("", sanitize=False).classes("w-full")
            self._meta.set_visibility(False)
        self.set("", "off", "")

    def set(self, name: str, state_kind: str, state_text: str, *, initial: Optional[str] = None) -> None:
        """``initial``: a letter for the avatar; None shows the muted brand mark."""
        if initial:
            ava = f'<div class="itg-ava">{esc(initial[:1].upper())}</div>'
        else:
            ava = f'<div class="itg-ava is-empty">{brand_svg(self.key)}</div>'
        content = (
            f'{ava}<div style="min-width:0"><div class="itg-acct-kicker">{esc(self.kicker)}</div>'
            f'<div class="itg-acct-name">{esc(name)}</div>'
            f'<div class="itg-acct-state is-{esc(state_kind)}"><span class="dot"></span><span>{esc(state_text)}</span></div></div>'
        )
        if content != self._top:
            self._top = content
            self._who.set_content(content)

    def set_meta(self, rows: Iterable[Tuple[str, str, str, str]]) -> None:
        """Detail cells: (label, value, sub-line, value class ok|warn|error|"")."""
        cells = "".join(
            f'<div><div class="k">{esc(k)}</div><div class="v {esc(c)}">{esc(v)}</div>'
            + (f'<div class="s">{esc(s)}</div>' if s else "")
            + "</div>"
            for k, v, s, c in rows
        )
        content = f'<div class="itg-meta">{cells}</div>' if cells else ""
        if content != self._meta_content:
            self._meta_content = content
            self._meta.set_content(content)
            self._meta.set_visibility(bool(cells))

    def extra(self) -> Any:
        """A padded strip under the details (e.g. now playing, invite link)."""
        with self.card:
            return ui.element("div").classes("itg-extra")

    def credentials(self, title: str, *, opened: bool = False) -> "CredentialsFold":
        with self.card:
            return CredentialsFold(title, opened=opened)


class CredentialsFold:
    """Collapsible credentials area with a Saved / Needed to connect tag."""

    def __init__(self, title: str, *, opened: bool = False) -> None:
        self.title = title
        self._tag_state: Optional[bool] = None
        self.expansion = ui.expansion(value=opened).classes("itg-fold").props("dense")
        with self.expansion.add_slot("header"):
            self._head = ui.html("", sanitize=False).classes("itg-fold-head")
        with self.expansion:
            self.body = ui.element("div").classes("itg-fold-body")
        self.set_saved(None)

    def set_saved(self, saved: Optional[bool], missing_text: str = "Needed to connect") -> None:
        tag = ""
        if saved is True:
            tag = f'<span class="itg-fold-tag">{icon_html("check_circle")}Saved</span>'
        elif saved is False:
            tag = f'<span class="itg-fold-tag is-missing">{icon_html("error_outline")}{esc(missing_text)}</span>'
        self._head.set_content(f'{icon_html("vpn_key")}<span class="itg-fold-title">{esc(self.title)}</span>{tag}')

    def open(self) -> None:
        self.expansion.value = True


def fields(one: bool = False) -> Any:
    return ui.element("div").classes("itg-fields" + (" is-one" if one else ""))


def field_label(label: str, tip: str = "") -> Any:
    el = ui.html(f'{esc(label)}{icon_html("help_outline") if tip else ""}', sanitize=False).classes("itg-field-label")
    if tip:
        el.tooltip(tip)
    return el


def field(
    label: str,
    *,
    value: Any = "",
    tip: str = "",
    password: bool = False,
    placeholder: str = "",
    readonly: bool = False,
    on_change: Optional[Callable[[Any], Any]] = None,
) -> Any:
    """Label above an outlined input. Only secrets should use ``password``."""
    with ui.element("div").style("min-width:0"):
        field_label(label, tip)
        inp = ui.input(
            value="" if value is None else str(value),
            placeholder=placeholder,
            password=password,
            password_toggle_button=password,
        ).props("outlined dense" + (" readonly" if readonly else "")).classes("w-full")
        if on_change is not None:
            inp.on_value_change(on_change)
    return inp


def row(title: str, sub: str = "") -> Tuple[Any, Any]:
    """One settings line. Returns (row, control container): ``with ctrl: ui.switch(...)``."""
    r = ui.element("div").classes("itg-row")
    with r:
        text = f'<div class="itg-row-title">{esc(title)}</div>'
        if sub:
            text += f'<div class="itg-row-sub">{esc(sub)}</div>'
        ui.html(text, sanitize=False).classes("itg-row-text")
        ctrl = ui.element("div").classes("itg-row-ctrl")
    return r, ctrl


def switch_row(title: str, sub: str, value: bool, on_change: Callable[[bool], Any], *, tooltip: str = "") -> Any:
    """A row with a switch on the right. Returns the switch."""
    _r, ctrl = row(title, sub)
    with ctrl:
        sw = ui.switch(value=bool(value)).props("dense")
        sw.on_value_change(lambda e: on_change(bool(e.value)))
        if tooltip:
            sw.tooltip(tooltip)
    return sw


def steps_html(items: Iterable[Tuple[str, bool]]) -> str:
    """Numbered steps; text may contain <b>. A True flag shows a tick (only when Mycelian can tell)."""
    return '<div class="itg-steps">' + "".join(
        f'<div class="itg-step{" is-done" if done else ""}"><span>{text}</span></div>' for text, done in items
    ) + "</div>"


def steps(items: Iterable[Tuple[str, bool]]) -> Any:
    return ui.html(steps_html(items), sanitize=False).classes("w-full")


def hint(text: str, icon: str = "info", *, link_label: str = "", url: str = "", warn: bool = False) -> Any:
    """Muted note with an icon; optional trailing link to an external page."""
    box = ui.element("div").classes("itg-hint" + (" is-warn" if warn else ""))
    with box:
        ui.html(icon_html(icon), sanitize=False)
        with ui.element("span"):
            ui.html(text, sanitize=False).classes("inline")
            if link_label and url:
                ui.html("&nbsp;", sanitize=False).classes("inline")
                link(link_label, url).classes("inline")
    return box


class CopyRow:
    """Label + monospace value + copy button (redirect URIs, invite links)."""

    def __init__(self, label: str, value: str = "", *, copied: str = "Copied", empty: str = "", extra: Callable[[], Any] = None) -> None:
        self._value = value or ""
        self._empty = empty
        self._copied = copied
        with ui.element("div").classes("itg-copy"):
            ui.label(label).classes("itg-copy-k")
            self._label = ui.label("").classes("itg-copy-v")
            self.button = (
                ui.button(icon="content_copy", on_click=self._copy)
                .props("flat round dense size=sm")
                .classes("itg-ibtn")
                .tooltip("Copy")
            )
            if extra is not None:
                extra()
        self.set(self._value)

    def set(self, value: str) -> None:
        self._value = value or ""
        self._label.set_text(self._value or self._empty)
        if self._value:
            self._label.classes(remove="is-empty")
            self.button.props(remove="disable")
        else:
            self._label.classes(add="is-empty")
            self.button.props("disable")

    def _copy(self) -> None:
        if self._value:
            copy_to_clipboard(self._value, self._copied)


def list_box(*, scroll: bool = False) -> Any:
    return ui.element("div").classes("itg-list" + (" is-scroll" if scroll else ""))


def list_item(
    icon: str,
    main: str,
    muted: str = "",
    *,
    on_remove: Optional[Callable[[], Any]] = None,
    remove_tip: str = "Remove",
    on_click: Optional[Callable[[], Any]] = None,
    active: bool = False,
) -> Any:
    cls = "itg-li" + (" is-click" if on_click else "") + (" is-active" if active else "")
    li = ui.element("div").classes(cls)
    with li:
        ui.html(icon_html(icon), sanitize=False).classes("itg-li-icon")
        ui.label(main).classes("itg-li-main").tooltip(main) if len(main) > 48 else ui.label(main).classes("itg-li-main")
        if muted:
            ui.label(muted).classes("itg-li-muted")
        if on_remove is not None:
            btn = ui.button(icon="close").props("flat round dense size=sm").classes("itg-ibtn").tooltip(remove_tip)
            btn.on("click.stop", lambda _e: on_remove())
    if on_click is not None:
        li.on("click", lambda _e: on_click())
    return li


def list_empty(text: str) -> Any:
    return ui.label(text).classes("itg-empty")


def list_foot(text: str) -> Any:
    return ui.label(text).classes("itg-list-foot")


def tokens(names: Sequence[str], on_click: Callable[[str], Any]) -> Any:
    """Clickable placeholder chips ({platform}, {url}...)."""
    box = ui.element("div").classes("itg-tokens")
    with box:
        for name in names:
            t = ui.label(name).classes("itg-token").tooltip("Insert")
            t.on("click", lambda _e, n=name: on_click(n))
    return box


def banner() -> Any:
    """Warning strip across the top of the body; fill with ``set_content`` and toggle visibility."""
    el = ui.html("", sanitize=False).classes("itg-banner")
    el.set_visibility(False)
    return el


def banner_html(title: str, text: str) -> str:
    return f'{icon_html("warning_amber")}<div><b>{esc(title)}</b> {esc(text)}</div>'


def media_html(icon: str, title: str, sub: str = "", *, muted: bool = False) -> str:
    """Artwork tile + title + sub-line (e.g. the current track in ``AccountCard.extra()``)."""
    text = f'<div class="itg-media-title">{esc(title)}</div>'
    if sub:
        text += f'<div class="itg-media-sub">{esc(sub)}</div>'
    return (
        f'<div class="itg-media{" is-muted" if muted else ""}"><div class="itg-media-art">{icon_html(icon)}</div>'
        f'<div class="itg-media-text">{text}</div></div>'
    )


def set_connect_style(btn: Any, connected: bool, *, connect: str = "Connect", reconnect: str = "Reconnect") -> None:
    """Primary "Connect" while not connected, outline "Reconnect" once connected.

    For a button made with ``primary_button``; only the label and the outline prop change.
    """
    label = reconnect if connected else connect
    if getattr(btn, "_itg_connected", None) == connected and btn.text == label:
        return
    btn._itg_connected = connected
    btn.set_text(label)
    if connected:
        btn.props("outline")
    else:
        btn.props(remove="outline")


def initial_of(name: str) -> Optional[str]:
    for ch in str(name or ""):
        if ch.isalnum():
            return ch.upper()
    return None


__all__: List[str] = [
    "AccountCard",
    "BRAND_COLORS",
    "CopyRow",
    "CredentialsFold",
    "IntegrationPage",
    "banner",
    "banner_html",
    "brand_svg",
    "copy_to_clipboard",
    "esc",
    "field",
    "field_label",
    "fields",
    "hint",
    "icon_html",
    "initial_of",
    "inject_integration_styles",
    "link",
    "list_box",
    "list_empty",
    "list_foot",
    "list_item",
    "media_html",
    "open_url",
    "pill_html",
    "row",
    "section",
    "set_connect_style",
    "steps",
    "steps_html",
    "switch_row",
    "tokens",
]

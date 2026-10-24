# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the Connectors tab cards, folders and toolbar (theme variables only)."""

CONNECTORS_CSS = """
.cnx { color: var(--color-text-primary); }
.cnx * { box-sizing: border-box; }
.cnx .cx-icon { color: inherit; }
.cnx button > .contents { color: inherit; }
body:not(.body--dark) .cnx :is(p, span, div, label):not([class]) { color: inherit; }
.cnx .q-btn.text-white :is(span, div, i) { color: #fff; }
@layer overrides { .cnx .q-btn.disabled { opacity: .38 !important; } }

.cx-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }
.cx-brand { display: inline-flex; width: 1em; height: 1em; flex: 0 0 auto; }
.cx-brand svg { width: 100%; height: 100%; display: block; }
/* The OBS mark ships with a fixed dark fill; follow the text colour so it shows on dark themes. */
.cx-brand.is-obs path { fill: currentColor; }
.cx-spacer { flex: 1 1 auto; }

/* Toolbar */
.cx-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 12px; border-radius: 12px; flex: 0 0 auto;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.cx-bar-icon { width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.cx-bar-icon .cx-icon { font-size: 17px; }
.cx-bar-title { font-size: .92rem; font-weight: 650; line-height: 1.15; color: var(--color-text-primary); }
.cx-bar-sub { font-size: .72rem; color: var(--color-text-muted); }
.cx-search { width: 280px; max-width: 100%; }
.cx-search .q-field__control { min-height: 32px; height: 32px; border-radius: 8px; }
.cx-search .q-field__marginal { height: 32px; }
.cx-search .q-field__native { min-height: 32px; padding: 0; font-size: .8rem; }
.cx-filters { display: inline-flex; padding: 2px; gap: 2px; border-radius: 8px; border: 1px solid var(--color-border-default); background: var(--color-bg-base); }
.cx-filter { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 6px; font-size: .76rem; font-weight: 600;
    color: var(--color-text-muted); cursor: pointer; user-select: none; }
.cx-filter:hover { color: var(--color-text-primary); }
.cx-filter b { font-size: .66rem; padding: 0 5px; border-radius: 6px; background: var(--color-bg-surface); color: var(--color-text-secondary); }
.cx-filter.is-on { background: var(--color-primary); color: #fff; }
.cx-filter.is-on b { background: rgba(255, 255, 255, .22); color: #fff; }
.cx-bar .q-btn { min-height: 32px; font-size: .8rem; }

/* Grid */
.cx-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; align-items: stretch; }
.cx-root-drop { display: none; align-items: center; justify-content: center; gap: 6px; min-height: 44px; margin-bottom: 12px; border-radius: 10px;
    font-size: .78rem; color: var(--color-text-secondary); border: 1px dashed var(--color-border-accent); background: var(--color-primary-light); }
body.cx-dragging .cnx .cx-root-drop { display: flex; }
.cx-root-drop .cx-icon { font-size: 16px; color: var(--color-primary); }
.cx-empty { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 48px 16px; text-align: center; color: var(--color-text-muted); }
.cx-empty .cx-icon { font-size: 44px; }
.cx-empty-title { font-size: 1rem; font-weight: 650; color: var(--color-text-secondary); }

/* Cards */
.cx-card { position: relative; display: flex; flex-direction: column; min-width: 0; border-radius: 12px; overflow: hidden;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); box-shadow: inset 3px 0 0 var(--color-success);
    transition: border-color .15s, transform .15s; }
.cx-card:hover { border-color: var(--color-border-accent); }
.cx-card[draggable="true"] { cursor: grab; }
.cx-card.is-off { box-shadow: inset 3px 0 0 var(--color-border-default); }
.cx-card.is-off .cx-head .cx-tile, .cx-card.is-off .cx-flow { opacity: .5; }
.cx-card.is-off .cx-name { color: var(--color-text-secondary); }
.cx-head { display: flex; align-items: flex-start; gap: 10px; padding: 12px 12px 8px 15px; }
.cx-tile { width: 34px; height: 34px; border-radius: 10px; flex: 0 0 auto; display: flex; align-items: center; justify-content: center; font-size: 18px;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.cx-tile .cx-icon { font-size: 18px; }
.cx-tile.is-twitch { background: rgba(145, 70, 255, .14); color: #9146ff; border-color: rgba(145, 70, 255, .35); }
.cx-tile.is-youtube { background: rgba(255, 0, 0, .12); color: #ff3b3b; border-color: rgba(255, 0, 0, .3); }
.cx-tile.is-obs, .cx-tile.is-streamlabs, .cx-tile.is-streamelements { background: var(--color-bg-base); color: var(--color-text-primary);
    border-color: var(--color-border-default); }
.cx-tile.is-folder { background: color-mix(in srgb, var(--color-warning) 14%, transparent); color: var(--color-warning);
    border-color: color-mix(in srgb, var(--color-warning) 35%, transparent); }
.cx-title { flex: 1 1 auto; min-width: 0; }
.cx-name { font-size: .9rem; font-weight: 650; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cx-desc { font-size: .74rem; color: var(--color-text-muted); line-height: 1.35; margin-top: 1px; display: -webkit-box; -webkit-line-clamp: 2;
    -webkit-box-orient: vertical; overflow: hidden; }
.cx-desc.is-empty { font-style: italic; opacity: .7; }
.cx-head .q-toggle { margin-top: 4px; }

.cx-flow { display: flex; flex-direction: column; gap: 5px; padding: 2px 12px 10px 15px; flex: 1 1 auto; }
.cx-flow-row { display: grid; grid-template-columns: 38px minmax(0, 1fr); gap: 6px; align-items: start; }
.cx-flow-key { font-size: .62rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); padding-top: 5px; }
.cx-flow-val { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
.cx-chip { display: inline-flex; align-items: center; gap: 5px; max-width: 100%; padding: 2px 8px 2px 6px; border-radius: 6px; font-size: .72rem; font-weight: 550;
    color: var(--color-text-primary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); min-width: 0; }
.cx-chip > span:last-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cx-chip .cx-icon, .cx-chip .cx-brand { font-size: 13px; flex: 0 0 auto; }
.cx-chip.is-trigger > .cx-icon, .cx-chip.is-trigger > .cx-brand:not(.is-twitch):not(.is-youtube):not(.is-discord) { color: var(--color-info); }
.cx-chip.is-cond > .cx-icon { color: var(--color-warning); }
.cx-chip.is-action > .cx-icon, .cx-chip.is-action > .cx-brand.is-obs { color: var(--color-success); }
.cx-chip.is-none { color: var(--color-text-muted); font-style: italic; }
.cx-more { font-size: .7rem; color: var(--color-text-muted); align-self: center; }

.cx-foot { display: flex; align-items: center; gap: 2px; padding: 6px 8px 6px 15px; border-top: 1px solid var(--color-border-subtle);
    background: color-mix(in srgb, var(--color-bg-base) 45%, transparent); }
.cx-stat { display: inline-flex; align-items: center; gap: 4px; font-size: .72rem; color: var(--color-text-secondary); white-space: nowrap; }
.cx-stat b { color: var(--color-text-primary); font-weight: 650; }
.cx-stat .cx-icon { font-size: 14px; color: var(--color-primary); }
.cx-stat.is-muted, .cx-stat.is-muted .cx-icon { color: var(--color-text-muted); }
.cx-foot .q-btn { color: var(--color-text-secondary); }
.cx-foot .q-btn.cx-open { color: var(--color-primary); min-height: 28px; font-size: .76rem; }

/* Folders */
.cx-folder { box-shadow: inset 3px 0 0 var(--color-warning);
    background: linear-gradient(180deg, color-mix(in srgb, var(--color-warning) 5%, var(--color-bg-surface)), var(--color-bg-surface)); }
.cx-folder.is-drop { border-color: var(--color-warning); }
.cx-members { display: flex; flex-direction: column; gap: 3px; padding: 0 12px 10px 15px; flex: 1 1 auto; cursor: pointer; }
.cx-member { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 7px; background: var(--color-bg-base);
    border: 1px solid var(--color-border-subtle); font-size: .76rem; min-width: 0; }
.cx-member-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-success); flex: 0 0 auto; }
.cx-member.is-off .cx-member-dot { background: var(--color-border-default); }
.cx-member.is-off .cx-member-name { color: var(--color-text-muted); }
.cx-member-icon { font-size: 13px; color: var(--color-text-muted); display: inline-flex; flex: 0 0 auto; }
.cx-member-name { flex: 1 1 auto; min-width: 0; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--color-text-primary); }
.cx-member-trig { font-size: .66rem; color: var(--color-text-muted); white-space: nowrap; flex: 0 0 auto; }
.cx-member-more { font-size: .7rem; color: var(--color-text-muted); padding: 2px 8px; }
.cx-members-empty { font-size: .74rem; color: var(--color-text-muted); padding: 10px 8px; border-radius: 7px; border: 1px dashed var(--color-border-default); text-align: center; }

/* Floating folder window */
.cx-float { display: flex; flex-direction: column; min-height: 0; border-radius: 14px; overflow: hidden;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-accent); box-shadow: 0 24px 60px rgba(0, 0, 0, .45); }
.cx-float-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--color-border-subtle);
    cursor: move; flex: 0 0 auto; user-select: none; touch-action: none; }
.cx-float-head .q-btn { color: var(--color-text-secondary); }
.cx-float-body { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 12px; background: var(--color-bg-base); }
"""

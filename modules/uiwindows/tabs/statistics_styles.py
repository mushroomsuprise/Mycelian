# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the Settings → Statistics dashboard (theme variables only)."""

STATISTICS_CSS = """
.mst { width: 100%; display: flex; flex-direction: column; gap: 14px; color: var(--color-text-primary); }
.mst * { box-sizing: border-box; }
.mst .material-icons { color: inherit; }  /* first: specific icon colors below override */

/* ---- toolbar ------------------------------------------------------------ */
.mst-toolbar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px;
    padding: 10px 12px; border-radius: 10px;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle);
}
.mst-seg { display: inline-flex; flex-wrap: wrap; gap: 2px; padding: 3px; border-radius: 9px;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.mst-seg-btn {
    font: inherit; font-size: 0.8rem; font-weight: 500; line-height: 1;
    padding: 7px 12px; border-radius: 7px; border: none; cursor: pointer;
    background: transparent; color: var(--color-text-secondary);
    display: inline-flex; align-items: center; gap: 6px; transition: background .15s, color .15s;
}
.mst-seg-btn:hover { background: var(--color-hover-overlay); color: var(--color-text-primary); }
.mst-seg-btn.is-active { background: var(--color-primary); color: #fff; font-weight: 600; }
.mst-seg-btn .material-icons { font-size: 16px; }
.mst-range-caption { font-size: 0.78rem; color: var(--color-text-muted); }
.mst-spacer { flex: 1 1 auto; }
.mst-live { display: inline-flex; align-items: center; gap: 8px; font-size: 0.78rem; color: var(--color-text-secondary); }
.mst-live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-text-muted); }
.mst-live.is-on .mst-live-dot { background: var(--color-success); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-success) 25%, transparent);
    animation: mst-pulse 2s ease-in-out infinite; }
@keyframes mst-pulse { 50% { box-shadow: 0 0 0 6px color-mix(in srgb, var(--color-success) 5%, transparent); } }
.mst-date-input { width: 150px; }

/* ---- section nav ------------------------------------------------------------ */
.mst-nav { display: flex; flex-wrap: wrap; gap: 4px; border-bottom: 1px solid var(--color-border-subtle); }
.mst-nav-btn {
    font: inherit; font-size: 0.86rem; font-weight: 500; cursor: pointer;
    background: none; border: none; border-bottom: 2px solid transparent;
    padding: 8px 12px 9px; margin-bottom: -1px; color: var(--color-text-muted);
    display: inline-flex; align-items: center; gap: 6px;
}
.mst-nav-btn:hover { color: var(--color-text-primary); }
.mst-nav-btn.is-active { color: var(--color-primary); border-bottom-color: var(--color-primary); font-weight: 600; }
.mst-nav-btn .material-icons { font-size: 18px; }

/* ---- cards ------------------------------------------------------------ */
.mst-card {
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle);
    border-radius: 10px; padding: 14px 16px; min-width: 0;
}
.mst-card-head { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.mst-card-title { font-size: 0.95rem; font-weight: 600; color: var(--color-text-primary); }
.mst-card-sub { font-size: 0.78rem; color: var(--color-text-muted); margin-top: 2px; }
.mst-grid { display: grid; gap: 12px; width: 100%; }
.mst-grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.mst-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.mst-grid-main { grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); }
.mst-kpis { display: grid; gap: 10px; grid-template-columns: repeat(5, minmax(0, 1fr)); }
.mst-kpis.is-auto { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
@media (max-width: 1250px) { .mst-kpis { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); } }
.mst-card.is-fill { display: flex; flex-direction: column; }
.mst-card.is-fill > .mst-chart { flex: 1 1 auto; min-height: 280px; }
@media (max-width: 1500px) { .mst-grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 1200px) { .mst-grid-main, .mst-grid-2 { grid-template-columns: 1fr; } }

/* ---- stat tiles ------------------------------------------------------------ */
.mst-tile {
    position: relative; text-align: left; font: inherit; color: inherit; cursor: pointer;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle);
    border-radius: 10px; padding: 11px 12px 9px; display: flex; flex-direction: column; gap: 4px;
    transition: border-color .15s, transform .15s, background .15s; min-width: 0;
}
.mst-tile:hover { border-color: var(--color-border-accent); transform: translateY(-1px); }
.mst-tile.is-active { border-color: var(--color-primary);
    background: color-mix(in srgb, var(--color-primary) 7%, var(--color-bg-surface));
    box-shadow: inset 0 0 0 1px var(--color-primary); }
.mst-tile.is-static { cursor: default; }
.mst-tile.is-static:hover { transform: none; border-color: var(--color-border-subtle); }
.mst-tile-label { display: flex; align-items: center; gap: 6px; font-size: 0.76rem; color: var(--color-text-secondary); white-space: nowrap; overflow: hidden; }
.mst-tile-label .material-icons { font-size: 16px; color: var(--color-primary); }
.mst-tile-value { font-size: 1.55rem; font-weight: 650; line-height: 1.15; color: var(--color-text-primary); }
.mst-tile-foot { display: flex; align-items: center; justify-content: space-between; gap: 6px; min-height: 26px; }
.mst-tile-note { font-size: 0.72rem; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mst-spark { flex: 0 0 auto; display: block; }

.mst-delta { display: inline-flex; align-items: center; gap: 2px; font-size: 0.72rem; font-weight: 600; white-space: nowrap; }
.mst-delta .material-icons { font-size: 15px; }
.mst-delta.is-up { color: var(--color-success); }
.mst-delta.is-down { color: var(--color-error); }
.mst-delta.is-flat, .mst-delta.is-none { color: var(--color-text-muted); font-weight: 500; }
.mst-delta.is-new { color: var(--color-info); }

/* ---- hero + small values ------------------------------------------------ */
.mst-hero { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.mst-hero-value { font-size: 2.1rem; font-weight: 700; line-height: 1; }
.mst-kv { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.mst-kv-item { padding: 8px 10px; border-radius: 8px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); min-width: 0; }
.mst-kv-label { font-size: 0.72rem; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mst-kv-value { font-size: 1.1rem; font-weight: 650; color: var(--color-text-primary); }

/* ---- ranked lists ------------------------------------------------------------ */
.mst-rank { display: flex; flex-direction: column; gap: 2px; }
.mst-rank-row {
    display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; align-items: center; gap: 8px;
    padding: 5px 6px; border-radius: 7px; cursor: pointer; position: relative;
}
.mst-rank-row:hover { background: var(--color-hover-overlay); }
.mst-rank-row.is-static { cursor: default; }
.mst-rank-pos { font-size: 0.74rem; font-weight: 700; color: var(--color-text-muted); text-align: center; }
.mst-rank-row:nth-child(1) .mst-rank-pos { color: var(--color-primary); }
.mst-rank-name { font-size: 0.85rem; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--color-text-primary); }
.mst-rank-bar { height: 4px; margin-top: 4px; border-radius: 0 4px 4px 0; background: var(--color-primary); opacity: .85; min-width: 2px; }
.mst-rank-row:not(:first-child) .mst-rank-bar { opacity: .45; }
.mst-rank-value { font-size: 0.82rem; font-weight: 600; color: var(--color-text-secondary); font-variant-numeric: tabular-nums; }
.mst-empty { font-size: 0.82rem; color: var(--color-text-muted); padding: 16px 4px; text-align: center; }
.mst-empty .material-icons { display: block; font-size: 28px; margin-bottom: 4px; opacity: .6; }

/* ---- spotlight ------------------------------------------------------------ */
.mst-spot { display: flex; gap: 12px; align-items: center; padding: 10px 12px; border-radius: 9px; cursor: pointer;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); min-width: 0; }
.mst-spot:hover { border-color: var(--color-border-accent); }
.mst-spot.is-static { cursor: default; }
.mst-avatar { flex: 0 0 auto; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.95rem; color: var(--color-primary);
    background: var(--color-primary-light); border: 1px solid var(--color-border-accent); }
.mst-avatar.is-lg { width: 54px; height: 54px; font-size: 1.35rem; }
.mst-avatar.has-photo { position: relative; overflow: hidden; }
.mst-avatar.has-photo img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; border-radius: 50%; }
.mst-spot-kicker { font-size: 0.7rem; text-transform: uppercase; letter-spacing: .06em; color: var(--color-text-muted); }
.mst-spot-name { font-size: 0.92rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mst-spot-value { font-size: 0.78rem; color: var(--color-text-secondary); }

/* ---- event mix bars ------------------------------------------------------------ */
.mst-bars { display: flex; flex-direction: column; gap: 6px; }
.mst-bar-row { display: grid; grid-template-columns: 150px minmax(0, 1fr) 58px; align-items: center; gap: 10px; cursor: default; padding: 2px 4px; border-radius: 6px; }
.mst-bar-row.is-link { cursor: pointer; }
.mst-bar-row.is-link:hover { background: var(--color-hover-overlay); }
.mst-bar-label { display: flex; align-items: center; gap: 6px; font-size: 0.8rem; color: var(--color-text-secondary); white-space: nowrap; overflow: hidden; }
.mst-bar-label .material-icons { font-size: 15px; color: var(--color-text-muted); }
.mst-bar-track { height: 12px; }
.mst-bar-fill { height: 100%; min-width: 2px; border-radius: 0 4px 4px 0; background: var(--color-primary); }
.mst-bar-value { font-size: 0.8rem; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; color: var(--color-text-primary); }

/* ---- chips / filters ------------------------------------------------------------ */
.mst-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.mst-chip { font: inherit; font-size: 0.74rem; padding: 4px 10px; border-radius: 999px; cursor: pointer;
    background: var(--color-bg-base); color: var(--color-text-secondary); border: 1px solid var(--color-border-subtle);
    display: inline-flex; align-items: center; gap: 5px; }
.mst-chip:hover { border-color: var(--color-border-accent); }
.mst-chip.is-active { background: var(--color-primary-light); border-color: var(--color-primary); color: var(--color-text-primary); }
.mst-chip .material-icons { font-size: 14px; }
.mst-chip-count { color: var(--color-text-muted); font-variant-numeric: tabular-nums; }

/* ---- timeline ------------------------------------------------------------ */
.mst-timeline { display: flex; flex-direction: column; max-height: 420px; overflow-y: auto; }
.mst-tl-row { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; gap: 10px; align-items: center;
    padding: 7px 4px; border-bottom: 1px solid var(--color-border-subtle); }
.mst-tl-row:last-child { border-bottom: none; }
.mst-tl-icon { width: 28px; height: 28px; border-radius: 8px; display: flex; align-items: center; justify-content: center;
    background: var(--color-primary-light); color: var(--color-primary); }
.mst-tl-icon .material-icons { font-size: 16px; }
.mst-tl-main { font-size: 0.84rem; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mst-tl-detail { color: var(--color-text-muted); }
.mst-tl-time { font-size: 0.74rem; color: var(--color-text-muted); white-space: nowrap; text-align: right; }

/* ---- profile ------------------------------------------------------------ */
.mst-profile { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.mst-profile-name { font-size: 1.25rem; font-weight: 700; }
.mst-profile-meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.78rem; color: var(--color-text-muted); margin-top: 3px; }
.mst-profile-meta span { display: inline-flex; align-items: center; gap: 4px; }
.mst-profile-meta .material-icons { font-size: 15px; }

/* ---- export ------------------------------------------------------------ */
.mst-export { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 14px; align-items: start; }
@media (max-width: 1100px) { .mst-export { grid-template-columns: 1fr; } }
.mst-export-preview { position: relative; border-radius: 10px; overflow: hidden; background: var(--color-bg-base);
    border: 1px solid var(--color-border-subtle); min-height: 240px; display: flex; align-items: center; justify-content: center; }
.mst-export-preview img { display: block; width: 100%; height: auto; }
.mst-export-preview.is-portrait img { max-width: 560px; margin: 0 auto; }
.mst-export-preview.is-busy { opacity: .55; }
.mst-option-label { font-size: 0.74rem; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: .05em; margin: 4px 0 6px; }
.mst-path { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.74rem; color: var(--color-text-secondary);
    word-break: break-all; padding: 6px 8px; border-radius: 6px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }

/* The app's light-mode rule paints every p/span/div in the primary text color.
   Inside our buttons the label lives in a wrapper div, so let it inherit the button's color,
   and keep Quasar's white-on-primary buttons white. */
.mst button > .contents { color: inherit; }
body:not(.body--dark) .mst :is(p, span, div, label):not([class]) { color: inherit; }
.mst .q-btn.text-white :is(span, div, i) { color: #fff; }

.mst-chart { width: 100%; }
.mst-muted { color: var(--color-text-muted); font-size: 0.78rem; }
.mst-section-title { font-size: 0.74rem; text-transform: uppercase; letter-spacing: .06em; color: var(--color-text-muted); margin: 2px 0 -4px; }
"""

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the Alerts tab (theme variables only)."""

ALERTS_CSS = """
.alr { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; color: var(--color-text-primary); }
.alr * { box-sizing: border-box; }
.alr .alr-icon { color: inherit; }
.alr button > .contents { color: inherit; }
body:not(.body--dark) .alr :is(p, span, div, label):not([class]) { color: inherit; }
.alr .q-btn.text-white :is(span, div, i) { color: #fff; }
/* Quasar's own rule is in the quasar_importants layer; an earlier layer wins for !important. */
@layer overrides { .alr .q-btn.disabled { opacity: .38 !important; } }

.alr-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.alr-shell { flex: 1 1 auto; min-height: 0; display: grid; grid-template-columns: 232px minmax(0, 1fr); background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle); border-radius: 12px; overflow: hidden; }

/* Alert list */
.alr-rail { background: var(--color-bg-base); border-right: 1px solid var(--color-border-subtle); display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.alr-rail-head { display: flex; gap: 4px; align-items: center; padding: 10px 10px 6px; }
.alr-rail-head .q-field { flex: 1 1 auto; }
.alr-rail-head .q-field__control { min-height: 30px; height: 30px; border-radius: 7px; background: var(--color-bg-surface); }
.alr-rail-head .q-field__marginal { height: 30px; }
.alr-rail-head .q-field__native { min-height: 30px; padding: 0; font-size: .8rem; }
.alr-rail-head .q-btn { color: var(--color-text-secondary); }
.alr-new { display: flex; align-items: center; gap: 6px; margin: 2px 10px 6px; height: 30px; padding: 0 10px; border-radius: 7px; font-size: .78rem;
    font-weight: 600; color: var(--color-primary); border: 1px dashed var(--color-border-accent); background: var(--color-primary-light); cursor: pointer; }
.alr-new:hover { border-style: solid; }
.alr-new.is-active { border-style: solid; box-shadow: inset 3px 0 0 var(--color-primary); }
.alr-new .alr-icon { font-size: 16px; }
.alr-list { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 0 6px 8px; display: flex; flex-direction: column; gap: 2px; }
.alr-item { padding: 7px 9px; border-radius: 8px; cursor: pointer; border: 1px solid transparent; }
.alr-item:hover { background: var(--color-hover-overlay); }
.alr-item.is-active { background: var(--color-bg-surface); border-color: var(--color-border-accent); box-shadow: inset 3px 0 0 var(--color-primary); }
.alr-item.is-off .alr-item-name { color: var(--color-text-muted); }
.alr-item-top { display: flex; align-items: center; gap: 6px; }
.alr-item-name { font-size: .82rem; font-weight: 600; color: var(--color-text-primary); flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.alr-item.is-active .alr-item-name { color: var(--color-primary); }
.alr-kind { font-size: .6rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; padding: 1px 6px; border-radius: 999px; flex: 0 0 auto;
    color: var(--color-info); background: color-mix(in srgb, var(--color-info) 14%, transparent); }
.alr-kind.is-fb { color: var(--color-warning); background: color-mix(in srgb, var(--color-warning) 14%, transparent); }
.alr-cost { display: inline-flex; align-items: center; gap: 2px; font-size: .68rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.alr-cost .alr-icon { font-size: 12px; }
.alr-item-flags { display: flex; gap: 4px; margin-top: 3px; min-height: 13px; }
.alr-flag { font-size: 13px; color: var(--color-text-muted); }
.alr-item.is-active .alr-flag { color: var(--color-text-secondary); }
.alr-off { font-size: .66rem; color: var(--color-text-muted); font-style: italic; }
.alr-empty { padding: 12px 10px; font-size: .76rem; line-height: 1.45; color: var(--color-text-muted); }
.alr-rail-foot { border-top: 1px solid var(--color-border-subtle); padding: 4px 12px; }

/* Detail */
.alr-detail { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.alr-dhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 14px; border-bottom: 1px solid var(--color-border-subtle); }
.alr-dtitle { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; min-width: 0; }
.alr-h { font-size: 1.02rem; font-weight: 700; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 320px; }
.alr-trigger { display: flex; align-items: center; gap: 8px; padding-left: 16px; border-left: 1px solid var(--color-border-subtle); flex-wrap: wrap; }
.alr-trigger .alr-row { min-height: 30px; gap: 12px; }
.alr-trig-label { font-size: .72rem; color: var(--color-text-muted); white-space: nowrap; }
.alr-trigger .q-btn-group { border-radius: 8px; border: 1px solid var(--color-border-default); background: var(--color-bg-base); padding: 2px; box-shadow: none; }
.alr-trigger .q-btn-group .q-btn { min-height: 26px; padding: 0 10px; font-size: .74rem; font-weight: 600; border-radius: 6px !important; color: var(--color-text-muted); }
.alr-trigger .q-btn-group .q-btn.bg-primary { color: #fff; }
.alr-dash { font-size: .72rem; color: var(--color-text-muted); }
.alr-pill { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 9px; border-radius: 999px; font-size: .7rem; font-weight: 600; white-space: nowrap;
    color: var(--color-warning); background: color-mix(in srgb, var(--color-warning) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-warning) 30%, transparent); }
.alr-pill .alr-icon { font-size: 14px; }
.alr-spacer { flex: 1 1 auto; }
.alr-dhead .q-btn { min-height: 30px; font-size: .8rem; }
.alr-dhead .q-btn .q-icon { font-size: 17px; }
.alr-banner { display: flex; gap: 8px; align-items: center; margin: 10px 14px 0; padding: 8px 12px; border-radius: 9px; font-size: .78rem;
    color: var(--color-text-primary); background: color-mix(in srgb, var(--color-error) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-error) 35%, transparent); }
.alr-banner .alr-icon { color: var(--color-error); font-size: 17px; }

.alr-dbody { flex: 1 1 auto; min-height: 0; overflow: auto; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-content: start; }
@media (max-width: 1250px) { .alr-dbody { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .alr-col:nth-child(2) { grid-row: span 2; }
    .alr-col:nth-child(3) { border-left: none; border-top: 1px solid var(--color-border-subtle); } }
.alr-col { min-width: 0; border-left: 1px solid var(--color-border-subtle); }
.alr-col:first-child { border-left: none; }
.alr-sec { padding: 10px 14px; border-top: 1px solid var(--color-border-subtle); }
.alr-sec:first-child { border-top: none; }
.alr-sec-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: .68rem; font-weight: 650; letter-spacing: .06em;
    text-transform: uppercase; color: var(--color-text-muted); }
.alr-sec-head > .alr-icon { font-size: 14px; color: var(--color-primary); }

/* Rows */
.alr-row { display: flex; align-items: center; gap: 10px; min-height: 36px; transition: opacity .15s; }
.alr-row.is-dim, .alr-stack.is-dim, .alr-media.is-dim, .alr-dimc.is-dim { opacity: .45; }
.alr-label { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 6px; font-size: .82rem; color: var(--color-text-primary); }
.alr-label > :first-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.alr-label-r { flex: 0 0 auto; margin-left: auto; padding-left: 6px; }
.alr-row.is-pair > .alr-label:first-child { flex: 0 0 auto; }
.alr-help { display: inline-flex; color: var(--color-text-muted); cursor: help; }
.alr-help .alr-icon { font-size: 14px; }
.alr-tag { font-size: .62rem; font-weight: 600; padding: 1px 6px; border-radius: 999px; white-space: nowrap; color: var(--color-text-secondary);
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); text-transform: none; letter-spacing: 0; }
.alr-ctrl { display: inline-flex; align-items: center; gap: 6px; flex: 0 0 auto; }
.alr-ctrl.is-grow { flex: 1 1 auto; justify-content: flex-end; }
.alr-dimc { display: inline-flex; align-items: center; gap: 6px; transition: opacity .15s; }
.alr-stack { display: flex; flex-direction: column; gap: 4px; padding: 4px 0 6px; transition: opacity .15s; }
.alr :is(.alr-ctrl, .alr-stack, .alr-trigger, .alr-edit) .q-field__control { min-height: 30px; height: 30px; border-radius: 7px; }
.alr :is(.alr-ctrl, .alr-stack, .alr-trigger, .alr-edit) .q-field__marginal { height: 30px; }
.alr :is(.alr-ctrl, .alr-stack, .alr-trigger, .alr-edit) .q-field__native { min-height: 30px; padding: 0; font-size: .8rem; }
.alr :is(.alr-ctrl, .alr-stack, .alr-trigger, .alr-edit) .q-field--labeled .q-field__native { padding-top: 0; }
.alr-num { width: 76px; }
.alr-num-wide { width: 100px; }
.alr-unit { font-size: .72rem; color: var(--color-text-muted); min-width: 22px; }
.alr-slider { flex: 1 1 auto; max-width: 220px; min-width: 100px; }
.alr-pct { font-size: .78rem; font-weight: 600; min-width: 36px; text-align: right; font-variant-numeric: tabular-nums; }
.alr-div { height: 1px; background: var(--color-border-subtle); margin: 6px 0; }

/* File cards */
.alr-media { display: flex; flex-direction: column; margin: 2px 0 4px; border-radius: 9px; background: var(--color-bg-base);
    border: 1px solid var(--color-border-subtle); min-width: 0; transition: opacity .15s; }
.alr-media-main { display: flex; align-items: center; gap: 9px; padding: 7px; min-width: 0; }
.alr-thumb { width: 40px; height: 40px; border-radius: 7px; flex: 0 0 auto; display: flex; align-items: center; justify-content: center; overflow: hidden;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.alr-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.alr-thumb .alr-icon { font-size: 20px; }
.alr-media-text { flex: 1; min-width: 0; }
.alr-media-name { font-size: .8rem; font-weight: 600; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.alr-media-name.is-empty { color: var(--color-text-muted); font-weight: 500; }
.alr-media-path { font-size: .68rem; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.alr-media .q-btn { min-height: 26px; font-size: .74rem; }
.alr-media .q-btn .q-icon { font-size: 15px; }
.alr-media-main > .q-btn.q-btn--round { color: var(--color-text-secondary); }
.alr-edit { display: flex; flex-direction: column; gap: 6px; padding: 0 8px 8px; }
.alr-edit .q-field { width: 100%; }
/* Length / sound count beside the file name, and the ▶ preview buttons */
.alr-media-title { display: flex; align-items: center; gap: 7px; min-width: 0; }
.alr-media-title .alr-media-name { flex: 0 1 auto; min-width: 0; }
.alr-media-meta { flex: 0 0 auto; font-size: .66rem; font-weight: 700; line-height: 1; padding: 3px 6px; border-radius: 5px; white-space: nowrap;
    color: var(--color-text-secondary); background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle);
    font-variant-numeric: tabular-nums; max-width: 60%; overflow: hidden; text-overflow: ellipsis; }
.alr .q-btn.alr-play { color: var(--color-primary); }
.alr .q-btn.alr-play.is-playing { color: #fff; background: var(--color-primary); }
.alr-media-main > .q-btn.q-btn--round.alr-play { color: var(--color-primary); }
.alr-media-main > .q-btn.q-btn--round.alr-play.is-playing { color: #fff; background: var(--color-primary); }
"""

# Alert-type tabs sit outside ``.alr``; this part stays unscoped.
ALERTS_TAB_CSS = """
.alr-tab-count { margin-left: 6px; min-width: 18px; height: 16px; padding: 0 5px; border-radius: 8px; display: inline-flex; align-items: center;
    justify-content: center; font-size: .64rem; font-weight: 700; line-height: 1; color: var(--color-text-secondary);
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.q-tab--active .alr-tab-count { color: var(--color-primary); background: var(--color-primary-light); border-color: var(--color-border-accent); }
"""

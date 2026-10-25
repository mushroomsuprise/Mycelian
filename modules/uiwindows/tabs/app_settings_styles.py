# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for Settings → App Settings (theme variables only)."""

APP_SETTINGS_CSS = """
.aps { width: 100%; color: var(--color-text-primary); }
.aps * { box-sizing: border-box; }
.aps .aps-icon { color: inherit; }
.aps button > .contents { color: inherit; }
body:not(.body--dark) .aps :is(p, span, div, label):not([class]) { color: inherit; }
.aps .q-btn.text-white :is(span, div, i) { color: #fff; }
/* Quasar's own rule is in the quasar_importants layer; an earlier layer wins for !important. */
@layer overrides { .aps .q-btn.disabled { opacity: .38 !important; } }

.aps-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.aps-shell { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 12px; overflow: hidden; }

/* Header */
.aps-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 12px; border-bottom: 1px solid var(--color-border-subtle); }
.aps-title { display: flex; align-items: center; gap: 10px; }
.aps-title-icon { width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.aps-title-icon .aps-icon { font-size: 17px; }
.aps-title-name { font-size: .92rem; font-weight: 650; line-height: 1.15; color: var(--color-text-primary); }
.aps-title-sub { font-size: .72rem; color: var(--color-text-muted); }
.aps-pill { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 9px; margin-left: 6px; border-radius: 999px;
    font-size: .7rem; font-weight: 600; white-space: nowrap; color: var(--aps-state);
    background: color-mix(in srgb, var(--aps-state) 12%, transparent); border: 1px solid color-mix(in srgb, var(--aps-state) 30%, transparent); }
.aps-pill .aps-icon { font-size: 14px; }
.aps-pill.is-ok { --aps-state: var(--color-success); }
.aps-pill.is-warn { --aps-state: var(--color-warning); }
.aps-spacer { flex: 1 1 auto; }
.aps-head .q-btn { min-height: 30px; font-size: .8rem; }
.aps-head .q-btn .q-icon { font-size: 17px; }

/* Columns and sections */
.aps-body { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
@media (max-width: 1150px) { .aps-body { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .aps-body { grid-template-columns: 1fr; } .aps-col { border-left: none; border-top: 1px solid var(--color-border-subtle); } }
.aps-col { min-width: 0; border-left: 1px solid var(--color-border-subtle); }
.aps-col:first-child { border-left: none; }
.aps-sec { padding: 10px 14px; border-top: 1px solid var(--color-border-subtle); }
.aps-sec:first-child { border-top: none; }
.aps-sec-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: .68rem; font-weight: 650; letter-spacing: .06em;
    text-transform: uppercase; color: var(--color-text-muted); }
.aps-sec-head .aps-icon { font-size: 14px; color: var(--color-primary); }

/* One-line rows: label (+ hover help) left, control right */
.aps-row { display: flex; align-items: center; gap: 10px; min-height: 36px; transition: opacity .15s; }
.aps-row.is-dim { opacity: .45; }
.aps-label { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 6px; font-size: .82rem; color: var(--color-text-primary); }
.aps-help { display: inline-flex; color: var(--color-text-muted); cursor: help; }
.aps-help .aps-icon { font-size: 14px; }
.aps-label .q-btn { color: var(--color-text-muted); min-height: 0; padding: 0; }
.aps-label .q-btn .q-icon { font-size: 15px; }
.aps-tag { font-size: .62rem; font-weight: 600; padding: 1px 6px; border-radius: 999px; white-space: nowrap; color: var(--color-text-secondary);
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.aps-ctrl { display: inline-flex; align-items: center; gap: 6px; flex: 0 0 auto; }
.aps-ctrl .q-field__control { min-height: 30px; height: 30px; border-radius: 7px; }
.aps-ctrl .q-field__marginal { height: 30px; }
.aps-ctrl .q-field__native { min-height: 30px; padding: 0; }
.aps-ctrl .q-field { min-width: 0; }
.aps-num { width: 76px; }
.aps-unit { font-size: .72rem; color: var(--color-text-muted); min-width: 34px; }

.aps-sample { display: flex; align-items: baseline; gap: 8px; margin: 2px 0; padding: 6px 10px; border-radius: 7px; font-size: .78rem;
    color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px dashed var(--color-border-default); }
.aps-sample b { font-size: 1rem; color: var(--color-text-primary); }
.aps-foot { display: flex; gap: 6px; align-items: flex-start; margin-top: 4px; font-size: .72rem; line-height: 1.4; color: var(--color-text-muted); }
.aps-foot:empty { display: none; }
.aps-foot .aps-icon { font-size: 14px; color: var(--color-primary); margin-top: 1px; }
.aps-foot b { color: var(--color-text-secondary); font-weight: 600; }

/* Stream Deck plugin */
.aps-plugin { display: flex; align-items: center; gap: 10px; min-height: 36px; flex-wrap: wrap; }
.aps-plugin .q-btn { min-height: 30px; font-size: .8rem; }
.aps-plugin-badge { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px; border-radius: 999px; font-size: .72rem; font-weight: 600;
    white-space: nowrap; color: var(--aps-state); background: color-mix(in srgb, var(--aps-state) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--aps-state) 30%, transparent); }
.aps-plugin-badge .aps-icon { font-size: 14px; }
.aps-plugin-badge.is-ok { --aps-state: var(--color-success); }
.aps-plugin-badge.is-warn { --aps-state: var(--color-warning); }
.aps-plugin-badge.is-muted { --aps-state: var(--color-text-muted); }

/* Notifications panel (full width under the columns) */
.aps-npanel { border-top: 1px solid var(--color-border-subtle); }
.aps-nhead { display: flex; align-items: center; gap: 8px; padding: 10px 14px 0; }
.aps-nhead .aps-sec-head { margin: 0; }
.aps-nhead .q-btn { min-height: 28px; font-size: .76rem; }
.aps-ngrid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 28px; padding: 4px 14px 8px; }
@media (max-width: 900px) { .aps-ngrid { grid-template-columns: 1fr; } }
.aps-nsub { padding: 8px 14px 4px; font-size: .64rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--color-text-muted); }
.aps-levels { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; padding: 0 14px 10px; }
@media (max-width: 900px) { .aps-levels { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.aps-level { --c: var(--color-notify-info); border-radius: 10px; padding: 8px 10px; border: 1px solid var(--color-border-subtle); background: var(--color-bg-base); }
.aps-level.is-error { --c: var(--color-notify-error); } .aps-level.is-warning { --c: var(--color-notify-warning); }
.aps-level.is-success { --c: var(--color-notify-success); }
.aps-level.is-off { opacity: .6; }
.aps-level-top { display: flex; align-items: center; gap: 7px; font-size: .82rem; font-weight: 650; color: var(--color-text-primary); }
.aps-level-top .d { width: 9px; height: 9px; border-radius: 50%; background: var(--c); }
.aps-level-top .aps-spacer { flex: 1; }
.aps-level-secs.q-field { width: 7.5rem !important; }
.aps-level-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 6px; font-size: .72rem; color: var(--color-text-muted); }
.aps-mx-wrap { padding: 0 14px 12px; }
.aps-mx { width: 100%; border-collapse: collapse; font-size: .8rem; }
.aps-mx th { padding: 4px; font-size: .64rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--color-text-muted); text-align: center;
  cursor: pointer; user-select: none; white-space: nowrap; }
.aps-mx th:first-child { text-align: left; cursor: default; }
.aps-mx th .d { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; vertical-align: 1px; }
.aps-mx td { padding: 1px 4px; border-top: 1px solid var(--color-border-subtle); text-align: center; }
.aps-mx td:first-child { text-align: left; }
.aps-mx .mod { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; color: var(--color-text-primary); white-space: nowrap; }
.aps-mx td:first-child { white-space: nowrap; padding-right: 10px; }
.aps-mx .mod .aps-icon { font-size: 16px; color: var(--color-text-muted); }
.aps-mx tr.is-muted .mod { opacity: .55; }
.aps-mx .q-checkbox__inner { font-size: 30px; }
.aps-mx-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 28px; }
@media (max-width: 1100px) { .aps-mx-cols { grid-template-columns: 1fr; } }
.aps-mute-tag { margin-left: 6px; font-size: .62rem; font-weight: 700; padding: 0 6px; border-radius: 999px; color: var(--color-text-muted);
  border: 1px solid var(--color-border-subtle); }
"""

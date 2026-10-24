# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for Settings → App Settings (theme variables only)."""

APP_SETTINGS_CSS = """
.aps { width: 100%; display: flex; flex-direction: column; gap: 14px; color: var(--color-text-primary); }
.aps * { box-sizing: border-box; }
.aps .aps-icon { color: inherit; }
.aps button > .contents { color: inherit; }
body:not(.body--dark) .aps :is(p, span, div, label):not([class]) { color: inherit; }
.aps .q-btn.text-white :is(span, div, i) { color: #fff; }

.aps-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.aps-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; align-items: start; }
@media (max-width: 1150px) { .aps-grid { grid-template-columns: 1fr; } }
.aps-col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }

.aps-card { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 12px; overflow: hidden; min-width: 0; }
.aps-card-head { display: flex; align-items: center; gap: 12px; padding: 14px 18px 12px; }
.aps-card-icon { width: 34px; height: 34px; flex: 0 0 auto; border-radius: 10px; display: flex; align-items: center; justify-content: center;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.aps-card-icon .aps-icon { font-size: 19px; }
.aps-card-title { font-size: 0.98rem; font-weight: 650; color: var(--color-text-primary); }
.aps-card-sub { font-size: 0.76rem; color: var(--color-text-muted); margin-top: 1px; }

.aps-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px 18px;
    padding: 11px 18px; border-top: 1px solid var(--color-border-subtle); transition: opacity .15s; }
.aps-row.is-dim { opacity: .5; }
.aps-row.is-stack { grid-template-columns: 1fr; }
.aps-row-title { font-size: 0.88rem; font-weight: 550; color: var(--color-text-primary); display: flex; align-items: center; gap: 6px; }
.aps-row-desc { font-size: 0.76rem; color: var(--color-text-muted); margin-top: 2px; line-height: 1.4; }
.aps-control { display: flex; align-items: center; gap: 8px; justify-content: flex-end; min-width: 0; }
.aps-control .q-field { min-width: 0; }
.aps-unit { font-size: 0.78rem; color: var(--color-text-muted); white-space: nowrap; }
.aps-tag { font-size: 0.66rem; font-weight: 600; padding: 1px 7px; border-radius: 999px; white-space: nowrap;
    color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }

.aps-note { display: flex; gap: 10px; align-items: flex-start; margin: 0 18px 14px; padding: 9px 12px; border-radius: 9px;
    font-size: 0.78rem; line-height: 1.45; color: var(--color-text-secondary);
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.aps-note .aps-icon { font-size: 17px; color: var(--color-primary); margin-top: 1px; }
.aps-note b { color: var(--color-text-primary); font-weight: 600; }
.aps-note.is-warn .aps-icon { color: var(--color-warning); }

.aps-preview { margin: 0 18px 14px; padding: 12px 14px; border-radius: 9px; background: var(--color-bg-base);
    border: 1px dashed var(--color-border-default); }
.aps-preview-big { font-size: 1.15rem; font-weight: 650; color: var(--color-text-primary); }
.aps-preview-small { font-size: 0.82rem; color: var(--color-text-secondary); margin-top: 2px; }

.aps-plugin { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 4px 18px 16px; }
.aps-plugin-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 0.8rem; font-weight: 600; padding: 4px 11px; border-radius: 999px;
    color: var(--aps-state); background: color-mix(in srgb, var(--aps-state) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--aps-state) 35%, transparent); }
.aps-plugin-badge .aps-icon { font-size: 16px; }
.aps-plugin-badge.is-ok { --aps-state: var(--color-success); }
.aps-plugin-badge.is-warn { --aps-state: var(--color-warning); }
.aps-plugin-badge.is-muted { --aps-state: var(--color-text-muted); }
.aps-plugin-meta { font-size: 0.78rem; color: var(--color-text-muted); display: flex; flex-direction: column; gap: 2px; }
.aps-plugin-meta .is-new { color: var(--color-warning); font-weight: 600; }

.aps-actions { position: sticky; bottom: 0; z-index: 5; display: flex; align-items: center; gap: 10px; justify-content: flex-end;
    padding: 10px 14px; border-radius: 12px; background: var(--color-bg-elevated); border: 1px solid var(--color-border-subtle); }
.aps-dirty { display: inline-flex; align-items: center; gap: 6px; font-size: 0.8rem; color: var(--color-text-muted); margin-right: auto; }
.aps-dirty .aps-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-text-muted); }
.aps-dirty.is-dirty { color: var(--color-warning); font-weight: 600; }
.aps-dirty.is-dirty .aps-dot { background: var(--color-warning); }
"""

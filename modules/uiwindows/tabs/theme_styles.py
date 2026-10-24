# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for Settings → Theme.

The tab chrome uses the applied theme (``--color-*``). Inside ``.thm-mock`` every
``--color-*`` and ``--q-*`` variable is pointed at the previewed theme's
``--preview-color-*`` values, so the mock (and the app's own status footer CSS it
reuses) shows the selected theme before it is applied.
"""

_PREVIEW_VARS = (
    "primary", "primary-hover", "primary-light",
    "bg-base", "bg-elevated", "bg-surface", "bg-overlay",
    "text-primary", "text-secondary", "text-muted", "text-inverse",
    "border-default", "border-subtle", "border-accent",
    "success", "warning", "error", "info",
    "notify-success", "notify-warning", "notify-error", "notify-info",
    "hover-overlay", "active-overlay", "focus-ring",
)

_MOCK_VAR_MAP = "".join(f"--color-{v}: var(--preview-color-{v}); " for v in _PREVIEW_VARS) + (
    "--q-primary: var(--preview-color-primary); --q-positive: var(--preview-color-success); "
    "--q-negative: var(--preview-color-error); --q-warning: var(--preview-color-warning); "
    "--q-info: var(--preview-color-info);"
)

THEME_CSS = """
.thm { width: 100%; color: var(--color-text-primary); }
.thm * { box-sizing: border-box; }
.thm .thm-icon { color: inherit; }
.thm button > .contents { color: inherit; }
body:not(.body--dark) .thm :is(p, span, div, label):not([class]) { color: inherit; }
.thm .q-btn.text-white :is(span, div, i) { color: #fff; }

.thm-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.thm-shell { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 12px; overflow: hidden; }

/* Header: title, picker, state, actions */
.thm-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 12px; border-bottom: 1px solid var(--color-border-subtle); }
.thm-title { display: flex; align-items: center; gap: 10px; margin-right: 6px; }
.thm-title-icon { width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.thm-title-icon .thm-icon { font-size: 17px; }
.thm-title-name { font-size: .92rem; font-weight: 650; color: var(--color-text-primary); line-height: 1.15; }
.thm-title-sub { font-size: .72rem; color: var(--color-text-muted); }
.thm-select { width: 240px; }
.thm-select .q-field__control { min-height: 32px; height: 32px; border-radius: 8px; }
.thm-select .q-field__marginal { height: 32px; }
.thm-select .q-field__native { min-height: 32px; padding: 0; }
.thm-dots { display: inline-flex; align-items: center; }
.thm-dots i { width: 12px; height: 12px; border-radius: 50%; margin-left: -4px; border: 1.5px solid var(--color-bg-base);
    box-shadow: 0 0 0 1px rgba(127, 127, 127, .35); }
.thm-dots i:first-child { margin-left: 0; }
.thm-tags { display: inline-flex; align-items: center; gap: 6px; }
.thm-tag { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 8px; border-radius: 999px; font-size: .7rem; font-weight: 600;
    white-space: nowrap; color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.thm-tag .thm-icon { font-size: 13px; }
.thm-pill { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 9px; border-radius: 999px; font-size: .7rem; font-weight: 600;
    white-space: nowrap; color: var(--thm-state); background: color-mix(in srgb, var(--thm-state) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--thm-state) 30%, transparent); }
.thm-pill .thm-icon { font-size: 14px; }
.thm-pill.is-ok { --thm-state: var(--color-success); }
.thm-pill.is-warn { --thm-state: var(--color-warning); }
.thm-spacer { flex: 1 1 auto; }
.thm-divider { width: 1px; height: 20px; background: var(--color-border-subtle); margin: 0 2px; }
.thm-head .q-btn { min-height: 30px; font-size: .8rem; }
.thm-head .q-btn .q-icon { font-size: 17px; }
/* Quasar's own rule is in the quasar_importants layer; an earlier layer wins for !important. */
@layer overrides { .thm .q-btn.disabled { opacity: .38 !important; } }

/* Body: preview stage + colors panel */
.thm-body { display: grid; grid-template-columns: minmax(0, 1fr) 248px; }
@media (max-width: 900px) { .thm-body { grid-template-columns: 1fr; } .thm-side { border-left: none; border-top: 1px solid var(--color-border-subtle); } }
.thm-stage { padding: 12px; min-width: 0; background: var(--color-bg-base); }
.thm-side { border-left: 1px solid var(--color-border-subtle); padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.thm-side-head { display: flex; align-items: center; gap: 2px; min-height: 26px; font-size: .7rem; font-weight: 650; letter-spacing: .05em;
    text-transform: uppercase; color: var(--color-text-muted); }
.thm-side-head.is-sub { margin-top: 2px; padding-top: 10px; border-top: 1px solid var(--color-border-subtle); }
.thm-side-head .q-btn { color: var(--color-text-secondary); }
.thm-groups { display: flex; flex-direction: column; gap: 6px; }
.thm-grp { display: grid; grid-template-columns: 84px 1fr; align-items: center; gap: 6px; }
.thm-grp-name { font-size: .74rem; color: var(--color-text-secondary); }
.thm-grp-sw { display: flex; gap: 5px; }
.thm-sw { width: 24px; height: 24px; border-radius: 7px; position: relative; cursor: pointer; overflow: hidden; flex: 0 0 auto;
    border: 1px solid rgba(127, 127, 127, .35); transition: transform .12s;
    background-image: linear-gradient(45deg, rgba(127,127,127,.3) 25%, transparent 25%, transparent 75%, rgba(127,127,127,.3) 75%),
        linear-gradient(45deg, rgba(127,127,127,.3) 25%, transparent 25%, transparent 75%, rgba(127,127,127,.3) 75%);
    background-size: 8px 8px; background-position: 0 0, 4px 4px; }
.thm-sw-fill { position: absolute; inset: 0; }
.thm-sw:hover { transform: scale(1.12); }
.thm-sw.is-active { overflow: visible; box-shadow: 0 0 0 2px var(--color-bg-surface), 0 0 0 3.5px var(--color-primary); }
.thm-sw.is-edited::after { content: ''; position: absolute; top: 2px; right: 2px; width: 6px; height: 6px; border-radius: 50%;
    background: var(--color-warning); box-shadow: 0 0 0 1.5px var(--color-bg-surface); }
.thm-focus { display: flex; gap: 9px; align-items: center; padding: 8px; border-radius: 9px; background: var(--color-bg-base);
    border: 1px solid var(--color-border-subtle); }
.thm-focus-chip { width: 30px; height: 30px; border-radius: 8px; flex: 0 0 auto; border: 1px solid rgba(127, 127, 127, .35); position: relative; overflow: hidden;
    background-image: linear-gradient(45deg, rgba(127,127,127,.3) 25%, transparent 25%, transparent 75%, rgba(127,127,127,.3) 75%),
        linear-gradient(45deg, rgba(127,127,127,.3) 25%, transparent 25%, transparent 75%, rgba(127,127,127,.3) 75%);
    background-size: 8px 8px; background-position: 0 0, 4px 4px; }
.thm-focus-name { font-size: .78rem; font-weight: 600; color: var(--color-text-primary); display: flex; align-items: center; gap: 5px; }
.thm-focus-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-warning); }
.thm-focus-val { font-size: .68rem; color: var(--color-text-muted); line-height: 1.35; }
.thm-reads { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
.thm-read { display: flex; align-items: center; gap: 5px; font-size: .72rem; color: var(--color-text-secondary); padding: 4px 6px; border-radius: 6px;
    background: var(--color-bg-base); }
.thm-read b { margin-left: auto; font-weight: 650; font-variant-numeric: tabular-nums; color: var(--color-text-primary); }
.thm-read .thm-icon { font-size: 12px; width: 16px; height: 16px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
    color: #fff; background: var(--thm-read); flex: 0 0 auto; }
.thm-read.is-ok { --thm-read: var(--color-success); }
.thm-read.is-low { --thm-read: var(--color-warning); }
.thm-read.is-bad { --thm-read: var(--color-error); }

/* ---------- Mock app (previewed theme) ---------- */
.thm-mock { __MOCK_VARS__
    background: var(--color-bg-base); color: var(--color-text-primary); border-radius: 10px; padding: 8px 8px 6px;
    border: 1px solid var(--color-border-default); font-size: 12px; line-height: 1.4; overflow: hidden; zoom: .8;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; user-select: none; }
.thm-mock .thm-icon { font-size: 15px; }

.thm-m-tabs { display: flex; align-items: flex-end; gap: 2px; }
.thm-m-tab { padding: 6px 10px; font-size: 11px; font-weight: 500; color: var(--color-text-muted); background: var(--color-bg-surface);
    border-radius: 8px 8px 0 0; border-bottom: 1px solid var(--color-border-subtle); white-space: nowrap; }
.thm-m-tab.is-active { background: var(--color-bg-elevated); color: var(--color-primary); font-weight: 650;
    border: 1px solid var(--color-border-accent); border-bottom: none; margin-bottom: -1px; position: relative; z-index: 2; }
.thm-m-bell { margin-left: auto; width: 28px; height: 28px; margin-bottom: 4px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); color: var(--color-text-secondary); position: relative; }
.thm-m-bell-badge { position: absolute; top: -3px; right: -3px; min-width: 14px; height: 14px; border-radius: 7px; font-size: 9px; font-weight: 700;
    display: flex; align-items: center; justify-content: center; background: var(--color-error); color: #fff; }

.thm-m-shell { background: var(--color-bg-elevated); border: 1px solid var(--color-border-accent); border-radius: 0 10px 10px 10px; padding: 9px 10px 10px; }
.thm-m-subtabs { display: flex; flex-wrap: wrap; gap: 2px; align-items: flex-end; }
.thm-m-subtab { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; font-size: 10px; font-weight: 500; white-space: nowrap;
    color: var(--color-text-muted); background: var(--color-bg-surface); border-radius: 6px 6px 0 0; border-bottom: 1px solid var(--color-border-subtle); }
.thm-m-subtab .thm-icon { font-size: 12px; }
.thm-m-subtab svg { width: 12px; height: 12px; display: block; }
.thm-m-subtab.is-active { color: var(--color-primary); font-weight: 650; background: var(--color-bg-base);
    border: 1px solid var(--color-border-accent); border-bottom: none; margin-bottom: -1px; position: relative; z-index: 2; }
.thm-m-panel { background: var(--color-bg-base); border: 1px solid var(--color-border-accent); border-radius: 0 8px 8px 8px; padding: 10px; }

.thm-m-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 10px; align-items: start; }
@media (max-width: 1000px) { .thm-m-grid { grid-template-columns: 1fr; } }
.thm-m-col { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.thm-m-card { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 10px; overflow: hidden; }
.thm-m-card-head { display: flex; align-items: center; gap: 9px; padding: 9px 11px; }
.thm-m-card-icon { width: 24px; height: 24px; border-radius: 7px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.thm-m-card-icon .thm-icon { font-size: 14px; }
.thm-m-title { font-size: 12px; font-weight: 650; color: var(--color-text-primary); }
.thm-m-sub { font-size: 10px; color: var(--color-text-muted); }
.thm-m-row { display: flex; align-items: center; gap: 10px; padding: 7px 11px; border-top: 1px solid var(--color-border-subtle); }
.thm-m-row-text { flex: 1; min-width: 0; }
.thm-m-row-title { font-size: 11px; font-weight: 550; color: var(--color-text-primary); }
.thm-m-row-desc { font-size: 10px; color: var(--color-text-muted); }
.thm-m-switch { width: 28px; height: 14px; border-radius: 7px; background: var(--color-border-default); position: relative; flex: 0 0 auto; }
.thm-m-switch::after { content: ''; position: absolute; top: -2px; left: -2px; width: 18px; height: 18px; border-radius: 50%; background: #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, .35); }
.thm-m-switch.is-on { background: color-mix(in srgb, var(--q-primary) 70%, transparent); }
.thm-m-switch.is-on::after { left: 12px; background: var(--q-primary); }
.thm-m-select { display: inline-flex; align-items: center; gap: 8px; padding: 4px 6px 4px 9px; border-radius: 6px; font-size: 10.5px;
    color: var(--color-text-primary); background: var(--color-bg-elevated); border: 1px solid var(--color-border-default); }
.thm-m-select .thm-icon { font-size: 14px; color: var(--color-text-muted); }

.thm-m-feed { display: flex; flex-direction: column; padding: 0 6px 6px; gap: 2px; }
.thm-m-event { display: flex; align-items: center; gap: 9px; padding: 6px; border-radius: 7px; }
.thm-m-event.is-hover { background: var(--color-hover-overlay); }
.thm-m-event.is-active { background: var(--color-active-overlay); }
.thm-m-event-icon { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    color: var(--thm-ev); background: color-mix(in srgb, var(--thm-ev) 15%, transparent); }
.thm-m-event-icon .thm-icon { font-size: 13px; }
.thm-m-event-text { flex: 1; min-width: 0; font-size: 11px; color: var(--color-text-secondary); }
.thm-m-event-text b { color: var(--color-text-primary); font-weight: 650; }
.thm-m-time { font-size: 10px; color: var(--color-text-muted); white-space: nowrap; }

.thm-m-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.thm-m-stat { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 10px; padding: 8px 10px; min-width: 0; }
.thm-m-stat-label { font-size: 10px; color: var(--color-text-muted); display: flex; align-items: center; gap: 4px; }
.thm-m-stat-label .thm-icon { font-size: 12px; color: var(--color-primary); }
.thm-m-stat-value { font-size: 17px; font-weight: 700; color: var(--color-text-primary); margin-top: 2px; font-variant-numeric: tabular-nums; }
.thm-m-delta { font-size: 9.5px; font-weight: 650; color: var(--color-success); }
.thm-m-delta.is-down { color: var(--color-error); }
.thm-m-bars { display: flex; align-items: flex-end; gap: 2px; height: 16px; margin-top: 4px; }
.thm-m-bars i { flex: 1; border-radius: 2px 2px 0 0; background: color-mix(in srgb, var(--color-primary) 45%, transparent); }
.thm-m-bars i:last-child { background: var(--color-primary); }

.thm-m-toasts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; padding: 0 11px 10px; }
.thm-m-toast { display: flex; align-items: center; gap: 7px; padding: 6px 8px; border-radius: 7px; font-size: 10.5px; color: var(--color-text-primary);
    background: var(--color-bg-elevated); border: 1px solid var(--color-border-subtle); border-left: 3px solid var(--thm-toast); min-width: 0; }
.thm-m-toast .thm-icon { font-size: 14px; color: var(--thm-toast); }
.thm-m-toast span:last-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.thm-m-toast.is-success { --thm-toast: var(--color-notify-success); }
.thm-m-toast.is-info { --thm-toast: var(--color-notify-info); }
.thm-m-toast.is-warning { --thm-toast: var(--color-notify-warning); }
.thm-m-toast.is-error { --thm-toast: var(--color-notify-error); }

.thm-m-controls { padding: 0 11px 11px; display: flex; flex-direction: column; gap: 8px; }
.thm-m-input { padding: 6px 9px; border-radius: 6px; font-size: 10.5px; color: var(--color-text-primary); background: var(--color-bg-elevated);
    border: 1px solid var(--color-primary); box-shadow: 0 0 0 3px var(--color-focus-ring); }
.thm-m-input-label { font-size: 9.5px; color: var(--color-primary); margin-bottom: 3px; font-weight: 600; }
.thm-m-caret { display: inline-block; width: 1px; height: 11px; background: var(--color-text-primary); vertical-align: -2px; margin-left: 1px; }
.thm-m-buttons { display: flex; flex-wrap: wrap; gap: 5px; }
.thm-m-btn { padding: 4px 9px; border-radius: 5px; font-size: 10px; font-weight: 600; color: #fff; background: var(--thm-btn, var(--color-primary)); }
.thm-m-btn.is-hover { background: var(--color-primary-hover); }
.thm-m-btn.is-outline { background: transparent; color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.thm-m-btn.is-success { --thm-btn: var(--color-success); }
.thm-m-btn.is-warning { --thm-btn: var(--color-warning); }
.thm-m-btn.is-error { --thm-btn: var(--color-error); }
.thm-m-type { display: flex; gap: 10px; flex-wrap: wrap; font-size: 10.5px; align-items: baseline; }
.thm-m-type .is-primary { color: var(--color-text-primary); font-weight: 650; }
.thm-m-type .is-secondary { color: var(--color-text-secondary); }
.thm-m-type .is-muted { color: var(--color-text-muted); }
.thm-m-type .is-link { color: var(--color-primary); text-decoration: underline; }

.thm-mock .service-status-footer { margin-top: 8px; }
""".replace("__MOCK_VARS__", _MOCK_VAR_MAP)

# Theme dropdown options live in a menu outside ``.thm``; this part stays unscoped.
# Per-theme dot colors are added by the tab as ``.thm-optdots[data-thm="…"]`` rules.
THEME_MENU_CSS = """
.thm-optdots { display: inline-flex; align-items: center; }
.thm-optdots i { width: 12px; height: 12px; border-radius: 50%; margin-left: -4px; border: 1.5px solid var(--color-bg-elevated);
    box-shadow: 0 0 0 1px rgba(127, 127, 127, .35); }
.thm-optdots i:first-child { margin-left: 0; }
"""

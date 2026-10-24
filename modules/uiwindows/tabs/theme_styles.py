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
.thm { width: 100%; display: flex; flex-direction: column; gap: 14px; color: var(--color-text-primary); }
.thm * { box-sizing: border-box; }
.thm .thm-icon { color: inherit; }
.thm button > .contents { color: inherit; }
body:not(.body--dark) .thm :is(p, span, div, label):not([class]) { color: inherit; }
.thm .q-btn.text-white :is(span, div, i) { color: #fff; }

.thm-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.thm-card { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 12px; min-width: 0; }
.thm-card-head { display: flex; align-items: center; gap: 12px; padding: 14px 18px 12px; flex-wrap: wrap; }
.thm-card-icon { width: 34px; height: 34px; flex: 0 0 auto; border-radius: 10px; display: flex; align-items: center; justify-content: center;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.thm-card-icon .thm-icon { font-size: 19px; }
.thm-card-text { min-width: 0; flex: 1 1 220px; }
.thm-card-title { font-size: 0.98rem; font-weight: 650; color: var(--color-text-primary); }
.thm-card-sub { font-size: 0.76rem; color: var(--color-text-muted); margin-top: 1px; }
.thm-card-body { padding: 0 18px 16px; }

.thm-picker { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 18px; border-top: 1px solid var(--color-border-subtle); }
.thm-picker .q-field { min-width: 0; }
.thm-spacer { flex: 1 1 auto; }
.thm-tag { display: inline-flex; align-items: center; gap: 5px; font-size: 0.72rem; font-weight: 600; padding: 3px 9px; border-radius: 999px;
    white-space: nowrap; color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.thm-tag .thm-icon { font-size: 14px; }
.thm-tag.is-accent { color: var(--color-primary); background: var(--color-primary-light); border-color: var(--color-border-accent); }

.thm-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; padding: 12px 18px 16px;
    border-top: 1px solid var(--color-border-subtle); }
.thm-chip { display: flex; flex-direction: column; gap: 7px; padding: 7px; border-radius: 10px; cursor: pointer; min-width: 0;
    border: 1px solid var(--color-border-subtle); background: var(--color-bg-base); transition: border-color .15s, transform .15s; }
.thm-chip:hover { border-color: var(--color-border-accent); transform: translateY(-1px); }
.thm-chip.is-selected { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
.thm-chip-thumb { height: 46px; border-radius: 7px; padding: 6px; display: flex; gap: 5px; overflow: hidden; border: 1px solid rgba(127, 127, 127, .25); }
.thm-chip-side { width: 22%; border-radius: 4px; }
.thm-chip-main { flex: 1; display: flex; flex-direction: column; gap: 4px; justify-content: center; border-radius: 4px; padding: 5px; }
.thm-chip-line { height: 4px; border-radius: 2px; }
.thm-chip-btn { width: 42%; height: 7px; border-radius: 3px; margin-top: 2px; }
.thm-chip-foot { display: flex; align-items: center; gap: 6px; min-width: 0; padding: 0 2px; }
.thm-chip-name { font-size: 0.78rem; font-weight: 600; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
.thm-chip-mode { font-size: 14px; color: var(--color-text-muted); }
.thm-chip-applied { font-size: 15px; color: var(--color-success); }

.thm-state { display: inline-flex; align-items: center; gap: 6px; font-size: 0.74rem; font-weight: 600; padding: 3px 10px; border-radius: 999px;
    color: var(--thm-state); background: color-mix(in srgb, var(--thm-state) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--thm-state) 35%, transparent); }
.thm-state .thm-icon { font-size: 15px; }
.thm-state.is-ok { --thm-state: var(--color-success); }
.thm-state.is-warn { --thm-state: var(--color-warning); }

.thm-note { display: flex; gap: 10px; align-items: flex-start; padding: 9px 12px; border-radius: 9px;
    font-size: 0.78rem; line-height: 1.45; color: var(--color-text-secondary);
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.thm-note .thm-icon { font-size: 17px; color: var(--color-primary); margin-top: 1px; }
.thm-note.is-warn .thm-icon { color: var(--color-warning); }
.thm-note b { color: var(--color-text-primary); font-weight: 600; }

.thm-read { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 18px 12px; }
.thm-read-item { display: inline-flex; align-items: center; gap: 7px; padding: 5px 10px 5px 6px; border-radius: 9px; font-size: 0.75rem;
    color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.thm-read-sample { width: 26px; height: 20px; border-radius: 5px; display: flex; align-items: center; justify-content: center;
    font-size: 0.72rem; font-weight: 700; border: 1px solid rgba(127, 127, 127, .25); }
.thm-read-ratio { font-weight: 650; font-variant-numeric: tabular-nums; color: var(--thm-read); }
.thm-read-item .thm-icon { font-size: 15px; color: var(--thm-read); }
.thm-read-item.is-ok { --thm-read: var(--color-success); }
.thm-read-item.is-low { --thm-read: var(--color-warning); }
.thm-read-item.is-bad { --thm-read: var(--color-error); }

.thm-groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 12px; padding: 0 18px 14px; }
.thm-group { border: 1px solid var(--color-border-subtle); border-radius: 10px; background: var(--color-bg-base); padding: 10px 10px 8px; min-width: 0; }
.thm-group-title { font-size: 0.72rem; font-weight: 650; letter-spacing: .04em; text-transform: uppercase; color: var(--color-text-muted); margin: 0 2px 8px; }
.thm-swatches { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.thm-swatch { display: flex; align-items: center; gap: 9px; padding: 5px 7px 5px 5px; border-radius: 8px; cursor: pointer; min-width: 0;
    border: 1px solid transparent; transition: background .12s, border-color .12s; }
.thm-swatch:hover { background: var(--color-hover-overlay); border-color: var(--color-border-subtle); }
.thm-swatch-chip { width: 30px; height: 30px; flex: 0 0 auto; border-radius: 7px; border: 1px solid rgba(127, 127, 127, .35);
    background-image: linear-gradient(45deg, rgba(127,127,127,.25) 25%, transparent 25%, transparent 75%, rgba(127,127,127,.25) 75%),
        linear-gradient(45deg, rgba(127,127,127,.25) 25%, transparent 25%, transparent 75%, rgba(127,127,127,.25) 75%);
    background-size: 10px 10px; background-position: 0 0, 5px 5px; position: relative; overflow: hidden; }
.thm-swatch-fill { position: absolute; inset: 0; }
.thm-swatch-text { min-width: 0; display: flex; flex-direction: column; }
.thm-swatch-label { font-size: 0.78rem; font-weight: 550; color: var(--color-text-primary); display: flex; align-items: center; gap: 5px; }
.thm-swatch-value { font-size: 0.68rem; color: var(--color-text-muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.thm-swatch-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-warning); display: none; }
.thm-swatch.is-edited .thm-swatch-dot { display: inline-block; }

.thm-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 0 18px 16px; }

.thm-actions { position: sticky; bottom: 0; z-index: 5; display: flex; align-items: center; gap: 10px; justify-content: flex-end; flex-wrap: wrap;
    padding: 10px 14px; border-radius: 12px; background: var(--color-bg-elevated); border: 1px solid var(--color-border-subtle); }
.thm-status { display: inline-flex; align-items: center; gap: 6px; font-size: 0.8rem; color: var(--color-text-muted); margin-right: auto; min-width: 0; }
.thm-status .thm-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-success); flex: 0 0 auto; }
.thm-status b { color: var(--color-text-primary); font-weight: 600; }
.thm-status.is-dirty { color: var(--color-warning); font-weight: 600; }
.thm-status.is-dirty .thm-dot { background: var(--color-warning); }

/* ---------- Mock app (previewed theme) ---------- */
.thm-mock-wrap { padding: 0 18px 18px; }
.thm-mock { __MOCK_VARS__
    background: var(--color-bg-base); color: var(--color-text-primary); border-radius: 12px; padding: 12px 12px 10px;
    border: 1px solid var(--color-border-default); font-size: 12px; line-height: 1.4; overflow: hidden;
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
.thm-m-note { display: flex; gap: 7px; align-items: center; margin: 2px 11px 10px; padding: 6px 8px; border-radius: 7px; font-size: 10px;
    color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.thm-m-note .thm-icon { font-size: 13px; color: var(--color-primary); }

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

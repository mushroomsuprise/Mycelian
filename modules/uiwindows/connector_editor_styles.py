# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the New/Edit connector dialog (theme variables only)."""

CONNECTOR_EDITOR_CSS = """
.cxe { width: min(1180px, 96vw); height: min(800px, 92vh); max-width: none !important; display: flex; flex-direction: column; padding: 0;
    gap: 0 !important; align-items: stretch !important; color: var(--color-text-primary); background: var(--color-bg-surface);
    border: 1px solid var(--color-border-accent); border-radius: 14px; overflow: hidden; }
.cxe > * { width: 100%; }
.cxe * { box-sizing: border-box; }
.cxe .ce-icon { color: inherit; }
.cxe button > .contents { color: inherit; }
body:not(.body--dark) .cxe :is(p, span, div, label):not([class]) { color: inherit; }
.cxe .q-btn.text-white :is(span, div, i) { color: #fff; }
@layer overrides { .cxe .q-btn.disabled { opacity: .38 !important; } }

.ce-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }
.ce-brand { display: inline-flex; width: 1em; height: 1em; flex: 0 0 auto; }
.ce-brand svg { width: 100%; height: 100%; display: block; }
.ce-brand.is-obs path { fill: currentColor; }
.ce-spacer { flex: 1 1 auto; }

/* Compact Quasar fields inside the editor */
.cxe .q-field__control { min-height: 30px; border-radius: 7px; }
.cxe .q-field:not(.q-textarea) .q-field__control { height: 30px; }
.cxe .q-field__marginal { height: 30px; }
.cxe .q-field__native { min-height: 30px; padding: 0; font-size: .8rem; }
.cxe .q-textarea .q-field__native { padding: 6px 0; line-height: 1.55; resize: vertical; }
.cxe .q-field--outlined .q-field__control { padding: 0 8px 0 10px; }
.cxe .q-field__append .q-icon { font-size: 18px; }
.cxe .q-field--with-bottom { padding-bottom: 0; }
.cxe .q-field__bottom { display: none; }

/* Header */
.ce-head { display: flex; align-items: center; gap: 12px; padding: 9px 12px; border-bottom: 1px solid var(--color-border-subtle); flex: 0 0 auto; }
.ce-tile { width: 34px; height: 34px; border-radius: 10px; flex: 0 0 auto; display: flex; align-items: center; justify-content: center; font-size: 18px;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.ce-tile .ce-icon { font-size: 18px; }
.ce-tile.is-twitch { background: rgba(145, 70, 255, .14); color: #9146ff; border-color: rgba(145, 70, 255, .35); }
.ce-tile.is-youtube { background: rgba(255, 0, 0, .12); color: #ff3b3b; border-color: rgba(255, 0, 0, .3); }
.ce-tile.is-obs { background: var(--color-bg-base); color: var(--color-text-primary); border-color: var(--color-border-default); }
.ce-tile.is-action { width: 24px; height: 24px; font-size: 14px; border-radius: 7px; color: var(--color-success);
    background: color-mix(in srgb, var(--color-success) 12%, transparent); border-color: color-mix(in srgb, var(--color-success) 30%, transparent); }
.ce-tile.is-action .ce-icon { font-size: 14px; }
.ce-head-text { display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; }
.ce-name-row { display: flex; align-items: center; gap: 8px; }
.ce-kicker { font-size: .6rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--color-primary);
    padding: 1px 6px; border-radius: 5px; background: var(--color-primary-light); flex: 0 0 auto; }
.ce-name-input { flex: 1 1 auto; max-width: 520px; }
.ce-name-input .q-field__control { height: 28px !important; min-height: 28px; padding: 0 6px !important; }
.ce-name-input .q-field__native { font-size: 1rem; font-weight: 700; color: var(--color-text-primary); }
.ce-desc-input { max-width: 640px; }
.ce-desc-input .q-field__control { height: 22px !important; min-height: 22px; padding: 0 6px !important; }
.ce-desc-input .q-field__native { font-size: .74rem; min-height: 22px; color: var(--color-text-secondary); }
.ce-head-text .q-field--borderless .q-field__control, .ce-head-text .q-field--borderless .q-field__control::before { background: transparent !important; }
.ce-head-text .q-field--borderless .q-field__control:hover { outline: 1px dashed var(--color-border-default); }
.ce-onoff { display: inline-flex; align-items: center; gap: 4px; font-size: .78rem; font-weight: 600; color: var(--color-text-secondary); margin-right: 4px; }
.ce-head .q-btn { color: var(--color-text-secondary); }

/* Body */
.ce-body { flex: 1 1 auto; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 250px; }
@media (max-width: 980px) { .ce-body { grid-template-columns: 1fr; } .ce-vars { display: none !important; } }
.ce-flow { min-height: 0; overflow: auto; padding: 10px 12px; background: var(--color-bg-base); }
/* Block stacks, not flex columns: WebKit sizes flex items holding auto-fill grids at their intrinsic width. */
.ce-stack > * + * { margin-top: 6px; }
.ce-step { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 8px; }
.ce-step-rail { display: flex; flex-direction: column; align-items: center; }
.ce-step-rail::after { content: ''; flex: 1; width: 2px; background: var(--color-border-subtle); margin: 4px 0; }
.ce-step:last-child .ce-step-rail::after { display: none; }
.ce-step-n { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: .7rem; font-weight: 700;
    color: var(--color-primary); background: var(--color-primary-light); border: 1px solid var(--color-border-accent); flex: 0 0 auto; }
.ce-step-main { padding: 0 0 10px; min-width: 0; }
.ce-step-head { margin-bottom: 6px; }
.ce-step-head { display: flex; align-items: baseline; gap: 8px; min-height: 22px; padding-top: 2px; }
.ce-step-key { font-size: .66rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--color-primary); }
.ce-step-title { font-size: .88rem; font-weight: 700; color: var(--color-text-primary); }
.ce-step-sub { font-size: .74rem; color: var(--color-text-muted); }
.ce-tag { font-size: .66rem; font-weight: 600; padding: 2px 8px; border-radius: 999px; white-space: nowrap; color: var(--color-text-secondary);
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ce-tag.is-warn { color: var(--color-warning); border-color: color-mix(in srgb, var(--color-warning) 40%, transparent); }
.ce-empty { font-size: .76rem; color: var(--color-text-muted); padding: 10px 12px; border-radius: 10px; border: 1px dashed var(--color-border-default); }

/* Trigger */
.ce-trig { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 7px 8px 7px 10px; border-radius: 10px;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ce-trig-text { flex: 1 1 200px; min-width: 0; }
.ce-trig-name { font-size: .86rem; font-weight: 700; color: var(--color-text-primary); }
.ce-trig-hint { font-size: .7rem; color: var(--color-text-muted); }
.ce-trig-opt { display: inline-flex; align-items: center; gap: 6px; font-size: .74rem; color: var(--color-text-secondary); white-space: nowrap; }
.ce-trig-opt > .ce-icon { font-size: 15px; color: var(--color-text-muted); }
.ce-trig .q-btn { min-height: 28px; font-size: .76rem; }
.ce-trig-settings { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; padding: 8px 10px; border-radius: 10px;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ce-trig-settings .q-btn { min-height: 28px; font-size: .76rem; }
.ce-warnline { display: flex; align-items: flex-start; gap: 6px; font-size: .72rem; line-height: 1.4; color: var(--color-warning); }
.ce-warnline .ce-icon { font-size: 15px; margin-top: 1px; }
.ce-note { display: flex; align-items: flex-start; gap: 6px; font-size: .72rem; line-height: 1.4; color: var(--color-text-muted); }
.ce-note .ce-icon { font-size: 15px; color: var(--color-primary); margin-top: 1px; }
.ce-code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; padding: 4px 8px; border-radius: 6px;
    color: var(--color-text-primary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); word-break: break-all; }
.ce-days { display: inline-flex; gap: 3px; }
.ce-day { font-size: .7rem; font-weight: 700; width: 34px; text-align: center; padding: 4px 0; border-radius: 6px; cursor: pointer; user-select: none;
    color: var(--color-text-muted); border: 1px solid var(--color-border-default); }
.ce-day.is-on { color: #fff; background: var(--color-primary); border-color: var(--color-primary); }

/* Picker (triggers and actions) */
.ce-picker { padding: 8px 10px; border-radius: 10px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ce-picker > * + * { margin-top: 7px; }
.ce-picker-head { display: flex; align-items: center; gap: 8px; }
.ce-picker-head .q-field { width: 240px; }
.ce-pgroup { position: relative; padding-left: 96px; min-height: 30px; }
.ce-pgroup > :first-child { position: absolute; left: 0; top: 0; width: 88px; }
.ce-pgroup-head { display: flex; align-items: center; gap: 6px; font-size: .72rem; font-weight: 700; color: var(--color-text-secondary); margin-top: 6px; }
.ce-pgroup-head .ce-icon, .ce-pgroup-head .ce-brand { font-size: 15px; }
.ce-pgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(128px, 1fr)); gap: 5px; }
.ce-popt { display: flex; flex-direction: column; padding: 5px 8px; border-radius: 8px; cursor: pointer; min-width: 0;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.ce-popt:hover { border-color: var(--color-border-accent); }
.ce-popt.is-selected { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
.ce-popt-title { font-size: .78rem; font-weight: 650; color: var(--color-text-primary); }
.ce-popt-hint { font-size: .66rem; color: var(--color-text-muted); }
.ce-popt-warn { font-size: .62rem; color: var(--color-warning); }

/* Conditions */
.ce-conds { border-radius: 10px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); padding: 4px 8px 6px; }
.ce-cond { display: grid; grid-template-columns: 58px minmax(140px, 200px) minmax(120px, 170px) minmax(120px, 1fr) 28px; gap: 6px; align-items: center; padding: 2px 0; }
.ce-cond.is-head { font-size: .62rem; font-weight: 600; color: var(--color-text-muted); padding: 2px 0 0; }
.ce-cond-word { font-size: .64rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--color-warning); }
.ce-join { display: inline-flex; padding: 2px; gap: 1px; border-radius: 6px; border: 1px solid var(--color-border-default); background: var(--color-bg-base);
    width: fit-content; cursor: pointer; user-select: none; }
.ce-join > * { font-size: .58rem; font-weight: 800; letter-spacing: .04em; padding: 2px 4px; border-radius: 4px; color: var(--color-text-muted); }
.ce-join > .is-on { color: #fff; background: var(--color-warning); }
.ce-or-div { display: flex; align-items: center; gap: 8px; margin: 2px 0; font-size: .6rem; font-weight: 800; letter-spacing: .08em;
    text-transform: uppercase; color: var(--color-warning); }
.ce-or-div::before, .ce-or-div::after { content: ''; flex: 1; height: 1px; background: color-mix(in srgb, var(--color-warning) 40%, transparent); }
.ce-inline { display: flex; gap: 4px; align-items: center; min-width: 0; }
.ce-inline > .q-field { flex: 1 1 auto; min-width: 0; }
.ce-case { font-size: .7rem; font-weight: 700; padding: 5px 7px; border-radius: 6px; cursor: pointer; user-select: none; flex: 0 0 auto;
    color: var(--color-text-muted); border: 1px solid var(--color-border-default); }
.ce-case.is-on { color: var(--color-primary); border-color: var(--color-border-accent); background: var(--color-primary-light); }
.ce-summary { display: flex; align-items: flex-start; gap: 6px; margin-top: 4px; padding: 6px 8px; border-radius: 7px; font-size: .72rem;
    line-height: 1.4; color: var(--color-text-secondary); background: var(--color-bg-base); }
.ce-summary .ce-icon { font-size: 15px; color: var(--color-primary); margin-top: 1px; }
.ce-summary b { color: var(--color-text-primary); font-weight: 600; }
.ce-cond .q-btn, .ce-action-head .q-btn { color: var(--color-text-secondary); }
.ce-cond .q-btn.is-danger, .ce-action-head .q-btn.is-danger { color: var(--color-error); }

/* Actions */
.ce-actions { display: flex; flex-direction: column; gap: 6px; }
.ce-action { border-radius: 10px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); min-width: 0; }
.ce-action.is-open { border-color: var(--color-border-accent); }
.ce-action.is-off .ce-action-text, .ce-action.is-off .ce-tile { opacity: .45; }
.ce-action.is-invalid { border-color: color-mix(in srgb, var(--color-warning) 60%, transparent); }
.ce-action-head { display: flex; align-items: center; gap: 6px; padding: 5px 6px 5px 8px; cursor: pointer; }
.ce-action-n { width: 20px; font-size: .72rem; font-weight: 700; color: var(--color-text-muted); text-align: center; flex: 0 0 auto; }
.ce-action-text { flex: 1 1 auto; min-width: 0; display: flex; align-items: baseline; gap: 8px; }
.ce-action-title { font-size: .82rem; font-weight: 650; white-space: nowrap; color: var(--color-text-primary); }
.ce-action-sum { font-size: .72rem; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.ce-action-sum.is-warn { color: var(--color-warning); }
.ce-delay { display: inline-flex; align-items: center; gap: 3px; font-size: .68rem; font-weight: 600; padding: 2px 7px; border-radius: 999px; white-space: nowrap;
    color: var(--color-info); background: color-mix(in srgb, var(--color-info) 12%, transparent); flex: 0 0 auto; }
.ce-delay .ce-icon { font-size: 13px; }
.ce-action-body { padding: 8px 10px 10px 36px; display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--color-border-subtle); }
.ce-fields { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px 10px; align-items: start; }
.ce-f { display: flex; flex-direction: column; gap: 3px; min-width: 0; grid-column: span 6; }
.ce-f.is-half { grid-column: span 3; }
.ce-f.is-third { grid-column: span 2; }
@media (max-width: 760px) { .ce-f.is-half, .ce-f.is-third { grid-column: span 6; } }
.ce-f-label { display: flex; align-items: center; gap: 6px; font-size: .66rem; font-weight: 600; color: var(--color-text-muted); }
.ce-f-help { font-size: .68rem; color: var(--color-text-muted); line-height: 1.35; }
.ce-f-error { font-size: .68rem; color: var(--color-warning); line-height: 1.35; }
.ce-f-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
.ce-f-row > .q-field { flex: 1 1 auto; min-width: 0; }
.ce-unit { font-size: .72rem; color: var(--color-text-muted); white-space: nowrap; }
.ce-varhost { position: relative; }
.ce-varhost .ce-varbtn { position: absolute; right: 5px; top: 4px; z-index: 2; }
.ce-varhost.is-area .q-field__control { padding-right: 44px !important; }
.ce-varhost:not(.is-area) .q-field__control { padding-right: 40px !important; }
.ce-varbtn { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; font-weight: 700; padding: 2px 7px; border-radius: 6px;
    cursor: pointer; user-select: none; color: var(--color-primary); background: var(--color-primary-light); border: 1px solid var(--color-border-accent); }
.ce-varbtn.q-btn { min-height: 22px; padding: 0 7px; line-height: 20px; }
.ce-varbtn.q-btn .q-btn__content { color: var(--color-primary); }
.ce-preview { margin-top: 2px; }
.ce-var .q-btn { min-height: 20px; width: 20px; }
.ce-preview { display: flex; align-items: flex-start; gap: 6px; font-size: .74rem; line-height: 1.4; padding: 4px 8px; border-radius: 7px;
    color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px dashed var(--color-border-default); white-space: pre-wrap; word-break: break-word; }
.ce-preview .ce-icon { font-size: 14px; color: var(--color-text-muted); margin-top: 1px; }
.ce-preview b { font-weight: 600; color: var(--color-text-muted); white-space: nowrap; }
.ce-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.ce-pill { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600; cursor: pointer;
    user-select: none; color: var(--color-text-muted); border: 1px solid var(--color-border-default); }
.ce-pill.is-on { color: var(--color-text-primary); border-color: var(--color-primary); background: var(--color-primary-light); }
.ce-pill .ce-icon, .ce-pill .ce-brand { font-size: 14px; }
.ce-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 4px 2px 9px; border-radius: 999px; font-size: .74rem;
    color: var(--color-text-primary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.ce-macro { display: grid; grid-template-columns: 22px 120px minmax(0, 1fr) 90px 28px; gap: 6px; align-items: center; }
.ce-macro-n { font-size: .7rem; font-weight: 700; color: var(--color-text-muted); text-align: center; }
.ce-add { display: flex; align-items: center; justify-content: center; gap: 6px; height: 32px; border-radius: 10px; font-size: .82rem; font-weight: 600;
    cursor: pointer; user-select: none; color: var(--color-primary); border: 1px dashed var(--color-border-accent); background: var(--color-primary-light); }
.ce-add:hover { border-style: solid; }
.ce-add .ce-icon { font-size: 16px; }

/* Variables panel and menu */
.ce-vars { border-left: 1px solid var(--color-border-subtle); padding: 10px; min-height: 0; overflow-y: auto; overflow-x: hidden; display: flex; flex-direction: column; gap: 3px; }
.ce-vars-head { display: flex; align-items: center; gap: 6px; font-size: .86rem; font-weight: 700; color: var(--color-text-primary); }
.ce-vars-head .ce-icon { font-size: 17px; color: var(--color-primary); }
.ce-vars .q-field { margin: 4px 0; }
.ce-vgroup { font-size: .62rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--color-text-muted); margin: 6px 0 2px; }
.ce-var { display: flex; align-items: center; gap: 6px; padding: 3px 6px 3px 8px; border-radius: 6px; cursor: pointer; min-width: 0; max-width: 100%; overflow: hidden; }
.ce-var:hover { background: var(--color-hover-overlay); }
.ce-var code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; font-weight: 600; color: var(--color-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 0 1 auto; }
.ce-var-ex { flex: 0 0 auto; margin-left: auto; font-size: .68rem; color: var(--color-text-secondary); max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ce-var .q-btn { color: var(--color-text-muted); }
.ce-vars-empty { display: flex; gap: 8px; font-size: .72rem; line-height: 1.45; color: var(--color-text-muted); padding: 8px; border-radius: 8px;
    border: 1px dashed var(--color-border-default); margin: 4px 0 6px; }
.ce-vars-empty .ce-icon { font-size: 16px; color: var(--color-primary); }
.ce-vars-empty code { color: var(--color-primary); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.ce-vars-tip { font-size: .68rem; color: var(--color-text-muted); line-height: 1.4; margin-top: 8px; }

/* Footer */
.ce-foot { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--color-border-subtle); flex: 0 0 auto; }
.ce-status { display: inline-flex; align-items: center; gap: 6px; font-size: .78rem; font-weight: 600; min-width: 0; }
.ce-status .ce-icon { font-size: 17px; }
.ce-status.is-ok { color: var(--color-success); }
.ce-status.is-warn { color: var(--color-warning); }
.ce-foot .q-btn { min-height: 30px; font-size: .8rem; }
.ce-foot .q-btn .q-icon { font-size: 17px; }
"""

# The insert-variable menu and select popups render outside the dialog; this part stays unscoped.
CONNECTOR_EDITOR_MENU_CSS = """
.ce-vmenu { width: 360px; padding: 6px; }
.ce-vmenu .ce-vm-row { display: grid; grid-template-columns: minmax(0, 120px) minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 5px 8px;
    border-radius: 6px; font-size: .74rem; cursor: pointer; }
.ce-vmenu .ce-vm-row:hover { background: var(--color-primary-light); }
.ce-vmenu code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; font-weight: 600; color: var(--color-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ce-vmenu .ce-vm-label { color: var(--color-text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ce-vmenu .ce-vm-ex { font-size: .68rem; color: var(--color-text-muted); max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ce-vmenu .ce-vm-group { font-size: .62rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--color-text-muted); padding: 6px 8px 2px; }
.ce-vmenu .ce-vm-empty { font-size: .74rem; color: var(--color-text-muted); padding: 8px; }
.ce-fmenu { width: 320px; padding: 6px; }
.ce-vmenu .ce-vm-row.is-fmt { grid-template-columns: minmax(0, 1fr) auto; }
.ce-vmenu .ce-vm-row.is-fmt .ce-vm-ex { max-width: 200px; }
"""

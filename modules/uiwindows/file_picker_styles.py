# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the shared file picker dialog (theme variables only)."""

FILE_PICKER_CSS = """
.fpk { gap: 0 !important; align-items: stretch !important; width: min(1100px, 94vw); height: min(680px, 88vh); max-width: none !important; display: flex; flex-direction: column; padding: 0;
    color: var(--color-text-primary); background: var(--color-bg-surface); border: 1px solid var(--color-border-accent); border-radius: 14px; overflow: hidden; }
.fpk * { box-sizing: border-box; }
.fpk > * { width: 100%; }
.fpk .fpk-icon { color: inherit; }
.fpk button > .contents { color: inherit; }
body:not(.body--dark) .fpk :is(p, span, div, label):not([class]) { color: inherit; }
.fpk .q-btn.text-white :is(span, div, i) { color: #fff; }
@layer overrides { .fpk .q-btn.disabled { opacity: .38 !important; } }

.fpk-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.fpk-head { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-bottom: 1px solid var(--color-border-subtle); flex: 0 0 auto; }
.fpk-hic { width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.fpk-hic .fpk-icon { font-size: 19px; }
.fpk-h { font-size: .98rem; font-weight: 700; color: var(--color-text-primary); }
.fpk-s { font-size: .74rem; color: var(--color-text-muted); }
.fpk-sp { flex: 1 1 auto; }
.fpk-head .q-btn { color: var(--color-text-secondary); }

.fpk-main { flex: 1 1 auto; min-height: 0; display: grid; grid-template-columns: 176px minmax(0, 1fr) 250px; }
@media (max-width: 900px) { .fpk-main { grid-template-columns: 150px minmax(0, 1fr); } .fpk-pv { display: none; } }

/* Places */
.fpk-side { background: var(--color-bg-base); border-right: 1px solid var(--color-border-subtle); padding: 10px 8px; display: flex; flex-direction: column;
    gap: 2px; min-height: 0; overflow: auto; }
.fpk-side-h { font-size: .64rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--color-text-muted); padding: 2px 8px 6px; }
.fpk-place { display: flex; align-items: center; gap: 8px; padding: 7px 8px; border-radius: 7px; font-size: .8rem; color: var(--color-text-secondary);
    cursor: pointer; min-width: 0; }
.fpk-place span:last-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fpk-place:hover { background: var(--color-hover-overlay); }
.fpk-place .fpk-icon { font-size: 16px; color: var(--color-text-muted); flex: 0 0 auto; }
.fpk-place.is-active { background: var(--color-bg-surface); color: var(--color-primary); font-weight: 600; box-shadow: inset 3px 0 0 var(--color-primary); }
.fpk-place.is-active .fpk-icon { color: var(--color-primary); }
.fpk-side-note { margin-top: auto; display: flex; gap: 6px; padding: 8px; border-radius: 8px; font-size: .68rem; line-height: 1.4; color: var(--color-text-muted);
    border: 1px solid var(--color-border-subtle); }
.fpk-side-note .fpk-icon { font-size: 14px; color: var(--color-primary); flex: 0 0 auto; }

/* Centre */
.fpk-center { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.fpk-tool { display: flex; align-items: center; gap: 8px; padding: 9px 12px 6px; flex: 0 0 auto; }
.fpk-tool .q-btn { color: var(--color-text-secondary); }
.fpk-crumbs { display: flex; align-items: center; gap: 2px; min-width: 0; flex: 1 1 auto; overflow: hidden; font-size: .8rem; }
.fpk-crumb { display: inline-flex; align-items: center; gap: 4px; padding: 3px 6px; border-radius: 6px; color: var(--color-text-secondary); white-space: nowrap;
    cursor: pointer; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.fpk-crumb:hover { background: var(--color-hover-overlay); }
.fpk-crumb .fpk-icon { font-size: 15px; }
.fpk-crumb.is-last { color: var(--color-text-primary); font-weight: 650; background: var(--color-bg-base); cursor: default; }
.fpk-crumb-sep { color: var(--color-text-muted); flex: 0 0 auto; }
.fpk-filter { width: 170px; flex: 0 0 auto; }
.fpk-filter .q-field__control { min-height: 28px; height: 28px; border-radius: 7px; }
.fpk-filter .q-field__marginal { height: 28px; }
.fpk-filter .q-field__native { min-height: 28px; padding: 0; font-size: .78rem; }
.fpk-seg { display: inline-flex; gap: 2px; padding: 2px; border-radius: 7px; border: 1px solid var(--color-border-default); background: var(--color-bg-base); flex: 0 0 auto; }
.fpk-seg .q-btn { min-height: 22px; min-width: 26px; padding: 0 4px; border-radius: 5px; color: var(--color-text-muted); }
.fpk-seg .q-btn.is-on { background: var(--color-primary); color: #fff; }
.fpk-tool2 { display: flex; align-items: center; gap: 10px; padding: 0 12px 8px; font-size: .74rem; color: var(--color-text-muted);
    border-bottom: 1px solid var(--color-border-subtle); flex: 0 0 auto; min-height: 34px; }
.fpk-tool2 .q-checkbox__label { font-size: .76rem; color: var(--color-text-secondary); }
.fpk-tool2 .q-btn { min-height: 26px; font-size: .74rem; }
.fpk-tool2 .q-btn .q-icon { font-size: 15px; }

.fpk-body { flex: 1 1 auto; min-height: 0; overflow: auto; }
.fpk-grid { padding: 10px 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 10px; align-content: start; }
.fpk-card { padding: 6px; border-radius: 10px; border: 1px solid var(--color-border-subtle); background: var(--color-bg-base); cursor: pointer; min-width: 0; }
.fpk-card:hover { border-color: var(--color-border-accent); }
.fpk-card.is-selected { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
.fpk-card.is-dim, .fpk-row.is-dim { opacity: .4; }
.fpk-thumb { height: 74px; border-radius: 7px; display: flex; align-items: center; justify-content: center; overflow: hidden; color: var(--color-text-muted);
    background: var(--color-bg-surface); border: 1px dashed var(--color-border-default); }
.fpk-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.fpk-thumb.has-img { border: none; background: repeating-conic-gradient(rgba(127,127,127,.18) 0% 25%, transparent 0% 50%) 50% / 12px 12px; }
.fpk-thumb .fpk-icon { font-size: 24px; }
.fpk-thumb.is-folder { background: color-mix(in srgb, var(--color-warning) 14%, transparent); color: var(--color-warning); border: none; }
.fpk-thumb.is-folder .fpk-icon { font-size: 34px; }
.fpk-thumb.is-ok { color: var(--color-primary); background: var(--color-primary-light); border: 1px solid var(--color-border-accent); }
.fpk-name { font-size: .76rem; font-weight: 600; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--color-text-primary); }
.fpk-meta { display: flex; align-items: center; gap: 5px; font-size: .66rem; color: var(--color-text-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; }
.fpk-type { font-size: .58rem; font-weight: 700; letter-spacing: .04em; padding: 1px 5px; border-radius: 4px; color: var(--color-text-muted);
    background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); flex: 0 0 auto; }
.fpk-type.is-ok { color: var(--color-success); background: color-mix(in srgb, var(--color-success) 12%, transparent);
    border-color: color-mix(in srgb, var(--color-success) 30%, transparent); }

.fpk-list { padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; }
.fpk-row { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 8px; border: 1px solid transparent; font-size: .8rem; cursor: pointer; min-width: 0; }
.fpk-row:hover { background: var(--color-hover-overlay); }
.fpk-row.is-selected { background: var(--color-bg-base); border-color: var(--color-primary); }
.fpk-ricon { width: 28px; height: 28px; border-radius: 7px; display: flex; align-items: center; justify-content: center; color: var(--color-text-muted);
    background: var(--color-bg-base); flex: 0 0 auto; }
.fpk-ricon .fpk-icon { font-size: 16px; }
.fpk-ricon.is-ok { color: var(--color-primary); background: var(--color-primary-light); }
.fpk-ricon.is-folder { color: var(--color-warning); background: color-mix(in srgb, var(--color-warning) 14%, transparent); }
.fpk-rname { flex: 1 1 auto; min-width: 0; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--color-text-primary); }
.fpk-rmeta { font-size: .7rem; color: var(--color-text-muted); min-width: 90px; text-align: right; white-space: nowrap; flex: 0 0 auto; }
.fpk-play { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--color-primary);
    border: 1px solid var(--color-border-accent); flex: 0 0 auto; }
.fpk-play .fpk-icon { font-size: 15px; }
.fpk-chev { color: var(--color-text-muted); flex: 0 0 auto; }
.fpk-ok { display: inline-flex; align-items: center; gap: 3px; font-size: .66rem; font-weight: 700; color: var(--color-success); flex: 0 0 auto; }
.fpk-ok .fpk-icon { font-size: 13px; }
.fpk-empty { padding: 28px 16px; text-align: center; font-size: .8rem; color: var(--color-text-muted); }
.fpk-more { padding: 8px 12px 12px; font-size: .72rem; color: var(--color-text-muted); }

/* Preview */
.fpk-pv { border-left: 1px solid var(--color-border-subtle); padding: 12px; display: flex; flex-direction: column; gap: 10px; background: var(--color-bg-base);
    min-height: 0; overflow: auto; }
.fpk-pv-media { height: 150px; border-radius: 10px; display: flex; align-items: center; justify-content: center; overflow: hidden; flex: 0 0 auto;
    background: var(--color-bg-surface); color: var(--color-text-muted); border: 1px solid var(--color-border-subtle); }
.fpk-pv-media img, .fpk-pv-media video { width: 100%; height: 100%; object-fit: contain; display: block; }
.fpk-pv-media.has-img { background: repeating-conic-gradient(rgba(127,127,127,.18) 0% 25%, transparent 0% 50%) 50% / 14px 14px; }
.fpk-pv-media .fpk-icon { font-size: 40px; }
.fpk-pv-media.is-ok { background: var(--color-primary-light); color: var(--color-primary); border-color: var(--color-border-accent); }
.fpk-pv-media.is-folder { background: color-mix(in srgb, var(--color-warning) 12%, transparent); color: var(--color-warning); border: none; }
.fpk-pv-name { font-size: .88rem; font-weight: 700; word-break: break-all; color: var(--color-text-primary); }
.fpk-pv-rows { display: flex; flex-direction: column; gap: 5px; font-size: .74rem; }
.fpk-pv-rows div { display: flex; justify-content: space-between; gap: 8px; color: var(--color-text-muted); }
.fpk-pv-rows b { color: var(--color-text-primary); font-weight: 600; text-align: right; }
.fpk-pv-list { display: flex; flex-direction: column; gap: 4px; font-size: .74rem; color: var(--color-text-secondary); }
.fpk-pv-list div { display: flex; align-items: center; gap: 6px; min-width: 0; }
.fpk-pv-list div span:last-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fpk-pv-list .fpk-icon { font-size: 14px; color: var(--color-primary); }
.fpk-pv-list .fpk-pv-more { color: var(--color-text-muted); padding-left: 20px; }
.fpk-pv-hint { font-size: .76rem; color: var(--color-text-muted); line-height: 1.45; }
.fpk-pv audio { width: 100%; height: 36px; }
.fpk-vol { display: flex; align-items: center; gap: 8px; padding: 2px 2px 0; }
.fpk-vol-icon .fpk-icon { font-size: 18px; color: var(--color-text-secondary); }
.fpk-vol-slider { flex: 1 1 auto; min-width: 0; }
.fpk-vol-pct { flex: 0 0 auto; min-width: 42px; text-align: right; font-size: .74rem; font-weight: 600; color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums; }

/* Footer */
.fpk-foot { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-top: 1px solid var(--color-border-subtle); flex: 0 0 auto; min-width: 0; }
.fpk-fills { display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1 1 auto; overflow: hidden; }
.fpk-fill-label { display: inline-flex; align-items: center; gap: 4px; font-size: .72rem; color: var(--color-text-muted); white-space: nowrap; flex: 0 0 auto; }
.fpk-fill-label .fpk-icon { font-size: 15px; }
.fpk-fill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 4px 3px 8px; border-radius: 7px; font-size: .7rem; color: var(--color-text-muted);
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); min-width: 0; flex: 0 1 auto; }
.fpk-fill code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .72rem; color: var(--color-text-primary);
    background: var(--color-bg-surface); padding: 2px 6px; border-radius: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.fpk-fill-none { font-size: .74rem; color: var(--color-text-muted); }
.fpk-foot .q-btn { min-height: 30px; font-size: .8rem; flex: 0 0 auto; }
.fpk-foot .q-btn .q-icon { font-size: 17px; }
.fpk-upload { display: none !important; }
"""

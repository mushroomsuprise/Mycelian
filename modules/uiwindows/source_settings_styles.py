# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the Source Settings tab (theme variables only; scoped under ``.ssx``)."""

SOURCE_SETTINGS_CSS = """
.ssx { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; color: var(--color-text-primary);
  font-size: 14px; container-type: inline-size; }
.ssx * { box-sizing: border-box; }
body:not(.body--dark) .ssx :is(p, span, div, label):not([class]) { color: inherit; }
.ssx button > .contents { color: inherit; }
.ssx .q-btn.text-white :is(span, div, i) { color: #fff; }
/* Quasar's own rule is in the quasar_importants layer; an earlier layer wins for !important. */
@layer overrides { .ssx .q-btn.disabled { opacity: .38 !important; } }
.ssx-mi { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
  text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

.ssx-shell { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle); border-radius: 12px; overflow: hidden; }

/* Header */
.ssx-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--color-border-subtle);
  flex-wrap: wrap; flex: 0 0 auto; }
.ssx-tile { width: 34px; height: 34px; border-radius: 9px; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
  background: var(--color-primary-light); border: 1px solid var(--color-border-accent); }
.ssx-tile .ssx-mi { font-size: 19px; color: var(--color-primary); }
.ssx-title { display: flex; flex-direction: column; min-width: 0; }
.ssx-kicker { font-size: .66rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); }
.ssx-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 1px; }
.ssx-picker.q-field { width: 220px; }
.ssx-picker .q-field__control { min-height: 30px; height: 30px; padding: 0 6px 0 10px; border-radius: 8px !important;
  background: var(--color-bg-base) !important; }
.ssx-picker .q-field__marginal { height: 30px; }
.ssx-picker .q-field__native, .ssx-picker .q-field__input { min-height: 30px; padding: 0; font-size: .98rem; font-weight: 650; }
.ssx-picker .q-field__append .q-icon { font-size: 18px; color: var(--color-text-muted); }
.ssx-meta { font-size: .72rem; color: var(--color-text-muted); white-space: nowrap; }
.ssx-pill { --st: var(--color-text-muted); display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px; border-radius: 999px;
  font-size: .7rem; font-weight: 650; white-space: nowrap; color: var(--st);
  background: color-mix(in srgb, var(--st) 12%, transparent); border: 1px solid color-mix(in srgb, var(--st) 30%, transparent); }
.ssx-pill .ssx-mi { font-size: 14px; color: var(--st); }
.ssx-pill.is-warn { --st: var(--color-warning); }
.ssx-pill.is-ok { --st: var(--color-success); }
.ssx-spacer { flex: 1 1 auto; }
.ssx-ibtn { width: 32px; height: 32px; border-radius: 8px; border: 0; padding: 0; background: transparent; color: var(--color-text-muted);
  cursor: pointer; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; }
.ssx-ibtn:hover { color: var(--color-text-primary); background: var(--color-bg-base); }
.ssx-ibtn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 1px; }
.ssx-ibtn.is-danger:hover { color: var(--color-error); }
.ssx-ibtn .q-icon { font-size: 18px; color: inherit; }
.ssx-ibtn.is-small { width: 26px; height: 26px; border-radius: 6px; }
.ssx-ibtn.is-small .q-icon { font-size: 16px; }
.ssx-sep { width: 1px; height: 20px; background: var(--color-border-subtle); flex: 0 0 auto; }
.ssx-tbtn { height: 30px; padding: 0 10px; border-radius: 8px; font: inherit; font-size: .76rem; font-weight: 600; cursor: pointer;
  display: inline-flex; align-items: center; gap: 5px; color: var(--color-text-secondary); background: var(--color-bg-base);
  border: 1px solid var(--color-border-subtle); }
.ssx-tbtn:hover { color: var(--color-text-primary); border-color: var(--color-border-accent); }
.ssx-tbtn .q-icon { font-size: 16px; color: inherit; }
.ssx-head .q-btn { min-height: 32px; font-size: .8rem; }
.ssx-head .q-btn .q-icon { font-size: 17px; }
.ssx-head .help-inline-icon, .ssx-head .help-inline-icon .q-icon { color: var(--color-text-muted); }

/* Body */
.ssx-body { flex: 1 1 auto; min-height: 0; display: flex; }
.ssx-nav { flex: 0 0 220px; width: 220px; min-height: 0; display: flex; flex-direction: column; border-right: 1px solid var(--color-border-subtle); }
.ssx-search { margin: 10px; display: flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px; border-radius: 8px; flex: 0 0 auto;
  background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); color: var(--color-text-muted); }
.ssx-search:focus-within { border-color: var(--color-primary); }
.ssx-search input { border: 0; outline: 0; background: transparent; color: var(--color-text-primary); font: inherit; font-size: .8rem;
  width: 100%; min-width: 0; }
.ssx-search .q-icon { font-size: 17px; color: inherit; }
.ssx-navlist { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 0 6px 8px; }
.ssx-navhead { padding: 6px 8px 4px; font-size: .64rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); }
.ssx-navitem { display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px; border-radius: 6px; font-size: .78rem; cursor: pointer;
  color: var(--color-text-secondary); }
.ssx-navitem .nm { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: inherit; }
.ssx-navitem:hover { background: var(--color-bg-base); color: var(--color-text-primary); }
.ssx-navitem.is-on { background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-text-primary); font-weight: 600; }
.ssx-navitem .n { font-size: .66rem; font-weight: 500; color: var(--color-text-muted); }
.ssx-navitem .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-warning); flex: 0 0 auto; }
.ssx-navnone { padding: 8px; font-size: .74rem; line-height: 1.4; color: var(--color-text-muted); }
.ssx-navfoot { padding: 6px 8px 6px 12px; border-top: 1px solid var(--color-border-subtle); display: flex; align-items: center;
  justify-content: space-between; font-size: .76rem; color: var(--color-text-secondary); flex: 0 0 auto; }

.ssx-split { flex: 1 1 auto; min-width: 0; min-height: 0; display: flex; }
.ssx-editor { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.ssx-jump { display: none; flex: 0 0 auto; margin: 10px 12px 0; height: 32px; padding: 0 28px 0 9px; border-radius: 8px; font: inherit;
  font-size: .8rem; color: var(--color-text-primary); background-color: var(--color-bg-base); border: 1px solid var(--color-border-subtle);
  -webkit-appearance: none; appearance: none; cursor: pointer;
  background-image: linear-gradient(45deg, transparent 50%, var(--color-text-muted) 50%), linear-gradient(135deg, var(--color-text-muted) 50%, transparent 50%);
  background-position: calc(100% - 15px) 14px, calc(100% - 10px) 14px; background-size: 5px 5px; background-repeat: no-repeat; }
.ssx-form { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; position: relative; }

/* Section cards */
.ssx-sec { flex: 0 0 auto; border: 1px solid var(--color-border-subtle); border-radius: 10px; background: var(--color-bg-base); }
.ssx-sec-head { display: flex; align-items: center; gap: 8px; padding: 9px 12px; cursor: pointer; user-select: none; border-radius: 10px; }
.ssx-sec:not(.is-closed) .ssx-sec-head { border-bottom: 1px solid var(--color-border-subtle); border-radius: 10px 10px 0 0; }
.ssx-sec-head:hover { background: color-mix(in srgb, var(--color-primary) 5%, transparent); }
.ssx-sec-title { font-size: .84rem; font-weight: 650; color: var(--color-text-primary); min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.ssx-sec-head .n { font-size: .66rem; font-weight: 700; padding: 0 6px; height: 18px; border-radius: 999px; display: inline-flex;
  align-items: center; flex: 0 0 auto; color: var(--color-text-muted); background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ssx-sec-head .chev { margin-left: auto; font-size: 18px; color: var(--color-text-muted); transition: transform .15s; }
.ssx-sec:not(.is-closed) .ssx-sec-head .chev { transform: rotate(180deg); }
.ssx-sec-body { padding: 0 12px 6px; clip-path: inset(1px -20px 0 -20px); }
.ssx-sec.is-closed .ssx-sec-body { display: none; }

/* Rows */
.ssx-row { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 44px; padding: 6px 0;
  border-top: 1px dashed color-mix(in srgb, var(--color-border-subtle) 75%, transparent); }
.ssx-row.is-stack { flex-direction: column; align-items: stretch; gap: 6px; }
.ssx-row-text { min-width: 0; flex: 1 1 auto; }
.ssx-row-title { display: flex; align-items: center; gap: 6px; min-height: 20px; font-size: .82rem; color: var(--color-text-primary); }
.ssx-row-label { min-width: 0; color: inherit; }
.ssx-row-sub { font-size: .7rem; line-height: 1.35; color: var(--color-text-muted); display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; }
.ssx-row.is-changed::before { content: ''; position: absolute; left: -12px; top: 8px; bottom: 8px; width: 3px; border-radius: 2px;
  background: var(--color-warning); }
.ssx-changed { display: none; font-size: .6rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--color-warning);
  flex: 0 0 auto; }
.ssx-revert { display: none; width: 22px; height: 22px; border-radius: 6px; border: 0; padding: 0; background: transparent;
  color: var(--color-text-muted); cursor: pointer; align-items: center; justify-content: center; flex: 0 0 auto; }
.ssx-revert:hover { color: var(--color-text-primary); background: var(--color-bg-surface); }
.ssx-revert .ssx-mi { font-size: 15px; color: inherit; }
.ssx-row.is-changed .ssx-changed { display: inline; }
.ssx-row.is-changed .ssx-revert { display: inline-flex; }
.ssx-ctrl { flex: 0 0 auto; display: flex; align-items: center; gap: 6px; }
.ssx :is(.ssx-ctrl, .ssx-row.is-stack, .ssx-size) .q-field__bottom { display: none; }
.ssx :is(.ssx-ctrl, .ssx-row.is-stack, .ssx-size) .q-field--with-bottom { padding-bottom: 0; }
.ssx .ssx-ctrl .q-field__control { min-height: 32px; height: 32px; border-radius: 8px !important; padding: 0 9px; }
.ssx .ssx-ctrl .q-field__marginal { height: 32px; }
.ssx .ssx-ctrl .q-field__native, .ssx .ssx-ctrl .q-field__input { min-height: 32px; padding: 0; font-size: .8rem; }
.ssx-num.q-field { width: 92px; }
.ssx-num input { text-align: right; font-variant-numeric: tabular-nums; }
.ssx-num input::-webkit-inner-spin-button, .ssx-num input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.ssx-num input[type=number] { -moz-appearance: textfield; }
.ssx-unit { font-size: .7rem; color: var(--color-text-muted); min-width: 16px; }
.ssx-text.q-field { width: 230px; }
.ssx-text.is-asset.q-field { width: 190px; }
.ssx-select.q-field { width: 180px; }
.ssx-area.q-field { width: 100%; }
.ssx .ssx-area .q-field__control { border-radius: 8px !important; }
.ssx .ssx-area textarea { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .76rem; line-height: 1.5; }
.ssx-slider { width: 150px; }
.ssx-readout { min-width: 40px; text-align: right; font-size: .76rem; font-variant-numeric: tabular-nums; color: var(--color-text-secondary); }
.ssx-thumb { width: 32px; height: 32px; border-radius: 6px; overflow: hidden; flex: 0 0 auto; display: inline-flex; align-items: center;
  justify-content: center; color: var(--color-text-muted); background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ssx-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ssx-thumb .ssx-mi { font-size: 16px; color: inherit; }
.ssx-color { width: 196px; }
.ssx-color .mycelian-color-row { flex-wrap: nowrap; gap: 6px; align-items: center; }
.ssx .ssx-color .q-field__native { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .76rem; }
.ssx-empty { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 36px 16px; text-align: center; }
.ssx-empty .q-icon { font-size: 36px; color: var(--color-text-muted); opacity: .6; }
.ssx-empty-title { font-size: .9rem; font-weight: 650; color: var(--color-text-secondary); }
.ssx-empty-sub { font-size: .74rem; color: var(--color-text-muted); }

/* Divider */
.ssx-divider { flex: 0 0 6px; width: 6px; cursor: col-resize; position: relative; }
.ssx-divider::after { content: ''; position: absolute; top: 0; bottom: 0; left: 2px; width: 2px; background: var(--color-border-subtle); }
.ssx-divider:hover::after { background: var(--color-primary); }

/* Preview */
.ssx-prev { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.ssx-prev-bar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--color-border-subtle);
  flex-wrap: wrap; flex: 0 0 auto; }
.ssx-prev-bar.is-sub { border-bottom: 0; padding-bottom: 0; }
.ssx-prev-title { font-size: .66rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); }
.ssx-chip { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 8px; border-radius: 999px; font-size: .7rem;
  font-weight: 600; white-space: nowrap; color: var(--color-text-secondary); background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.ssx-chip .ssx-mi { font-size: 13px; color: inherit; }
.ssx-chip.is-live { color: var(--color-success); border-color: color-mix(in srgb, var(--color-success) 35%, transparent); }
.ssx-chip .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.ssx-size { display: inline-flex; align-items: center; gap: 4px; font-size: .74rem; color: var(--color-text-muted); }
.ssx-size > .q-icon { font-size: 16px; color: var(--color-text-muted); }
.ssx-size .q-field { width: 68px; }
.ssx .ssx-size .q-field__control { min-height: 26px; height: 26px; padding: 0 7px; border-radius: 7px !important; }
.ssx .ssx-size .q-field__marginal { height: 26px; }
.ssx .ssx-size .q-field__native { min-height: 26px; padding: 0; font-size: .74rem; text-align: right; font-variant-numeric: tabular-nums; }
.ssx-size input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.ssx-zoom { display: inline-flex; align-items: center; border: 1px solid var(--color-border-subtle); border-radius: 8px; overflow: hidden; height: 28px; }
.ssx-zoom button { border: 0; background: var(--color-bg-base); color: var(--color-text-secondary); height: 100%; padding: 0 8px; font: inherit;
  font-size: .74rem; cursor: pointer; }
.ssx-zoom button:hover { color: var(--color-text-primary); background: var(--color-bg-surface); }
.ssx-zoom button + button, .ssx-zoom span { border-left: 1px solid var(--color-border-subtle); }
.ssx-zoom span { min-width: 46px; padding: 0 8px; text-align: center; font-size: .74rem; font-variant-numeric: tabular-nums;
  color: var(--color-text-primary); line-height: 26px; }
.ssx-stage { position: relative; flex: 1 1 auto; min-height: 200px; margin: 10px; border-radius: 10px; overflow: hidden;
  border: 1px solid var(--color-border-subtle); background-color: var(--color-bg-base);
  background-image: linear-gradient(45deg, color-mix(in srgb, var(--color-text-primary) 6%, transparent) 25%, transparent 25%, transparent 75%, color-mix(in srgb, var(--color-text-primary) 6%, transparent) 75%),
    linear-gradient(45deg, color-mix(in srgb, var(--color-text-primary) 6%, transparent) 25%, transparent 25%, transparent 75%, color-mix(in srgb, var(--color-text-primary) 6%, transparent) 75%);
  background-size: 20px 20px; background-position: 0 0, 10px 10px; }
.ssx-stage iframe { border: 0; background: transparent; }
.ssx-frame { position: absolute; left: 0; top: 0; width: 0; height: 0; z-index: 5; pointer-events: none; background: transparent;
  border: 1px dashed color-mix(in srgb, var(--color-primary) 50%, transparent); }
.ssx-stage-msg { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); z-index: 11; max-width: 80%; padding: 8px 12px;
  border-radius: 8px; text-align: center; font-size: .76rem; line-height: 1.4; pointer-events: none; color: var(--color-text-secondary);
  background: color-mix(in srgb, var(--color-bg-surface) 90%, transparent); border: 1px solid var(--color-border-subtle); }
.ssx-mocks { display: flex; align-items: center; gap: 6px; padding: 0 10px 10px; flex: 0 0 auto; flex-wrap: nowrap; overflow-x: auto;
  scrollbar-width: thin; -webkit-mask-image: linear-gradient(to right, #000 92%, transparent); mask-image: linear-gradient(to right, #000 92%, transparent); }
.ssx-mocks-label { flex: 0 0 auto; white-space: nowrap; margin-right: 2px; font-size: .66rem; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: var(--color-text-muted); }
.ssx-mocks-empty { flex: 0 0 auto; white-space: nowrap; font-size: .72rem; color: var(--color-text-muted); }
.ssx-mock { flex: 0 0 auto; white-space: nowrap; height: 28px; padding: 0 10px; border-radius: 999px; font: inherit; font-size: .74rem;
  font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 4px;
  color: color-mix(in srgb, var(--color-primary) 55%, var(--color-text-primary)); background: var(--color-primary-light);
  border: 1px solid var(--color-border-accent); }
.ssx-mock:hover { border-color: var(--color-primary); }
.ssx-mock .q-icon { font-size: 15px; color: inherit; }
.ssx-mock span { color: inherit; }

/* Narrow tabs: the outline becomes a "Jump to section" list above the form. */
@container (max-width: 1250px) {
  .ssx .ssx-nav { display: none; }
  .ssx .ssx-jump { display: block; }
}
"""

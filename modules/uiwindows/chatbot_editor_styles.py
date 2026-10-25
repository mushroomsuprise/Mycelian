# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Extra styles for the chatbot command/event editor (on top of ``CONNECTOR_EDITOR_CSS``)."""

CHATBOT_EDITOR_CSS = """
.ce-box { padding: 8px 10px; border-radius: 10px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.ce-box > * + * { margin-top: 8px; }
.ce-row { display: flex; align-items: center; gap: 8px 10px; flex-wrap: wrap; min-width: 0; }
.ce-row > .q-field { min-width: 0; }
.ce-mini { font-size: .74rem; font-weight: 600; color: var(--color-text-secondary); white-space: nowrap; }
.ce-sep { width: 1px; height: 20px; background: var(--color-border-subtle); flex: 0 0 auto; }
.ce-help-inline { display: inline-flex; align-items: center; gap: 4px; font-size: .72rem; color: var(--color-text-muted); }
.ce-help-inline .ce-icon { font-size: 14px; }

.ce-seg { display: inline-flex; padding: 2px; gap: 2px; border-radius: 8px; border: 1px solid var(--color-border-default); background: var(--color-bg-base); flex: 0 0 auto; }
.ce-seg > div { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 6px; font-size: .76rem; font-weight: 600;
    color: var(--color-text-muted); cursor: pointer; user-select: none; white-space: nowrap; }
.ce-seg > div:hover { color: var(--color-text-primary); }
.ce-seg > div .ce-icon { font-size: 15px; }
.ce-seg > div.is-on { background: var(--color-primary); color: #fff; }

.ce-cmdgrid { display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 8px 12px; align-items: end; }
.ce-cmdgrid > .ce-f { grid-column: auto; }
@media (max-width: 760px) { .ce-cmdgrid { grid-template-columns: 1fr; } }
.ce-bang .q-field__control { height: 34px !important; min-height: 34px; }
.ce-bang .q-field__native { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .95rem !important; font-weight: 700; }
.ce-bang .q-field__prepend { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 1rem; font-weight: 700; color: var(--color-primary);
    height: 34px; padding-right: 2px; }
.ce-chips { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; min-height: 30px; padding: 2px 6px; border-radius: 7px;
    background: var(--color-bg-surface); border: 1px solid var(--color-border-default); }
.ce-chips .q-field { flex: 1 1 120px; min-width: 100px; }
.ce-chips .q-field__control { height: 24px !important; min-height: 24px; padding: 0 2px !important; }
.ce-chips .q-field__native { min-height: 24px; }
.ce-alias { display: inline-flex; align-items: center; gap: 2px; padding: 0 2px 0 8px; border-radius: 999px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .72rem; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.ce-alias .q-btn { color: var(--color-text-muted); min-height: 18px; width: 18px; }

.ce-usage { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .76rem; padding: 4px 10px; border-radius: 7px;
    background: var(--color-bg-base); border: 1px dashed var(--color-border-default); color: var(--color-text-secondary); }
.ce-usage b { color: var(--color-primary); font-weight: 700; }
.ce-usage i { font-style: normal; color: var(--color-warning); }
.ce-arg { display: grid; grid-template-columns: 22px 150px 22px 170px minmax(0, 1fr) 28px; gap: 6px; align-items: center; }
@media (max-width: 760px) { .ce-arg { grid-template-columns: 22px 1fr 22px 1fr 28px; } .ce-arg > :nth-child(5) { grid-column: 2 / -2; } }
.ce-arg-n { font-size: .7rem; font-weight: 700; color: var(--color-text-muted); text-align: center; }
.ce-arrow { font-size: 16px; color: var(--color-text-muted); text-align: center; }
.ce-arg .q-field__prefix, .ce-arg .q-field__suffix { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--color-text-muted); }

.ce-look { display: flex; gap: 10px; align-items: flex-start; padding: 8px 8px 8px 10px; border-radius: 10px; background: var(--color-bg-surface);
    border: 1px solid var(--color-border-accent); }
.ce-look-t { flex: 1 1 auto; min-width: 0; }
.ce-look > .q-btn { flex: 0 0 auto; white-space: nowrap; min-height: 28px; font-size: .76rem; }
.ce-look > .q-btn .q-btn__content { flex-wrap: nowrap; }
.ce-unknown { display: flex; align-items: flex-start; gap: 6px; font-size: .72rem; line-height: 1.4; color: var(--color-warning); }
.ce-unknown .ce-icon { font-size: 15px; margin-top: 1px; }
.ce-unknown code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.ce-look-t > * + * { margin-top: 6px; }
.ce-look-n { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: .86rem; font-weight: 700; color: var(--color-text-primary); }
.ce-look-d { font-size: .72rem; color: var(--color-text-muted); }
.ce-auto { display: inline-flex; align-items: center; gap: 4px; font-size: .66rem; font-weight: 600; padding: 2px 7px; border-radius: 999px;
    color: var(--color-success); background: color-mix(in srgb, var(--color-success) 12%, transparent); }
.ce-auto.is-warn { color: var(--color-warning); background: color-mix(in srgb, var(--color-warning) 12%, transparent); }
.ce-auto .ce-icon { font-size: 12px; }
.ce-gives { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.ce-gives code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .7rem; font-weight: 600; color: var(--color-primary);
    padding: 1px 6px; border-radius: 5px; background: var(--color-primary-light); cursor: pointer; }
.ce-gives em { font-style: normal; font-size: .68rem; color: var(--color-text-muted); }
.ce-adv { display: inline-flex; align-items: center; gap: 4px; font-size: .72rem; color: var(--color-text-muted); }
.ce-adv .ce-icon { font-size: 14px; }
.ce-adv a, .ce-link { color: var(--color-primary); font-weight: 600; cursor: pointer; text-decoration: none; }
.ce-kv { display: grid; grid-template-columns: 170px minmax(0, 1fr) 28px; gap: 6px; align-items: center; }
.ce-auto-param { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .72rem; color: var(--color-success); }

.ce-out { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ce-out .ce-pill { cursor: pointer; }
.ce-out .q-btn, .ce-row .q-btn { min-height: 26px; font-size: .74rem; }
.ce-out .q-btn .q-icon, .ce-row .q-btn .q-icon { font-size: 16px; }
.ce-dc { display: inline-flex; align-items: center; gap: 4px; padding: 1px 2px 1px 8px; border-radius: 999px; font-size: .72rem;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.ce-dc .ce-brand { font-size: 13px; }
.ce-dc .q-btn { color: var(--color-text-muted); min-height: 18px; width: 18px; }
.ce-filter { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: .76rem; color: var(--color-text-secondary); }

.ce-vadd { display: flex; align-items: center; gap: 6px; font-size: .74rem; font-weight: 600; color: var(--color-primary); padding: 4px 8px; border-radius: 6px;
    border: 1px dashed var(--color-border-accent); margin: 2px 0 4px; cursor: pointer; }
.ce-vadd .ce-icon { font-size: 15px; }
.ce-var-tools { display: inline-flex; margin-left: 2px; }

/* custom variable builder */
/* The builder dialog's card carries both classes so it reuses the editor's field styles. */
.cxe.cv { width: min(680px, 94vw); height: auto; max-height: 92vh; }
.cv-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--color-border-subtle); }
.cv-body { padding: 12px; background: var(--color-bg-base); }
.cv-body > * + * { margin-top: 10px; }
.cv-kinds { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 6px; }
.cv-kind { display: flex; flex-direction: column; gap: 2px; padding: 7px 9px; border-radius: 9px; cursor: pointer; background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle); }
.cv-kind:hover { border-color: var(--color-border-accent); }
.cv-kind.is-on { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
.cv-kind-t { display: flex; align-items: center; gap: 6px; font-size: .78rem; font-weight: 700; }
.cv-kind-t .ce-icon { font-size: 16px; color: var(--color-primary); }
.cv-kind-d { font-size: .66rem; color: var(--color-text-muted); }
.cv-builder { padding: 8px 10px; border-radius: 9px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.cv-builder > * + * { margin-top: 8px; }
.cv-expr { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .74rem; color: var(--color-text-muted); word-break: break-all; }
.cv-expr b { color: var(--color-text-primary); font-weight: 600; }
.cv-result { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 8px; font-size: .78rem;
    background: color-mix(in srgb, var(--color-success) 8%, var(--color-bg-surface)); border: 1px solid color-mix(in srgb, var(--color-success) 30%, transparent); }
.cv-result b { color: var(--color-success); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-word; }
.cv-result.is-error { background: color-mix(in srgb, var(--color-error) 10%, var(--color-bg-surface)); border-color: color-mix(in srgb, var(--color-error) 45%, transparent); }
.cv-result.is-error b, .cv-result.is-error .ce-icon { color: var(--color-error); }
.cv-result .ce-icon { font-size: 16px; color: var(--color-success); }
.cv-foot { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--color-border-subtle); }
"""

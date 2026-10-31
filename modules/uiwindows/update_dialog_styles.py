# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for the update / older-versions window (theme variables only)."""

UPDATE_CSS = """
.upd { width: 580px; max-width: calc(100vw - 32px); border-radius: 16px; overflow: hidden; display: flex; flex-direction: column;
    max-height: calc(100vh - 48px); background: var(--color-bg-surface); color: var(--color-text-primary);
    border: 1px solid var(--color-border-accent);
    box-shadow: 0 24px 64px rgba(0, 0, 0, .45), 0 0 0 1px color-mix(in srgb, var(--color-primary) 10%, transparent); }
.upd * { box-sizing: border-box; }
body:not(.body--dark) .upd :is(p, span, div, label):not([class]) { color: inherit; }
.upd .q-btn.text-white :is(span, div, i) { color: #fff; }
.upd-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; display: inline-block; }

/* ---- header ------------------------------------------------------------------ */
.upd-head { flex: 0 0 auto; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 14px; align-items: center; padding: 18px 20px 16px;
    background: radial-gradient(120% 160% at 0% 0%, color-mix(in srgb, var(--color-primary) 20%, transparent), transparent 60%), var(--color-bg-surface);
    border-bottom: 1px solid var(--color-border-subtle); }
.upd-logo { width: 48px; height: 48px; border-radius: 13px; display: block; object-fit: cover;
    box-shadow: 0 0 0 1px var(--color-border-accent), 0 6px 18px color-mix(in srgb, var(--color-primary) 30%, transparent); }
.upd-badge { width: 48px; height: 48px; border-radius: 13px; display: flex; align-items: center; justify-content: center;
    background: color-mix(in srgb, var(--upd-state, var(--color-primary)) 16%, transparent); color: var(--upd-state, var(--color-primary));
    border: 1px solid color-mix(in srgb, var(--upd-state, var(--color-primary)) 35%, transparent); }
.upd-badge .upd-icon { font-size: 26px; }
.upd-badge.is-error { --upd-state: var(--color-error); }
.upd-badge.is-ok { --upd-state: var(--color-success); }
.upd-title { font-size: 1.15rem; font-weight: 700; line-height: 1.2; color: var(--color-text-primary); }
.upd-sub { font-size: .8rem; color: var(--color-text-secondary); margin-top: 3px; }
.upd-x { width: 32px; height: 32px; border-radius: 50%; border: 0; padding: 0; background: transparent; cursor: pointer;
    display: flex; align-items: center; justify-content: center; color: var(--color-text-muted); }
.upd-x:hover { background: var(--color-hover-overlay); color: var(--color-text-primary); }
.upd-x .upd-icon { font-size: 20px; }
.upd-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.upd-chip { font-size: .72rem; display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px; white-space: nowrap;
    background: var(--color-bg-base); color: var(--color-text-secondary); border: 1px solid var(--color-border-subtle); }
.upd-chip .upd-icon { font-size: 14px; color: var(--color-text-muted); }
.upd-chip b { color: var(--color-text-primary); font-weight: 600; }
.upd-chip.is-accent { background: var(--color-primary-light); border-color: var(--color-border-accent); color: var(--color-text-primary); }
.upd-chip.is-accent .upd-icon { color: var(--color-primary); }

/* ---- body ---------------------------------------------------------------------- */
.upd-body { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.upd-sect { font-size: .7rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); }
.upd-notes { max-height: 230px; overflow: auto; padding: 12px 14px; border-radius: 10px; background: var(--color-bg-base);
    border: 1px solid var(--color-border-subtle); font-size: .84rem; line-height: 1.5; color: var(--color-text-secondary); word-break: break-word; }
.upd-notes :is(h2, h3, h4) { margin: 10px 0 4px; font-size: .84rem; line-height: 1.4; letter-spacing: normal; font-weight: 700; color: var(--color-text-primary); }
.upd-notes :is(h2, h3, h4, p, ul):first-child { margin-top: 0; }
.upd-notes p { margin: 4px 0; }
.upd-notes ul { margin: 0; padding-left: 18px; list-style: disc; }
.upd-notes li { margin: 2px 0; }
.upd-notes strong { color: var(--color-text-primary); }
.upd-notes a { color: var(--color-primary); }
.upd-notes code { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: .76rem; padding: 0 4px; border-radius: 4px;
    background: var(--color-bg-surface); color: var(--color-text-primary); }
.upd-empty-notes { font-size: .8rem; color: var(--color-text-muted); }
.upd-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 560px) { .upd-facts { grid-template-columns: 1fr; } }
.upd-fact { display: flex; gap: 8px; align-items: flex-start; padding: 9px 10px; border-radius: 10px; background: var(--color-bg-base);
    border: 1px solid var(--color-border-subtle); font-size: .74rem; color: var(--color-text-secondary); line-height: 1.35; }
.upd-fact .upd-icon { font-size: 17px; color: var(--color-primary); margin-top: 1px; }
.upd-fact b { display: block; color: var(--color-text-primary); font-weight: 600; font-size: .78rem; }

.upd-callout { display: flex; gap: 10px; padding: 10px 12px; border-radius: 10px; font-size: .78rem; line-height: 1.45; color: var(--color-text-secondary);
    background: color-mix(in srgb, var(--color-info) 8%, var(--color-bg-base)); border: 1px solid color-mix(in srgb, var(--color-info) 32%, transparent); }
.upd-callout > .upd-icon { font-size: 19px; color: var(--color-info); margin-top: 1px; }
.upd-callout b { color: var(--color-text-primary); }
.upd-callout ul { margin: 4px 0 0; padding-left: 16px; list-style: disc; }
.upd-callout code { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: .72rem; color: var(--color-text-primary); }

.upd-loading { display: flex; align-items: center; gap: 12px; padding: 22px 6px; font-size: .86rem; color: var(--color-text-secondary); }
.upd-loading .upd-icon { font-size: 22px; color: var(--color-primary); }
.upd-empty { text-align: center; padding: 18px 8px; font-size: .84rem; color: var(--color-text-muted); }
.upd-empty .upd-icon { display: block; font-size: 30px; margin-bottom: 6px; opacity: .6; }

/* ---- older versions ------------------------------------------------------------------ */
.upd-vlist { display: flex; flex-direction: column; gap: 6px; max-height: 262px; overflow: auto; }
.upd-ver { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; gap: 4px 12px; align-items: center; padding: 10px 12px; border-radius: 10px;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); cursor: pointer; transition: border-color .15s, background .15s; }
.upd-ver:hover { border-color: var(--color-border-accent); }
.upd-radio { width: 18px; height: 18px; border-radius: 50%; border: 2px solid var(--color-border-default); display: flex; align-items: center; justify-content: center; }
.upd-ver.is-on { border-color: var(--color-primary); background: color-mix(in srgb, var(--color-primary) 8%, var(--color-bg-base)); }
.upd-ver.is-on .upd-radio { border-color: var(--color-primary); }
.upd-ver.is-on .upd-radio::after { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--color-primary); }
.upd-ver.is-current { opacity: .55; cursor: default; }
.upd-ver.is-current:hover { border-color: var(--color-border-subtle); }
.upd-ver-name { font-size: .9rem; font-weight: 600; display: flex; align-items: center; gap: 8px; color: var(--color-text-primary); }
.upd-ver-meta { font-size: .74rem; color: var(--color-text-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.upd-tag { font-size: .66rem; font-weight: 600; padding: 1px 7px; border-radius: 999px; background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle); color: var(--color-text-secondary); }
.upd-tag.is-accent { background: var(--color-primary-light); border-color: var(--color-border-accent); color: var(--color-text-primary); }
details.upd-ver-notes { grid-column: 2 / -1; font-size: .76rem; color: var(--color-text-secondary); line-height: 1.45; }
details.upd-ver-notes > summary { list-style: none; cursor: pointer; color: var(--color-primary); display: inline-flex; align-items: center; gap: 3px; font-size: .74rem; }
details.upd-ver-notes > summary::-webkit-details-marker { display: none; }
details.upd-ver-notes > summary .upd-icon { font-size: 15px; transition: transform .15s; }
details.upd-ver-notes[open] > summary .upd-icon { transform: rotate(180deg); }
details.upd-ver-notes .upd-notes { margin-top: 6px; max-height: 160px; padding: 8px 10px; font-size: .76rem; }

/* ---- progress ------------------------------------------------------------------------- */
.upd-overall { padding: 14px; border-radius: 12px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.upd-overall-top { display: flex; align-items: baseline; gap: 10px; }
.upd-pct { font-size: 1.9rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; color: var(--color-text-primary); }
.upd-pct-wrap { display: flex; align-items: baseline; gap: 1px; }
.upd-pct-sign { font-size: .95rem; color: var(--color-text-muted); font-weight: 600; }
.upd-overall-label { font-size: .78rem; color: var(--color-text-secondary); }
.upd-overall-label b { color: var(--color-text-primary); font-weight: 600; }
.upd-eta { margin-left: auto; text-align: right; font-size: .74rem; color: var(--color-text-muted); }
.upd-eta-clock { font-size: .95rem; font-weight: 600; color: var(--color-text-primary); font-variant-numeric: tabular-nums; }
.upd-eta.is-error { color: var(--color-error); }
.upd-bar { position: relative; height: 10px; border-radius: 999px; background: color-mix(in srgb, var(--color-text-primary) 8%, transparent); overflow: hidden; margin-top: 12px; }
.upd-bar-fill { position: absolute; top: 0; bottom: 0; left: 0; border-radius: inherit; transition: width .25s ease;
    background: linear-gradient(90deg, var(--color-primary), color-mix(in srgb, var(--color-primary) 60%, white)); }
.upd-bar.is-error .upd-bar-fill { background: var(--color-error); }
.upd-seg { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--color-bg-base); }
.upd-bar.is-thin { height: 6px; margin-top: 8px; }
.upd-bar.is-thin .upd-bar-fill { background: var(--color-primary); }

.upd-steps { display: flex; flex-direction: column; gap: 2px; }
.upd-step { display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; gap: 0 10px; align-items: center; padding: 7px 10px; border-radius: 9px;
    font-size: .82rem; color: var(--color-text-muted); border: 1px solid transparent; }
.upd-dot { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    border: 1.5px solid var(--color-border-default); font-size: .68rem; font-weight: 700; }
.upd-dot .upd-icon { font-size: 15px; }
.upd-step.is-done { color: var(--color-text-secondary); }
.upd-step.is-done .upd-dot { background: color-mix(in srgb, var(--color-success) 16%, transparent);
    border-color: color-mix(in srgb, var(--color-success) 45%, transparent); color: var(--color-success); }
.upd-step.is-active { background: color-mix(in srgb, var(--color-primary) 8%, var(--color-bg-base)); border-color: var(--color-border-accent);
    color: var(--color-text-primary); padding: 9px 10px 10px; }
.upd-step.is-active .upd-dot { border-color: var(--color-primary); color: var(--color-primary); }
.upd-step.is-error { background: color-mix(in srgb, var(--color-error) 8%, var(--color-bg-base));
    border-color: color-mix(in srgb, var(--color-error) 40%, transparent); color: var(--color-text-primary); }
.upd-step.is-error .upd-dot { border-color: var(--color-error); color: var(--color-error); background: color-mix(in srgb, var(--color-error) 14%, transparent); }
.upd-step-right { font-size: .74rem; font-variant-numeric: tabular-nums; color: var(--color-text-muted); white-space: nowrap; }
.upd-step.is-active .upd-step-right { color: var(--color-text-primary); font-weight: 600; }
.upd-step.is-error .upd-step-right { color: var(--color-error); }
.upd-step-detail { grid-column: 2 / -1; min-width: 0; }
.upd-step-file { font-size: .74rem; color: var(--color-text-secondary); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.upd-step.is-error .upd-step-file { white-space: normal; }
.upd-step-meta { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 7px; font-size: .72rem; color: var(--color-text-muted); font-variant-numeric: tabular-nums; }
.upd-step-meta span { display: inline-flex; align-items: center; gap: 4px; }
.upd-step-meta .upd-icon { font-size: 14px; }
.upd-step-meta b { color: var(--color-text-primary); font-weight: 600; }
.upd-spin { animation: upd-spin 1s linear infinite; }
@keyframes upd-spin { to { transform: rotate(360deg); } }

/* ---- footer ------------------------------------------------------------------------- */
.upd-foot { flex: 0 0 auto; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 12px 20px 16px; border-top: 1px solid var(--color-border-subtle); }
.upd-foot > :first-child { margin-right: auto; }
.upd-note { font-size: .74rem; color: var(--color-text-muted); display: inline-flex; gap: 5px; align-items: center; }
.upd-note .upd-icon { font-size: 15px; }
.upd-foot .upd-link { color: var(--color-text-muted); font-size: .76rem; }
"""

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stylesheet for Settings → About (theme variables only)."""

ABOUT_CSS = """
.abt { width: 100%; display: flex; flex-direction: column; gap: 14px; color: var(--color-text-primary); }
.abt * { box-sizing: border-box; }
.abt .abt-icon { color: inherit; }  /* first: specific icon colors below override */
.abt button > .contents { color: inherit; }
body:not(.body--dark) .abt :is(p, span, div, label):not([class]) { color: inherit; }
.abt .q-btn.text-white :is(span, div, i) { color: #fff; }

.abt-card { background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); border-radius: 12px; padding: 16px 18px; min-width: 0; }
.abt-card-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.abt-card-title { font-size: 1rem; font-weight: 600; color: var(--color-text-primary); }
.abt-card-sub { font-size: 0.78rem; color: var(--color-text-muted); margin-top: 2px; }
.abt-spacer { flex: 1 1 auto; }
.abt-icon { font-family: 'Material Icons'; font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased; }

/* ---- hero ---------------------------------------------------------------- */
.abt-hero {
    position: relative; overflow: hidden;
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 18px; align-items: center;
    background:
        radial-gradient(120% 140% at 0% 0%, color-mix(in srgb, var(--color-primary) 16%, transparent), transparent 55%),
        var(--color-bg-surface);
    border: 1px solid var(--color-border-accent); border-radius: 14px; padding: 20px 22px;
}
@media (max-width: 1000px) { .abt-hero { grid-template-columns: auto minmax(0, 1fr); } .abt-hero-actions { grid-column: 1 / -1; } }
.abt-logo { width: 68px; height: 68px; border-radius: 18px; display: block; object-fit: cover;
    box-shadow: 0 0 0 1px var(--color-border-accent), 0 8px 24px color-mix(in srgb, var(--color-primary) 30%, transparent); }
.abt-logo-fallback { display: flex; align-items: center; justify-content: center; font-size: 2rem; font-weight: 800;
    color: var(--color-primary); background: var(--color-primary-light); }
.abt-name { font-size: 1.5rem; font-weight: 750; line-height: 1.1; color: var(--color-text-primary); }
.abt-tagline { font-size: 0.82rem; color: var(--color-text-secondary); margin-top: 3px; }
.abt-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.abt-chip {
    font: inherit; font-size: 0.74rem; display: inline-flex; align-items: center; gap: 5px; white-space: nowrap;
    padding: 3px 9px; border-radius: 999px; background: var(--color-bg-base); color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
}
.abt-chip .abt-icon { font-size: 14px; color: var(--color-text-muted); }
.abt-chip b { color: var(--color-text-primary); font-weight: 600; }
.abt-chip.is-copy { cursor: pointer; }
.abt-chip.is-copy:hover { border-color: var(--color-border-accent); }
.abt-chip.is-copy:hover .abt-icon { color: var(--color-primary); }
.abt-chip code { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: 0.72rem; color: var(--color-text-primary); }
.abt-hero-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; align-items: center; }

/* ---- update status ---------------------------------------------------------- */
.abt-update {
    grid-column: 1 / -1; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 10px 12px; border-radius: 10px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle);
}
.abt-update-icon { width: 32px; height: 32px; border-radius: 9px; flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
    background: color-mix(in srgb, var(--abt-state, var(--color-text-muted)) 14%, transparent); color: var(--abt-state, var(--color-text-muted)); }
.abt-update-icon .abt-icon { font-size: 19px; }
.abt-update-title { font-size: 0.88rem; font-weight: 600; color: var(--color-text-primary); }
.abt-update-sub { font-size: 0.76rem; color: var(--color-text-muted); }
.abt-update.is-ok { --abt-state: var(--color-success); }
.abt-update.is-available { --abt-state: var(--color-primary); border-color: var(--color-border-accent);
    background: color-mix(in srgb, var(--color-primary) 7%, var(--color-bg-base)); }
.abt-update.is-error { --abt-state: var(--color-warning); }
.abt-update.is-checking { --abt-state: var(--color-info); }
.abt-update.is-checking .abt-update-icon .abt-icon { animation: abt-spin 1s linear infinite; }
@keyframes abt-spin { to { transform: rotate(360deg); } }

/* ---- health ------------------------------------------------------------------ */
.abt-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 600; padding: 4px 10px; border-radius: 999px;
    color: var(--abt-state); background: color-mix(in srgb, var(--abt-state) 12%, transparent); border: 1px solid color-mix(in srgb, var(--abt-state) 35%, transparent); }
.abt-pill .abt-icon { font-size: 16px; }
.abt-pill.is-ok { --abt-state: var(--color-success); }
.abt-pill.is-warn { --abt-state: var(--color-warning); }
.abt-pill.is-error { --abt-state: var(--color-error); }
.abt-clean { display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 10px; background: var(--color-bg-base);
    border: 1px dashed var(--color-border-default); }
.abt-clean .abt-icon { font-size: 26px; color: var(--color-success); }
.abt-errors { display: flex; flex-direction: column; gap: 6px; }
details.abt-err { border-radius: 10px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); overflow: hidden; }
details.abt-err[open] { border-color: var(--color-border-accent); }
details.abt-err > summary { list-style: none; cursor: pointer; display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 10px;
    align-items: start; padding: 9px 12px; }
details.abt-err > summary::-webkit-details-marker { display: none; }
details.abt-err > summary:hover { background: var(--color-hover-overlay); }
.abt-err-icon { font-size: 19px; margin-top: 1px; color: var(--color-warning); }
.abt-err.is-critical .abt-err-icon { color: var(--color-error); }
.abt-err-msg { font-size: 0.84rem; color: var(--color-text-primary); line-height: 1.4;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; word-break: break-word; }
details.abt-err[open] .abt-err-msg { -webkit-line-clamp: unset; }
.abt-err-meta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 3px; font-size: 0.72rem; color: var(--color-text-muted); }
.abt-err-meta span { display: inline-flex; align-items: center; gap: 3px; }
.abt-err-meta .abt-icon { font-size: 13px; }
.abt-count { font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 999px; background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle); color: var(--color-text-secondary); white-space: nowrap; }
.abt-err-body { padding: 0 12px 12px 44px; display: flex; flex-direction: column; gap: 8px; }
.abt-err-body pre { margin: 0; max-height: 220px; overflow: auto; padding: 10px 12px; border-radius: 8px; font-size: 0.74rem; line-height: 1.45;
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; background: var(--color-bg-surface); color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle); white-space: pre-wrap; word-break: break-word; }
.abt-foot-note { font-size: 0.74rem; color: var(--color-text-muted); margin-top: 8px; }

/* ---- source URLs ------------------------------------------------------------------ */
.abt-count-chip { font-size: 0.74rem; padding: 2px 9px; border-radius: 999px; background: var(--color-primary-light);
    color: var(--color-text-primary); border: 1px solid var(--color-border-accent); }
.abt-search { width: 240px; }
.source-url-cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 10px; width: 100%; }
.source-url-card {
    display: flex; flex-direction: column; gap: 9px; min-width: 0;
    background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); border-radius: 11px; padding: 10px 11px 11px;
    transition: border-color .15s, transform .15s, box-shadow .15s;
}
.source-url-card:hover { border-color: var(--color-border-accent); transform: translateY(-1px);
    box-shadow: 0 6px 18px color-mix(in srgb, var(--color-primary) 10%, transparent); }
.source-url-card.is-container { border-color: color-mix(in srgb, var(--color-primary) 35%, var(--color-border-subtle)); }
.source-url-card-header { display: flex; align-items: center; gap: 10px; min-width: 0; }
.source-url-card-type { width: 32px; height: 32px; flex: 0 0 auto; border-radius: 9px; display: flex; align-items: center; justify-content: center;
    background: var(--color-primary-light); color: var(--color-primary); border: 1px solid var(--color-border-accent); }
.source-url-card-type .abt-icon { font-size: 18px; }
.source-url-card.is-container .source-url-card-type { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
.source-url-card-heading { flex: 1 1 auto; min-width: 0; }
.source-url-card-title { font-size: 0.9rem; font-weight: 600; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.source-url-card-badges { display: flex; gap: 5px; margin-top: 2px; flex-wrap: nowrap; overflow: hidden; }
.source-url-card-badge { font-size: 0.68rem; font-weight: 500; line-height: 1.5; padding: 0 7px; border-radius: 999px; white-space: nowrap;
    color: var(--color-text-secondary); background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle);
    display: inline-flex; align-items: center; gap: 3px; }
.source-url-card-badge .abt-icon { font-size: 12px; }
.source-url-card-badge.is-accent { color: var(--color-text-primary); background: var(--color-primary-light); border-color: var(--color-border-accent); }
.source-url-card-actions { display: flex; gap: 4px; flex: 0 0 auto; }
.source-url-card-actions .q-btn { width: 32px; height: 32px; min-height: 32px; min-width: 32px; padding: 0; border-radius: 8px; }
.source-url-card-actions .q-btn .q-icon { font-size: 17px; }
.abt-err-body .q-btn .q-icon { font-size: 16px; }
.abt-err-body .q-btn { min-height: 30px; font-size: 0.8rem; }
.source-url-card-actions .q-btn--outline:before { border: 1px solid var(--color-border-accent) !important; }
.source-url-card-actions .q-btn:hover:before { border-color: var(--color-primary) !important; }
.source-url-card-actions .q-btn.is-copied { color: var(--color-success) !important; }
.source-url-card-actions .q-btn.is-copied:before { border-color: var(--color-success) !important; }
.source-url-card-obs-icon { display: flex; line-height: 0; }
.source-url-card-obs-icon svg { width: 17px; height: 17px; display: block; }
.source-url-card-obs-icon svg path { fill: var(--color-text-secondary) !important; transition: fill .15s; }
.source-url-card-actions .q-btn:hover .source-url-card-obs-icon svg path { fill: var(--color-primary) !important; }
.source-url-card-url {
    display: flex; align-items: center; gap: 8px; min-width: 0; cursor: pointer;
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: 0.76rem;
    padding: 7px 10px; border-radius: 8px; background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle);
    transition: border-color .15s;
}
.source-url-card-url:hover { border-color: var(--color-border-accent); }
.source-url-card-url .abt-url-text { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; user-select: all; }
.source-url-card-url .abt-url-host { color: var(--color-text-muted); }
.source-url-card-url .abt-url-path { color: var(--color-text-primary); font-weight: 600; }
.source-url-card-url .abt-icon { font-size: 15px; color: var(--color-text-muted); }
.source-url-card-url:hover .abt-icon { color: var(--color-primary); }
.abt-empty { font-size: 0.84rem; color: var(--color-text-muted); text-align: center; padding: 22px 8px; }
.abt-empty .abt-icon { display: block; font-size: 30px; margin-bottom: 6px; opacity: .6; }
"""

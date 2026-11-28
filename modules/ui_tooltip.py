# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""One shared, themed tooltip for the whole app window.

``element.tooltip(text)`` creates a Quasar tooltip component per element. Form fields
and list cards add several each, so big tabs carried thousands of extra elements that
were built, serialized and sent to the window although at most one tooltip is ever
visible. :func:`tip` instead puts the text in a ``data-mtip`` attribute, and a single
delegated script (:data:`TOOLTIP_HEAD_HTML`, added once per page) shows one floating
tooltip styled like the Quasar ones.
"""

from __future__ import annotations

from typing import Any

TOOLTIP_ATTR = "data-mtip"

TOOLTIP_HEAD_HTML = """
<style id="mycelian-tip-css">
.mycelian-tip {
  position: fixed; z-index: 9999; pointer-events: none; max-width: 320px;
  padding: 6px 10px; border-radius: 4px; font-size: 0.8125rem; line-height: 1.35;
  background: var(--color-bg-surface); color: var(--color-text-primary);
  border: 1px solid var(--color-border-default);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
  opacity: 0; transition: opacity 0.12s ease; white-space: pre-line;
}
.mycelian-tip.is-on { opacity: 1; }
@media (prefers-reduced-motion: reduce) { .mycelian-tip { transition: none; } }
</style>
<script id="mycelian-tip-js">
(function () {
  if (window.__mycelianTip) return;
  window.__mycelianTip = true;
  var tip = null, target = null, timer = null;
  function el() {
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'mycelian-tip';
      tip.setAttribute('role', 'tooltip');
      document.body.appendChild(tip);
    }
    return tip;
  }
  function hide() {
    clearTimeout(timer); timer = null; target = null;
    if (tip) tip.classList.remove('is-on');
  }
  function place(t) {
    var box = el(), r = t.getBoundingClientRect();
    box.textContent = t.getAttribute('data-mtip') || '';
    if (!box.textContent) { hide(); return; }
    box.style.left = '0px'; box.style.top = '0px';
    var w = box.offsetWidth, h = box.offsetHeight;
    var x = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 8));
    var y = r.bottom + 8;
    if (y + h > window.innerHeight - 8) y = Math.max(8, r.top - h - 8);
    box.style.left = x + 'px'; box.style.top = y + 'px';
    box.classList.add('is-on');
  }
  function show(t, delay) {
    if (t === target) return;
    hide(); target = t;
    timer = setTimeout(function () { if (target === t && document.contains(t)) place(t); }, delay);
  }
  document.addEventListener('mouseover', function (e) {
    var t = e.target && e.target.closest && e.target.closest('[data-mtip]');
    if (t) show(t, 350); else if (target) hide();
  }, true);
  document.addEventListener('focusin', function (e) {
    var t = e.target && e.target.closest && e.target.closest('[data-mtip]');
    if (t && t.matches(':focus-visible')) show(t, 150);
  }, true);
  ['mousedown', 'scroll', 'focusout', 'keydown', 'blur'].forEach(function (ev) {
    document.addEventListener(ev, hide, true);
  });
})();
</script>
"""


def tip(element: Any, text: str) -> Any:
    """Give ``element`` a hover/focus tooltip without creating a tooltip component."""
    if text:
        element.props(f"{TOOLTIP_ATTR}={_attr(text)}")
    return element


def icon_label(text: str) -> str:
    """Props for an icon-only button: a screen-reader name plus the shared tooltip.

    Usage: ``ui.button(icon="close", ...).props(icon_label("Remove"))``.
    """
    return f"aria-label={_attr(text)} {TOOLTIP_ATTR}={_attr(text)}"


def _attr(text: str) -> str:
    # NiceGUI parses props like an HTML attribute list; quote and escape the value.
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

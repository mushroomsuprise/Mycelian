#!/usr/bin/env python3
"""
Compile Spore Studio editor bindings into the JavaScript snippet that
lives between the ``SPORE_STUDIO:auto-begin`` and ``SPORE_STUDIO:auto-end``
markers of a generated template.

A binding looks like::

    {
        "event": "next_alert",
        "filter": {"alert_type": "follow"},
        "action": "show_for",
        "args": {"seconds": 5, "anim_in": "fade", "anim_out": "fade"}
    }

We do NOT use ``json.dumps`` to inject the values directly into JS because
the helper script runs inside a Jinja-rendered HTML page; we want the
emitted code to be readable for the "advanced user" who later opens the
file by hand. The output is therefore intentionally indented and grouped
by element id.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


# Animation CSS injected into the head when at least one binding asks for
# an animation that needs keyframes. Kept tiny and standalone so it does
# not clash with template-author CSS.
ANIMATION_CSS = """
@keyframes sporeFadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes sporeFadeOut { from { opacity: 1; } to { opacity: 0; } }
@keyframes sporeSlideIn { from { transform: translateX(-30px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes sporeSlideOut { from { transform: translateX(0); opacity: 1; } to { transform: translateX(30px); opacity: 0; } }
@keyframes sporeScaleIn { from { transform: scale(0.6); opacity: 0; } to { transform: scale(1); opacity: 1; } }
@keyframes sporeScaleOut { from { transform: scale(1); opacity: 1; } to { transform: scale(0.6); opacity: 0; } }

.spore-anim-fade-in { animation: sporeFadeIn 0.3s ease-out both; }
.spore-anim-fade-out { animation: sporeFadeOut 0.3s ease-in both; }
.spore-anim-slidein { animation: sporeSlideIn 0.3s ease-out both; }
.spore-anim-slideout { animation: sporeSlideOut 0.3s ease-in both; }
.spore-anim-scalein { animation: sporeScaleIn 0.3s ease-out both; }
.spore-anim-scaleout { animation: sporeScaleOut 0.3s ease-in both; }
""".strip()


_ANIM_CLASS = {
    "fade": ("spore-anim-fade-in", "spore-anim-fade-out"),
    "slideIn": ("spore-anim-slidein", "spore-anim-slideout"),
    "slideOut": ("spore-anim-slidein", "spore-anim-slideout"),
    "scaleIn": ("spore-anim-scalein", "spore-anim-scaleout"),
    "scaleOut": ("spore-anim-scalein", "spore-anim-scaleout"),
    "none": ("", ""),
}


def _js_string(value: Any) -> str:
    """Safely encode a Python value as a JavaScript string literal."""
    return json.dumps("" if value is None else str(value))


def _js_value(value: Any) -> str:
    """Safely encode any JSON-serializable value as JavaScript."""
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return _js_string(value)


def _compile_filter(filter_obj: Dict[str, Any]) -> str:
    """
    Render a filter dict as a ``sporeMatchesFilter(payload, {...})`` call.

    The helper is defined in the boilerplate templates and does a simple
    deep-equality check on each key — sufficient for the alert payload
    shapes we expose in the event registry.
    """
    if not filter_obj:
        return "true"
    return "sporeMatchesFilter(payload, " + _js_value(filter_obj) + ")"


def _compile_action(element_id: str, action: str, args: Dict[str, Any]) -> List[str]:
    """
    Compile a single action into one or more JS statements.

    Returns a list of indented statement strings (without trailing semicolons
    on the consumer side — the caller joins them with ``\\n``).
    """
    eid = _js_string(element_id)
    args = args or {}

    if action == "show":
        return [f"sporeShow({eid});"]
    if action == "hide":
        return [f"sporeHide({eid});"]

    if action == "show_for":
        seconds = args.get("seconds", 5)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 5.0
        anim_in = str(args.get("anim_in", "fade") or "none")
        anim_out = str(args.get("anim_out", "fade") or "none")
        in_class, _ = _ANIM_CLASS.get(anim_in, ("", ""))
        _, out_class = _ANIM_CLASS.get(anim_out, ("", ""))
        lines: List[str] = []
        lines.append(f"(function () {{")
        lines.append(f"    var __el = document.getElementById({eid});")
        lines.append(f"    if (!__el) {{ return; }}")
        lines.append(f"    sporeShow({eid});")
        if in_class:
            lines.append(f"    __el.classList.remove({_js_string(in_class)});")
            lines.append(f"    void __el.offsetWidth;")
            lines.append(f"    __el.classList.add({_js_string(in_class)});")
        lines.append(f"    setTimeout(function () {{")
        if out_class:
            lines.append(f"        __el.classList.remove({_js_string(out_class)});")
            lines.append(f"        void __el.offsetWidth;")
            lines.append(f"        __el.classList.add({_js_string(out_class)});")
            lines.append(f"        setTimeout(function () {{ sporeHide({eid}); }}, 300);")
        else:
            lines.append(f"        sporeHide({eid});")
        lines.append(f"    }}, {seconds * 1000});")
        lines.append(f"}})();")
        return lines

    if action == "set_text":
        from_payload = args.get("from_payload") or ""
        literal = args.get("literal", "")
        if from_payload:
            return [
                f"sporeSetText({eid}, payload && payload[{_js_string(from_payload)}]);"
            ]
        return [f"sporeSetText({eid}, {_js_string(literal)});"]

    if action == "set_image":
        from_payload = args.get("from_payload") or ""
        literal = args.get("literal", "")
        lines = [
            f"(function () {{",
            f"    var __el = document.getElementById({eid});",
            f"    if (!__el) {{ return; }}",
        ]
        if from_payload:
            lines.append(
                f"    var __src = payload && payload[{_js_string(from_payload)}];"
            )
        else:
            lines.append(f"    var __src = {_js_string(literal)};")
        lines += [
            f"    if (__src) {{ __el.setAttribute('src', String(__src)); }}",
            f"}})();",
        ]
        return lines

    if action == "play_audio":
        return [
            f"(function () {{",
            f"    var __el = document.getElementById({eid});",
            f"    if (__el && typeof __el.play === 'function') {{",
            f"        try {{ __el.currentTime = 0; }} catch (e) {{}}",
            f"        var __p = __el.play();",
            f"        if (__p && typeof __p.catch === 'function') {{ __p.catch(function () {{}}); }}",
            f"    }}",
            f"}})();",
        ]

    return [f"// Unknown action: {action}"]


# Events the boilerplate templates wire up themselves — those have
# bespoke handling (queue/instant alert lifecycle), so the auto-block
# must NOT add a duplicate ``socket.on`` for them. Every other event the
# user binds to (chat messages, pause toggles, refresh-alerts, …) needs a
# generic listener emitted alongside ``sporeApplyBindings`` because the
# boilerplate doesn't know about them in advance.
_BOILERPLATE_HANDLED_EVENTS = frozenset({"next_alert", "instant_alert"})


def compile_bindings(elements: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Compile every binding across every element into a JS block.

    Args:
        elements: The editor model's ``elements`` array. Each element may
            optionally have a ``bindings`` list.

    Returns:
        A dict with:

        - ``js``: ready-to-drop body for the ``SPORE_STUDIO:auto-*`` block.
          Contains ``sporeApplyBindings(eventName, payload)`` plus a
          ``socket.on`` registration for every non-built-in event used by
          a binding (chat, pause toggles, etc.).
        - ``css``: keyframe / animation class CSS for any animations the
          bindings reference. Empty string when no animation is used.
    """
    by_event: Dict[str, List[str]] = {}
    use_animation = False

    for element in elements or []:
        if not isinstance(element, dict):
            continue
        eid = element.get("id")
        bindings = element.get("bindings") or []
        if not eid or not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            event = binding.get("event")
            action = binding.get("action")
            if not event or not action:
                continue
            filter_obj = binding.get("filter") or {}
            args = binding.get("args") or {}

            block: List[str] = []
            block.append(f"if ({_compile_filter(filter_obj)}) {{")
            for stmt in _compile_action(eid, action, args):
                block.append("    " + stmt)
            block.append("}")
            by_event.setdefault(event, []).extend(block)

            anim_in = (args or {}).get("anim_in")
            anim_out = (args or {}).get("anim_out")
            if anim_in and anim_in != "none":
                use_animation = True
            if anim_out and anim_out != "none":
                use_animation = True

    lines: List[str] = ["function sporeApplyBindings(eventName, payload) {"]
    for event, statements in by_event.items():
        lines.append(f"    if (eventName === {_js_string(event)}) {{")
        for stmt in statements:
            lines.append("        " + stmt)
        lines.append("    }")
    lines.append("}")

    extra_events = sorted(
        ev for ev in by_event.keys()
        if ev not in _BOILERPLATE_HANDLED_EVENTS
    )
    if extra_events:
        lines.append("")
        lines.append(
            "// Auto-registered listeners for events the boilerplate does"
        )
        lines.append(
            "// not handle natively (chat messages, pause toggles, etc.)."
        )
        lines.append(
            "// Guarded with __sporeBoundEvents so a hot-reloaded auto-block"
        )
        lines.append("// never double-binds the same event on the same socket.")
        lines.append(
            "if (typeof socket !== 'undefined' && socket && socket.on) {"
        )
        lines.append("    window.__sporeBoundEvents = window.__sporeBoundEvents || {};")
        for event in extra_events:
            ev_str = _js_string(event)
            lines.append(f"    if (!window.__sporeBoundEvents[{ev_str}]) {{")
            lines.append(f"        window.__sporeBoundEvents[{ev_str}] = true;")
            lines.append(f"        socket.on({ev_str}, function (data) {{")
            lines.append("            try {")
            lines.append(
                f"                sporeApplyBindings({ev_str}, data || {{}});"
            )
            lines.append("            } catch (err) {")
            lines.append(
                f"                console.error('[spore-studio] binding error on '"
                f" + {ev_str}, err);"
            )
            lines.append("            }")
            lines.append("        });")
            lines.append("    }")
        lines.append("}")

    js = "\n".join(lines)

    css = ANIMATION_CSS if use_animation else ""
    return {"js": js, "css": css}

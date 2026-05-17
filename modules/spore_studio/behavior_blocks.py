#!/usr/bin/env python3
"""
Compile Spore Studio editor bindings into the JavaScript snippet that
lives between the ``SPORE_STUDIO:auto-begin`` and ``SPORE_STUDIO:auto-end``
markers of a generated template.

Helix calls from user script (outside generated bindings) can use the same
Socket.IO pattern as ``templates/title.html``:

- ``socket.emit('twitch-api-request', { endpoint, method, requestId, params?, json_data? })``
- ``socket.on('twitch-api-response', …)`` — match on ``requestId``

``endpoint`` must be ``https://api.twitch.tv/...``. Optional ``params`` is a
query object; optional ``json_data`` (or ``json``) is the JSON body for
POST/PATCH/PUT.

A binding looks like::

    {
        "event": "next_alert",
        "filter": {"alert_type": "follow"},
        "action": "show_for",
        "args": {"seconds": 5, "anim_in": "fade", "anim_out": "fade"}
    }

Optional ``chain`` runs further actions after the primary ``action``. Each
chain row has ``delay_ms`` (wait after the previous step's synchronous JS,
then run this row's ``action`` / ``args``). Chained ``show_for`` timers are
independent: delays do not wait for the inner hide animation to finish.

``twitch-api-response`` bindings add ``twitch_api`` (``endpoint``, ``method``,
``params_json``, ``body_json``) and ``twitch_filters`` (list of ``{"key", "value"}``
rows). Values are coerced with :func:`json.loads` when valid JSON so filters can
match booleans and numbers. The compiler injects ``requestId`` into each row's
effective filter and emits a matching ``twitch-api-request`` after the socket
connects.

We do NOT use ``json.dumps`` to inject the values directly into JS because
the helper script runs inside a Jinja-rendered HTML page; we want the
emitted code to be readable for the "advanced user" who later opens the
file by hand. The output is therefore intentionally indented and grouped
by element id.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


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
@keyframes sporeShake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-6px); }
  40% { transform: translateX(6px); }
  60% { transform: translateX(-4px); }
  80% { transform: translateX(4px); }
}
@keyframes sporePop {
  0% { transform: scale(0.92); opacity: 0.85; }
  55% { transform: scale(1.04); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}

.spore-anim-fade-in { animation: sporeFadeIn 0.3s ease-out both; }
.spore-anim-fade-out { animation: sporeFadeOut 0.3s ease-in both; }
.spore-anim-slidein { animation: sporeSlideIn 0.3s ease-out both; }
.spore-anim-slideout { animation: sporeSlideOut 0.3s ease-in both; }
.spore-anim-scalein { animation: sporeScaleIn 0.3s ease-out both; }
.spore-anim-scaleout { animation: sporeScaleOut 0.3s ease-in both; }
.sporeShake { animation: sporeShake 0.42s ease-in-out both; }
.sporePop { animation: sporePop 0.35s ease-out both; }
""".strip()


_ANIM_CLASS = {
    "fade": ("spore-anim-fade-in", "spore-anim-fade-out"),
    "slideIn": ("spore-anim-slidein", "spore-anim-slideout"),
    "slideOut": ("spore-anim-slidein", "spore-anim-slideout"),
    "scaleIn": ("spore-anim-scalein", "spore-anim-scaleout"),
    "scaleOut": ("spore-anim-scalein", "spore-anim-scaleout"),
    "none": ("", ""),
}


def _coerce_float_opt(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _safe_element_token(element_id: Any) -> str:
    raw = str(element_id) if element_id is not None else "el"
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw) or "el"


def _twitch_request_id(element_id: Any, binding_index: int) -> str:
    return f"spore_tw_{_safe_element_token(element_id)}_{int(binding_index)}"


def _coerce_twitch_filter_cell(raw: Any) -> Any:
    """Turn a user-typed filter value into a JSON-friendly Python value."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if s == "":
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return s


def _twitch_response_filter_dict(binding: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"requestId": request_id}
    for row in binding.get("twitch_filters") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if not key or key == "requestId":
            continue
        coerced = _coerce_twitch_filter_cell(row.get("value"))
        if coerced is None:
            continue
        out[key] = coerced
    return out


def _compile_twitch_emit_iife(binding: Dict[str, Any], request_id: str) -> str:
    """
    Produce an IIFE that emits ``twitch-api-request`` after the socket connects.
    Warns in the console when the endpoint is blank or JSON fields are invalid.
    """
    ta = binding.get("twitch_api")
    if not isinstance(ta, dict):
        ta = {}
    endpoint = str(ta.get("endpoint") or "").strip()
    if not endpoint:
        return (
            "(function () {\n"
            "    try {\n"
            "        console.warn('[spore-studio] Twitch API binding skipped: "
            "set an HTTPS api.twitch.tv endpoint in Spore Studio.');\n"
            "    } catch (e) {}\n"
            "})();"
        )

    method = str(ta.get("method") or "GET").strip().upper() or "GET"
    emit_obj: Dict[str, Any] = {
        "endpoint": endpoint,
        "method": method,
        "requestId": request_id,
    }

    params_err: Optional[str] = None
    params_raw = ta.get("params_json")
    if isinstance(params_raw, str) and params_raw.strip():
        try:
            parsed_p = json.loads(params_raw)
        except json.JSONDecodeError as e:
            params_err = f"params JSON: {e}"
            parsed_p = None
        if params_err is None:
            if not isinstance(parsed_p, dict):
                params_err = "params JSON must be an object `{...}`"
            else:
                emit_obj["params"] = parsed_p

    body_err: Optional[str] = None
    body_raw = ta.get("body_json")
    if isinstance(body_raw, str) and body_raw.strip():
        try:
            parsed_b = json.loads(body_raw)
        except json.JSONDecodeError as e:
            body_err = f"body JSON: {e}"
            parsed_b = None
        if body_err is None:
            if not isinstance(parsed_b, dict):
                body_err = "body JSON must be an object `{...}`"
            else:
                emit_obj["json_data"] = parsed_b

    err_parts: List[str] = []
    if params_err:
        err_parts.append(params_err)
    if body_err:
        err_parts.append(body_err)

    if err_parts:
        msg = "; ".join(err_parts)
        return (
            "(function () {\n"
            f"    try {{ console.warn('[spore-studio] Twitch API emit: ' + "
            f"{_js_string(msg)}); }}\n"
            "    catch (e) {}\n"
            "})();"
        )

    emit_literal = _js_value(emit_obj)
    return (
        "(function () {\n"
        "    var __payload = "
        + emit_literal
        + ";\n"
        "    var __go = function () {\n"
        "        try {\n"
        "            socket.emit('twitch-api-request', __payload);\n"
        "        } catch (e) {\n"
        "            console.error('[spore-studio] twitch-api-request emit', e);\n"
        "        }\n"
        "    };\n"
        "    if (socket && socket.connected) { __go(); }\n"
        "    else if (socket) { socket.once('connect', __go); }\n"
        "})();"
    )


def _compile_action(element_id: str, action: str, args: Dict[str, Any]) -> List[str]:
    """
    Compile a single action into one or more JS statements.

    Returns a list of indented statement strings (without trailing semicolons
    on the consumer side — the caller joins them with ``\\n``).
    """
    eid = _js_string(element_id)
    args = args or {}

    if action == "show":
        return [f"sporeShow({eid}, null);"]
    if action == "hide":
        return [f"sporeHide({eid}, null);"]

    if action == "toggle":
        return [f"sporeToggle({eid});"]

    if action == "show_for":
        seconds = args.get("seconds", 5)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 5.0
        anim_in = str(args.get("anim_in", "fade") or "none")
        anim_out = str(args.get("anim_out", "fade") or "none")
        ov: Dict[str, Any] = {}
        if anim_in and anim_in != "none":
            ov["animIn"] = anim_in
        if anim_out and anim_out != "none":
            ov["animOut"] = anim_out
        ov_js = _js_value(ov if ov else None)
        return [
            "(function () {",
            f"    sporeShow({eid}, {ov_js});",
            f"    setTimeout(function () {{ sporeHide({eid}, {ov_js}); }}, "
            f"{max(0.0, seconds) * 1000});",
            "})();",
        ]

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
            "(function () {",
            f"    var __el = document.getElementById({eid});",
            "    if (!__el) { return; }",
        ]
        if from_payload:
            lines.append(
                f"    var __src = payload && payload[{_js_string(from_payload)}];"
            )
        else:
            lines.append(f"    var __src = {_js_string(literal)};")
        lines.extend(
            [
                "    if (__src) {",
                "        var __srcS = String(__src);",
                "        if (__el.tagName === 'IMG') { __el.setAttribute('src', __srcS); }",
                "        else {",
                "            var __img = __el.querySelector('img');",
                "            if (__img) { __img.setAttribute('src', __srcS); }",
                "        }",
                "    }",
                "})();",
            ]
        )
        return lines

    if action == "set_visual_src":
        from_payload = args.get("from_payload") or ""
        literal = args.get("literal", "")
        lines = [
            "(function () {",
            f"    var __el = document.getElementById({eid});",
            "    if (!__el) { return; }",
        ]
        if from_payload:
            lines.append(
                f"    var __src = payload && payload[{_js_string(from_payload)}];"
            )
        else:
            lines.append(f"    var __src = {_js_string(literal)};")
        lines.extend(
            [
                "    if (__src) {",
                "        var __u = String(__src);",
                "        var __im = __el.querySelector('img');",
                "        if (__im) { __im.setAttribute('src', __u); }",
                "        var __vd = __el.querySelector('video');",
                "        if (__vd) {",
                "            var __sr = __vd.querySelector('source');",
                "            if (__sr) { __sr.setAttribute('src', __u); }",
                "            try { __vd.load(); } catch (__l) {}",
                "        }",
                "    }",
                "})();",
            ]
        )
        return lines

    if action == "play_audio":
        over: Dict[str, Any] = {}
        vol_a = args.get("volume")
        if vol_a not in (None, ""):
            over["volume"] = vol_a
        fin_a = args.get("fade_in_ms")
        if fin_a not in (None, ""):
            over["fade_in_ms"] = fin_a
        return [f"sporePlayMediaAudio({eid}, {_js_value(over)});"]

    if action == "randomize_position":
        x_cv = _coerce_float_opt(args.get("x_min"))
        xmn = 0.0 if x_cv is None else float(x_cv)
        x_raw = args.get("x_max")
        xmx_opt = _coerce_float_opt(x_raw) if x_raw not in (None, "") else None
        y_cv = _coerce_float_opt(args.get("y_min"))
        ymn = 0.0 if y_cv is None else float(y_cv)
        y_raw = args.get("y_max")
        ymx_opt = _coerce_float_opt(y_raw) if y_raw not in (None, "") else None
        xmx_sent = float(xmx_opt) if xmx_opt is not None else -1.0
        ymx_sent = float(ymx_opt) if ymx_opt is not None else -1.0
        return [
            "(function () {",
            f"    var __el = document.getElementById({eid});",
            "    if (!__el) { return; }",
            "    var __root = document.getElementById('sporeRoot');",
            "    var __rw = __root ? __root.offsetWidth : 1920;",
            "    var __rh = __root ? __root.offsetHeight : 1080;",
            "    var __ew = __el.offsetWidth || 1;",
            "    var __eh = __el.offsetHeight || 1;",
            f"    var __xMn = {_js_value(xmn)};",
            f"    var __xMxCfg = {_js_value(xmx_sent)};",
            f"    var __yMn = {_js_value(ymn)};",
            f"    var __yMxCfg = {_js_value(ymx_sent)};",
            "    var __xCap = Math.max(0, __rw - __ew);",
            "    var __yCap = Math.max(0, __rh - __eh);",
            "    var __xMx = (__xMxCfg < 0) ? __xCap : Math.min(__xMxCfg, __xCap);",
            "    var __yMx = (__yMxCfg < 0) ? __yCap : Math.min(__yMxCfg, __yCap);",
            "    if (__xMx < __xMn) { __xMx = __xMn; }",
            "    if (__yMx < __yMn) { __yMx = __yMn; }",
            "    var __nx = __xMn + Math.random() * ((__xMx - __xMn) || 1);",
            "    var __ny = __yMn + Math.random() * ((__yMx - __yMn) || 1);",
            "    __el.style.left = Math.round(__nx) + 'px';",
            "    __el.style.top = Math.round(__ny) + 'px';",
            "})();",
        ]

    if action == "set_transform":
        parts_tf: List[str] = []
        r_tf = args.get("rotate_deg")
        if r_tf not in (None, ""):
            try:
                parts_tf.append(f"rotate({float(r_tf)}deg)")
            except (TypeError, ValueError):
                pass
        sc_tf = args.get("scale")
        if sc_tf not in (None, ""):
            try:
                sf = float(sc_tf)
                parts_tf.append(f"scale({sf})")
            except (TypeError, ValueError):
                pass
        tx_tf = args.get("translate_x")
        ty_tf = args.get("translate_y")
        try:
            if tx_tf not in (None, "") and ty_tf not in (None, ""):
                parts_tf.insert(0, f"translate({float(tx_tf)}px, {float(ty_tf)}px)")
            elif tx_tf not in (None, ""):
                parts_tf.insert(0, f"translateX({float(tx_tf)}px)")
            elif ty_tf not in (None, ""):
                parts_tf.insert(0, f"translateY({float(ty_tf)}px)")
        except (TypeError, ValueError):
            pass
        joined = " ".join(parts_tf)
        return [
            "(function () {",
            f"    var __el = document.getElementById({eid});",
            "    if (!__el) { return; }",
            f"    var __tf = {_js_string(joined)};",
            "    if (__tf) { __el.style.transform = __tf; }",
            "})();",
        ]

    if action == "transform_jitter":
        rot_r_s = args.get("rotate_range")
        tr_r_s = args.get("translate_range")
        try:
            rot_r = abs(float(rot_r_s)) if rot_r_s not in (None, "") else 0.0
        except (TypeError, ValueError):
            rot_r = 0.0
        try:
            tr_r = abs(float(tr_r_s)) if tr_r_s not in (None, "") else 0.0
        except (TypeError, ValueError):
            tr_r = 0.0
        smin_raw = args.get("scale_min")
        smax_raw = args.get("scale_max")
        smin_opt = _coerce_float_opt(smin_raw)
        smax_opt = _coerce_float_opt(smax_raw)
        has_scale = smin_opt is not None and smax_opt is not None
        if rot_r <= 0 and tr_r <= 0 and not has_scale:
            return ["// transform_jitter: set rotate_range, translate_range, and/or scale_min/max"]
        if has_scale and smax_opt is not None and smin_opt is not None:
            lo = min(float(smin_opt), float(smax_opt))
            hi = max(float(smin_opt), float(smax_opt))
        else:
            lo, hi = 1.0, 1.0
        lines = [
            "(function () {",
            f"    var __el = document.getElementById({eid});",
            "    if (!__el) { return; }",
            f"    var __rr = {_js_value(rot_r)};",
            f"    var __tr = {_js_value(tr_r)};",
            "    var tf = [];",
            "    var __r1 = function (mx) { return mx > 0 ? (Math.random() * 2 - 1) * mx : 0; };",
            "    if (__tr > 0) {",
            "        tf.push('translate(' + __r1(__tr).toFixed(2) + 'px, ' + __r1(__tr).toFixed(2) + 'px)');",
            "    }",
            "    if (__rr > 0) {",
            "        tf.push('rotate(' + __r1(__rr).toFixed(2) + 'deg)');",
            "    }",
        ]
        if has_scale:
            lines.extend(
                [
                    f"    var __sl = {_js_value(lo)};",
                    f"    var __sh = {_js_value(hi)};",
                    (
                        "    tf.push('scale(' + "
                        "(__sl + Math.random() * (__sh - __sl)).toFixed(4) + ')');"
                    ),
                ]
            )
        lines.extend(
            [
                "    if (tf.length) { __el.style.transform = tf.join(' '); }",
                "})();",
            ]
        )
        return lines

    if action == "flash_class":
        cls = str(args.get("class_name") or "").strip()
        dur_ms_raw = args.get("duration_ms", 500)
        try:
            dur_ms = int(float(dur_ms_raw))
        except (TypeError, ValueError):
            dur_ms = 500
        dur_ms = max(0, min(60000, dur_ms))
        lines = [
            "(function () {",
            f"    var __el = document.getElementById({eid});",
            "    if (!__el) { return; }",
            f"    var __c = {_js_string(cls)};",
            "    if (!__c) { return; }",
            "    __el.classList.remove(__c);",
            "    void __el.offsetWidth;",
            "    __el.classList.add(__c);",
            f"    setTimeout(function () {{",
            "        try { __el.classList.remove(__c); } catch (__e1) {}",
            f"    }}, {dur_ms});",
            "})();",
        ]
        return lines

    return [f"// Unknown action: {action}"]


def _coerce_delay_ms(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, v)


def _binding_action_steps(binding: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], float]]:
    """
    Primary ``action``/``args`` is step 0 (delay unused). Each ``chain`` row
    is a subsequent step; its ``delay_ms`` is the pause after the previous
    step's synchronous JS before this step runs.
    """
    out: List[Tuple[str, Dict[str, Any], float]] = []
    primary = binding.get("action")
    if not primary or not isinstance(primary, str):
        return out
    a0 = binding.get("args")
    args0: Dict[str, Any] = dict(a0) if isinstance(a0, dict) else {}
    out.append((primary, args0, 0.0))
    for row in binding.get("chain") or []:
        if not isinstance(row, dict):
            continue
        act = row.get("action")
        if not act or not isinstance(act, str):
            continue
        ra = row.get("args")
        args_k: Dict[str, Any] = dict(ra) if isinstance(ra, dict) else {}
        out.append((act, args_k, _coerce_delay_ms(row.get("delay_ms", 0))))
    return out


def _step_uses_animation(action: str, args: Dict[str, Any]) -> bool:
    if action == "show_for":
        a = args or {}
        anim_in_eff = str(a.get("anim_in", "fade") or "none")
        anim_out_eff = str(a.get("anim_out", "fade") or "none")
        return anim_in_eff != "none" or anim_out_eff != "none"
    anim_in = (args or {}).get("anim_in")
    anim_out = (args or {}).get("anim_out")
    return bool(
        (anim_in and str(anim_in) != "none")
        or (anim_out and str(anim_out) != "none")
    )


def _compile_action_sequence(
    element_id: str, steps: List[Tuple[str, Dict[str, Any], float]]
) -> List[str]:
    if not steps:
        return []
    if len(steps) == 1:
        return _compile_action(element_id, steps[0][0], steps[0][1])
    tail: List[str] = list(_compile_action(element_id, steps[-1][0], steps[-1][1]))
    for i in range(len(steps) - 2, -1, -1):
        delay_ms = int(round(max(0.0, steps[i + 1][2])))
        prev = _compile_action(element_id, steps[i][0], steps[i][1])
        block: List[str] = []
        block.extend(prev)
        block.append("setTimeout(function () {")
        for line in tail:
            block.append("    " + line)
        block.append(f"}}, {delay_ms});")
        tail = block
    return tail


def _bindings_need_toggle(elements: List[Dict[str, Any]]) -> bool:
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        for binding in element.get("bindings") or []:
            if not isinstance(binding, dict):
                continue
            for act, _args, _d in _binding_action_steps(binding):
                if act == "toggle":
                    return True
    return False


def _element_uses_animations(element: Dict[str, Any]) -> bool:
    anims = element.get("animations")
    if not isinstance(anims, dict):
        return False
    for key in ("anim_in", "anim_out"):
        val = str(anims.get(key) or "none").strip()
        if val and val != "none":
            return True
    return False


def _bindings_need_preset_css(elements: List[Dict[str, Any]]) -> bool:
    """Keyframe utility classes (.sporeShake, .sporePop) used by flash_class."""
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        if _element_uses_animations(element):
            return True
        for binding in element.get("bindings") or []:
            if not isinstance(binding, dict):
                continue
            for act, _args, _d in _binding_action_steps(binding):
                if act == "flash_class":
                    return True
    return False


_TOGGLE_JS = """
function sporeToggle(id) {
    var el = document.getElementById(id);
    if (!el) { return; }
    if (el.hasAttribute('data-spore-hidden')) {
        sporeShow(id, null);
    } else {
        sporeHide(id, null);
    }
}
""".strip()


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
        for bidx, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                continue
            event = binding.get("event")
            action = binding.get("action")
            if not event or not action:
                continue
            if event == "twitch-api-response":
                filter_obj = _twitch_response_filter_dict(
                    binding, _twitch_request_id(eid, bidx)
                )
            else:
                filter_obj = binding.get("filter") or {}

            steps = _binding_action_steps(binding)
            if not steps:
                continue

            block: List[str] = []
            block.append(f"if ({_compile_filter(filter_obj)}) {{")
            for stmt in _compile_action_sequence(eid, steps):
                block.append("    " + stmt)
            block.append("}")
            by_event.setdefault(event, []).extend(block)

            if _element_uses_animations(element):
                use_animation = True
            for act, step_args, _delay in steps:
                if _step_uses_animation(act, step_args):
                    use_animation = True

    lines: List[str] = [
        "function sporeApplyBindings(eventName, payload) {",
        "    try {",
        "        console.log('[spore-studio] sporeApplyBindings', eventName, payload);",
        "    } catch (__sporeLog) {}",
    ]
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
                f"                console.log('[spore-studio] socket.on', {ev_str}, data);"
            )
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
    if _bindings_need_toggle(elements):
        js = _TOGGLE_JS + "\n\n" + js

    # One-shot Helix requests for twitch-api-response bindings (after listeners).
    twitch_emits: List[str] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        eid = element.get("id")
        bindings = element.get("bindings") or []
        if not eid or not isinstance(bindings, list):
            continue
        for bidx, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                continue
            if binding.get("event") != "twitch-api-response":
                continue
            if not binding.get("action"):
                continue
            rid = _twitch_request_id(eid, bidx)
            emit_js = _compile_twitch_emit_iife(binding, rid)
            if emit_js:
                twitch_emits.append(emit_js)

    if twitch_emits:
        js = js + "\n\n" + "\n\n".join(twitch_emits)

    css = (
        ANIMATION_CSS
        if (use_animation or _bindings_need_preset_css(elements))
        else ""
    )
    return {"js": js, "css": css}

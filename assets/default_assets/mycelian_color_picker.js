/*
 * Mycelian colour picker (Source Settings, the Theme tab and Spore Studio share this file).
 * Styles: mycelian_color_picker.css (theme variables only).
 *
 * var handle = MycelianColorPicker.open({
 *     anchor,                 element the popover hangs off (flips / clamps to stay on screen)
 *     value,                  current CSS value: #hex, rgb(), rgba(), hsl(), a name, transparent or a gradient
 *     presets,                optional list of swatch values ("Template colours")
 *     allowAlpha = true,      opacity bar, % box and opacity chips
 *     allowGradient = false,  Solid / Gradient tabs (linear + radial)
 *     preferredFormat,        "hex" | "rgb" for opaque output (default: the format the value came in with)
 *     title, note,            optional heading and footer hint
 *     onInput(v),             live, throttled (~60 ms) while dragging / typing
 *     onDone(v),              Done, Enter in a field, or a click outside (keeps the colour)
 *     onCancel()              Cancel or Escape
 * });                         -> {close(how), element}; only one picker is open at a time
 *
 * MycelianColorPicker.parse(value)                -> {kind: "solid", r, g, b, a, format}
 *                                                    | {kind: "gradient", type, angle, shape, stops: [{r, g, b, a, pos}]}
 *                                                    | null
 * MycelianColorPicker.format(state, preferred)    -> CSS text for a parse() result ("hex" | "rgb")
 * MycelianColorPicker.isTransparent(value)        -> true for transparent, alpha < 1, or a gradient with such a stop
 * MycelianColorPicker.swatchBackground(value)     -> safe CSS background for a preview swatch
 * MycelianColorPicker.close(how)                  -> closes the open picker ("done" | "cancel")
 *
 * Output: an untouched value is handed back exactly as it came in. Opaque colours keep their format
 * (#hex by default, rgb() when they came in as rgb/rgba), alpha < 1 gives rgba(r, g, b, a), alpha 0 gives
 * "transparent" (rgba(..., 0) when it came in that way) and gradients come back as CSS text.
 */
(function () {
    'use strict';
    if (window.MycelianColorPicker) { return; }

    var RECENT_KEY = 'mycelian.colorPicker.recent';
    var RECENT_MAX = 10;
    var PRESET_ROWS = 20;
    var THROTTLE_MS = 60;

    var ICON = {
        gradient: 'M11 9h2v2h-2zm-2 2h2v2H9zm4 0h2v2h-2zm2-2h2v2h-2zM7 9h2v2H7zm12-6H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 18H7v-2h2v2zm4 0h-2v-2h2v2zm4 0h-2v-2h2v2zm2-7h-2v2h2v2h-2v-2h-2v2h-2v-2h-2v2H9v-2H7v2H5v-2h2v-2H5V5h14v6z',
        colorize: 'M20.71 5.63l-2.34-2.34a.996.996 0 00-1.41 0l-3.12 3.12-1.93-1.91-1.41 1.41 1.42 1.42L3 16.25V21h4.75l8.92-8.92 1.42 1.42 1.41-1.41-1.92-1.92 3.12-3.12c.4-.4.4-1.03.01-1.42zM6.92 19L5 17.08l8.06-8.06 1.92 1.92L6.92 19z',
        block: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zM4 12c0-4.42 3.58-8 8-8 1.85 0 3.55.63 4.9 1.69L5.69 16.9A7.902 7.902 0 014 12zm8 8c-1.85 0-3.55-.63-4.9-1.69L18.31 7.1A7.902 7.902 0 0120 12c0 4.42-3.58 8-8 8z',
        info: 'M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z',
        touch: 'M9 11.24V7.5a2.5 2.5 0 015 0v3.74c1.21-.81 2-2.18 2-3.74C16 5.01 13.99 3 11.5 3S7 5.01 7 7.5c0 1.56.79 2.93 2 3.74zm9.84 4.63l-4.54-2.26c-.17-.07-.35-.11-.54-.11H13v-6c0-.83-.67-1.5-1.5-1.5S10 6.67 10 7.5v10.74l-3.43-.72c-.08-.01-.15-.03-.24-.03-.31 0-.59.13-.79.33l-.79.8 4.94 4.94c.27.27.65.44 1.06.44h6.79c.75 0 1.33-.55 1.44-1.28l.75-5.27c.01-.07.02-.14.02-.2 0-.62-.38-1.16-.91-1.38z',
        trash: 'M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM8 9h8v10H8V9zm7.5-5l-1-1h-5l-1 1H5v2h14V4z'
    };

    function svg(name) {
        if (name === 'circle') {
            return '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>';
        }
        return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + ICON[name] + '"/></svg>';
    }

    /* ------------------------------------------------------------------ colour maths */

    function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
    function round3(v) { return Math.round(v * 1000) / 1000; }
    function fmtNum(v) { return String(Math.round(v * 10) / 10); }
    function hx(n) { var s = Math.round(clamp(n, 0, 255)).toString(16); return s.length < 2 ? '0' + s : s; }
    function toHex(c) { return '#' + hx(c.r) + hx(c.g) + hx(c.b); }

    function hsvToRgb(h, s, v) {
        h = ((h % 360) + 360) % 360;
        s = clamp(s, 0, 1);
        v = clamp(v, 0, 1);
        var c = v * s;
        var x = c * (1 - Math.abs((h / 60) % 2 - 1));
        var m = v - c;
        var r = 0, g = 0, b = 0;
        if (h < 60) { r = c; g = x; } else if (h < 120) { r = x; g = c; } else if (h < 180) { g = c; b = x; }
        else if (h < 240) { g = x; b = c; } else if (h < 300) { r = x; b = c; } else { r = c; b = x; }
        return { r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255) };
    }

    function rgbToHsv(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min, h = 0;
        if (d !== 0) {
            if (max === r) { h = ((g - b) / d) % 6; } else if (max === g) { h = (b - r) / d + 2; } else { h = (r - g) / d + 4; }
            h *= 60;
            if (h < 0) { h += 360; }
        }
        return { h: h, s: max === 0 ? 0 : d / max, v: max };
    }

    function hslToRgb(h, s, l) {
        h = ((h % 360) + 360) % 360; s = clamp(s, 0, 1); l = clamp(l, 0, 1);
        var c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = l - c / 2;
        var r = 0, g = 0, b = 0;
        if (h < 60) { r = c; g = x; } else if (h < 120) { r = x; g = c; } else if (h < 180) { g = c; b = x; }
        else if (h < 240) { g = x; b = c; } else if (h < 300) { r = x; b = c; } else { r = c; b = x; }
        return { r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255) };
    }

    /* ------------------------------------------------------------------ parsing */

    var NOT_A_COLOUR = /^(inherit|initial|unset|revert|revert-layer|currentcolor|none|auto|normal)$/i;
    var probeCtx = null;

    function num(tok, pctScale) {
        tok = String(tok).trim();
        if (/^-?[\d.]+%$/.test(tok)) { return parseFloat(tok) / 100 * pctScale; }
        if (/^-?[\d.]+(e-?\d+)?$/i.test(tok)) { return parseFloat(tok); }
        return NaN;
    }

    function fnParts(body) {
        body = body.trim();
        var parts;
        if (body.indexOf(',') >= 0) {
            parts = body.split(',');
        } else {
            var slash = body.split('/');
            if (slash.length > 2) { return null; }
            parts = slash[0].trim().split(/\s+/);
            if (slash.length === 2) { parts.push(slash[1]); }
        }
        return parts.map(function (p) { return p.trim(); });
    }

    function parseSolid(value) {
        var s = String(value == null ? '' : value).trim();
        var low = s.toLowerCase();
        if (!s) { return null; }
        if (low === 'transparent') { return { r: 0, g: 0, b: 0, a: 0, format: 'transparent' }; }
        var m = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(s);
        if (m) {
            var h = m[1];
            if (h.length <= 4) { h = h.split('').map(function (ch) { return ch + ch; }).join(''); }
            return {
                r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16),
                a: h.length === 8 ? round3(parseInt(h.slice(6, 8), 16) / 255) : 1, format: 'hex'
            };
        }
        m = /^(rgba?|hsla?)\(([^()]*)\)$/i.exec(s);
        if (m) {
            var fn = m[1].toLowerCase();
            var parts = fnParts(m[2]);
            if (!parts || parts.length < 3 || parts.length > 4) { return null; }
            var a = parts.length === 4 ? num(parts[3], 1) : 1;
            if (isNaN(a)) { return null; }
            var rgb;
            if (fn.charAt(0) === 'r') {
                var ch = parts.slice(0, 3).map(function (p) { return num(p, 255); });
                if (ch.some(isNaN)) { return null; }
                rgb = { r: Math.round(clamp(ch[0], 0, 255)), g: Math.round(clamp(ch[1], 0, 255)), b: Math.round(clamp(ch[2], 0, 255)) };
            } else {
                var hm = /^(-?[\d.]+)(deg|turn|rad|grad)?$/i.exec(parts[0]);
                var sat = num(parts[1], 1), lig = num(parts[2], 1);
                if (!hm || isNaN(sat) || isNaN(lig)) { return null; }
                if (!/%$/.test(parts[1])) { sat /= 100; }
                if (!/%$/.test(parts[2])) { lig /= 100; }
                rgb = hslToRgb(angleToDeg(parseFloat(hm[1]), hm[2]), sat, lig);
            }
            return {
                r: rgb.r, g: rgb.g, b: rgb.b, a: round3(clamp(a, 0, 1)),
                format: fn.charAt(0) === 'r' ? (fn === 'rgba' || parts.length === 4 ? 'rgba' : 'rgb') : 'hsl'
            };
        }
        if (/^[a-z]+$/i.test(s) && !NOT_A_COLOUR.test(s)) {
            try {
                if (!probeCtx) { probeCtx = document.createElement('canvas').getContext('2d'); }
                probeCtx.fillStyle = '#010203';
                probeCtx.fillStyle = s;
                var got = String(probeCtx.fillStyle);
                if (got !== '#010203') {
                    var named = parseSolid(got);
                    if (named) { named.format = 'name'; return named; }
                }
            } catch (e) { /* no canvas */ }
        }
        return null;
    }

    function angleToDeg(n, unit) {
        unit = (unit || 'deg').toLowerCase();
        if (unit === 'turn') { return n * 360; }
        if (unit === 'rad') { return n * 180 / Math.PI; }
        if (unit === 'grad') { return n * 0.9; }
        return n;
    }

    function splitTop(s) {
        var out = [], depth = 0, start = 0;
        for (var i = 0; i < s.length; i++) {
            var c = s.charAt(i);
            if (c === '(') { depth++; } else if (c === ')') { depth--; } else if (c === ',' && depth === 0) {
                out.push(s.slice(start, i).trim());
                start = i + 1;
            }
        }
        out.push(s.slice(start).trim());
        return out;
    }

    var DIRS = {
        'to top': 0, 'to right': 90, 'to bottom': 180, 'to left': 270,
        'to top right': 45, 'to right top': 45, 'to bottom right': 135, 'to right bottom': 135,
        'to bottom left': 225, 'to left bottom': 225, 'to top left': 315, 'to left top': 315
    };

    function parseStop(tok) {
        tok = tok.trim();
        var color, rest;
        var m = /^((?:rgba?|hsla?)\([^()]*\))\s*(.*)$/i.exec(tok);
        if (m) { color = m[1]; rest = m[2]; } else {
            var i = tok.search(/\s/);
            color = i < 0 ? tok : tok.slice(0, i);
            rest = i < 0 ? '' : tok.slice(i + 1).trim();
        }
        var c = parseSolid(color);
        if (!c) { return null; }
        var poss = rest ? rest.split(/\s+/) : [];
        if (poss.length > 2) { return null; }
        var pos = [];
        for (var k = 0; k < poss.length; k++) {
            var pm = /^(-?[\d.]+)%$/.exec(poss[k]);
            var p = pm ? parseFloat(pm[1]) : (poss[k] === '0' ? 0 : NaN);
            if (isNaN(p) || p < 0 || p > 100) { return null; }
            pos.push(p);
        }
        return { c: c, pos: pos };
    }

    function parseGradient(value) {
        var s = String(value == null ? '' : value).trim();
        var m = /^(linear|radial)-gradient\(([\s\S]*)\)$/i.exec(s);
        if (!m) { return null; }
        var type = m[1].toLowerCase();
        var args = splitTop(m[2]);
        var angle = 180, shape = '';
        var first = (args[0] || '').toLowerCase().replace(/\s+/g, ' ');
        if (type === 'linear') {
            var am = /^(-?[\d.]+)(deg|turn|rad|grad)$/.exec(first);
            if (am) { angle = angleToDeg(parseFloat(am[1]), am[2]); args.shift(); }
            else if (first === '0') { angle = 0; args.shift(); }
            else if (Object.prototype.hasOwnProperty.call(DIRS, first)) { angle = DIRS[first]; args.shift(); }
            else if (/^to /.test(first)) { return null; }
        } else if (first === 'circle' || first === 'ellipse') {
            shape = first;
            args.shift();
        }
        var stops = [];
        for (var i = 0; i < args.length; i++) {
            var st = parseStop(args[i]);
            if (!st) { return null; }
            if (!st.pos.length) { stops.push({ c: st.c, pos: null }); } else {
                for (var j = 0; j < st.pos.length; j++) { stops.push({ c: st.c, pos: st.pos[j] }); }
            }
        }
        if (stops.length < 2) { return null; }
        if (stops[0].pos == null) { stops[0].pos = 0; }
        if (stops[stops.length - 1].pos == null) { stops[stops.length - 1].pos = 100; }
        var last = stops[0].pos;
        for (i = 1; i < stops.length; i++) {
            if (stops[i].pos == null) { continue; }
            if (stops[i].pos < last) { stops[i].pos = last; }
            last = stops[i].pos;
        }
        for (i = 1; i < stops.length; i++) {
            if (stops[i].pos != null) { continue; }
            var k = i;
            while (stops[k].pos == null) { k++; }
            var from = stops[i - 1].pos, to = stops[k].pos, n = k - i + 1;
            for (j = i; j < k; j++) { stops[j].pos = from + (to - from) * (j - i + 1) / n; }
        }
        return {
            kind: 'gradient', type: type, angle: angle, shape: shape,
            stops: stops.map(function (x) { return { r: x.c.r, g: x.c.g, b: x.c.b, a: x.c.a, pos: x.pos }; })
        };
    }

    function parse(value) {
        var g = parseGradient(value);
        if (g) { return g; }
        var c = parseSolid(value);
        if (!c) { return null; }
        c.kind = 'solid';
        return c;
    }

    /* ------------------------------------------------------------------ formatting */

    function formatSolid(c, fmt, zeroRgba) {
        var a = round3(clamp(c.a == null ? 1 : c.a, 0, 1));
        var rgb = Math.round(c.r) + ', ' + Math.round(c.g) + ', ' + Math.round(c.b);
        if (a <= 0) { return zeroRgba ? 'rgba(' + rgb + ', 0)' : 'transparent'; }
        if (a < 1) { return 'rgba(' + rgb + ', ' + a + ')'; }
        return fmt === 'rgb' ? 'rgb(' + rgb + ')' : toHex(c);
    }

    function stopColour(c) {
        var a = round3(clamp(c.a, 0, 1));
        if (a >= 1) { return toHex(c); }
        return 'rgba(' + Math.round(c.r) + ', ' + Math.round(c.g) + ', ' + Math.round(c.b) + ', ' + a + ')';
    }

    function stopList(stops) {
        return stops.slice().sort(function (x, y) { return x.pos - y.pos; }).map(function (s) {
            return stopColour(s) + ' ' + fmtNum(s.pos) + '%';
        }).join(', ');
    }

    function formatGradient(g) {
        if (g.type === 'radial') {
            return 'radial-gradient(' + (g.shape ? g.shape + ', ' : '') + stopList(g.stops) + ')';
        }
        return 'linear-gradient(' + fmtNum(g.angle) + 'deg, ' + stopList(g.stops) + ')';
    }

    function format(state, preferred) {
        if (!state) { return ''; }
        if (state.kind === 'gradient' || state.stops) { return formatGradient(state); }
        var fmt = preferred || ((state.format === 'rgb' || state.format === 'rgba') ? 'rgb' : 'hex');
        return formatSolid(state, fmt, state.format === 'rgba');
    }

    function isTransparent(value) {
        var p = parse(value);
        if (p && p.kind === 'solid') { return p.a < 1; }
        if (p && p.kind === 'gradient') { return p.stops.some(function (s) { return s.a < 1; }); }
        var s = String(value == null ? '' : value).toLowerCase();
        if (/-gradient\(/.test(s)) {
            if (/\btransparent\b/.test(s)) { return true; }
            var m, re = /(rgba?|hsla?)\(([^()]*)\)|#([0-9a-f]{8}|[0-9a-f]{4})\b/g;
            while ((m = re.exec(s))) {
                var c = parseSolid(m[0]);
                if (c && c.a < 1) { return true; }
            }
        }
        return false;
    }

    function cssOk(v) {
        if (!v) { return false; }
        try { return !!(window.CSS && CSS.supports && (CSS.supports('background', v))); } catch (e) { return false; }
    }

    function swatchBackground(value) {
        var v = String(value == null ? '' : value).trim();
        var p = parse(v);
        if (p && p.kind === 'solid') { return p.a <= 0 ? 'transparent' : formatSolid(p, 'rgb', false); }
        if (p) { return formatGradient(p); }
        return cssOk(v) ? v : 'transparent';
    }

    /* ------------------------------------------------------------------ recent colours */

    function loadRecent() {
        try {
            var raw = window.localStorage.getItem(RECENT_KEY);
            var list = raw ? JSON.parse(raw) : [];
            return Array.isArray(list) ? list.filter(function (x) { return typeof x === 'string' && parseSolid(x); }).slice(0, RECENT_MAX) : [];
        } catch (e) { return []; }
    }

    function pushRecent(v) {
        if (!parseSolid(v)) { return; }
        try {
            var key = v.toLowerCase();
            var list = loadRecent().filter(function (x) { return x.toLowerCase() !== key; });
            list.unshift(v);
            window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
        } catch (e) { /* storage blocked */ }
    }

    /* ------------------------------------------------------------------ DOM helpers */

    function el(tag, cls, attrs) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                if (k === 'text') { n.textContent = attrs[k]; } else if (k === 'html') { n.innerHTML = attrs[k]; } else { n.setAttribute(k, attrs[k]); }
            });
        }
        return n;
    }

    function button(cls, label, title) {
        var b = el('button', cls, { type: 'button' });
        if (label != null) { b.innerHTML = label; }
        if (title) { b.title = title; b.setAttribute('aria-label', title); }
        return b;
    }

    function swatchButton(value) {
        var b = button('mcp-s', null, value);
        b.appendChild(el('span', 'mcp-layer mcp-checker'));
        var f = el('span', 'mcp-fill');
        f.style.background = swatchBackground(value);
        b.appendChild(f);
        b.setAttribute('data-value', value);
        return b;
    }

    function preview(cls) {
        var d = el('div', cls);
        d.appendChild(el('div', 'mcp-layer mcp-checker'));
        var f = el('i', 'mcp-fill');
        d.appendChild(f);
        d._fill = f;
        return d;
    }

    function dragArea(area, onPos, onEnd) {
        area.addEventListener('pointerdown', function (ev) {
            if (ev.button !== 0) { return; }
            ev.preventDefault();
            try { area.setPointerCapture(ev.pointerId); } catch (e) { /* old engine */ }
            var move = function (e) { onPos(e, area.getBoundingClientRect()); };
            var up = function (e) {
                area.removeEventListener('pointermove', move);
                area.removeEventListener('pointerup', up);
                area.removeEventListener('pointercancel', up);
                try { area.releasePointerCapture(e.pointerId); } catch (x) { /* ignore */ }
                if (onEnd) { onEnd(); }
            };
            area.addEventListener('pointermove', move);
            area.addEventListener('pointerup', up);
            area.addEventListener('pointercancel', up);
            onPos(ev, area.getBoundingClientRect(), true);
        });
    }

    function arrowStep(ev) {
        var big = ev.shiftKey ? 10 : 1;
        switch (ev.key) {
            case 'ArrowLeft': return { x: -big, y: 0 };
            case 'ArrowRight': return { x: big, y: 0 };
            case 'ArrowUp': return { x: 0, y: big };
            case 'ArrowDown': return { x: 0, y: -big };
            case 'PageUp': return { x: 10, y: 10 };
            case 'PageDown': return { x: -10, y: -10 };
            default: return null;
        }
    }

    /*
     * SV square + hue bar + alpha bar bound to a colour {h, s, v, a} supplied by get()/set().
     */
    function colourControls(get, set, opts) {
        var root = document.createDocumentFragment();
        var sv = el('div', 'mcp-sv' + (opts.short ? ' is-short' : ''));
        sv.appendChild(el('div', 'mcp-sv-w'));
        sv.appendChild(el('div', 'mcp-sv-b'));
        var svKnob = el('div', 'mcp-knob', { tabindex: '0', role: 'slider', 'aria-label': 'Saturation and brightness' });
        sv.appendChild(svKnob);
        var hue = el('div', 'mcp-bar mcp-hue');
        var hueKnob = el('div', 'mcp-knob', { tabindex: '0', role: 'slider', 'aria-label': 'Hue', 'aria-valuemin': '0', 'aria-valuemax': '360' });
        hue.appendChild(hueKnob);
        var alpha = el('div', 'mcp-bar');
        alpha.appendChild(el('div', 'mcp-checker'));
        var ag = el('div', 'mcp-ag');
        alpha.appendChild(ag);
        var alphaKnob = el('div', 'mcp-knob', { tabindex: '0', role: 'slider', 'aria-label': 'Opacity', 'aria-valuemin': '0', 'aria-valuemax': '100' });
        alpha.appendChild(alphaKnob);
        root.appendChild(sv);
        root.appendChild(hue);
        if (opts.alpha) { root.appendChild(alpha); }

        function wake(c) { if (c.a <= 0 && opts.alpha) { c.a = 1; } }

        dragArea(sv, function (ev, r, first) {
            var c = get();
            c.s = clamp((ev.clientX - r.left) / r.width, 0, 1);
            c.v = clamp(1 - (ev.clientY - r.top) / r.height, 0, 1);
            if (first) { wake(c); svKnob.focus({ preventScroll: true }); }
            set(c);
        }, opts.onEnd);
        dragArea(hue, function (ev, r, first) {
            var c = get();
            c.h = clamp((ev.clientX - r.left) / r.width, 0, 1) * 360;
            if (first) { wake(c); hueKnob.focus({ preventScroll: true }); }
            set(c);
        }, opts.onEnd);
        dragArea(alpha, function (ev, r, first) {
            var c = get();
            c.a = round3(clamp((ev.clientX - r.left) / r.width, 0, 1));
            if (first) { alphaKnob.focus({ preventScroll: true }); }
            set(c);
        }, opts.onEnd);

        svKnob.addEventListener('keydown', function (ev) {
            var d = arrowStep(ev);
            if (!d) { return; }
            ev.preventDefault();
            var c = get();
            wake(c);
            c.s = clamp(c.s + d.x / 100, 0, 1);
            c.v = clamp(c.v + d.y / 100, 0, 1);
            set(c);
        });
        hueKnob.addEventListener('keydown', function (ev) {
            var d = arrowStep(ev);
            if (!d) { return; }
            ev.preventDefault();
            var c = get();
            wake(c);
            c.h = clamp(c.h + (d.x || d.y), 0, 360);
            set(c);
        });
        alphaKnob.addEventListener('keydown', function (ev) {
            var d = arrowStep(ev);
            if (!d) { return; }
            ev.preventDefault();
            var c = get();
            c.a = round3(clamp(c.a + (d.x || d.y) / 100, 0, 1));
            set(c);
        });

        function sync() {
            var c = get();
            var rgb = hsvToRgb(c.h, c.s, c.v);
            var pure = hsvToRgb(c.h, 1, 1);
            sv.style.background = toHex(pure);
            svKnob.style.left = (c.s * 100) + '%';
            svKnob.style.top = ((1 - c.v) * 100) + '%';
            svKnob.style.background = toHex(rgb);
            svKnob.setAttribute('aria-valuetext', 'Saturation ' + Math.round(c.s * 100) + '%, brightness ' + Math.round(c.v * 100) + '%');
            hueKnob.style.left = (c.h / 360 * 100) + '%';
            hueKnob.style.background = toHex(pure);
            hueKnob.setAttribute('aria-valuenow', String(Math.round(c.h)));
            var trip = rgb.r + ',' + rgb.g + ',' + rgb.b;
            ag.style.background = 'linear-gradient(to right, rgba(' + trip + ',0), rgb(' + trip + '))';
            alphaKnob.style.left = (c.a * 100) + '%';
            alphaKnob.style.background = 'rgba(' + trip + ',' + c.a + ')';
            alphaKnob.setAttribute('aria-valuenow', String(Math.round(c.a * 100)));
        }

        return { fragment: root, sync: sync, focus: function () { svKnob.focus({ preventScroll: true }); } };
    }

    /* ------------------------------------------------------------------ the picker */

    var current = null;
    var suppress = null;

    function hsva(c) {
        var h = rgbToHsv(c.r, c.g, c.b);
        var out = { h: h.h, s: h.s, v: h.v, a: c.a == null ? 1 : c.a };
        if (c.pos != null) { out.pos = c.pos; }
        return out;
    }

    function themeAccent() {
        try {
            var v = getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim();
            var p = parseSolid(v);
            if (p) { return hsva(p); }
        } catch (e) { /* no theme */ }
        return { h: 0, s: 0, v: 1 };
    }
    function rgba(c) { var rgb = hsvToRgb(c.h, c.s, c.v); rgb.a = c.a; return rgb; }

    function createPicker(opts) {
        var orig = String(opts.value == null ? '' : opts.value);
        var allowAlpha = opts.allowAlpha !== false;
        var allowGradient = !!opts.allowGradient;
        var preferred = opts.preferredFormat === 'rgb' || opts.preferredFormat === 'hex' ? opts.preferredFormat : null;
        var presets = [];
        (Array.isArray(opts.presets) ? opts.presets : []).forEach(function (p) {
            var s = String(p == null ? '' : p).trim();
            if (s && presets.indexOf(s) < 0) { presets.push(s); }
        });
        var anchor = opts.anchor && opts.anchor.nodeType === 1 ? opts.anchor : null;
        var lastRect = anchor ? anchor.getBoundingClientRect() : null;

        var solid, grad, gradRaw, mode, dirty, cssOpen, showAll, closed = false;
        var lastEmit = 0, pending = null;

        function seed() {
            var ps = parseSolid(orig);
            var pg = parseGradient(orig);
            var looksGradient = /^\s*(repeating-)?(linear|radial|conic)-gradient\(/i.test(orig);
            if (ps) {
                solid = hsva(ps);
                if (ps.a <= 0 && ps.format === 'transparent') { var acc = themeAccent(); solid.h = acc.h; solid.s = acc.s; solid.v = acc.v; }
                solid.fmt = preferred || (ps.format === 'rgb' || ps.format === 'rgba' || ps.format === 'hsl' ? 'rgb' : 'hex');
                solid.zeroRgba = ps.format === 'rgba';
            } else if (pg) {
                solid = hsva(pg.stops[0]);
                solid.fmt = preferred || 'hex';
                solid.zeroRgba = false;
            } else {
                solid = { h: 0, s: 0, v: 1, a: 1, fmt: preferred || 'hex', zeroRgba: false };
            }
            if (!allowAlpha) { solid.a = 1; }
            grad = null;
            gradRaw = null;
            cssOpen = false;
            if (allowGradient && pg) {
                grad = { type: pg.type, angle: pg.angle, shape: pg.shape, stops: pg.stops.map(hsva), sel: 0 };
            } else if (allowGradient && looksGradient) {
                grad = defaultGradient();
                gradRaw = orig;
                cssOpen = true;
            }
            mode = grad ? 'gradient' : 'solid';
            dirty = false;
        }

        function defaultGradient() {
            var a = { h: solid.h, s: solid.s, v: solid.v, a: solid.a > 0 ? solid.a : 1 };
            var b = { h: (solid.h + 40) % 360, s: Math.max(0.35, solid.s * 0.6), v: 1, a: a.a };
            a.pos = 0;
            b.pos = 100;
            return { type: 'linear', angle: 90, shape: '', stops: [a, b], sel: 0 };
        }

        function solidValue() { return formatSolid(rgba(solid), solid.fmt, solid.zeroRgba); }
        function gradientValue() { return gradRaw != null ? gradRaw : formatGradient(gradModel()); }
        function gradModel() {
            return {
                type: grad.type, angle: grad.angle, shape: grad.shape,
                stops: grad.stops.map(function (s) { var c = rgba(s); c.pos = s.pos; return c; })
            };
        }
        function output() {
            if (!dirty) { return orig; }
            return mode === 'gradient' ? gradientValue() : solidValue();
        }

        /* ---- events out ---- */
        function emitInput() {
            if (closed || typeof opts.onInput !== 'function') { return; }
            try { opts.onInput(output()); } catch (e) { console.error('[MycelianColorPicker] onInput', e); }
        }
        function scheduleInput() {
            if (typeof opts.onInput !== 'function') { return; }
            var wait = THROTTLE_MS - (Date.now() - lastEmit);
            if (wait <= 0) {
                if (pending) { clearTimeout(pending); pending = null; }
                lastEmit = Date.now();
                emitInput();
            } else if (!pending) {
                pending = setTimeout(function () { pending = null; lastEmit = Date.now(); emitInput(); }, wait);
            }
        }
        function changed(skip) {
            dirty = true;
            render(skip);
            scheduleInput();
        }

        /* ---- build ---- */
        var root = el('div', 'mcp-pop', { role: 'dialog', 'aria-label': opts.title || 'Colour picker', tabindex: '-1' });
        if (opts.title) { root.appendChild(el('div', 'mcp-title', { text: opts.title })); }

        var tabs = el('div', 'mcp-tabs', { role: 'tablist' });
        var tabSolid = button('mcp-tab', svg('circle') + 'Solid');
        var tabGrad = button('mcp-tab', svg('gradient') + 'Gradient');
        tabSolid.setAttribute('role', 'tab');
        tabGrad.setAttribute('role', 'tab');
        tabs.appendChild(tabSolid);
        tabs.appendChild(tabGrad);
        if (allowGradient) { root.appendChild(tabs); }

        /* Solid body */
        var bodyS = el('div', 'mcp-body');
        var ctlS = colourControls(function () { return solid; }, function (c) {
            solid.h = c.h; solid.s = c.s; solid.v = c.v; solid.a = allowAlpha ? c.a : 1;
            changed();
        }, { alpha: allowAlpha });
        bodyS.appendChild(ctlS.fragment);
        var rowS = el('div', 'mcp-row');
        var seg = el('div', 'mcp-seg', { role: 'group', 'aria-label': 'Colour format' });
        var segHex = button('', 'HEX', 'Hex colour');
        var segRgb = button('', 'RGB', 'RGB colour');
        seg.appendChild(segHex);
        seg.appendChild(segRgb);
        var txt = el('input', 'mcp-inp is-text', { type: 'text', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Colour value' });
        var pct = el('input', 'mcp-inp is-num', { type: 'text', inputmode: 'numeric', 'aria-label': 'Opacity percent' });
        var pctUnit = el('span', 'mcp-unit', { text: '%' });
        var eye = button('mcp-ib', svg('colorize'), 'Pick a colour from the screen');
        rowS.appendChild(seg);
        rowS.appendChild(txt);
        if (allowAlpha) { rowS.appendChild(pct); rowS.appendChild(pctUnit); }
        if (typeof window.EyeDropper === 'function') { rowS.appendChild(eye); }
        bodyS.appendChild(rowS);
        var chips = el('div', 'mcp-chips');
        var chipVals = [1, 0.75, 0.5, 0.25, 0];
        var chipEls = chipVals.map(function (v) {
            var c = button('mcp-chip', v > 0 ? (v * 100) + '%' : svg('block'), v > 0 ? 'Opacity ' + (v * 100) + '%' : 'Fully transparent');
            c.addEventListener('click', function () {
                solid.a = v;
                changed();
            });
            chips.appendChild(c);
            return c;
        });
        if (allowAlpha) {
            bodyS.appendChild(el('div', 'mcp-sect', { html: '<span>Opacity</span>' }));
            bodyS.appendChild(chips);
        }
        var compare = el('div', 'mcp-compare');
        var before = preview('is-click');
        before.title = 'Back to the colour you started with';
        before.appendChild(el('span', '', { text: 'Before' }));
        var after = preview('');
        after.appendChild(el('span', '', { text: 'New' }));
        compare.appendChild(before);
        compare.appendChild(after);
        bodyS.appendChild(compare);
        var presetHead = el('div', 'mcp-sect');
        presetHead.appendChild(el('span', '', { text: opts.presetsLabel || 'Template colours' }));
        var showAllBtn = button('mcp-link', 'Show all ' + presets.length);
        if (presets.length > PRESET_ROWS) { presetHead.appendChild(showAllBtn); }
        var presetGrid = el('div', 'mcp-swatches');
        var recent = loadRecent();
        var recentGrid = el('div', 'mcp-swatches');
        if (presets.length) { bodyS.appendChild(presetHead); bodyS.appendChild(presetGrid); }
        if (recent.length) {
            bodyS.appendChild(el('div', 'mcp-sect', { html: '<span>Recent</span>' }));
            bodyS.appendChild(recentGrid);
            recent.forEach(function (v) { recentGrid.appendChild(swatchButton(v)); });
        }
        root.appendChild(bodyS);

        /* Gradient body */
        var bodyG = el('div', 'mcp-body');
        var rowG = el('div', 'mcp-row');
        var segG = el('div', 'mcp-seg', { role: 'group', 'aria-label': 'Gradient type' });
        var segLin = button('', 'Linear', 'Linear gradient');
        var segRad = button('', 'Radial', 'Radial gradient');
        segG.appendChild(segLin);
        segG.appendChild(segRad);
        rowG.appendChild(segG);
        rowG.appendChild(el('span', 'mcp-sp'));
        var dial = el('span', 'mcp-angle', { tabindex: '0', role: 'slider', 'aria-label': 'Angle', 'aria-valuemin': '-360', 'aria-valuemax': '360' });
        var dialHand = el('i');
        dial.appendChild(dialHand);
        var angIn = el('input', 'mcp-inp is-num', { type: 'text', inputmode: 'numeric', 'aria-label': 'Angle in degrees' });
        var angUnit = el('span', 'mcp-unit', { text: '°' });
        rowG.appendChild(dial);
        rowG.appendChild(angIn);
        rowG.appendChild(angUnit);
        bodyG.appendChild(rowG);
        var gbar = el('div', 'mcp-gbar', { title: 'Click to add a stop' });
        gbar.appendChild(el('div', 'mcp-checker'));
        var gfill = el('div', 'mcp-g');
        gbar.appendChild(gfill);
        bodyG.appendChild(gbar);
        var stopRow = el('div', 'mcp-stoprow');
        var mini = preview('mcp-mini');
        var stopTxt = el('input', 'mcp-inp is-text', { type: 'text', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Stop colour' });
        var stopA = el('input', 'mcp-inp is-num', { type: 'text', inputmode: 'numeric', 'aria-label': 'Stop opacity percent' });
        var stopPos = el('input', 'mcp-inp is-num', { type: 'text', inputmode: 'numeric', 'aria-label': 'Stop position percent' });
        var stopDel = button('mcp-ib', svg('trash'), 'Remove this stop');
        stopRow.appendChild(mini);
        stopRow.appendChild(stopTxt);
        stopRow.appendChild(stopA);
        stopRow.appendChild(el('span', 'mcp-unit', { text: '%' }));
        stopRow.appendChild(stopPos);
        stopRow.appendChild(el('span', 'mcp-unit', { text: 'pos' }));
        stopRow.appendChild(stopDel);
        bodyG.appendChild(stopRow);
        var ctlG = colourControls(function () { return selStop(); }, function (c) {
            var s = selStop();
            s.h = c.h; s.s = c.s; s.v = c.v; s.a = c.a;
            gradRaw = null;
            changed();
        }, { alpha: true, short: true });
        bodyG.appendChild(ctlG.fragment);
        var resHead = el('div', 'mcp-sect');
        resHead.appendChild(el('span', '', { text: 'Result' }));
        var cssBtn = button('mcp-link', 'Edit as CSS');
        resHead.appendChild(cssBtn);
        bodyG.appendChild(resHead);
        var result = el('div', 'mcp-compare is-single');
        var resultPrev = preview('');
        result.appendChild(resultPrev);
        bodyG.appendChild(result);
        var cssLine = el('div', 'mcp-css');
        var cssArea = el('textarea', 'mcp-css', { spellcheck: 'false', rows: '3', 'aria-label': 'Gradient CSS' });
        var cssNote = el('div', 'mcp-note');
        bodyG.appendChild(cssLine);
        bodyG.appendChild(cssArea);
        bodyG.appendChild(cssNote);
        if (allowGradient) { root.appendChild(bodyG); }

        /* Footer */
        var foot = el('div', 'mcp-foot');
        var footIcon = el('span', '');
        var hint = el('span', 'mcp-hint');
        var cancelBtn = button('mcp-btn', 'Cancel');
        var doneBtn = button('mcp-btn is-primary', 'Done');
        foot.appendChild(footIcon);
        foot.appendChild(hint);
        foot.appendChild(cancelBtn);
        foot.appendChild(doneBtn);
        root.appendChild(foot);

        function selStop() {
            if (!grad) { grad = defaultGradient(); }
            grad.sel = clamp(grad.sel || 0, 0, grad.stops.length - 1);
            return grad.stops[grad.sel];
        }

        /* ---- render ---- */
        var stopEls = [];
        function renderStops() {
            while (stopEls.length > grad.stops.length) { gbar.removeChild(stopEls.pop()); }
            while (stopEls.length < grad.stops.length) {
                var s = el('div', 'mcp-stop', { tabindex: '0', role: 'slider', 'aria-label': 'Gradient stop', 'aria-valuemin': '0', 'aria-valuemax': '100' });
                gbar.appendChild(s);
                stopEls.push(s);
            }
            grad.stops.forEach(function (st, i) {
                var n = stopEls[i];
                n._index = i;
                n.style.left = st.pos + '%';
                n.style.background = stopColour(rgba(st));
                n.classList.toggle('is-on', i === grad.sel);
                n.setAttribute('aria-valuenow', fmtNum(st.pos));
            });
        }

        function setVal(input, v, skip) { if (input !== skip && input.value !== v) { input.value = v; } }

        function render(skip) {
            var isG = mode === 'gradient' && allowGradient;
            tabSolid.classList.toggle('is-on', !isG);
            tabGrad.classList.toggle('is-on', isG);
            tabSolid.setAttribute('aria-selected', String(!isG));
            tabGrad.setAttribute('aria-selected', String(isG));
            bodyS.classList.toggle('mcp-hide', isG);
            bodyG.classList.toggle('mcp-hide', !isG);
            var out = output();
            if (presetGrid.childElementCount !== (showAll ? presets.length : Math.min(presets.length, PRESET_ROWS))) {
                presetGrid.innerHTML = '';
                (showAll ? presets : presets.slice(0, PRESET_ROWS)).forEach(function (v) { presetGrid.appendChild(swatchButton(v)); });
                presetGrid.classList.toggle('is-all', !!showAll);
                showAllBtn.textContent = showAll ? 'Show fewer' : 'Show all ' + presets.length;
            }
            if (!isG) {
                ctlS.sync();
                var c = rgba(solid);
                segHex.classList.toggle('is-on', solid.fmt !== 'rgb');
                segRgb.classList.toggle('is-on', solid.fmt === 'rgb');
                if (skip !== txt) { txt.classList.remove('is-bad'); }
                setVal(txt, solid.fmt === 'rgb' ? 'rgb(' + c.r + ', ' + c.g + ', ' + c.b + ')' : toHex(c), skip);
                setVal(pct, String(Math.round(solid.a * 100)), skip);
                chipEls.forEach(function (ch, i) { ch.classList.toggle('is-on', Math.abs(chipVals[i] - solid.a) < 0.005); });
                before._fill.style.background = swatchBackground(orig);
                after._fill.style.background = swatchBackground(out);
                var key = out.toLowerCase();
                [presetGrid, recentGrid].forEach(function (grid) {
                    Array.prototype.forEach.call(grid.children, function (b) {
                        var v = b.getAttribute('data-value') || '';
                        var p = parseSolid(v);
                        var same = v.toLowerCase() === key || (p && dirty && formatSolid(p, solid.fmt, solid.zeroRgba).toLowerCase() === key);
                        b.classList.toggle('is-on', !!same);
                    });
                });
                footIcon.innerHTML = svg('info');
                hint.textContent = opts.note || (typeof opts.onInput === 'function' ? 'Changes show in the preview as you pick' : 'Enter or Done applies the colour');
            } else {
                var st = selStop();
                ctlG.sync();
                renderStops();
                var lin = grad.type !== 'radial';
                segLin.classList.toggle('is-on', lin);
                segRad.classList.toggle('is-on', !lin);
                dial.classList.toggle('mcp-hide', !lin);
                angIn.classList.toggle('mcp-hide', !lin);
                angUnit.classList.toggle('mcp-hide', !lin);
                dialHand.style.transform = 'rotate(' + (grad.angle + 180) + 'deg)';
                dial.setAttribute('aria-valuenow', fmtNum(grad.angle));
                setVal(angIn, fmtNum(grad.angle), skip);
                var sc = rgba(st);
                mini._fill.style.background = stopColour(sc);
                if (skip !== stopTxt) { stopTxt.classList.remove('is-bad'); }
                setVal(stopTxt, toHex(sc), skip);
                setVal(stopA, String(Math.round(st.a * 100)), skip);
                setVal(stopPos, fmtNum(st.pos), skip);
                stopDel.disabled = grad.stops.length <= 2;
                gfill.style.background = 'linear-gradient(to right, ' + stopList(gradModel().stops) + ')';
                var gv = dirty ? gradientValue() : (gradRaw != null ? gradRaw : (parseGradient(orig) ? orig : gradientValue()));
                resultPrev._fill.style.background = cssOk(gv) ? gv : 'transparent';
                cssLine.textContent = gv;
                cssLine.classList.toggle('mcp-hide', cssOpen);
                cssArea.classList.toggle('mcp-hide', !cssOpen);
                if (cssOpen && skip !== cssArea) { cssArea.value = gv; cssArea.classList.remove('is-bad'); }
                cssBtn.textContent = cssOpen ? 'Hide CSS' : 'Edit as CSS';
                if (skip !== cssArea) {
                    var raw = gradRaw != null && !parseGradient(gradRaw);
                    cssNote.textContent = raw ? 'This gradient uses options the stop editor can’t show, so it is kept as written. Edit the CSS here.' : '';
                    cssNote.classList.remove('is-bad');
                }
                cssNote.classList.toggle('mcp-hide', !cssOpen || !cssNote.textContent);
                footIcon.innerHTML = svg('touch');
                hint.textContent = 'Click the bar to add a stop · drag to move';
            }
        }

        /* ---- behaviour: solid ---- */
        function applySolidText(text, src) {
            var t = String(text || '').trim();
            if (/^[0-9a-f]{3}([0-9a-f]{3})?$/i.test(t)) { t = '#' + t; }
            var p = parseSolid(t);
            if (!p) { return false; }
            var keepA = solid.a;
            var h = hsva(p);
            if (h.s === 0 || h.v === 0) { h.h = solid.h; }
            solid.h = h.h; solid.s = h.s; solid.v = h.v;
            var explicitA = p.format === 'transparent' || p.format === 'rgba' || (p.format === 'hex' && p.a < 1) || p.a < 1;
            solid.a = allowAlpha ? (explicitA ? p.a : (keepA > 0 ? keepA : 1)) : 1;
            if (p.format === 'rgb' || p.format === 'rgba') { solid.fmt = 'rgb'; } else if (p.format === 'hex') { solid.fmt = 'hex'; }
            changed(src);
            return true;
        }

        function applyPreset(v) {
            var pg = allowGradient ? parseGradient(v) : null;
            if (pg) {
                grad = { type: pg.type, angle: pg.angle, shape: pg.shape, stops: pg.stops.map(hsva), sel: 0 };
                gradRaw = null;
                mode = 'gradient';
                changed();
                position();
                return;
            }
            var p = parseSolid(v);
            if (!p) { return; }
            var h = hsva(p);
            if (p.a <= 0) { h.h = solid.h; h.s = solid.s; h.v = solid.v; }
            solid.h = h.h; solid.s = h.s; solid.v = h.v;
            solid.a = allowAlpha ? p.a : 1;
            changed();
        }

        segHex.addEventListener('click', function () { solid.fmt = 'hex'; changed(); });
        segRgb.addEventListener('click', function () { solid.fmt = 'rgb'; changed(); });
        txt.addEventListener('input', function () {
            var ok = applySolidText(txt.value, txt);
            txt.classList.toggle('is-bad', !ok && txt.value.trim() !== '');
        });
        txt.addEventListener('blur', function () { render(); });
        pct.addEventListener('input', function () {
            var n = parseFloat(pct.value);
            if (isNaN(n)) { return; }
            solid.a = round3(clamp(n, 0, 100) / 100);
            changed(pct);
        });
        pct.addEventListener('blur', function () { render(); });
        eye.addEventListener('click', function () {
            try {
                new window.EyeDropper().open().then(function (r) {
                    if (r && r.sRGBHex) { applySolidText(r.sRGBHex); }
                }).catch(function () { /* dismissed */ });
            } catch (e) { /* unsupported */ }
        });
        before.addEventListener('click', function () {
            seed();
            render();
            scheduleInput();
        });
        showAllBtn.addEventListener('click', function () { showAll = !showAll; render(); position(); });
        function onSwatch(ev) {
            var b = ev.target.closest ? ev.target.closest('.mcp-s') : null;
            if (b) { applyPreset(b.getAttribute('data-value') || ''); }
        }
        presetGrid.addEventListener('click', onSwatch);
        recentGrid.addEventListener('click', onSwatch);

        /* ---- behaviour: gradient ---- */
        tabSolid.addEventListener('click', function () {
            if (mode === 'solid') { return; }
            if (grad) {
                var s = selStop();
                solid.h = s.h; solid.s = s.s; solid.v = s.v; solid.a = allowAlpha ? s.a : 1;
            }
            mode = 'solid';
            changed();
            position();
        });
        tabGrad.addEventListener('click', function () {
            if (mode === 'gradient') { return; }
            if (!grad) { grad = defaultGradient(); }
            mode = 'gradient';
            changed();
            position();
        });
        segLin.addEventListener('click', function () { grad.type = 'linear'; gradRaw = null; changed(); });
        segRad.addEventListener('click', function () { grad.type = 'radial'; gradRaw = null; changed(); });

        function setAngle(a, src) {
            a = Math.round(a);
            if (a > 360 || a < -360) { a = a % 360; }
            grad.angle = a;
            gradRaw = null;
            changed(src);
        }
        dragArea(dial, function (ev) {
            var r = dial.getBoundingClientRect();
            var dx = ev.clientX - (r.left + r.width / 2), dy = ev.clientY - (r.top + r.height / 2);
            if (Math.abs(dx) + Math.abs(dy) < 2) { return; }
            var a = Math.atan2(dx, -dy) * 180 / Math.PI;
            if (a < 0) { a += 360; }
            if (ev.shiftKey) { a = Math.round(a / 15) * 15; }
            dial.focus({ preventScroll: true });
            setAngle(a % 360);
        });
        dial.addEventListener('keydown', function (ev) {
            var d = arrowStep(ev);
            if (!d) { return; }
            ev.preventDefault();
            setAngle(grad.angle + (ev.shiftKey ? 15 * Math.sign(d.x || d.y) : (d.x || d.y)));
        });
        angIn.addEventListener('input', function () {
            var n = parseFloat(angIn.value);
            if (!isNaN(n)) { setAngle(n, angIn); }
        });
        angIn.addEventListener('blur', function () { render(); });

        function sampleAt(pos) {
            var st = gradModel().stops.sort(function (x, y) { return x.pos - y.pos; });
            var lo = st[0], hi = st[st.length - 1];
            for (var i = 0; i < st.length - 1; i++) {
                if (pos >= st[i].pos && pos <= st[i + 1].pos) { lo = st[i]; hi = st[i + 1]; break; }
            }
            if (pos <= st[0].pos) { hi = lo; }
            if (pos >= st[st.length - 1].pos) { lo = hi; }
            var t = hi.pos === lo.pos ? 0 : (pos - lo.pos) / (hi.pos - lo.pos);
            return {
                r: lo.r + (hi.r - lo.r) * t, g: lo.g + (hi.g - lo.g) * t, b: lo.b + (hi.b - lo.b) * t, a: round3(lo.a + (hi.a - lo.a) * t)
            };
        }

        var dragStop = null;
        gbar.addEventListener('pointerdown', function (ev) {
            if (ev.button !== 0) { return; }
            ev.preventDefault();
            var r = gbar.getBoundingClientRect();
            var pos = Math.round(clamp((ev.clientX - r.left) / r.width * 100, 0, 100) * 10) / 10;
            var hit = ev.target.closest ? ev.target.closest('.mcp-stop') : null;
            if (hit && hit._index != null) {
                grad.sel = hit._index;
            } else {
                var c = sampleAt(pos);
                var s = hsva({ r: Math.round(c.r), g: Math.round(c.g), b: Math.round(c.b), a: c.a });
                s.pos = pos;
                grad.stops.push(s);
                grad.sel = grad.stops.length - 1;
                gradRaw = null;
                changed();
            }
            dragStop = grad.stops[grad.sel];
            try { gbar.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
            render();
            var n = stopEls[grad.sel];
            if (n) { n.focus({ preventScroll: true }); }
        });
        gbar.addEventListener('pointermove', function (ev) {
            if (!dragStop) { return; }
            var r = gbar.getBoundingClientRect();
            dragStop.pos = Math.round(clamp((ev.clientX - r.left) / r.width * 100, 0, 100) * 10) / 10;
            gradRaw = null;
            changed();
        });
        function endStopDrag(ev) {
            if (!dragStop) { return; }
            dragStop = null;
            try { gbar.releasePointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
        }
        gbar.addEventListener('pointerup', endStopDrag);
        gbar.addEventListener('pointercancel', endStopDrag);
        gbar.addEventListener('keydown', function (ev) {
            var n = ev.target.closest ? ev.target.closest('.mcp-stop') : null;
            if (!n || n._index == null) { return; }
            grad.sel = n._index;
            if (ev.key === 'Delete' || ev.key === 'Backspace') {
                ev.preventDefault();
                removeStop();
                return;
            }
            var d = arrowStep(ev);
            if (!d || d.y) { return; }
            ev.preventDefault();
            var s = selStop();
            s.pos = clamp(Math.round((s.pos + d.x) * 10) / 10, 0, 100);
            gradRaw = null;
            changed();
        });

        function removeStop() {
            if (grad.stops.length <= 2) { return; }
            grad.stops.splice(grad.sel, 1);
            grad.sel = Math.max(0, grad.sel - 1);
            gradRaw = null;
            changed();
            var n = stopEls[grad.sel];
            if (n) { n.focus({ preventScroll: true }); }
        }
        stopDel.addEventListener('click', removeStop);
        stopTxt.addEventListener('input', function () {
            var t = stopTxt.value.trim();
            if (/^[0-9a-f]{3}([0-9a-f]{3})?$/i.test(t)) { t = '#' + t; }
            var p = parseSolid(t);
            stopTxt.classList.toggle('is-bad', !p && t !== '');
            if (!p) { return; }
            var s = selStop();
            var h = hsva(p);
            if (h.s === 0 || h.v === 0) { h.h = s.h; }
            s.h = h.h; s.s = h.s; s.v = h.v;
            if (p.a < 1 || p.format === 'rgba' || p.format === 'transparent') { s.a = p.a; }
            gradRaw = null;
            changed(stopTxt);
        });
        stopA.addEventListener('input', function () {
            var n = parseFloat(stopA.value);
            if (isNaN(n)) { return; }
            selStop().a = round3(clamp(n, 0, 100) / 100);
            gradRaw = null;
            changed(stopA);
        });
        stopPos.addEventListener('input', function () {
            var n = parseFloat(stopPos.value);
            if (isNaN(n)) { return; }
            selStop().pos = clamp(Math.round(n * 10) / 10, 0, 100);
            gradRaw = null;
            changed(stopPos);
        });
        [stopTxt, stopA, stopPos].forEach(function (i) { i.addEventListener('blur', function () { render(); }); });
        cssBtn.addEventListener('click', function () {
            cssOpen = !cssOpen;
            render();
            position();
            if (cssOpen) { cssArea.focus({ preventScroll: true }); }
        });
        cssArea.addEventListener('input', function () {
            var t = cssArea.value.trim();
            var pg = parseGradient(t);
            var ok = !!pg || (/-gradient\(/i.test(t) && cssOk(t));
            cssArea.classList.toggle('is-bad', !ok);
            if (!ok) {
                cssNote.textContent = 'Not a CSS gradient yet — the last valid one is kept.';
                cssNote.classList.add('is-bad');
                cssNote.classList.remove('mcp-hide');
                return;
            }
            if (pg) {
                var sel = grad ? grad.sel : 0;
                grad = { type: pg.type, angle: pg.angle, shape: pg.shape, stops: pg.stops.map(hsva), sel: Math.min(sel, pg.stops.length - 1) };
            }
            gradRaw = t;
            cssNote.textContent = pg ? '' : 'Kept as written — the stop editor can’t show this one.';
            cssNote.classList.remove('is-bad');
            cssNote.classList.toggle('mcp-hide', !cssNote.textContent);
            changed(cssArea);
        });

        /* ---- open / close ---- */
        function position() {
            if (closed) { return; }
            var vw = window.innerWidth || document.documentElement.clientWidth;
            var vh = window.innerHeight || document.documentElement.clientHeight;
            var m = 8;
            var w = root.offsetWidth, h = root.offsetHeight;
            if (anchor && anchor.isConnected) {
                var ar = anchor.getBoundingClientRect();
                if (ar.width || ar.height) { lastRect = ar; }
            }
            var r = lastRect || { left: (vw - w) / 2, right: (vw + w) / 2, top: vh / 2 - h / 2 - 6, bottom: vh / 2 - h / 2 - 6 };
            var left = r.left;
            if (left + w > vw - m) { left = r.right - w; }
            left = clamp(left, m, Math.max(m, vw - w - m));
            var top = r.bottom + 6;
            if (top + h > vh - m) {
                var above = r.top - 6 - h;
                top = above >= m ? above : Math.max(m, vh - h - m);
            }
            root.style.left = Math.round(left) + 'px';
            root.style.top = Math.round(top) + 'px';
        }

        function finish(how) {
            if (closed) { return; }
            if (pending) { clearTimeout(pending); pending = null; }
            var value = output();
            var hadFocus = root.contains(document.activeElement);
            closed = true;
            document.removeEventListener('pointerdown', onDocDown, true);
            document.removeEventListener('keydown', onKey, true);
            window.removeEventListener('resize', position);
            window.removeEventListener('scroll', onScroll, true);
            if (root.parentNode) { root.parentNode.removeChild(root); }
            if (anchor) { anchor.classList.remove('is-picking'); }
            if (current === api) { current = null; }
            if (hadFocus && anchor && anchor.isConnected && typeof anchor.focus === 'function') {
                try { anchor.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
            }
            try {
                if (how === 'cancel') {
                    if (typeof opts.onCancel === 'function') { opts.onCancel(); }
                } else {
                    if (dirty && mode !== 'gradient') { pushRecent(value); }
                    if (typeof opts.onDone === 'function') { opts.onDone(value); }
                }
            } catch (e) { console.error('[MycelianColorPicker] callback', e); }
        }

        function onDocDown(ev) {
            if (root.contains(ev.target)) { return; }
            if (anchor && anchor.contains(ev.target)) { suppress = { anchor: anchor, t: Date.now() }; }
            finish('done');
        }
        function onKey(ev) {
            if (ev.key === 'Escape') {
                ev.preventDefault();
                ev.stopPropagation();
                finish('cancel');
                return;
            }
            if (ev.key === 'Enter' && root.contains(ev.target) && ev.target.tagName === 'INPUT') {
                ev.preventDefault();
                ev.stopPropagation();
                finish('done');
            }
        }
        function onScroll(ev) { if (!root.contains(ev.target)) { position(); } }

        cancelBtn.addEventListener('click', function () { finish('cancel'); });
        doneBtn.addEventListener('click', function () { finish('done'); });

        var api = { close: function (how) { finish(how === 'cancel' ? 'cancel' : 'done'); }, element: root, value: output };

        seed();
        root.style.left = '-9999px';
        root.style.top = '0px';
        var host = (anchor && anchor.closest && anchor.closest('.q-dialog__inner')) || document.body;
        host.appendChild(root);
        render();
        position();
        if (anchor) { anchor.classList.add('is-picking'); }
        document.addEventListener('pointerdown', onDocDown, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', position);
        window.addEventListener('scroll', onScroll, true);
        if (opts.autoFocus !== false) {
            try {
                if (mode === 'gradient' && cssOpen) { cssArea.focus({ preventScroll: true }); } else if (mode === 'gradient') { ctlG.focus(); } else { ctlS.focus(); }
            } catch (e) { /* ignore */ }
        }
        return api;
    }

    function open(opts) {
        opts = opts || {};
        if (suppress && opts.anchor && suppress.anchor === opts.anchor && Date.now() - suppress.t < 500) {
            suppress = null;
            return null;
        }
        suppress = null;
        if (current) { current.close('done'); }
        current = createPicker(opts);
        return current;
    }

    window.MycelianColorPicker = {
        open: open,
        close: function (how) { if (current) { current.close(how); } },
        isOpen: function () { return !!current; },
        parse: parse,
        format: format,
        isTransparent: isTransparent,
        swatchBackground: swatchBackground,
        _internal: { parseSolid: parseSolid, parseGradient: parseGradient, formatSolid: formatSolid, formatGradient: formatGradient }
    };
})();

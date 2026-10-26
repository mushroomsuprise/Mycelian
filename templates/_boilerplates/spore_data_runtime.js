/* Spore Studio shared data/counter/control runtime (injected into overlays). */
(function () {
    if (typeof window === 'undefined') { return; }
    if (window.__sporeDataRuntimeLoaded) { return; }
    window.__sporeDataRuntimeLoaded = true;

    window.__sporeCounters = window.__sporeCounters || {};
    window.__sporeCounterMeta = window.__sporeCounterMeta || {};
    window.__sporeDataDisplays = window.__sporeDataDisplays || [];
    window.__sporeCounterImages = window.__sporeCounterImages || [];
    window.__sporeCounterLastRendered = window.__sporeCounterLastRendered || {};
    window.__sporeCounterImageLastSrc = window.__sporeCounterImageLastSrc || {};
    window.__sporeDisplayLastRendered = window.__sporeDisplayLastRendered || {};
    window.__sporeTwitchCache = window.__sporeTwitchCache || {};
    window.__sporeConfigCache = window.__sporeConfigCache || {};
    window.__sporeLastPayload = window.__sporeLastPayload || {};

    function sporeFormatDisplay(value, format) {
        var fmt = (format == null || format === '') ? '{value}' : String(format);
        var v = (value == null) ? '' : String(value);
        return fmt.split('{value}').join(v);
    }

    function sporeFormatBoundToken(bound) {
        if (bound == null || bound === '') { return ''; }
        return String(bound);
    }

    function sporeFormatCounterDisplay(value, format, minBound, maxBound) {
        var fmt = (format == null || format === '') ? '{value}' : String(format);
        var v = (value == null) ? '' : String(value);
        return fmt
            .split('{value}').join(v)
            .split('{min}').join(sporeFormatBoundToken(minBound))
            .split('{max}').join(sporeFormatBoundToken(maxBound));
    }

    function sporeCoerceNumber(v, fallback) {
        if (v == null || v === '') { return fallback == null ? 0 : fallback; }
        var n = Number(v);
        return (typeof n === 'number' && !isNaN(n)) ? n : (fallback == null ? 0 : fallback);
    }

    /* Publish a widget result (wheel_result, timer_finished, ...) for other templates.
     * window.__sporeOutputs (compiled per template) says which outputs are enabled. */
    function sporeEmitOutput(event, elementId, data) {
        var table = window.__sporeOutputs;
        if (Array.isArray(table)) {
            var enabled = false;
            for (var i = 0; i < table.length; i++) {
                if (table[i].event === event && table[i].element_id === elementId) {
                    enabled = !!table[i].enabled;
                    break;
                }
            }
            if (!enabled) { return; }
        }
        if (typeof socket === 'undefined' || !socket || !socket.emit) { return; }
        var payload = {};
        Object.keys(data || {}).forEach(function (k) { payload[k] = data[k]; });
        payload.template = window.__sporeTemplateName || '';
        payload.elementId = elementId;
        socket.emit(event, payload);
    }

    function sporeRandomDelta(spec) {
        spec = spec || {};
        var kind = spec.kind || spec.type || 'fixed';
        if (kind === 'fixed') {
            return sporeCoerceNumber(spec.value, 1);
        }
        if (kind === 'random_int') {
            var minI = Math.floor(sporeCoerceNumber(spec.min, 0));
            var maxI = Math.floor(sporeCoerceNumber(spec.max, minI));
            if (maxI < minI) { var t = minI; minI = maxI; maxI = t; }
            return minI + Math.floor(Math.random() * (maxI - minI + 1));
        }
        if (kind === 'random_float') {
            var minF = sporeCoerceNumber(spec.min, 0);
            var maxF = sporeCoerceNumber(spec.max, minF);
            if (maxF < minF) { var t2 = minF; minF = maxF; maxF = t2; }
            var raw = minF + Math.random() * (maxF - minF);
            var dec = parseInt(spec.decimals, 10);
            if (!isNaN(dec) && dec >= 0) {
                var p = Math.pow(10, dec);
                return Math.round(raw * p) / p;
            }
            return raw;
        }
        if (kind === 'data_source') {
            return sporeResolveSource(
                {
                    kind: 'data_source',
                    source: spec.source,
                    source_id: spec.source_id,
                    runtime_path: spec.runtime_path,
                    runtime_key: spec.runtime_key,
                    twitch_binding: spec.twitch_binding,
                    twitch_path: spec.twitch_path,
                    tier_filter: spec.tier_filter,
                    alert_type_filter: spec.alert_type_filter,
                    fallback: spec.fallback
                },
                window.__sporeLastPayload.event || {},
                window.__sporeLastPayload.ctx || {}
            );
        }
        return sporeCoerceNumber(spec.value, 1);
    }

    function sporePayloadKeyForSource(sourceId) {
        if (!sourceId) { return ''; }
        var map = window.__sporeAlertPayloadKeys || {};
        if (map[sourceId]) { return map[sourceId]; }
        var cmap = window.__sporeChatPayloadKeys || {};
        if (cmap[sourceId]) { return cmap[sourceId]; }
        if (sourceId.indexOf('alert.') === 0) {
            return sourceId.slice(6);
        }
        if (sourceId.indexOf('chat.') === 0) {
            return sourceId.slice(5);
        }
        return '';
    }

    function sporeAlertType(payload) {
        if (!payload || typeof payload !== 'object') { return ''; }
        return String(
            payload.alert_type || payload.alerttype || payload.type || ''
        ).toLowerCase();
    }

    function sporeNormalizeSubTier(tier) {
        if (tier == null || tier === '') { return null; }
        var n = Number(tier);
        if (typeof n === 'number' && !isNaN(n)) {
            if (n >= 1000) { return Math.floor(n / 1000); }
            if (n >= 1 && n <= 3) { return n; }
        }
        var s = String(tier).toLowerCase();
        if (s === '1000' || s === 'prime') { return 1; }
        if (s === '2000') { return 2; }
        if (s === '3000') { return 3; }
        return (typeof n === 'number' && !isNaN(n)) ? n : null;
    }

    function sporeTierMatches(filterTier, payloadTier) {
        var f = filterTier == null || filterTier === '' ? 'any' : String(filterTier);
        if (f === 'any') { return true; }
        var want = sporeNormalizeSubTier(f);
        var got = sporeNormalizeSubTier(payloadTier);
        if (want == null) { return true; }
        if (got == null) { return false; }
        return want === got;
    }

    function sporeResolveSubDelta(sourceId, spec, payload) {
        var fb = spec && spec.fallback != null ? sporeCoerceNumber(spec.fallback, 0) : 0;
        if (!payload || typeof payload !== 'object') { return fb; }
        if (!sporeTierMatches(spec.tier_filter, payload.tier)) { return fb; }
        var at = sporeAlertType(payload);
        if (sourceId === 'sub.new_sub') {
            if (at === 'sub') { return 1; }
            return fb;
        }
        if (sourceId === 'sub.resub') {
            if (at === 'resub') { return 1; }
            return fb;
        }
        if (sourceId === 'sub.gift_sub') {
            if (at === 'giftsub' || at === 'gift_sub') {
                var qty = payload.gift_qty != null ? payload.gift_qty : payload.quantity;
                return sporeCoerceNumber(qty, fb);
            }
            return fb;
        }
        return fb;
    }

    function sporeEffectiveSourceId(spec) {
        spec = spec || {};
        var sourceId = spec.source || '';
        if (sourceId === 'counter' || sourceId === 'counter.{id}') {
            return spec.source_id ? ('counter.' + spec.source_id) : '';
        }
        if (sourceId === 'config' || sourceId === 'config.{id}') {
            return spec.source_id ? ('config.' + spec.source_id) : '';
        }
        if (sourceId === 'runtime' || sourceId === 'runtime.{path}.{key}') {
            if (spec.runtime_path && spec.runtime_key) {
                return 'runtime.' + spec.runtime_path + '.' + spec.runtime_key;
            }
            return '';
        }
        if (sourceId === 'twitch' || sourceId === 'twitch.{binding_id}.{path}') {
            if (spec.twitch_binding && spec.twitch_path) {
                return 'twitch.' + spec.twitch_binding + '.' + spec.twitch_path;
            }
            return '';
        }
        if (sourceId === 'stats') {
            return spec.source_id ? ('stats.' + spec.source_id) : '';
        }
        return sourceId;
    }

    function sporeSpecPassesAlertFilters(spec, payload) {
        spec = spec || {};
        var atf = spec.alert_type_filter;
        if (atf && String(atf).toLowerCase() !== 'any') {
            if (sporeAlertType(payload) !== String(atf).toLowerCase()) { return false; }
        }
        var tf = spec.tier_filter;
        if (tf && String(tf) !== 'any') {
            var tier = payload && payload.tier;
            if (!sporeTierMatches(tf, tier)) { return false; }
        }
        return true;
    }

    function sporeResolveSource(spec, payload, ctx) {
        spec = spec || {};
        var kind = spec.kind || 'data_source';
        if (kind === 'fixed') {
            return spec.value;
        }
        if (kind === 'random_int' || kind === 'random_float') {
            return sporeRandomDelta(spec);
        }
        var sourceId = sporeEffectiveSourceId(spec);
        if (!sourceId && kind === 'data_source') { return spec.fallback; }

        var alertish = sourceId.indexOf('alert.') === 0 || sourceId.indexOf('sub.') === 0;
        if (alertish && !sporeSpecPassesAlertFilters(spec, payload)) {
            return spec.fallback != null ? spec.fallback : 0;
        }

        if (sourceId === 'sub.new_sub' || sourceId === 'sub.resub' ||
            sourceId === 'sub.gift_sub') {
            return sporeResolveSubDelta(sourceId, spec, payload);
        }

        if (sourceId === 'alerts.paused') {
            return !!(payload && payload.paused);
        }

        var pk = sporePayloadKeyForSource(sourceId);
        if (pk && payload && Object.prototype.hasOwnProperty.call(payload, pk)) {
            var pv = payload[pk];
            if (sourceId === 'chat.message_length' && typeof pv === 'string') {
                return pv.length;
            }
            return pv;
        }

        if (sourceId.indexOf('stats.') === 0) {
            var stats = (ctx && ctx.stats) || window.__sporeStatsCache || {};
            return stats[sourceId.slice(6)];
        }

        if (sourceId.indexOf('chatbot.') === 0) {
            var cb = (ctx && ctx.chatbot) || window.__sporeChatbotCache || {};
            return cb[sourceId.slice(8)];
        }

        if (sourceId.indexOf('counter.') === 0) {
            var cid = sourceId.slice(8);
            var meta = window.__sporeCounterMeta[cid];
            if (meta && meta.elementId != null) {
                return window.__sporeCounters[cid];
            }
            return window.__sporeCounters[cid];
        }

        if (sourceId.indexOf('config.') === 0) {
            var fid = sourceId.slice(7);
            return window.__sporeConfigCache[fid];
        }

        if (sourceId.indexOf('runtime.') === 0) {
            var rest = sourceId.slice(8);
            var dot = rest.indexOf('.');
            if (dot > 0) {
                var rpath = rest.slice(0, dot);
                var rkey = rest.slice(dot + 1);
                var doc = (ctx && ctx.runtime && ctx.runtime[rpath]) || {};
                return doc[rkey];
            }
        }

        if (sourceId.indexOf('twitch.') === 0) {
            var tw = sourceId.slice(7);
            var dot2 = tw.indexOf('.');
            if (dot2 > 0) {
                var bid = tw.slice(0, dot2);
                var path = tw.slice(dot2 + 1);
                var cached = window.__sporeTwitchCache[bid];
                if (!cached) { return spec.fallback; }
                var parts = path.split('.');
                var cur = cached;
                for (var i = 0; i < parts.length; i++) {
                    if (cur == null) { return spec.fallback; }
                    cur = cur[parts[i]];
                }
                return cur;
            }
        }

        if (spec.fallback !== undefined) { return spec.fallback; }
        return null;
    }

    function sporeDbGet(path, cb) {
        if (typeof socket === 'undefined' || !socket || !socket.emit) {
            if (cb) { cb(null); }
            return;
        }
        var done = false;
        var requestId = 'spore_get_' + Date.now() + '_' + Math.random().toString(36).slice(2, 10);
        function finish(data) {
            if (done) { return; }
            done = true;
            try { socket.off('get_data', onResp); } catch (e) {}
            if (cb) { cb(data); }
        }
        function onResp(res) {
            if (!res) { return; }
            if (res.request_id !== requestId) { return; }
            if (res.error) { finish(null); return; }
            finish(res.data != null ? res.data : res);
        }
        socket.on('get_data', onResp);
        socket.emit('get_data', { path: path, request_id: requestId });
        setTimeout(function () { finish(null); }, 8000);
    }

    function sporeDbSet(path, data, cb) {
        if (typeof socket === 'undefined' || !socket || !socket.emit) {
            if (cb) { cb(false); }
            return;
        }
        socket.emit('set_data', { path: path, data: data });
        if (cb) { cb(true); }
    }

    var SPORE_VALUE_ANIM_CLASSES = [
        'spore-value-fade-in', 'spore-value-fade-out', 'spore-value-slide-in',
        'spore-value-bounce', 'spore-value-pulse'
    ];

    function sporeClearValueAnimationClasses(el) {
        if (!el || !el.classList) { return; }
        SPORE_VALUE_ANIM_CLASSES.forEach(function (c) { el.classList.remove(c); });
    }

    function sporeEaseProgress(t, easingName) {
        if (easingName === 'linear') { return t; }
        if (easingName === 'ease-in') { return t * t * t; }
        if (easingName === 'ease-in-out') {
            return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
        }
        return 1 - Math.pow(1 - t, 3);
    }

    function sporeAnimateValueTick(elementId, startVal, endVal, format, spec, done, bounds) {
        var el = document.getElementById(elementId);
        if (!el) { if (done) { done(); } return; }
        var durMs = Math.max(0, spec.duration_ms != null ? spec.duration_ms : 500);
        var easing = spec.easing || 'ease-out';
        var startN = sporeCoerceNumber(startVal, 0);
        var endN = sporeCoerceNumber(endVal, startN);
        var fmt = format || '{value}';
        var minB = bounds && bounds.min;
        var maxB = bounds && bounds.max;
        function fmtN(n) {
            return sporeFormatCounterDisplay(n, fmt, minB, maxB);
        }
        if (durMs === 0 || startN === endN) {
            if (typeof sporeSetText === 'function') {
                sporeSetText(elementId, fmtN(endN));
            } else {
                el.textContent = fmtN(endN);
            }
            if (done) { done(); }
            return;
        }
        if (spec.pulse) {
            el.classList.add('spore-value-pulse');
            el.style.setProperty('--spore-va-dur', (durMs / 1000) + 's');
            el.style.setProperty('--spore-va-ease', easing);
        }
        var t0 = null;
        function frame(ts) {
            if (t0 == null) { t0 = ts; }
            var progress = Math.min((ts - t0) / durMs, 1);
            var current = Math.round(startN + (endN - startN) * sporeEaseProgress(progress, easing));
            el.textContent = fmtN(current);
            if (progress < 1) {
                requestAnimationFrame(frame);
            } else {
                el.textContent = fmtN(endN);
                if (spec.pulse) {
                    el.classList.remove('spore-value-pulse');
                }
                if (done) { done(); }
            }
        }
        requestAnimationFrame(frame);
    }

    function sporePlayValueAnimation(elementId, spec) {
        if (!spec || !spec.enabled) { return; }
        var el = document.getElementById(elementId);
        if (!el) { return; }
        var type = spec.type || 'fade-in';
        if (type === 'tick_up') { return; }
        var durMs = spec.duration_ms != null ? spec.duration_ms : 500;
        var easing = spec.easing || 'ease-out';
        el.style.setProperty('--spore-va-dur', (durMs / 1000) + 's');
        el.style.setProperty('--spore-va-ease', easing);
        sporeClearValueAnimationClasses(el);
        if (spec.pulse) {
            el.classList.add('spore-value-pulse');
            return;
        }
        if (type === 'fade-in') {
            el.classList.add('spore-value-fade-out');
            setTimeout(function () {
                sporeClearValueAnimationClasses(el);
                el.classList.add('spore-value-fade-in');
                setTimeout(function () {
                    el.classList.remove('spore-value-fade-in');
                }, durMs);
            }, Math.round(durMs / 2));
            return;
        }
        var cls = type === 'slide-in' ? 'spore-value-slide-in' :
            (type === 'bounce' ? 'spore-value-bounce' : 'spore-value-fade-in');
        el.classList.add(cls);
        setTimeout(function () {
            el.classList.remove(cls);
        }, durMs);
    }

    function sporeResolveCounterImageSrc(value, spec) {
        spec = spec || {};
        var n = sporeCoerceNumber(value, 0);
        var ranges = spec.ranges || [];
        for (var ri = 0; ri < ranges.length; ri++) {
            var row = ranges[ri];
            if (!row) { continue; }
            var lo = sporeCoerceNumber(row.min, 0);
            var hi = sporeCoerceNumber(row.max, lo);
            if (n >= lo && n <= hi && row.src) {
                return String(row.src);
            }
        }
        return spec.default_src != null ? String(spec.default_src) : '';
    }

    function sporeCounterImageNode(spec) {
        if (!spec || !spec.elementId) { return null; }
        var node = document.getElementById(spec.elementId);
        if (!node) { return null; }
        if (node.tagName === 'IMG') { return node; }
        return node.querySelector('img');
    }

    function sporeCounterImageTransitionActive(tr) {
        return tr && tr.enabled && tr.type && tr.type !== 'none';
    }

    function sporeEnsureCounterImageHost(img) {
        if (!img || !img.parentNode) { return img; }
        var parent = img.parentNode;
        if (parent.classList && parent.classList.contains('spore-counter-image-host')) {
            return parent;
        }
        var host = document.createElement('div');
        host.className = 'spore-counter-image-host spore-element';
        if (img.classList) {
            img.classList.forEach(function (c) {
                if (c && c !== 'spore-element') { host.classList.add(c); }
            });
            img.classList.remove('spore-element');
        }
        ['left', 'top', 'width', 'height', 'zIndex'].forEach(function (prop) {
            if (img.style[prop]) {
                host.style[prop] = img.style[prop];
                img.style[prop] = (prop === 'width' || prop === 'height') ? '100%' : '0';
            }
        });
        if (img.getAttribute('data-spore-hidden') === 'true') {
            host.setAttribute('data-spore-hidden', 'true');
            img.removeAttribute('data-spore-hidden');
        }
        img.parentNode.insertBefore(host, img);
        host.appendChild(img);
        img.style.position = 'absolute';
        img.style.objectFit = img.style.objectFit || 'contain';
        return host;
    }

    function sporeCounterImageAltLayer(host, primary) {
        if (!host) { return null; }
        if (host.__sporeCiAlt && host.__sporeCiAlt.parentNode === host) {
            return host.__sporeCiAlt;
        }
        var alt = document.createElement('img');
        alt.className = 'spore-counter-image-alt';
        alt.alt = primary.getAttribute('alt') || '';
        alt.style.opacity = '0';
        host.appendChild(alt);
        host.__sporeCiAlt = alt;
        return alt;
    }

    function sporeCiSetTiming(el, halfMs, ease) {
        el.style.setProperty('--spore-ci-dur', (halfMs / 1000) + 's');
        el.style.setProperty('--spore-ci-ease', ease || 'ease-out');
    }

    function sporeCiClearTiming(el) {
        el.style.removeProperty('--spore-ci-dur');
        el.style.removeProperty('--spore-ci-ease');
    }

    function sporeCiWaitAnimation(el, ms, done) {
        var finished = false;
        function finish() {
            if (finished) { return; }
            finished = true;
            if (done) { done(); }
        }
        function onEnd(ev) {
            if (ev && ev.target !== el) { return; }
            el.removeEventListener('animationend', onEnd);
            finish();
        }
        el.addEventListener('animationend', onEnd);
        setTimeout(finish, Math.max(16, ms) + 40);
    }

    function sporeCiClassPair(type) {
        var t = (type || 'fade').toLowerCase();
        if (t === 'slide') {
            return { out: 'spore-ci-slide-out', inn: 'spore-ci-slide-in' };
        }
        if (t === 'bounce') {
            return { out: 'spore-ci-bounce-out', inn: 'spore-ci-bounce-in' };
        }
        if (t === 'roll') {
            return { out: 'spore-ci-roll-out', inn: 'spore-ci-roll-in' };
        }
        return { out: 'spore-ci-fade-out', inn: 'spore-ci-fade-in' };
    }

    function sporeApplyCounterImageSrc(img, url, spec) {
        var tr = spec.range_transition || spec.transition;
        var key = spec.elementId;
        var last = window.__sporeCounterImageLastSrc[key];
        if (!img) { return; }
        if (!url) {
            img.src = '';
            window.__sporeCounterImageLastSrc[key] = '';
            return;
        }
        if (!sporeCounterImageTransitionActive(tr) || last === url) {
            img.src = url;
            window.__sporeCounterImageLastSrc[key] = url;
            return;
        }
        if (last == null || last === '') {
            img.src = url;
            window.__sporeCounterImageLastSrc[key] = url;
            return;
        }

        var dur = Math.max(0, tr.duration_ms || 400);
        var ease = tr.easing || 'ease-out';
        var half = Math.max(16, Math.round(dur / 2));
        var type = (tr.type || 'fade').toLowerCase();

        if (type === 'crossfade') {
            var host = sporeEnsureCounterImageHost(img);
            var alt = sporeCounterImageAltLayer(host, img);
            var preload = new Image();
            preload.onload = function () {
                alt.src = url;
                alt.style.transition = 'opacity ' + dur + 'ms ' + ease;
                img.style.transition = 'opacity ' + dur + 'ms ' + ease;
                void alt.offsetWidth;
                alt.style.opacity = '1';
                img.style.opacity = '0';
                setTimeout(function () {
                    img.src = url;
                    img.style.opacity = '1';
                    img.style.transition = '';
                    alt.style.opacity = '0';
                    alt.style.transition = '';
                    window.__sporeCounterImageLastSrc[key] = url;
                }, dur);
            };
            preload.onerror = function () {
                img.src = url;
                window.__sporeCounterImageLastSrc[key] = url;
            };
            preload.src = url;
            return;
        }

        var classes = sporeCiClassPair(type);
        sporeCiSetTiming(img, half, ease);
        img.classList.add(classes.out);
        sporeCiWaitAnimation(img, half, function () {
            img.classList.remove(classes.out);
            img.src = url;
            img.classList.add(classes.inn);
            sporeCiWaitAnimation(img, half, function () {
                img.classList.remove(classes.inn);
                sporeCiClearTiming(img);
                window.__sporeCounterImageLastSrc[key] = url;
            });
        });
    }

    function sporeApplyCounterImages(counterId) {
        var val = window.__sporeCounters[counterId];
        if (val == null) {
            var meta0 = window.__sporeCounterMeta[counterId];
            val = meta0 ? (meta0.initial_value || 0) : 0;
        }
        (window.__sporeCounterImages || []).forEach(function (spec) {
            if (!spec || spec.counter_id !== counterId) { return; }
            var url = sporeResolveCounterImageSrc(val, spec);
            var img = sporeCounterImageNode(spec);
            if (!img) { return; }
            sporeApplyCounterImageSrc(img, url, spec);
        });
    }

    function sporeCounterRender(counterId, opts) {
        var meta = window.__sporeCounterMeta[counterId];
        if (!meta) { return; }
        var val = window.__sporeCounters[counterId];
        if (val == null) { val = meta.initial_value || 0; }
        var displayMax = meta.max;
        if (sporeCounterUsesProgressive(meta)) {
            if (meta.min != null && val < meta.min) { val = meta.min; }
            var shown = sporeProgressiveBounds(val, meta.goals, meta.min);
            if (shown && val > shown.ceiling) {
                val = shown.ceiling;
                shown = sporeProgressiveBounds(val, meta.goals, meta.min);
            }
            displayMax = shown ? shown.activeGoal : meta.max;
        } else {
            if (meta.min != null && val < meta.min) { val = meta.min; }
            if (meta.max != null && val > meta.max) { val = meta.max; }
        }
        window.__sporeCounters[counterId] = val;
        var fmt = meta.format || '{value}';
        var skipAnim = opts && opts.skipAnimation;
        var anim = meta.value_animation;
        var prev = window.__sporeCounterLastRendered[counterId];
        if (prev == null) { prev = meta.initial_value || 0; }
        var afterText = function () {
            if (typeof sporeApplyCounterImages === 'function') {
                sporeApplyCounterImages(counterId);
            }
            if (typeof sporeUpdateProgressBars === 'function') {
                sporeUpdateProgressBars(counterId);
            }
        };
        if (!skipAnim && anim && anim.enabled && anim.type === 'tick_up' && meta.elementId) {
            sporeAnimateValueTick(
                meta.elementId, prev, val, fmt, anim, afterText,
                { min: meta.min, max: displayMax }
            );
        } else {
            var text = sporeFormatCounterDisplay(val, fmt, meta.min, displayMax);
            if (meta.elementId && typeof sporeSetText === 'function') {
                sporeSetText(meta.elementId, text);
            }
            if (!skipAnim && anim && anim.enabled && meta.elementId) {
                sporePlayValueAnimation(meta.elementId, anim);
            }
            afterText();
        }
        window.__sporeCounterLastRendered[counterId] = val;
        if (typeof sporeApplyTransforms === 'function') { sporeApplyTransforms(); }
    }

    function sporeCounterPersist(counterId) {
        var meta = window.__sporeCounterMeta[counterId];
        if (!meta || !meta.persist) { return; }
        var path = meta.database_path;
        var key = meta.database_key || counterId;
        if (!path) { return; }
        sporeDbGet(path, function (doc) {
            doc = doc && typeof doc === 'object' ? doc : {};
            doc[key] = window.__sporeCounters[counterId];
            sporeDbSet(path, doc);
        });
    }

    function sporeCounterHydrate(counterId, cb) {
        var meta = window.__sporeCounterMeta[counterId];
        if (!meta) { if (cb) { cb(); } return; }
        if (!meta.persist) {
            window.__sporeCounters[counterId] = meta.initial_value || 0;
            sporeCounterRender(counterId, { skipAnimation: true });
            if (cb) { cb(); }
            return;
        }
        var path = meta.database_path;
        var key = meta.database_key || counterId;
        sporeDbGet(path, function (doc) {
            var val = meta.initial_value || 0;
            if (doc && typeof doc === 'object' && doc[key] != null) {
                val = sporeCoerceNumber(doc[key], val);
            }
            window.__sporeCounters[counterId] = val;
            sporeCounterRender(counterId, { skipAnimation: true });
            if (cb) { cb(); }
        });
    }

    function sporeNormalizeGoals(goals) {
        var out = [];
        (goals || []).forEach(function (raw) {
            var n = Number(raw);
            if (typeof n !== 'number' || isNaN(n)) { return; }
            if (out.length && n < out[out.length - 1]) { n = out[out.length - 1]; }
            if (out.length && n === out[out.length - 1]) { return; }
            out.push(n);
        });
        return out;
    }

    function sporeCounterUsesProgressive(meta) {
        return !!(meta && meta.progressive && sporeNormalizeGoals(meta.goals).length);
    }

    /* Active goal is the first goal above value, or the last goal once that
     * value has been reached. Floor is the previous goal (or min before the
     * first). Ceiling is always the last goal. */
    function sporeProgressiveBounds(value, goals, minBound) {
        goals = sporeNormalizeGoals(goals);
        if (!goals.length) { return null; }
        var val = sporeCoerceNumber(value, 0);
        var activeIndex = goals.length - 1;
        var i;
        for (i = 0; i < goals.length; i++) {
            if (goals[i] > val) {
                activeIndex = i;
                break;
            }
        }
        var floor = (minBound != null && minBound !== '') ? sporeCoerceNumber(minBound, 0) : null;
        if (activeIndex > 0) { floor = goals[activeIndex - 1]; }
        return {
            goals: goals,
            floor: floor,
            ceiling: goals[goals.length - 1],
            activeGoal: goals[activeIndex],
            activeIndex: activeIndex
        };
    }

    function sporeEmitProgressiveGoals(meta, counterId, before, val) {
        if (!sporeCounterUsesProgressive(meta) || !(val > before)) { return; }
        var goals = sporeNormalizeGoals(meta.goals);
        goals.forEach(function (goal, index) {
            if (!(goal > before && goal <= val)) { return; }
            var next = index + 1 < goals.length ? goals[index + 1] : null;
            sporeEmitOutput('goal_reached', meta.elementId, {
                counterId: counterId,
                value: val,
                goal: goal,
                max: goal,
                nextGoal: next,
                index: index
            });
        });
    }

    /* Replayed alerts play again but never count (counters, goals, lists, feeds). */
    function sporeIsReplay(payload) {
        return !!(payload && typeof payload === 'object' && payload.is_replay);
    }

    function sporeCounterAdjust(counterId, operation, deltaSpec, payload) {
        var meta = window.__sporeCounterMeta[counterId];
        if (!meta) { return; }
        if (sporeIsReplay(payload)) { return; }
        window.__sporeLastPayload.event = payload || {};
        var val = sporeCoerceNumber(window.__sporeCounters[counterId], meta.initial_value || 0);
        var before = val;
        var op = operation || 'increment';
        if (op === 'reset') {
            val = meta.initial_value || 0;
        } else if (op === 'set') {
            val = sporeCoerceNumber(sporeRandomDelta(deltaSpec), val);
        } else {
            var delta = sporeRandomDelta(deltaSpec);
            if (op === 'decrement') { delta = -delta; }
            val = val + delta;
        }
        if (op === 'reset') {
            if (meta.min != null) { val = Math.max(meta.min, val); }
            if (sporeCounterUsesProgressive(meta)) {
                var resetBounds = sporeProgressiveBounds(val, meta.goals, meta.min);
                if (resetBounds && val > resetBounds.ceiling) { val = resetBounds.ceiling; }
            } else if (meta.max != null) {
                val = Math.min(meta.max, val);
            }
        } else if (sporeCounterUsesProgressive(meta)) {
            var live = sporeProgressiveBounds(before, meta.goals, meta.min);
            if (meta.min != null) { val = Math.max(meta.min, val); }
            if (live) {
                if (live.floor != null) { val = Math.max(live.floor, val); }
                val = Math.min(live.ceiling, val);
            }
        } else {
            if (meta.min != null) { val = Math.max(meta.min, val); }
            if (meta.max != null) { val = Math.min(meta.max, val); }
        }
        window.__sporeCounters[counterId] = val;
        if (op !== 'reset') { sporeEmitProgressiveGoals(meta, counterId, before, val); }
        sporeCounterRender(counterId);
        sporeCounterPersist(counterId);
        if (val !== before) {
            sporeEmitOutput('counter_changed', meta.elementId, {
                counterId: counterId, value: val, delta: val - before
            });
        }
    }

    function sporeResolveProgressMax(spec) {
        if (!spec) { return 100; }
        if (spec.max_kind === 'counter' && spec.max_counter_id) {
            var mv = window.__sporeCounters[spec.max_counter_id];
            if (mv == null) {
                var mmeta = window.__sporeCounterMeta[spec.max_counter_id];
                mv = mmeta ? (mmeta.initial_value || 0) : 0;
            }
            return sporeCoerceNumber(mv, 0);
        }
        if (spec.follow_counter_goals) {
            var goalMeta = window.__sporeCounterMeta[spec.counter_id];
            if (sporeCounterUsesProgressive(goalMeta)) {
                var goalCurrent = window.__sporeCounters[spec.counter_id];
                if (goalCurrent == null) { goalCurrent = goalMeta.initial_value || 0; }
                var goalBounds = sporeProgressiveBounds(goalCurrent, goalMeta.goals, goalMeta.min);
                if (goalBounds) { return goalBounds.activeGoal; }
            }
        }
        return sporeCoerceNumber(spec.max, 100);
    }

    function sporeResolveNearGoalEffect(spec) {
        if (!spec) { return 'none'; }
        var effect = spec.near_goal_effect;
        if (effect == null || effect === '') {
            effect = spec.near_goal_pulse ? 'pulse' : 'none';
        }
        effect = String(effect).toLowerCase();
        if (effect === 'pulse' || effect === 'shimmer' || effect === 'scroll') {
            return effect;
        }
        return 'none';
    }

    function sporeApplyNearGoalEffect(fill, effect) {
        if (!fill || !fill.classList) { return; }
        fill.classList.remove(
            'spore-progress-near-goal',
            'spore-progress-near-pulse',
            'spore-progress-near-shimmer',
            'spore-progress-near-scroll'
        );
        if (effect === 'pulse') {
            fill.classList.add('spore-progress-near-pulse');
        } else if (effect === 'shimmer') {
            fill.classList.add('spore-progress-near-shimmer');
        } else if (effect === 'scroll') {
            fill.classList.add('spore-progress-near-scroll');
        }
    }

    function sporeUpdateProgressBars(counterId) {
        var bars = window.__sporeProgressBars || [];
        if (!bars.length) { return; }
        bars.forEach(function (spec) {
            if (counterId && spec.counter_id !== counterId &&
                spec.max_counter_id !== counterId) {
                return;
            }
            var root = document.getElementById(spec.elementId);
            if (!root) { return; }
            var current = window.__sporeCounters[spec.counter_id];
            if (current == null) {
                var cmeta = window.__sporeCounterMeta[spec.counter_id];
                current = cmeta ? (cmeta.initial_value || 0) : 0;
            }
            current = sporeCoerceNumber(current, 0);
            if (spec.override_value != null) { current = sporeCoerceNumber(spec.override_value, 0); }
            var max = spec.override_max != null
                ? sporeCoerceNumber(spec.override_max, 100)
                : sporeResolveProgressMax(spec);
            var reached = max > 0 && current >= max;
            if (spec._goalReached === undefined) {
                spec._goalReached = reached;
            } else if (reached && !spec._goalReached) {
                spec._goalReached = true;
                sporeEmitOutput('goal_reached', spec.elementId, {
                    counterId: spec.counter_id, value: current, max: max
                });
            } else if (!reached) {
                spec._goalReached = false;
            }
            var pct = max > 0 ? Math.min(100, (current / max) * 100) : 0;
            var fill = root.querySelector('.spore-progress-fill');
            if (fill) {
                fill.style.width = pct + '%';
                var effect = sporeResolveNearGoalEffect(spec);
                if (effect !== 'none' && spec.near_goal_threshold != null &&
                    pct >= spec.near_goal_threshold) {
                    sporeApplyNearGoalEffect(fill, effect);
                } else {
                    sporeApplyNearGoalEffect(fill, 'none');
                }
            }
        });
    }

    function sporeUpdateAllProgressBars() {
        sporeUpdateProgressBars(null);
    }

    function sporeRefreshDataDisplay(idx) {
        var spec = window.__sporeDataDisplays[idx];
        if (!spec) { return; }
        if (spec.feed) { return; }  /* a Data feed drives this display instead */
        var payload = window.__sporeLastPayload.event || {};
        var ctx = window.__sporeLastPayload.ctx || {};
        sporeApplyDataDisplay(idx, sporeResolveSource(spec.source, payload, ctx));
    }

    /* Paint a data display from an already-resolved value (shared with Data feeds). */
    function sporeApplyDataDisplay(idx, raw, textOverride) {
        var spec = window.__sporeDataDisplays[idx];
        if (!spec) { return; }
        var text;
        if (textOverride != null && textOverride !== '') {
            text = String(textOverride);
        } else if (raw == null || raw === '') {
            text = spec.default_text != null ? String(spec.default_text) : '';
        } else {
            text = sporeFormatDisplay(raw, spec.format || '{value}');
        }
        var anim = spec.value_animation;
        var elId = spec.elementId;
        if (!elId) { return; }
        var skipTick = false;
        if (anim && anim.enabled && anim.type === 'tick_up') {
            var prevD = window.__sporeDisplayLastRendered[elId];
            var endN = sporeCoerceNumber(raw, NaN);
            if (!isNaN(endN) && prevD != null && !isNaN(sporeCoerceNumber(prevD, NaN))) {
                sporeAnimateValueTick(elId, prevD, endN, spec.format || '{value}', anim);
                window.__sporeDisplayLastRendered[elId] = endN;
                skipTick = true;
            } else if (!isNaN(endN)) {
                window.__sporeDisplayLastRendered[elId] = endN;
            }
        }
        if (!skipTick) {
            if (typeof sporeSetText === 'function') {
                sporeSetText(elId, text);
            }
            if (anim && anim.enabled) {
                sporePlayValueAnimation(elId, anim);
            }
            if (raw != null && raw !== '' && !isNaN(sporeCoerceNumber(raw, NaN))) {
                window.__sporeDisplayLastRendered[elId] = sporeCoerceNumber(raw, 0);
            }
        }
    }

    function sporeRefreshAllDataDisplays() {
        for (var i = 0; i < window.__sporeDataDisplays.length; i++) {
            sporeRefreshDataDisplay(i);
        }
        if (typeof sporeRefreshStatCards === 'function') { sporeRefreshStatCards(); }
    }

    function sporeLoadStatsCache() {
        sporeDbGet('statistics/session', function (doc) {
            if (doc && typeof doc === 'object') {
                window.__sporeStatsCache = doc;
                sporeRefreshAllDataDisplays();
                if (typeof sporeFeedRefreshAllValues === 'function') { sporeFeedRefreshAllValues(); }
                if (typeof sporeApplyTransforms === 'function') { sporeApplyTransforms(); }
            }
        });
    }

    function sporeDispatchControlAction(action, data, templateName) {
        data = data || {};
        var a = action || '';
        if (a === 'pause_alerts') {
            if (socket) { socket.emit('pause_alerts', {}); }
            return;
        }
        if (a === 'resume_alerts') {
            if (socket) { socket.emit('resume_alerts', {}); }
            return;
        }
        if (a === 'toggle_alerts') {
            if (socket) { socket.emit('toggle_alerts', {}); }
            return;
        }
        if (a === 'skip_alert') {
            if (socket) { socket.emit('skip_alert', {}); }
            return;
        }
        if (a === 'clear_alert_queue') {
            if (socket) { socket.emit('clear_alert_queue', {}); }
            return;
        }
        if (a === 'refresh_alerts') {
            if (socket) { socket.emit('refresh-alerts', {}); }
            return;
        }
        if (a === 'counter_adjust') {
            var cid = data.target_counter_id || data.counter_id;
            if (cid) {
                sporeCounterAdjust(
                    cid,
                    data.operation || 'increment',
                    data.delta || { kind: 'fixed', value: 1 },
                    data
                );
            }
            return;
        }
        if (a === 'timer_start' || a === 'timer_pause' || a === 'timer_reset') {
            var tid = data.element_id || data.target_timer_id;
            if (tid && a === 'timer_start' && typeof sporeTimerStart === 'function') {
                sporeTimerStart(tid);
            } else if (tid && a === 'timer_pause' && typeof sporeTimerPause === 'function') {
                sporeTimerPause(tid);
            } else if (tid && a === 'timer_reset' && typeof sporeTimerReset === 'function') {
                sporeTimerReset(tid);
            }
            return;
        }
        if (a === 'element_show' && data.element_id) {
            if (typeof sporeShow === 'function') { sporeShow(data.element_id, null); }
            return;
        }
        if (a === 'element_hide' && data.element_id) {
            if (typeof sporeHide === 'function') { sporeHide(data.element_id, null); }
            return;
        }
        if (a === 'element_toggle' && data.element_id) {
            if (typeof sporeToggle === 'function') { sporeToggle(data.element_id); }
            return;
        }
        if (a === 'twitch_api_request' && data.endpoint) {
            if (socket) {
                socket.emit('twitch-api-request', {
                    endpoint: data.endpoint,
                    method: data.method || 'GET',
                    requestId: data.requestId || ('spore_ctrl_' + Date.now())
                });
            }
            return;
        }
        if (a === 'websocket_emit' && data.event_name) {
            var pl = data.payload || data.data || {};
            if (socket) { socket.emit(data.event_name, pl); }
            return;
        }
        if (a === 'streamdeck_forward') {
            if (socket) {
                socket.emit('streamdeck_template_action', {
                    templateName: templateName,
                    actionName: data.action_name || data.actionName || '',
                    data: data.data || {}
                });
            }
            return;
        }
        if (templateName && a) {
            if (socket) { socket.emit(templateName + '_' + a, data); }
        }
    }

    window.sporeFormatDisplay = sporeFormatDisplay;
    window.sporeFormatCounterDisplay = sporeFormatCounterDisplay;
    window.sporeResolveSource = sporeResolveSource;
    window.sporeRandomDelta = sporeRandomDelta;
    window.sporeCounterAdjust = sporeCounterAdjust;
    window.sporeCounterHydrate = sporeCounterHydrate;
    window.sporeCounterRender = sporeCounterRender;
    window.sporeUpdateProgressBars = sporeUpdateProgressBars;
    window.sporeUpdateAllProgressBars = sporeUpdateAllProgressBars;
    window.sporePlayValueAnimation = sporePlayValueAnimation;
    window.sporeApplyCounterImages = sporeApplyCounterImages;
    window.sporeResolveCounterImageSrc = sporeResolveCounterImageSrc;
    window.sporeRefreshDataDisplay = sporeRefreshDataDisplay;
    window.sporeRefreshAllDataDisplays = sporeRefreshAllDataDisplays;
    window.sporeLoadStatsCache = sporeLoadStatsCache;
    window.sporeDispatchControlAction = sporeDispatchControlAction;
    window.sporeDbGet = sporeDbGet;
    window.sporeDbSet = sporeDbSet;

    window.__sporeClocks = window.__sporeClocks || [];
    window.__sporeTimers = window.__sporeTimers || {};
    window.__sporeClockIntervals = window.__sporeClockIntervals || [];
    window.__sporeTimerIntervals = window.__sporeTimerIntervals || {};

    function sporePad2(n) {
        n = Math.floor(n);
        return (n < 10 ? '0' : '') + n;
    }

    /* [[word]] in text shows that part highlighted (the block's highlight colours). */
    function sporeAccentHtml(text) {
        var safe = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        /* One wrapper keeps the line together in centred (flex) text blocks. */
        return '<span class="spore-accent-line">' +
            safe.replace(/\[\[([\s\S]*?)\]\]/g, '<span class="spore-accent">$1</span>') + '</span>';
    }

    function sporeSetTextContent(el, value) {
        if (!el) { return; }
        var t = (value == null ? '' : String(value));
        var accent = t.indexOf('[[') >= 0 && t.indexOf(']]') > t.indexOf('[[');
        if (el.classList && el.classList.contains('spore-marquee')) {
            var nodes = el.querySelectorAll('.spore-marquee-text');
            for (var i = 0; i < nodes.length; i++) {
                if (accent) { nodes[i].innerHTML = sporeAccentHtml(t); } else { nodes[i].textContent = t; }
            }
            return;
        }
        if (accent) { el.innerHTML = sporeAccentHtml(t); } else { el.textContent = t; }
    }

    /* Static text is rendered by the server; turn its [[...]] into highlights once. */
    function sporeAccentStaticText() {
        var nodes = document.querySelectorAll('.spore-element[data-spore-type="text"], .spore-marquee-text');
        for (var i = 0; i < nodes.length; i++) {
            var html = nodes[i].innerHTML;
            if (html.indexOf('[[') < 0) { continue; }
            nodes[i].innerHTML = '<span class="spore-accent-line">' +
                html.replace(/\[\[([\s\S]*?)\]\]/g, '<span class="spore-accent">$1</span>') + '</span>';
        }
    }

    function sporePatchSetText() {
        window.sporeSetText = function (id, value) {
            var el = document.getElementById(id);
            sporeSetTextContent(el, value);
        };
    }

    function sporeClockDate(spec) {
        var now = new Date();
        if (!spec) { return now; }
        var tz = spec.timezone || 'local';
        if (tz === 'utc') {
            return new Date(now.getTime() + now.getTimezoneOffset() * 60000);
        }
        if (tz === 'offset') {
            var off = parseInt(spec.timezone_offset_minutes, 10) || 0;
            return new Date(now.getTime() + now.getTimezoneOffset() * 60000 + off * 60000);
        }
        return now;
    }

    function sporeFormatClockDate(date, format) {
        var fmt = format || 'HH:mm:ss';
        var hh = sporePad2(date.getHours());
        var mm = sporePad2(date.getMinutes());
        var ss = sporePad2(date.getSeconds());
        return fmt.split('HH').join(hh).split('mm').join(mm).split('ss').join(ss);
    }

    function sporeInitClocks() {
        if (window.__sporeClockIntervals && window.__sporeClockIntervals.length) {
            for (var ci = 0; ci < window.__sporeClockIntervals.length; ci++) {
                clearInterval(window.__sporeClockIntervals[ci]);
            }
        }
        window.__sporeClockIntervals = [];
        var list = window.__sporeClocks || [];
        for (var i = 0; i < list.length; i++) {
            (function (spec) {
                var tick = function () {
                    var el = document.getElementById(spec.elementId);
                    if (!el) { return; }
                    sporeSetTextContent(el, sporeFormatClockDate(sporeClockDate(spec), spec.format));
                };
                tick();
                window.__sporeClockIntervals.push(setInterval(tick, 1000));
            })(list[i]);
        }
    }

    function sporeTimerStartBound(spec) {
        if (spec && spec.start_seconds != null && spec.start_seconds !== '') {
            return sporeCoerceNumber(spec.start_seconds, 0);
        }
        return sporeCoerceNumber(spec && spec.duration_seconds, 0);
    }

    function sporeTimerShownSeconds(spec, elapsed) {
        var mode = (spec && spec.mode) || 'count_down';
        var total = Math.max(0, Math.floor(sporeCoerceNumber(elapsed, 0)));
        if (mode === 'count_up') { return total; }
        return Math.max(0, sporeTimerStartBound(spec) - total);
    }

    function sporeFormatTimerElapsed(spec, elapsed) {
        var fmt = (spec && spec.format) || '{mm}:{ss}';
        var shown = sporeTimerShownSeconds(spec, elapsed);
        var hh = Math.floor(shown / 3600);
        var mm = Math.floor((shown % 3600) / 60);
        var ss = shown % 60;
        var tokens = [
            ['{time}', sporePad2(hh) + ':' + sporePad2(mm) + ':' + sporePad2(ss)],
            ['{hh}', sporePad2(hh)],
            ['{h}', String(hh)],
            ['{mm}', sporePad2(mm)],
            ['{m}', String(mm)],
            ['{total}', String(shown)],
            ['{ss}', sporePad2(ss)],
            ['{s}', String(ss)]
        ];
        var out = fmt;
        for (var ti = 0; ti < tokens.length; ti++) {
            out = out.split(tokens[ti][0]).join(tokens[ti][1]);
        }
        return out;
    }

    function sporeTimerClearInterval(id) {
        if (window.__sporeTimerIntervals[id]) {
            clearInterval(window.__sporeTimerIntervals[id]);
            delete window.__sporeTimerIntervals[id];
        }
    }

    function sporeTimerStopAt(id, spec, elapsed) {
        spec.elapsed = elapsed;
        spec.elapsed_base = elapsed;
        spec.running = false;
        spec.started_at = null;
        sporeTimerClearInterval(id);
        sporeTimerRender(id);
        sporeEmitOutput('timer_finished', spec.elementId || id, {
            mode: spec.mode, elapsed: elapsed
        });
    }

    function sporeTimerRender(id) {
        var spec = window.__sporeTimers[id];
        if (!spec) { return; }
        var el = document.getElementById(spec.elementId || id);
        if (!el) { return; }
        var elapsed = spec.elapsed || 0;
        sporeSetTextContent(el, sporeFormatTimerElapsed(spec, elapsed));
    }

    function sporeTimerTick(id) {
        var spec = window.__sporeTimers[id];
        if (!spec || !spec.running) { return; }
        var base = spec.started_at != null ? spec.started_at : Date.now();
        if (spec.started_at == null) { spec.started_at = base; }
        var elapsed = (Date.now() - base) / 1000 + (spec.elapsed_base || 0);
        spec.elapsed = elapsed;
        if (spec.mode === 'count_up') {
            var limit = sporeCoerceNumber(spec.limit_seconds, 0);
            if (limit > 0 && elapsed >= limit) {
                sporeTimerStopAt(id, spec, limit);
                return;
            }
        } else {
            var startBound = sporeTimerStartBound(spec);
            if (elapsed >= startBound) {
                sporeTimerStopAt(id, spec, startBound);
                return;
            }
        }
        sporeTimerRender(id);
    }

    function sporeTimerStart(id) {
        var spec = window.__sporeTimers[id];
        if (!spec) { return; }
        if (!spec.running) {
            spec.running = true;
            spec.started_at = Date.now();
            spec.elapsed_base = spec.elapsed || 0;
        }
        if (!window.__sporeTimerIntervals[id]) {
            window.__sporeTimerIntervals[id] = setInterval(function () {
                sporeTimerTick(id);
            }, 250);
        }
        sporeTimerTick(id);
    }

    function sporeTimerPause(id) {
        var spec = window.__sporeTimers[id];
        if (!spec) { return; }
        if (spec.running && spec.started_at != null) {
            spec.elapsed = (Date.now() - spec.started_at) / 1000 + (spec.elapsed_base || 0);
            spec.elapsed_base = spec.elapsed;
        }
        spec.running = false;
        spec.started_at = null;
        sporeTimerClearInterval(id);
    }

    function sporeTimerReset(id) {
        var spec = window.__sporeTimers[id];
        if (!spec) { return; }
        spec.elapsed = 0;
        spec.elapsed_base = 0;
        spec.started_at = null;
        spec.running = !!spec.auto_start;
        sporeTimerClearInterval(id);
        sporeTimerRender(id);
        if (spec.running) { sporeTimerStart(id); }
    }

    function sporeInitTimers() {
        var ids = Object.keys(window.__sporeTimers || {});
        for (var i = 0; i < ids.length; i++) {
            var id = ids[i];
            var spec = window.__sporeTimers[id];
            sporeTimerRender(id);
            if (spec && spec.auto_start) { sporeTimerStart(id); }
        }
    }

    function sporeAttr(el, name, fallback) {
        if (!el) { return fallback; }
        var v = el.getAttribute(name);
        return (v == null || v === '') ? fallback : v;
    }

    function sporeFormatTokens(template, item) {
        var out = String(template == null ? '' : template);
        var data = item || {};
        Object.keys(data).forEach(function (k) {
            var val = data[k] == null ? '' : String(data[k]);
            out = out.split('{' + k + '}').join(val);
        });
        return out;
    }

    function sporeListRows(el) {
        return el ? el.querySelector('.spore-list-rows') : null;
    }

    function sporeListPush(el, text, quiet) {
        var rows = sporeListRows(el);
        if (!rows) { return; }
        var max = parseInt(sporeAttr(el, 'data-spore-list-max', '8'), 10) || 8;
        var newest = sporeAttr(el, 'data-spore-list-newest', 'top');
        var gap = parseInt(sporeAttr(el, 'data-spore-list-gap', '4'), 10) || 0;
        var addAnim = quiet ? 'none' : sporeAttr(el, 'data-spore-list-add', 'fade');
        var row = document.createElement('div');
        row.className = 'spore-list-row';
        if (addAnim === 'slide') { row.classList.add('spore-list-in-slide'); }
        else if (addAnim === 'slide side') { row.classList.add('spore-list-in-side'); }
        else if (addAnim === 'bounce') { row.classList.add('spore-list-in-bounce'); }
        else if (addAnim !== 'none') { row.classList.add('spore-list-in-fade'); }
        row.style.marginBottom = gap + 'px';
        var rowBg = sporeAttr(el, 'data-spore-list-row-bg', '');
        var rowPad = parseFloat(sporeAttr(el, 'data-spore-list-row-pad', '0')) || 0;
        var rowRadius = parseFloat(sporeAttr(el, 'data-spore-list-row-radius', '0')) || 0;
        if (rowBg) { row.style.background = rowBg; }
        if (rowPad > 0) { row.style.padding = rowPad + 'px'; }
        if (rowRadius > 0) { row.style.borderRadius = rowRadius + 'px'; }
        row.textContent = text;
        sporeEmitOutput('list_row_added', el.id, { text: String(text) });
        if (newest === 'bottom') { rows.appendChild(row); }
        else { rows.insertBefore(row, rows.firstChild); }
        var live = Array.prototype.filter.call(rows.children, function (r) {
            return !r.classList.contains('spore-list-leaving');
        });
        while (live.length > max) {
            var victim = newest === 'bottom' ? live.shift() : live.pop();
            if (victim) { sporeListDrop(el, victim); }
        }
        var lifetime = parseFloat(sporeAttr(el, 'data-spore-list-lifetime', '0')) || 0;
        if (lifetime > 0) {
            setTimeout(function () {
                if (row.parentNode) { sporeListDrop(el, row); }
            }, lifetime * 1000);
        }
    }

    /* Replace every row from a list (first entry = newest), without add animations. */
    function sporeListReplace(el, entries) {
        var rows = sporeListRows(el);
        if (!rows) { return; }
        rows.textContent = '';
        var max = parseInt(sporeAttr(el, 'data-spore-list-max', '8'), 10) || 8;
        var list = (entries || []).slice(0, max);
        if (sporeAttr(el, 'data-spore-list-newest', 'top') !== 'bottom') { list.reverse(); }
        list.forEach(function (entry) {
            var payload = (entry && typeof entry === 'object') ? entry : { username: entry };
            sporeListPush(el, sporeListItem(el, payload, ''), true);
        });
    }

    function sporeListDrop(el, row) {
        if (!row || !row.parentNode) { return; }
        var anim = sporeAttr(el, 'data-spore-list-remove', 'fade');
        if (anim === 'none') {
            row.parentNode.removeChild(row);
            return;
        }
        row.classList.add('spore-list-leaving', 'spore-list-out-fade');
        setTimeout(function () {
            if (row.parentNode) { row.parentNode.removeChild(row); }
        }, 320);
    }

    function sporeListItem(el, payload, literal) {
        payload = payload || {};
        var textKey = sporeAttr(el, 'data-spore-list-text', 'username');
        var valueKey = sporeAttr(el, 'data-spore-list-value', 'value');
        var item = {
            username: payload[textKey] != null ? payload[textKey] : (payload.username || payload.message || literal || ''),
            value: payload[valueKey] != null ? payload[valueKey] : (
                payload.amount != null ? payload.amount :
                (payload.amt_cheered != null ? payload.amt_cheered :
                (payload.gift_qty != null ? payload.gift_qty : ''))
            )
        };
        Object.keys(payload).forEach(function (k) {
            if (item[k] == null) { item[k] = payload[k]; }
        });
        if (literal && !item.username) { item.username = literal; }
        var tmpl = sporeAttr(el, 'data-spore-list-template', '{username}');
        return sporeFormatTokens(tmpl, item);
    }

    function sporeListAdd(id, payload, literal) {
        var el = document.getElementById(id);
        if (!el) { return; }
        sporeListPush(el, sporeListItem(el, payload, literal));
    }

    function sporeListClear(id) {
        var el = document.getElementById(id);
        var rows = sporeListRows(el);
        if (rows) { rows.innerHTML = ''; }
        sporeEmitOutput('list_cleared', id, {});
    }

    function sporeWheelSegments(el) {
        try {
            var parsed = JSON.parse(el.getAttribute('data-spore-wheel-segments') || '[]');
            if (Array.isArray(parsed) && parsed.length) { return parsed; }
        } catch (e) {}
        return [
            { label: 'One', color: '#a855f7' },
            { label: 'Two', color: '#22c55e' }
        ];
    }

    function sporeWheelTextColor(color) {
        var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(color || '').trim());
        if (!m) { return '#ffffff'; }
        var h = m[1];
        if (h.length === 3) { h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]; }
        var lum = (0.299 * parseInt(h.substr(0, 2), 16) + 0.587 * parseInt(h.substr(2, 2), 16) +
            0.114 * parseInt(h.substr(4, 2), 16)) / 255;
        return lum > 0.6 ? '#111111' : '#ffffff';
    }

    function sporeWheelSvg(segments) {
        var n = segments.length;
        var esc = function (v) {
            return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        };
        var pt = function (deg, r) {
            var rad = deg * Math.PI / 180;
            return (r * Math.sin(rad)).toFixed(3) + ' ' + (-r * Math.cos(rad)).toFixed(3);
        };
        var fontSize = Math.max(6, Math.min(16, (2 * Math.PI * 55 / n) * 0.5));
        var out = '<svg viewBox="-100 -100 200 200" width="100%" height="100%" style="display:block">';
        for (var i = 0; i < n; i++) {
            var seg = segments[i] || {};
            var color = seg.color || '#a855f7';
            var a0 = (i / n) * 360;
            var a1 = ((i + 1) / n) * 360;
            if (n === 1) {
                out += '<circle r="100" fill="' + esc(color) + '"/>';
            } else {
                out += '<path d="M0 0 L' + pt(a0, 100) + ' A100 100 0 ' + ((a1 - a0) > 180 ? 1 : 0) +
                    ' 1 ' + pt(a1, 100) + ' Z" fill="' + esc(color) + '"/>';
            }
            var label = String(seg.label == null ? '' : seg.label);
            if (label.length > 16) { label = label.slice(0, 15) + '\u2026'; }
            if (label) {
                var mid = (a0 + a1) / 2;
                out += '<text transform="rotate(' + mid.toFixed(3) + ') translate(0,-88) rotate(-90)" ' +
                    'text-anchor="end" dominant-baseline="central" font-size="' + fontSize.toFixed(2) +
                    '" font-weight="600" fill="' + sporeWheelTextColor(color) + '">' + esc(label) + '</text>';
            }
        }
        return out + '</svg>';
    }

    var SPORE_CLICK_LOOKAHEAD_MS = 64;
    var SPORE_CLICK_LATENCY_SEC = 0.028;
    var SPORE_CLICK_LATENCY_MAX = 0.08;
    var SPORE_POINTER_SVG = '<svg viewBox="0 0 24 28" width="22" height="26" aria-hidden="true">' +
        '<path d="M12 26 L2 4 H22 Z" fill="#ffffff" stroke="#111111" stroke-width="2" ' +
        'stroke-linejoin="round"/></svg>';
    var sporeClickCtx = null;
    var sporeClickGain = null;
    var sporeClickBuffer = null;
    var sporeClickBufferUrl = '';
    var sporeClickBufferOffset = 0;
    var sporeClickAudio = null;
    var sporePendingClicks = [];
    var sporeWheelJobs = {};

    function sporeOn(el, attr, fallback) {
        var raw = String(sporeAttr(el, attr, fallback ? 'true' : 'false')).toLowerCase();
        return raw !== 'false' && raw !== '0' && raw !== 'no' && raw !== 'off';
    }

    function sporeMod(deg) {
        return ((deg % 360) + 360) % 360;
    }

    function sporeCubic(x1, y1, x2, y2, t) {
        function sample(u, a, b) {
            var c = 3 * a;
            var d = 3 * (b - a) - c;
            var e = 1 - c - d;
            return ((e * u + d) * u + c) * u;
        }
        function slope(u, a, b) {
            var c = 3 * a;
            var d = 3 * (b - a) - c;
            var e = 1 - c - d;
            return (3 * e * u + 2 * d) * u + c;
        }
        var u = t;
        var i;
        for (i = 0; i < 8; i++) {
            var x = sample(u, x1, x2) - t;
            var dx = slope(u, x1, x2);
            if (Math.abs(dx) < 1e-6) { break; }
            u -= x / dx;
        }
        if (u < 0) { u = 0; }
        if (u > 1) { u = 1; }
        return sample(u, y1, y2);
    }

    function sporeEaseUnit(name, t) {
        if (t <= 0) { return 0; }
        if (t >= 1) { return 1; }
        if (name === 'linear') { return t; }
        if (name === 'ease-in') { return sporeCubic(0.42, 0, 1, 1, t); }
        if (name === 'ease-in-out') { return sporeCubic(0.42, 0, 0.58, 1, t); }
        if (name === 'ease') { return sporeCubic(0.25, 0.1, 0.25, 1, t); }
        return sporeCubic(0, 0, 0.58, 1, t);
    }

    function sporePlacePointer(pointer, pos) {
        pointer.innerHTML = SPORE_POINTER_SVG;
        pointer.style.top = 'auto';
        pointer.style.bottom = 'auto';
        pointer.style.left = 'auto';
        pointer.style.right = 'auto';
        pointer.style.margin = '0';
        pointer.style.transform = '';
        if (pos === 'bottom') {
            pointer.style.left = '50%';
            pointer.style.bottom = '6px';
            pointer.style.transform = 'translateX(-50%) rotate(180deg)';
        } else if (pos === 'left') {
            pointer.style.top = '50%';
            pointer.style.left = '6px';
            pointer.style.transform = 'translateY(-50%) rotate(-90deg)';
        } else if (pos === 'right') {
            pointer.style.top = '50%';
            pointer.style.right = '6px';
            pointer.style.transform = 'translateY(-50%) rotate(90deg)';
        } else {
            pointer.style.left = '50%';
            pointer.style.top = '6px';
            pointer.style.transform = 'translateX(-50%)';
        }
    }

    function sporeStyleWheelLabel(el, text, visible) {
        var label = el.querySelector('.spore-wheel-label');
        if (!label) { return; }
        if (!sporeOn(el, 'data-spore-wheel-result', true)) {
            label.style.display = 'none';
            label.textContent = '';
            return;
        }
        var place = sporeAttr(el, 'data-spore-wheel-result-place', 'bottom');
        label.style.display = visible ? 'block' : 'none';
        if (text != null) { label.textContent = text; }
        label.style.top = 'auto';
        label.style.bottom = 'auto';
        label.style.left = '0';
        label.style.right = '0';
        label.style.margin = '0';
        label.style.transform = '';
        label.style.color = sporeAttr(el, 'data-spore-wheel-result-color', '#ffffff');
        label.style.fontSize = (parseFloat(sporeAttr(el, 'data-spore-wheel-result-size', '14')) || 14) + 'px';
        label.style.fontFamily = sporeAttr(el, 'data-spore-wheel-result-font', 'Arial');
        label.style.fontWeight = sporeOn(el, 'data-spore-wheel-result-bold', false) ? '700' : '400';
        if (place === 'center') {
            label.style.top = '50%';
            label.style.transform = 'translateY(-50%)';
        } else if (place === 'top') {
            label.style.top = '28%';
        } else if (place === 'above') {
            label.style.bottom = '100%';
            label.style.marginBottom = '6px';
        } else if (place === 'below') {
            label.style.top = '100%';
            label.style.marginTop = '6px';
        } else {
            label.style.bottom = '8px';
        }
    }

    function sporeClearResultTimer(el) {
        if (el && el._sporeResultTimer) {
            clearTimeout(el._sporeResultTimer);
            el._sporeResultTimer = null;
        }
    }

    function sporeClickUrl(el) {
        var file = String(sporeAttr(el, 'data-spore-wheel-click-file', 'click.mp3'));
        file = file.replace(/^.*[\\/]/, '') || 'click.mp3';
        return '/assets/roulette/' + encodeURIComponent(file);
    }

    function sporeClickLatencySec() {
        var ctx = sporeClickCtx;
        var latency = 0;
        if (!ctx) { return SPORE_CLICK_LATENCY_SEC; }
        if (typeof ctx.outputLatency === 'number' && ctx.outputLatency > 0) { latency += ctx.outputLatency; }
        if (typeof ctx.baseLatency === 'number' && ctx.baseLatency > 0) { latency += ctx.baseLatency; }
        if (latency <= 0 || latency > SPORE_CLICK_LATENCY_MAX) { return SPORE_CLICK_LATENCY_SEC; }
        return latency;
    }

    function sporeEnsureClickGraph(volume) {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) { return null; }
        try {
            if (!sporeClickCtx) {
                sporeClickCtx = new Ctx();
                sporeClickGain = sporeClickCtx.createGain();
                sporeClickGain.connect(sporeClickCtx.destination);
            }
            if (sporeClickGain) { sporeClickGain.gain.value = volume; }
            if (sporeClickCtx.state === 'suspended') {
                sporeClickCtx.resume();
            }
        } catch (e) {
            return null;
        }
        return sporeClickCtx;
    }

    function sporeDecodeClick(ctx, arrayBuffer) {
        try {
            var result = ctx.decodeAudioData(arrayBuffer);
            if (result && typeof result.then === 'function') { return result; }
        } catch (e) { /* older webkit is callback-only */ }
        return new Promise(function (resolve, reject) {
            ctx.decodeAudioData(arrayBuffer, resolve, reject);
        });
    }

    function sporeTrimClickOffset(buffer) {
        sporeClickBufferOffset = 0;
        if (!buffer || !buffer.length) { return; }
        try {
            var data = buffer.getChannelData(0);
            var i = 0;
            while (i < data.length && Math.abs(data[i]) < 0.02) { i++; }
            var offset = i / buffer.sampleRate;
            if (offset > 0 && offset < 0.03) { sporeClickBufferOffset = offset; }
        } catch (e) {
            sporeClickBufferOffset = 0;
        }
    }

    function sporeLoadClickBuffer(url) {
        var ctx = sporeClickCtx;
        if (!ctx) { return; }
        fetch(url).then(function (response) {
            if (!response.ok) { throw new Error('click sound fetch failed'); }
            return response.arrayBuffer();
        }).then(function (buf) {
            return sporeDecodeClick(ctx, buf);
        }).then(function (decoded) {
            if (decoded && sporeClickBufferUrl === url) {
                sporeClickBuffer = decoded;
                sporeTrimClickOffset(decoded);
            }
        }).catch(function () { /* fall back to HTML audio */ });
    }

    function sporePrepareClick(el, volume) {
        var url = sporeClickUrl(el);
        sporeEnsureClickGraph(volume);
        if (!sporeClickAudio || sporeClickBufferUrl !== url) {
            sporeClickAudio = new Audio(url);
        }
        sporeClickAudio.volume = volume;
        if (sporeClickBufferUrl !== url) {
            sporeClickBufferUrl = url;
            sporeClickBuffer = null;
            sporeLoadClickBuffer(url);
        }
    }

    function sporeCancelFutureClicks(ownerId) {
        var now = sporeClickCtx ? sporeClickCtx.currentTime : 0;
        sporePendingClicks = sporePendingClicks.filter(function (item) {
            if (ownerId && item.owner !== ownerId) { return true; }
            if (item.src && item.when > now + 0.001) {
                try { item.src.stop(); } catch (e) {}
                return false;
            }
            return true;
        });
    }

    function sporePlayClickFallback(volume) {
        if (!sporeClickAudio) { return; }
        try {
            var node = sporeClickAudio.cloneNode(true);
            node.volume = volume;
            node.play().catch(function () {});
        } catch (e) {}
    }

    function sporeScheduleClick(delayMs, volume, ownerId) {
        try {
            var ctx = sporeEnsureClickGraph(volume);
            if (ctx && sporeClickBuffer && sporeClickGain) {
                var src = ctx.createBufferSource();
                src.buffer = sporeClickBuffer;
                src.connect(sporeClickGain);
                var when = ctx.currentTime + (delayMs / 1000) - sporeClickLatencySec();
                if (when < ctx.currentTime) { when = ctx.currentTime; }
                src.start(when, sporeClickBufferOffset);
                var item = { src: src, when: when, owner: ownerId };
                sporePendingClicks.push(item);
                src.onended = function () {
                    sporePendingClicks = sporePendingClicks.filter(function (row) {
                        return row.src !== src;
                    });
                };
                return;
            }
        } catch (e) {}
        var fallbackDelay = Math.max(0, delayMs - SPORE_CLICK_LATENCY_SEC * 1000);
        if (fallbackDelay < 16) { sporePlayClickFallback(volume); }
        else { setTimeout(function () { sporePlayClickFallback(volume); }, fallbackDelay); }
    }

    function sporeBorderHits(r0, r1, n) {
        var hits = [];
        if (!(n > 0) || !(r1 > r0)) { return hits; }
        var slice = 360 / n;
        var i;
        for (i = 0; i < n; i++) {
            var target = sporeMod(-i * slice);
            var k0 = Math.floor((r0 - target) / 360) + 1;
            var k1 = Math.floor((r1 - target) / 360);
            var k;
            for (k = k0; k <= k1; k++) {
                var rCross = target + 360 * k;
                if (rCross <= r0 || rCross > r1) { continue; }
                hits.push({ r: rCross, key: k + ':' + i });
            }
        }
        hits.sort(function (a, b) { return a.r - b.r; });
        return hits;
    }

    function sporeRotAt(job, tMs) {
        var u = tMs <= 0 ? 0 : (tMs >= job.ms ? 1 : sporeEaseUnit(job.easing, tMs / job.ms));
        return job.from + (job.to - job.from) * u;
    }

    function sporeScheduleWheelClicks(job, elapsed, frameDt) {
        var armBy = sporeClickLatencySec() * 1000 + Math.max(frameDt, 16);
        var sim = elapsed;
        var prev = sporeRotAt(job, sim);
        var walked = 0;
        var step = Math.max(8, frameDt);
        while (walked < SPORE_CLICK_LOOKAHEAD_MS) {
            var nextT = Math.min(job.ms, sim + step);
            if (nextT <= sim) { break; }
            var next = sporeRotAt(job, nextT);
            var paintAt = nextT - elapsed;
            if (paintAt <= armBy) {
                var hits = sporeBorderHits(prev, next, job.n);
                if (hits.length) {
                    var span = next - prev;
                    var fracs = [];
                    var i;
                    for (i = 0; i < hits.length; i++) {
                        var frac = span > 0 ? (hits[i].r - prev) / span : 0;
                        if (frac < 0) { frac = 0; }
                        if (frac > 1) { frac = 1; }
                        fracs.push(frac);
                    }
                    var lastFrac = fracs[fracs.length - 1];
                    var dur = nextT - sim;
                    for (i = 0; i < hits.length; i++) {
                        if (job.keys[hits[i].key]) { continue; }
                        var delay = paintAt - (lastFrac - fracs[i]) * dur;
                        if (delay < 0) { delay = 0; }
                        if (delay > armBy) { continue; }
                        job.keys[hits[i].key] = true;
                        sporeScheduleClick(delay, job.volume, job.id);
                    }
                }
            }
            if (nextT >= job.ms) { break; }
            prev = next;
            sim = nextT;
            walked += step;
        }
    }

    function sporeCancelWheel(id) {
        var job = sporeWheelJobs[id];
        if (job && job.raf) { cancelAnimationFrame(job.raf); }
        delete sporeWheelJobs[id];
        sporeCancelFutureClicks(id);
    }

    function sporeShowWheelResult(el, labelText, gen) {
        if (!sporeOn(el, 'data-spore-wheel-result', true)) {
            sporeStyleWheelLabel(el, '', false);
            return;
        }
        var fmt = sporeAttr(el, 'data-spore-wheel-result-format', '{label}');
        var text = String(fmt).split('{label}').join(labelText || '');
        sporeStyleWheelLabel(el, text, true);
        var hold = parseFloat(sporeAttr(el, 'data-spore-wheel-result-hold', '0')) || 0;
        if (hold > 0) {
            el._sporeResultTimer = setTimeout(function () {
                if (el._sporeSpinGen !== gen) { return; }
                sporeStyleWheelLabel(el, '', false);
            }, hold * 1000);
        }
    }

    function sporePaintWheel(el) {
        var rotor = el.querySelector('.spore-wheel-rotor');
        if (!rotor) { return; }
        var segments = sporeWheelSegments(el);
        rotor.style.background = 'none';
        rotor.style.transition = 'none';
        rotor.innerHTML = sporeWheelSvg(segments);
        var rot = parseFloat(el.getAttribute('data-spore-wheel-rot') || '0') || 0;
        rotor.style.transform = 'rotate(' + rot + 'deg)';
        var pointer = el.querySelector('.spore-wheel-pointer');
        if (pointer) {
            sporePlacePointer(pointer, sporeAttr(el, 'data-spore-wheel-pointer', 'top'));
        }
        if (sporeOn(el, 'data-spore-wheel-click', true)) {
            var volume = parseFloat(sporeAttr(el, 'data-spore-wheel-click-volume', '0.2'));
            if (isNaN(volume)) { volume = 0.2; }
            sporePrepareClick(el, Math.max(0, Math.min(1, volume)));
        }
    }

    function sporeWheelSpin(id, payload) {
        var el = document.getElementById(id);
        if (!el) { return; }
        var segments = sporeWheelSegments(el);
        var n = segments.length || 1;
        sporeCancelWheel(id);
        sporeClearResultTimer(el);
        sporePaintWheel(el);
        sporeStyleWheelLabel(el, '', false);

        var forcedIdx = -1;
        if (payload && payload.label != null && String(payload.label) !== '') {
            var i;
            for (i = 0; i < n; i++) {
                if (String(segments[i].label) === String(payload.label)) { forcedIdx = i; break; }
            }
        }
        var idx = forcedIdx >= 0 ? forcedIdx : Math.floor(Math.random() * n);
        var variance = sporeOn(el, 'data-spore-wheel-variance', true);
        var last = parseInt(el.getAttribute('data-spore-wheel-last') || '-1', 10);
        if (forcedIdx < 0 && variance && n > 1 && idx === last) {
            idx = (idx + 1 + Math.floor(Math.random() * (n - 1))) % n;
        }
        var slice = 360 / n;
        var jitter = 0;
        if (variance && n > 1) {
            jitter = (Math.random() * 2 - 1) * slice * 0.35;
        }
        var landing = sporeMod(360 - (idx * slice + slice / 2 + jitter));
        var current = parseFloat(el.getAttribute('data-spore-wheel-rot') || '0') || 0;
        var delta = sporeMod(landing - sporeMod(current));
        if (delta === 0) { delta = 360; }
        var turns = variance ? (4 + Math.floor(Math.random() * 4)) : 5;
        var target = current + turns * 360 + delta;
        var ms = parseInt(sporeAttr(el, 'data-spore-wheel-ms', '4000'), 10) || 4000;
        if (ms < 200) { ms = 200; }
        var easing = sporeAttr(el, 'data-spore-wheel-easing', 'ease-out');
        var sound = sporeOn(el, 'data-spore-wheel-click', true);
        var volume = parseFloat(sporeAttr(el, 'data-spore-wheel-click-volume', '0.2'));
        if (isNaN(volume)) { volume = 0.2; }
        volume = Math.max(0, Math.min(1, volume));
        var gen = (el._sporeSpinGen || 0) + 1;
        el._sporeSpinGen = gen;
        if (sound) { sporePrepareClick(el, volume); }

        var rotor = el.querySelector('.spore-wheel-rotor');
        if (rotor) {
            rotor.style.transition = 'none';
            rotor.style.transform = 'rotate(' + current + 'deg)';
        }
        var job = {
            id: id,
            from: current,
            to: target,
            ms: ms,
            easing: easing,
            n: n,
            volume: volume,
            keys: {},
            start: 0,
            lastTs: 0,
            raf: 0
        };
        sporeWheelJobs[id] = job;

        function frame(ts) {
            if (el._sporeSpinGen !== gen) { return; }
            if (!job.start) { job.start = ts; }
            var elapsed = ts - job.start;
            var frameDt = job.lastTs ? (ts - job.lastTs) : 16.67;
            job.lastTs = ts;
            if (frameDt < 0) { frameDt = 0; }
            if (frameDt > 50) { frameDt = 50; }
            if (elapsed >= ms) {
                if (rotor) { rotor.style.transform = 'rotate(' + target + 'deg)'; }
                el.setAttribute('data-spore-wheel-rot', String(target));
                el.setAttribute('data-spore-wheel-last', String(idx));
                delete sporeWheelJobs[id];
                var winner = segments[idx] || {};
                window.__sporeLastPayload.wheel = {
                    elementId: id,
                    index: idx,
                    label: winner.label || ''
                };
                sporeShowWheelResult(el, winner.label || '', gen);
                sporeEmitOutput('wheel_result', id, window.__sporeLastPayload.wheel);
                return;
            }
            var rot = sporeRotAt(job, elapsed);
            if (rotor) { rotor.style.transform = 'rotate(' + rot + 'deg)'; }
            if (sound) { sporeScheduleWheelClicks(job, elapsed, frameDt); }
            job.raf = requestAnimationFrame(frame);
        }
        job.raf = requestAnimationFrame(frame);
    }

    function sporePaintNowPlaying(el, data) {
        data = data || {};
        var show = function (attr) {
            var v = String(sporeAttr(el, attr, 'true')).toLowerCase();
            return v !== 'false' && v !== '0' && v !== 'no';
        };
        var art = el.querySelector('.spore-np-art');
        var title = el.querySelector('.spore-np-title');
        var artist = el.querySelector('.spore-np-artist');
        var bar = el.querySelector('.spore-np-bar');
        var fill = el.querySelector('.spore-np-fill');
        if (art) {
            art.style.display = show('data-spore-np-art') ? '' : 'none';
            if (data.album_image_url) { art.src = data.album_image_url; }
        }
        if (title) {
            title.style.display = show('data-spore-np-title') ? '' : 'none';
            title.style.color = sporeAttr(el, 'data-spore-np-title-color', '#ffffff');
            title.textContent = data.track_name || 'Not playing';
        }
        if (artist) {
            artist.style.display = show('data-spore-np-artist') ? '' : 'none';
            artist.style.color = sporeAttr(el, 'data-spore-np-artist-color', '#94a3b8');
            artist.textContent = data.artist_name || '';
        }
        if (bar) { bar.style.display = show('data-spore-np-progress') ? '' : 'none'; }
        if (fill) {
            fill.style.width = (Number(data.progress_percentage) || 0) + '%';
            fill.style.background = sporeAttr(el, 'data-spore-np-bar', '#1db954');
        }
        if (sporeAttr(el, 'data-spore-np-layout', 'row') === 'stack') {
            el.style.flexDirection = 'column';
            el.style.alignItems = 'stretch';
        }
    }

    function sporePaintQr(el) {
        var host = el.querySelector('.spore-qr-host');
        if (!host) { return; }
        var text = sporeAttr(el, 'data-spore-qr-text', '');
        if (typeof qrcode !== 'function') {
            host.textContent = text;
            return;
        }
        try {
            var qr = qrcode(0, 'M');
            qr.addData(text || ' ');
            qr.make();
            var svg = qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
            var fg = sporeAttr(el, 'data-spore-qr-fg', '#000000');
            var bg = sporeAttr(el, 'data-spore-qr-bg', '#ffffff');
            svg = svg.replace('fill="white"', 'fill="' + bg.replace(/"/g, '') + '"')
                .replace('fill="black"', 'fill="' + fg.replace(/"/g, '') + '"');
            host.innerHTML = svg;
            var node = host.querySelector('svg');
            if (node) { node.style.width = '100%'; node.style.height = '100%'; }
        } catch (err) {
            host.textContent = text;
        }
    }

    function sporePaintEmbed(el) {
        var frame = el.querySelector('.spore-embed-frame');
        if (!frame) { return; }
        var url = sporeAttr(el, 'data-spore-embed-url', '');
        var scale = parseFloat(sporeAttr(el, 'data-spore-embed-scale', '1')) || 1;
        if (scale <= 0) { scale = 1; }
        frame.style.transformOrigin = 'top left';
        frame.style.transform = 'scale(' + scale + ')';
        frame.style.width = (100 / scale) + '%';
        frame.style.height = (100 / scale) + '%';
        if (url && frame.getAttribute('src') !== url) { frame.setAttribute('src', url); }
        var refresh = parseInt(sporeAttr(el, 'data-spore-embed-refresh', '0'), 10) || 0;
        if (refresh > 0 && !el.getAttribute('data-spore-embed-timer')) {
            el.setAttribute('data-spore-embed-timer', '1');
            setInterval(function () {
                var next = sporeAttr(el, 'data-spore-embed-url', '');
                if (!next) { return; }
                var bust = next + (next.indexOf('?') >= 0 ? '&' : '?') + '_spore=' + Date.now();
                frame.setAttribute('src', bust);
            }, refresh * 1000);
        }
    }

    function sporeRefreshStatCards() {
        var cards = window.__sporeStatCards || [];
        for (var i = 0; i < cards.length; i++) {
            var spec = cards[i];
            var el = document.getElementById(spec.elementId);
            if (!el) { continue; }
            var value = sporeResolveSource(
                spec.source || {},
                window.__sporeLastPayload.event || {},
                window.__sporeLastPayload.ctx || {}
            );
            sporeApplyStatCard(el, value);
        }
    }

    /* Paint a stat card from a resolved value (shared with Data feeds). */
    function sporeApplyStatCard(el, value, textOverride) {
        var fmt = sporeAttr(el, 'data-spore-stat-format', '{value}');
        var label = sporeAttr(el, 'data-spore-stat-label', '');
        var icon = sporeAttr(el, 'data-spore-stat-icon', '');
        var labelEl = el.querySelector('.spore-stat-label');
        var valueEl = el.querySelector('.spore-stat-value');
        var iconEl = el.querySelector('.spore-stat-icon');
        if (labelEl) {
            labelEl.textContent = label;
            labelEl.style.color = sporeAttr(el, 'data-spore-stat-label-color', '#94a3b8');
        }
        if (valueEl) {
            if (textOverride != null && textOverride !== '') {
                valueEl.textContent = String(textOverride);
            } else {
                valueEl.textContent = (value == null || value === '')
                    ? '\u2014'
                    : sporeFormatDisplay(value, fmt);
            }
            valueEl.style.color = sporeAttr(el, 'data-spore-stat-value-color', '#ffffff');
        }
        if (iconEl) { iconEl.textContent = icon; }
    }

    function sporeBindList(el) {
        if (!el || el.getAttribute('data-spore-list-bound') === '1') { return; }
        el.setAttribute('data-spore-list-bound', '1');
        var source = sporeAttr(el, 'data-spore-list-source', 'alerts');
        var events = [];
        if (source === 'alerts') { events = ['instant_alert', 'next_alert']; }
        else if (source === 'giveaway') { events = ['giveaway_entry', 'giveaway_winner']; }
        else if (source === 'payload') {
            var named = sporeAttr(el, 'data-spore-list-event', '');
            if (named) { events = [named]; }
        }
        events.forEach(function (evName) {
            if (typeof socket === 'undefined' || !socket || !socket.on) { return; }
            socket.on(evName, function (data) {
                data = data || {};
                if (sporeIsReplay(data)) { return; }
                if (source === 'alerts') {
                    var want = String(sporeAttr(el, 'data-spore-list-alert-type', 'any')).toLowerCase();
                    if (want && want !== 'any' && sporeAlertType(data) !== want) { return; }
                }
                if (evName === 'giveaway_winner' && Array.isArray(data.winners)) {
                    /* One row per winner; the payload itself has no single username. */
                    data.winners.forEach(function (name) {
                        var row = Object.assign({}, data, { username: String(name) });
                        sporeListAdd(el.id, row, '');
                    });
                    return;
                }
                sporeListAdd(el.id, data, '');
            });
        });
    }

    function sporePollTemplateName() {
        if (window.__sporeTemplateName) { return String(window.__sporeTemplateName); }
        var root = document.getElementById('sporeRoot');
        return root ? String(root.getAttribute('data-mycelian-template') || '') : '';
    }

    function sporeNotePollClock(el, data) {
        if (!el || !data || !data.server_now) { return; }
        var stamp = Number(data.server_now);
        if (!stamp || el._sporePollServerNow === stamp) { return; }
        el._sporePollServerNow = stamp;
        el._sporePollSkew = stamp - (Date.now() / 1000);
    }

    function sporePollRemaining(el, data) {
        if (!data || data.status !== 'open' || data.permanent || !data.ends_at) { return null; }
        sporeNotePollClock(el, data);
        var skew = el._sporePollSkew || 0;
        return Math.max(0, Number(data.ends_at) - ((Date.now() / 1000) + skew));
    }

    function sporeFormatPollClock(seconds) {
        var total = Math.max(0, Math.ceil(seconds));
        var minutes = Math.floor(total / 60);
        var secs = total % 60;
        var hours = Math.floor(minutes / 60);
        minutes = minutes % 60;
        function pad(n) { return (n < 10 ? '0' : '') + n; }
        if (hours) { return hours + ':' + pad(minutes) + ':' + pad(secs); }
        return pad(minutes) + ':' + pad(secs);
    }

    function sporePollChild(el, cls) {
        return el.querySelector('.' + cls);
    }

    function sporeUpdatePollClock(el) {
        var data = el._sporePollLast || {};
        var timer = sporePollChild(el, 'spore-poll-timer');
        var chip = sporePollChild(el, 'spore-poll-chip');
        var showText = sporeOn(el, 'data-spore-poll-timer', true);
        var showBar = sporeOn(el, 'data-spore-poll-timer-bar', true);
        var remain = sporePollRemaining(el, data);
        var open = data.status === 'open';
        var timed = open && !data.permanent && remain != null;
        if (chip) {
            var chipText = '';
            chip.classList.remove('is-live');
            if (open && showText) {
                chipText = timed ? sporeFormatPollClock(remain) : 'Live';
                chip.classList.add('is-live');
            } else if (data.status === 'ended') {
                chipText = 'Poll closed';
            }
            chip.hidden = !chipText;
            if (chip.textContent !== chipText) { chip.textContent = chipText; }
        }
        sporePollSyncHead(el);
        if (!timer) { return; }
        if (!timed || !showBar) {
            timer.hidden = true;
            return;
        }
        timer.hidden = false;
        var fill = timer.querySelector('.spore-poll-timer-fill');
        if (fill) {
            var duration = Number(data.duration_seconds || 0);
            var pct = duration > 0 ? Math.max(0, Math.min(100, (remain / duration) * 100)) : 0;
            fill.style.width = pct + '%';
        }
    }

    function sporePollSyncHead(el) {
        var head = sporePollChild(el, 'spore-poll-head');
        if (!head) { return; }
        var title = sporePollChild(el, 'spore-poll-title');
        var chip = sporePollChild(el, 'spore-poll-chip');
        var hasTitle = !!(title && title.textContent);
        head.hidden = !hasTitle && !(chip && !chip.hidden);
    }

    function sporeArmPollClock(el, data) {
        var want = !!(data && data.status === 'open' && !data.permanent && data.ends_at &&
            (sporeOn(el, 'data-spore-poll-timer', true) || sporeOn(el, 'data-spore-poll-timer-bar', true)));
        if (!want) {
            if (el._sporePollClock) {
                clearInterval(el._sporePollClock);
                el._sporePollClock = null;
            }
            return;
        }
        if (el._sporePollClock) { return; }
        el._sporePollClock = setInterval(function () { sporeUpdatePollClock(el); }, 250);
    }

    function sporeArmPollRotator(el, pageCount, seconds, enabled) {
        var key = enabled ? (String(pageCount) + ':' + String(seconds)) : '';
        if (el._sporePollRotKey === key) { return; }
        if (el._sporePollRotTimer) {
            clearInterval(el._sporePollRotTimer);
            el._sporePollRotTimer = null;
        }
        el._sporePollRotKey = key;
        if (!enabled) {
            el._sporePollPage = 0;
            return;
        }
        el._sporePollRotTimer = setInterval(function () {
            el._sporePollPage = ((el._sporePollPage || 0) + 1) % pageCount;
            if (el._sporePollLast) { sporePaintPoll(el, el._sporePollLast, true); }
        }, seconds * 1000);
    }

    function sporePollAnimClass(raw) {
        var name = String(raw || 'slide_horizontal').toLowerCase().replace(/\s+/g, '_');
        if (name === 'slide_vertical' || name === 'vertical') { return 'slide_vertical'; }
        if (name === 'fade') { return 'fade'; }
        if (name === 'none') { return 'none'; }
        return 'slide_horizontal';
    }

    /* Read every look setting once per paint. Colours and sizes become CSS variables
       on the widget so the stylesheet (and smooth bar growth) does the rest. */
    function sporePollConfig(el) {
        var style = String(sporeAttr(el, 'data-spore-poll-style', 'bars')).toLowerCase();
        if (style !== 'inside' && style !== 'cards') { style = 'bars'; }
        var bar = sporeAttr(el, 'data-spore-poll-bar', '#a855f7');
        return {
            style: style,
            showBars: sporeAttr(el, 'data-spore-poll-display', 'bars') !== 'text' || style !== 'bars',
            showCounts: sporeOn(el, 'data-spore-poll-counts', true),
            showPercent: sporeOn(el, 'data-spore-poll-percent', true),
            showKeywords: sporeOn(el, 'data-spore-poll-keywords', true),
            showRank: sporeOn(el, 'data-spore-poll-rank', false),
            showTotal: sporeOn(el, 'data-spore-poll-total', true),
            footText: sporeAttr(el, 'data-spore-poll-foot-text', ''),
            title: sporeAttr(el, 'data-spore-poll-title', ''),
            bar: bar,
            bar2: sporeAttr(el, 'data-spore-poll-bar2', '') || bar,
            track: sporeAttr(el, 'data-spore-poll-track', 'rgba(255,255,255,0.15)'),
            winner: sporeAttr(el, 'data-spore-poll-winner', '#facc15'),
            barHeight: parseFloat(sporeAttr(el, 'data-spore-poll-bar-height', '12')),
            barRadius: parseFloat(sporeAttr(el, 'data-spore-poll-bar-radius', '12')),
            leader: sporeOn(el, 'data-spore-poll-leader', true),
            animate: sporeOn(el, 'data-spore-poll-animate', true),
            winnerFx: String(sporeAttr(el, 'data-spore-poll-winner-fx', 'glow')).toLowerCase(),
            dim: sporeOn(el, 'data-spore-poll-dim', true),
            shadow: sporeOn(el, 'data-spore-poll-shadow', true)
        };
    }

    function sporePollApplyLook(el, cfg, status) {
        var s = el.style;
        s.setProperty('--sp-bar', cfg.bar);
        s.setProperty('--sp-bar2', cfg.bar2);
        s.setProperty('--sp-track', cfg.track);
        s.setProperty('--sp-win', cfg.winner);
        s.setProperty('--sp-barh', (isFinite(cfg.barHeight) && cfg.barHeight >= 0 ? cfg.barHeight : 12) + 'px');
        s.setProperty('--sp-radius', (isFinite(cfg.barRadius) && cfg.barRadius >= 0 ? cfg.barRadius : 12) + 'px');
        var cls = el.classList;
        ['bars', 'inside', 'cards'].forEach(function (name) { cls.toggle('is-style-' + name, cfg.style === name); });
        cls.toggle('is-animated', cfg.animate);
        cls.toggle('is-leader-on', cfg.leader);
        cls.toggle('is-dim-losers', cfg.dim);
        cls.toggle('has-shadow', cfg.shadow);
        cls.toggle('fx-glow', cfg.winnerFx === 'glow');
        cls.toggle('fx-pulse', cfg.winnerFx === 'pulse');
        cls.toggle('is-ended', status === 'ended');
        var title = sporePollChild(el, 'spore-poll-title');
        if (title && title.textContent !== cfg.title) { title.textContent = cfg.title; }
    }

    function sporePollMeta(row, cfg) {
        var bits = [];
        /* Cards are narrow: the share alone reads best when both are on. */
        var counts = cfg.showCounts && !(cfg.style === 'cards' && cfg.showPercent);
        if (counts) { bits.push(String(Number(row && row.votes || 0))); }
        if (cfg.showPercent) { bits.push(String(row && row.percent != null ? row.percent : 0) + '%'); }
        return bits.join(' · ');
    }

    function sporePollMakeOption(row, cfg, rank) {
        var option = document.createElement('div');
        option.className = 'spore-poll-option';
        option.setAttribute('data-option-id', String(row && row.id != null ? row.id : (row && row.name) || ''));
        var line = document.createElement('div');
        line.className = 'spore-poll-row';
        if (cfg.showRank && cfg.style !== 'cards') {
            var badge = document.createElement('span');
            badge.className = 'spore-poll-rank';
            badge.textContent = String(rank);
            line.appendChild(badge);
        }
        var name = document.createElement('span');
        name.className = 'spore-poll-name';
        line.appendChild(name);
        if (cfg.showKeywords) {
            var kw = document.createElement('span');
            kw.className = 'spore-poll-kw';
            line.appendChild(kw);
        }
        var meta = document.createElement('span');
        meta.className = 'spore-poll-meta';
        line.appendChild(meta);
        option.appendChild(line);
        if (cfg.showBars) {
            var track = document.createElement('div');
            track.className = 'spore-poll-bar';
            var fill = document.createElement('div');
            fill.className = 'spore-poll-bar-fill';
            fill.style.setProperty('--sp-fill', '0%');
            track.appendChild(fill);
            option.appendChild(track);
        }
        return option;
    }

    function sporePollUpdateOption(option, row, cfg, leader, status) {
        var votes = Number(row && row.votes || 0);
        option.classList.toggle('is-winner', !!(row && row.winner));
        option.classList.toggle('is-leader', status === 'open' && leader > 0 && votes === leader);
        var name = option.querySelector('.spore-poll-name');
        var label = row && row.name ? String(row.name) : '';
        if (name && name.textContent !== label) { name.textContent = label; }
        var kw = option.querySelector('.spore-poll-kw');
        if (kw) {
            var word = row && row.keyword ? String(row.keyword) : '';
            kw.textContent = word;
            kw.hidden = !word;
        }
        var meta = option.querySelector('.spore-poll-meta');
        if (meta) {
            var text = sporePollMeta(row, cfg);
            if (meta.textContent !== text) { meta.textContent = text; }
            meta.hidden = !text;
        }
        var fill = option.querySelector('.spore-poll-bar-fill');
        if (fill) { fill.style.setProperty('--sp-fill', (leader > 0 ? (votes / leader) * 100 : 0) + '%'); }
    }

    function sporePollFitCount(el, host, probe, gap, horizontal, reserved) {
        probe.style.visibility = 'hidden';
        probe.style.flex = '0 0 auto';
        probe.style.width = horizontal ? 'auto' : '100%';
        host.appendChild(probe);
        var rowH = probe.offsetHeight || 1;
        var rowW = probe.offsetWidth || 1;
        host.removeChild(probe);
        var style = window.getComputedStyle(el);
        var padY = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        var padX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
        var availH = Math.max(0, el.clientHeight - padY - reserved);
        var availW = Math.max(0, el.clientWidth - padX);
        var fitH = Math.floor((availH + gap) / (rowH + gap));
        var fitW = Math.floor((availW + gap) / (rowW + gap));
        var fit = horizontal ? fitW : fitH;
        if (!isFinite(fit) || fit < 1) { fit = 1; }
        return fit;
    }

    /* Height used by the header, timer bar and footer, so the options get the rest. */
    function sporePollReserved(el) {
        var style = window.getComputedStyle(el);
        var outerGap = parseFloat(style.rowGap || style.gap) || 0;
        var total = 0;
        ['spore-poll-head', 'spore-poll-timer', 'spore-poll-foot'].forEach(function (cls) {
            var part = sporePollChild(el, cls);
            if (part && !part.hidden) { total += part.offsetHeight + outerGap; }
        });
        return total;
    }

    function sporePollPaintFoot(el, cfg, data) {
        var foot = sporePollChild(el, 'spore-poll-foot');
        if (!foot) { return; }
        var left = foot.querySelector('.spore-poll-foot-text');
        var right = foot.querySelector('.spore-poll-total');
        var note = data.status === 'open' ? cfg.footText : '';
        var total = Number(data.total_votes || 0);
        var totalText = cfg.showTotal ? (total + (total === 1 ? ' vote' : ' votes')) : '';
        if (left) { left.textContent = note; }
        if (right) { right.textContent = totalText; }
        foot.hidden = !note && !totalText;
    }

    /* Hide/show a poll. One that started hidden goes back to hidden (attribute + Out
       animation) so the next poll plays its In animation again. */
    function sporePollSetVisible(el, visible) {
        if (visible) {
            el.style.display = '';
            if (el.getAttribute('data-spore-hidden') === 'true') {
                el._sporePollStartHidden = true;
                sporeFeedShow(el);
            }
            return;
        }
        if (el._sporePollStartHidden) {
            if (el.getAttribute('data-spore-hidden') !== 'true') { sporeFeedHide(el); }
        } else {
            el.style.display = 'none';
        }
    }

    function sporePaintPoll(el, data, fromRotator) {
        if (!el) { return; }
        data = data || {};
        el._sporePollLast = data;
        var status = String(data.status || 'idle');
        if (status === 'open' && el._sporePollHide) {
            clearTimeout(el._sporePollHide);
            el._sporePollHide = null;
            el._sporePollHideEndsAt = null;
        }
        if (status === 'idle') {
            sporePollSetVisible(el, false);
            sporeArmPollRotator(el, 1, 5, false);
            sporeArmPollClock(el, data);
            return;
        }
        var autohide = parseFloat(sporeAttr(el, 'data-spore-poll-autohide', '0'));
        if (!isFinite(autohide) || autohide < 0) { autohide = 0; }
        if (status !== 'open' && autohide > 0) {
            var endedAt = Number(data.ended_at || 0);
            sporeNotePollClock(el, data);
            var skew = el._sporePollSkew || 0;
            var age = endedAt ? ((Date.now() / 1000) + skew) - endedAt : autohide;
            var stillShowingWinner = status === 'ended' && endedAt && age < autohide;
            if (!stillShowingWinner) {
                sporePollSetVisible(el, false);
                sporeArmPollRotator(el, 1, 5, false);
                sporeArmPollClock(el, data);
                return;
            }
            sporePollSetVisible(el, true);
            if (el._sporePollHideEndsAt !== endedAt) {
                if (el._sporePollHide) { clearTimeout(el._sporePollHide); }
                el._sporePollHideEndsAt = endedAt;
                el._sporePollHide = setTimeout(function () {
                    sporePollSetVisible(el, false);
                }, Math.max(0, (autohide - age) * 1000));
            }
        } else {
            /* "Start hidden" means hidden until a poll runs: this reveals it with its In animation. */
            sporePollSetVisible(el, true);
        }

        var size = sporeAttr(el, 'data-spore-poll-font-size', '');
        if (size) { el.style.fontSize = parseFloat(size) + 'px'; }
        var color = sporeAttr(el, 'data-spore-poll-color', '');
        if (color) { el.style.color = color; }
        var weight = sporeAttr(el, 'data-spore-poll-weight', '');
        if (weight) { el.style.fontWeight = weight; }
        var bg = sporeAttr(el, 'data-spore-poll-bg', '');
        if (bg) { el.style.background = bg; }

        var cfg = sporePollConfig(el);
        sporePollApplyLook(el, cfg, status);
        var options = Array.isArray(data.options) ? data.options.slice() : [];
        var winnerMode = sporeAttr(el, 'data-spore-poll-winner-mode', 'highlight');
        if (status === 'ended' && winnerMode === 'winner_only') {
            options = options.filter(function (row) { return row && row.winner; });
        }
        var host = sporePollChild(el, 'spore-poll-options');
        if (!host) { return; }
        sporeUpdatePollClock(el);
        sporeArmPollClock(el, data);
        sporePollPaintFoot(el, cfg, data);
        var horizontal = cfg.style === 'cards' || sporeAttr(el, 'data-spore-poll-layout', 'vertical') === 'horizontal';
        var gap = parseFloat(sporeAttr(el, 'data-spore-poll-gap', '8'));
        if (!isFinite(gap) || gap < 0) { gap = 0; }
        var leader = 0;
        options.forEach(function (row) {
            var count = Number(row && row.votes || 0);
            if (count > leader) { leader = count; }
        });
        var reserved = sporePollReserved(el);
        var style = window.getComputedStyle(el);
        var padY = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        var availH = Math.max(0, el.clientHeight - padY - reserved);
        host.style.height = availH + 'px';
        var probe = sporePollMakeOption(options[0] || { name: 'Option', votes: 0, percent: 0 }, cfg, 1);
        sporePollUpdateOption(probe, options[0] || { name: 'Option', votes: 0, percent: 0 }, cfg, leader, status);
        var fit = options.length ? sporePollFitCount(el, host, probe, gap, horizontal, reserved) : 1;
        if (cfg.style === 'cards' && options.length) {
            /* Tiles share the width, so fit by a minimum tile width rather than the probe's. */
            var tileW = (parseFloat(window.getComputedStyle(el).fontSize) || 22) * 4.5;
            var padX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
            fit = Math.max(1, Math.floor((Math.max(0, el.clientWidth - padX) + gap) / (tileW + gap)));
        }
        var maxItems = parseInt(sporeAttr(el, 'data-spore-poll-max', '6'), 10);
        if (!isFinite(maxItems) || maxItems < 0) { maxItems = 0; }
        var pageSize = maxItems > 0 ? Math.min(maxItems, fit) : fit;
        if (pageSize < 1) { pageSize = 1; }
        var pageCount = Math.max(1, Math.ceil((options.length || 1) / pageSize));
        if ((el._sporePollPage || 0) >= pageCount) { el._sporePollPage = 0; }
        var rotatorOn = sporeOn(el, 'data-spore-poll-rotator', false) && options.length > pageSize;
        var rotSeconds = parseFloat(sporeAttr(el, 'data-spore-poll-rotator-seconds', '5'));
        if (!isFinite(rotSeconds) || rotSeconds <= 0) { rotSeconds = 5; }
        sporeArmPollRotator(el, pageCount, rotSeconds, rotatorOn);
        var page = rotatorOn ? (el._sporePollPage || 0) : 0;
        var slice = options.slice(page * pageSize, (page * pageSize) + pageSize);
        var shape = [cfg.style, cfg.showBars, cfg.showKeywords, cfg.showRank, horizontal, page].join('|');

        /* Same options on the same page: update in place so bars grow smoothly. */
        var current = host.querySelector('.spore-poll-page:not(.is-exiting)');
        if (!fromRotator && current && current._sporeShape === shape) {
            var known = {};
            Array.prototype.forEach.call(current.children, function (node) {
                known[node.getAttribute('data-option-id')] = node;
            });
            var ids = slice.map(function (row) { return String(row && row.id != null ? row.id : (row && row.name) || ''); });
            var allKnown = ids.length === current.children.length && ids.every(function (id) { return !!known[id]; });
            if (allKnown) {
                slice.forEach(function (row, i) {
                    var node = known[ids[i]];
                    current.appendChild(node);
                    var badge = node.querySelector('.spore-poll-rank');
                    if (badge) { badge.textContent = String(page * pageSize + i + 1); }
                    sporePollUpdateOption(node, row, cfg, leader, status);
                });
                current.style.gap = gap + 'px';
                return;
            }
        }

        var previousWidths = {};
        if (current) {
            Array.prototype.forEach.call(current.children, function (node) {
                var fillOld = node.querySelector('.spore-poll-bar-fill');
                if (fillOld) { previousWidths[node.getAttribute('data-option-id')] = fillOld.style.getPropertyValue('--sp-fill'); }
            });
        }
        var heldPage = null;
        if (fromRotator) {
            heldPage = current;
            if (heldPage) { heldPage.remove(); }
        }
        host.textContent = '';
        host.style.position = 'relative';
        host.style.overflow = 'hidden';
        host.style.transform = 'translateZ(0)';
        var pageEl = document.createElement('div');
        pageEl.className = 'spore-poll-page' + (horizontal ? ' is-horizontal' : '');
        pageEl.style.gap = gap + 'px';
        pageEl._sporeShape = shape;
        var anim = 'none';
        var animMs = 400;
        if (fromRotator) {
            anim = sporePollAnimClass(sporeAttr(el, 'data-spore-poll-rotator-anim', 'slide_horizontal'));
            animMs = parseFloat(sporeAttr(el, 'data-spore-poll-rotator-anim-ms', '400'));
            if (!isFinite(animMs) || animMs < 0) { animMs = 400; }
        }
        var fresh = [];
        slice.forEach(function (row, i) {
            var node = sporePollMakeOption(row, cfg, page * pageSize + i + 1);
            var id = node.getAttribute('data-option-id');
            var fillNew = node.querySelector('.spore-poll-bar-fill');
            if (fillNew && previousWidths[id]) { fillNew.style.setProperty('--sp-fill', previousWidths[id]); }
            pageEl.appendChild(node);
            fresh.push([node, row]);
        });
        var previous = heldPage;
        if (previous && anim !== 'none') {
            previous.classList.add('is-exiting', 'is-exit-' + anim);
            previous.style.position = 'absolute';
            previous.style.left = '0';
            previous.style.right = '0';
            previous.style.top = '0';
            previous.style.width = '100%';
            previous.style.margin = '0';
            previous.style.animationDuration = (animMs / 1000) + 's';
            host.appendChild(previous);
            var removePrevious = function () {
                if (previous.parentNode === host) { host.removeChild(previous); }
            };
            previous.addEventListener('animationend', removePrevious);
            setTimeout(removePrevious, animMs + 50);
        }
        if (fromRotator && anim !== 'none') {
            pageEl.classList.add('is-' + anim);
            pageEl.style.animationDuration = (animMs / 1000) + 's';
        }
        host.appendChild(pageEl);
        /* Lay out the old widths first (forced reflow) so a reorder still animates to the
           new widths. No requestAnimationFrame: browsers pause it for hidden sources. */
        if (cfg.animate) { void pageEl.offsetWidth; }
        fresh.forEach(function (pair) { sporePollUpdateOption(pair[0], pair[1], cfg, leader, status); });
    }

    function sporePollIsMine(payload) {
        var mine = sporePollTemplateName();
        return !!(payload && payload.template && mine && String(payload.template) === mine);
    }

    /* feedDriven: a Data feed repaints the poll, so only replay state on load and
       announce the result here (the feed handles poll_state / poll_ended). */
    function sporeBindPoll(el, feedDriven) {
        if (!el || el.getAttribute('data-spore-poll-bound') === '1') { return; }
        el.setAttribute('data-spore-poll-bound', '1');
        function onPayload(payload) {
            payload = payload || {};
            if (!sporePollIsMine(payload)) { return; }
            sporePaintPoll(el, payload);
        }
        if (typeof socket !== 'undefined' && socket && socket.on) {
            if (!feedDriven) { socket.on('poll_state', onPayload); }
            socket.on('poll_ended', function (payload) {
                if (!feedDriven) { onPayload(payload); }
                payload = payload || {};
                var mine = sporePollTemplateName();
                if (payload.template && mine && String(payload.template) === mine) {
                    sporeEmitOutput('poll_result', el.id, {
                        winners: payload.winners || [],
                        total_votes: payload.total_votes || 0,
                        options: payload.options || []
                    });
                }
            });
            socket.on('poll_sync', onPayload);
            if (!window.__sporePollRequested && socket.emit) {
                window.__sporePollRequested = true;
                socket.emit('poll_request_state', { template: sporePollTemplateName() });
            }
        }
        sporePaintPoll(el, { status: 'idle', options: [] });
    }

    /* Confetti block: a canvas the size of the block. Each burst adds pieces over the
       block's duration; several bursts can overlap. Draws with requestAnimationFrame when
       the browser runs it, otherwise with a timer. */
    function sporeConfettiConfig(el) {
        function num(attr, fallback, min, max) {
            var v = parseFloat(sporeAttr(el, attr, String(fallback)));
            if (!isFinite(v)) { v = fallback; }
            return Math.max(min, Math.min(max, v));
        }
        var colors = String(sporeAttr(el, 'data-spore-confetti-colors', '')).split(/[\n,]+/).map(function (c) {
            return c.trim();
        }).filter(Boolean);
        return {
            count: num('data-spore-confetti-count', 150, 1, 2000),
            duration: num('data-spore-confetti-duration', 5, 0.2, 60),
            spread: num('data-spore-confetti-spread', 70, 5, 360),
            size: num('data-spore-confetti-size', 10, 2, 60),
            gravity: num('data-spore-confetti-gravity', 1, 0, 5),
            origin: String(sporeAttr(el, 'data-spore-confetti-origin', 'sides')).toLowerCase(),
            colors: colors.length ? colors : ['#7c3aed', '#2563eb', '#e11d48', '#16a34a', '#d97706', '#facc15']
        };
    }

    function sporeConfettiPiece(cfg, w, h, side) {
        var rad = Math.PI / 180;
        var angle;
        var x;
        var y;
        var speed = (0.55 + Math.random() * 0.6) * Math.max(w, h) * 0.022;
        if (cfg.origin === 'top') {
            x = Math.random() * w;
            y = -cfg.size;
            angle = 90 + (Math.random() - 0.5) * Math.min(cfg.spread, 90);
            speed *= 0.35;
        } else if (cfg.origin === 'center') {
            x = w / 2;
            y = h / 2;
            angle = 270 + (Math.random() - 0.5) * cfg.spread;
        } else {
            x = side ? w : 0;
            y = h * 0.85;
            angle = (side ? 240 : 300) + (Math.random() - 0.5) * cfg.spread;
        }
        return {
            x: x, y: y,
            vx: Math.cos(angle * rad) * speed,
            vy: Math.sin(angle * rad) * speed,
            rot: Math.random() * 360, vr: (Math.random() - 0.5) * 18,
            tilt: Math.random() * Math.PI, vt: 0.05 + Math.random() * 0.1,
            w: cfg.size * (0.6 + Math.random() * 0.6), h: cfg.size * (0.35 + Math.random() * 0.4),
            color: cfg.colors[Math.floor(Math.random() * cfg.colors.length)],
            round: Math.random() < 0.25,
            g: cfg.gravity,
            age: 0, life: 2.6 + Math.random() * 1.8
        };
    }

    function sporeConfettiBurst(el) {
        var canvas = el && el.querySelector('.spore-confetti-canvas');
        if (!canvas || !canvas.getContext) { return; }
        var cfg = sporeConfettiConfig(el);
        var state = el._sporeConfetti || (el._sporeConfetti = { pieces: [], spawns: [], running: false });
        var started = Date.now();
        state.spawns.push({ cfg: cfg, started: started, made: 0 });
        if (state.running) { return; }
        state.running = true;
        var ctx = canvas.getContext('2d');
        var last = Date.now();
        var raf = window.requestAnimationFrame;
        function schedule() {
            if (raf && !document.hidden) { raf(step); } else { setTimeout(step, 16); }
        }
        function step() {
            var now = Date.now();
            var dt = Math.min(0.05, (now - last) / 1000);
            last = now;
            var dpr = window.devicePixelRatio || 1;
            var w = el.clientWidth || 1;
            var h = el.clientHeight || 1;
            if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
                canvas.width = Math.round(w * dpr);
                canvas.height = Math.round(h * dpr);
            }
            state.spawns = state.spawns.filter(function (spawn) {
                var t = (now - spawn.started) / 1000;
                var emitFor = spawn.cfg.duration * 0.6;
                var due = Math.min(spawn.cfg.count, Math.ceil(spawn.cfg.count * Math.min(1, t / emitFor)));
                while (spawn.made < due) {
                    state.pieces.push(sporeConfettiPiece(spawn.cfg, w, h, spawn.made % 2 === 1));
                    spawn.made++;
                }
                return spawn.made < spawn.cfg.count;
            });
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, w, h);
            var frame = dt * 60;
            state.pieces = state.pieces.filter(function (p) {
                p.age += dt;
                p.vy += 0.32 * p.g * frame;
                p.vx *= Math.pow(0.985, frame);
                p.vy *= Math.pow(0.985, frame);
                p.x += p.vx * frame;
                p.y += p.vy * frame;
                p.rot += p.vr * frame;
                p.tilt += p.vt * frame;
                var fade = Math.max(0, Math.min(1, (p.life - p.age) / 0.6));
                if (fade <= 0 || p.y > h + 40) { return false; }
                ctx.save();
                ctx.globalAlpha = fade;
                ctx.translate(p.x, p.y);
                ctx.rotate(p.rot * Math.PI / 180);
                ctx.scale(1, Math.abs(Math.cos(p.tilt)) * 0.8 + 0.2);
                ctx.fillStyle = p.color;
                if (p.round) {
                    ctx.beginPath();
                    ctx.arc(0, 0, p.h * 0.6, 0, Math.PI * 2);
                    ctx.fill();
                } else {
                    ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
                }
                ctx.restore();
                return true;
            });
            if (state.pieces.length || state.spawns.length) {
                schedule();
            } else {
                state.running = false;
                ctx.clearRect(0, 0, w, h);
            }
        }
        schedule();
    }

    window.sporeConfetti = function (id) {
        var el = document.getElementById(id);
        if (el) { sporeConfettiBurst(el); }
    };

    function sporeFeedDrives(elementId, targetId) {
        return (window.__sporeFeeds || []).some(function (feed) {
            return feed && feed.enabled && feed.elementId === elementId &&
                feed.target && feed.target.id === targetId;
        });
    }

    function sporeInitWidgets() {
        sporeAccentStaticText();
        var lists = document.querySelectorAll('[data-spore-type="list"]');
        /* Generated templates drive lists through Data feeds; the legacy binder is for old HTML. */
        for (var i = 0; i < lists.length; i++) {
            if (!window.__sporeFeedsCompiled) { sporeBindList(lists[i]); }
        }
        var wheels = document.querySelectorAll('[data-spore-type="wheel"]');
        for (var w = 0; w < wheels.length; w++) {
            sporePaintWheel(wheels[w]);
            if (!window.__sporeFeedsCompiled &&
                    sporeAttr(wheels[w], 'data-spore-wheel-source', 'static') === 'giveaway' &&
                    typeof socket !== 'undefined' && socket && socket.on) {
                (function (wheel) {
                    socket.on('giveaway_winner', function (data) {
                        var entries = (data && (data.pool_entries || data.winners)) || [];
                        if (entries.length) {
                            var segs = entries.map(function (name, n) {
                                var colors = ['#a855f7', '#22c55e', '#f59e0b', '#38bdf8', '#f43f5e', '#eab308'];
                                return { label: String(name), color: colors[n % colors.length] };
                            });
                            wheel.setAttribute('data-spore-wheel-segments', JSON.stringify(segs));
                        }
                        var winner = data && data.winners && data.winners[0];
                        sporeWheelSpin(wheel.id, winner ? { label: winner } : data);
                    });
                })(wheels[w]);
            }
        }
        var playing = document.querySelectorAll('[data-spore-type="now_playing"]');
        var playingNeedsBuiltin = false;
        for (var n = 0; n < playing.length; n++) {
            if (!sporeFeedDrives(playing[n].id, 'repaint')) { playingNeedsBuiltin = true; }
        }
        if (playingNeedsBuiltin && playing.length && typeof socket !== 'undefined' && socket && socket.on) {
            socket.on('spotify_data_update', function (data) {
                for (var p = 0; p < playing.length; p++) {
                    if (!sporeFeedDrives(playing[p].id, 'repaint')) {
                        sporePaintNowPlaying(playing[p], data);
                    }
                }
            });
        }
        for (var p2 = 0; p2 < playing.length; p2++) { sporePaintNowPlaying(playing[p2], {}); }
        var qrs = document.querySelectorAll('[data-spore-type="qr"]');
        for (var q = 0; q < qrs.length; q++) { sporePaintQr(qrs[q]); }
        var polls = document.querySelectorAll('[data-spore-type="poll"]');
        for (var pollIndex = 0; pollIndex < polls.length; pollIndex++) {
            sporeBindPoll(polls[pollIndex], sporeFeedDrives(polls[pollIndex].id, 'repaint'));
        }
        var embeds = document.querySelectorAll('[data-spore-type="embed"]');
        for (var e = 0; e < embeds.length; e++) { sporePaintEmbed(embeds[e]); }
        sporeRefreshStatCards();
        sporeKeepStatCardsFresh();
        sporeInitFeeds();
    }

    /* Stat cards re-read their source on alerts and on a timer so values stay current. */
    function sporeKeepStatCardsFresh() {
        var cards = window.__sporeStatCards || [];
        if (!cards.length || window.__sporeStatCardsLive) { return; }
        window.__sporeStatCardsLive = true;
        var usesStats = cards.some(function (c) {
            var s = String((c.source && c.source.source) || '');
            return s === 'stats' || s.indexOf('stats.') === 0;
        });
        if (usesStats && typeof sporeLoadStatsCache === 'function') { sporeLoadStatsCache(); }
        if (typeof socket !== 'undefined' && socket && socket.on) {
            ['instant_alert', 'next_alert'].forEach(function (evName) {
                socket.on(evName, function (data) {
                    window.__sporeLastPayload.event = data || {};
                    sporeRefreshStatCards();
                    if (usesStats) { setTimeout(sporeLoadStatsCache, 1500); }
                });
            });
        }
        setInterval(sporeRefreshStatCards, 1000);
        if (usesStats) { setInterval(sporeLoadStatsCache, 15000); }
    }

    /* ====================================================================
     * Unified data feeds
     *
     * Every block can carry data_feed rows. Each one is:
     *   source -> filters -> mapping/formatting -> timing -> target
     * compiled into window.__sporeFeeds by Spore Studio. The engine below is
     * the single implementation for all blocks; only the targets differ.
     * ==================================================================== */

    window.__sporeFeeds = window.__sporeFeeds || [];
    var sporeFeedStates = {};
    var sporeFeedLastValue = {};

    function sporeFeedGet(obj, path) {
        if (obj == null || path == null || path === '') { return undefined; }
        if (typeof obj === 'object' && Object.prototype.hasOwnProperty.call(obj, path)) {
            return obj[path];
        }
        var parts = String(path).split('.');
        var cur = obj;
        for (var i = 0; i < parts.length; i++) {
            if (cur == null) { return undefined; }
            cur = cur[parts[i]];
        }
        return cur;
    }

    function sporeFeedText(raw) {
        if (raw == null) { return ''; }
        /* A list of names reads as "a, b, c" rather than JSON. */
        if (Array.isArray(raw) && raw.every(function (v) { return v == null || typeof v !== 'object'; })) {
            return raw.join(', ');
        }
        if (typeof raw === 'object') {
            try { return JSON.stringify(raw); } catch (e) { return String(raw); }
        }
        return String(raw);
    }

    function sporeFeedList(text) {
        return String(text == null ? '' : text).split(/[\n,]+/).map(function (s) {
            return s.trim().toLowerCase();
        }).filter(function (s) { return s !== ''; });
    }

    function sporeFeedRule(rule, payload) {
        var raw = sporeFeedGet(payload, rule.field);
        var op = rule.op || 'eq';
        var want = rule.value == null ? '' : String(rule.value);
        var got = sporeFeedText(raw);
        var gl = got.toLowerCase();
        var wl = want.toLowerCase();
        var gn = Number(raw);
        var wn = Number(want);
        var numeric = got !== '' && want !== '' && !isNaN(gn) && !isNaN(wn);
        var isEmpty = raw == null || got === '' || (Array.isArray(raw) && raw.length === 0);
        var parts;
        switch (op) {
            case 'eq': return numeric ? gn === wn : gl === wl;
            case 'ne': return numeric ? gn !== wn : gl !== wl;
            case 'contains':
                if (Array.isArray(raw)) {
                    return raw.some(function (x) { return sporeFeedText(x).toLowerCase() === wl; });
                }
                return gl.indexOf(wl) >= 0;
            case 'not_contains':
                if (Array.isArray(raw)) {
                    return !raw.some(function (x) { return sporeFeedText(x).toLowerCase() === wl; });
                }
                return gl.indexOf(wl) < 0;
            case 'starts_with': return gl.indexOf(wl) === 0;
            case 'ends_with': return wl === '' || gl.slice(-wl.length) === wl;
            case 'regex':
                try { return new RegExp(want, 'i').test(got); } catch (e) { return false; }
            case 'gt': return !isNaN(gn) && !isNaN(wn) && got !== '' && gn > wn;
            case 'gte': return !isNaN(gn) && !isNaN(wn) && got !== '' && gn >= wn;
            case 'lt': return !isNaN(gn) && !isNaN(wn) && got !== '' && gn < wn;
            case 'lte': return !isNaN(gn) && !isNaN(wn) && got !== '' && gn <= wn;
            case 'between':
                parts = want.split(',');
                if (parts.length < 2 || got === '' || isNaN(gn)) { return false; }
                return gn >= Number(parts[0]) && gn <= Number(parts[1]);
            case 'in_list': return sporeFeedList(want).indexOf(gl) >= 0;
            case 'not_in_list': return sporeFeedList(want).indexOf(gl) < 0;
            case 'empty': return isEmpty;
            case 'not_empty': return !isEmpty;
        }
        return true;
    }

    /* Shared filter check. ``state`` carries rate-limit memory for one feed or rule. */
    function sporeFeedFilterPasses(filters, payload, state) {
        filters = filters || {};
        payload = payload || {};
        var at = filters.alert_type;
        if (at && String(at).toLowerCase() !== 'any' &&
            sporeAlertType(payload) !== String(at).toLowerCase()) { return false; }
        var tier = filters.tier;
        if (tier && String(tier) !== 'any' && !sporeTierMatches(tier, payload.tier)) { return false; }

        var rules = filters.rules || [];
        if (rules.length) {
            var anyMode = filters.match === 'any';
            var ok = anyMode ? false : true;
            for (var i = 0; i < rules.length; i++) {
                var hit = sporeFeedRule(rules[i], payload);
                if (anyMode && hit) { ok = true; break; }
                if (!anyMode && !hit) { ok = false; break; }
            }
            if (!ok) { return false; }
        }

        var who = String(payload.username != null ? payload.username : '').toLowerCase();
        var allow = sporeFeedList(filters.users_allow);
        if (allow.length && allow.indexOf(who) < 0) { return false; }
        var block = sporeFeedList(filters.users_block);
        if (block.length && block.indexOf(who) >= 0) { return false; }

        if (!state) { return true; }
        var now = Date.now();
        if (filters.skip_first > 0 && (state.seen || 0) < filters.skip_first) {
            state.seen = (state.seen || 0) + 1;
            return false;
        }
        if (filters.cooldown_ms > 0 && state.last && now - state.last < filters.cooldown_ms) {
            return false;
        }
        if (filters.max_per_minute > 0) {
            state.stamps = (state.stamps || []).filter(function (t) { return now - t < 60000; });
            if (state.stamps.length >= filters.max_per_minute) { return false; }
        }
        if (filters.dedupe_ms > 0) {
            var key = sporeFeedText(payload);
            state.recent = state.recent || {};
            if (state.recent[key] && now - state.recent[key] < filters.dedupe_ms) { return false; }
            state.recent[key] = now;
        }
        state.last = now;
        if (filters.max_per_minute > 0) { state.stamps.push(now); }
        return true;
    }

    function sporeFeedMod(value, mod) {
        var cut = mod.indexOf(':');
        var name = (cut >= 0 ? mod.slice(0, cut) : mod).trim().toLowerCase();
        var arg = cut >= 0 ? mod.slice(cut + 1) : '';
        if (Array.isArray(value)) {
            if (name === 'join') { return value.join(arg === '' ? ', ' : arg); }
            if (name === 'count') { return String(value.length); }
            value = sporeFeedText(value);
        }
        var n = Number(value);
        var isNum = value !== '' && value != null && !isNaN(n);
        var s = value == null ? '' : String(value);
        switch (name) {
            case 'number': return isNum ? n.toLocaleString('en-US') : s;
            case 'int': return isNum ? String(Math.round(n)) : s;
            case 'decimals':
                return isNum ? n.toFixed(Math.max(0, parseInt(arg, 10) || 0)) : s;
            case 'percent': return isNum ? (Math.round(n * 1000) / 10) + '%' : s;
            case 'compact':
                if (!isNum) { return s; }
                var abs = Math.abs(n);
                if (abs >= 1e9) { return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B'; }
                if (abs >= 1e6) { return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'; }
                if (abs >= 1e3) { return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K'; }
                return String(n);
            case 'duration':
                if (!isNum) { return s; }
                var total = Math.max(0, Math.floor(n));
                var m = Math.floor(total / 60);
                var sec = total % 60;
                return m + ':' + (sec < 10 ? '0' : '') + sec;
            case 'ordinal':
                if (!isNum) { return s; }
                var k = Math.abs(Math.round(n)) % 100;
                var d = k % 10;
                var suf = (k >= 11 && k <= 13) ? 'th' : (d === 1 ? 'st' : d === 2 ? 'nd' : d === 3 ? 'rd' : 'th');
                return Math.round(n) + suf;
            case 'upper': return s.toUpperCase();
            case 'lower': return s.toLowerCase();
            case 'title':
                return s.toLowerCase().replace(/(^|\s)(\S)/g, function (all, a, b) {
                    return a + b.toUpperCase();
                });
            case 'trim': return s.trim();
            case 'truncate':
                var len = parseInt(arg, 10);
                return (len > 0 && s.length > len) ? s.slice(0, Math.max(0, len - 1)) + '\u2026' : s;
            case 'default': return s === '' ? arg : s;
            case 'join': return s;
            case 'count': return s === '' ? '0' : String(s.split(',').length);
            case 'plural':
                /* {count|plural:entry,entries} -> "1 entry" / "5 entries" */
                var forms = arg.split(',');
                var one = (forms[0] || '').trim();
                var many = (forms.length > 1 ? forms[1] : one + 's').trim();
                return isNum ? (n.toLocaleString('en-US') + ' ' + (Math.abs(n) === 1 ? one : many)) : s;
        }
        return s;
    }

    function sporeFeedRender(template, data) {
        return String(template == null ? '' : template).replace(
            /\{([^{}|]+)((?:\|[^{}|]*)*)\}/g,
            function (all, field, mods) {
                var raw = sporeFeedGet(data, field.trim());
                var value = raw == null ? '' : (typeof raw === 'object' && !Array.isArray(raw) ? sporeFeedText(raw) : raw);
                if (mods) {
                    mods.split('|').slice(1).forEach(function (mod) {
                        if (mod !== '') { value = sporeFeedMod(value, mod); }
                    });
                }
                if (Array.isArray(value)) { value = sporeFeedText(value); }
                return value == null ? '' : String(value);
            }
        );
    }

    function sporeFeedFirst(obj, keys) {
        for (var i = 0; i < keys.length; i++) {
            if (obj[keys[i]] != null && obj[keys[i]] !== '') { return obj[keys[i]]; }
        }
        return '';
    }

    /* Turn one payload into the mapped item every target receives. */
    function sporeFeedItem(feed, payload, valueKind) {
        payload = payload || {};
        var m = feed.mapping || {};
        var item = {};
        Object.keys(payload).forEach(function (k) { item[k] = payload[k]; });

        var who = m.text_field ? sporeFeedGet(payload, m.text_field)
            : sporeFeedFirst(payload, ['username', 'message', 'label', 'result', 'text']);
        var val = m.value_field ? sporeFeedGet(payload, m.value_field)
            : sporeFeedFirst(payload, ['value', 'amount', 'amt_cheered', 'gift_qty', 'quantity', 'total', 'count']);
        if (Array.isArray(who)) { who = who.join(', '); }
        item.username = who == null ? '' : who;
        item.value = val == null ? '' : val;

        var dec = m.decimals;
        if (dec !== '' && dec != null && item.value !== '' && !isNaN(Number(item.value))) {
            item.value = Number(item.value).toFixed(Math.max(0, parseInt(dec, 10) || 0));
        }

        var text;
        if (m.template) {
            text = sporeFeedRender(m.template, item);
        } else if (valueKind) {
            text = item.value === '' ? '' : String(item.value);
        } else {
            text = item.username !== '' ? String(item.username) : (item.value === '' ? '' : String(item.value));
        }
        if (text === '' && m.fallback) { text = String(m.fallback); }
        if (text !== '') { text = (m.prefix || '') + text + (m.suffix || ''); }
        item.text = text;
        return item;
    }

    function sporeFeedElement(feed) {
        return document.getElementById(feed.elementId);
    }

    function sporeFeedSetMedia(el, url) {
        if (!el || !url) { return; }
        var s = String(url);
        var tag = el.tagName;
        if (tag === 'IMG') { el.setAttribute('src', s); return; }
        if (tag === 'VIDEO' || tag === 'AUDIO') {
            var own = el.querySelector('source');
            if (own) { own.setAttribute('src', s); } else { el.setAttribute('src', s); }
            try { el.load(); } catch (e) {}
            return;
        }
        var img = el.querySelector('img');
        if (img) { img.setAttribute('src', s); }
        var media = el.querySelector('video, audio');
        if (media) {
            var srcNode = media.querySelector('source');
            if (srcNode) { srcNode.setAttribute('src', s); } else { media.setAttribute('src', s); }
            try { media.load(); } catch (e2) {}
        }
    }

    function sporeFeedShow(el) {
        if (typeof sporeShow === 'function') { sporeShow(el.id, null); return; }
        el.removeAttribute('data-spore-hidden');
        el.style.display = '';
    }

    function sporeFeedHide(el) {
        if (typeof sporeHide === 'function') { sporeHide(el.id, null); return; }
        el.setAttribute('data-spore-hidden', 'true');
        el.style.display = 'none';
    }

    function sporeFeedProgressOverride(feed, key, value) {
        var bars = window.__sporeProgressBars || [];
        for (var i = 0; i < bars.length; i++) {
            if (bars[i].elementId === feed.elementId) {
                bars[i][key] = value;
                if (typeof sporeUpdateProgressBars === 'function') { sporeUpdateProgressBars(null); }
                return;
            }
        }
        /* A bar with no counter binding: register one so the override has somewhere to live. */
        var spec = { elementId: feed.elementId, counter_id: '', max_kind: 'fixed', max: 100,
            near_goal_threshold: 90, near_goal_effect: 'none' };
        spec[key] = value;
        window.__sporeProgressBars = bars.concat([spec]);
        if (typeof sporeUpdateProgressBars === 'function') { sporeUpdateProgressBars(null); }
    }

    function sporeFeedCounterId(el) {
        var metas = window.__sporeCounterMeta || {};
        var keys = Object.keys(metas);
        for (var i = 0; i < keys.length; i++) {
            if (metas[keys[i]] && metas[keys[i]].elementId === el.id) { return keys[i]; }
        }
        return el.id;
    }

    function sporeFeedCounterOp(el, op, item, raw, opt) {
        if (typeof sporeCounterAdjust !== 'function' || !el) { return; }
        var delta = opt && opt.delta && typeof opt.delta === 'object' ? opt.delta : null;
        if (!delta) {
            var amount = item && item.value !== '' && item.value != null
                ? sporeCoerceNumber(item.value, 1) : 1;
            delta = { kind: 'fixed', value: amount };
        }
        window.__sporeLastPayload.event = raw || window.__sporeLastPayload.event || {};
        sporeCounterAdjust(sporeFeedCounterId(el), op, delta, raw || {});
    }

    var sporeFeedTargets = {
        list_add: function (feed, el, item) {
            if (item.text !== '') { sporeListPush(el, item.text); }
        },
        list_clear: function (feed, el) { sporeListClear(el.id); },
        confetti_burst: function (feed, el) { sporeConfettiBurst(el); },
        list_replace: function (feed, el, item, raw, opt) {
            var entries = sporeFeedGet(raw, (opt && opt.items_field) || 'pool_entries');
            sporeListReplace(el, Array.isArray(entries) ? entries : []);
        },
        wheel_spin: function (feed, el, item, raw, opt) {
            if (opt.segments_field) {
                var names = sporeFeedGet(raw, opt.segments_field);
                if (Array.isArray(names) && names.length) {
                    var colors = ['#a855f7', '#22c55e', '#f59e0b', '#38bdf8', '#f43f5e', '#eab308'];
                    var segs = names.map(function (name, n) {
                        return { label: sporeFeedText(name), color: colors[n % colors.length] };
                    });
                    el.setAttribute('data-spore-wheel-segments', JSON.stringify(segs));
                    sporePaintWheel(el);
                }
            }
            var land = opt.land_field ? sporeFeedGet(raw, opt.land_field) : undefined;
            if (Array.isArray(land)) { land = land[0]; }
            sporeWheelSpin(el.id, land != null && land !== '' ? { label: sporeFeedText(land) } : {});
        },
        wheel_add_segment: function (feed, el, item, raw, opt) {
            if (item.text === '') { return; }
            var segs = sporeWheelSegments(el);
            var fallback = ['#a855f7', '#22c55e', '#f59e0b', '#38bdf8', '#f43f5e', '#eab308'];
            segs.push({
                label: item.text,
                color: opt.color || fallback[segs.length % fallback.length]
            });
            el.setAttribute('data-spore-wheel-segments', JSON.stringify(segs));
            sporePaintWheel(el);
        },
        set_value: function (feed, el, item, raw) {
            var type = el.getAttribute('data-spore-type');
            if (type === 'stat_card') {
                sporeApplyStatCard(el, item.value, feed.mapping && feed.mapping.template ? item.text : '');
            } else {
                sporeFeedProgressOverride(feed, 'override_value', sporeCoerceNumber(item.value, 0));
            }
        },
        set_max: function (feed, el, item) {
            sporeFeedProgressOverride(feed, 'override_max', sporeCoerceNumber(item.value, 100));
        },
        counter_increment: function (feed, el, item, raw, opt) { sporeFeedCounterOp(el, 'increment', item, raw, opt); },
        counter_decrement: function (feed, el, item, raw, opt) { sporeFeedCounterOp(el, 'decrement', item, raw, opt); },
        counter_set: function (feed, el, item, raw, opt) { sporeFeedCounterOp(el, 'set', item, raw, opt); },
        counter_reset: function (feed, el, item, raw, opt) { sporeFeedCounterOp(el, 'reset', item, raw, opt); },
        set_text: function (feed, el, item, raw) {
            var displays = window.__sporeDataDisplays || [];
            for (var i = 0; i < displays.length; i++) {
                if (displays[i].elementId === el.id && displays[i].feed) {
                    sporeApplyDataDisplay(i, item.value === '' ? null : item.value,
                        feed.mapping && feed.mapping.template ? item.text : '');
                    return;
                }
            }
            if (typeof sporeSetText === 'function') { sporeSetText(el.id, item.text); }
            else { el.textContent = item.text; }
        },
        timer_start: function (feed, el) { if (typeof sporeTimerStart === 'function') { sporeTimerStart(el.id); } },
        timer_pause: function (feed, el) { if (typeof sporeTimerPause === 'function') { sporeTimerPause(el.id); } },
        timer_reset: function (feed, el) { if (typeof sporeTimerReset === 'function') { sporeTimerReset(el.id); } },
        timer_set: function (feed, el, item) {
            var spec = window.__sporeTimers && window.__sporeTimers[el.id];
            if (!spec) { return; }
            var seconds = sporeCoerceNumber(item.value, 0);
            spec.elapsed = seconds;
            spec.elapsed_base = seconds;
            spec.started_at = spec.running ? Date.now() : null;
            sporeTimerRender(el.id);
        },
        set_src: function (feed, el, item) { sporeFeedSetMedia(el, item.text); },
        play_media: function (feed, el, item, raw, opt) {
            if (typeof sporePlayMediaAudio !== 'function') { return; }
            var over = {};
            if (opt.volume !== '' && opt.volume != null) { over.volume = opt.volume; }
            sporePlayMediaAudio(el.id, over);
        },
        set_qr_text: function (feed, el, item) {
            el.setAttribute('data-spore-qr-text', item.text);
            sporePaintQr(el);
        },
        set_embed_url: function (feed, el, item) {
            if (item.text === '') { return; }
            el.setAttribute('data-spore-embed-url', item.text);
            var frame = el.querySelector('.spore-embed-frame');
            if (frame) { frame.removeAttribute('src'); }
            sporePaintEmbed(el);
        },
        repaint: function (feed, el, item, raw) {
            var type = el.getAttribute('data-spore-type');
            if (type === 'poll') {
                /* Every overlay hears every poll; only paint this template's. */
                if (raw && raw.template && sporePollTemplateName() && !sporePollIsMine(raw)) { return; }
                sporePaintPoll(el, raw || {});
            }
            else if (type === 'now_playing') { sporePaintNowPlaying(el, raw || {}); }
        },
        show: function (feed, el) { sporeFeedShow(el); },
        hide: function (feed, el) { sporeFeedHide(el); },
        toggle: function (feed, el) {
            if (el.hasAttribute('data-spore-hidden')) { sporeFeedShow(el); } else { sporeFeedHide(el); }
        },
        show_for: function (feed, el, item, raw, opt) {
            sporeFeedShow(el);
            var secs = sporeCoerceNumber(opt.seconds, 5);
            if (el._sporeFeedHideTimer) { clearTimeout(el._sporeFeedHideTimer); }
            el._sporeFeedHideTimer = setTimeout(function () { sporeFeedHide(el); }, Math.max(0, secs) * 1000);
        },
        flash_class: function (feed, el, item, raw, opt) {
            var cls = String(opt.class_name || 'sporePop').replace(/[^A-Za-z0-9_-]/g, '');
            if (!cls) { return; }
            el.classList.remove(cls);
            void el.offsetWidth;
            el.classList.add(cls);
            var hold = sporeCoerceNumber(opt.duration_ms, 420);
            setTimeout(function () { el.classList.remove(cls); }, Math.max(0, hold));
        },
        set_style: function (feed, el, item, raw, opt) {
            var v = (opt.fixed !== '' && opt.fixed != null) ? String(opt.fixed) : item.text;
            if (v === '') { return; }
            var prop = opt.property || 'background_color';
            if (prop === 'background_color') { el.style.background = v; }
            else if (prop === 'color') { el.style.color = v; }
            else if (prop === 'border_color') { el.style.borderColor = v; }
            else if (prop === 'opacity') { el.style.opacity = v; }
            else if (prop === 'font_size') { el.style.fontSize = (isNaN(Number(v)) ? v : v + 'px'); }
        }
    };

    function sporeFeedStateFor(feed) {
        var key = feed.elementId + '::' + feed.id;
        if (!sporeFeedStates[key]) { sporeFeedStates[key] = {}; }
        return sporeFeedStates[key];
    }

    function sporeFeedApply(feed, payload, valueKind) {
        var el = sporeFeedElement(feed);
        if (!el) { return; }
        var target = feed.target || {};
        var handler = sporeFeedTargets[target.id];
        if (!handler) { return; }
        var item = sporeFeedItem(feed, payload, valueKind);
        try {
            handler(feed, el, item, payload, target.options || {});
        } catch (err) {
            if (typeof console !== 'undefined') { console.error('[spore-studio] feed target error', err); }
        }
    }

    /* Run one incoming payload through filters, fan-out, timing, and the target. */
    function sporeFeedFire(feed, payload, valueKind) {
        if (!feed.enabled) { return; }
        var filters = feed.filters || {};
        var passes;
        if (valueKind) {
            passes = sporeFeedFilterPasses({ rules: filters.rules, match: filters.match },
                (payload && typeof payload === 'object') ? payload : { value: payload }, null);
        } else {
            passes = sporeFeedFilterPasses(filters, payload, sporeFeedStateFor(feed));
        }
        if (!passes) { return; }

        var rows = [payload];
        var split = feed.mapping && feed.mapping.split_field;
        if (split && !valueKind) {
            var list = sporeFeedGet(payload, split);
            if (Array.isArray(list)) {
                rows = list.map(function (entry) {
                    var base = Object.assign({}, payload);
                    if (entry && typeof entry === 'object') { Object.assign(base, entry); }
                    else { base.username = String(entry); base.item = entry; }
                    return base;
                });
            }
        }
        var delay = sporeCoerceNumber(feed.timing && feed.timing.delay_ms, 0);
        rows.forEach(function (row) {
            var run = function () {
                sporeFeedApply(feed, row, valueKind);
                if (typeof sporeApplyTransforms === 'function') { sporeApplyTransforms(); }
            };
            if (delay > 0) { setTimeout(run, delay); } else { run(); }
        });
    }

    function sporeFeedRefreshValue(feed) {
        var spec = (feed.source && feed.source.value) || {};
        var last = window.__sporeLastPayload || {};
        var value = sporeResolveSource(spec, last.event || {}, last.ctx || {});
        var key = feed.elementId + '::' + feed.id;
        var enc = sporeFeedText(value);
        var target = feed.target && feed.target.id;
        if (sporeFeedLastValue[key] === enc && target !== 'timer_start') { return; }
        sporeFeedLastValue[key] = enc;
        var payload = Object.assign({}, last.event || {}, { value: value });
        sporeFeedFire(feed, payload, true);
    }

    function sporeFeedRefreshAllValues() {
        (window.__sporeFeeds || []).forEach(function (feed) {
            if (feed.source && feed.source.kind === 'value' && feed.enabled) {
                sporeFeedRefreshValue(feed);
            }
        });
    }

    function sporeFeedBind(feed) {
        var src = feed.source || {};
        var hasSocket = typeof socket !== 'undefined' && socket && socket.on;
        if (src.kind === 'event') {
            if (!hasSocket) { return; }
            (src.events || []).forEach(function (evName) {
                socket.on(evName, function (data) {
                    data = (data && typeof data === 'object') ? data : { value: data };
                    if (sporeIsReplay(data)) { return; }
                    window.__sporeLastPayload.event = data;
                    sporeFeedFire(feed, data, false);
                });
            });
        } else if (src.kind === 'template_output') {
            if (!hasSocket) { return; }
            var want = src.template_output || {};
            socket.on('template_output', function (env) {
                env = env || {};
                if (want.event && env.event !== want.event) { return; }
                if (want.template && env.template && env.template !== want.template) { return; }
                var data = (env.data && typeof env.data === 'object') ? env.data : {};
                var match = want.match || {};
                var keys = Object.keys(match);
                for (var i = 0; i < keys.length; i++) {
                    if (String(data[keys[i]]) !== String(match[keys[i]])) { return; }
                }
                window.__sporeLastPayload.event = data;
                sporeFeedFire(feed, data, false);
            });
        } else if (src.kind === 'value') {
            if (hasSocket) {
                (src.refresh_on || []).forEach(function (evName) {
                    socket.on(evName, function (data) {
                        window.__sporeLastPayload.event = data || {};
                        sporeFeedRefreshValue(feed);
                    });
                });
            }
            var ms = sporeCoerceNumber(src.refresh_ms, 0);
            if (ms > 0) { setInterval(function () { sporeFeedRefreshValue(feed); }, Math.max(100, ms)); }
            sporeFeedRefreshValue(feed);
        }
    }

    function sporeInitFeeds() {
        if (window.__sporeFeedsBound) { return; }
        window.__sporeFeedsBound = true;
        var feeds = window.__sporeFeeds || [];
        var usesStats = feeds.some(function (f) {
            var s = String((f.source && f.source.value && f.source.value.source) || '');
            return f.source && f.source.kind === 'value' && (s === 'stats' || s.indexOf('stats.') === 0);
        });
        if (usesStats && typeof sporeLoadStatsCache === 'function') {
            sporeLoadStatsCache();
            setInterval(sporeLoadStatsCache, 15000);
        }
        feeds.forEach(sporeFeedBind);
        sporeRequestFeedState(feeds);
    }

    /* Feeds only hear events as they happen. For sources that can replay their current
       state, ask once on load (and after a reconnect) so a refreshed overlay is not blank. */
    var SPORE_STATE_REQUESTS = { giveaway_: 'giveaway_request_state' };

    function sporeRequestFeedState(feeds) {
        if (typeof socket === 'undefined' || !socket || !socket.emit) { return; }
        var wanted = {};
        feeds.forEach(function (feed) {
            if (!feed || !feed.enabled || !feed.source || feed.source.kind !== 'event') { return; }
            (feed.source.events || []).forEach(function (ev) {
                Object.keys(SPORE_STATE_REQUESTS).forEach(function (prefix) {
                    if (String(ev).indexOf(prefix) === 0) { wanted[SPORE_STATE_REQUESTS[prefix]] = true; }
                });
            });
        });
        Object.keys(wanted).forEach(function (request) {
            var ask = function () { socket.emit(request, {}); };
            ask();
            if (socket.on) { socket.on('connect', ask); }
        });
    }

    function sporeTransformOriginCss(origin) {
        var map = {
            center: 'center center',
            'top left': 'left top',
            'top center': 'center top',
            'top right': 'right top',
            'center left': 'left center',
            'center right': 'right center',
            'bottom left': 'left bottom',
            'bottom center': 'center bottom',
            'bottom right': 'right bottom'
        };
        return map[origin] || 'center center';
    }

    function sporeMapTransform(value, rule) {
        var n = sporeCoerceNumber(value, rule.in_min);
        var inMin = sporeCoerceNumber(rule.in_min, 0);
        var inMax = sporeCoerceNumber(rule.in_max, 100);
        var outMin = sporeCoerceNumber(rule.out_min, 0);
        var outMax = sporeCoerceNumber(rule.out_max, 1);
        var t = (inMax === inMin) ? 0 : (n - inMin) / (inMax - inMin);
        if (t < 0) { t = 0; }
        if (t > 1) { t = 1; }
        return outMin + t * (outMax - outMin);
    }

    function sporeFirstNumber(data, preferredKey) {
        if (typeof data === 'number' && !isNaN(data)) { return data; }
        if (data == null || typeof data !== 'object') {
            var direct = Number(data);
            return isNaN(direct) ? null : direct;
        }
        var keys = [];
        if (preferredKey) { keys.push(preferredKey); }
        ['value', 'amount', 'count', 'delta', 'goal', 'max'].forEach(function (key) {
            if (keys.indexOf(key) < 0) { keys.push(key); }
        });
        var i;
        for (i = 0; i < keys.length; i++) {
            if (data[keys[i]] == null || data[keys[i]] === '') { continue; }
            var num = Number(data[keys[i]]);
            if (!isNaN(num)) { return num; }
        }
        return null;
    }

    function sporeResolveTransformInput(rule) {
        var src = rule.source || {};
        if (src.kind === 'event' || src.kind === 'template_output') {
            if (rule._lastNumber == null) {
                var remembered = src.value && src.value.fallback;
                return remembered != null && remembered !== '' ? remembered : rule.in_min;
            }
            return rule._lastNumber;
        }
        var spec = src.value || {};
        var last = window.__sporeLastPayload || {};
        var resolved = sporeResolveSource(spec, last.event || {}, last.ctx || {});
        if (resolved == null || resolved === '') {
            if (spec.fallback != null && spec.fallback !== '') { return spec.fallback; }
            return rule.in_min;
        }
        return resolved;
    }

    function sporeComposeTransform(rules) {
        var acc = {
            translate_x: 0,
            translate_y: 0,
            rotate: 0,
            skew_x: 0,
            skew_y: 0,
            scale: 1,
            origin: 'center'
        };
        var any = false;
        (rules || []).forEach(function (rule) {
            if (!rule || rule.enabled === false) { return; }
            var prop = rule.property || 'scale';
            if (!Object.prototype.hasOwnProperty.call(acc, prop)) { return; }
            acc[prop] = sporeMapTransform(sporeResolveTransformInput(rule), rule);
            if (rule.origin) { acc.origin = rule.origin; }
            any = true;
        });
        if (!any) { return null; }
        var parts = [];
        if (acc.translate_x || acc.translate_y) {
            parts.push('translate(' + acc.translate_x + 'px, ' + acc.translate_y + 'px)');
        }
        if (acc.rotate) { parts.push('rotate(' + acc.rotate + 'deg)'); }
        if (acc.skew_x || acc.skew_y) {
            parts.push('skew(' + acc.skew_x + 'deg, ' + acc.skew_y + 'deg)');
        }
        if (acc.scale !== 1) { parts.push('scale(' + acc.scale + ')'); }
        return {
            transform: parts.join(' '),
            origin: sporeTransformOriginCss(acc.origin)
        };
    }

    function sporeWriteTransform(el, composed) {
        if (!el) { return; }
        if (!composed) {
            if (el.getAttribute('data-spore-transform-owned') === '1') {
                el.style.transform = '';
                el.style.transformOrigin = '';
                el.removeAttribute('data-spore-transform-owned');
            }
            return;
        }
        el.setAttribute('data-spore-transform-owned', '1');
        el.style.transform = composed.transform;
        el.style.transformOrigin = composed.origin;
    }

    function sporeApplyTransforms() {
        var table = window.__sporeTransforms;
        if (!table) { return; }
        sporeWriteTransform(
            document.getElementById('sporeRoot'),
            sporeComposeTransform(table.canvas || [])
        );
        var elements = table.elements || {};
        Object.keys(elements).forEach(function (id) {
            sporeWriteTransform(document.getElementById(id), sporeComposeTransform(elements[id]));
        });
    }

    function sporeTransformUsesStats(rule) {
        var src = (rule && rule.source) || {};
        var spec = src.value || {};
        var sid = String(spec.source || '');
        return src.kind === 'value' && (sid === 'stats' || sid.indexOf('stats.') === 0);
    }

    function sporeBindTransformRule(rule) {
        var src = rule.source || {};
        var hasSocket = typeof socket !== 'undefined' && socket && socket.on;
        if (!hasSocket) { return; }
        if (src.kind === 'event') {
            (src.events || []).forEach(function (evName) {
                socket.on(evName, function (data) {
                    data = (data && typeof data === 'object') ? data : { value: data };
                    if (sporeIsReplay(data)) { return; }
                    var num = sporeFirstNumber(data, rule.value_field);
                    if (num != null) { rule._lastNumber = num; }
                    window.__sporeLastPayload.event = data;
                    sporeApplyTransforms();
                });
            });
            return;
        }
        if (src.kind === 'template_output') {
            var want = src.template_output || {};
            socket.on('template_output', function (env) {
                env = env || {};
                if (want.event && env.event !== want.event) { return; }
                if (want.template && env.template && env.template !== want.template) { return; }
                var data = (env.data && typeof env.data === 'object') ? env.data : {};
                var match = want.match || {};
                var keys = Object.keys(match);
                var i;
                for (i = 0; i < keys.length; i++) {
                    if (String(data[keys[i]]) !== String(match[keys[i]])) { return; }
                }
                var num = sporeFirstNumber(data, rule.value_field);
                if (num != null) { rule._lastNumber = num; }
                window.__sporeLastPayload.event = data;
                sporeApplyTransforms();
            });
            return;
        }
        (src.refresh_on || []).forEach(function (evName) {
            socket.on(evName, function (data) {
                window.__sporeLastPayload.event = data || {};
                sporeApplyTransforms();
            });
        });
        var ms = sporeCoerceNumber(src.refresh_ms, 0);
        if (ms > 0) {
            setInterval(function () { sporeApplyTransforms(); }, Math.max(100, ms));
        }
    }

    function sporeInitTransforms() {
        if (window.__sporeTransformsBound) { return; }
        window.__sporeTransformsBound = true;
        var table = window.__sporeTransforms || { canvas: [], elements: {} };
        var rules = (table.canvas || []).slice();
        Object.keys(table.elements || {}).forEach(function (id) {
            (table.elements[id] || []).forEach(function (rule) { rules.push(rule); });
        });
        var usesStats = rules.some(sporeTransformUsesStats);
        if (usesStats && typeof sporeLoadStatsCache === 'function') {
            sporeLoadStatsCache();
            if (!window.__sporeStatsPoll) {
                window.__sporeStatsPoll = setInterval(sporeLoadStatsCache, 15000);
            }
        }
        rules.forEach(sporeBindTransformRule);
        sporeApplyTransforms();
    }

    window.sporeListAdd = sporeListAdd;
    window.sporeListClear = sporeListClear;
    window.sporeWheelSpin = sporeWheelSpin;
    window.sporeRefreshStatCards = sporeRefreshStatCards;
    window.sporeInitWidgets = sporeInitWidgets;
    window.sporeInitFeeds = sporeInitFeeds;
    window.sporeApplyTransforms = sporeApplyTransforms;
    window.sporeInitTransforms = sporeInitTransforms;
    window.sporeFeedFilterPasses = sporeFeedFilterPasses;
    window.sporeFeedRender = sporeFeedRender;
    window.sporeFeedItem = sporeFeedItem;
    window.sporeFeedFire = sporeFeedFire;
    window.sporeEmitOutput = sporeEmitOutput;
    window.sporePaintQr = sporePaintQr;

    window.sporePatchSetText = sporePatchSetText;
    window.sporeInitClocks = sporeInitClocks;
    window.sporeInitTimers = sporeInitTimers;
    window.sporeTimerStart = sporeTimerStart;
    window.sporeTimerPause = sporeTimerPause;
    window.sporeTimerReset = sporeTimerReset;
})();

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
        if (meta.min != null && val < meta.min) { val = meta.min; }
        if (meta.max != null && val > meta.max) { val = meta.max; }
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
                { min: meta.min, max: meta.max }
            );
        } else {
            var text = sporeFormatCounterDisplay(val, fmt, meta.min, meta.max);
            if (meta.elementId && typeof sporeSetText === 'function') {
                sporeSetText(meta.elementId, text);
            }
            if (!skipAnim && anim && anim.enabled && meta.elementId) {
                sporePlayValueAnimation(meta.elementId, anim);
            }
            afterText();
        }
        window.__sporeCounterLastRendered[counterId] = val;
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

    function sporeCounterAdjust(counterId, operation, deltaSpec, payload) {
        var meta = window.__sporeCounterMeta[counterId];
        if (!meta) { return; }
        window.__sporeLastPayload.event = payload || {};
        var val = sporeCoerceNumber(window.__sporeCounters[counterId], meta.initial_value || 0);
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
        if (meta.min != null) { val = Math.max(meta.min, val); }
        if (meta.max != null) { val = Math.min(meta.max, val); }
        window.__sporeCounters[counterId] = val;
        sporeCounterRender(counterId);
        sporeCounterPersist(counterId);
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
            var max = sporeResolveProgressMax(spec);
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
        var payload = window.__sporeLastPayload.event || {};
        var ctx = window.__sporeLastPayload.ctx || {};
        var raw = sporeResolveSource(spec.source, payload, ctx);
        var text;
        if (raw == null || raw === '') {
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

    function sporeSetTextContent(el, value) {
        if (!el) { return; }
        var t = (value == null ? '' : String(value));
        if (el.classList && el.classList.contains('spore-marquee')) {
            var nodes = el.querySelectorAll('.spore-marquee-text');
            for (var i = 0; i < nodes.length; i++) {
                nodes[i].textContent = t;
            }
            return;
        }
        el.textContent = t;
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

    function sporeListPush(el, text) {
        var rows = sporeListRows(el);
        if (!rows) { return; }
        var max = parseInt(sporeAttr(el, 'data-spore-list-max', '8'), 10) || 8;
        var newest = sporeAttr(el, 'data-spore-list-newest', 'top');
        var gap = parseInt(sporeAttr(el, 'data-spore-list-gap', '4'), 10) || 0;
        var addAnim = sporeAttr(el, 'data-spore-list-add', 'fade');
        var row = document.createElement('div');
        row.className = 'spore-list-row';
        if (addAnim === 'slide') { row.classList.add('spore-list-in-slide'); }
        else if (addAnim !== 'none') { row.classList.add('spore-list-in-fade'); }
        row.style.marginBottom = gap + 'px';
        var rowBg = sporeAttr(el, 'data-spore-list-row-bg', '');
        var rowPad = parseFloat(sporeAttr(el, 'data-spore-list-row-pad', '0')) || 0;
        var rowRadius = parseFloat(sporeAttr(el, 'data-spore-list-row-radius', '0')) || 0;
        if (rowBg) { row.style.background = rowBg; }
        if (rowPad > 0) { row.style.padding = rowPad + 'px'; }
        if (rowRadius > 0) { row.style.borderRadius = rowRadius + 'px'; }
        row.textContent = text;
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

    function sporePaintWheel(el) {
        var rotor = el.querySelector('.spore-wheel-rotor');
        if (!rotor) { return; }
        var segments = sporeWheelSegments(el);
        rotor.style.background = 'none';
        rotor.innerHTML = sporeWheelSvg(segments);
        var pointer = el.querySelector('.spore-wheel-pointer');
        if (pointer) {
            var pos = sporeAttr(el, 'data-spore-wheel-pointer', 'top');
            pointer.style.top = 'auto';
            pointer.style.bottom = 'auto';
            pointer.style.left = '50%';
            pointer.style.right = 'auto';
            pointer.style.transform = '';
            if (pos === 'bottom') {
                pointer.style.bottom = '4px';
                pointer.style.transform = 'translateX(-8px) rotate(180deg)';
            } else if (pos === 'left') {
                pointer.style.top = '50%';
                pointer.style.left = '4px';
                pointer.style.transform = 'rotate(-90deg)';
            } else if (pos === 'right') {
                pointer.style.top = '50%';
                pointer.style.left = 'auto';
                pointer.style.right = '4px';
                pointer.style.transform = 'rotate(90deg)';
            } else {
                pointer.style.top = '4px';
            }
        }
    }

    function sporeWheelSpin(id, payload) {
        var el = document.getElementById(id);
        if (!el) { return; }
        var segments = sporeWheelSegments(el);
        var idx = Math.floor(Math.random() * segments.length);
        if (payload && payload.label) {
            for (var i = 0; i < segments.length; i++) {
                if (String(segments[i].label) === String(payload.label)) { idx = i; break; }
            }
        }
        sporePaintWheel(el);
        var rotor = el.querySelector('.spore-wheel-rotor');
        var slice = 360 / segments.length;
        var center = idx * slice + slice / 2;
        var current = parseFloat(el.getAttribute('data-spore-wheel-rot') || '0') || 0;
        var landing = (360 - center);
        var next = current + 360 * 5 + ((landing - (current % 360) + 360) % 360);
        if (next <= current) { next += 360; }
        var ms = parseInt(sporeAttr(el, 'data-spore-wheel-ms', '4000'), 10) || 4000;
        if (rotor) {
            rotor.style.transition = 'transform ' + ms + 'ms cubic-bezier(.15,.7,.2,1)';
            rotor.style.transform = 'rotate(' + next + 'deg)';
        }
        el.setAttribute('data-spore-wheel-rot', String(next));
        setTimeout(function () {
            var winner = segments[idx] || {};
            window.__sporeLastPayload.wheel = {
                elementId: id,
                index: idx,
                label: winner.label || ''
            };
            var label = el.querySelector('.spore-wheel-label');
            if (label) { label.textContent = winner.label || ''; }
            if (typeof socket !== 'undefined' && socket && socket.emit) {
                socket.emit('wheel_result', window.__sporeLastPayload.wheel);
            }
        }, ms);
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
                valueEl.textContent = (value == null || value === '')
                    ? '\u2014'
                    : sporeFormatDisplay(value, fmt);
                valueEl.style.color = sporeAttr(el, 'data-spore-stat-value-color', '#ffffff');
            }
            if (iconEl) { iconEl.textContent = icon; }
        }
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

    function sporeInitWidgets() {
        var lists = document.querySelectorAll('[data-spore-type="list"]');
        for (var i = 0; i < lists.length; i++) { sporeBindList(lists[i]); }
        var wheels = document.querySelectorAll('[data-spore-type="wheel"]');
        for (var w = 0; w < wheels.length; w++) {
            sporePaintWheel(wheels[w]);
            if (sporeAttr(wheels[w], 'data-spore-wheel-source', 'static') === 'giveaway' &&
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
        if (playing.length && typeof socket !== 'undefined' && socket && socket.on) {
            socket.on('spotify_data_update', function (data) {
                for (var n = 0; n < playing.length; n++) { sporePaintNowPlaying(playing[n], data); }
            });
        }
        for (var p = 0; p < playing.length; p++) { sporePaintNowPlaying(playing[p], {}); }
        var qrs = document.querySelectorAll('[data-spore-type="qr"]');
        for (var q = 0; q < qrs.length; q++) { sporePaintQr(qrs[q]); }
        var embeds = document.querySelectorAll('[data-spore-type="embed"]');
        for (var e = 0; e < embeds.length; e++) { sporePaintEmbed(embeds[e]); }
        sporeRefreshStatCards();
        sporeKeepStatCardsFresh();
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

    window.sporeListAdd = sporeListAdd;
    window.sporeListClear = sporeListClear;
    window.sporeWheelSpin = sporeWheelSpin;
    window.sporeRefreshStatCards = sporeRefreshStatCards;
    window.sporeInitWidgets = sporeInitWidgets;
    window.sporePaintQr = sporePaintQr;

    window.sporePatchSetText = sporePatchSetText;
    window.sporeInitClocks = sporeInitClocks;
    window.sporeInitTimers = sporeInitTimers;
    window.sporeTimerStart = sporeTimerStart;
    window.sporeTimerPause = sporeTimerPause;
    window.sporeTimerReset = sporeTimerReset;
})();

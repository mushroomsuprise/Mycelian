/* Spore Studio shared data/counter/control runtime (injected into overlays). */
(function () {
    if (typeof window === 'undefined') { return; }
    if (window.__sporeDataRuntimeLoaded) { return; }
    window.__sporeDataRuntimeLoaded = true;

    window.__sporeCounters = window.__sporeCounters || {};
    window.__sporeCounterMeta = window.__sporeCounterMeta || {};
    window.__sporeDataDisplays = window.__sporeDataDisplays || [];
    window.__sporeTwitchCache = window.__sporeTwitchCache || {};
    window.__sporeConfigCache = window.__sporeConfigCache || {};
    window.__sporeLastPayload = window.__sporeLastPayload || {};

    function sporeFormatDisplay(value, format) {
        var fmt = (format == null || format === '') ? '{value}' : String(format);
        var v = (value == null) ? '' : String(value);
        return fmt.split('{value}').join(v);
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
                { kind: 'data_source', source: spec.source },
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

    function sporeResolveSource(spec, payload, ctx) {
        spec = spec || {};
        var kind = spec.kind || 'data_source';
        if (kind === 'fixed') {
            return spec.value;
        }
        if (kind === 'random_int' || kind === 'random_float') {
            return sporeRandomDelta(spec);
        }
        var sourceId = spec.source || '';
        if (!sourceId && kind === 'data_source') { return spec.fallback; }

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
        function finish(data) {
            if (done) { return; }
            done = true;
            try { socket.off('get_data', onResp); } catch (e) {}
            if (cb) { cb(data); }
        }
        function onResp(res) {
            if (!res || res.error) { finish(null); return; }
            finish(res.data != null ? res.data : res);
        }
        socket.once('get_data', onResp);
        socket.emit('get_data', { path: path });
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

    function sporeCounterRender(counterId) {
        var meta = window.__sporeCounterMeta[counterId];
        if (!meta) { return; }
        var val = window.__sporeCounters[counterId];
        if (val == null) { val = meta.initial_value || 0; }
        if (meta.min != null && val < meta.min) { val = meta.min; }
        if (meta.max != null && val > meta.max) { val = meta.max; }
        window.__sporeCounters[counterId] = val;
        var text = sporeFormatDisplay(val, meta.format || '{value}');
        if (meta.elementId && typeof sporeSetText === 'function') {
            sporeSetText(meta.elementId, text);
        }
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
            sporeCounterRender(counterId);
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
            sporeCounterRender(counterId);
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
        if (spec.elementId && typeof sporeSetText === 'function') {
            sporeSetText(spec.elementId, text);
        }
    }

    function sporeRefreshAllDataDisplays() {
        for (var i = 0; i < window.__sporeDataDisplays.length; i++) {
            sporeRefreshDataDisplay(i);
        }
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
    window.sporeResolveSource = sporeResolveSource;
    window.sporeRandomDelta = sporeRandomDelta;
    window.sporeCounterAdjust = sporeCounterAdjust;
    window.sporeCounterHydrate = sporeCounterHydrate;
    window.sporeCounterRender = sporeCounterRender;
    window.sporeRefreshDataDisplay = sporeRefreshDataDisplay;
    window.sporeRefreshAllDataDisplays = sporeRefreshAllDataDisplays;
    window.sporeLoadStatsCache = sporeLoadStatsCache;
    window.sporeDispatchControlAction = sporeDispatchControlAction;
})();

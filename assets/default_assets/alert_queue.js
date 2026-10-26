/**
 * Shared alert-queue handshake for Mycelian overlays.
 * Holders emit playing/progress/complete; spectators must not.
 */
(function (global) {
    'use strict';

    function currentRoute() {
        var path = (global.location && global.location.pathname) || '';
        try {
            path = decodeURIComponent(path);
        } catch (eDecode) {}
        return String(path).replace(/^\//, '').replace(/\/+$/, '');
    }

    function normalizeRoute(value) {
        return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function parseSeq(seq) {
        if (seq == null || seq === '') {
            return null;
        }
        var n = parseInt(seq, 10);
        return Number.isNaN(n) ? null : n;
    }

    function isAllowedHolder(alertData) {
        var holders = alertData && alertData.queue_holders;
        if (!holders || !holders.length) {
            holders = ['alerts'];
        }
        var route = currentRoute();
        if (!route) {
            return false;
        }
        var i;
        var norm = normalizeRoute(route);
        for (i = 0; i < holders.length; i++) {
            var holder = String(holders[i] || '').replace(/^\//, '').replace(/\/+$/, '');
            if (holder === route) {
                return true;
            }
            if (norm && normalizeRoute(holder) === norm) {
                return true;
            }
        }
        return false;
    }

    function guardHandshakeEmits(socket) {
        if (!socket || socket.__mycelianHandshakeGuarded) {
            return socket;
        }
        socket.__mycelianHandshakeGuarded = true;
        var lastAlert = null;
        if (typeof socket.on === 'function') {
            socket.on('next_alert', function (data) {
                lastAlert = data || {};
            });
        }
        if (typeof socket.emit !== 'function') {
            return socket;
        }
        var origEmit = socket.emit.bind(socket);
        socket.emit = function (event) {
            if (event === 'alert_playing' || event === 'alert_progress' || event === 'alert_complete' || event === 'alert_started') {
                var payload = arguments.length > 1 ? arguments[1] : {};
                var merged = {};
                var key;
                if (lastAlert && typeof lastAlert === 'object') {
                    for (key in lastAlert) {
                        if (Object.prototype.hasOwnProperty.call(lastAlert, key)) {
                            merged[key] = lastAlert[key];
                        }
                    }
                }
                if (payload && typeof payload === 'object') {
                    for (key in payload) {
                        if (Object.prototype.hasOwnProperty.call(payload, key)) {
                            merged[key] = payload[key];
                        }
                    }
                }
                if (!isAllowedHolder(merged)) {
                    return socket;
                }
            }
            return origEmit.apply(socket, arguments);
        };
        return socket;
    }

    /**
     * Does an alerts_skip_alert payload target the alert with queue_seq `seq`?
     * Skips carry the queue_seq of the alert being skipped; a skip without one
     * (older senders) means "whatever is playing".
     */
    function skipMatches(skipData, seq) {
        var target = parseSeq(skipData && skipData.queue_seq);
        if (target == null) {
            return true;
        }
        return target === parseSeq(seq);
    }

    function attach(socket) {
        guardHandshakeEmits(socket);
        var pendingCompleteSeq = null;
        var retryTimer = null;

        function clearRetry() {
            if (retryTimer) {
                clearTimeout(retryTimer);
                retryTimer = null;
            }
        }

        if (socket && typeof socket.on === 'function') {
            socket.on('alert_complete_ack', function (data) {
                var acked = parseSeq(data && data.queue_seq);
                if (acked == null || pendingCompleteSeq == null) {
                    return;
                }
                if (acked === pendingCompleteSeq) {
                    pendingCompleteSeq = null;
                    clearRetry();
                }
            });
        }

        function playing(seq) {
            var n = parseSeq(seq);
            if (n == null || !socket) {
                return;
            }
            socket.emit('alert_playing', { queue_seq: n, route: currentRoute() });
        }

        function progress(seq, fields) {
            var n = parseSeq(seq);
            if (n == null || !socket) {
                return;
            }
            var payload = fields && typeof fields === 'object' ? fields : {};
            socket.emit('alert_progress', {
                queue_seq: n,
                route: currentRoute(),
                visual: !!payload.visual,
                audio: !!payload.audio,
                tts: !!payload.tts,
                resolve: !!payload.resolve,
                remaining_ms: payload.remaining_ms != null ? payload.remaining_ms : 0
            });
        }

        function complete(seq) {
            var n = parseSeq(seq);
            if (n == null || !socket) {
                return;
            }
            pendingCompleteSeq = n;
            clearRetry();
            function send() {
                if (pendingCompleteSeq !== n) {
                    return;
                }
                socket.emit('alert_complete', { queue_seq: n, route: currentRoute() });
                retryTimer = setTimeout(send, 500);
            }
            send();
        }

        function cancelComplete() {
            pendingCompleteSeq = null;
            clearRetry();
        }

        /** Report the alert's real run time so the Activity Feed timer matches. */
        function started(seq, durationMs) {
            var n = parseSeq(seq);
            var ms = Math.round(Number(durationMs));
            if (n == null || !socket || !isFinite(ms) || ms <= 0) {
                return;
            }
            socket.emit('alert_started', { queue_seq: n, duration_ms: ms, route: currentRoute() });
        }

        return {
            started: started,
            playing: playing,
            progress: progress,
            complete: complete,
            cancelComplete: cancelComplete
        };
    }

    global.MycelianOverlay = global.MycelianOverlay || {};
    if (typeof global.MycelianOverlay.connect === 'function' && !global.MycelianOverlay.connect.__mycelianHandshakeWrapped) {
        var prevConnect = global.MycelianOverlay.connect;
        function wrappedConnect(url, options) {
            return guardHandshakeEmits(prevConnect(url, options));
        }
        wrappedConnect.__mycelianHandshakeWrapped = true;
        global.MycelianOverlay.connect = wrappedConnect;
    }
    global.MycelianOverlay.alertQueue = {
        attach: attach,
        currentRoute: currentRoute,
        isAllowedHolder: isAllowedHolder,
        parseSeq: parseSeq,
        skipMatches: skipMatches,
        guardHandshakeEmits: guardHandshakeEmits
    };
})(typeof window !== 'undefined' ? window : this);

/**
 * Forward template JavaScript errors and warnings to mycelian.log via Socket.IO or HTTP.
 */
(function (global) {
    'use strict';

    var MAX_QUEUE = 50;
    var MAX_MESSAGE_LEN = 2048;
    var MAX_STACK_LEN = 4096;

    var state = {
        templateName: '',
        socket: null,
        httpBaseUrl: '',
        queue: [],
        consolePatched: false,
        hooksInstalled: false,
        initialized: false
    };

    function deriveTemplateName() {
        var path = (global.location && global.location.pathname) || '';
        path = path.replace(/^\/+/, '').replace(/\/+$/, '');
        return path || 'unknown';
    }

    function serializeArg(arg) {
        if (arg === null || arg === undefined) {
            return String(arg);
        }
        if (typeof arg === 'string') {
            return arg;
        }
        if (arg instanceof Error) {
            return arg.message + (arg.stack ? '\n' + arg.stack : '');
        }
        try {
            return JSON.stringify(arg);
        } catch (e) {
            return String(arg);
        }
    }

    function formatConsoleArgs(args) {
        var parts = [];
        for (var i = 0; i < args.length; i++) {
            parts.push(serializeArg(args[i]));
        }
        return parts.join(' ');
    }

    function buildPayload(level, message, extra) {
        extra = extra || {};
        var payload = {
            template_name: state.templateName || deriveTemplateName(),
            level: level,
            message: String(message || '').slice(0, MAX_MESSAGE_LEN),
            url: (global.location && global.location.pathname) || '',
            source: extra.source || 'mycelian_log'
        };
        if (extra.stack) {
            payload.stack = String(extra.stack).slice(0, MAX_STACK_LEN);
        }
        return payload;
    }

    function sendHttp(payload) {
        try {
            if (typeof global.fetch !== 'function') {
                return;
            }
            var base = state.httpBaseUrl;
            if (!base && global.location && global.location.origin && global.location.origin !== 'null') {
                base = global.location.origin;
            }
            if (!base) {
                return;
            }
            global.fetch(base + '/api/template-log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                keepalive: true
            }).catch(function () {});
        } catch (e0) {}
    }

    function transmit(payload) {
        if (state.socket && typeof state.socket.emit === 'function') {
            if (state.socket.connected) {
                try {
                    state.socket.emit('template_log', payload);
                    return;
                } catch (e1) {}
            }
            if (state.queue.length < MAX_QUEUE) {
                state.queue.push(payload);
            }
            return;
        }
        sendHttp(payload);
    }

    function flushQueue() {
        if (!state.socket || !state.socket.connected || typeof state.socket.emit !== 'function') {
            return;
        }
        while (state.queue.length) {
            try {
                state.socket.emit('template_log', state.queue.shift());
            } catch (e) {
                break;
            }
        }
    }

    function log(level, message, extra) {
        transmit(buildPayload(level, message, extra));
    }

    function patchConsole() {
        if (state.consolePatched || !global.console) {
            return;
        }
        state.consolePatched = true;
        var origError = global.console.error;
        var origWarn = global.console.warn;
        global.console.error = function () {
            log('error', formatConsoleArgs(arguments), { source: 'console.error' });
            return origError.apply(global.console, arguments);
        };
        global.console.warn = function () {
            log('warn', formatConsoleArgs(arguments), { source: 'console.warn' });
            return origWarn.apply(global.console, arguments);
        };
    }

    function installGlobalHooks() {
        if (state.hooksInstalled) {
            return;
        }
        state.hooksInstalled = true;
        global.addEventListener('error', function (ev) {
            var msg = ev.message || 'Script error';
            var stack = ev.error && ev.error.stack ? ev.error.stack : '';
            log('error', msg, { stack: stack, source: 'window.onerror' });
        });
        global.addEventListener('unhandledrejection', function (ev) {
            var reason = ev.reason;
            var msg = reason instanceof Error ? reason.message : serializeArg(reason);
            var stack = reason instanceof Error ? reason.stack : '';
            log('error', 'Unhandled rejection: ' + msg, {
                stack: stack,
                source: 'unhandledrejection'
            });
        });
    }

    function attachSocket(socket) {
        if (!socket) {
            return;
        }
        state.socket = socket;
        flushQueue();
        if (typeof socket.on === 'function') {
            socket.on('connect', flushQueue);
        }
    }

    function init(options) {
        options = options || {};
        if (options.templateName) {
            state.templateName = options.templateName;
        } else if (!state.templateName) {
            state.templateName = deriveTemplateName();
        }
        if (options.httpBaseUrl) {
            state.httpBaseUrl = String(options.httpBaseUrl);
        }
        if (!state.initialized) {
            state.initialized = true;
            installGlobalHooks();
            patchConsole();
        }
    }

    function setHttpBaseUrl(url) {
        state.httpBaseUrl = url ? String(url) : '';
    }

    global.MycelianLog = {
        init: init,
        setHttpBaseUrl: setHttpBaseUrl,
        error: function (message, extra) {
            log('error', message, extra);
        },
        warn: function (message, extra) {
            log('warn', message, extra);
        },
        info: function (message, extra) {
            log('info', message, extra);
        },
        attachSocket: attachSocket
    };
})(typeof window !== 'undefined' ? window : this);

/**
 * Shared overlay reconnect sync for Mycelian browser sources.
 * Loaded by legacy templates and Spore Studio boilerplates.
 */
(function (global) {
    'use strict';

    var DEFAULT_SOCKET_OPTS = {
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 10000,
        timeout: 20000
    };

    function matchesService(data, services) {
        if (!services || !services.length) {
            return true;
        }
        if (!data || !data.service) {
            return true;
        }
        if (data.service === 'internet') {
            return true;
        }
        return services.indexOf(data.service) !== -1;
    }

    function runSporeRecovery() {
        try {
            if (typeof global.sporeLoadStatsCache === 'function') {
                global.sporeLoadStatsCache();
            }
            if (typeof global.sporeRefreshAllDataDisplays === 'function') {
                global.sporeRefreshAllDataDisplays();
            }
        } catch (e) {
            try {
                console.warn('[overlay-recovery] spore refresh failed', e);
            } catch (e2) {}
        }
    }

    function runRecovery(data, options) {
        options = options || {};
        runSporeRecovery();
        if (typeof options.onRecovery === 'function') {
            options.onRecovery(data);
            return;
        }
        if (typeof global.onMycelianOverlayRecovery === 'function') {
            global.onMycelianOverlayRecovery(data);
        }
    }

    function mergeSocketOptions(options) {
        var ioOpts = {};
        var key;
        for (key in DEFAULT_SOCKET_OPTS) {
            if (Object.prototype.hasOwnProperty.call(DEFAULT_SOCKET_OPTS, key)) {
                ioOpts[key] = DEFAULT_SOCKET_OPTS[key];
            }
        }
        if (options && options.socketOptions) {
            for (key in options.socketOptions) {
                if (Object.prototype.hasOwnProperty.call(options.socketOptions, key)) {
                    ioOpts[key] = options.socketOptions[key];
                }
            }
        }
        return ioOpts;
    }

    function installHandlers(socket, options) {
        options = options || {};
        if (!socket || typeof socket.on !== 'function') {
            return socket;
        }

        socket.on('overlay-recovery', function (data) {
            if (!matchesService(data, options.services)) {
                return;
            }
            try {
                console.log('[overlay-recovery] sync', data);
            } catch (e0) {}
            runRecovery(data, options);
        });

        socket.on('disconnect', function (reason) {
            if (typeof options.onDisconnect === 'function') {
                options.onDisconnect(reason);
            }
        });

        return socket;
    }

    function connect(url, options) {
        options = options || {};
        if (typeof global.io !== 'function') {
            throw new Error('Socket.IO client (io) is not loaded');
        }
        var socket = global.io(url, mergeSocketOptions(options));
        return installHandlers(socket, options);
    }

    global.MycelianOverlay = {
        connect: connect,
        installHandlers: installHandlers,
        runSporeRecovery: runSporeRecovery
    };
})(typeof window !== 'undefined' ? window : this);

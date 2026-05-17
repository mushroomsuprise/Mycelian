/* Shared Spore Studio runtime: copy into queue_alert.html and instant_alert.html */
        var SPORE_ANIM_CLASS_MAP = {
            fade: { in: 'spore-anim-fade-in', out: 'spore-anim-fade-out' },
            slideIn: { in: 'spore-anim-slidein', out: 'spore-anim-slideout' },
            slideOut: { in: 'spore-anim-slidein', out: 'spore-anim-slideout' },
            scaleIn: { in: 'spore-anim-scalein', out: 'spore-anim-scaleout' },
            scaleOut: { in: 'spore-anim-scalein', out: 'spore-anim-scaleout' },
            none: { in: '', out: '' }
        };
        function sporeAnimClassFor(type, dir) {
            var m = SPORE_ANIM_CLASS_MAP[type] || SPORE_ANIM_CLASS_MAP.none;
            return dir === 'in' ? (m.in || '') : (m.out || '');
        }
        function sporeReadAnimConfig(el, override) {
            override = override || {};
            var g = function (key, attr, def) {
                if (override[key] !== undefined && override[key] !== null && override[key] !== '') {
                    return String(override[key]);
                }
                var v = el.getAttribute(attr);
                return v != null && v !== '' ? v : def;
            };
            var gi = function (key, attr, def) {
                if (override[key] !== undefined && override[key] !== null && override[key] !== '') {
                    var o = parseInt(String(override[key]), 10);
                    return isNaN(o) ? def : o;
                }
                var v = el.getAttribute(attr);
                var n = v != null && v !== '' ? parseInt(v, 10) : def;
                return isNaN(n) ? def : n;
            };
            return {
                animIn: g('animIn', 'data-spore-anim-in', 'none'),
                animOut: g('animOut', 'data-spore-anim-out', 'none'),
                animInMs: gi('animInMs', 'data-spore-anim-in-ms', 300),
                animOutMs: gi('animOutMs', 'data-spore-anim-out-ms', 300),
                delayMs: gi('delayMs', 'data-spore-anim-delay-ms', 0),
                easing: g('easing', 'data-spore-anim-easing', 'ease-out')
            };
        }
        function sporeAnimateIn(el, override, done) {
            if (!el) { if (done) { done(); } return; }
            var cfg = sporeReadAnimConfig(el, override);
            var cls = sporeAnimClassFor(cfg.animIn, 'in');
            if (!cls || cfg.animIn === 'none') { if (done) { done(); } return; }
            var ms = Math.max(0, cfg.animInMs || 300);
            el.classList.remove(cls);
            void el.offsetWidth;
            el.style.animationDuration = (ms / 1000) + 's';
            el.style.animationTimingFunction = cfg.easing || 'ease-out';
            el.classList.add(cls);
            setTimeout(function () {
                try { el.classList.remove(cls); } catch (e0) {}
                el.style.animationDuration = '';
                el.style.animationTimingFunction = '';
                if (done) { done(); }
            }, ms);
        }
        function sporeAnimateOut(el, override, done) {
            if (!el) { if (done) { done(); } return; }
            var cfg = sporeReadAnimConfig(el, override);
            var cls = sporeAnimClassFor(cfg.animOut, 'out');
            if (!cls || cfg.animOut === 'none') { if (done) { done(); } return; }
            var ms = Math.max(0, cfg.animOutMs || 300);
            el.classList.remove(cls);
            void el.offsetWidth;
            el.style.animationDuration = (ms / 1000) + 's';
            el.style.animationTimingFunction = cfg.easing || 'ease-in';
            el.classList.add(cls);
            setTimeout(function () {
                try { el.classList.remove(cls); } catch (e1) {}
                el.style.animationDuration = '';
                el.style.animationTimingFunction = '';
                if (done) { done(); }
            }, ms);
        }
        function sporeShow(id, override) {
            var el = document.getElementById(id);
            if (!el) { return; }
            el.removeAttribute('data-spore-hidden');
            var cfg = sporeReadAnimConfig(el, override);
            var run = function () { sporeAnimateIn(el, override, null); };
            if (cfg.delayMs > 0) {
                setTimeout(run, cfg.delayMs);
            } else {
                run();
            }
        }
        function sporeHide(id, override) {
            var el = document.getElementById(id);
            if (!el) { return; }
            var au = null;
            if (el.tagName === 'AUDIO') {
                au = el;
            } else {
                au = el.querySelector('audio');
            }
            function applyHidden() {
                var cfg = sporeReadAnimConfig(el, override);
                var cls = sporeAnimClassFor(cfg.animOut, 'out');
                if (!cls || cfg.animOut === 'none') {
                    el.setAttribute('data-spore-hidden', 'true');
                    return;
                }
                sporeAnimateOut(el, override, function () {
                    el.setAttribute('data-spore-hidden', 'true');
                });
            }
            if (!au) {
                applyHidden();
                return;
            }
            var fadeRaw = au.getAttribute('data-spore-audio-fade-out');
            var fadeMs = fadeRaw ? parseInt(fadeRaw, 10) : 0;
            if (!fadeMs || fadeMs <= 0 || isNaN(fadeMs)) {
                try { au.pause(); } catch (e1) {}
                applyHidden();
                return;
            }
            var steps = Math.max(8, Math.floor(fadeMs / 40));
            var iv = fadeMs / steps;
            var startVol = (typeof au.volume === 'number' && !isNaN(au.volume)) ? au.volume : 1;
            var step = startVol / steps;
            var n = 0;
            var t = setInterval(function () {
                n += 1;
                try {
                    au.volume = Math.max(0, startVol - step * n);
                } catch (e2) {}
                if (n >= steps) {
                    clearInterval(t);
                    try {
                        au.volume = startVol;
                    } catch (e3) {}
                    try { au.pause(); } catch (e4) {}
                    applyHidden();
                }
            }, iv);
        }

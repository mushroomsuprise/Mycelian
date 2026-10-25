/*
 * Mycelian pop-up notifications (the app window and the OBS docks share this file).
 *
 * MycelianToast.show(entry)   show or update (same id) a pop-up
 * MycelianToast.dismiss(id)
 * MycelianToast.configure({position, max, popups, levels, durations})
 * MycelianToast.onAction = function (id, index, entry) {}   host hook for button clicks
 * MycelianToast.onDismiss = function (id) {}
 *
 * entry: {id, level: error|warning|info|success|ongoing, source, title, message, hint,
 *         actions: [{label, icon}], count, timeout (ms, 0 = until dismissed),
 *         progress (null or 0-100), click (index of the action the card body runs)}
 *
 * The look comes from theme CSS variables: --color-notify-*, --color-bg-*, --color-text-*,
 * --notify-style (accent|chip|minimal|tinted|glass), --notify-radius, --notify-shadow
 * (none|soft|strong), --notify-timer (on|off), --notify-density (comfortable|compact).
 */
(function () {
    if (window.MycelianToast) { return; }

    var CSS = [
        '.mt-stack{position:fixed;z-index:9000;display:flex;flex-direction:column;gap:10px;width:min(392px,calc(100vw - 20px));pointer-events:none;}',
        '.mt-stack[data-pos="top-right"]{top:var(--mycelian-notify-header-clearance,64px);right:12px;}',
        '.mt-stack[data-pos="bottom-right"]{bottom:44px;right:12px;flex-direction:column-reverse;}',
        '.mt-stack[data-pos="top-center"]{top:var(--mycelian-notify-header-clearance,64px);left:50%;transform:translateX(-50%);}',
        '.mt-stack[data-pos="bottom-left"]{bottom:44px;left:12px;flex-direction:column-reverse;}',
        '.mt-stack.is-dock{top:auto !important;bottom:8px !important;left:8px !important;right:8px !important;width:auto;transform:none !important;flex-direction:column-reverse;}',
        '.mt{--c:var(--color-notify-info,#3b82f6);position:relative;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:3px 11px;',
        'padding:12px 8px 13px 14px;border-radius:var(--notify-radius,12px);background:var(--color-bg-surface,#1f2430);',
        'border:1px solid var(--color-border-subtle,rgba(255,255,255,.08));color:var(--color-text-primary,#f1f5f9);',
        'font:14px/1.4 var(--mycelian-ui-font,Inter,"Segoe UI",system-ui,sans-serif);pointer-events:auto;overflow:hidden;',
        'box-shadow:0 12px 32px rgba(0,0,0,.28),0 2px 6px rgba(0,0,0,.18);transition:transform .32s cubic-bezier(.22,1,.36,1),opacity .32s ease;}',
        '.mt.is-enter{opacity:0;transform:translateX(24px);} .mt.is-leave{opacity:0;transform:translateX(24px);}',
        '.mt-stack[data-pos="top-center"] .mt.is-enter,.mt-stack[data-pos="top-center"] .mt.is-leave{transform:translateY(-12px);}',
        '.mt-stack[data-pos="bottom-left"] .mt.is-enter,.mt-stack[data-pos="bottom-left"] .mt.is-leave{transform:translateX(-24px);}',
        '.mt.is-error{--c:var(--color-notify-error,#ef4444);} .mt.is-warning{--c:var(--color-notify-warning,#f59e0b);}',
        '.mt.is-success{--c:var(--color-notify-success,#22c55e);}',
        '.mt.is-click{cursor:pointer;} .mt.is-click:hover{border-color:color-mix(in srgb,var(--c) 40%,var(--color-border-subtle,transparent));}',
        '.mt-i{font-family:"Material Icons";font-weight:normal;font-style:normal;line-height:1;letter-spacing:normal;text-transform:none;white-space:nowrap;',
        'font-feature-settings:"liga";-webkit-font-smoothing:antialiased;font-size:18px;}',
        '.mt-ico{grid-row:span 4;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--c);',
        'background:color-mix(in srgb,var(--c) 15%,transparent);}',
        '.mt-ico .mt-i{font-size:19px;}',
        /* The app's global text rules would otherwise repaint these. */
        '.mt .mt-ico,.mt .mt-ico .mt-i{color:var(--c) !important;} .mt .mt-lvl,.mt .mt-count{color:var(--c) !important;}',
        '.mt.st-tinted .mt-ico,.mt.st-tinted .mt-ico .mt-i{color:#fff !important;} .mt.st-minimal .mt-ico{color:transparent !important;}',
        '.mt .mt-title{color:var(--color-text-primary,#f1f5f9) !important;} .mt .mt-msg,.mt .mt-fix{color:var(--color-text-secondary,#cbd5e1) !important;}',
        '.mt .mt-top{color:var(--color-text-muted,#94a3b8);} .mt .mt-fix .mt-i{color:var(--color-warning,#f59e0b) !important;}',
        '.mt-spin{width:18px;height:18px;border-radius:50%;border:2.5px solid color-mix(in srgb,var(--c) 30%,transparent);border-top-color:var(--c);animation:mt-spin .9s linear infinite;}',
        '@keyframes mt-spin{to{transform:rotate(360deg);}}',
        '.mt-top{display:flex;align-items:center;gap:6px;min-width:0;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;',
        'color:var(--color-text-muted,#94a3b8);white-space:nowrap;overflow:hidden;}',
        '.mt-top .mt-lvl{color:var(--c);} .mt-dot{width:3px;height:3px;border-radius:50%;background:currentColor;opacity:.6;flex:none;}',
        '.mt-count{display:inline-flex;align-items:center;height:17px;padding:0 6px;border-radius:999px;font-size:10px;font-weight:800;letter-spacing:0;',
        'color:var(--c);background:color-mix(in srgb,var(--c) 16%,transparent);}',
        '.mt-x{grid-row:1;grid-column:3;width:24px;height:24px;margin:-4px 0 0;border:0;padding:0;background:none;cursor:pointer;display:flex;',
        'align-items:center;justify-content:center;border-radius:50%;color:var(--color-text-muted,#94a3b8);}',
        '.mt-x:hover{background:var(--color-hover-overlay,rgba(255,255,255,.08));color:var(--color-text-primary,#fff);} .mt-x .mt-i{font-size:16px;}',
        '.mt-title{grid-column:2/span 2;font-size:14.4px;font-weight:650;line-height:1.3;overflow-wrap:anywhere;}',
        '.mt-msg{grid-column:2/span 2;font-size:12.8px;line-height:1.45;color:var(--color-text-secondary,#cbd5e1);overflow-wrap:anywhere;white-space:pre-line;}',
        '.mt-fix{grid-column:2/span 2;display:flex;gap:7px;margin-top:6px;padding:7px 9px;border-radius:8px;font-size:12.2px;line-height:1.45;',
        'color:var(--color-text-secondary,#cbd5e1);background:var(--color-bg-base,rgba(0,0,0,.2));border:1px solid var(--color-border-subtle,transparent);overflow-wrap:anywhere;}',
        '.mt-fix .mt-i{font-size:15px;color:var(--color-warning,#f59e0b);margin-top:1px;}',
        '.mt-prog{grid-column:2/span 2;height:5px;border-radius:3px;margin-top:8px;background:color-mix(in srgb,var(--c) 18%,transparent);overflow:hidden;}',
        '.mt-prog i{display:block;height:100%;width:0;background:var(--c);border-radius:3px;transition:width .4s ease;}',
        '.mt-prog.is-busy i{width:35%;animation:mt-busy 1.3s ease-in-out infinite;}',
        '@keyframes mt-busy{0%{transform:translateX(-100%);}100%{transform:translateX(300%);}}',
        '.mt-acts{grid-column:2/span 2;display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;}',
        '.mt-btn{display:inline-flex;align-items:center;gap:5px;height:26px;padding:0 10px;border-radius:7px;font:inherit;font-size:11.8px;font-weight:650;cursor:pointer;',
        'color:var(--color-text-secondary,#cbd5e1);background:none;border:1px solid var(--color-border-default,rgba(255,255,255,.15));}',
        '.mt-btn:hover{color:var(--color-text-primary,#fff);background:var(--color-hover-overlay,rgba(255,255,255,.06));} .mt-btn .mt-i{font-size:15px;}',
        '.mt-btn.is-main{color:color-mix(in srgb,var(--color-primary,#7c3aed) 55%,var(--color-text-primary,#fff));',
        'border-color:var(--color-border-accent,var(--color-primary,#7c3aed));background:var(--color-primary-light,rgba(124,58,237,.15));}',
        '.mt-bar{position:absolute;left:0;bottom:0;height:2px;width:100%;background:var(--c);opacity:.85;}',
        /* styles */
        '.mt.st-accent::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c);}',
        '.mt.st-minimal{padding:10px 8px 11px 12px;} .mt.st-minimal .mt-ico{width:10px;height:10px;margin-top:4px;background:var(--c);grid-row:span 1;}',
        '.mt.st-minimal .mt-ico>*{display:none;}',
        '.mt.st-tinted{background:color-mix(in srgb,var(--c) 18%,var(--color-bg-surface,#1f2430));border-color:color-mix(in srgb,var(--c) 40%,var(--color-border-default,transparent));}',
        '.mt.st-tinted .mt-ico{background:var(--c);color:#fff;} .mt.st-tinted .mt-spin{border-color:rgba(255,255,255,.35);border-top-color:#fff;}',
        '.mt.st-glass{background:color-mix(in srgb,var(--color-bg-surface,#1f2430) 72%,transparent);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);',
        'border-color:color-mix(in srgb,var(--c) 35%,var(--color-border-subtle,transparent));}',
        '.mt.st-glass .mt-ico{box-shadow:0 0 14px color-mix(in srgb,var(--c) 45%,transparent);}',
        '.mt.sh-none{box-shadow:none;} .mt.sh-strong{box-shadow:0 18px 44px rgba(0,0,0,.45),0 4px 10px rgba(0,0,0,.25);}',
        '.mt.is-compact{padding:9px 8px 10px 11px;gap:2px 9px;} .mt.is-compact .mt-ico{width:26px;height:26px;} .mt.is-compact .mt-ico .mt-i{font-size:16px;}',
        '.mt.is-compact .mt-title{font-size:13px;} .mt.is-compact .mt-msg{font-size:12px;}',
        '.mt-stack.is-dock .mt{padding:9px 6px 10px 11px;gap:2px 9px;} .mt-stack.is-dock .mt-ico{width:26px;height:26px;} .mt-stack.is-dock .mt-title{font-size:13px;}',
        '@media (prefers-reduced-motion: reduce){.mt{transition:none;} .mt-spin,.mt-prog.is-busy i{animation:none;}}'
    ].join('\n');

    var ICONS = { error: 'error', warning: 'warning', info: 'info', success: 'check_circle' };
    var TAGS = { error: 'Error', warning: 'Warning', info: 'Info', success: 'Done', ongoing: 'Working' };
    var cfg = { position: 'top-right', max: 4, popups: true, dock: false };
    var items = {};
    var order = [];
    var stack = null;

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        if (text != null) { node.textContent = text; }
        return node;
    }

    function icon(name) { return el('span', 'mt-i', name); }

    function themeVar(name, fallback) {
        try {
            var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
            return v || fallback;
        } catch (e) { return fallback; }
    }

    function ensureStyles() {
        if (!document.getElementById('mycelian-toast-css')) {
            var style = el('style');
            style.id = 'mycelian-toast-css';
            style.textContent = CSS;
            (document.head || document.documentElement).appendChild(style);
        }
    }

    function ensureStack() {
        ensureStyles();
        if (!stack || !stack.isConnected) {
            stack = el('div', 'mt-stack');
            (document.body || document.documentElement).appendChild(stack);
        }
        stack.setAttribute('data-pos', cfg.position);
        stack.classList.toggle('is-dock', !!cfg.dock);
        return stack;
    }

    function applyLook(node) {
        var style = themeVar('--notify-style', 'accent');
        ['accent', 'chip', 'minimal', 'tinted', 'glass'].forEach(function (s) { node.classList.toggle('st-' + s, s === style); });
        var shadow = themeVar('--notify-shadow', 'soft');
        node.classList.toggle('sh-none', shadow === 'none');
        node.classList.toggle('sh-strong', shadow === 'strong');
        node.classList.toggle('is-compact', themeVar('--notify-density', 'comfortable') === 'compact');
    }

    function timerOn() { return themeVar('--notify-timer', 'on') !== 'off'; }

    function clearTimer(item) {
        if (item.timer) { clearTimeout(item.timer); item.timer = null; }
    }

    function startTimer(item) {
        clearTimer(item);
        var bar = item.node.querySelector('.mt-bar');
        if (!item.remaining || item.remaining <= 0) {
            if (bar) { bar.remove(); }
            return;
        }
        if (!bar && timerOn()) {
            bar = el('div', 'mt-bar');
            item.node.appendChild(bar);
        }
        if (bar) {
            var total = item.timeout || item.remaining;
            bar.style.transition = 'none';
            bar.style.width = (item.remaining / total * 100) + '%';
            void bar.offsetWidth;
            bar.style.transition = 'width ' + item.remaining + 'ms linear';
            bar.style.width = '0%';
        }
        item.startedAt = Date.now();
        item.timer = setTimeout(function () { dismiss(item.id); }, item.remaining);
    }

    function pauseTimer(item) {
        if (!item.timer) { return; }
        clearTimer(item);
        item.remaining = Math.max(0, item.remaining - (Date.now() - item.startedAt));
        var bar = item.node.querySelector('.mt-bar');
        if (bar) {
            var w = getComputedStyle(bar).width;
            bar.style.transition = 'none';
            bar.style.width = w;
        }
    }

    function render(item) {
        var e = item.entry;
        var level = TAGS[e.level] ? e.level : 'info';
        var node = item.node;
        node.className = 'mt is-' + (level === 'ongoing' ? 'info' : level);
        applyLook(node);
        node.textContent = '';
        var ico = el('div', 'mt-ico');
        ico.appendChild(level === 'ongoing' ? el('span', 'mt-spin') : icon(ICONS[level]));
        node.appendChild(ico);
        var top = el('div', 'mt-top');
        top.appendChild(el('span', 'mt-lvl', TAGS[level]));
        if (e.source) {
            top.appendChild(el('span', 'mt-dot'));
            top.appendChild(el('span', '', e.source));
        }
        if (e.count && e.count > 1) { top.appendChild(el('span', 'mt-count', '×' + e.count)); }
        node.appendChild(top);
        var x = el('button', 'mt-x');
        x.type = 'button';
        x.title = 'Dismiss';
        x.appendChild(icon('close'));
        x.addEventListener('click', function (ev) { ev.stopPropagation(); dismiss(item.id, true); });
        node.appendChild(x);
        node.appendChild(el('div', 'mt-title', e.title || e.message || ''));
        if (e.title && e.message) { node.appendChild(el('div', 'mt-msg', e.message)); }
        if (e.hint) {
            var fix = el('div', 'mt-fix');
            fix.appendChild(icon('lightbulb'));
            fix.appendChild(el('span', '', e.hint));
            node.appendChild(fix);
        }
        if (e.progress != null || level === 'ongoing') {
            var prog = el('div', 'mt-prog');
            var fill = el('i');
            if (e.progress == null) { prog.classList.add('is-busy'); } else { fill.style.width = Math.max(0, Math.min(100, e.progress)) + '%'; }
            prog.appendChild(fill);
            node.appendChild(prog);
        }
        var acts = e.actions || [];
        if (acts.length) {
            var row = el('div', 'mt-acts');
            acts.forEach(function (a, index) {
                var b = el('button', 'mt-btn' + (index === 0 && a.main !== false ? ' is-main' : ''));
                b.type = 'button';
                if (a.icon) { b.appendChild(icon(a.icon)); }
                b.appendChild(document.createTextNode(a.label || 'Open'));
                b.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    fireAction(item, index);
                });
                row.appendChild(b);
            });
            node.appendChild(row);
        }
        node.classList.toggle('is-click', e.click != null && acts[e.click] != null);
    }

    function fireAction(item, index) {
        var a = (item.entry.actions || [])[index];
        if (!a) { return; }
        try { if (typeof api.onAction === 'function') { api.onAction(item.id, index, item.entry); } } catch (err) { console.error(err); }
        if (a.dismiss !== false) { dismiss(item.id); }
    }

    function trim() {
        var visible = order.filter(function (id) { return items[id] && !items[id].leaving; });
        while (visible.length > cfg.max) {
            /* Drop the oldest that is not an error or work in progress, else the oldest. */
            var victim = visible.find(function (id) { var lv = items[id].entry.level; return lv !== 'error' && lv !== 'ongoing'; }) || visible[0];
            dismiss(victim);
            visible = visible.filter(function (id) { return id !== victim; });
        }
    }

    /* While the notification centre is open, pop-ups would cover it: fade the visible ones
       out and keep new ones away (they are listed in the centre). */
    var suppressed = false;
    function suppress(on) {
        suppressed = !!on;
        if (suppressed) { order.slice().forEach(function (id) { dismiss(id); }); }
    }

    function show(entry) {
        if (!entry || !entry.id || suppressed) { return; }
        ensureStack();
        var item = items[entry.id];
        if (!item) {
            var node = el('div', 'mt');
            item = { id: entry.id, node: node, entry: entry };
            items[entry.id] = item;
            order.push(entry.id);
            node.addEventListener('mouseenter', function () { pauseTimer(item); });
            node.addEventListener('mouseleave', function () { if (!item.leaving) { startTimer(item); } });
            node.addEventListener('click', function () {
                if (item.entry.click != null) { fireAction(item, item.entry.click); }
            });
            render(item);
            node.classList.add('is-enter');
            stack.appendChild(node);
            void node.offsetWidth;
            node.classList.remove('is-enter');
        } else {
            item.entry = entry;
            item.leaving = false;
            item.node.classList.remove('is-leave');
            render(item);
        }
        item.timeout = Number(entry.timeout || 0);
        item.remaining = item.timeout;
        if (!item.node.matches(':hover')) { startTimer(item); }
        trim();
    }

    function dismiss(id, byUser) {
        var item = items[id];
        if (!item || item.leaving) { return; }
        item.leaving = true;
        clearTimer(item);
        item.node.classList.add('is-leave');
        setTimeout(function () {
            if (item.node.parentNode) { item.node.parentNode.removeChild(item.node); }
            if (items[id] === item) {
                delete items[id];
                order = order.filter(function (x) { return x !== id; });
            }
        }, 340);
        if (byUser) {
            try { if (typeof api.onDismiss === 'function') { api.onDismiss(id); } } catch (err) { console.error(err); }
        }
    }

    function configure(opts) {
        opts = opts || {};
        ['position', 'max', 'popups', 'levels', 'durations', 'dock'].forEach(function (k) {
            if (opts[k] != null) { cfg[k] = opts[k]; }
        });
        if (stack) { ensureStack(); }
    }

    /* For pages that make their own pop-ups (OBS docks): respects the level switches and
       per-level durations sent by the app. */
    var localSeq = 0;
    function local(level, title, message, extra) {
        extra = extra || {};
        level = TAGS[level] ? level : 'info';
        var filterLevel = level === 'ongoing' ? 'info' : level;
        if (cfg.popups === false || (cfg.levels && cfg.levels[filterLevel] === false)) { return null; }
        var timeout = extra.timeout;
        if (timeout == null) {
            timeout = cfg.durations && cfg.durations[filterLevel] != null ? cfg.durations[filterLevel]
                : ({ error: 0, warning: 8000, info: 4000, success: 3000 }[filterLevel] || 4000);
            if (level === 'ongoing') { timeout = 0; }
        }
        var id = extra.id || ('local-' + (++localSeq));
        var entry = {
            id: id, level: level, source: extra.source || '', title: title, message: message || '', hint: extra.hint || '',
            actions: (extra.actions || []).map(function (a) { return { label: a.label, icon: a.icon, main: a.main }; }),
            count: extra.count || 0, timeout: timeout, progress: extra.progress == null ? null : extra.progress,
            click: extra.click == null ? null : extra.click
        };
        var handlers = (extra.actions || []).map(function (a) { return a.run; });
        localHandlers[id] = handlers;
        show(entry);
        return id;
    }
    var localHandlers = {};

    var api = {
        show: show,
        dismiss: dismiss,
        configure: configure,
        ensureStyles: ensureStyles,
        suppress: suppress,
        local: local,
        onAction: function (id, index) {
            var fn = (localHandlers[id] || [])[index];
            if (typeof fn === 'function') { fn(); }
        },
        onDismiss: null
    };
    window.MycelianToast = api;
})();

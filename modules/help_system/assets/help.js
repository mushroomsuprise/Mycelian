/* Mycelian Help — interactive behaviour for help pages.
 *
 * Help pages are static HTML fragments (modules/help_system/content/*.html)
 * rendered with ui.html, so inline <script> tags never run. Everything
 * interactive is driven from here: one set of delegated listeners on
 * document, plus MycelianHelp.enhance(root) after each page renders.
 *
 * Contracts used by page authors:
 *   .hx-tabs / .hm     [data-tab="k"] buttons switch sibling [data-panel="k"]
 *                      inside the nearest [data-tabs] (or .hx-tabs) scope.
 *   .hx-guide[data-guide="key"] > .hx-step   numbered, "mark done" progress,
 *                      saved in localStorage.
 *   ul.hx-checklist[data-checklist="key"] > li   click to tick, saved.
 *   pre                copy button added automatically.
 *   .hx-copy[data-copy]  click copies data-copy (or text).
 *   .hm-switch, .hm-check   click toggles .is-on (illustration only).
 *   .hm-expansion      head click toggles .is-open.
 *   .hm-marker[data-n] <-> ol.hx-annotations li[data-n]  hover highlight.
 */
(function () {
    if (window.MycelianHelp) return;

    var STORE_PREFIX = 'mycelian.help.';

    function load(key) {
        try {
            return JSON.parse(localStorage.getItem(STORE_PREFIX + key) || 'null');
        } catch (e) {
            return null;
        }
    }

    function save(key, value) {
        try {
            localStorage.setItem(STORE_PREFIX + key, JSON.stringify(value));
        } catch (e) { /* storage unavailable: progress is per-session only */ }
    }

    function copyText(text, el) {
        function done() {
            if (!el) return;
            el.classList.add('is-copied');
            var label = el.querySelector('.hx-copy-label');
            if (label) label.textContent = 'Copied';
            setTimeout(function () {
                el.classList.remove('is-copied');
                if (label) label.textContent = 'Copy';
            }, 1400);
        }
        function fallback() {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); } catch (e) { /* ignore */ }
            document.body.removeChild(ta);
            done();
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, fallback);
        } else {
            fallback();
        }
    }

    /* ---- Tabs -------------------------------------------------------- */

    function tabScope(btn) {
        return btn.closest('[data-tabs]') || btn.closest('.hx-tabs') || btn.closest('.hm');
    }

    function activateTab(scope, key) {
        if (!scope) return;
        scope.querySelectorAll('[data-tab]').forEach(function (b) {
            if (tabScope(b) === scope) b.classList.toggle('is-active', b.getAttribute('data-tab') === key);
        });
        scope.querySelectorAll('[data-panel]').forEach(function (p) {
            if (tabScope(p) === scope) p.classList.toggle('is-active', p.getAttribute('data-panel') === key);
        });
    }

    function initTabs(root) {
        root.querySelectorAll('[data-tabs], .hx-tabs').forEach(function (scope) {
            var buttons = Array.prototype.filter.call(scope.querySelectorAll('[data-tab]'), function (b) {
                return tabScope(b) === scope;
            });
            if (!buttons.length) return;
            var active = buttons.filter(function (b) { return b.classList.contains('is-active'); })[0] || buttons[0];
            activateTab(scope, active.getAttribute('data-tab'));
        });
    }

    /* ---- Guides ------------------------------------------------------ */

    function guideKey(guide) {
        return 'guide.' + (guide.getAttribute('data-guide') || 'default');
    }

    function refreshGuide(guide) {
        var steps = guide.querySelectorAll(':scope > .hx-step');
        var done = guide.querySelectorAll(':scope > .hx-step.is-done').length;
        var bar = guide.querySelector('.hx-guide-progress > span');
        var count = guide.querySelector('.hx-guide-count');
        if (bar) bar.style.width = (steps.length ? (100 * done / steps.length) : 0) + '%';
        if (count) count.textContent = done + ' of ' + steps.length + ' done';
        guide.classList.toggle('is-complete', steps.length > 0 && done === steps.length);
    }

    function initGuide(guide) {
        if (guide.getAttribute('data-hx-ready')) {
            refreshGuide(guide);
            return;
        }
        guide.setAttribute('data-hx-ready', '1');
        var state = load(guideKey(guide)) || {};
        var head = document.createElement('div');
        head.className = 'hx-guide-head';
        head.innerHTML =
            '<span class="hx-guide-title"></span>' +
            '<span class="hx-guide-progress"><span></span></span>' +
            '<span class="hx-guide-count"></span>' +
            '<button type="button" class="hx-guide-reset" title="Clear progress">Reset</button>';
        head.querySelector('.hx-guide-title').textContent = guide.getAttribute('data-title') || 'Setup progress';
        guide.insertBefore(head, guide.firstChild);

        var steps = guide.querySelectorAll(':scope > .hx-step');
        steps.forEach(function (step, i) {
            step.setAttribute('data-step', String(i + 1));
            if (state[i]) step.classList.add('is-done');
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'hx-step-done';
            btn.textContent = 'Mark step done';
            step.appendChild(btn);
        });
        refreshGuide(guide);
    }

    function saveGuide(guide) {
        var state = {};
        guide.querySelectorAll(':scope > .hx-step').forEach(function (step, i) {
            if (step.classList.contains('is-done')) state[i] = 1;
        });
        save(guideKey(guide), state);
    }

    /* ---- Checklists -------------------------------------------------- */

    function initChecklist(list) {
        var key = 'check.' + (list.getAttribute('data-checklist') || 'default');
        var state = load(key) || {};
        list.querySelectorAll(':scope > li').forEach(function (li, i) {
            li.classList.toggle('is-checked', !!state[i]);
        });
    }

    function saveChecklist(list) {
        var key = 'check.' + (list.getAttribute('data-checklist') || 'default');
        var state = {};
        list.querySelectorAll(':scope > li').forEach(function (li, i) {
            if (li.classList.contains('is-checked')) state[i] = 1;
        });
        save(key, state);
    }

    /* ---- Enhance a freshly rendered page ---------------------------- */

    function enhance(root) {
        root = root || document.querySelector('.help-doc');
        if (!root) return false;

        root.querySelectorAll('pre').forEach(function (pre) {
            if (pre.querySelector('.hx-copy-btn')) return;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'hx-copy-btn';
            btn.innerHTML = '<span class="hx-mi" style="font-size:14px">content_copy</span><span class="hx-copy-label">Copy</span>';
            pre.appendChild(btn);
        });

        root.querySelectorAll('a[href^="http://"], a[href^="https://"]').forEach(function (a) {
            if (!a.classList.contains('hx-card')) a.classList.add('hx-ext');
        });

        initTabs(root);
        root.querySelectorAll('.hx-guide').forEach(initGuide);
        root.querySelectorAll('ul.hx-checklist').forEach(initChecklist);
        return true;
    }

    /* ---- Delegated listeners (installed once) ----------------------- */

    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || !t.closest || !t.closest('.help-doc')) return;

        var tab = t.closest('[data-tab]');
        if (tab) {
            activateTab(tabScope(tab), tab.getAttribute('data-tab'));
            return;
        }

        var copyBtn = t.closest('.hx-copy-btn');
        if (copyBtn) {
            var code = copyBtn.parentElement.querySelector('code') || copyBtn.parentElement;
            copyText(code.innerText.replace(/\s*Copy$/, ''), copyBtn);
            return;
        }

        var inlineCopy = t.closest('.hx-copy');
        if (inlineCopy) {
            copyText(inlineCopy.getAttribute('data-copy') || inlineCopy.textContent.trim(), inlineCopy);
            return;
        }

        var doneBtn = t.closest('.hx-step-done');
        if (doneBtn) {
            var step = doneBtn.closest('.hx-step');
            step.classList.toggle('is-done');
            var guide = step.closest('.hx-guide');
            saveGuide(guide);
            refreshGuide(guide);
            return;
        }

        var reset = t.closest('.hx-guide-reset');
        if (reset) {
            var g = reset.closest('.hx-guide');
            g.querySelectorAll(':scope > .hx-step').forEach(function (s) { s.classList.remove('is-done'); });
            saveGuide(g);
            refreshGuide(g);
            return;
        }

        var item = t.closest('ul.hx-checklist > li');
        if (item && !t.closest('a')) {
            item.classList.toggle('is-checked');
            saveChecklist(item.parentElement);
            return;
        }

        var sw = t.closest('.hm-switch, .hm-check');
        if (sw) {
            sw.classList.toggle('is-on');
            return;
        }

        var exp = t.closest('.hm-expansion-head');
        if (exp) {
            exp.parentElement.classList.toggle('is-open');
        }
    }, false);

    function setHot(target, on) {
        var n = target.getAttribute('data-n');
        var doc = target.closest('.help-doc');
        if (!n || !doc) return;
        doc.querySelectorAll('.hm-marker[data-n="' + n + '"], ol.hx-annotations > li[data-n="' + n + '"]').forEach(function (el) {
            el.classList.toggle('is-hot', on);
        });
    }

    document.addEventListener('mouseover', function (e) {
        var t = e.target && e.target.closest && e.target.closest('.help-doc .hm-marker, .help-doc ol.hx-annotations > li');
        if (t) setHot(t, true);
    });

    document.addEventListener('mouseout', function (e) {
        var t = e.target && e.target.closest && e.target.closest('.help-doc .hm-marker, .help-doc ol.hx-annotations > li');
        if (t) setHot(t, false);
    });

    window.MycelianHelp = {
        enhance: enhance,
        /* Retry until NiceGUI has mounted the ui.html element. */
        enhanceSoon: function () {
            var tries = 0;
            (function attempt() {
                if (enhance() || ++tries > 20) return;
                setTimeout(attempt, 50);
            })();
        },
        scrollTo: function (id) {
            var el = document.getElementById(id);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    };
})();

/* Dependency-free Advanced JS editor for Spore Studio.
 *
 * Runs in two places with the same markup (editor.html, between the SS_JS_DIALOG markers):
 *   - the Spore Studio page, as the "Expand editor" dialog;
 *   - the pop-out page (/_spore_studio_js_editor, body[data-ss-js-popout="1"]), which keeps
 *     its text in sync with the Studio page through the overlay server's Socket.IO relay
 *     (spore_js_join / spore_js_relay). The pop-out may open in another browser process, so
 *     window.opener / BroadcastChannel cannot be relied on.
 *
 * Linting and the function reference come from js_lint.js (SporeJsLint, vendored acorn);
 * without it the editor still works, just without those panels.
 */
(function () {
    "use strict";

    var KEYWORDS = {
        break: 1, case: 1, catch: 1, const: 1, continue: 1, debugger: 1,
        default: 1, delete: 1, do: 1, else: 1, false: 1, finally: 1, for: 1,
        function: 1, if: 1, in: 1, instanceof: 1, let: 1, new: 1, null: 1,
        return: 1, switch: 1, this: 1, throw: 1, true: 1, try: 1, typeof: 1,
        undefined: 1, var: 1, void: 1, while: 1, with: 1, class: 1, extends: 1,
        super: 1, yield: 1, async: 1, await: 1, of: 1
    };
    var PAIRS = { "(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`" };
    var CLOSERS = { ")": "(", "]": "[", "}": "{" };
    var REFS = [
        { name: "sporeShow(id)", snippet: "sporeShow(\"element_id\");\n" },
        { name: "sporeHide(id)", snippet: "sporeHide(\"element_id\");\n" },
        { name: "sporeSetText(id, value)", snippet: "sporeSetText(\"element_id\", \"hello\");\n" },
        {
            name: "sporePlayMediaAudio",
            snippet: "sporePlayMediaAudio(\"element_id\", { volume: 1, fade_in_ms: 200 });\n"
        },
        {
            name: "sporeCounterAdjust",
            snippet: "sporeCounterAdjust(\"counter_id\", \"increment\", { kind: \"fixed\", value: 1 });\n"
        },
        {
            name: "sporeTimerStart / Pause / Reset",
            snippet: "sporeTimerStart(\"timer_id\");\n"
        },
        {
            name: "sporeDbGet(path, cb)",
            snippet: "sporeDbGet(\"path/here\", function (data) {\n    \n});\n"
        },
        {
            name: "sporeDbSet(path, data)",
            snippet: "sporeDbSet(\"path/here\", { key: \"value\" });\n"
        },
        {
            name: "socket.on(event)",
            snippet: "socket.on(\"instant_alert\", function (data) {\n    \n});\n"
        },
        {
            name: "alert_complete",
            snippet: "socket.emit(\"alert_complete\", { queue_seq: data.queue_seq });\n"
        }
    ];
    var LINE_PX = 20;
    var PAD_TOP = 8;
    var PAD_LEFT = 12;
    var LINT_DELAY_MS = 250;
    var CONTEXT_DELAY_MS = 1500;
    var POPOUT_EDIT_DELAY_MS = 120;
    var POPOUT_PING_MS = 5000;
    var POPOUT_STALE_MS = 20000;
    var POPOUT_CONNECT_MS = 10000;

    var handlers = {
        getText: function () { return ""; },
        setText: function () {},
        commit: function () {},
        // Optional: Promise of { scripts: [...] } for the compiled template (see js_lint.js).
        getContext: null,
        // Optional: string that changes when the template (not the Advanced JS) changes.
        contextKey: function () { return ""; },
        getSocket: function () { return null; },
        templateName: function () { return ""; },
        notify: function () {}
    };
    var open = false;
    var commitTimer = null;
    var findFrom = 0;
    var lintTimer = null;
    var lintResult = null;
    var lintedText = null;
    var lintCtx = null;
    var ctxKey = null;
    var ctxState = "idle";
    var ctxTimer = null;
    var ctxRequest = 0;
    var refBuilt = false;
    var charWidth = 0;
    var popoutMode = false;
    var popout = {
        channel: "",
        socket: null,
        alive: false,
        lastPing: 0,
        connectTimer: null,
        sendTimer: null,
        pingTimer: null,
        template: "",
        closed: false,
        listening: false
    };

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function escAttr(s) {
        return esc(s).replace(/"/g, "&quot;");
    }

    // The text <span> of a toolbar button (not the .ss-ic icon spore_ui adds in front).
    function labelSpan(btn) {
        var spans = btn ? btn.getElementsByTagName("span") : [];
        for (var i = 0; i < spans.length; i++) {
            if (!/\bss-ic\b/.test(spans[i].className || "")) { return spans[i]; }
        }
        return null;
    }

    function lint() {
        return window.SporeJsLint && typeof window.SporeJsLint.lint === "function" ? window.SporeJsLint : null;
    }

    function highlightJs(src) {
        var out = "";
        var i = 0;
        var n = src.length;
        while (i < n) {
            var c = src.charAt(i);
            var next = src.charAt(i + 1);
            if (c === "/" && next === "/") {
                var end = src.indexOf("\n", i);
                if (end < 0) { end = n; }
                out += '<span class="ss-js-tok-com">' + esc(src.slice(i, end)) + "</span>";
                i = end;
                continue;
            }
            if (c === "/" && next === "*") {
                var endBlock = src.indexOf("*/", i + 2);
                if (endBlock < 0) { endBlock = n; }
                else { endBlock += 2; }
                out += '<span class="ss-js-tok-com">' + esc(src.slice(i, endBlock)) + "</span>";
                i = endBlock;
                continue;
            }
            if (c === '"' || c === "'" || c === "`") {
                var j = i + 1;
                while (j < n) {
                    if (src.charAt(j) === "\\") { j += 2; continue; }
                    if (src.charAt(j) === c) { j += 1; break; }
                    if (c !== "`" && src.charAt(j) === "\n") { break; }
                    j += 1;
                }
                out += '<span class="ss-js-tok-str">' + esc(src.slice(i, j)) + "</span>";
                i = j;
                continue;
            }
            if (c >= "0" && c <= "9" && !/[A-Za-z0-9_$]/.test(src.charAt(i - 1) || "")) {
                var k = i + 1;
                while (k < n && /[0-9.xXa-fA-F_]/.test(src.charAt(k))) { k += 1; }
                out += '<span class="ss-js-tok-num">' + esc(src.slice(i, k)) + "</span>";
                i = k;
                continue;
            }
            if (/[A-Za-z_$]/.test(c)) {
                var w = i + 1;
                while (w < n && /[A-Za-z0-9_$]/.test(src.charAt(w))) { w += 1; }
                var word = src.slice(i, w);
                var after = src.slice(w).match(/^\s*\(/);
                var cls = KEYWORDS[word] ? "ss-js-tok-kw" : (after ? "ss-js-tok-fn" : "");
                out += cls ? ('<span class="' + cls + '">' + esc(word) + "</span>") : esc(word);
                i = w;
                continue;
            }
            out += esc(c);
            i += 1;
        }
        return out;
    }

    function lineCol(text, index) {
        var slice = text.slice(0, index);
        var lines = slice.split("\n");
        return { line: lines.length, col: lines[lines.length - 1].length + 1 };
    }

    /* ------------------------------------------------------------- problems display */

    function currentProblems() {
        var ta = $("ss-js-input");
        if (!lintResult || !ta || lintedText !== ta.value) { return []; }
        return lintResult.problems || [];
    }

    function measureCharWidth() {
        if (charWidth) { return charWidth; }
        var code = $("ss-js-code");
        if (!code || !document.createElement) { return 7.8; }
        var probe = document.createElement("span");
        probe.className = "ss-js-measure";
        probe.textContent = "MMMMMMMMMM";
        code.appendChild(probe);
        var w = probe.getBoundingClientRect ? probe.getBoundingClientRect().width / 10 : 0;
        code.removeChild(probe);
        if (w > 0) { charWidth = w; }
        return w > 0 ? w : 7.8;
    }

    // Visual column (tabs are 2 wide) for a 1-based column on a line.
    function visualCol(lineText, col) {
        var v = 0;
        for (var i = 0; i < col - 1 && i < lineText.length; i++) {
            v += lineText.charAt(i) === "\t" ? 2 - (v % 2) : 1;
        }
        return v + Math.max(0, col - 1 - lineText.length);
    }

    function renderGutter(lineCount, problems) {
        var gutter = $("ss-js-gutter");
        if (!gutter) { return; }
        var marks = {};
        problems.forEach(function (p) {
            if (marks[p.line] !== "error") { marks[p.line] = p.severity; }
        });
        if (!problems.length) {
            var nums = [];
            for (var i = 1; i <= lineCount; i++) { nums.push(String(i)); }
            gutter.textContent = nums.join("\n");
            return;
        }
        var html = [];
        for (var n = 1; n <= lineCount; n++) {
            html.push(marks[n]
                ? '<span class="ss-js-gut-' + (marks[n] === "error" ? "err" : "warn") + '">' + n + "</span>"
                : String(n));
        }
        gutter.innerHTML = html.join("\n");
    }

    function renderMarks(text, problems) {
        var layer = $("ss-js-marks");
        if (!layer) { return; }
        if (!problems.length) { layer.innerHTML = ""; return; }
        var lines = text.split("\n");
        var cw = measureCharWidth();
        var html = problems.map(function (p) {
            var lineText = lines[p.line - 1] || "";
            var endCol = p.endLine === p.line ? p.endCol : lineText.length + 1;
            var a = visualCol(lineText, p.col);
            var b = Math.max(a + 1, visualCol(lineText, endCol));
            return '<div class="ss-js-mark ss-js-mark--' + (p.severity === "error" ? "error" : "warn") +
                '" style="top:' + (PAD_TOP + (p.line - 1) * LINE_PX + LINE_PX - 4) + "px;left:" +
                (PAD_LEFT + a * cw) + "px;width:" + ((b - a) * cw) + 'px"></div>';
        });
        layer.innerHTML = html.join("");
    }

    function problemSummaryText(sum, available) {
        if (!available) { return "Checker unavailable"; }
        if (!sum.errors && !sum.warnings) { return "No problems"; }
        var parts = [];
        if (sum.errors) { parts.push(sum.errors + (sum.errors === 1 ? " error" : " errors")); }
        if (sum.warnings) { parts.push(sum.warnings + (sum.warnings === 1 ? " warning" : " warnings")); }
        return parts.join(" · ");
    }

    function summaryOf(result) {
        var L = lint();
        return L ? L.summarize(result) : { errors: 0, warnings: 0 };
    }

    function renderStatus() {
        var btn = $("ss-js-lint");
        var toggle = $("ss-js-problems-toggle");
        var L = lint();
        var sum = summaryOf(lintResult);
        var state = !L ? "off" : (sum.errors ? "error" : (sum.warnings ? "warn" : "ok"));
        var text = problemSummaryText(sum, !!L && (!lintResult || lintResult.available !== false));
        if (btn) {
            btn.textContent = text;
            btn.setAttribute("data-state", state);
        }
        if (toggle) {
            toggle.setAttribute("data-state", state);
            var label = labelSpan(toggle);
            var count = sum.errors + sum.warnings;
            if (label) { label.textContent = count ? "Problems (" + count + ")" : "Problems"; }
        }
    }

    function renderProblemList() {
        var list = $("ss-js-problems-list");
        if (!list) { return; }
        var problems = currentProblems();
        list.innerHTML = "";
        if (!problems.length) {
            var empty = document.createElement("div");
            empty.className = "ss-js-problems__empty";
            empty.textContent = lint() ? "No problems found. This script will run." : "The checker could not load.";
            list.appendChild(empty);
            return;
        }
        problems.forEach(function (p) {
            var row = document.createElement("button");
            row.type = "button";
            row.className = "ss-js-problem ss-js-problem--" + (p.severity === "error" ? "error" : "warn");
            row.setAttribute("role", "listitem");
            row.innerHTML = '<span class="ss-js-problem__where">Ln ' + p.line + ", Col " + p.col + "</span>" +
                '<span class="ss-js-problem__msg">' + esc(p.message) + "</span>";
            row.addEventListener("click", function () { jumpTo(p.start, p.end); });
            list.appendChild(row);
        });
    }

    function updateChip() {
        var chip = $("ss-advanced-js-lint");
        if (!chip || popoutMode) { return; }
        var L = lint();
        if (!L) { chip.hidden = true; return; }
        var text = handlers.getText() || "";
        var result = lintedText === text ? lintResult : L.lint(text, lintCtx);
        var sum = L.summarize(result);
        var label = problemSummaryText(sum, result.available !== false);
        if (popout.alive) { label += " · editing in pop-out"; }
        chip.hidden = !text.trim() && !popout.alive;
        chip.textContent = label;
        chip.setAttribute("data-state", sum.errors ? "error" : (sum.warnings ? "warn" : "ok"));
    }

    function runLint() {
        lintTimer = null;
        var L = lint();
        var ta = $("ss-js-input");
        var text = ta && (open || popoutMode) ? ta.value : (handlers.getText() || "");
        if (L) {
            lintResult = L.lint(text, lintCtx);
            lintedText = text;
        } else {
            lintResult = null;
            lintedText = null;
        }
        renderStatus();
        renderProblemList();
        updateChip();
        if (refBuilt) { renderRefs(); }
        refresh();
        // The checker needs the template's names; fetch them in the background for the chip too.
        if (!popoutMode && text.trim() && ctxState !== "loading") { scheduleContext(); }
    }

    function scheduleLint(delay) {
        if (lintTimer) { clearTimeout(lintTimer); }
        lintTimer = setTimeout(runLint, delay == null ? LINT_DELAY_MS : delay);
    }

    function lintNow() {
        if (lintTimer) { clearTimeout(lintTimer); lintTimer = null; }
        runLint();
        return lintResult;
    }

    /* ------------------------------------------------------------- template context */

    function setContext(ctx) {
        lintCtx = ctx || null;
        ctxState = ctx ? "ready" : ctxState;
        if (popout.alive) { relay({ type: "context", ctx: lintCtx }); }
        scheduleLint(0);
        if (refBuilt) { renderRefs(); }
    }

    function loadContext(force) {
        if (popoutMode) {
            if (force) { relay({ type: "need-context" }); }
            return;
        }
        var L = lint();
        if (!L || typeof handlers.getContext !== "function") { return; }
        var key = String(handlers.contextKey() || "");
        if (!force && key === ctxKey && ctxState !== "error") { return; }
        ctxKey = key;
        ctxState = "loading";
        renderRefStatus();
        var mine = ++ctxRequest;
        var pending;
        try { pending = handlers.getContext(); } catch (err) { pending = null; }
        if (!pending || typeof pending.then !== "function") {
            ctxState = "error";
            renderRefStatus();
            return;
        }
        pending.then(function (data) {
            if (mine !== ctxRequest) { return; }
            setContext(L.analyzeTemplate((data && data.scripts) || []));
            renderRefStatus();
        }, function () {
            if (mine !== ctxRequest) { return; }
            ctxState = "error";
            renderRefStatus();
        });
    }

    function scheduleContext() {
        if (ctxTimer) { clearTimeout(ctxTimer); }
        ctxTimer = setTimeout(function () {
            ctxTimer = null;
            loadContext(false);
        }, CONTEXT_DELAY_MS);
    }

    /* ------------------------------------------------------------- reference panel */

    function renderRefStatus() {
        var el = $("ss-js-ref-status");
        if (!el) { return; }
        var msg = "";
        if (!lint()) {
            msg = "Template functions need the checker, which did not load. Snippets still work.";
        } else if (ctxState === "loading") {
            msg = "Reading the template's functions…";
        } else if (ctxState === "error") {
            msg = "Couldn't read the template's functions. Showing snippets only.";
        } else if (lintCtx && lintCtx.errors && lintCtx.errors.length) {
            msg = "Some template code could not be read; its list may be incomplete.";
        }
        el.textContent = msg;
        el.hidden = !msg;
    }

    function callText(fn) {
        var args = (fn.params || []).map(function (p) {
            return String(p).replace(/^\.\.\./, "").replace(/\?$/, "");
        });
        return { text: fn.name + "(" + args.join(", ") + ")", argsStart: fn.name.length + 1, argsLen: args.join(", ").length };
    }

    function insertCall(fn) {
        var ta = $("ss-js-input");
        if (!ta) { return; }
        ta.focus();
        var start = ta.selectionStart || 0;
        var call = callText(fn);
        insertText(call.text);
        if (call.argsLen) {
            setCaret(start + call.argsStart, start + call.argsStart + call.argsLen);
        }
    }

    function refButton(label, title, onClick, extraClass) {
        var btn = document.createElement("button");
        btn.type = "button";
        if (extraClass) { btn.className = extraClass; }
        btn.innerHTML = label;
        if (title) { btn.title = title; }
        btn.addEventListener("click", onClick);
        return btn;
    }

    function fnTitle(fn) {
        var where = fn.group === "user" ? "Your code" : (fn.source || "This template");
        return fn.name + "(" + (fn.params || []).join(", ") + ")" +
            (fn.doc ? "\n\n" + fn.doc : "") + "\n\n" + where + (fn.line ? ", line " + fn.line : "");
    }

    function matches(q, fn) {
        if (!q) { return true; }
        return (fn.name + " " + (fn.doc || "")).toLowerCase().indexOf(q) !== -1;
    }

    function addGroup(list, id, label, items, openByDefault) {
        if (!items.length) { return; }
        var details = document.createElement("details");
        details.className = "ss-js-ref__group";
        details.setAttribute("data-group", id);
        details.open = openByDefault;
        var summary = document.createElement("summary");
        summary.innerHTML = esc(label) + ' <span class="ss-js-ref__count">' + items.length + "</span>";
        details.appendChild(summary);
        items.forEach(function (node) { details.appendChild(node); });
        list.appendChild(details);
    }

    function renderRefs() {
        var list = $("ss-js-ref-list");
        if (!list) { return; }
        refBuilt = true;
        var search = $("ss-js-ref-search");
        var q = search ? String(search.value || "").trim().toLowerCase() : "";
        var L = lint();
        var openGroups = {};
        Array.prototype.forEach.call(list.querySelectorAll("details[data-group]"), function (d) {
            openGroups[d.getAttribute("data-group")] = d.open;
        });
        list.innerHTML = "";

        var snippetButtons = REFS.filter(function (row) {
            return !q || row.name.toLowerCase().indexOf(q) !== -1 || row.snippet.toLowerCase().indexOf(q) !== -1;
        }).map(function (row) {
            return refButton(esc(row.name), row.snippet.trim(), function () {
                var ta = $("ss-js-input");
                if (ta) { ta.focus(); }
                insertText(row.snippet);
            }, "ss-js-ref__snippet");
        });

        var all = [];
        if (lintCtx && lintCtx.functions) { all = all.concat(lintCtx.functions); }
        if (lintResult && lintResult.functions && lintedText === (($("ss-js-input") || {}).value)) {
            all = all.concat(lintResult.functions);
        }
        var groups = L ? L.GROUPS : [];
        groups.forEach(function (g) {
            var items = all.filter(function (fn) { return fn.group === g.id && matches(q, fn); });
            items.sort(function (a, b) {
                return (a.internal === b.internal ? 0 : (a.internal ? 1 : -1)) || a.name.localeCompare(b.name);
            });
            var nodes = items.map(function (fn) {
                var sig = '<span class="ss-js-ref__sig">' + esc(fn.name) + "(<i>" +
                    esc((fn.params || []).join(", ")) + "</i>)</span>" +
                    (fn.doc ? '<span class="ss-js-ref__doc">' + esc(fn.doc) + "</span>" : "");
                return refButton(sig, fnTitle(fn), function () { insertCall(fn); },
                    "ss-js-ref__fn" + (fn.internal ? " ss-js-ref__fn--internal" : ""));
            });
            var wasOpen = hasOwnKey(openGroups, g.id) ? openGroups[g.id] : (g.id !== "data" && g.id !== "library");
            addGroup(list, g.id, g.label, nodes, q ? true : wasOpen);
        });
        var hadSnippets = hasOwnKey(openGroups, "snippets") ? openGroups.snippets : true;
        addGroup(list, "snippets", "Snippets", snippetButtons, q ? true : hadSnippets);
        if (!list.childNodes.length) {
            var none = document.createElement("div");
            none.className = "ss-js-ref__empty";
            none.textContent = q ? "No functions match “" + q + "”." : "No functions found.";
            list.appendChild(none);
        }
        renderRefStatus();
    }

    function hasOwnKey(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    // Kept for callers/tests: build the list once.
    function buildRefs() {
        var list = $("ss-js-ref-list");
        if (!list || list.childNodes.length) { return; }
        renderRefs();
    }

    /* ------------------------------------------------------------- editor core */

    function refresh() {
        var ta = $("ss-js-input");
        var hi = $("ss-js-highlight");
        var gutter = $("ss-js-gutter");
        var linehl = $("ss-js-linehl");
        var pos = $("ss-js-pos");
        var count = $("ss-js-count");
        if (!ta || !hi) { return; }
        var text = ta.value;
        hi.innerHTML = highlightJs(text) + (text.slice(-1) === "\n" || text === "" ? "<br>" : "");
        var lineCount = text.split("\n").length;
        var problems = currentProblems();
        if (gutter) { renderGutter(lineCount, problems); }
        renderMarks(text, problems);
        var caret = lineCol(text, ta.selectionStart || 0);
        linehl.style.top = (PAD_TOP + (caret.line - 1) * LINE_PX) + "px";
        if (pos) { pos.textContent = "Ln " + caret.line + ", Col " + caret.col; }
        if (count) { count.textContent = text.length + " chars"; }
    }

    function emitChange() {
        var ta = $("ss-js-input");
        if (!ta) { return; }
        handlers.setText(ta.value);
        scheduleLint();
        if (commitTimer) { clearTimeout(commitTimer); }
        commitTimer = setTimeout(function () {
            commitTimer = null;
            handlers.commit();
        }, 600);
    }

    function setCaret(start, end) {
        var ta = $("ss-js-input");
        ta.selectionStart = start;
        ta.selectionEnd = end == null ? start : end;
        refresh();
    }

    function scrollToIndex(index) {
        var ta = $("ss-js-input");
        var scroller = $("ss-js-scroller");
        if (!ta || !scroller) { return; }
        var loc = lineCol(ta.value, index);
        scroller.scrollTop = Math.max(0, (loc.line - 3) * LINE_PX);
    }

    function jumpTo(start, end) {
        var ta = $("ss-js-input");
        if (!ta) { return; }
        var max = ta.value.length;
        ta.focus();
        setCaret(Math.min(start, max), Math.min(end == null ? start : end, max));
        scrollToIndex(start);
    }

    function insertText(text) {
        var ta = $("ss-js-input");
        var start = ta.selectionStart || 0;
        var end = ta.selectionEnd || 0;
        var next = ta.value.slice(0, start) + text + ta.value.slice(end);
        ta.value = next;
        setCaret(start + text.length);
        emitChange();
    }

    function indentOf(line) {
        var m = /^\s*/.exec(line);
        return m ? m[0] : "";
    }

    function onKeyDown(ev) {
        var ta = $("ss-js-input");
        var start = ta.selectionStart || 0;
        var end = ta.selectionEnd || 0;
        var value = ta.value;
        var key = ev.key;

        if ((ev.metaKey || ev.ctrlKey) && key.toLowerCase() === "f") {
            ev.preventDefault();
            showFind(true);
            return;
        }
        if (key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            var findbar = $("ss-js-findbar");
            if (findbar && !findbar.hidden) {
                findbar.hidden = true;
                ta.focus();
                return;
            }
            if (!popoutMode) { closeDialog(); }
            return;
        }
        if (key === "Tab") {
            ev.preventDefault();
            if (start !== end && value.slice(start, end).indexOf("\n") !== -1) {
                var blockStart = value.lastIndexOf("\n", start - 1) + 1;
                var blockEnd = value.indexOf("\n", end);
                if (blockEnd < 0) { blockEnd = value.length; }
                var block = value.slice(blockStart, blockEnd);
                var lines = block.split("\n");
                var replaced = lines.map(function (line) {
                    if (ev.shiftKey) { return line.replace(/^( {1,2}|\t)/, ""); }
                    return "  " + line;
                }).join("\n");
                ta.value = value.slice(0, blockStart) + replaced + value.slice(blockEnd);
                setCaret(blockStart, blockStart + replaced.length);
                emitChange();
                return;
            }
            if (ev.shiftKey) {
                var ls = value.lastIndexOf("\n", start - 1) + 1;
                if (value.slice(ls, ls + 2) === "  ") {
                    ta.value = value.slice(0, ls) + value.slice(ls + 2);
                    setCaret(Math.max(ls, start - 2));
                    emitChange();
                }
                return;
            }
            insertText("  ");
            return;
        }
        if (key === "Enter") {
            ev.preventDefault();
            var lineStart = value.lastIndexOf("\n", start - 1) + 1;
            var current = value.slice(lineStart, start);
            var indent = indentOf(current);
            if (/\{\s*$/.test(current)) { indent += "  "; }
            insertText("\n" + indent);
            return;
        }
        if ((CLOSERS[key] || PAIRS[key] === key) && start === end && value.charAt(start) === key) {
            ev.preventDefault();
            setCaret(start + 1);
            return;
        }
        if (PAIRS[key] && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
            ev.preventDefault();
            var closer = PAIRS[key];
            var selected = value.slice(start, end);
            var next = value.slice(0, start) + key + selected + closer + value.slice(end);
            ta.value = next;
            setCaret(start + 1, start + 1 + selected.length);
            emitChange();
            return;
        }
        if (key === "Backspace" && start === end && start > 0) {
            var prev = value.charAt(start - 1);
            var nxt = value.charAt(start);
            if (PAIRS[prev] && PAIRS[prev] === nxt) {
                ev.preventDefault();
                ta.value = value.slice(0, start - 1) + value.slice(start + 1);
                setCaret(start - 1);
                emitChange();
            }
        }
    }

    // Show the problem under the mouse as the textarea's tooltip.
    function onHover(ev) {
        var ta = $("ss-js-input");
        if (!ta) { return; }
        var problems = currentProblems();
        if (!problems.length) { ta.removeAttribute("title"); return; }
        var line = Math.floor(((ev.offsetY || 0) - PAD_TOP) / LINE_PX) + 1;
        var vcol = Math.floor(((ev.offsetX || 0) - PAD_LEFT) / measureCharWidth()) + 1;
        var lines = ta.value.split("\n");
        var hit = null;
        problems.forEach(function (p) {
            if (hit || p.line !== line) { return; }
            var lineText = lines[p.line - 1] || "";
            var endCol = p.endLine === p.line ? p.endCol : lineText.length + 1;
            var a = visualCol(lineText, p.col) + 1;
            var b = Math.max(a + 1, visualCol(lineText, endCol) + 1);
            if (vcol >= a && vcol < b) { hit = p; }
        });
        if (hit) { ta.title = hit.message; } else { ta.removeAttribute("title"); }
    }

    function showFind(focus) {
        var bar = $("ss-js-findbar");
        if (!bar) { return; }
        bar.hidden = false;
        if (focus) {
            var input = $("ss-js-find");
            if (input) { input.focus(); input.select(); }
        }
    }

    function findMatch(dir) {
        var ta = $("ss-js-input");
        var q = ($("ss-js-find") && $("ss-js-find").value) || "";
        if (!q) { return false; }
        var text = ta.value;
        var from = dir < 0
            ? (ta.selectionStart || 0) - 1
            : (ta.selectionEnd || 0);
        var idx = dir < 0
            ? (from < 0 ? -1 : text.lastIndexOf(q, from))
            : text.indexOf(q, from);
        if (idx < 0) {
            idx = dir < 0 ? text.lastIndexOf(q) : text.indexOf(q);
        }
        if (idx < 0) { return false; }
        findFrom = idx + q.length;
        ta.focus();
        setCaret(idx, idx + q.length);
        scrollToIndex(idx);
        return true;
    }

    function replaceOne() {
        var ta = $("ss-js-input");
        var q = ($("ss-js-find") && $("ss-js-find").value) || "";
        var rep = ($("ss-js-replace") && $("ss-js-replace").value) || "";
        if (!q) { return; }
        var sel = ta.value.slice(ta.selectionStart || 0, ta.selectionEnd || 0);
        if (sel !== q) {
            if (!findMatch(1)) { return; }
            sel = ta.value.slice(ta.selectionStart || 0, ta.selectionEnd || 0);
            if (sel !== q) { return; }
        }
        var start = ta.selectionStart || 0;
        ta.value = ta.value.slice(0, start) + rep + ta.value.slice(start + q.length);
        setCaret(start, start + rep.length);
        emitChange();
    }

    function replaceAll() {
        var ta = $("ss-js-input");
        var q = ($("ss-js-find") && $("ss-js-find").value) || "";
        var rep = ($("ss-js-replace") && $("ss-js-replace").value) || "";
        if (!q) { return; }
        var parts = ta.value.split(q);
        if (parts.length < 2) { return; }
        ta.value = parts.join(rep);
        refresh();
        emitChange();
    }

    function openDialog() {
        var dialog = $("ss-js-dialog");
        var ta = $("ss-js-input");
        if (!dialog || !ta) { return; }
        if (popout.alive && !popoutMode) {
            handlers.notify({
                type: "info",
                title: "Advanced JS is open in its own window",
                message: "Edit it there, or close that window to edit here."
            });
            relay({ type: "focus" });
            return;
        }
        ta.value = handlers.getText() || "";
        dialog.hidden = false;
        open = true;
        refresh();
        lintNow();
        loadContext(false);
        ta.focus();
    }

    function flushCommit() {
        if (commitTimer) {
            clearTimeout(commitTimer);
            commitTimer = null;
            handlers.commit();
        }
    }

    function closeDialog() {
        var dialog = $("ss-js-dialog");
        flushCommit();
        if (dialog) { dialog.hidden = true; }
        open = false;
        var small = $("ss-advanced-js");
        if (small) { small.focus(); }
    }

    function togglePanel(id) {
        var panel = $(id);
        if (!panel) { return null; }
        panel.hidden = !panel.hidden;
        return panel;
    }

    /* ------------------------------------------------------------- pop-out sync */

    function makeChannel() {
        var bytes = [];
        try {
            var arr = new Uint8Array(12);
            window.crypto.getRandomValues(arr);
            bytes = Array.prototype.slice.call(arr);
        } catch (err) {
            for (var i = 0; i < 12; i++) { bytes.push(Math.floor(Math.random() * 256)); }
        }
        return bytes.map(function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
    }

    function relay(msg) {
        if (!popout.socket || !popout.channel) { return; }
        try {
            popout.socket.emit("spore_js_relay", { channel: popout.channel, msg: msg });
        } catch (err) { /* socket gone; the heartbeat notices */ }
    }

    function joinChannel() {
        if (popout.socket && popout.channel) {
            popout.socket.emit("spore_js_join", { channel: popout.channel });
        }
    }

    function listen(socket) {
        if (popout.listening || !socket || typeof socket.on !== "function") { return; }
        popout.listening = true;
        socket.on("spore_js_relay", function (data) {
            if (!data || data.channel !== popout.channel || !data.msg) { return; }
            if (popoutMode) { onPopoutMessage(data.msg); } else { onMainMessage(data.msg); }
        });
        socket.on("connect", function () {
            joinChannel();
            if (popoutMode) { relay({ type: "hello" }); }
        });
    }

    function sendState() {
        popout.template = handlers.templateName() || "";
        relay({ type: "state", text: handlers.getText() || "", template: popout.template, ctx: lintCtx });
    }

    function onMainMessage(msg) {
        switch (msg.type) {
            case "hello":
                popout.alive = true;
                popout.lastPing = Date.now();
                if (popout.connectTimer) { clearTimeout(popout.connectTimer); popout.connectTimer = null; }
                sendState();
                loadContext(false);
                updateChip();
                break;
            case "edit":
                if (typeof msg.text === "string") {
                    popout.lastPing = Date.now();
                    handlers.setText(msg.text);
                    scheduleLint();
                }
                break;
            case "commit":
                handlers.commit();
                break;
            case "ping":
                popout.lastPing = Date.now();
                if (!popout.alive) { popout.alive = true; sendState(); updateChip(); }
                break;
            case "need-context":
                loadContext(true);
                relay({ type: "context", ctx: lintCtx });
                break;
            case "bye":
                popout.alive = false;
                updateChip();
                break;
            default:
                break;
        }
    }

    // Inside the app window Studio is an iframe whose window.open is ignored.
    function embeddedInApp() {
        try { return window.self !== window.top; } catch (err) { return true; }
    }

    // Ask the overlay server to open the pop-out in the default browser.
    function openViaServer(url) {
        var fail = function () {
            if (popout.connectTimer) { clearTimeout(popout.connectTimer); popout.connectTimer = null; }
            handlers.notify({
                type: "error",
                title: "Couldn't open the pop-out editor",
                message: "Mycelian couldn't open your browser. Paste this address into any browser on this " +
                    "computer: " + window.location.origin + url
            });
        };
        if (typeof window.fetch !== "function") { fail(); return; }
        window.fetch("/api/spore-studio/js-popout/open", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ channel: popout.channel, template: handlers.templateName() || "" })
        }).then(function (res) {
            if (!res || !res.ok) { fail(); }
        }, fail);
    }

    function startPopout() {
        var socket = handlers.getSocket();
        if (!socket) {
            handlers.notify({
                type: "error",
                title: "Couldn't pop out the editor",
                message: "Spore Studio is not connected to the overlay server."
            });
            return;
        }
        flushCommit();
        if (open) { closeDialog(); }
        popout.socket = socket;
        popout.channel = makeChannel();
        popout.alive = false;
        listen(socket);
        joinChannel();
        var url = "/_spore_studio_js_editor?channel=" + encodeURIComponent(popout.channel) +
            "&template=" + encodeURIComponent(handlers.templateName() || "");
        var opened = null;
        if (!embeddedInApp()) {
            try {
                opened = window.open(url, "ss-js-popout-" + popout.channel, "popup,width=1180,height=820");
            } catch (err) { opened = null; }
        }
        if (!opened) { openViaServer(url); }
        if (popout.connectTimer) { clearTimeout(popout.connectTimer); }
        popout.connectTimer = setTimeout(function () {
            popout.connectTimer = null;
            if (popout.alive) { return; }
            handlers.notify({
                type: "warning",
                title: "The pop-out editor didn't connect",
                message: "No editor window connected within 10 seconds. Look for a new browser window or " +
                    "tab; Advanced JS is still editable here."
            });
        }, POPOUT_CONNECT_MS);
        if (!popout.pingTimer) {
            popout.pingTimer = setInterval(function () {
                if (popout.alive && Date.now() - popout.lastPing > POPOUT_STALE_MS) {
                    popout.alive = false;
                    updateChip();
                }
            }, POPOUT_PING_MS);
        }
        updateChip();
    }

    function showPopoutBanner(text) {
        var panel = document.querySelector(".ss-js-dialog__panel");
        if (!panel) { return; }
        var banner = $("ss-js-popout-banner");
        if (!banner) {
            banner = document.createElement("div");
            banner.id = "ss-js-popout-banner";
            banner.className = "ss-js-popout-banner";
            panel.insertBefore(banner, panel.children[1] || null);
        }
        banner.textContent = text;
        banner.hidden = !text;
    }

    function setPopoutTitle(template) {
        var title = $("ss-js-dialog-title");
        var label = "Advanced JS" + (template ? " — " + template : "");
        if (title) {
            var span = title.querySelector(".ss-js-dialog__name");
            if (!span) {
                span = document.createElement("span");
                span.className = "ss-js-dialog__name";
                title.appendChild(span);
            }
            span.textContent = template ? template : "";
        }
        document.title = label;
    }

    function onPopoutMessage(msg) {
        var ta = $("ss-js-input");
        switch (msg.type) {
            case "state":
                popout.closed = false;
                showPopoutBanner("");
                if (ta) { ta.readOnly = false; }
                if (typeof msg.template === "string") { setPopoutTitle(msg.template); }
                if (msg.ctx) { lintCtx = msg.ctx; }
                applyRemoteText(msg.text);
                scheduleLint(0);
                break;
            case "text":
                applyRemoteText(msg.text);
                scheduleLint();
                break;
            case "context":
                lintCtx = msg.ctx || null;
                ctxState = lintCtx ? "ready" : "error";
                scheduleLint(0);
                break;
            case "focus":
                try { window.focus(); } catch (err) { /* ignore */ }
                break;
            case "closed":
                popout.closed = true;
                if (ta) { ta.readOnly = true; }
                showPopoutBanner("Spore Studio was closed or reloaded. This window is no longer in sync; " +
                    "copy anything you need, then close it.");
                break;
            default:
                break;
        }
    }

    function applyRemoteText(text) {
        var ta = $("ss-js-input");
        if (!ta || typeof text !== "string" || ta.value === text) { return; }
        var start = ta.selectionStart || 0;
        var end = ta.selectionEnd || 0;
        ta.value = text;
        ta.selectionStart = Math.min(start, text.length);
        ta.selectionEnd = Math.min(end, text.length);
        refresh();
    }

    function initPopout() {
        var params = {};
        String(window.location.search || "").replace(/^\?/, "").split("&").forEach(function (kv) {
            if (!kv) { return; }
            var i = kv.indexOf("=");
            var k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i));
            params[k] = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " "));
        });
        popout.channel = params.channel || "";
        setPopoutTitle(params.template || "");
        var dialog = $("ss-js-dialog");
        if (dialog) { dialog.hidden = false; }
        open = true;
        var done = $("ss-js-done");
        if (done) {
            var label = labelSpan(done);
            if (label) { label.textContent = "Close"; }
            done.title = "Close this window (your edits are already in Spore Studio)";
        }
        var pop = $("ss-js-popout");
        if (pop) { pop.hidden = true; }
        var sendTimerFn = function () {
            popout.sendTimer = null;
            var ta = $("ss-js-input");
            if (ta && !popout.closed) { relay({ type: "edit", text: ta.value }); }
        };
        handlers.getText = function () { var ta = $("ss-js-input"); return ta ? ta.value : ""; };
        handlers.setText = function () {
            if (popout.sendTimer) { clearTimeout(popout.sendTimer); }
            popout.sendTimer = setTimeout(sendTimerFn, POPOUT_EDIT_DELAY_MS);
        };
        handlers.commit = function () {
            if (popout.sendTimer) { clearTimeout(popout.sendTimer); sendTimerFn(); }
            relay({ type: "commit" });
        };
        if (!popout.channel) {
            showPopoutBanner("This window was opened without a link to Spore Studio. Use Pop out in the " +
                "Advanced JS editor instead.");
            return;
        }
        var socket = null;
        try {
            var connect = (window.MycelianOverlay && window.MycelianOverlay.connect) || (window.io && window.io.connect);
            socket = connect ? connect("http://" + document.domain + ":" + location.port) : null;
        } catch (err) { socket = null; }
        if (!socket) {
            showPopoutBanner("Couldn't connect to the overlay server, so this window can't sync with Spore Studio.");
            return;
        }
        popout.socket = socket;
        listen(socket);
        socket.on("theme_update", applyTheme);
        socket.on("connect", function () { socket.emit("get_current_theme"); });
        if (socket.connected) {
            joinChannel();
            relay({ type: "hello" });
            socket.emit("get_current_theme");
        }
        popout.pingTimer = setInterval(function () { relay({ type: "ping" }); }, POPOUT_PING_MS);
        window.addEventListener("beforeunload", function () {
            handlers.commit();
            relay({ type: "bye" });
        });
        refresh();
        runLint();
    }

    function applyTheme(data) {
        if (!data || !data.css) { return; }
        var style = $("mycelian-theme-vars");
        if (!style) {
            style = document.createElement("style");
            style.id = "mycelian-theme-vars";
            document.head.insertBefore(style, document.head.firstChild);
        }
        style.textContent = data.css;
        if (data.theme_type) { document.documentElement.dataset.themeType = data.theme_type; }
    }

    /* ------------------------------------------------------------- wiring */

    function bindUi() {
        popoutMode = !!(document.body && document.body.getAttribute("data-ss-js-popout") === "1");
        var expand = $("ss-btn-js-expand");
        var ta = $("ss-js-input");
        var scroller = $("ss-js-scroller");
        var gutter = $("ss-js-gutter");
        if (expand) { expand.addEventListener("click", openDialog); }
        if ($("ss-js-done")) {
            $("ss-js-done").addEventListener("click", function () {
                if (popoutMode) {
                    handlers.commit();
                    relay({ type: "bye" });
                    window.close();
                    return;
                }
                closeDialog();
            });
        }
        if ($("ss-js-find-toggle")) {
            $("ss-js-find-toggle").addEventListener("click", function () { showFind(true); });
        }
        if ($("ss-js-ref-toggle")) {
            $("ss-js-ref-toggle").addEventListener("click", function () {
                var ref = $("ss-js-ref");
                if (!ref) { return; }
                buildRefs();
                ref.hidden = !ref.hidden;
                if (!ref.hidden) {
                    loadContext(false);
                    var search = $("ss-js-ref-search");
                    if (search && search.value) { search.focus(); }
                }
            });
        }
        if ($("ss-js-ref-search")) {
            $("ss-js-ref-search").addEventListener("input", renderRefs);
            $("ss-js-ref-search").addEventListener("keydown", function (ev) {
                if (ev.key === "Escape") {
                    ev.preventDefault();
                    ev.stopPropagation();
                    ev.target.value = "";
                    renderRefs();
                    if (ta) { ta.focus(); }
                }
            });
        }
        if ($("ss-js-ref-reload")) {
            $("ss-js-ref-reload").addEventListener("click", function () { loadContext(true); });
        }
        var showProblems = function () {
            var panel = togglePanel("ss-js-problems");
            if (panel && !panel.hidden) { renderProblemList(); }
        };
        if ($("ss-js-problems-toggle")) { $("ss-js-problems-toggle").addEventListener("click", showProblems); }
        if ($("ss-js-lint")) { $("ss-js-lint").addEventListener("click", showProblems); }
        if ($("ss-js-popout")) { $("ss-js-popout").addEventListener("click", startPopout); }
        var chip = $("ss-advanced-js-lint");
        if (chip) {
            chip.addEventListener("click", function () {
                if (popout.alive) { relay({ type: "focus" }); return; }
                openDialog();
                var panel = $("ss-js-problems");
                if (panel && open) { panel.hidden = false; renderProblemList(); }
            });
        }
        if ($("ss-js-find-next")) { $("ss-js-find-next").addEventListener("click", function () { findMatch(1); }); }
        if ($("ss-js-find-prev")) { $("ss-js-find-prev").addEventListener("click", function () { findMatch(-1); }); }
        if ($("ss-js-replace-one")) { $("ss-js-replace-one").addEventListener("click", replaceOne); }
        if ($("ss-js-replace-all")) { $("ss-js-replace-all").addEventListener("click", replaceAll); }
        var findInput = $("ss-js-find");
        if (findInput) {
            findInput.addEventListener("keydown", function (ev) {
                if (ev.key === "Enter") { ev.preventDefault(); findMatch(ev.shiftKey ? -1 : 1); }
                if (ev.key === "Escape") { ev.preventDefault(); $("ss-js-findbar").hidden = true; ta.focus(); }
            });
        }
        if (ta) {
            ta.addEventListener("keydown", onKeyDown);
            ta.addEventListener("input", function () { refresh(); emitChange(); });
            ta.addEventListener("click", refresh);
            ta.addEventListener("keyup", refresh);
            ta.addEventListener("mousemove", onHover);
        }
        if (scroller && gutter) {
            scroller.addEventListener("scroll", function () {
                gutter.scrollTop = scroller.scrollTop;
            });
        }
        var dialog = $("ss-js-dialog");
        if (dialog && !popoutMode) {
            dialog.addEventListener("mousedown", function (ev) {
                if (ev.target === dialog) { closeDialog(); }
            });
        }
        if (!popoutMode) {
            window.addEventListener("beforeunload", function () {
                if (popout.channel) { relay({ type: "closed" }); }
            });
        }
        renderStatus();
        if (popoutMode) { initPopout(); }
    }

    window.SporeJsEditor = {
        bind: function (next) {
            Object.keys(handlers).forEach(function (k) {
                if (next && typeof next[k] === "function") { handlers[k] = next[k]; }
            });
            scheduleLint();
        },
        isOpen: function () { return open; },
        isPoppedOut: function () { return popout.alive; },
        setValue: function (text) {
            var ta = $("ss-js-input");
            if (popout.alive && !popoutMode) {
                var tpl = handlers.templateName() || "";
                if (tpl !== popout.template) {
                    sendState();
                } else {
                    relay({ type: "text", text: text });
                }
            }
            scheduleLint();
            if (!open || !ta || ta.value === text) { return; }
            ta.value = text;
            refresh();
        },
        // The template (elements, data, alert system) changed: re-read its functions soon.
        modelChanged: function () {
            if (open || popout.alive || String(handlers.getText() || "").trim()) { scheduleContext(); }
        },
        // Lint the current Advanced JS right away (used before saving).
        check: function () {
            var L = lint();
            if (!L) { return null; }
            var text = handlers.getText() || "";
            return lintedText === text && lintResult ? lintResult : L.lint(text, lintCtx);
        },
        open: openDialog
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindUi);
    } else {
        bindUi();
    }
})();

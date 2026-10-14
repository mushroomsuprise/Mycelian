/* Dependency-free Advanced JS editor for Spore Studio. */
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

    var handlers = {
        getText: function () { return ""; },
        setText: function () {},
        commit: function () {}
    };
    var open = false;
    var commitTimer = null;
    var findFrom = 0;

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
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
        var nums = [];
        var i;
        for (i = 1; i <= lineCount; i++) { nums.push(String(i)); }
        gutter.textContent = nums.join("\n");
        var caret = lineCol(text, ta.selectionStart || 0);
        linehl.style.top = (8 + (caret.line - 1) * 20) + "px";
        if (pos) { pos.textContent = "Ln " + caret.line + ", Col " + caret.col; }
        if (count) { count.textContent = text.length + " chars"; }
    }

    function emitChange() {
        var ta = $("ss-js-input");
        if (!ta) { return; }
        handlers.setText(ta.value);
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
            closeDialog();
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
        if ((CLOSERS[key] || PAIRS[key]) && start === end && value.charAt(start) === key) {
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
            ? text.lastIndexOf(q, Math.max(0, from))
            : text.indexOf(q, from);
        if (idx < 0) {
            idx = dir < 0 ? text.lastIndexOf(q) : text.indexOf(q);
        }
        if (idx < 0) { return false; }
        findFrom = idx + q.length;
        ta.focus();
        setCaret(idx, idx + q.length);
        var scroller = $("ss-js-scroller");
        if (scroller) {
            var loc = lineCol(text, idx);
            scroller.scrollTop = Math.max(0, (loc.line - 3) * 20);
        }
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
        ta.value = handlers.getText() || "";
        dialog.hidden = false;
        open = true;
        refresh();
        ta.focus();
    }

    function closeDialog() {
        var dialog = $("ss-js-dialog");
        if (commitTimer) {
            clearTimeout(commitTimer);
            commitTimer = null;
            handlers.commit();
        }
        if (dialog) { dialog.hidden = true; }
        open = false;
        var small = $("ss-advanced-js");
        if (small) { small.focus(); }
    }

    function buildRefs() {
        var list = $("ss-js-ref-list");
        if (!list || list.childNodes.length) { return; }
        REFS.forEach(function (row) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = row.name;
            btn.addEventListener("click", function () {
                var ta = $("ss-js-input");
                if (ta) { ta.focus(); }
                insertText(row.snippet);
            });
            list.appendChild(btn);
        });
    }

    function bindUi() {
        var expand = $("ss-btn-js-expand");
        var ta = $("ss-js-input");
        var scroller = $("ss-js-scroller");
        var gutter = $("ss-js-gutter");
        if (expand) { expand.addEventListener("click", openDialog); }
        if ($("ss-js-done")) { $("ss-js-done").addEventListener("click", closeDialog); }
        if ($("ss-js-find-toggle")) {
            $("ss-js-find-toggle").addEventListener("click", function () { showFind(true); });
        }
        if ($("ss-js-ref-toggle")) {
            $("ss-js-ref-toggle").addEventListener("click", function () {
                var ref = $("ss-js-ref");
                if (!ref) { return; }
                buildRefs();
                ref.hidden = !ref.hidden;
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
        }
        if (scroller && gutter) {
            scroller.addEventListener("scroll", function () {
                gutter.scrollTop = scroller.scrollTop;
            });
        }
        var dialog = $("ss-js-dialog");
        if (dialog) {
            dialog.addEventListener("mousedown", function (ev) {
                if (ev.target === dialog) { closeDialog(); }
            });
        }
    }

    window.SporeJsEditor = {
        bind: function (next) {
            handlers.getText = next.getText || handlers.getText;
            handlers.setText = next.setText || handlers.setText;
            handlers.commit = next.commit || handlers.commit;
        },
        isOpen: function () { return open; },
        setValue: function (text) {
            var ta = $("ss-js-input");
            if (!open || !ta || ta.value === text) { return; }
            ta.value = text;
            refresh();
        },
        open: openDialog
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindUi);
    } else {
        bindUi();
    }
})();

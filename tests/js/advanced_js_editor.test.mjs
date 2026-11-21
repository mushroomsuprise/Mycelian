// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/advanced_js_editor.js on the real editor.html dialog markup:
// syntax highlighter, gutter/status, editing keys (Tab/Shift+Tab, Enter indent, auto-pairs,
// overtype, Backspace), find/replace, the reference list, open/close and the debounced commit.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, fire, settle, readSource as readSourceRel } from "./helpers/env.mjs";
import { makeStudioWindow, fireDomReady } from "./helpers/studio.mjs";

function setup(text = "") {
    const { win, internals } = makeStudioWindow({ scripts: ["advanced_js_editor.js"] });
    fireDomReady(win);
    const J = internals.advanced_js_editor;
    const doc = win.document;
    const store = { text, commits: 0, sets: [] };
    win.SporeJsEditor.bind({
        getText: () => store.text,
        setText: (t) => { store.text = t; store.sets.push(t); },
        commit: () => { store.commits++; },
    });
    const ta = doc.getElementById("ss-js-input");
    return { win, J, doc, ta, store, E: win.SporeJsEditor, clock: win.__clock };
}
function key(s, k, extra = {}) {
    return fire(s.win, s.ta, "keydown", Object.assign({ key: k }, extra));
}
function caret(s, a, b) { s.ta.selectionStart = a; s.ta.selectionEnd = b === undefined ? a : b; }
function state(s) { return [s.ta.value, s.ta.selectionStart, s.ta.selectionEnd]; }

/* ------------------------------------------------------------- highlighter */

test("highlightJs marks comments, strings, numbers, keywords and calls, escaping HTML", () => {
    const { J } = setup();
    const out = J.highlightJs('var x = 10; // <note>\nfoo("a\\"b") /* c */ `t\nu` x2 0x1F');
    assert.equal(out,
        '<span class="ss-js-tok-kw">var</span> x = <span class="ss-js-tok-num">10</span>; ' +
        '<span class="ss-js-tok-com">// &lt;note&gt;</span>\n' +
        '<span class="ss-js-tok-fn">foo</span>(<span class="ss-js-tok-str">"a\\"b"</span>) ' +
        '<span class="ss-js-tok-com">/* c */</span> <span class="ss-js-tok-str">`t\nu`</span> x2 ' +
        '<span class="ss-js-tok-num">0x1F</span>');
});

test("highlightJs: unterminated comment / string stop at the end or newline", () => {
    const { J } = setup();
    assert.equal(J.highlightJs("/* open"), '<span class="ss-js-tok-com">/* open</span>');
    assert.equal(J.highlightJs("// end"), '<span class="ss-js-tok-com">// end</span>');
    assert.equal(J.highlightJs("'abc\nd"), '<span class="ss-js-tok-str">\'abc</span>\nd');
    assert.equal(J.highlightJs("a & b < c"), "a &amp; b &lt; c");
    assert.equal(J.highlightJs("if (x) {}"), '<span class="ss-js-tok-kw">if</span> (x) {}');
    assert.equal(J.highlightJs("call  ()"), '<span class="ss-js-tok-fn">call</span>  ()');
    assert.equal(J.highlightJs(""), "");
});

test("lineCol and indentOf", () => {
    const { J } = setup();
    assert.deepEqual(JSON.parse(JSON.stringify(J.lineCol("ab\ncd\nef", 4))), { line: 2, col: 2 });
    assert.deepEqual(JSON.parse(JSON.stringify(J.lineCol("", 0))), { line: 1, col: 1 });
    assert.equal(J.indentOf("    x"), "    ");
    assert.equal(J.indentOf("x"), "");
    assert.equal(J.esc("<&>"), "&lt;&amp;&gt;");
});

/* ------------------------------------------------------------- open / close */

test("open loads the text, renders gutter, status and highlight; Done commits pending edits", () => {
    const s = setup("let a = 1;\nlet b = 2;\n");
    const { doc, E, ta, store, clock } = s;
    assert.equal(E.isOpen(), false);
    doc.getElementById("ss-btn-js-expand").click();
    assert.equal(E.isOpen(), true);
    assert.equal(doc.getElementById("ss-js-dialog").hidden, false);
    assert.equal(ta.value, "let a = 1;\nlet b = 2;\n");
    assert.equal(doc.getElementById("ss-js-gutter").textContent, "1\n2\n3");
    assert.ok(doc.getElementById("ss-js-highlight").innerHTML.endsWith("<br>"));
    assert.equal(doc.getElementById("ss-js-count").textContent, "22 chars");
    assert.equal(doc.activeElement, ta);
    caret(s, 13);
    fire(s.win, ta, "click");
    assert.equal(doc.getElementById("ss-js-pos").textContent, "Ln 2, Col 3");
    assert.equal(doc.getElementById("ss-js-linehl").style.top, "28px");
    ta.value += "x";
    fire(s.win, ta, "input");
    assert.equal(store.text.endsWith("x"), true);
    clock.tick(100);
    doc.getElementById("ss-js-done").click();
    assert.equal(store.commits, 1, "pending commit flushed on close");
    assert.equal(E.isOpen(), false);
    assert.equal(doc.getElementById("ss-js-dialog").hidden, true);
    assert.equal(doc.activeElement.id, "ss-advanced-js");
    clock.tick(1000);
    assert.equal(store.commits, 1);
});

test("edits commit once, 600ms after the last change", () => {
    const s = setup("");
    s.E.open();
    s.ta.value = "a";
    fire(s.win, s.ta, "input");
    s.clock.tick(500);
    s.ta.value = "ab";
    fire(s.win, s.ta, "input");
    s.clock.tick(599);
    assert.equal(s.store.commits, 0);
    s.clock.tick(1);
    assert.equal(s.store.commits, 1);
    assert.deepEqual(s.store.sets, ["a", "ab"]);
});

test("open with empty getText, backdrop click closes, keyup refreshes", () => {
    const s = setup(null);
    s.E.open();
    assert.equal(s.ta.value, "");
    assert.equal(s.doc.getElementById("ss-js-highlight").innerHTML, "<br>");
    fire(s.win, s.doc.getElementById("ss-js-code"), "mousedown");
    assert.equal(s.E.isOpen(), true, "clicks inside the panel keep it open");
    s.ta.value = "q";
    fire(s.win, s.ta, "keyup");
    assert.equal(s.doc.getElementById("ss-js-count").textContent, "1 chars");
    fire(s.win, s.doc.getElementById("ss-js-dialog"), "mousedown");
    assert.equal(s.E.isOpen(), false);
    assert.equal(s.store.commits, 0, "no pending edit, nothing committed");
});

test("setValue only updates an open editor with different text", () => {
    const s = setup("one");
    s.E.setValue("ignored while closed");
    assert.equal(s.ta.value, "");
    s.E.open();
    s.E.setValue("one");
    s.E.setValue("two");
    assert.equal(s.ta.value, "two");
    assert.equal(s.doc.getElementById("ss-js-count").textContent, "3 chars");
    assert.equal(s.store.sets.length, 0, "setValue does not echo back");
});

test("bind keeps existing handlers for missing keys", () => {
    const s = setup("keep");
    s.E.bind({});
    s.E.open();
    assert.equal(s.ta.value, "keep");
});

test("scrolling the code scrolls the gutter", () => {
    const s = setup();
    const scroller = s.doc.getElementById("ss-js-scroller");
    scroller.scrollTop = 120;
    fire(s.win, scroller, "scroll", { bubbles: false });
    assert.equal(s.doc.getElementById("ss-js-gutter").scrollTop, 120);
});

/* ------------------------------------------------------------- editing keys */

test("Tab inserts two spaces at the caret, replacing a one-line selection", () => {
    const s = setup("abc");
    s.E.open();
    caret(s, 1);
    const ev = key(s, "Tab");
    assert.equal(ev.defaultPrevented, true);
    assert.deepEqual(state(s), ["a  bc", 3, 3]);
    caret(s, 0, 2);
    key(s, "Tab");
    assert.deepEqual(state(s), ["   bc", 2, 2]);
});

test("Tab / Shift+Tab indent and outdent every selected line", () => {
    const s = setup("a\n b\n\tc\nd");
    s.E.open();
    caret(s, 0, 6);
    key(s, "Tab");
    assert.deepEqual(state(s), ["  a\n   b\n  \tc\nd", 0, 13]);
    key(s, "Tab", { shiftKey: true });
    assert.deepEqual(state(s), ["a\n b\n\tc\nd", 0, 7]);
    key(s, "Tab", { shiftKey: true });
    assert.deepEqual(state(s), ["a\nb\nc\nd", 0, 5]);
    caret(s, 2, 7);
    key(s, "Tab");
    assert.deepEqual(state(s), ["a\n  b\n  c\n  d", 2, 13], "block runs to the end of the text");
});

test("Shift+Tab without a selection removes two leading spaces only", () => {
    const s = setup("    x\ny");
    s.E.open();
    caret(s, 4);
    key(s, "Tab", { shiftKey: true });
    assert.deepEqual(state(s), ["  x\ny", 2, 2]);
    caret(s, 1);
    key(s, "Tab", { shiftKey: true });
    assert.deepEqual(state(s), ["x\ny", 0, 0]);
    caret(s, 3);
    key(s, "Tab", { shiftKey: true });
    assert.deepEqual(state(s), ["x\ny", 3, 3], "no indent: unchanged");
});

test("Enter keeps the indent and adds two after an opening brace", () => {
    const s = setup("  if (x) {");
    s.E.open();
    caret(s, 10);
    key(s, "Enter");
    assert.deepEqual(state(s), ["  if (x) {\n    ", 15, 15]);
    key(s, "Enter");
    assert.deepEqual(state(s), ["  if (x) {\n    \n    ", 20, 20]);
});

test("openers and quotes auto-pair and wrap a selection", () => {
    const s = setup("ab");
    s.E.open();
    caret(s, 2);
    key(s, "(");
    assert.deepEqual(state(s), ["ab()", 3, 3]);
    caret(s, 0, 2);
    key(s, '"');
    assert.deepEqual(state(s), ['"ab"()', 1, 3]);
    caret(s, 0);
    const ev = key(s, "[", { ctrlKey: true });
    assert.equal(ev.defaultPrevented, false, "modified keys are left alone");
    assert.equal(s.ta.value, '"ab"()');
});

test("typing a closer or quote over the same character just steps over it", () => {
    const s = setup("f()");
    s.E.open();
    caret(s, 2);
    key(s, ")");
    assert.deepEqual(state(s), ["f()", 3, 3]);
});

test("typing an opener before the same opener inserts a new pair",
    () => {
        const s = setup("()");
        s.E.open();
        caret(s, 0);
        key(s, "(");
        assert.equal(s.ta.value, "()()");
    });

test("Backspace between an empty pair deletes both; other Backspaces are native", () => {
    const s = setup("x('')");
    s.E.open();
    caret(s, 3);
    const ev = key(s, "Backspace");
    assert.equal(ev.defaultPrevented, true);
    assert.deepEqual(state(s), ["x()", 2, 2]);
    caret(s, 1);
    assert.equal(key(s, "Backspace").defaultPrevented, false);
    caret(s, 0);
    assert.equal(key(s, "Backspace").defaultPrevented, false);
    caret(s, 1, 2);
    assert.equal(key(s, "Backspace").defaultPrevented, false);
    caret(s, 2);
    assert.equal(key(s, "x").defaultPrevented, false, "ordinary keys pass through");
});

test("Escape closes the find bar first, then the dialog", () => {
    const s = setup("abc");
    s.E.open();
    const ev = key(s, "f", { metaKey: true });
    assert.equal(ev.defaultPrevented, true);
    const bar = s.doc.getElementById("ss-js-findbar");
    assert.equal(bar.hidden, false);
    assert.equal(s.doc.activeElement.id, "ss-js-find");
    key(s, "Escape");
    assert.equal(bar.hidden, true);
    assert.equal(s.E.isOpen(), true);
    key(s, "F", { ctrlKey: true });
    assert.equal(bar.hidden, false);
    bar.remove();
    key(s, "Escape");
    assert.equal(s.E.isOpen(), false);
});

/* ------------------------------------------------------------- find / replace */

function findSetup(text, q, rep) {
    const s = setup(text);
    s.E.open();
    s.doc.getElementById("ss-js-find-toggle").click();
    s.doc.getElementById("ss-js-find").value = q;
    if (rep !== undefined) { s.doc.getElementById("ss-js-replace").value = rep; }
    return s;
}

test("find next/prev select matches and wrap around", () => {
    const s = findSetup("ab ab ab", "ab");
    caret(s, 0);
    s.doc.getElementById("ss-js-find-next").click();
    assert.deepEqual(state(s).slice(1), [0, 2]);
    s.doc.getElementById("ss-js-find-next").click();
    assert.deepEqual(state(s).slice(1), [3, 5]);
    s.doc.getElementById("ss-js-find-next").click();
    s.doc.getElementById("ss-js-find-next").click();
    assert.deepEqual(state(s).slice(1), [0, 2], "wrapped to the first");
    caret(s, 6, 8);
    s.doc.getElementById("ss-js-find-prev").click();
    assert.deepEqual(state(s).slice(1), [3, 5]);
    assert.equal(s.J.findFrom, 5);
});

test("find previous from the first match wraps to the last one",
    () => {
        const s = findSetup("ab ab ab", "ab");
        caret(s, 0, 2);
        s.doc.getElementById("ss-js-find-prev").click();
        assert.deepEqual(state(s).slice(1), [6, 8]);
    });

test("Enter / Shift+Enter / Escape in the find box", () => {
    const s = findSetup("x\nx\nx\nx\nx", "x");
    const input = s.doc.getElementById("ss-js-find");
    caret(s, 0, 0);
    fire(s.win, input, "keydown", { key: "Enter" });
    assert.deepEqual(state(s).slice(1), [0, 1]);
    for (let i = 0; i < 3; i++) { fire(s.win, input, "keydown", { key: "Enter" }); }
    assert.deepEqual(state(s).slice(1), [6, 7]);
    assert.equal(s.doc.getElementById("ss-js-scroller").scrollTop, 20, "scrolls the match near the top");
    fire(s.win, input, "keydown", { key: "Enter", shiftKey: true });
    assert.deepEqual(state(s).slice(1), [4, 5]);
    fire(s.win, input, "keydown", { key: "a" });
    fire(s.win, input, "keydown", { key: "Escape" });
    assert.equal(s.doc.getElementById("ss-js-findbar").hidden, true);
    assert.equal(s.doc.activeElement, s.ta);
});

test("find with no query or no match does nothing", () => {
    const s = findSetup("abc", "");
    caret(s, 1);
    assert.equal(s.J.findMatch(1), false);
    s.doc.getElementById("ss-js-find").value = "zzz";
    assert.equal(s.J.findMatch(1), false);
    assert.equal(s.J.findMatch(-1), false);
    assert.deepEqual(state(s), ["abc", 1, 1]);
});

test("replace one replaces the selected match or finds the next one first", () => {
    const s = findSetup("a1 a1 a1", "a1", "B");
    caret(s, 0);
    s.doc.getElementById("ss-js-replace-one").click();
    assert.deepEqual(state(s), ["B a1 a1", 0, 1]);
    s.doc.getElementById("ss-js-replace-one").click();
    assert.deepEqual(state(s), ["B B a1", 2, 3]);
    assert.equal(s.store.text, "B B a1");
    s.doc.getElementById("ss-js-find").value = "zz";
    s.doc.getElementById("ss-js-replace-one").click();
    assert.equal(s.ta.value, "B B a1");
    s.doc.getElementById("ss-js-find").value = "";
    s.doc.getElementById("ss-js-replace-one").click();
    assert.equal(s.ta.value, "B B a1");
});

test("replace one stops when the found text does not equal the query", () => {
    const s = findSetup("abc", "b", "X");
    caret(s, 0);
    // a find that lands on a different selection: make findMatch select something else
    s.J.__eval("findMatch = function () { var ta = document.getElementById('ss-js-input'); ta.selectionStart = 0; ta.selectionEnd = 2; return true; }");
    s.doc.getElementById("ss-js-replace-one").click();
    assert.equal(s.ta.value, "abc");
});

test("replace all replaces every occurrence (empty replacement deletes)", () => {
    const s = findSetup("foo(); foo(); bar()", "foo", "baz");
    s.doc.getElementById("ss-js-replace-all").click();
    assert.equal(s.ta.value, "baz(); baz(); bar()");
    assert.equal(s.store.text, "baz(); baz(); bar()");
    s.doc.getElementById("ss-js-find").value = "baz";
    s.doc.getElementById("ss-js-replace").value = "";
    s.doc.getElementById("ss-js-replace-all").click();
    assert.equal(s.ta.value, "(); (); bar()");
    s.doc.getElementById("ss-js-find").value = "nope";
    s.doc.getElementById("ss-js-replace-all").click();
    s.doc.getElementById("ss-js-find").value = "";
    s.doc.getElementById("ss-js-replace-all").click();
    assert.equal(s.ta.value, "(); (); bar()");
});

/* ------------------------------------------------------------- reference list */

test("reference toggle builds the helper list once and inserts snippets at the caret", () => {
    const s = setup("");
    s.E.open();
    const toggle = s.doc.getElementById("ss-js-ref-toggle");
    const ref = s.doc.getElementById("ss-js-ref");
    toggle.click();
    assert.equal(ref.hidden, false);
    const buttons = s.doc.querySelectorAll("#ss-js-ref-list button");
    assert.equal(buttons.length, s.J.REFS.length);
    toggle.click();
    toggle.click();
    assert.equal(s.doc.querySelectorAll("#ss-js-ref-list button").length, s.J.REFS.length);
    caret(s, 0);
    buttons[0].click();
    assert.equal(s.ta.value, 'sporeShow("element_id");\n');
    assert.equal(s.ta.selectionStart, s.ta.value.length);
    for (const r of s.J.REFS) { assert.ok(r.snippet.endsWith("\n"), r.name); }
});

test("reference toggle without its panel does nothing", () => {
    const s = setup("");
    s.doc.getElementById("ss-js-ref").remove();
    s.doc.getElementById("ss-js-ref-toggle").click();
    assert.equal(s.doc.querySelectorAll("#ss-js-ref-list button").length, 0);
});

/* ------------------------------------------------------------- missing markup */

test("works (as a no-op) on a page without the dialog markup", () => {
    const win = makeWindow();
    loadScript(win, "assets/default_assets/spore_studio/advanced_js_editor.js");
    const E = win.SporeJsEditor;
    E.open();
    assert.equal(E.isOpen(), false);
    E.setValue("x");
    const I = win.__internals;
    I.refresh();
    I.emitChange();
    I.showFind(true);
    I.buildRefs();
    I.closeDialog();
    assert.equal(I.replaceOne === undefined, false);
});

test("showFind without focus just shows the bar; with focus but no input is safe", () => {
    const s = setup("");
    s.J.showFind(false);
    assert.equal(s.doc.getElementById("ss-js-findbar").hidden, false);
    s.doc.getElementById("ss-js-find").remove();
    assert.doesNotThrow(() => s.J.showFind(true));
    assert.equal(s.J.findMatch(1), false);
    s.J.replaceOne();
    s.J.replaceAll();
});

/* ------------------------------------------------------------- checker, reference, pop-out */


function fakeSocket() {
    const handlers = {};
    return {
        connected: true,
        emitted: [],
        on(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); },
        emit(ev, data) { this.emitted.push([ev, JSON.parse(JSON.stringify(data === undefined ? null : data))]); },
        fire(ev, data) { (handlers[ev] || []).forEach((fn) => fn(data)); },
        relayed() { return this.emitted.filter((e) => e[0] === "spore_js_relay").map((e) => e[1].msg); },
    };
}

const TEMPLATE = {
    scripts: [{
        kind: "inline", main: true,
        code: "var socket = io();\nconst LIMIT = 3;\n/** Show an element. */\nfunction sporeShow(id) {}\n" +
            "function renderThing(el, opts) {}\n// SPORE_STUDIO:user-begin\n// SPORE_STUDIO:user-end\n",
    }],
};

function setupFull(text = "", { context = TEMPLATE, fail = false, body } = {}) {
    const { win, internals } = makeStudioWindow({
        scripts: ["assets/default_assets/spore_studio/vendor/acorn.js", "js_lint.js", "advanced_js_editor.js"], body,
    });
    const socket = fakeSocket();
    const store = { text, commits: 0, contexts: 0, notes: [], key: "k1" };
    fireDomReady(win);
    win.SporeJsEditor.bind({
        getText: () => store.text,
        setText: (t) => { store.text = t; },
        commit: () => { store.commits++; },
        getContext: () => { store.contexts++; return fail ? Promise.reject(new Error("x")) : Promise.resolve(context); },
        contextKey: () => store.key,
        getSocket: () => socket,
        templateName: () => "my_tpl",
        notify: (n) => { store.notes.push(n); },
    });
    const doc = win.document;
    return {
        win, doc, store, socket, J: internals.advanced_js_editor, E: win.SporeJsEditor, clock: win.__clock,
        ta: doc.getElementById("ss-js-input"),
    };
}

test("the checker marks problems live: status, gutter, squiggles, list, jump and hover", async () => {
    const s = setupFull("let socket = 1;\nfoo();\n");
    s.E.open();
    await settle();
    s.clock.tick(300);
    const status = s.doc.getElementById("ss-js-lint");
    assert.equal(status.textContent, "1 error · 1 warning");
    assert.equal(status.getAttribute("data-state"), "error");
    const gutter = s.doc.getElementById("ss-js-gutter");
    assert.equal(gutter.textContent, "1\n2\n3");
    assert.equal(gutter.querySelectorAll(".ss-js-gut-err").length, 1);
    assert.equal(gutter.querySelectorAll(".ss-js-gut-warn").length, 1);
    assert.equal(s.doc.querySelectorAll("#ss-js-marks .ss-js-mark--error").length, 1);
    status.click();
    assert.equal(s.doc.getElementById("ss-js-problems").hidden, false);
    const rows = s.doc.querySelectorAll("#ss-js-problems-list .ss-js-problem");
    assert.equal(rows.length, 2);
    assert.match(rows[0].textContent, /Ln 1, Col 5.*already declared by the template/);
    rows[1].click();
    assert.equal(s.ta.selectionStart, 16);
    assert.equal(s.ta.selectionEnd, 19);
    fire(s.win, s.ta, "mousemove", { offsetX: 12 + 1 * 7.8, offsetY: 8 + 20 + 5 });
    assert.match(s.ta.title, /'foo' is not defined/);
    fire(s.win, s.ta, "mousemove", { offsetX: 400, offsetY: 8 });
    assert.equal(s.ta.getAttribute("title"), null);

    s.ta.value = "let ok = 1;";
    fire(s.win, s.ta, "input");
    s.clock.tick(300);
    assert.equal(status.textContent, "No problems");
    assert.equal(s.doc.querySelector("#ss-js-problems-list").textContent, "No problems found. This script will run.");
    const toggleLabel = s.doc.getElementById("ss-js-problems-toggle").querySelector("span");
    assert.equal(toggleLabel.textContent, "Problems");
});

test("the inspector chip summarizes problems and check() lints the current text", async () => {
    const s = setupFull("x = ;");
    s.clock.tick(300);
    const chip = s.doc.getElementById("ss-advanced-js-lint");
    assert.equal(chip.hidden, false);
    assert.equal(chip.textContent, "1 error");
    assert.equal(chip.getAttribute("data-state"), "error");
    assert.equal(s.E.check().problems[0].rule, "syntax");
    s.store.text = "";
    s.E.setValue("");
    s.clock.tick(300);
    assert.equal(chip.hidden, true);
    chip.click();
    assert.equal(s.E.isOpen(), true);
    assert.equal(s.doc.getElementById("ss-js-problems").hidden, false);
});

test("the reference lists template and user functions by group, filters, and inserts calls", async () => {
    const s = setupFull("/** Mine. */\nfunction mine(a, b) {}\n");
    s.E.open();
    s.doc.getElementById("ss-js-ref-toggle").click();
    await settle();
    s.clock.tick(300);
    assert.equal(s.store.contexts, 1);
    const groups = [...s.doc.querySelectorAll("#ss-js-ref-list details")].map((d) => d.getAttribute("data-group"));
    assert.deepEqual(groups, ["spore", "template", "user", "snippets"]);
    const sigs = [...s.doc.querySelectorAll(".ss-js-ref__fn .ss-js-ref__sig")].map((n) => n.textContent);
    assert.deepEqual(sigs, ["sporeShow(id)", "renderThing(el, opts)", "mine(a, b)"]);
    assert.match(s.doc.querySelector(".ss-js-ref__fn").title, /Show an element\.[\s\S]*This template, line 4/);

    const search = s.doc.getElementById("ss-js-ref-search");
    search.value = "render";
    fire(s.win, search, "input");
    assert.deepEqual([...s.doc.querySelectorAll(".ss-js-ref__sig")].map((n) => n.textContent), ["renderThing(el, opts)"]);
    caret(s, s.ta.value.length);
    s.doc.querySelector(".ss-js-ref__fn").click();
    assert.ok(s.ta.value.endsWith("renderThing(el, opts)"));
    assert.equal(s.ta.value.slice(s.ta.selectionStart, s.ta.selectionEnd), "el, opts");
    search.value = "zzz";
    fire(s.win, search, "input");
    assert.equal(s.doc.querySelector(".ss-js-ref__empty").textContent, "No functions match “zzz”.");
    fire(s.win, search, "keydown", { key: "Escape" });
    assert.equal(search.value, "");

    s.doc.getElementById("ss-js-ref-reload").click();
    await settle();
    assert.equal(s.store.contexts, 2);
    s.E.modelChanged();
    s.clock.tick(2000);
    assert.equal(s.store.contexts, 2, "same template key: no refetch");
    s.store.key = "k2";
    s.E.modelChanged();
    s.clock.tick(2000);
    await settle();
    assert.equal(s.store.contexts, 3);
});

test("the reference says when the template can't be read", async () => {
    const s = setupFull("", { fail: true });
    s.E.open();
    s.doc.getElementById("ss-js-ref-toggle").click();
    await settle();
    const status = s.doc.getElementById("ss-js-ref-status");
    assert.equal(status.hidden, false);
    assert.match(status.textContent, /Couldn't read the template's functions/);
    assert.ok(s.doc.querySelectorAll(".ss-js-ref__snippet").length > 0);
});

test("the template's names feed the checker once the context loads", async () => {
    const s = setupFull("LIMIT = 4;");
    s.E.open();
    await settle();
    s.clock.tick(300);
    assert.equal(s.E.check().problems[0].rule, "const-assign");
});

function allowPopups(s) {
    s.win.open = (...a) => { s.win.__opened.push(a); return {}; };
}

test("pop out: joins a channel, opens the window, syncs both ways and notices the window closing", async () => {
    const s = setupFull("let a = 1;");
    allowPopups(s);
    s.E.open();
    s.doc.getElementById("ss-js-popout").click();
    assert.equal(s.E.isOpen(), false, "the dialog closes");
    const join = s.socket.emitted.find((e) => e[0] === "spore_js_join");
    const channel = join[1].channel;
    assert.match(channel, /^[0-9a-f]{24}$/);
    assert.equal(s.win.__opened.length, 1);
    assert.equal(s.win.__opened[0][0], "/_spore_studio_js_editor?channel=" + channel + "&template=my_tpl");

    s.socket.fire("spore_js_relay", { channel: "other", msg: { type: "hello" } });
    assert.equal(s.E.isPoppedOut(), false, "other channels are ignored");
    s.socket.fire("spore_js_relay", { channel, msg: { type: "hello" } });
    assert.equal(s.E.isPoppedOut(), true);
    const state = s.socket.relayed().find((m) => m.type === "state");
    assert.equal(state.text, "let a = 1;");
    assert.equal(state.template, "my_tpl");

    s.socket.fire("spore_js_relay", { channel, msg: { type: "edit", text: "let b = 2;" } });
    assert.equal(s.store.text, "let b = 2;");
    s.socket.fire("spore_js_relay", { channel, msg: { type: "commit" } });
    assert.equal(s.store.commits, 1);
    s.E.setValue("let c = 3;");
    assert.deepEqual(s.socket.relayed().at(-1), { type: "text", text: "let c = 3;" });

    s.E.open();
    assert.equal(s.E.isOpen(), false, "the dialog stays closed while popped out");
    assert.match(s.store.notes.at(-1).title, /open in its own window/);
    assert.equal(s.socket.relayed().at(-1).type, "focus");
    s.clock.tick(300);
    assert.match(s.doc.getElementById("ss-advanced-js-lint").textContent, /editing in pop-out/);

    s.socket.fire("spore_js_relay", { channel, msg: { type: "bye" } });
    assert.equal(s.E.isPoppedOut(), false);
    s.clock.tick(11000);
    assert.equal(s.store.notes.filter((n) => /didn't connect/.test(n.title)).length, 0);

    s.socket.fire("spore_js_relay", { channel, msg: { type: "ping" } });
    assert.equal(s.E.isPoppedOut(), true, "a ping revives it");
    s.clock.tick(26000);
    assert.equal(s.E.isPoppedOut(), false, "no pings for 20s: gone");
    s.socket.fire("connect");
    assert.equal(s.socket.emitted.filter((e) => e[0] === "spore_js_join").length, 2, "rejoins on reconnect");
});

test("pop out warns when no window connects or there is no socket", () => {
    const s = setupFull("");
    allowPopups(s);
    s.doc.getElementById("ss-js-popout").click();
    s.clock.tick(10001);
    assert.match(s.store.notes.at(-1).title, /didn't connect/);
    const t = setupFull("");
    t.E.bind({ getSocket: () => null });
    t.doc.getElementById("ss-js-popout").click();
    assert.match(t.store.notes.at(-1).title, /Couldn't pop out/);
});

test("pop out inside the app (or with popups blocked) asks the server to open the window", async () => {
    const s = setupFull("");
    s.win.top = {};
    s.win.fetch.on("/api/spore-studio/js-popout/open", { status: 200, body: { ok: true } });
    s.doc.getElementById("ss-js-popout").click();
    await settle();
    assert.equal(s.win.__opened.length, 0, "window.open is not used in the app's iframe");
    const call = s.win.fetch.calls.find((c) => c.url === "/api/spore-studio/js-popout/open");
    const channel = s.socket.emitted.find((e) => e[0] === "spore_js_join")[1].channel;
    assert.equal(call.method, "POST");
    assert.deepEqual(JSON.parse(call.body), { channel, template: "my_tpl" });
    assert.equal(s.store.notes.length, 0);

    const b = setupFull("");
    b.win.fetch.on("/api/spore-studio/js-popout/open", { status: 500, body: { error: "x" } });
    b.doc.getElementById("ss-js-popout").click();
    await settle();
    assert.equal(b.win.__opened.length, 1, "tried window.open first; it was blocked (null)");
    assert.match(b.store.notes.at(-1).title, /Couldn't open the pop-out editor/);
    assert.match(b.store.notes.at(-1).message, /http:\/\/127\.0\.0\.1:5000\/_spore_studio_js_editor\?channel=/);
    b.clock.tick(11000);
    assert.equal(b.store.notes.filter((n) => /didn't connect/.test(n.title)).length, 0, "no second warning");

    const c = setupFull("");
    c.win.top = {};
    c.win.fetch.on("/api/spore-studio/js-popout/open", new Error("offline"));
    c.doc.getElementById("ss-js-popout").click();
    await settle();
    assert.match(c.store.notes.at(-1).title, /Couldn't open the pop-out editor/);
});

function popoutBody() {
    const html = readSourceRel("assets/default_assets/spore_studio/editor.html");
    const m = /<!-- SS_JS_DIALOG:begin[^>]*-->([\s\S]*?)<!-- SS_JS_DIALOG:end -->/.exec(html);
    return m[1].replace('class="ss-js-dialog" hidden', 'class="ss-js-dialog"');
}

test("pop-out page: says hello, follows the Studio page, sends edits and handles closing", () => {
    const socket = fakeSocket();
    const { win, internals } = makeStudioWindow({
        scripts: ["assets/default_assets/spore_studio/vendor/acorn.js", "js_lint.js", "advanced_js_editor.js"],
        body: popoutBody(),
        url: "http://127.0.0.1:5000/_spore_studio_js_editor?channel=abcdef123456&template=my%20tpl",
    });
    win.io = { connect: () => socket };
    win.document.body.setAttribute("data-ss-js-popout", "1");
    fireDomReady(win);
    const doc = win.document;
    const ta = doc.getElementById("ss-js-input");
    const E = win.SporeJsEditor;
    assert.equal(E.isOpen(), true);
    assert.equal(doc.getElementById("ss-js-dialog").hidden, false);
    assert.equal(doc.getElementById("ss-js-popout").hidden, true);
    assert.equal(win.document.title, "Advanced JS — my tpl");
    assert.deepEqual(socket.emitted.filter((e) => e[0] === "spore_js_join")[0][1], { channel: "abcdef123456" });
    assert.equal(socket.relayed()[0].type, "hello");

    socket.fire("spore_js_relay", { channel: "abcdef123456", msg: {
        type: "state", text: "let x = 1;", template: "other", ctx: { declared: { x: "const" }, functions: [] } } });
    assert.equal(ta.value, "let x = 1;");
    assert.equal(win.document.title, "Advanced JS — other");
    win.__clock.tick(300);
    assert.match(doc.getElementById("ss-js-lint").textContent, /1 error/);

    ta.value = "let y = 2;";
    fire(win, ta, "input");
    win.__clock.tick(130);
    assert.deepEqual(socket.relayed().at(-1), { type: "edit", text: "let y = 2;" });
    win.__clock.tick(700);
    assert.equal(socket.relayed().at(-1).type, "commit");

    socket.fire("spore_js_relay", { channel: "abcdef123456", msg: { type: "text", text: "let z = 3;" } });
    assert.equal(ta.value, "let z = 3;");
    fire(win, ta, "keydown", { key: "Escape" });
    assert.equal(E.isOpen(), true, "Escape never closes the pop-out");
    win.__clock.tick(5000);
    assert.equal(socket.relayed().at(-1).type, "ping");

    socket.fire("spore_js_relay", { channel: "abcdef123456", msg: { type: "closed" } });
    assert.equal(ta.readOnly, true);
    assert.match(doc.getElementById("ss-js-popout-banner").textContent, /no longer in sync/);
    socket.fire("theme_update", { css: ":root{--x:1}", theme_type: "light" });
    assert.equal(doc.getElementById("mycelian-theme-vars").textContent, ":root{--x:1}");

    doc.getElementById("ss-js-done").click();
    assert.equal(socket.relayed().at(-1).type, "bye");
});

test("pop-out page without a channel or socket explains instead of syncing", () => {
    for (const [url, io, re] of [
        ["http://127.0.0.1:5000/_spore_studio_js_editor", { connect: () => fakeSocket() }, /opened without a link/],
        ["http://127.0.0.1:5000/_spore_studio_js_editor?channel=abcdef123456", undefined, /Couldn't connect/],
    ]) {
        const { win } = makeStudioWindow({
            scripts: ["advanced_js_editor.js"], body: popoutBody(), url,
        });
        if (io) { win.io = io; }
        win.document.body.setAttribute("data-ss-js-popout", "1");
        fireDomReady(win);
        assert.match(win.document.getElementById("ss-js-popout-banner").textContent, re);
    }
});

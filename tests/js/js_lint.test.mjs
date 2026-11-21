// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/js_lint.js: the template function catalog (analyzeTemplate)
// and the Advanced JS checker (lint), on the vendored acorn parser and the real boilerplates.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, readSource } from "./helpers/env.mjs";

const STUDIO = "assets/default_assets/spore_studio/";

function load({ acorn = true } = {}) {
    const win = makeWindow();
    if (acorn) { loadScript(win, STUDIO + "vendor/acorn.js", { expose: false }); }
    loadScript(win, STUDIO + "js_lint.js", { expose: false });
    return { win, L: win.SporeJsLint };
}

// Plain JSON copies so deepEqual ignores the vm realm.
const plain = (v) => JSON.parse(JSON.stringify(v));
const GLOBALS = { globals: { window: true, document: true, console: true, setTimeout: true, Math: true, Object: true } };

function lintRules(L, code, ctx = null, opts = GLOBALS) {
    return plain(L.lint(code, ctx, opts).problems).map((p) => `${p.severity}:${p.rule}@${p.line}:${p.col}`);
}

// The queue boilerplate as the server compiles it: data runtime injected, same-origin libraries read.
function boilerplateScripts() {
    let html = readSource("templates/_boilerplates/queue_alert.html");
    const runtime = readSource("templates/_boilerplates/spore_data_runtime.js");
    html = html.replace(/(\/\/ SPORE_STUDIO:data-runtime-begin)[\s\S]*?(\/\/ SPORE_STUDIO:data-runtime-end)/,
        (m, a, b) => a + "\n" + runtime + "\n" + b);
    const scripts = [];
    const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html))) {
        const src = /src="([^"]+)"/.exec(m[1]);
        if (src) {
            if (src[1].startsWith("/assets/")) {
                scripts.push({ kind: "external", src: src[1], code: readSource(src[1].slice(1)) });
            }
        } else if (m[2].trim()) {
            scripts.push({ kind: "inline", code: m[2], main: m[2].includes("SPORE_STUDIO:user-begin") });
        }
    }
    return scripts;
}

/* ------------------------------------------------------------- catalog */

test("analyzeTemplate lists the boilerplate's callable functions, grouped, with no parse errors", () => {
    const { L } = load();
    const ctx = plain(L.analyzeTemplate(boilerplateScripts()));
    assert.deepEqual(ctx.errors, []);
    const byName = Object.fromEntries(ctx.functions.map((f) => [f.name, f]));
    assert.equal(byName.sporeCounterAdjust.group, "spore", "window.* exports from the data runtime IIFE");
    assert.ok(byName.sporeCounterAdjust.params.length >= 2);
    assert.deepEqual(byName["MycelianOverlay.connect"].params, ["url", "options"]);
    assert.equal(byName["MycelianOverlay.connect"].group, "library");
    assert.equal(byName["MycelianOverlay.connect"].source, "/assets/default_assets/overlay_recovery.js");
    assert.ok(byName["MycelianLog.info"], "object namespace methods");
    assert.ok(byName["MycelianOverlay.alertQueue.attach"], "nested global.X.y = {...} namespaces");
    assert.equal(ctx.declared.socket, "var");
    assert.equal(ctx.declared.MycelianLog, "global");
});

test("analyzeTemplate: regions, docs, internals, user block stripped, later definitions win", () => {
    const { L } = load();
    const main = [
        "/** Shows a thing. */",
        "function sporeShowThing(id, opts = {}, ...rest) {}",
        "// helper one",
        "// helper two",
        "function helper({ a, b }) {}",
        "let counter = 0; const LIMIT = 3; class Box {}",
        "var api = { go: function (x) {}, nested: { deep(y) {} } };",
        "window.exported = function (z) {};",
        "fresh = 1;",
        "function _private() {}",
        "// SPORE_STUDIO:auto-begin",
        "function bindingFn(el) {}",
        "// SPORE_STUDIO:auto-end",
        "// SPORE_STUDIO:user-begin",
        "function userOnly() {}",
        "// SPORE_STUDIO:user-end",
        "function helper(changed) {}",
    ].join("\n");
    const ctx = plain(L.analyzeTemplate([{ kind: "inline", code: main, main: true }]));
    const by = Object.fromEntries(ctx.functions.map((f) => [f.name, f]));
    assert.deepEqual(by.sporeShowThing.params, ["id", "opts?", "...rest"]);
    assert.equal(by.sporeShowThing.doc, "Shows a thing.");
    assert.equal(by.sporeShowThing.line, 2);
    assert.deepEqual(by.helper.params, ["changed"], "the later definition wins");
    assert.equal(by.helper.group, "template");
    assert.deepEqual(by["api.go"].params, ["x"]);
    assert.deepEqual(by["api.nested.deep"].params, ["y"]);
    assert.deepEqual(by.exported.params, ["z"]);
    assert.equal(by.bindingFn.group, "generated");
    assert.equal(by._private.internal, true);
    assert.equal(by.userOnly, undefined, "the Advanced JS block is the editor's, not the template's");
    assert.deepEqual(
        [ctx.declared.counter, ctx.declared.LIMIT, ctx.declared.Box, ctx.declared.fresh, ctx.declared.exported],
        ["let", "const", "class", "global", "global"]);
});

test("analyzeTemplate: Jinja placeholders are masked; unreadable scripts fall back to a regex scan", () => {
    const { L } = load();
    const ok = plain(L.analyzeTemplate([{ kind: "inline", code: "var size = {{ FontSize }};\n{% if X %}function a(q) {}{% endif %}" }]));
    assert.deepEqual(ok.errors, []);
    assert.deepEqual(ok.functions.map((f) => f.name), ["a"]);
    const bad = plain(L.analyzeTemplate([{ kind: "inline", code: "function good(a, b) {}\nthis is not js (" }]));
    assert.equal(bad.errors.length, 1);
    assert.deepEqual(bad.functions.map((f) => [f.name, f.params]), [["good", ["a", "b"]]]);
});

test("analyzeTemplate without acorn still finds functions by regex", () => {
    const { L } = load({ acorn: false });
    assert.equal(L.available(), false);
    const ctx = plain(L.analyzeTemplate([{ kind: "inline", code: "  function x(a) {}" }, null, { code: 5 }]));
    assert.deepEqual(ctx.functions.map((f) => f.name), ["x"]);
});

/* ------------------------------------------------------------- linter */

test("syntax errors are errors with a position and stop further checks", () => {
    const { L } = load();
    const r = plain(L.lint("let a = 1;\nif (a {\n", null, GLOBALS));
    assert.equal(r.ok, false);
    assert.equal(r.problems.length, 1);
    assert.equal(r.problems[0].rule, "syntax");
    assert.equal(r.problems[0].line, 2);
    assert.match(r.problems[0].message, /^Unexpected token\. The template's whole script will not run/);
    assert.match(L.lint("return 1;", null, GLOBALS).problems[0].message, /top level of the template script/);
    assert.match(L.lint("await x;", null, GLOBALS).problems[0].message, /async function/);
});

test("\"</script\" is an error even inside a string", () => {
    const { L } = load();
    assert.deepEqual(lintRules(L, 'var s = "</SCRIPT>";'), ["error:script-close@1:10"]);
});

test("redeclaring template names: let/const clash is fatal, overrides are warnings", () => {
    const { L } = load();
    const ctx = { declared: { socket: "var", LIMIT: "const", sporeShow: "function", MycelianLog: "global", state: "var" } };
    assert.deepEqual(lintRules(L, "let socket = 1;", ctx), ["error:redeclare@1:5"]);
    assert.deepEqual(lintRules(L, "var LIMIT = 2;", ctx), ["error:redeclare@1:5"]);
    assert.deepEqual(lintRules(L, "function sporeShow(id) {}", ctx), ["warning:override@1:10"]);
    assert.deepEqual(lintRules(L, "var state = {};", ctx), ["warning:override@1:5"]);
    assert.deepEqual(lintRules(L, "const MycelianLog = null;", ctx), ["warning:shadow@1:7"]);
    assert.deepEqual(lintRules(L, "var name = 'x';"), ["warning:browser-global@1:5"]);
    assert.deepEqual(lintRules(L, "if (1) { var socket = 2; }", ctx), ["warning:override@1:14"], "hoisted vars count too");
    assert.deepEqual(lintRules(L, "function f() { var socket = 2; }", ctx), [], "a local var is fine");
});

test("undefined names, implicit globals and const assignment", () => {
    const { L } = load();
    const ctx = { declared: { LIMIT: "const", socket: "var" } };
    assert.deepEqual(lintRules(L, "missing();\nx = 3;\nLIMIT = 4;\nconst c = 1; c++;\nsocket.on('a', function () {});", ctx), [
        "warning:no-undef@1:1",
        "warning:implicit-global@2:1",
        "error:const-assign@3:1",
        "error:const-assign@4:14",
    ]);
});

test("scoping: params, hoisting, blocks, catch, classes, patterns, typeof and member names", () => {
    const { L } = load();
    const code = [
        "function outer(a, { b, c: [d] }, e = a, ...rest) {",
        "  var hoisted;",
        "  if (a) { let inner = b + d + e + rest.length; inner++; }",
        "  for (let i = 0; i < 2; i++) { hoisted = i; }",
        "  for (const k in arguments) { k; }",
        "  try { later(); } catch ({ message }) { message; }",
        "  function later() { return typeof nothingHere === 'undefined'; }",
        "  label: for (;;) { break label; }",
        "  switch (a) { case 1: let s = 1; s; }",
        "  return { a, key: c => c, [a]: 1, method() { return this; } }.key.deep;",
        "}",
        "class K extends Object { static x = 1; #p = 2; m() { return K; } static { this.y = 1; } }",
        "const f = function named() { return named; };",
        "const g = (p) => { ({ p } = { p: 1 }); [p] = [2]; };",
        "let late; ({ a: late = 1 } = {}); new K(); window.document; outer.call(null);",
    ].join("\n");
    assert.deepEqual(lintRules(L, code), []);
    assert.deepEqual(lintRules(L, "const h = () => arguments;", { declared: {} }), ["warning:no-undef@1:17"]);
    assert.deepEqual(lintRules(L, "debugger;"), ["warning:debugger@1:1"]);
});

test("lint returns the user's own top-level functions for the reference", () => {
    const { L } = load();
    const r = plain(L.lint("/** Plays it. */\nfunction playIt(id, opts) {}\nvar run = (x) => x;", null, GLOBALS));
    assert.deepEqual(r.functions.map((f) => [f.name, f.params, f.doc, f.group]), [
        ["playIt", ["id", "opts"], "Plays it.", "user"],
        ["run", ["x"], "", "user"],
    ]);
});

test("lint without acorn only reports </script and says it is unavailable", () => {
    const { L } = load({ acorn: false });
    const r = plain(L.lint("bad ( code </script>", null, GLOBALS));
    assert.equal(r.available, false);
    assert.deepEqual(r.problems.map((p) => p.rule), ["script-close"]);
});

test("summarize counts errors and warnings; browser globals fall back to the base list", () => {
    const { L } = load();
    const r = L.lint("let socket; foo;", { declared: { socket: "var" } }, GLOBALS);
    assert.deepEqual(plain(L.summarize(r)), { errors: 1, warnings: 1 });
    assert.deepEqual(plain(L.summarize(null)), { errors: 0, warnings: 0 });
    const g = L.browserGlobals();
    assert.equal(g.setTimeout, true);
    assert.equal(g.io, true);
    assert.equal(L.browserGlobals(), g, "cached");
    assert.deepEqual(plain(L.lint("setTimeout(fn);", { declared: {} }).problems).map((p) => p.message),
        ["'fn' is not defined. Running this line throws a ReferenceError."]);
});

test("without the template's names, undefined-name checks wait (no false alarms)", () => {
    const { L } = load();
    assert.deepEqual(lintRules(L, "sporeShow('x'); y = 1; const c = 1; c = 2;"), ["error:const-assign@1:37"]);
});

test("the problem cap and extra globals from the context", () => {
    const { L } = load();
    const many = Array.from({ length: 250 }, (_, i) => "u" + i + ";").join("\n");
    assert.equal(L.lint(many, { declared: {} }, GLOBALS).problems.length, 200);
    assert.deepEqual(lintRules(L, "custom();", { declared: {}, globals: ["custom"] }), []);
});

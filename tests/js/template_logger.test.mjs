// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/template_logger.js: payload building/truncation, Socket.IO vs HTTP
// transport, the offline queue, console patching and the window error hooks.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, makeIo, makeFetch, plain, run, settle } from "./helpers/env.mjs";

const FILE = "assets/default_assets/template_logger.js";

function setup(url = "http://127.0.0.1:5000/counter", opts = {}) {
    const fetch = opts.fetch || makeFetch([["/api/template-log", { ok: true }]]);
    const win = makeWindow({ url, fetch });
    const { internals: I } = loadScript(win, FILE, { expose: true });
    return { win, L: win.MycelianLog, I, fetch };
}

test("deriveTemplateName uses the path without slashes, or unknown", () => {
    assert.equal(setup("http://h/boo%20chat/").I.deriveTemplateName(), "boo%20chat");
    assert.equal(setup("http://h/").I.deriveTemplateName(), "unknown");
    const { I, win } = setup();
    win.location = null;
    assert.equal(I.deriveTemplateName(), "unknown");
});

test("serializeArg handles strings, nullish, errors, objects and cycles", () => {
    const { I, win } = setup();
    assert.equal(I.serializeArg("s"), "s");
    assert.equal(I.serializeArg(null), "null");
    assert.equal(I.serializeArg(undefined), "undefined");
    assert.equal(I.serializeArg(3), "3");
    assert.equal(I.serializeArg({ a: [1] }), '{"a":[1]}');
    const err = run(win, "var e = new Error('boom'); e.stack = 'STACK'; e");
    assert.equal(I.serializeArg(err), "boom\nSTACK");
    const noStack = run(win, "var e2 = new Error('m'); e2.stack = ''; e2");
    assert.equal(I.serializeArg(noStack), "m");
    const cyc = run(win, "var c = {}; c.self = c; c");
    assert.equal(I.serializeArg(cyc), "[object Object]");
    assert.equal(I.formatConsoleArgs(["a", 1, null]), "a 1 null");
});

test("buildPayload fills defaults and truncates message and stack", () => {
    const { I } = setup("http://h/title");
    const p = plain(I.buildPayload("warn", "x".repeat(3000), { stack: "s".repeat(5000), source: "here" }));
    assert.equal(p.template_name, "title");
    assert.equal(p.level, "warn");
    assert.equal(p.message.length, 2048);
    assert.equal(p.stack.length, 4096);
    assert.equal(p.url, "/title");
    assert.equal(p.source, "here");
    const q = plain(I.buildPayload("info", null));
    assert.deepEqual(q, { template_name: "title", level: "info", message: "", url: "/title", source: "mycelian_log" });
});

test("without a socket, logs POST to the page origin", async () => {
    const { L, fetch } = setup("http://127.0.0.1:5000/counter");
    L.error("bad thing", { stack: "at x" });
    assert.equal(fetch.calls.length, 1);
    const c = fetch.calls[0];
    assert.equal(c.url, "http://127.0.0.1:5000/api/template-log");
    assert.equal(c.method, "POST");
    assert.equal(c.init.keepalive, true);
    assert.equal(c.init.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(c.body), { template_name: "counter", level: "error", message: "bad thing", url: "/counter", source: "mycelian_log", stack: "at x" });
    await settle();
});

test("setHttpBaseUrl / init httpBaseUrl override the origin", () => {
    const { L, fetch } = setup();
    L.setHttpBaseUrl("http://other:1");
    L.warn("w");
    L.init({ httpBaseUrl: "http://third:2", templateName: "named" });
    L.info("i");
    L.setHttpBaseUrl("");
    L.info("back to origin");
    assert.deepEqual(fetch.calls.map((c) => c.url), [
        "http://other:1/api/template-log", "http://third:2/api/template-log", "http://127.0.0.1:5000/api/template-log",
    ]);
    assert.equal(JSON.parse(fetch.calls[1].body).template_name, "named");
});

test("no fetch, no origin, a throwing or rejecting fetch: logging never throws", async () => {
    const { L, win } = setup();
    win.fetch = undefined;
    assert.doesNotThrow(() => L.error("x"));
    win.fetch = () => { throw new Error("sync"); };
    assert.doesNotThrow(() => L.error("x"));
    const calls = [];
    win.fetch = (u) => { calls.push(u); return Promise.reject(new Error("net")); };
    L.error("x");
    await settle();
    assert.equal(calls.length, 1);
    win.location = { pathname: "/p", origin: "null" };
    L.error("file page");
    assert.equal(calls.length, 1, "origin 'null' (file://) has nowhere to send");
});

test("a connected socket gets template_log events instead of HTTP", () => {
    const { L, fetch } = setup();
    const s = makeIo()("u");
    s.connected = true;
    L.attachSocket(s);
    L.info("hello");
    assert.equal(fetch.calls.length, 0);
    assert.equal(s.emitted.length, 1);
    assert.equal(s.emitted[0][0], "template_log");
    assert.equal(s.emitted[0][1].message, "hello");
});

test("a disconnected socket queues up to 50 and flushes on connect", () => {
    const { L, fetch } = setup();
    const s = makeIo()("u");
    L.attachSocket(s);
    for (let i = 0; i < 60; i++) { L.info("m" + i); }
    assert.equal(s.emitted.length, 0);
    assert.equal(fetch.calls.length, 0);
    s.connected = true;
    s._recv("connect");
    assert.equal(s.emitted.length, 50);
    assert.equal(s.emitted[0][1].message, "m0");
    assert.equal(s.emitted[49][1].message, "m49");
});

test("an emit that throws while connected queues the payload; flush stops on errors", () => {
    const { L, I } = setup();
    let fail = true;
    const sent = [];
    const s = { connected: true, emit(ev, p) { if (fail) { throw new Error("x"); } sent.push(p.message); }, on() {} };
    L.attachSocket(s);
    L.info("a");
    L.info("b");
    assert.equal(I.state.queue.length, 2);
    I.flushQueue();
    assert.equal(I.state.queue.length, 1, "first shift is lost, loop breaks");
    fail = false;
    I.flushQueue();
    assert.deepEqual(sent, ["b"]);
});

test("attachSocket ignores null, flushes immediately and tolerates sockets without on()", () => {
    const { L, I } = setup();
    L.attachSocket(null);
    assert.equal(I.state.socket, null);
    const s = { connected: false, emit() {} };
    L.attachSocket(s);
    L.info("queued");
    const sent = [];
    const s2 = { connected: true, emit(ev, p) { sent.push(p.message); } };
    L.attachSocket(s2);
    assert.deepEqual(sent, ["queued"]);
    I.state.socket = { connected: true };
    I.flushQueue();
});

test("a socket without emit falls back to HTTP", () => {
    const { L, fetch } = setup();
    L.attachSocket({ connected: true });
    L.info("x");
    assert.equal(fetch.calls.length, 1);
});

test("init patches console.error/warn once and still calls the originals", () => {
    const { L, win, fetch } = setup();
    const seen = [];
    win.console = { error: (...a) => seen.push(["e", a]), warn: (...a) => seen.push(["w", a]), log() {} };
    L.init({ templateName: "t" });
    L.init();
    win.console.error("oops", { n: 1 });
    win.console.warn("careful");
    assert.deepEqual(seen, [["e", ["oops", { n: 1 }]], ["w", ["careful"]]]);
    const bodies = fetch.calls.map((c) => JSON.parse(c.body));
    assert.deepEqual(bodies.map((b) => [b.level, b.message, b.source, b.template_name]), [
        ["error", 'oops {"n":1}', "console.error", "t"],
        ["warn", "careful", "console.warn", "t"],
    ]);
});

test("init without a name derives it once; no console means no patch", () => {
    const { L, I, win } = setup("http://h/raids");
    win.console = undefined;
    L.init();
    assert.equal(I.state.templateName, "raids");
    assert.equal(I.state.consolePatched, false);
    I.__eval("state.templateName = 'kept'");
    L.init({});
    assert.equal(I.state.templateName, "kept");
});

test("window error and unhandledrejection events are logged", () => {
    const { L, win, fetch } = setup();
    L.init({ templateName: "t" });
    const err = run(win, "var x = new Error('kaput'); x.stack = 'S1'; x");
    win.dispatchEvent(new win.ErrorEvent("error", { message: "Uncaught kaput", error: err }));
    win.dispatchEvent(new win.ErrorEvent("error", {}));
    win.dispatchEvent(new win.Event("unhandledrejection", { reason: err }));
    win.dispatchEvent(new win.Event("unhandledrejection", { reason: "plain" }));
    const bodies = fetch.calls.map((c) => JSON.parse(c.body));
    assert.deepEqual(bodies.map((b) => [b.message, b.source, b.stack]), [
        ["Uncaught kaput", "window.onerror", "S1"],
        ["Script error", "window.onerror", undefined],
        ["Unhandled rejection: kaput", "unhandledrejection", "S1"],
        ["Unhandled rejection: plain", "unhandledrejection", undefined],
    ]);
    // hooks are installed once
    I_installAgain(win);
    assert.equal(fetch.calls.length, 4);
});

function I_installAgain(win) {
    win.__internals.installGlobalHooks();
}

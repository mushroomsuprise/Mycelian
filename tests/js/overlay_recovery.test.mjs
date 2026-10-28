// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/overlay_recovery.js: service filtering, recovery callbacks, socket option
// merging, hello on (re)connect, logger attach, chat banner requests, and load order with alert_queue.js.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, makeIo, plain } from "./helpers/env.mjs";

const FILE = "assets/default_assets/overlay_recovery.js";

function setup(url = "http://127.0.0.1:5000/chat") {
    const win = makeWindow({ url });
    const { internals: I } = loadScript(win, FILE, { expose: true });
    return { win, O: win.MycelianOverlay, I };
}

test("matchesService: no filter, no service and global services always match", () => {
    const { I } = setup();
    assert.equal(I.matchesService({ service: "twitch" }, null), true);
    assert.equal(I.matchesService({ service: "twitch" }, []), true);
    assert.equal(I.matchesService(null, ["twitch"]), true);
    assert.equal(I.matchesService({}, ["twitch"]), true);
    assert.equal(I.matchesService({ service: "internet" }, ["twitch"]), true);
    assert.equal(I.matchesService({ service: "web_engine" }, ["twitch"]), true);
    assert.equal(I.matchesService({ service: "twitch" }, ["twitch", "obs"]), true);
    assert.equal(I.matchesService({ service: "spotify" }, ["twitch"]), false);
});

test("mergeSocketOptions starts from the defaults and lets callers override", () => {
    const { I } = setup();
    const defaults = { reconnection: true, reconnectionDelay: 1000, reconnectionDelayMax: 10000, timeout: 20000, transports: ["websocket", "polling"] };
    assert.deepEqual(plain(I.mergeSocketOptions()), defaults);
    assert.deepEqual(plain(I.mergeSocketOptions({})), defaults);
    const merged = plain(I.mergeSocketOptions({ socketOptions: { timeout: 5, query: { a: 1 } } }));
    assert.deepEqual(merged, { ...defaults, timeout: 5, query: { a: 1 } });
    // defaults are not mutated
    assert.equal(I.DEFAULT_SOCKET_OPTS.timeout, 20000);
});

test("runSporeRecovery calls the spore runtime hooks when present", () => {
    const { O, win } = setup();
    const calls = [];
    O.runSporeRecovery();
    win.sporeLoadStatsCache = () => calls.push("stats");
    O.runSporeRecovery();
    win.sporeRefreshAllDataDisplays = () => calls.push("displays");
    O.runSporeRecovery();
    assert.deepEqual(calls, ["stats", "stats", "displays"]);
});

test("runSporeRecovery swallows hook errors with a warning", () => {
    const { O, win } = setup();
    win.sporeLoadStatsCache = () => { throw new Error("x"); };
    assert.doesNotThrow(() => O.runSporeRecovery());
    assert.ok(win.__console.some(([k, a]) => k === "warn" && /spore refresh failed/.test(a[0])));
    win.console = { warn() { throw new Error("console broken"); } };
    assert.doesNotThrow(() => O.runSporeRecovery());
});

test("connect requires Socket.IO", () => {
    const { O, win } = setup();
    win.io = undefined;
    assert.throws(() => O.connect("u"), /Socket.IO client \(io\) is not loaded/);
});

test("connect merges options, installs handlers and attaches the template logger", () => {
    const { O, win } = setup("http://h/chat");
    const attached = [];
    win.MycelianLog = { attachSocket: (s) => attached.push(s) };
    const s = O.connect("http://h", { socketOptions: { timeout: 1 } });
    assert.equal(s.url, "http://h");
    assert.equal(s.opts.timeout, 1);
    assert.equal(s.opts.reconnection, true);
    assert.deepEqual(attached, [s]);
    assert.ok(s._handlers.has("overlay-recovery"));
    assert.ok(s._handlers.has("connect"));
    assert.ok(s._handlers.has("disconnect"));
    win.MycelianLog = { attachSocket: "nope" };
    assert.doesNotThrow(() => O.connect("u2"));
    assert.equal(O.connect("u3", null).url, "u3");
});

test("connect says hello with the page path on every connect and calls onConnect", () => {
    const { O, win } = setup("http://h/boo%20chat");
    let connects = 0;
    const s = O.connect("u", { onConnect: () => connects++ });
    s.connect();
    s.connect();
    assert.deepEqual(plain(s.emitted), [["overlay_hello", { path: "/boo%20chat" }], ["overlay_hello", { path: "/boo%20chat" }]]);
    assert.equal(connects, 2);
    win.location = null;
    s.connect();
    assert.deepEqual(plain(s.emitted[2]), ["overlay_hello", { path: "" }]);
});

test("installHandlers says hello at once for an already connected socket", () => {
    const { O, win } = setup("http://h/title");
    const s = makeIo()("u");
    s.connected = true;
    assert.equal(O.installHandlers(s), s);
    assert.deepEqual(plain(s.emitted), [["overlay_hello", { path: "/title" }]]);
    win.location = undefined;
    const s2 = makeIo()("u");
    s2.connected = true;
    O.installHandlers(s2, {});
    assert.deepEqual(plain(s2.emitted), [["overlay_hello", { path: "" }]]);
});

test("hello emit failures are swallowed", () => {
    const { O } = setup();
    const s = makeIo()("u");
    s.connected = true;
    s.emit = () => { throw new Error("closed"); };
    assert.doesNotThrow(() => O.installHandlers(s));
    assert.doesNotThrow(() => s._recv("connect"));
});

test("installHandlers ignores missing or handler-less sockets", () => {
    const { O } = setup();
    assert.equal(O.installHandlers(null), null);
    const odd = { emit() {} };
    assert.equal(O.installHandlers(odd), odd);
});

test("overlay-recovery runs the spore refresh then options.onRecovery (not the global hook)", () => {
    const { O, win } = setup();
    const calls = [];
    win.sporeLoadStatsCache = () => calls.push("stats");
    win.onMycelianOverlayRecovery = (d) => calls.push(["global", d.service]);
    const s = O.installHandlers(makeIo()("u"), { services: ["twitch"], onRecovery: (d) => calls.push(["opt", d.service]) });
    s._recv("overlay-recovery", { service: "twitch" });
    s._recv("overlay-recovery", { service: "spotify" });
    assert.deepEqual(calls, ["stats", ["opt", "twitch"]]);
    assert.ok(win.__console.some(([k, a]) => k === "log" && a[0] === "[overlay-recovery] sync"));
});

test("overlay-recovery falls back to window.onMycelianOverlayRecovery", () => {
    const { O, win } = setup();
    const got = [];
    const s = O.installHandlers(makeIo()("u"));
    s._recv("overlay-recovery", { service: "obs" });
    win.onMycelianOverlayRecovery = (d) => got.push(d.service);
    s._recv("overlay-recovery", { service: "obs" });
    assert.deepEqual(got, ["obs"]);
    win.console = { log() { throw new Error("no console"); }, warn() {} };
    s._recv("overlay-recovery", { service: "obs" });
    assert.deepEqual(got, ["obs", "obs"]);
});

test("disconnect calls onDisconnect with the reason", () => {
    const { O } = setup();
    const reasons = [];
    const s = O.installHandlers(makeIo()("u"), { onDisconnect: (r) => reasons.push(r) });
    s._recv("disconnect", "transport close");
    O.installHandlers(makeIo()("u"))._recv("disconnect", "x");
    assert.deepEqual(reasons, ["transport close"]);
});

test("requestChatBanner emits the payload (or {}) only on real sockets", () => {
    const { O } = setup();
    const s = makeIo()("u");
    O.requestChatBanner(s, { id: 1 });
    O.requestChatBanner(s);
    O.requestChatBanner(null, {});
    O.requestChatBanner({}, {});
    assert.deepEqual(plain(s.emitted), [["chat_banner_request", { id: 1 }], ["chat_banner_request", {}]]);
});

test("loaded before alert_queue.js, connect() gets the handshake guard", () => {
    const win = makeWindow({ url: "http://h/follow" });
    loadScript(win, FILE);
    loadScript(win, "assets/default_assets/alert_queue.js");
    const s = win.MycelianOverlay.connect("u");
    assert.equal(s.__mycelianHandshakeGuarded, true);
    assert.ok(win.MycelianOverlay.alertQueue);
    s.emit("alert_complete", { queue_seq: 1 });
    assert.equal(s.emitted.length, 0, "spectator route must not emit handshake events");
});

test("exports the public API", () => {
    const { O } = setup();
    assert.deepEqual(Object.keys(O).sort(), ["connect", "installHandlers", "requestChatBanner", "runSporeRecovery"]);
});

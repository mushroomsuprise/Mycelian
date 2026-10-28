// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// templates/_boilerplates/spore_data_runtime.js, part 1 (lines 1-1120): formatting, random deltas,
// data source resolution, DB get/set over Socket.IO, value animations, counter images,
// counters (render/adjust/hydrate/persist, progressive goals), progress bars, data displays and
// control actions. Part 2 (spore_data_runtime_part2.test.mjs) covers clocks, timers, lists,
// wheel, widgets, polls, feeds and transforms.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, plain } from "./helpers/env.mjs";
import { setupRuntime as setup, RUNTIME as FILE } from "./helpers/spore_runtime.mjs";

/* ------------------------------------------------------------------ setup */

test("loads once and initialises the shared window state", () => {
    const { win } = setup();
    for (const k of ["__sporeCounters", "__sporeCounterMeta", "__sporeConfigCache", "__sporeLastPayload", "__sporeTimers", "__sporeFeeds"]) {
        assert.ok(win[k], k);
    }
    assert.equal(win.__sporeDataRuntimeLoaded, true);
    const before = win.sporeCounterAdjust;
    loadScript(win, FILE);
    assert.equal(win.sporeCounterAdjust, before, "second load is a no-op");
    const keep = makeWindow();
    keep.__sporeCounters = { a: 3 };
    loadScript(keep, FILE);
    assert.equal(keep.__sporeCounters.a, 3, "existing state is kept");
});

/* ------------------------------------------------------------------ formatting */

test("sporeFormatDisplay and sporeFormatCounterDisplay replace tokens", () => {
    const { I } = setup();
    assert.equal(I.sporeFormatDisplay(5, "{value} subs ({value})"), "5 subs (5)");
    assert.equal(I.sporeFormatDisplay(null, ""), "");
    assert.equal(I.sporeFormatDisplay("x", null), "x");
    assert.equal(I.sporeFormatCounterDisplay(3, "{value}/{max} (min {min})", 0, 10), "3/10 (min 0)");
    assert.equal(I.sporeFormatCounterDisplay(3, "{value}/{max}", null, ""), "3/");
    assert.equal(I.sporeFormatCounterDisplay(null, null), "");
});

test("sporeCoerceNumber", () => {
    const { I } = setup();
    assert.equal(I.sporeCoerceNumber("4.5"), 4.5);
    assert.equal(I.sporeCoerceNumber("", 7), 7);
    assert.equal(I.sporeCoerceNumber(null), 0);
    assert.equal(I.sporeCoerceNumber("abc", 2), 2);
    assert.equal(I.sporeCoerceNumber("abc"), 0);
    assert.ok(Number.isNaN(I.sporeCoerceNumber("abc", NaN)));
});

test("sporeEmitOutput honours the outputs table and needs a socket", () => {
    const { win, I, socket } = setup();
    win.__sporeTemplateName = "poll";
    I.sporeEmitOutput("wheel_result", "w1", { label: "A" });
    assert.deepEqual(plain(socket.emitted.at(-1)), ["wheel_result", { label: "A", template: "poll", elementId: "w1" }]);
    win.__sporeOutputs = [{ event: "wheel_result", element_id: "w1", enabled: false }, { event: "x", element_id: "e", enabled: true }];
    I.sporeEmitOutput("wheel_result", "w1", {});
    I.sporeEmitOutput("other", "w1", {});
    assert.equal(socket.emitted.length, 1);
    I.sporeEmitOutput("x", "e");
    assert.deepEqual(plain(socket.emitted.at(-1)), ["x", { template: "poll", elementId: "e" }]);
    win.socket = null;
    assert.doesNotThrow(() => I.sporeEmitOutput("x", "e", {}));
    const noSock = setup({ socket: false });
    assert.doesNotThrow(() => noSock.I.sporeEmitOutput("x", "e", {}));
});

/* ------------------------------------------------------------------ deltas and sources */

test("sporeRandomDelta: fixed, random int/float with swapped bounds and decimals", () => {
    const hi = setup({ random: 0.999 });
    const lo = setup({ random: 0 });
    assert.equal(hi.I.sporeRandomDelta(), 1);
    assert.equal(hi.I.sporeRandomDelta({ kind: "fixed", value: "3" }), 3);
    assert.equal(hi.I.sporeRandomDelta({ type: "fixed", value: "" }), 1);
    assert.equal(hi.I.sporeRandomDelta({ kind: "random_int", min: 5, max: 1 }), 5);
    assert.equal(lo.I.sporeRandomDelta({ kind: "random_int", min: 5, max: 1 }), 1);
    assert.equal(lo.I.sporeRandomDelta({ kind: "random_int", min: 2 }), 2);
    assert.equal(hi.I.sporeRandomDelta({ kind: "random_float", min: 2, max: 1, decimals: 2 }), 2);
    assert.equal(lo.I.sporeRandomDelta({ kind: "random_float", min: 1, max: 2, decimals: "x" }), 1);
    const mid = setup({ random: 0.5 });
    assert.equal(mid.I.sporeRandomDelta({ kind: "random_float", min: 0, max: 1, decimals: 0 }), 1);
    assert.equal(mid.I.sporeRandomDelta({ kind: "random_float" }), 0);
    assert.equal(mid.I.sporeRandomDelta({ kind: "mystery", value: 4 }), 4);
});

test("sporeRandomDelta data_source reads the last payload", () => {
    const { win, I } = setup();
    win.__sporeLastPayload.event = { amount: 250, alert_type: "bits" };
    win.__sporeAlertPayloadKeys = { "alert.bits": "amount" };
    assert.equal(I.sporeRandomDelta({ kind: "data_source", source: "alert.bits" }), 250);
    win.__sporeLastPayload = {};
    assert.equal(I.sporeRandomDelta({ kind: "data_source", source: "alert.bits", fallback: 1 }), 1);
});

test("sporePayloadKeyForSource uses the key maps then the prefix", () => {
    const { win, I } = setup();
    assert.equal(I.sporePayloadKeyForSource(""), "");
    win.__sporeAlertPayloadKeys = { "alert.viewers": "viewer_count" };
    win.__sporeChatPayloadKeys = { "chat.user": "username" };
    assert.equal(I.sporePayloadKeyForSource("alert.viewers"), "viewer_count");
    assert.equal(I.sporePayloadKeyForSource("chat.user"), "username");
    assert.equal(I.sporePayloadKeyForSource("alert.amount"), "amount");
    assert.equal(I.sporePayloadKeyForSource("chat.message"), "message");
    assert.equal(I.sporePayloadKeyForSource("stats.x"), "");
});

test("alert type and sub tier helpers", () => {
    const { I } = setup();
    assert.equal(I.sporeAlertType({ alert_type: "SUB" }), "sub");
    assert.equal(I.sporeAlertType({ alerttype: "Raid" }), "raid");
    assert.equal(I.sporeAlertType({ type: "bits" }), "bits");
    assert.equal(I.sporeAlertType({}), "");
    assert.equal(I.sporeAlertType("x"), "");
    assert.deepEqual([1000, "2000", 3000, 2, "prime", "PRIME", null, "", 5, "abc", 0].map(I.sporeNormalizeSubTier), [1, 2, 3, 2, 1, 1, null, null, 5, null, 0]);
    assert.equal(I.sporeTierMatches("any", 1000), true);
    assert.equal(I.sporeTierMatches(null, 1000), true);
    assert.equal(I.sporeTierMatches("2", 2000), true);
    assert.equal(I.sporeTierMatches("1", 2000), false);
    assert.equal(I.sporeTierMatches("1", null), false);
    assert.equal(I.sporeTierMatches("weird", 2000), true, "unknown filter tiers match anything");
});

test("sporeResolveSubDelta counts subs, resubs and gift quantities", () => {
    const { I } = setup();
    assert.equal(I.sporeResolveSubDelta("sub.new_sub", { fallback: 0 }, { alert_type: "sub" }), 1);
    assert.equal(I.sporeResolveSubDelta("sub.new_sub", {}, { alert_type: "resub" }), 0);
    assert.equal(I.sporeResolveSubDelta("sub.resub", {}, { alert_type: "resub" }), 1);
    assert.equal(I.sporeResolveSubDelta("sub.resub", { fallback: "2" }, { alert_type: "sub" }), 2);
    assert.equal(I.sporeResolveSubDelta("sub.gift_sub", {}, { alert_type: "giftsub", gift_qty: 5 }), 5);
    assert.equal(I.sporeResolveSubDelta("sub.gift_sub", {}, { alert_type: "gift_sub", quantity: "3" }), 3);
    assert.equal(I.sporeResolveSubDelta("sub.gift_sub", {}, { alert_type: "sub" }), 0);
    assert.equal(I.sporeResolveSubDelta("sub.other", {}, { alert_type: "sub" }), 0);
    assert.equal(I.sporeResolveSubDelta("sub.new_sub", { tier_filter: "3" }, { alert_type: "sub", tier: "1000" }), 0);
    assert.equal(I.sporeResolveSubDelta("sub.new_sub", { fallback: 9 }, null), 9);
});

test("sporeEffectiveSourceId expands placeholder sources", () => {
    const { I } = setup();
    assert.equal(I.sporeEffectiveSourceId({ source: "counter", source_id: "c1" }), "counter.c1");
    assert.equal(I.sporeEffectiveSourceId({ source: "counter.{id}" }), "");
    assert.equal(I.sporeEffectiveSourceId({ source: "config.{id}", source_id: "Color" }), "config.Color");
    assert.equal(I.sporeEffectiveSourceId({ source: "config" }), "");
    assert.equal(I.sporeEffectiveSourceId({ source: "runtime", runtime_path: "p", runtime_key: "k" }), "runtime.p.k");
    assert.equal(I.sporeEffectiveSourceId({ source: "runtime.{path}.{key}", runtime_path: "p" }), "");
    assert.equal(I.sporeEffectiveSourceId({ source: "twitch", twitch_binding: "b", twitch_path: "data.0" }), "twitch.b.data.0");
    assert.equal(I.sporeEffectiveSourceId({ source: "twitch.{binding_id}.{path}" }), "");
    assert.equal(I.sporeEffectiveSourceId({ source: "stats", source_id: "followers" }), "stats.followers");
    assert.equal(I.sporeEffectiveSourceId({ source: "stats" }), "");
    assert.equal(I.sporeEffectiveSourceId({ source: "alert.amount" }), "alert.amount");
    assert.equal(I.sporeEffectiveSourceId(null), "");
});

test("sporeSpecPassesAlertFilters", () => {
    const { I } = setup();
    assert.equal(I.sporeSpecPassesAlertFilters(null, {}), true);
    assert.equal(I.sporeSpecPassesAlertFilters({ alert_type_filter: "Any" }, {}), true);
    assert.equal(I.sporeSpecPassesAlertFilters({ alert_type_filter: "Bits" }, { alert_type: "bits" }), true);
    assert.equal(I.sporeSpecPassesAlertFilters({ alert_type_filter: "bits" }, { alert_type: "sub" }), false);
    assert.equal(I.sporeSpecPassesAlertFilters({ tier_filter: "2" }, { tier: 2000 }), true);
    assert.equal(I.sporeSpecPassesAlertFilters({ tier_filter: "2" }, null), false);
});

test("sporeResolveSource covers every source family", () => {
    const { win, I } = setup();
    assert.equal(I.sporeResolveSource({ kind: "fixed", value: 7 }), 7);
    assert.equal(I.sporeResolveSource({ kind: "random_int", min: 3, max: 3 }), 3);
    assert.equal(I.sporeResolveSource({ fallback: "fb" }), "fb", "no source id");
    assert.equal(I.sporeResolveSource(null), undefined);
    assert.equal(I.sporeResolveSource({ source: "alert.amount", alert_type_filter: "bits", fallback: 5 }, { alert_type: "sub", amount: 1 }), 5);
    assert.equal(I.sporeResolveSource({ source: "alert.amount", alert_type_filter: "bits" }, { alert_type: "sub" }), 0);
    assert.equal(I.sporeResolveSource({ source: "sub.gift_sub" }, { alert_type: "giftsub", gift_qty: 4 }), 4);
    assert.equal(I.sporeResolveSource({ source: "alerts.paused" }, { paused: 1 }), true);
    assert.equal(I.sporeResolveSource({ source: "alerts.paused" }, null), false);
    assert.equal(I.sporeResolveSource({ source: "alert.amount" }, { amount: 300 }), 300);
    assert.equal(I.sporeResolveSource({ source: "chat.message_length" }, { message_length: "hello" }), 5);
    assert.equal(I.sporeResolveSource({ source: "chat.message_length" }, { message_length: 9 }), 9);
    win.__sporeStatsCache = { followers: 10 };
    assert.equal(I.sporeResolveSource({ source: "stats", source_id: "followers" }, {}, {}), 10);
    assert.equal(I.sporeResolveSource({ source: "stats.followers" }, {}, { stats: { followers: 11 } }), 11);
    win.__sporeChatbotCache = { points: 3 };
    assert.equal(I.sporeResolveSource({ source: "chatbot.points" }, {}), 3);
    assert.equal(I.sporeResolveSource({ source: "chatbot.points" }, {}, { chatbot: { points: 4 } }), 4);
    win.__sporeCounters.c1 = 42;
    assert.equal(I.sporeResolveSource({ source: "counter", source_id: "c1" }), 42);
    win.__sporeCounterMeta.c1 = { elementId: "e" };
    assert.equal(I.sporeResolveSource({ source: "counter.c1" }), 42);
    win.__sporeConfigCache.Color = "#fff";
    assert.equal(I.sporeResolveSource({ source: "config", source_id: "Color" }), "#fff");
    assert.equal(I.sporeResolveSource({ source: "runtime.p.k" }, {}, { runtime: { p: { k: "v" } } }), "v");
    assert.equal(I.sporeResolveSource({ source: "runtime.p.k" }, {}, {}), undefined);
    assert.equal(I.sporeResolveSource({ source: "runtime.nodot", fallback: "f" }, {}, {}), "f");
    win.__sporeTwitchCache.b = { data: [{ title: "T" }] };
    assert.equal(I.sporeResolveSource({ source: "twitch.b.data.0.title" }), "T");
    assert.equal(I.sporeResolveSource({ source: "twitch.b.missing.x", fallback: "fb" }), "fb");
    assert.equal(I.sporeResolveSource({ source: "twitch.none.x", fallback: "fb2" }), "fb2");
    assert.equal(I.sporeResolveSource({ source: "twitch.nodot", fallback: "f3" }), "f3");
    assert.equal(I.sporeResolveSource({ source: "unknown.thing" }), null);
    assert.equal(I.sporeResolveSource({ source: "unknown.thing", fallback: 0 }), 0);
});

/* ------------------------------------------------------------------ database */

test("sporeDbGet emits get_data and resolves with the matching response only", () => {
    const { I, socket, clock } = setup();
    const got = [];
    I.sporeDbGet("a/b", (d) => got.push(d));
    const [ev, req] = socket.emitted[0];
    assert.equal(ev, "get_data");
    assert.equal(req.path, "a/b");
    assert.match(req.request_id, /^spore_get_\d+_/);
    socket._recv("get_data", null);
    socket._recv("get_data", { request_id: "other", data: 1 });
    socket._recv("get_data", { request_id: req.request_id, data: { x: 1 } });
    socket._recv("get_data", { request_id: req.request_id, data: { x: 2 } });
    clock.tick(8000);
    assert.deepEqual(plain(got), [{ x: 1 }]);
    assert.equal((socket._handlers.get("get_data") || []).length, 0, "handler removed");
});

test("sporeDbGet: error, raw response, timeout, no socket, off() failure", () => {
    const s = setup();
    const got = [];
    s.I.sporeDbGet("p", (d) => got.push(d));
    s.socket._recv("get_data", { request_id: s.socket.emitted[0][1].request_id, error: "nope" });
    s.I.sporeDbGet("p", (d) => got.push(d));
    s.socket._recv("get_data", { request_id: s.socket.emitted[1][1].request_id, value: 3 });
    s.I.sporeDbGet("p", (d) => got.push(d));
    s.clock.tick(8000);
    s.I.sporeDbGet("p");
    s.socket.off = () => { throw new Error("closed"); };
    s.clock.tick(8000);
    assert.deepEqual(plain(got), [null, { request_id: s.socket.emitted[1][1].request_id, value: 3 }, null]);
    const n = setup({ socket: false });
    n.I.sporeDbGet("p", (d) => got.push(["nosock", d]));
    n.I.sporeDbGet("p");
    assert.deepEqual(got.at(-1), ["nosock", null]);
});

test("sporeDbSet emits set_data", () => {
    const s = setup();
    const res = [];
    s.I.sporeDbSet("p", { a: 1 }, (ok) => res.push(ok));
    s.I.sporeDbSet("p", { a: 2 });
    assert.deepEqual(plain(s.socket.emitted), [["set_data", { path: "p", data: { a: 1 } }], ["set_data", { path: "p", data: { a: 2 } }]]);
    const n = setup({ socket: false });
    n.I.sporeDbSet("p", {}, (ok) => res.push(ok));
    n.I.sporeDbSet("p", {});
    assert.deepEqual(res, [true, false]);
});

/* ------------------------------------------------------------------ value animations */

test("easing curves", () => {
    const { I } = setup();
    assert.equal(I.sporeEaseProgress(0.5, "linear"), 0.5);
    assert.equal(I.sporeEaseProgress(0.5, "ease-in"), 0.125);
    assert.equal(I.sporeEaseProgress(0.25, "ease-in-out"), 0.0625);
    assert.equal(I.sporeEaseProgress(0.75, "ease-in-out"), 0.9375);
    assert.equal(I.sporeEaseProgress(0.5, "ease-out"), 0.875);
    I.sporeClearValueAnimationClasses(null);
});

test("sporeAnimateValueTick counts up frame by frame with an optional pulse", () => {
    const s = setup({ html: '<span id="v"></span>' });
    let done = 0;
    s.I.sporeAnimateValueTick("v", 0, 100, "{value}/{max}", { duration_ms: 160, easing: "linear", pulse: true }, () => done++, { min: 0, max: 200 });
    const el = s.doc.getElementById("v");
    assert.ok(el.classList.contains("spore-value-pulse"));
    assert.equal(el.style.getPropertyValue("--spore-va-dur"), "0.16s");
    s.clock.tick(16);
    assert.equal(el.textContent, "0/200");
    s.clock.tick(80);
    assert.equal(el.textContent, "50/200");
    s.clock.tick(200);
    assert.equal(el.textContent, "100/200");
    assert.equal(done, 1);
    assert.ok(!el.classList.contains("spore-value-pulse"));
});

test("sporeAnimateValueTick jumps straight to the end for 0ms or equal values", () => {
    const s = setup({ html: '<span id="v"></span>' });
    let done = 0;
    s.I.sporeAnimateValueTick("v", 5, 5, null, {}, () => done++);
    s.I.sporeAnimateValueTick("v", 1, 9, "{value}!", { duration_ms: 0 }, () => done++);
    assert.equal(done, 2);
    assert.deepEqual(s.win.__setText.at(-1), ["v", "9!"]);
    s.win.sporeSetText = undefined;
    s.I.sporeAnimateValueTick("v", 1, 3, null, { duration_ms: 0 });
    assert.equal(s.doc.getElementById("v").textContent, "3");
    s.I.sporeAnimateValueTick("missing", 1, 3, null, {}, () => done++);
    s.I.sporeAnimateValueTick("missing", 1, 3, null, {});
    assert.equal(done, 3);
    s.I.sporeAnimateValueTick("v", "x", "y", null, { duration_ms: 50 });
    s.clock.tick(100);
    s.I.sporeAnimateValueTick("v", 0, 10, null, {});
    s.clock.tick(600);
    assert.equal(s.doc.getElementById("v").textContent, "10");
});

test("sporePlayValueAnimation: fade-in swaps classes, slide/bounce add one, pulse stays", () => {
    const s = setup({ html: '<span id="v"></span>' });
    const el = s.doc.getElementById("v");
    s.I.sporePlayValueAnimation("v", null);
    s.I.sporePlayValueAnimation("v", { enabled: false });
    s.I.sporePlayValueAnimation("missing", { enabled: true });
    s.I.sporePlayValueAnimation("v", { enabled: true, type: "tick_up" });
    assert.equal(el.className, "");
    s.I.sporePlayValueAnimation("v", { enabled: true });
    assert.ok(el.classList.contains("spore-value-fade-out"));
    s.clock.tick(250);
    assert.ok(el.classList.contains("spore-value-fade-in"));
    assert.ok(!el.classList.contains("spore-value-fade-out"));
    s.clock.tick(500);
    assert.equal(el.className, "");
    for (const [type, cls] of [["slide-in", "spore-value-slide-in"], ["bounce", "spore-value-bounce"], ["zoom", "spore-value-fade-in"]]) {
        s.I.sporePlayValueAnimation("v", { enabled: true, type, duration_ms: 100, easing: "linear" });
        assert.ok(el.classList.contains(cls), type);
        s.clock.tick(100);
        assert.ok(!el.classList.contains(cls));
    }
    s.I.sporePlayValueAnimation("v", { enabled: true, pulse: true });
    assert.ok(el.classList.contains("spore-value-pulse"));
});

/* ------------------------------------------------------------------ counter images */

test("sporeResolveCounterImageSrc picks the first matching range or the default", () => {
    const { I } = setup();
    const spec = { ranges: [null, { min: 0, max: 9, src: "low.png" }, { min: 10, src: "ten.png" }, { min: 11, max: 99, src: "" }], default_src: "def.png" };
    assert.equal(I.sporeResolveCounterImageSrc(5, spec), "low.png");
    assert.equal(I.sporeResolveCounterImageSrc(10, spec), "ten.png");
    assert.equal(I.sporeResolveCounterImageSrc(50, spec), "def.png");
    assert.equal(I.sporeResolveCounterImageSrc(50, {}), "");
    assert.equal(I.sporeResolveCounterImageSrc(1), "");
});

test("counter image nodes, transitions and class pairs", () => {
    const s = setup({ html: '<img id="i"><div id="d"><img></div><div id="e"></div>' });
    assert.equal(s.I.sporeCounterImageNode(null), null);
    assert.equal(s.I.sporeCounterImageNode({}), null);
    assert.equal(s.I.sporeCounterImageNode({ elementId: "nope" }), null);
    assert.equal(s.I.sporeCounterImageNode({ elementId: "i" }).id, "i");
    assert.equal(s.I.sporeCounterImageNode({ elementId: "d" }).tagName, "IMG");
    assert.equal(s.I.sporeCounterImageNode({ elementId: "e" }), null);
    assert.ok(!s.I.sporeCounterImageTransitionActive(null));
    assert.ok(!s.I.sporeCounterImageTransitionActive({ enabled: true, type: "none" }));
    assert.ok(s.I.sporeCounterImageTransitionActive({ enabled: true, type: "fade" }));
    assert.deepEqual(plain(s.I.sporeCiClassPair("Slide")), { out: "spore-ci-slide-out", inn: "spore-ci-slide-in" });
    assert.deepEqual(plain(s.I.sporeCiClassPair("bounce")).inn, "spore-ci-bounce-in");
    assert.deepEqual(plain(s.I.sporeCiClassPair("roll")).out, "spore-ci-roll-out");
    assert.deepEqual(plain(s.I.sporeCiClassPair()).out, "spore-ci-fade-out");
});

test("sporeEnsureCounterImageHost wraps the image once, moving position styles and hidden state", () => {
    const s = setup({ html: '<div id="stage"><img id="i" class="spore-element big" data-spore-hidden="true" style="left: 10px; top: 5px; width: 50px"></div>' });
    const img = s.doc.getElementById("i");
    const host = s.I.sporeEnsureCounterImageHost(img);
    assert.equal(host.className, "spore-counter-image-host spore-element big");
    assert.equal(host.style.left, "10px");
    assert.equal(img.style.left, "0");
    assert.equal(img.style.width, "100%");
    assert.equal(host.getAttribute("data-spore-hidden"), "true");
    assert.equal(img.hasAttribute("data-spore-hidden"), false);
    assert.equal(img.style.position, "absolute");
    assert.equal(img.style.objectFit, "contain");
    assert.equal(s.I.sporeEnsureCounterImageHost(img), host, "already wrapped");
    const loose = s.doc.createElement("img");
    assert.equal(s.I.sporeEnsureCounterImageHost(loose), loose);
    assert.equal(s.I.sporeEnsureCounterImageHost(null), null);
    const alt = s.I.sporeCounterImageAltLayer(host, img);
    assert.equal(alt.className, "spore-counter-image-alt");
    assert.equal(s.I.sporeCounterImageAltLayer(host, img), alt);
    assert.equal(s.I.sporeCounterImageAltLayer(null, img), null);
});

test("sporeCiWaitAnimation finishes on animationend (own target) or the timeout, once", () => {
    const s = setup({ html: '<img id="i"><b id="child"></b>' });
    const img = s.doc.getElementById("i");
    let done = 0;
    s.I.sporeCiWaitAnimation(img, 100, () => done++);
    img.dispatchEvent(Object.assign(new s.win.Event("animationend"), { target: s.doc.getElementById("child") }));
    assert.equal(done, 0);
    img.dispatchEvent(new s.win.Event("animationend"));
    s.clock.tick(200);
    assert.equal(done, 1);
    s.I.sporeCiWaitAnimation(img, 0, null);
    s.clock.tick(56);
});

test("sporeApplyCounterImageSrc: direct swaps, empty URL and a fade transition", () => {
    const s = setup({ html: '<img id="i">' });
    const img = s.doc.getElementById("i");
    const spec = { elementId: "i", transition: { enabled: true, type: "fade", duration_ms: 200 } };
    s.I.sporeApplyCounterImageSrc(null, "a.png", spec);
    s.I.sporeApplyCounterImageSrc(img, "a.png", spec);
    assert.equal(img.src, "a.png", "first image has nothing to transition from");
    s.I.sporeApplyCounterImageSrc(img, "a.png", spec);
    s.I.sporeApplyCounterImageSrc(img, "b.png", spec);
    assert.ok(img.classList.contains("spore-ci-fade-out"));
    assert.equal(img.style.getPropertyValue("--spore-ci-dur"), "0.1s");
    s.clock.tick(140);
    assert.equal(img.src, "b.png");
    assert.ok(img.classList.contains("spore-ci-fade-in"));
    s.clock.tick(140);
    assert.equal(img.className, "");
    assert.equal(img.style.getPropertyValue("--spore-ci-dur"), "");
    assert.equal(s.win.__sporeCounterImageLastSrc.i, "b.png");
    s.I.sporeApplyCounterImageSrc(img, "", spec);
    assert.equal(img.src, "");
    s.I.sporeApplyCounterImageSrc(img, "c.png", { elementId: "i" });
    assert.equal(img.src, "c.png");
    s.I.sporeApplyCounterImageSrc(img, "d.png", { elementId: "i", range_transition: { enabled: true, type: "slide", duration_ms: 0, easing: "linear" } });
    assert.ok(img.classList.contains("spore-ci-slide-out"));
});

test("sporeApplyCounterImageSrc crossfade preloads then swaps layers", () => {
    const s = setup({ html: '<div><img id="i" src="a.png"></div>' });
    const img = s.doc.getElementById("i");
    const spec = { elementId: "i", transition: { enabled: true, type: "crossfade", duration_ms: 300 } };
    s.win.__sporeCounterImageLastSrc.i = "a.png";
    s.I.sporeApplyCounterImageSrc(img, "b.png", spec);
    const pre = s.win.__image.instances.at(-1);
    assert.equal(pre.src, "b.png");
    pre.onload();
    const alt = img.parentNode.querySelector(".spore-counter-image-alt");
    assert.equal(alt.src, "b.png");
    assert.equal(alt.style.opacity, "1");
    assert.equal(img.style.opacity, "0");
    s.clock.tick(300);
    assert.equal(img.src, "b.png");
    assert.equal(img.style.opacity, "1");
    assert.equal(alt.style.opacity, "0");
    s.I.sporeApplyCounterImageSrc(img, "c.png", spec);
    s.win.__image.instances.at(-1).onerror();
    assert.equal(img.src, "c.png");
});

test("sporeApplyCounterImages updates every image bound to the counter", () => {
    const s = setup({ html: '<img id="i"><img id="j">' });
    s.win.__sporeCounterMeta.c = { initial_value: 5 };
    s.win.__sporeCounterImages.push(null, { counter_id: "other", elementId: "j" }, { counter_id: "c", elementId: "i", ranges: [{ min: 0, max: 9, src: "low.png" }] }, { counter_id: "c", elementId: "missing" });
    s.I.sporeApplyCounterImages("c");
    assert.equal(s.doc.getElementById("i").src, "low.png");
    s.I.sporeApplyCounterImages("unknown");
    s.win.__sporeCounterImages = undefined;
    s.I.sporeApplyCounterImages("c");
});

/* ------------------------------------------------------------------ counters */

function counterSetup(meta, html = '<span id="cnt"></span>') {
    const s = setup({ html });
    s.win.__sporeCounterMeta.c = Object.assign({ elementId: "cnt", initial_value: 0 }, meta);
    return s;
}

test("sporeCounterRender clamps, formats and remembers the value", () => {
    const s = counterSetup({ min: 0, max: 10, format: "{value}/{max}", initial_value: 3 });
    s.I.sporeCounterRender("missing");
    s.I.sporeCounterRender("c");
    assert.equal(s.doc.getElementById("cnt").textContent, "3/10");
    s.win.__sporeCounters.c = 50;
    s.I.sporeCounterRender("c");
    assert.equal(s.win.__sporeCounters.c, 10);
    s.win.__sporeCounters.c = -5;
    s.I.sporeCounterRender("c");
    assert.equal(s.win.__sporeCounters.c, 0);
    assert.equal(s.win.__sporeCounterLastRendered.c, 0);
});

test("sporeCounterRender runs value animations unless skipped", () => {
    const s = counterSetup({ value_animation: { enabled: true, type: "bounce", duration_ms: 100 } });
    s.win.__sporeCounters.c = 2;
    s.I.sporeCounterRender("c");
    assert.ok(s.doc.getElementById("cnt").classList.contains("spore-value-bounce"));
    s.clock.tick(100);
    s.I.sporeCounterRender("c", { skipAnimation: true });
    assert.ok(!s.doc.getElementById("cnt").classList.contains("spore-value-bounce"));
    const t = counterSetup({ value_animation: { enabled: true, type: "tick_up", duration_ms: 100, easing: "linear" } });
    t.win.__sporeCounters.c = 10;
    t.I.sporeCounterRender("c");
    t.clock.tick(200);
    assert.equal(t.doc.getElementById("cnt").textContent, "10");
    const noEl = counterSetup({ elementId: null });
    noEl.I.sporeCounterRender("c");
    noEl.win.sporeSetText = undefined;
    noEl.win.__sporeCounterMeta.c.elementId = "cnt";
    noEl.I.sporeCounterRender("c");
});

test("progressive goals: bounds, normalisation, display max and clamping to the last goal", () => {
    const s = counterSetup({ progressive: true, goals: [10, "x", 5, 20, 20], min: 0, format: "{value}/{max}" });
    assert.deepEqual(plain(s.I.sporeNormalizeGoals([10, "x", 5, 20, 20])), [10, 20]);
    assert.deepEqual(plain(s.I.sporeNormalizeGoals(null)), []);
    assert.equal(s.I.sporeCounterUsesProgressive({ progressive: true, goals: [] }), false);
    assert.equal(s.I.sporeCounterUsesProgressive(null), false);
    assert.deepEqual(plain(s.I.sporeProgressiveBounds(3, [10, 20], 0)), { goals: [10, 20], floor: 0, ceiling: 20, activeGoal: 10, activeIndex: 0 });
    assert.deepEqual(plain(s.I.sporeProgressiveBounds(15, [10, 20], null)), { goals: [10, 20], floor: 10, ceiling: 20, activeGoal: 20, activeIndex: 1 });
    assert.deepEqual(plain(s.I.sporeProgressiveBounds(25, [10, 20], "")).activeGoal, 20);
    assert.equal(s.I.sporeProgressiveBounds(1, [], 0), null);
    s.win.__sporeCounters.c = 12;
    s.I.sporeCounterRender("c");
    assert.equal(s.doc.getElementById("cnt").textContent, "12/20");
    s.win.__sporeCounters.c = 99;
    s.I.sporeCounterRender("c");
    assert.equal(s.doc.getElementById("cnt").textContent, "20/20");
    s.win.__sporeCounters.c = -4;
    s.I.sporeCounterRender("c");
    assert.equal(s.doc.getElementById("cnt").textContent, "0/10");
});

test("sporeCounterAdjust: increment, decrement, set, reset with clamping and outputs", () => {
    const s = counterSetup({ min: 0, max: 10, initial_value: 2 });
    s.win.__sporeTemplateName = "t";
    s.I.sporeCounterAdjust("missing", "increment");
    s.I.sporeCounterAdjust("c", "increment", { kind: "fixed", value: 3 }, { user: "a" });
    assert.equal(s.win.__sporeCounters.c, 5);
    assert.deepEqual(plain(s.win.__sporeLastPayload.event), { user: "a" });
    assert.deepEqual(plain(s.socket.emitted.at(-1)), ["counter_changed", { counterId: "c", value: 5, delta: 3, template: "t", elementId: "cnt" }]);
    s.I.sporeCounterAdjust("c", "decrement", { kind: "fixed", value: 10 });
    assert.equal(s.win.__sporeCounters.c, 0);
    s.I.sporeCounterAdjust("c", "set", { kind: "fixed", value: 99 });
    assert.equal(s.win.__sporeCounters.c, 10);
    s.I.sporeCounterAdjust("c", "set", { kind: "fixed", value: "junk" });
    assert.equal(s.win.__sporeCounters.c, 1, "an unparseable fixed value counts as the default delta 1");
    s.I.sporeCounterAdjust("c", "set", { kind: "fixed", value: 10 });
    const emitted = s.socket.emitted.length;
    s.I.sporeCounterAdjust("c", "increment");
    assert.equal(s.socket.emitted.length, emitted, "no change, no output");
    s.I.sporeCounterAdjust("c", "reset");
    assert.equal(s.win.__sporeCounters.c, 2);
    s.I.sporeCounterAdjust("c", "increment", null, { is_replay: true });
    assert.equal(s.win.__sporeCounters.c, 2, "replays never count");
    s.I.sporeCounterAdjust("c");
    assert.equal(s.win.__sporeCounters.c, 3);
});

test("reset clamps to min/max and progressive ceilings", () => {
    const a = counterSetup({ min: 5, max: 8, initial_value: 0 });
    a.I.sporeCounterAdjust("c", "reset");
    assert.equal(a.win.__sporeCounters.c, 5);
    const b = counterSetup({ max: 8, initial_value: 20 });
    b.I.sporeCounterAdjust("c", "reset");
    assert.equal(b.win.__sporeCounters.c, 8);
    const p = counterSetup({ progressive: true, goals: [10, 20], initial_value: 50 });
    p.I.sporeCounterAdjust("c", "reset");
    assert.equal(p.win.__sporeCounters.c, 20);
    const q = counterSetup({ progressive: true, goals: [10, 20], initial_value: 0 });
    q.I.sporeCounterAdjust("c", "reset");
    assert.equal(q.win.__sporeCounters.c, 0);
});

test("progressive counters emit goal_reached for each goal crossed and stay above the reached floor", () => {
    const s = counterSetup({ progressive: true, goals: [10, 20, 30], min: 0 });
    s.win.__sporeCounters.c = 8;
    s.I.sporeCounterAdjust("c", "increment", { kind: "fixed", value: 15 });
    assert.equal(s.win.__sporeCounters.c, 23);
    const goals = s.socket.emitted.filter((e) => e[0] === "goal_reached").map((e) => [e[1].goal, e[1].nextGoal, e[1].index]);
    assert.deepEqual(goals, [[10, 20, 0], [20, 30, 1]]);
    s.I.sporeCounterAdjust("c", "decrement", { kind: "fixed", value: 100 });
    assert.equal(s.win.__sporeCounters.c, 20, "can't drop below the goal already reached");
    s.I.sporeCounterAdjust("c", "increment", { kind: "fixed", value: 100 });
    assert.equal(s.win.__sporeCounters.c, 30);
    assert.deepEqual(s.socket.emitted.filter((e) => e[0] === "goal_reached").at(-1)[1].nextGoal, null);
    const nominal = counterSetup({ progressive: true, goals: [10], min: 5 });
    nominal.I.sporeCounterAdjust("c", "decrement", { kind: "fixed", value: 3 });
    assert.equal(nominal.win.__sporeCounters.c, 5);
    nominal.I.sporeEmitProgressiveGoals({ progressive: false }, "c", 0, 50);
});

test("persisted counters read-modify-write their database document", () => {
    const s = counterSetup({ persist: true, database_path: "counters/x", database_key: "deaths" });
    s.win.__sporeCounters.c = 4;
    s.I.sporeCounterPersist("c");
    const req = s.socket.emitted.at(-1);
    assert.equal(req[0], "get_data");
    s.socket._recv("get_data", { request_id: req[1].request_id, data: { other: 1 } });
    assert.deepEqual(plain(s.socket.emitted.at(-1)), ["set_data", { path: "counters/x", data: { other: 1, deaths: 4 } }]);
    s.I.sporeCounterPersist("c");
    s.socket._recv("get_data", { request_id: s.socket.emitted.at(-1)[1].request_id, data: "junk" });
    assert.deepEqual(plain(s.socket.emitted.at(-1)[1].data), { deaths: 4 });
    const n = s.socket.emitted.length;
    s.win.__sporeCounterMeta.c.database_path = "";
    s.I.sporeCounterPersist("c");
    s.win.__sporeCounterMeta.c.persist = false;
    s.I.sporeCounterPersist("c");
    s.I.sporeCounterPersist("missing");
    assert.equal(s.socket.emitted.length, n);
    const dflt = counterSetup({ persist: true, database_path: "p" });
    dflt.win.__sporeCounters.c = 1;
    dflt.I.sporeCounterPersist("c");
    dflt.socket._recv("get_data", { request_id: dflt.socket.emitted.at(-1)[1].request_id, data: {} });
    assert.deepEqual(plain(dflt.socket.emitted.at(-1)[1].data), { c: 1 }, "key defaults to the counter id");
});

test("sporeCounterHydrate loads persisted values or the initial value", () => {
    const s = counterSetup({ persist: true, database_path: "p", database_key: "k", initial_value: 7 });
    let done = 0;
    s.I.sporeCounterHydrate("c", () => done++);
    s.socket._recv("get_data", { request_id: s.socket.emitted.at(-1)[1].request_id, data: { k: "12" } });
    assert.equal(s.win.__sporeCounters.c, 12);
    assert.equal(s.doc.getElementById("cnt").textContent, "12");
    s.I.sporeCounterHydrate("c");
    s.socket._recv("get_data", { request_id: s.socket.emitted.at(-1)[1].request_id, data: null });
    assert.equal(s.win.__sporeCounters.c, 7);
    const local = counterSetup({ initial_value: 3 });
    local.I.sporeCounterHydrate("c", () => done++);
    local.I.sporeCounterHydrate("c");
    assert.equal(local.win.__sporeCounters.c, 3);
    local.I.sporeCounterHydrate("missing", () => done++);
    local.I.sporeCounterHydrate("missing");
    assert.equal(done, 3);
    const zero = counterSetup({ persist: true, database_path: "p" });
    zero.I.sporeCounterHydrate("c");
    zero.socket._recv("get_data", { request_id: zero.socket.emitted.at(-1)[1].request_id, data: {} });
    assert.equal(zero.win.__sporeCounters.c, 0);
    zero.win.__sporeCounterMeta.c.initial_value = undefined;
    zero.I.sporeCounterHydrate("c");
});

/* ------------------------------------------------------------------ progress bars */

function barSetup(spec, meta) {
    const s = setup({ html: '<div id="bar"><div class="spore-progress-fill"></div></div><div id="empty"></div>' });
    s.win.__sporeCounterMeta.c = Object.assign({ initial_value: 0 }, meta);
    s.win.__sporeProgressBars = [Object.assign({ elementId: "bar", counter_id: "c", max: 50 }, spec)];
    s.fill = s.doc.querySelector(".spore-progress-fill");
    return s;
}

test("progress bars fill to value/max and emit goal_reached when crossing the max", () => {
    const s = barSetup({ near_goal_effect: "shimmer", near_goal_threshold: 80 });
    s.I.sporeUpdateProgressBars("c");
    assert.equal(s.fill.style.width, "0%");
    s.win.__sporeCounters.c = 25;
    s.I.sporeUpdateProgressBars("other");
    assert.equal(s.fill.style.width, "0%", "other counters are ignored");
    s.I.sporeUpdateAllProgressBars();
    assert.equal(s.fill.style.width, "50%");
    s.win.__sporeCounters.c = 45;
    s.I.sporeUpdateProgressBars("c");
    assert.ok(s.fill.classList.contains("spore-progress-near-shimmer"));
    s.win.__sporeCounters.c = 60;
    s.I.sporeUpdateProgressBars("c");
    assert.equal(s.fill.style.width, "100%");
    assert.equal(s.socket.emitted.filter((e) => e[0] === "goal_reached").length, 1);
    s.I.sporeUpdateProgressBars("c");
    assert.equal(s.socket.emitted.filter((e) => e[0] === "goal_reached").length, 1, "once per crossing");
    s.win.__sporeCounters.c = 10;
    s.I.sporeUpdateProgressBars("c");
    assert.equal(s.fill.className, "spore-progress-fill");
    s.win.__sporeCounters.c = 50;
    s.I.sporeUpdateProgressBars("c");
    assert.equal(s.socket.emitted.filter((e) => e[0] === "goal_reached").length, 2);
});

test("progress max from another counter, from progressive goals, overrides and zero", () => {
    const s = barSetup({ max_kind: "counter", max_counter_id: "m" });
    s.win.__sporeCounters.c = 5;
    s.I.sporeUpdateProgressBars("c");
    assert.equal(s.fill.style.width, "0%", "max counter without meta is 0");
    s.win.__sporeCounterMeta.m = { initial_value: 20 };
    s.I.sporeUpdateProgressBars("m");
    assert.equal(s.fill.style.width, "25%");
    s.win.__sporeCounters.m = 10;
    s.I.sporeUpdateProgressBars("m");
    assert.equal(s.fill.style.width, "50%");
    const g = barSetup({ follow_counter_goals: true }, { progressive: true, goals: [10, 100] });
    g.win.__sporeCounters.c = 15;
    g.I.sporeUpdateProgressBars("c");
    assert.equal(g.fill.style.width, "15%");
    g.win.__sporeCounters.c = undefined;
    g.win.__sporeCounterMeta.c.initial_value = 5;
    g.I.sporeUpdateProgressBars("c");
    assert.equal(g.fill.style.width, "50%");
    const f = barSetup({ follow_counter_goals: true });
    f.win.__sporeCounters.c = 10;
    f.I.sporeUpdateProgressBars("c");
    assert.equal(f.fill.style.width, "20%", "no goals: plain max");
    const o = barSetup({ override_value: "3", override_max: "6" });
    o.I.sporeUpdateProgressBars("c");
    assert.equal(o.fill.style.width, "50%");
    const z = barSetup({ override_max: 0 });
    z.I.sporeUpdateProgressBars("c");
    assert.equal(z.fill.style.width, "0%");
    assert.equal(z.I.sporeResolveProgressMax(null), 100);
    assert.equal(z.I.sporeResolveProgressMax({ max: "x" }), 100);
    const missing = barSetup({ elementId: "nope" });
    missing.I.sporeUpdateProgressBars("c");
    const noFill = barSetup({ elementId: "empty" });
    noFill.I.sporeUpdateProgressBars("c");
    const noCounter = barSetup({}, null);
    noCounter.win.__sporeCounterMeta = {};
    noCounter.I.sporeUpdateProgressBars("c");
    noCounter.win.__sporeProgressBars = undefined;
    noCounter.I.sporeUpdateProgressBars("c");
});

test("near-goal effects", () => {
    const { I, doc } = setup({ html: '<i id="f"></i>' });
    assert.equal(I.sporeResolveNearGoalEffect(null), "none");
    assert.equal(I.sporeResolveNearGoalEffect({ near_goal_pulse: true }), "pulse");
    assert.equal(I.sporeResolveNearGoalEffect({ near_goal_effect: "SCROLL" }), "scroll");
    assert.equal(I.sporeResolveNearGoalEffect({ near_goal_effect: "sparkle" }), "none");
    assert.equal(I.sporeResolveNearGoalEffect({}), "none");
    const f = doc.getElementById("f");
    I.sporeApplyNearGoalEffect(f, "pulse");
    assert.equal(f.className, "spore-progress-near-pulse");
    I.sporeApplyNearGoalEffect(f, "scroll");
    assert.equal(f.className, "spore-progress-near-scroll");
    I.sporeApplyNearGoalEffect(f, "shimmer");
    I.sporeApplyNearGoalEffect(f, "none");
    assert.equal(f.className, "");
    I.sporeApplyNearGoalEffect(null, "pulse");
});

/* ------------------------------------------------------------------ data displays */

test("data displays resolve their source from the last payload and format it", () => {
    const s = setup({ html: '<span id="d"></span>' });
    s.win.__sporeDataDisplays.push({ elementId: "d", source: { source: "alert.amount" }, format: "{value} bits", default_text: "-" });
    s.win.__sporeLastPayload.event = { amount: 300 };
    s.I.sporeRefreshDataDisplay(0);
    assert.equal(s.doc.getElementById("d").textContent, "300 bits");
    assert.equal(s.win.__sporeDisplayLastRendered.d, 300);
    s.win.__sporeLastPayload.event = {};
    s.I.sporeRefreshAllDataDisplays();
    assert.equal(s.doc.getElementById("d").textContent, "-");
    s.I.sporeApplyDataDisplay(0, "x", "override");
    assert.equal(s.doc.getElementById("d").textContent, "override");
    s.I.sporeRefreshDataDisplay(5);
    s.I.sporeApplyDataDisplay(5, 1);
    s.win.__sporeDataDisplays[0].feed = true;
    s.I.sporeRefreshDataDisplay(0);
    s.win.__sporeDataDisplays.push({ source: {} });
    s.I.sporeApplyDataDisplay(1, 3);
    s.win.__sporeDataDisplays.push({ elementId: "d" });
    s.I.sporeApplyDataDisplay(2, null);
    assert.equal(s.doc.getElementById("d").textContent, "");
});

test("data displays tick up from the last number and play other animations", () => {
    const s = setup({ html: '<span id="d"></span>' });
    s.win.__sporeDataDisplays.push({ elementId: "d", value_animation: { enabled: true, type: "tick_up", duration_ms: 100, easing: "linear" } });
    s.I.sporeApplyDataDisplay(0, 10);
    assert.equal(s.doc.getElementById("d").textContent, "10", "first value is shown directly");
    s.I.sporeApplyDataDisplay(0, 20);
    s.clock.tick(200);
    assert.equal(s.doc.getElementById("d").textContent, "20");
    s.I.sporeApplyDataDisplay(0, "text");
    assert.equal(s.doc.getElementById("d").textContent, "text");
    s.win.__sporeDataDisplays.push({ elementId: "d", value_animation: { enabled: true, type: "bounce", duration_ms: 50 } });
    s.I.sporeApplyDataDisplay(1, 5);
    assert.ok(s.doc.getElementById("d").classList.contains("spore-value-bounce"));
    s.win.sporeSetText = undefined;
    s.I.sporeApplyDataDisplay(1, 6);
});

test("sporeLoadStatsCache fetches session stats and refreshes displays", () => {
    const s = setup({ html: '<span id="d"></span>' });
    s.win.__sporeDataDisplays.push({ elementId: "d", source: { source: "stats.followers" } });
    s.I.sporeLoadStatsCache();
    const req = s.socket.emitted.at(-1);
    assert.deepEqual([req[0], req[1].path], ["get_data", "statistics/session"]);
    s.socket._recv("get_data", { request_id: req[1].request_id, data: { followers: 77 } });
    assert.equal(s.doc.getElementById("d").textContent, "77");
    s.I.sporeLoadStatsCache();
    s.socket._recv("get_data", { request_id: s.socket.emitted.at(-1)[1].request_id, error: "x" });
    assert.equal(s.win.__sporeStatsCache.followers, 77);
});

/* ------------------------------------------------------------------ control actions */

test("sporeDispatchControlAction maps actions to socket events and runtime calls", () => {
    const s = setup({ html: '<span id="cnt"></span>' });
    const calls = [];
    for (const f of ["sporeTimerStart", "sporeTimerPause", "sporeTimerReset", "sporeShow", "sporeHide", "sporeToggle"]) {
        s.win[f] = (id) => calls.push([f, id]);
    }
    s.win.__sporeCounterMeta.c = { elementId: "cnt", initial_value: 0 };
    const D = (a, d, t) => s.I.sporeDispatchControlAction(a, d, t);
    for (const a of ["pause_alerts", "resume_alerts", "toggle_alerts", "skip_alert", "clear_alert_queue"]) { D(a); }
    D("refresh_alerts");
    D("counter_adjust", { target_counter_id: "c", operation: "increment", delta: { kind: "fixed", value: 4 } });
    D("counter_adjust", { counter_id: "c" });
    D("counter_adjust", {});
    D("timer_start", { element_id: "t1" });
    D("timer_pause", { target_timer_id: "t1" });
    D("timer_reset", { element_id: "t1" });
    D("timer_start", {});
    D("element_show", { element_id: "e" });
    D("element_hide", { element_id: "e" });
    D("element_toggle", { element_id: "e" });
    D("twitch_api_request", { endpoint: "/users", method: "POST", requestId: "r1" });
    D("twitch_api_request", { endpoint: "/streams" });
    D("websocket_emit", { event_name: "custom", payload: { a: 1 } });
    D("websocket_emit", { event_name: "custom2", data: { b: 2 } });
    D("websocket_emit", { event_name: "custom3" });
    D("streamdeck_forward", { action_name: "go", data: { x: 1 } }, "poll");
    D("streamdeck_forward", { actionName: "go2" }, "poll");
    D("streamdeck_forward", null, "poll");
    D("start_poll", { q: 1 }, "poll");
    D("start_poll", {}, "");
    D("", {}, "poll");
    D(undefined, undefined, undefined);
    assert.equal(s.win.__sporeCounters.c, 5);
    // timer actions call the runtime's own timer functions (covered in part 2); show/hide/toggle are page globals
    assert.deepEqual(calls, [["sporeShow", "e"], ["sporeHide", "e"], ["sporeToggle", "e"]]);
    const ev = s.socket.emitted.filter((e) => !["counter_changed", "get_data", "set_data"].includes(e[0])).map((e) => plain(e));
    assert.equal(ev[0][0], "pause_alerts");
    assert.deepEqual(ev.map((e) => e[0]), ["pause_alerts", "resume_alerts", "toggle_alerts", "skip_alert", "clear_alert_queue", "refresh-alerts",
        "twitch-api-request", "twitch-api-request", "custom", "custom2", "custom3", "streamdeck_template_action", "streamdeck_template_action", "streamdeck_template_action", "poll_start_poll"]);
    assert.deepEqual(ev[6][1], { endpoint: "/users", method: "POST", requestId: "r1" });
    assert.equal(ev[7][1].method, "GET");
    assert.match(ev[7][1].requestId, /^spore_ctrl_\d+$/);
    assert.deepEqual(ev[8][1], { a: 1 });
    assert.deepEqual(ev[9][1], { b: 2 });
    assert.deepEqual(ev[10][1], {});
    assert.deepEqual(ev[11][1], { templateName: "poll", actionName: "go", data: { x: 1 } });
    assert.deepEqual(ev[12][1].actionName, "go2");
    assert.deepEqual(ev[13][1], { templateName: "poll", actionName: "", data: {} });
});

test("control actions without a socket or runtime helpers do nothing", () => {
    const s = setup();
    s.win.socket = null;
    for (const a of ["pause_alerts", "resume_alerts", "toggle_alerts", "skip_alert", "clear_alert_queue", "refresh_alerts", "streamdeck_forward"]) {
        assert.doesNotThrow(() => s.I.sporeDispatchControlAction(a, {}, "t"), a);
    }
    s.I.sporeDispatchControlAction("twitch_api_request", { endpoint: "/x" });
    s.I.sporeDispatchControlAction("websocket_emit", { event_name: "e" });
    s.I.sporeDispatchControlAction("custom", {}, "t");
    for (const f of ["sporeTimerStart", "sporeTimerPause", "sporeTimerReset", "sporeShow", "sporeHide", "sporeToggle"]) { s.win[f] = undefined; }
    s.I.__eval("sporeTimerStart = undefined; sporeTimerPause = undefined; sporeTimerReset = undefined;");
    for (const a of ["timer_start", "timer_pause", "timer_reset"]) { s.I.sporeDispatchControlAction(a, { element_id: "x" }); }
    for (const a of ["element_show", "element_hide", "element_toggle"]) { s.I.sporeDispatchControlAction(a, { element_id: "x" }); }
    s.I.sporeDispatchControlAction("element_show", {});
});

test("window exports", () => {
    const { win } = setup();
    for (const n of ["sporeFormatDisplay", "sporeFormatCounterDisplay", "sporeResolveSource", "sporeRandomDelta", "sporeCounterAdjust", "sporeCounterHydrate",
        "sporeCounterRender", "sporeUpdateProgressBars", "sporeUpdateAllProgressBars", "sporePlayValueAnimation", "sporeApplyCounterImages",
        "sporeResolveCounterImageSrc", "sporeRefreshDataDisplay", "sporeRefreshAllDataDisplays", "sporeLoadStatsCache", "sporeDispatchControlAction",
        "sporeDbGet", "sporeDbSet", "sporeListAdd", "sporeListClear", "sporeWheelSpin", "sporeRefreshStatCards", "sporeInitWidgets", "sporeInitFeeds",
        "sporeApplyTransforms", "sporeInitTransforms", "sporeFeedFilterPasses", "sporeFeedRender", "sporeFeedItem", "sporeFeedFire", "sporeEmitOutput",
        "sporePaintQr", "sporePatchSetText", "sporeInitClocks", "sporeInitTimers", "sporeTimerStart", "sporeTimerPause", "sporeTimerReset", "sporeConfetti"]) {
        assert.equal(typeof win[n], "function", n);
    }
});

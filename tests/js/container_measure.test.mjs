// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/container_measure.js: what a template inside a container is
// showing right now (shared by the live container page and the Spore Studio canvas).

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, plain } from "./helpers/env.mjs";
import { frameDoc, RED } from "./helpers/frame_doc.mjs";

const FILE = "assets/default_assets/spore_studio/container_measure.js";

function setup() {
    const win = makeWindow();
    loadScript(win, FILE, { expose: false });
    return { win, M: win.MycelianContainerMeasure };
}

const measure = (M, tree, opts = {}) => plain(M.measureDocument(frameDoc(tree, opts), opts.w || 400, opts.h || 300));

test("exports the measurer on window and module.exports", () => {
    const { M } = setup();
    assert.deepEqual(Object.keys(M).sort(), ["MESSAGE_TYPE", "createExplicitSizes", "measure", "measureDocument", "watch"]);
    assert.equal(M.MESSAGE_TYPE, "mycelian-content-size");
    const mod = { exports: {} };
    const win = makeWindow({ extra: { module: mod } });
    loadScript(win, FILE, { expose: false });
    assert.equal(mod.exports, win.MycelianContainerMeasure);
});

test("transparent wrappers of any size don't count; what paints inside them does", () => {
    const { M } = setup();
    // A 100%-tall transparent column (chat layout, #sporeRoot, FF7 root) around one panel.
    const tree = [{ rect: [0, 0, 400, 300], children: [
        { rect: [0, 0, 300, 300], children: [{ rect: [10, 20, 100, 50], style: RED }] },
    ] }];
    assert.deepEqual(measure(M, tree), { w: 100, h: 50, x: 10, y: 20 });
});

test("a painted full-size box does count (it really fills the window)", () => {
    const { M } = setup();
    assert.deepEqual(measure(M, [{ rect: [0, 0, 400, 300], style: RED }]), { w: 400, h: 300, x: 0, y: 0 });
});

test("backgrounds, images, shadows and visible borders paint; transparent ones don't", () => {
    const { M } = setup();
    const at = (style, tag) => measure(M, [{ tag, rect: [5, 6, 20, 30], style }]);
    assert.deepEqual(at({ backgroundImage: "url(x.png)" }), { w: 20, h: 30, x: 5, y: 6 });
    assert.deepEqual(at({ boxShadow: "0 0 4px black" }), { w: 20, h: 30, x: 5, y: 6 });
    assert.deepEqual(at({}, "img"), { w: 20, h: 30, x: 5, y: 6 }, "media always paints");
    assert.deepEqual(at({}, "canvas"), { w: 20, h: 30, x: 5, y: 6 });
    assert.deepEqual(at({ borderTopWidth: "2px", borderTopStyle: "solid", borderTopColor: "rgb(1, 2, 3)" }), { w: 20, h: 30, x: 5, y: 6 });
    assert.deepEqual(at({ borderTopWidth: "2px", borderTopStyle: "solid", borderTopColor: "rgba(1, 2, 3, 0)" }), { empty: true });
    assert.deepEqual(at({ borderTopWidth: "2px", borderTopStyle: "none" }), { empty: true });
    assert.deepEqual(at({ backgroundColor: "transparent" }), { empty: true });
    assert.deepEqual(at({ backgroundColor: "rgb(0 0 0 / 0%)" }), { empty: true });
    assert.deepEqual(at({ backgroundColor: "rgba(0, 0, 0, 0.4)" }), { w: 20, h: 30, x: 5, y: 6 });
});

test("text is measured from its own glyph box", () => {
    const { M } = setup();
    const tree = [{ rect: [0, 0, 400, 300], children: [
        { text: "Waiting for data", rect: [12, 40, 90, 14] },
        { text: "   \n  ", rect: [0, 0, 400, 300] },
    ] }];
    assert.deepEqual(measure(M, tree), { w: 90, h: 14, x: 12, y: 40 });
});

test("a hidden or faded-out parent hides everything inside it", () => {
    const { M } = setup();
    const inside = (style, attrs) => measure(M, [{ rect: [0, 0, 400, 300], style, attrs, children: [
        { rect: [10, 10, 50, 50], style: RED }, { text: "hi", rect: [10, 70, 20, 10] },
    ] }]);
    assert.deepEqual(inside({ opacity: "0" }), { empty: true }, "alert layer faded to 0");
    assert.deepEqual(inside({ display: "none" }), { empty: true });
    assert.deepEqual(inside({ contentVisibility: "hidden" }), { empty: true });
    assert.deepEqual(inside({}, { "data-spore-hidden": "true" }), { empty: true }, "Spore out-animation done");
    assert.deepEqual(inside({ opacity: "0.3" }), { w: 50, h: 70, x: 10, y: 10 });
});

test("visibility:hidden hides the box and its text, but a child can show itself again", () => {
    const { M } = setup();
    const tree = [{ rect: [0, 0, 300, 300], style: Object.assign({ visibility: "hidden" }, RED), children: [
        { text: "hidden text", rect: [0, 0, 100, 10] },
        { rect: [50, 60, 10, 10], style: Object.assign({ visibility: "visible" }, RED) },
        { rect: [200, 200, 10, 10], style: Object.assign({ visibility: "hidden" }, RED) },
    ] }];
    assert.deepEqual(measure(M, tree), { w: 10, h: 10, x: 50, y: 60 });
});

test("content clipped by an overflow parent, or outside the window, doesn't count", () => {
    const { M } = setup();
    // FF7's rotor: a wide track sliding inside an overflow:hidden viewport.
    const rotor = [{ rect: [20, 30, 200, 40], style: { overflow: "hidden", overflowX: "hidden" }, children: [
        { rect: [-600, 30, 1400, 40], style: RED },
    ] }];
    assert.deepEqual(measure(M, rotor), { w: 200, h: 40, x: 20, y: 30 });
    // An alert parked above the window, waiting to slide in.
    assert.deepEqual(measure(M, [{ rect: [0, -200, 100, 100], style: RED }]), { empty: true });
    assert.deepEqual(measure(M, [{ rect: [350, 250, 100, 100], style: RED }]), { w: 50, h: 50, x: 350, y: 250 });
    // A fully clipped child adds nothing.
    const gone = [{ rect: [0, 0, 100, 100], style: { overflowY: "auto" }, children: [{ rect: [0, 150, 50, 50], style: RED }] }];
    assert.deepEqual(measure(M, gone), { empty: true });
    // A clipping parent off screen hides its children entirely.
    const offscreen = [{ rect: [0, 500, 100, 100], style: { overflow: "hidden" }, children: [{ rect: [0, 0, 50, 50], style: RED }] }];
    assert.deepEqual(measure(M, offscreen), { empty: true });
});

test("position:fixed escapes its parent's clipping", () => {
    const { M } = setup();
    const tree = [{ rect: [0, 0, 50, 50], style: { overflow: "hidden" }, children: [
        { rect: [100, 100, 40, 40], style: Object.assign({ position: "fixed" }, RED) },
    ] }];
    assert.deepEqual(measure(M, tree), { w: 40, h: 40, x: 100, y: 100 });
});

test("media elements are measured as a whole, not their insides", () => {
    const { M } = setup();
    const tree = [{ tag: "svg", rect: [10, 10, 30, 30], children: [{ rect: [0, 0, 400, 300], style: RED }] }];
    assert.deepEqual(measure(M, tree), { w: 30, h: 30, x: 10, y: 10 });
});

test("scripts, styles and zero-size boxes are ignored", () => {
    const { M } = setup();
    const tree = [
        { tag: "script", rect: [0, 0, 400, 300], style: RED },
        { tag: "style", rect: [0, 0, 400, 300], style: RED },
        { rect: [10, 10, 0.5, 40], style: RED },
    ];
    assert.deepEqual(measure(M, tree), { empty: true });
});

test("a [data-mycelian-content] element limits measuring to itself", () => {
    const { M } = setup();
    const tree = [
        { rect: [0, 0, 400, 20], style: RED },
        { rect: [0, 100, 400, 200], attrs: { "data-mycelian-content": "" }, children: [{ rect: [5, 120, 60, 30], style: RED }] },
    ];
    assert.deepEqual(measure(M, tree), { w: 60, h: 30, x: 5, y: 120 });
    const hidden = [{ rect: [0, 0, 400, 300], style: { opacity: "0" }, children: [
        { rect: [0, 0, 400, 300], attrs: { "data-mycelian-content": "" }, children: [{ rect: [5, 5, 10, 10], style: RED }] },
    ] }];
    assert.deepEqual(measure(M, hidden), { empty: true }, "a hidden ancestor still hides it");
});

test("the page body: its own backdrop is ignored, a hidden or clipped body shows nothing", () => {
    const { M } = setup();
    const panel = [{ rect: [10, 10, 20, 20], style: RED }];
    assert.deepEqual(measure(M, panel, { bodyStyle: RED }), { w: 20, h: 20, x: 10, y: 10 });
    assert.deepEqual(measure(M, panel, { bodyStyle: { display: "none" } }), { empty: true });
    assert.deepEqual(measure(M, panel, { bodyStyle: { overflow: "hidden" }, bodyRect: [0, 0, 15, 15] }), { w: 5, h: 5, x: 10, y: 10 });
    assert.deepEqual(measure(M, panel, { bodyStyle: { overflow: "hidden" }, bodyRect: [500, 500, 15, 15] }), { empty: true });
});

test("measureDocument returns null when there is nothing to read yet", () => {
    const { M } = setup();
    assert.equal(M.measureDocument(null, 400, 300), null);
    assert.equal(M.measureDocument(frameDoc([]), 1, 300), null, "tiny window");
    const doc = frameDoc([]);
    doc.defaultView = null;
    assert.equal(M.measureDocument(doc, 400, 300), null);
});

test("measure reads the iframe's document and window size; errors are null", () => {
    const { M } = setup();
    const iframe = { contentDocument: frameDoc([{ rect: [1, 2, 3, 4], style: RED }]), clientWidth: 400, clientHeight: 300 };
    assert.deepEqual(plain(M.measure(iframe)), { w: 3, h: 4, x: 1, y: 2 });
    const offset = { contentDocument: iframe.contentDocument, clientWidth: 0, offsetWidth: 400, clientHeight: 0, offsetHeight: 300 };
    assert.deepEqual(plain(M.measure(offset)), { w: 3, h: 4, x: 1, y: 2 });
    const throwing = { get contentDocument() { throw new Error("cross-origin"); } };
    assert.equal(M.measure(throwing), null);
});

test("explicit sizes: a template's own report wins until it sends {auto: true}", () => {
    const { M } = setup();
    const sizes = M.createExplicitSizes();
    const win = { name: "frame" };
    const frames = [{ contentWindow: { name: "other" }, getAttribute: () => "x" }, { contentWindow: win, getAttribute: () => "chat" }];
    const msg = (data, source = win) => ({ data, source });
    assert.equal(sizes.handle(msg({ type: "something-else" }), frames), null);
    assert.equal(sizes.handle(null, frames), null);
    assert.equal(sizes.handle(msg({ type: "mycelian-content-size", width: 1, height: 1 }, { name: "stranger" }), frames), null);
    assert.equal(sizes.handle(msg({ type: "mycelian-content-size", width: "wide", height: 1 }), frames), null);
    assert.equal(sizes.get("chat"), null);
    assert.equal(sizes.handle(msg({ type: "mycelian-content-size", width: 300, height: 120 }), frames), "chat");
    assert.deepEqual(plain(sizes.get("chat")), { w: 300, h: 120, x: 0, y: 0 });
    assert.equal(sizes.handle(msg({ type: "mycelian-content-size", width: 0, height: 0 }), frames), "chat");
    assert.deepEqual(plain(sizes.get("chat")), { empty: true }, "0x0 means nothing showing");
    assert.equal(sizes.handle(msg({ type: "mycelian-content-size", auto: true }), frames), "chat");
    assert.equal(sizes.get("chat"), null, "back to measuring");
    sizes.handle(msg({ type: "mycelian-content-size", width: 5, height: 5 }), frames);
    sizes.clear("chat");
    assert.equal(sizes.get("chat"), null);
});

function fakeFrame(win, doc) {
    const listeners = {};
    return {
        contentDocument: doc,
        addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
        fire(type) { (listeners[type] || []).forEach((fn) => fn()); },
        listeners,
    };
}

test("watch observes the page, coalesces to one call per frame, polls, re-attaches on load and stops", () => {
    const { win, M } = setup();
    const doc = frameDoc([{ rect: [0, 0, 10, 10], style: RED }]);
    let fontsDone;
    doc.fonts = { ready: { then: (ok) => { fontsDone = ok; } } };
    doc.defaultView.MutationObserver = win.MutationObserver;
    doc.defaultView.ResizeObserver = win.ResizeObserver;
    const iframe = fakeFrame(win, doc);
    let calls = 0;
    const watcher = M.watch(iframe, () => { calls += 1; });
    win.__clock.tick(16);
    assert.equal(calls, 1, "measures once right away");
    const observers = win.MutationObserver.all || [];
    const mo = observers.find((o) => o.targets.includes(doc.documentElement) && o.opts && o.opts.subtree);
    assert.ok(mo, "mutation observer on the page");
    assert.deepEqual(plain(mo.opts.attributeFilter), ["class", "style", "hidden", "data-spore-hidden", "src", "open"]);
    mo.trigger([]);
    mo.trigger([]);
    win.__clock.tick(16);
    assert.equal(calls, 2, "bursts coalesce into one call per frame");
    fontsDone();
    win.__clock.tick(16);
    assert.equal(calls, 3);
    win.__clock.tick(1000);
    assert.equal(calls, 4, "safety poll");
    const before = calls;
    iframe.fire("load");
    win.__clock.tick(16);
    assert.equal(calls, before + 1, "re-attached after a reload");
    assert.equal(mo.disconnected, true, "old observers disconnected on reload");
    watcher.stop();
    win.__clock.tick(5000);
    assert.equal(calls, before + 1, "nothing after stop");
    assert.equal((iframe.listeners.load || []).length, 0);
});

test("watch tolerates frames it can't read", () => {
    const { win, M } = setup();
    const blocked = { addEventListener() {}, removeEventListener() {}, get contentDocument() { throw new Error("x"); } };
    const empty = { addEventListener() {}, removeEventListener() {}, contentDocument: null };
    let calls = 0;
    M.watch(blocked, () => { calls += 1; }).stop();
    const w = M.watch(empty, () => { calls += 1; });
    win.__clock.tick(1016);
    assert.equal(calls, 1, "only the poll fires");
    w.stop();
});

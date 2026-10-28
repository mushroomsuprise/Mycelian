// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/container_shell.js: the live container page. Measures locked
// templates inside their iframes (or takes explicit postMessage sizes), re-solves the layout with
// container_layout.js and moves slots / clips / iframes. The page markup mirrors
// containers.py render_shell.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript } from "./helpers/env.mjs";
import { Document } from "./helpers/dom.mjs";

const LAYOUT = "assets/default_assets/spore_studio/container_layout.js";
const SHELL = "assets/default_assets/spore_studio/container_shell.js";

function slot(id, over = {}) {
    return Object.assign({
        id, template: "t_" + id, x: 0, y: 0, w: 400, h: 300, z: 1, opacity: 100, visible: true,
        pointer_events: true, padding: { t: 0, r: 0, b: 0, l: 0 }, fit: "native",
        design_width: null, design_height: null, audio: { muted: false, volume: 100 }, link: null,
    }, over);
}
function link(id, over = {}) {
    return Object.assign({ id, axis: "vertical", role: "flexible", min_px: 0, min_pct: 0, max_px: null, max_pct: null }, over);
}

// A fake same-origin iframe document whose elements have fixed rects and computed styles.
function frameDoc(boxes) {
    const doc = new Document();
    const styles = new Map();
    for (const b of boxes) {
        const el = doc.createElement("div");
        el._rect = { left: b.left, top: b.top, width: b.width, height: b.height };
        styles.set(el, { display: b.display || "block", visibility: b.visibility || "visible", opacity: b.opacity === undefined ? "1" : b.opacity });
        doc.body.appendChild(el);
    }
    doc.defaultView = { getComputedStyle: (el) => styles.get(el) };
    return doc;
}

function setup(container, opts = {}) {
    const win = makeWindow();
    const doc = win.document;
    const slots = container.slots.map((s) =>
        '<div class="mc-slot" data-slot="' + s.id + '">' +
        (opts.noClip && opts.noClip.includes(s.id) ? "" : '<div class="mc-clip"><iframe data-slot="' + s.id + '"></iframe></div>') +
        "</div>").join("");
    const data = opts.rawData !== undefined ? opts.rawData : JSON.stringify({ container });
    doc.body.innerHTML = '<div id="mc-container">' + slots + '</div><script id="mc-container-data" type="application/json">' + data + "</script>";
    for (const f of doc.querySelectorAll("iframe")) {
        f.contentWindow = { id: f.getAttribute("data-slot") };
        f._rect = { width: 400, height: 300 };
        const boxes = (opts.frames || {})[f.getAttribute("data-slot")];
        if (boxes) { f.contentDocument = frameDoc(boxes); }
    }
    if (!opts.noLayout) { loadScript(win, LAYOUT); }
    const solveCalls = [];
    if (win.MycelianContainerLayout) {
        const real = win.MycelianContainerLayout.solve;
        win.MycelianContainerLayout.solve = (c, used) => { solveCalls.push(JSON.parse(JSON.stringify(used))); return real(c, used); };
    }
    const { internals: I } = loadScript(win, SHELL);
    return { win, doc, I, clock: win.__clock, solveCalls };
}
const slotEl = (doc, id) => doc.querySelector('.mc-slot[data-slot="' + id + '"]');
const frameEl = (doc, id) => doc.querySelector('iframe[data-slot="' + id + '"]');
const px = (el, ...props) => props.map((p) => el.style[p]);

test("does nothing without the data block, the solver or valid JSON", () => {
    const c = { width: 800, height: 600, slots: [slot("a")] };
    const noLayout = setup(c, { noLayout: true });
    noLayout.clock.tick(1000);
    assert.equal(slotEl(noLayout.doc, "a").style.left, "");
    const bad = setup(c, { rawData: "{not json" });
    bad.clock.tick(1000);
    assert.equal(bad.clock.pending(), 0);
    const win = makeWindow();
    loadScript(win, LAYOUT);
    loadScript(win, SHELL);
    assert.equal(win.__clock.pending(), 0);
});

test("first relayout after 120ms positions every slot, clip and iframe", () => {
    const c = {
        width: 800, height: 600, slots: [
            slot("a", { x: 10, y: 20, w: 300, h: 200, z: 3, opacity: 50, padding: { t: 5, r: 6, b: 7, l: 8 } }),
            slot("b", { x: 400, y: 0, opacity: 0 }),
        ],
    };
    const { doc, clock } = setup(c);
    clock.tick(119);
    assert.equal(slotEl(doc, "a").style.left, "");
    clock.tick(1);
    const a = slotEl(doc, "a");
    assert.deepEqual(px(a, "left", "top", "width", "height", "zIndex", "opacity"), ["10px", "20px", "300px", "200px", "3", "0.5"]);
    assert.deepEqual(px(a.querySelector(".mc-clip"), "left", "top", "width", "height"), ["8px", "5px", "286px", "188px"]);
    assert.deepEqual(px(frameEl(doc, "a"), "left", "top", "width", "height", "transform"), ["0px", "0px", "286px", "188px", ""]);
    assert.equal(slotEl(doc, "b").style.opacity, "0");
});

test("scaled slots are scaled and centred in their content box", () => {
    const c = { width: 800, height: 600, slots: [slot("s", { w: 400, h: 300, fit: "scale", design_width: 1600, design_height: 900 })] };
    const { doc, clock } = setup(c);
    clock.tick(120);
    const f = frameEl(doc, "s");
    assert.deepEqual(px(f, "width", "height", "transformOrigin", "transform", "left", "top"),
        ["1600px", "900px", "top left", "scale(0.25)", "0px", "37.5px"]);
});

test("locked slots shrink to the measured visible content and shift the iframe on the linked axis", () => {
    const c = {
        width: 800, height: 600, slots: [
            slot("head", { y: 0, h: 300, link: link("col", { role: "locked" }) }),
            slot("body", { y: 300, h: 300, link: link("col") }),
        ],
    };
    const frames = {
        head: [
            { left: 20, top: 40, width: 100, height: 50 },
            { left: 0, top: 0, width: 400, height: 300 }, // full-viewport background: ignored
            { left: 50, top: 60, width: 200, height: 70 },
            { left: 0, top: 0, width: 50, height: 50, display: "none" },
            { left: 0, top: 0, width: 50, height: 50, visibility: "hidden" },
            { left: 0, top: 0, width: 50, height: 50, opacity: "0" },
            { left: 0, top: 0, width: 0.5, height: 50 },
            { left: 500, top: 10, width: 50, height: 50 }, // off screen to the right
        ],
    };
    const { doc, clock, solveCalls } = setup(c, { frames });
    clock.tick(120);
    assert.deepEqual(solveCalls[0], { head: { w: 230, h: 90 } });
    assert.deepEqual(px(slotEl(doc, "head"), "top", "height"), ["0px", "90px"]);
    assert.deepEqual(px(slotEl(doc, "body"), "top", "height"), ["90px", "510px"]);
    const f = frameEl(doc, "head");
    assert.deepEqual(px(f, "left", "top", "height"), ["0px", "-40px", "300px"], "content moved up to the clip top");
});

test("horizontal locked slots shift left by the content origin", () => {
    const c = {
        width: 800, height: 600, slots: [
            slot("l", { x: 0, w: 400, link: link("row", { axis: "horizontal", role: "locked" }) }),
            slot("r", { x: 400, w: 400, link: link("row", { axis: "horizontal" }) }),
        ],
    };
    const { doc, clock } = setup(c, { frames: { l: [{ left: 30, top: 10, width: 100.2, height: 20 }] } });
    clock.tick(120);
    assert.equal(slotEl(doc, "l").style.width, "101px", "measured width is rounded up");
    assert.deepEqual(px(frameEl(doc, "l"), "left", "top"), ["-30px", "0px"]);
});

test("unmeasurable frames fall back to the designated size", () => {
    const c = { width: 800, height: 600, slots: [
        slot("a", { y: 0, h: 200, link: link("L", { role: "locked" }) }),
        slot("b", { y: 1, h: 200, link: link("L", { role: "locked" }) }),
        slot("c", { y: 2, h: 200, link: link("L", { role: "locked" }) }),
        slot("d", { y: 3, h: 200, link: link("L", { role: "locked" }) }),
        slot("e", { y: 4, h: 100, link: link("L", { role: "locked", }), fit: "scale" }),
        slot("f", { y: 5, h: 100, link: link("L") }),
    ] };
    const { doc, clock, solveCalls, win } = setup(c, { frames: { b: [], c: [{ left: 0, top: 0, width: 10, height: 10 }], d: [{ left: 0, top: 0, width: 10, height: 10 }] } });
    // a: no document; b: nothing visible; c: frame too small; d: getComputedStyle throws
    frameEl(doc, "c")._rect = { width: 1, height: 300 };
    frameEl(doc, "d").contentDocument.defaultView.getComputedStyle = () => { throw new Error("cross-origin"); };
    const noBody = frameEl(doc, "e");
    noBody.contentDocument = { body: null };
    clock.tick(120);
    assert.deepEqual(solveCalls[0], {});
    assert.deepEqual(px(slotEl(doc, "a"), "height"), ["200px"]);
    win.__clock.tick(0);
});

test("explicit mycelian-content-size messages win over measuring", () => {
    const c = { width: 800, height: 600, slots: [
        slot("a", { y: 0, h: 300, link: link("L", { role: "locked" }) }),
        slot("b", { y: 300, h: 300, link: link("L") }),
    ] };
    const { doc, clock, win, solveCalls } = setup(c, { frames: { a: [{ left: 10, top: 10, width: 50, height: 50 }] } });
    clock.tick(120);
    const source = frameEl(doc, "a").contentWindow;
    const post = (data, src = source) => win.dispatchEvent(Object.assign(new win.Event("message"), { data, source: src }));
    post(null);
    post({ type: "other" });
    post({ type: "mycelian-content-size", width: 10, height: 10 }, { stranger: true });
    post({ type: "mycelian-content-size", width: "x", height: 5 });
    clock.tick(500);
    assert.deepEqual(solveCalls.at(-1), { a: { w: 50, h: 50 } });
    post({ type: "mycelian-content-size", width: "120", height: 80 });
    clock.tick(120);
    assert.deepEqual(solveCalls.at(-1), { a: { w: 120, h: 80, explicit: true } });
    assert.equal(slotEl(doc, "a").style.height, "80px");
    assert.deepEqual(px(frameEl(doc, "a"), "top"), ["0px"], "explicit sizes use a zero origin");
});

test("relayout is throttled and skips DOM writes when nothing changed", () => {
    const c = { width: 800, height: 600, slots: [slot("a")] };
    const { doc, clock, I, solveCalls } = setup(c);
    clock.tick(120);
    assert.equal(solveCalls.length, 1);
    slotEl(doc, "a").style.left = "999px";
    clock.tick(500); // interval tick schedules one more relayout
    clock.tick(120);
    assert.equal(solveCalls.length, 2);
    assert.equal(slotEl(doc, "a").style.left, "999px", "same layout: no rewrite");
    I.schedule();
    I.schedule();
    clock.tick(120);
    assert.equal(solveCalls.length, 3, "two schedule() calls inside 120ms run once");
});

test("iframe load re-schedules and observes the frame document; cross-origin is tolerated", () => {
    const c = { width: 800, height: 600, slots: [slot("a"), slot("b"), slot("c")] };
    const { doc, clock, win, solveCalls } = setup(c, { frames: { a: [] } });
    clock.tick(120);
    const before = solveCalls.length;
    frameEl(doc, "a").dispatchEvent(new win.Event("load"));
    const obs = win.__observers.filter((o) => o.targets.length).at(-1);
    assert.equal(obs.targets[0], frameEl(doc, "a").contentDocument.documentElement);
    assert.deepEqual({ ...obs.opts }, { childList: true, subtree: true, attributes: true });
    clock.tick(120);
    assert.equal(solveCalls.length, before + 1);
    frameEl(doc, "b").dispatchEvent(new win.Event("load")); // no contentDocument
    Object.defineProperty(frameEl(doc, "c"), "contentDocument", { get() { throw new Error("SecurityError"); } });
    assert.doesNotThrow(() => frameEl(doc, "c").dispatchEvent(new win.Event("load")));
});

test("slots without markup or without a clip/iframe are skipped safely", () => {
    const c = { width: 800, height: 600, slots: [slot("a"), slot("gone"), slot("bare")] };
    const { doc, clock } = setup(c, { noClip: ["bare"] });
    slotEl(doc, "gone").remove();
    clock.tick(120);
    assert.equal(slotEl(doc, "bare").style.width, "400px");
    assert.equal(slotEl(doc, "a").style.width, "400px");
});

test("slotMeta finds slots by id; a container without slots is handled", () => {
    const { I } = setup({ width: 10, height: 10, slots: [slot("a")] });
    assert.equal(I.slotMeta("a").id, "a");
    assert.equal(I.slotMeta("zz"), null);
    I.__eval("container = { width: 10, height: 10 }");
    assert.equal(I.slotMeta("a"), null);
});

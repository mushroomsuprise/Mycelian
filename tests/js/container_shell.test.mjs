// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/container_shell.js: the live container page. Measures
// "fits its content" templates with container_measure.js (or takes their own postMessage
// sizes), re-solves the layout with container_layout.js and moves slots / clips / iframes.
// The page markup mirrors containers.py render_shell.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript } from "./helpers/env.mjs";
import { frameDoc, RED } from "./helpers/frame_doc.mjs";

const LAYOUT = "assets/default_assets/spore_studio/container_layout.js";
const MEASURE = "assets/default_assets/spore_studio/container_measure.js";
const SHELL = "assets/default_assets/spore_studio/container_shell.js";

function slot(id, over = {}) {
    return Object.assign({
        id, template: "t_" + id, x: 0, y: 0, w: 400, h: 300, z: 1, opacity: 100, visible: true,
        pointer_events: true, padding: { t: 0, r: 0, b: 0, l: 0 }, fit: "native",
        design_width: null, design_height: null, audio: { muted: false, volume: 100 }, link: null,
    }, over);
}
function link(id, over = {}) {
    return Object.assign({ id, axis: "vertical", role: "flexible", when_empty: "collapse", min_px: 0, min_pct: 0, max_px: null, max_pct: null }, over);
}
const box = (left, top, width, height, style = RED) => ({ rect: [left, top, width, height], style });

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
        const tree = (opts.frames || {})[f.getAttribute("data-slot")];
        if (tree) { f.contentDocument = frameDoc(tree); }
    }
    if (!opts.noLayout) { loadScript(win, LAYOUT); }
    if (!opts.noMeasure) { loadScript(win, MEASURE, { expose: false }); }
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

test("does nothing without the data block, the solver, the measurer or valid JSON", () => {
    const c = { width: 800, height: 600, slots: [slot("a")] };
    for (const opts of [{ noLayout: true }, { noMeasure: true }, { rawData: "{not json" }]) {
        const run = setup(c, opts);
        run.clock.tick(2000);
        assert.equal(slotEl(run.doc, "a").style.left, "", JSON.stringify(opts));
    }
    const win = makeWindow();
    loadScript(win, LAYOUT);
    loadScript(win, MEASURE, { expose: false });
    loadScript(win, SHELL);
    assert.equal(win.__clock.pending(), 0);
});

test("first relayout right away positions every slot, clip and iframe", () => {
    const c = {
        width: 800, height: 600, slots: [
            slot("a", { x: 10, y: 20, w: 300, h: 200, z: 3, opacity: 50, padding: { t: 5, r: 6, b: 7, l: 8 } }),
            slot("b", { x: 400, y: 0, opacity: 0 }),
        ],
    };
    const { doc, clock } = setup(c);
    clock.tick(0);
    const a = slotEl(doc, "a");
    assert.deepEqual(px(a, "left", "top", "width", "height", "zIndex", "opacity"), ["10px", "20px", "300px", "200px", "3", "0.5"]);
    assert.deepEqual(px(a.querySelector(".mc-clip"), "left", "top", "width", "height"), ["8px", "5px", "286px", "188px"]);
    assert.deepEqual(px(frameEl(doc, "a"), "left", "top", "width", "height", "transform"), ["0px", "0px", "286px", "188px", ""]);
    assert.equal(slotEl(doc, "b").style.opacity, "0");
});

test("scaled slots are scaled and centred in their content box", () => {
    const c = { width: 800, height: 600, slots: [slot("s", { w: 400, h: 300, fit: "scale", design_width: 1600, design_height: 900 })] };
    const { doc, clock } = setup(c);
    clock.tick(0);
    const f = frameEl(doc, "s");
    assert.deepEqual(px(f, "width", "height", "transformOrigin", "transform", "left", "top"),
        ["1600px", "900px", "top left", "scale(0.25)", "0px", "37.5px"]);
});

test("FF7 + chat: chat gets the room FF7 isn't using; FF7's iframe keeps its drawn size", () => {
    const c = {
        width: 800, height: 900, slots: [
            slot("chat", { y: 100, h: 400, link: link("col") }),
            slot("ff7", { y: 500, h: 300, link: link("col", { role: "locked" }) }),
            slot("title", { y: 0, h: 80 }),
        ],
    };
    // FF7: a transparent full-height root (ignored), party panel and enemies panel.
    const ff7 = [{ rect: [0, 0, 400, 300], children: [box(4, 4, 392, 100), box(4, 110, 392, 60)] }];
    const { doc, clock, solveCalls } = setup(c, { frames: { ff7 } });
    clock.tick(0);
    assert.deepEqual(solveCalls.at(-1), { ff7: { w: 392, h: 166 } });
    // Shared space is y=100..800: FF7 takes 166, chat the other 534.
    assert.deepEqual(px(slotEl(doc, "chat"), "top", "height"), ["100px", "534px"]);
    assert.deepEqual(px(frameEl(doc, "chat"), "height"), ["534px"], "chat's resolution really changes");
    assert.deepEqual(px(slotEl(doc, "ff7"), "top", "height"), ["634px", "166px"]);
    assert.deepEqual(px(frameEl(doc, "ff7"), "top", "height"), ["-4px", "300px"], "content moved up to the clip top");
    assert.deepEqual(px(slotEl(doc, "title"), "top", "height"), ["0px", "80px"], "outside the space: untouched");
});

test("a template showing nothing closes; chat takes the whole space", () => {
    const c = { width: 800, height: 600, slots: [
        slot("chat", { y: 0, h: 300, link: link("col") }),
        slot("alerts", { y: 300, h: 300, link: link("col", { role: "locked" }) }),
    ] };
    const idle = [{ rect: [0, 0, 400, 300], style: { opacity: "0" }, children: [box(50, 50, 300, 200)] }];
    const { doc, clock, solveCalls } = setup(c, { frames: { alerts: idle } });
    clock.tick(0);
    assert.deepEqual(solveCalls.at(-1), { alerts: { empty: true } });
    assert.deepEqual(px(slotEl(doc, "alerts"), "height"), ["0px"]);
    assert.deepEqual(px(slotEl(doc, "chat"), "height"), ["600px"]);
    assert.deepEqual(px(frameEl(doc, "alerts"), "top"), ["0px"], "no origin when empty");
});

test("horizontal fits-content slots shift left by the content origin", () => {
    const c = {
        width: 800, height: 600, slots: [
            slot("l", { x: 0, w: 400, link: link("row", { axis: "horizontal", role: "locked" }) }),
            slot("r", { x: 400, w: 400, link: link("row", { axis: "horizontal" }) }),
        ],
    };
    const { doc, clock } = setup(c, { frames: { l: [box(30, 10, 100.2, 20)] } });
    clock.tick(0);
    assert.equal(slotEl(doc, "l").style.width, "101px", "measured width is rounded up");
    assert.deepEqual(px(frameEl(doc, "l"), "left", "top"), ["-30px", "0px"]);
});

test("frames that can't be read yet keep their drawn size", () => {
    const c = { width: 800, height: 600, slots: [
        slot("a", { y: 0, h: 200, link: link("L", { role: "locked" }) }),
        slot("c", { y: 200, h: 200, link: link("L", { role: "locked" }) }),
        slot("e", { y: 400, h: 100, link: link("L", { role: "locked" }), fit: "scale" }),
        slot("f", { y: 500, h: 100, link: link("L") }),
    ] };
    const { doc, clock, solveCalls } = setup(c, { frames: { c: [box(0, 0, 10, 10)] } });
    // a: no document yet; c: window too small to measure; e: scaled (never measured)
    frameEl(doc, "c")._rect = { width: 1, height: 300 };
    clock.tick(0);
    assert.deepEqual(solveCalls.at(-1), {});
    assert.deepEqual(px(slotEl(doc, "a"), "height"), ["200px"]);
    assert.deepEqual(px(slotEl(doc, "c"), "height"), ["200px"]);
});

test("a template's own mycelian-content-size message wins until it sends auto", () => {
    const c = { width: 800, height: 600, slots: [
        slot("a", { y: 0, h: 300, link: link("L", { role: "locked" }) }),
        slot("b", { y: 300, h: 300, link: link("L") }),
    ] };
    const { doc, clock, win, solveCalls } = setup(c, { frames: { a: [box(10, 10, 50, 50)] } });
    clock.tick(0);
    const source = frameEl(doc, "a").contentWindow;
    const post = (data, src = source) => win.dispatchEvent(Object.assign(new win.Event("message"), { data, source: src }));
    post({ type: "other" });
    post({ type: "mycelian-content-size", width: 10, height: 10 }, { stranger: true });
    clock.tick(0);
    assert.deepEqual(solveCalls.at(-1), { a: { w: 50, h: 50 } });
    post({ type: "mycelian-content-size", width: "120", height: 80 });
    clock.tick(0);
    assert.deepEqual(solveCalls.at(-1), { a: { w: 120, h: 80 } });
    assert.equal(slotEl(doc, "a").style.height, "80px");
    assert.deepEqual(px(frameEl(doc, "a"), "top"), ["0px"], "reported sizes use a zero origin");
    post({ type: "mycelian-content-size", auto: true });
    clock.tick(0);
    assert.deepEqual(solveCalls.at(-1), { a: { w: 50, h: 50 } }, "back to measuring");
});

test("changes inside a template relayout on the next frame; unchanged layouts aren't rewritten", () => {
    const c = { width: 800, height: 600, slots: [
        slot("chat", { y: 0, h: 300, link: link("col") }),
        slot("ff7", { y: 300, h: 300, link: link("col", { role: "locked" }) }),
    ] };
    const { doc, clock, win, solveCalls } = setup(c, { frames: { ff7: [box(0, 0, 400, 100)] } });
    clock.tick(16);
    assert.equal(slotEl(doc, "ff7").style.height, "100px");
    const page = frameEl(doc, "ff7").contentDocument;
    const enemies = page.createElement("div");
    enemies._rect = { left: 0, top: 100, width: 400, height: 80 };
    page.__styles.set(enemies, Object.assign({}, page.defaultView.getComputedStyle(page.body), RED));
    page.body.appendChild(enemies);
    const observer = win.MutationObserver.all.find((o) => o.targets.includes(page.documentElement));
    observer.trigger([]);
    clock.tick(16);
    assert.equal(slotEl(doc, "ff7").style.height, "180px", "enemies panel appeared");
    assert.equal(slotEl(doc, "chat").style.height, "420px");
    const calls = solveCalls.length;
    slotEl(doc, "chat").style.left = "999px";
    clock.tick(1016);
    assert.equal(solveCalls.length, calls + 1, "safety poll re-solved");
    assert.equal(slotEl(doc, "chat").style.left, "999px", "same layout: no rewrite");
});

test("slots without markup or without a clip/iframe are skipped safely", () => {
    const c = { width: 800, height: 600, slots: [slot("a"), slot("gone"), slot("bare")] };
    const { doc, clock } = setup(c, { noClip: ["bare"] });
    slotEl(doc, "gone").remove();
    clock.tick(0);
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

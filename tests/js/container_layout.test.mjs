// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/container_layout.js: the size-link solver (mirror of
// modules/spore_studio/containers.py solve_layout). Hand-computed cases here; a randomized
// JS-vs-Python parity check lives in tests/modules/test_root__frontend_assets.py.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, plain, xfail } from "./helpers/env.mjs";

const FILE = "assets/default_assets/spore_studio/container_layout.js";

function setup() {
    const win = makeWindow();
    const { internals: I } = loadScript(win, FILE, { expose: true, anchor: "return { solve: solve };" });
    return { win, L: win.MycelianContainerLayout, I };
}

function slot(id, over = {}) {
    return Object.assign({
        id, template: "t_" + id, x: 0, y: 0, w: 640, h: 360, z: 1, opacity: 100, visible: true,
        pointer_events: true, padding: { t: 0, r: 0, b: 0, l: 0 }, fit: "native",
        design_width: null, design_height: null, audio: { muted: false, volume: 100 }, link: null,
    }, over);
}
function link(id, over = {}) {
    return Object.assign({ id, axis: "vertical", role: "flexible", min_px: 0, min_pct: 0, max_px: null, max_pct: null }, over);
}
function container(slots, over = {}) {
    return Object.assign({ width: 1920, height: 1080, spacing: 0, background: "transparent", slots }, over);
}
const rects = (res) => Object.fromEntries(res.slots.map((s) => [s.id, [s.x, s.y, s.w, s.h]]));

test("exports solve on window and on module.exports (UMD)", () => {
    const { L } = setup();
    assert.deepEqual(Object.keys(L), ["solve"]);
    const mod = { exports: {} };
    const win = makeWindow({ extra: { module: mod } });
    loadScript(win, FILE, { expose: true, anchor: "return { solve: solve };" });
    assert.equal(mod.exports, win.MycelianContainerLayout);
    assert.equal(typeof mod.exports.solve, "function");
});

test("pyRound rounds half to even like Python's round()", () => {
    const { I } = setup();
    assert.deepEqual([0.5, 1.5, 2.5, 3.5, -0.5, -1.5, 2.4, 2.6, 7].map(I.pyRound), [0, 2, 2, 4, 0, -2, 2, 3, 7]);
});

test("effMin takes the larger of px and percent of the span", () => {
    const { I } = setup();
    assert.equal(I.effMin({ min_px: 100, min_pct: 10 }, 2000), 200);
    assert.equal(I.effMin({ min_px: 300, min_pct: 10 }, 2000), 300);
    assert.equal(I.effMin({}, 2000), 0);
    assert.equal(I.effMin({ min_pct: 25 }, 2), 0, "0.5 rounds to even 0");
});

test("effMax is the smaller cap or null", () => {
    const { I } = setup();
    assert.equal(I.effMax({ max_px: null, max_pct: null }, 1000), null);
    assert.equal(I.effMax({}, 1000), null);
    assert.equal(I.effMax({ max_px: 300 }, 1000), 300);
    assert.equal(I.effMax({ max_pct: 50 }, 1000), 500);
    assert.equal(I.effMax({ max_px: 600, max_pct: 50 }, 1000), 500);
    assert.equal(I.effMax({ max_px: 0 }, 1000), 0);
});

test("axisPads, designated and contentBox", () => {
    const { I } = setup();
    const s = slot("a", { w: 100, h: 50, padding: { t: 1, r: 2, b: 3, l: 4 } });
    assert.deepEqual(plain(I.axisPads(s, "vertical")), [1, 3]);
    assert.deepEqual(plain(I.axisPads(s, "horizontal")), [4, 2]);
    assert.equal(I.designated(s, "vertical"), 50);
    assert.equal(I.designated(s, "horizontal"), 100);
    assert.deepEqual(plain(I.contentBox(s, 100, 50)), { x: 4, y: 1, w: 94, h: 46 });
    assert.deepEqual(plain(I.contentBox(s, 3, 2)), { x: 4, y: 1, w: 0, h: 0 });
});

test("measuredContent reads used[id][w|h] unless scaled or missing", () => {
    const { I } = setup();
    const s = slot("a");
    assert.equal(I.measuredContent(s, "vertical", null), null);
    assert.equal(I.measuredContent(s, "vertical", {}), null);
    assert.equal(I.measuredContent(s, "vertical", { a: 5 }), null);
    assert.equal(I.measuredContent(s, "vertical", { a: { w: 9 } }), null);
    assert.equal(I.measuredContent(s, "vertical", { a: { h: null } }), null);
    assert.equal(I.measuredContent(s, "vertical", { a: { h: "abc" } }), null);
    assert.equal(I.measuredContent(s, "vertical", { a: { h: "120.5" } }), 120.5);
    assert.equal(I.measuredContent(s, "horizontal", { a: { w: 7 } }), 7);
    assert.equal(I.measuredContent(slot("a", { fit: "scale" }), "vertical", { a: { h: 7 } }), null);
});

test("lockedOuter shrinks to measured content plus padding within min and designated size", () => {
    const { I } = setup();
    const s = slot("a", { h: 300, padding: { t: 10, r: 0, b: 20, l: 0 }, link: link("L", { role: "locked" }) });
    const w = [];
    assert.equal(I.lockedOuter(s, "vertical", 1080, null, w), 300, "unmeasured: designated size");
    assert.equal(I.lockedOuter(s, "vertical", 1080, { a: { h: 100.5 } }, w), 130, "100.5 -> 100 (even) + 30");
    assert.equal(I.lockedOuter(s, "vertical", 1080, { a: { h: 5000 } }, w), 300, "capped by content box");
    assert.equal(I.lockedOuter(s, "vertical", 1080, { a: { h: -40 } }, w), 30, "negative measure is 0");
    const minned = slot("a", { h: 300, link: link("L", { role: "locked", min_px: 200 }) });
    assert.equal(I.lockedOuter(minned, "vertical", 1080, { a: { h: 50 } }, w), 200);
    const capped = slot("a", { h: 300, link: link("L", { role: "locked", max_pct: 10 }) });
    assert.equal(I.lockedOuter(capped, "vertical", 1000, null, w), 100);
    assert.deepEqual(w, []);
});

test("lockedOuter warns and keeps the designated size when its minimum is above it", () => {
    const { I } = setup();
    const w = [];
    const s = slot("a", { h: 100, link: link("L", { role: "locked", min_px: 500 }) });
    assert.equal(I.lockedOuter(s, "vertical", 1080, null, w), 100);
    assert.equal(w.length, 1);
    assert.equal(w[0].code, "min_above_designated");
    assert.equal(w[0].link, "L");
});

test("splitInt distributes integers by weight, largest remainders first, ties by id", () => {
    const { I } = setup();
    assert.deepEqual(plain(I.splitInt(10, [])), {});
    assert.deepEqual(plain(I.splitInt(0, [["a", 1], ["b", 2]])), { a: 0, b: 0 });
    assert.deepEqual(plain(I.splitInt(-5, [["a", 1]])), { a: 0 });
    assert.deepEqual(plain(I.splitInt(10, [["a", 1], ["b", 1], ["c", 1]])), { a: 4, b: 3, c: 3 });
    assert.deepEqual(plain(I.splitInt(10, [["c", 1], ["b", 1], ["a", 1]])), { c: 3, b: 3, a: 4 });
    assert.deepEqual(plain(I.splitInt(100, [["a", 1], ["b", 3]])), { a: 25, b: 75 });
    assert.deepEqual(plain(I.splitInt(7, [["a", 0], ["b", -4]])), { a: 4, b: 3 }, "weights below 1 count as 1");
    assert.deepEqual(plain(I.splitInt(5, [["a", 2], ["b", 1]])), { a: 3, b: 2 });
    assert.deepEqual(plain(I.splitInt(1, [["b", 1], ["a", 1]])), { b: 0, a: 1 });
    assert.deepEqual(plain(I.splitInt(1, [["a", 1], ["b", 1]])), { a: 1, b: 0 });
});

test("distributeFlexible: empty, min overflow, plain split", () => {
    const { I } = setup();
    assert.deepEqual(plain(I.distributeFlexible(100, [])), [{}, false]);
    assert.deepEqual(plain(I.distributeFlexible(100, [{ id: "a", weight: 1, min: 80, max: null }, { id: "b", weight: 1, min: 40, max: null }])),
        [{ a: 80, b: 40 }, true]);
    assert.deepEqual(plain(I.distributeFlexible(100, [{ id: "a", weight: 1, min: -5, max: null }, { id: "b", weight: 3, min: 0 }])),
        [{ a: 25, b: 75 }, false]);
});

test("distributeFlexible freezes slots under their minimum before re-splitting", () => {
    const { I } = setup();
    const [sizes, overflow] = I.distributeFlexible(1000, [
        { id: "a", weight: 100, min: 300, max: null },
        { id: "b", weight: 900, min: 0, max: null },
    ]);
    assert.deepEqual(plain(sizes), { a: 300, b: 700 });
    assert.equal(overflow, false);
});

test("distributeFlexible caps slots over their maximum and gives the rest away", () => {
    const { I } = setup();
    const [sizes] = I.distributeFlexible(1000, [
        { id: "a", weight: 1, min: 0, max: 200 },
        { id: "b", weight: 1, min: 0, max: null },
    ]);
    assert.deepEqual(plain(sizes), { a: 200, b: 800 });
    const [allCapped] = I.distributeFlexible(1000, [{ id: "a", weight: 1, min: 0, max: 100 }]);
    assert.deepEqual(plain(allCapped), { a: 100 });
});

test("distributeFlexible: min first, then a contradictory smaller max wins (same as containers.py)", () => {
    const { I } = setup();
    // shares 50/50; a is under min 60 and frozen; next round b's 40 is over its max 20
    const [sizes, overflow] = I.distributeFlexible(100, [
        { id: "a", weight: 1, min: 60, max: null },
        { id: "b", weight: 1, min: 39, max: 20 },
    ]);
    assert.equal(overflow, false);
    assert.deepEqual(plain(sizes), { a: 60, b: 20 });
    const [s2, o2] = I.distributeFlexible(10, [{ id: "a", weight: 1, min: 0, max: -5 }]);
    assert.equal(o2, false);
    assert.deepEqual(plain(s2), { a: -5 });
    // (the `budget < 0` overflow exit can't be reached: frozen minimums never exceed the budget)
});

test("iframeBox: scale uses design size, locked native keeps the designated axis", () => {
    const { I } = setup();
    const pad = { t: 5, r: 6, b: 7, l: 8 };
    const content = { x: 8, y: 5, w: 100, h: 50 };
    assert.deepEqual(plain(I.iframeBox(slot("a", { fit: "scale", design_width: 1920, design_height: 0, w: 300, h: 200, padding: pad }), "vertical", content)),
        { w: 1920, h: 200, left: 8, top: 5 });
    assert.deepEqual(plain(I.iframeBox(slot("a", { fit: "scale", w: 0, h: 0, padding: pad }), null, content)), { w: 1, h: 1, left: 8, top: 5 });
    const locked = slot("a", { w: 300, h: 200, padding: pad, link: link("L", { role: "locked" }) });
    assert.deepEqual(plain(I.iframeBox(locked, "vertical", content)), { w: 100, h: 188, left: 8, top: 5 });
    assert.deepEqual(plain(I.iframeBox(locked, "horizontal", content)), { w: 286, h: 50, left: 8, top: 5 });
    const flexible = slot("a", { padding: pad, link: link("L") });
    assert.deepEqual(plain(I.iframeBox(flexible, "vertical", content)), { w: 100, h: 50, left: 8, top: 5 });
    assert.deepEqual(plain(I.iframeBox(slot("a", { fit: undefined, padding: pad }), null, content)), { w: 100, h: 50, left: 8, top: 5 });
    const lockedTiny = slot("a", { w: 5, h: 5, padding: pad, link: link("L", { role: "locked" }) });
    assert.deepEqual(plain(I.iframeBox(lockedTiny, "vertical", content)).h, 0);
});

test("solve leaves unlinked slots where they are and copies their fields", () => {
    const { L } = setup();
    const s = slot("a", { x: 10, y: 20, w: 300, h: 100, z: 3, opacity: 50, padding: { t: 1, r: 2, b: 3, l: 4 } });
    const res = plain(L.solve(container([s], { spacing: undefined, background: "#000000" })));
    assert.equal(res.spacing, 0);
    assert.equal(res.background, "#000000");
    assert.deepEqual(res.warnings, []);
    assert.deepEqual(res.slots[0], {
        id: "a", template: "t_a", visible: true, x: 10, y: 20, w: 300, h: 100, z: 3, opacity: 50, pointer_events: true,
        fit: "native", design_width: null, design_height: null, audio: { muted: false, volume: 100 },
        padding: { t: 1, r: 2, b: 3, l: 4 }, role: null, axis: null, link_id: null, designated_w: 300, designated_h: 100,
        content: { x: 4, y: 1, w: 294, h: 96 }, iframe: { w: 294, h: 96, left: 4, top: 1 },
    });
});

test("solve with no slots", () => {
    const { L } = setup();
    assert.deepEqual(plain(L.solve({ width: 10, height: 20 })), { width: 10, height: 20, spacing: 0, warnings: [], slots: [] });
});

test("vertical link: locked header shrinks to content, flexible slots share the rest by weight", () => {
    const { L } = setup();
    const c = container([
        slot("chat", { y: 400, h: 600, link: link("col") }),
        slot("header", { y: 0, h: 200, link: link("col", { role: "locked" }) }),
        slot("goal", { y: 900, h: 200, link: link("col") }),
        slot("free", { x: 1000, y: 5, w: 100, h: 100 }),
    ], { spacing: 10 });
    const res = L.solve(c, { header: { h: 80 } });
    const r = rects(res);
    assert.deepEqual(r.header, [0, 0, 640, 80]);
    // 1080 - 2*10 - 80 = 980 split 600:200 -> 735 / 245
    assert.deepEqual(r.chat, [0, 90, 640, 735]);
    assert.deepEqual(r.goal, [0, 835, 640, 245]);
    assert.deepEqual(r.free, [1000, 5, 100, 100]);
    const header = res.slots.find((s) => s.id === "header");
    assert.equal(header.role, "locked");
    assert.equal(header.axis, "vertical");
    assert.equal(header.link_id, "col");
    assert.equal(header.iframe.h, 200, "locked iframe keeps its designated height");
    assert.deepEqual(res.slots.map((s) => s.id), ["chat", "header", "goal", "free"], "input order is kept");
});

test("horizontal link sorts by x then id and lays out left to right", () => {
    const { L } = setup();
    const c = container([
        slot("b", { x: 100, w: 300, link: link("row", { axis: "horizontal" }) }),
        slot("a", { x: 100, w: 100, link: link("row", { axis: "horizontal" }) }),
        slot("c", { x: 0, w: 100, link: link("row", { axis: "horizontal", role: "locked" }) }),
    ], { width: 1000 });
    const r = rects(L.solve(c));
    assert.deepEqual(r.c, [0, 0, 100, 360]);
    assert.deepEqual(r.a, [100, 0, 225, 360]);
    assert.deepEqual(r.b, [325, 0, 675, 360]);
});

test("min overflow is warned once per link; separate links are independent", () => {
    const { L } = setup();
    const c = container([
        slot("a", { y: 0, link: link("L", { min_px: 800 }) }),
        slot("b", { y: 10, link: link("L", { min_px: 800 }) }),
        slot("c", { y: 0, link: link("M", { axis: "horizontal", role: "locked", min_px: 5000 }) }),
        slot("d", { y: 0, link: link("M", { axis: "horizontal", role: "locked", min_px: 5000 }) }),
    ]);
    const res = plain(L.solve(c));
    assert.deepEqual(res.warnings.map((w) => [w.code, w.link]), [["min_overflow", "L"], ["min_above_designated", "M"]]);
    const r = rects(res);
    assert.deepEqual(r.a, [0, 0, 640, 800]);
    assert.deepEqual(r.b, [0, 800, 640, 800]);
});

test("locked slots larger than the span leave negative room and warn", () => {
    const { L } = setup();
    const c = container([
        slot("a", { h: 1000, link: link("L", { role: "locked" }) }),
        slot("b", { y: 1, h: 1000, link: link("L", { role: "locked" }) }),
        slot("f", { y: 2, h: 100, link: link("L") }),
    ]);
    const res = plain(L.solve(c));
    assert.deepEqual(res.warnings.map((w) => w.code), ["min_overflow"]);
    assert.deepEqual(rects(res).f, [0, 2000, 640, 0], "flexible collapses to its minimum, like containers.py");
});

xfail(test, "percent limits round like containers.py (span * pct / 100)",
    "BUG: container_layout.js:28 span * (pct / 100) = 764.5000000000001 for 55% of 1390, so JS rounds to 765 where containers.py gets 764",
    () => {
        const { I } = setup();
        assert.equal(I.effMin({ min_pct: 55 }, 1390), 764);
    });

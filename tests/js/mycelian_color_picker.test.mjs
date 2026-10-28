// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/mycelian_color_picker.js: colour maths, parsing (hex/rgb/hsl/names/
// transparent/gradients), formatting round-trips, isTransparent / swatchBackground, recent colours,
// and the popover: solid + gradient editing, inputs, chips, presets, keyboard, drag areas,
// throttled onInput, done/cancel/outside-click, positioning and the anchor toggle.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, fire, plain } from "./helpers/env.mjs";

const FILE = "assets/default_assets/mycelian_color_picker.js";
const NAMED_CANVAS = `probeCtx = (function () {
    var v = '#000000';
    var names = { red: '#ff0000', white: '#ffffff', rebeccapurple: '#663399', blue: '#0000ff' };
    return {
        get fillStyle() { return v; },
        set fillStyle(x) { x = String(x).toLowerCase(); if (names[x]) { v = names[x]; } else if (/^#[0-9a-f]{6}$/.test(x)) { v = x; } }
    };
})()`;

function setup(opts = {}) {
    const win = makeWindow(Object.assign({ innerWidth: 1000, innerHeight: 800 }, opts));
    const { internals: I } = loadScript(win, FILE);
    I.__eval(NAMED_CANVAS);
    return { win, P: win.MycelianColorPicker, I, doc: win.document, clock: win.__clock };
}
const S = setup();
const P = S.P;
const I = S.I;

/* ------------------------------------------------------------------ maths */

test("small helpers", () => {
    assert.equal(I.clamp(5, 0, 3), 3);
    assert.equal(I.clamp(-1, 0, 3), 0);
    assert.equal(I.round3(0.12345), 0.123);
    assert.equal(I.fmtNum(12.345), "12.3");
    assert.equal(I.fmtNum(90), "90");
    assert.equal(I.hx(10), "0a");
    assert.equal(I.hx(300), "ff");
    assert.equal(I.hx(-5), "00");
    assert.equal(I.toHex({ r: 255, g: 128.4, b: 0 }), "#ff8000");
    assert.ok(I.svg("circle").includes("<circle"));
    assert.ok(I.svg("trash").includes(I.ICON.trash));
});

test("hsvToRgb / rgbToHsv cover every hue sector and round-trip", () => {
    const cases = [[0, "#ff0000"], [60, "#ffff00"], [120, "#00ff00"], [180, "#00ffff"], [240, "#0000ff"], [300, "#ff00ff"], [-60, "#ff00ff"], [720, "#ff0000"]];
    for (const [h, hex] of cases) { assert.equal(I.toHex(I.hsvToRgb(h, 1, 1)), hex, String(h)); }
    assert.deepEqual(plain(I.hsvToRgb(0, 2, -1)), { r: 0, g: 0, b: 0 });
    assert.deepEqual(plain(I.rgbToHsv(0, 0, 0)), { h: 0, s: 0, v: 0 });
    assert.deepEqual(plain(I.rgbToHsv(255, 0, 255)), { h: 300, s: 1, v: 1 });
    assert.deepEqual(plain(I.rgbToHsv(255, 0, 128)).h > 329, true);
    for (const [r, g, b] of [[12, 200, 99], [255, 128, 0], [3, 7, 250], [200, 10, 220], [90, 90, 90]]) {
        const hsv = I.rgbToHsv(r, g, b);
        assert.deepEqual(plain(I.hsvToRgb(hsv.h, hsv.s, hsv.v)), { r, g, b });
    }
});

test("hslToRgb covers every sector", () => {
    const want = [[0, "#ff0000"], [90, "#80ff00"], [150, "#00ff80"], [210, "#0080ff"], [270, "#8000ff"], [330, "#ff0080"]];
    for (const [h, hex] of want) { assert.equal(I.toHex(I.hslToRgb(h, 1, 0.5)), hex, String(h)); }
    assert.equal(I.toHex(I.hslToRgb(0, 0, 1)), "#ffffff");
});

test("angleToDeg converts units", () => {
    assert.equal(I.angleToDeg(0.5, "turn"), 180);
    assert.equal(I.angleToDeg(Math.PI, "RAD"), 180);
    assert.equal(I.angleToDeg(100, "grad"), 90);
    assert.equal(I.angleToDeg(45), 45);
});

test("num and fnParts", () => {
    assert.equal(I.num("50%", 255), 127.5);
    assert.equal(I.num(" 1e2 ", 1), 100);
    assert.ok(Number.isNaN(I.num("abc", 1)));
    assert.deepEqual(plain(I.fnParts("1, 2 ,3")), ["1", "2", "3"]);
    assert.deepEqual(plain(I.fnParts("1 2 3 / 50%")), ["1", "2", "3", "50%"]);
    assert.equal(I.fnParts("1 / 2 / 3"), null);
});

/* ------------------------------------------------------------------ parsing */

test("parseSolid: hex forms", () => {
    assert.deepEqual(plain(I.parseSolid("#fff")), { r: 255, g: 255, b: 255, a: 1, format: "hex" });
    assert.deepEqual(plain(I.parseSolid("#F008")), { r: 255, g: 0, b: 0, a: 0.533, format: "hex" });
    assert.deepEqual(plain(I.parseSolid(" #12ab34 ")), { r: 18, g: 171, b: 52, a: 1, format: "hex" });
    assert.deepEqual(plain(I.parseSolid("#12ab3480")), { r: 18, g: 171, b: 52, a: 0.502, format: "hex" });
    assert.equal(I.parseSolid("#12345"), null);
    assert.equal(I.parseSolid("#ggg"), null);
});

test("parseSolid: rgb/rgba in comma, space and percent syntax", () => {
    assert.deepEqual(plain(I.parseSolid("rgb(1, 2, 3)")), { r: 1, g: 2, b: 3, a: 1, format: "rgb" });
    assert.deepEqual(plain(I.parseSolid("RGBA(300, -5, 2.6, 0.5)")), { r: 255, g: 0, b: 3, a: 0.5, format: "rgba" });
    assert.deepEqual(plain(I.parseSolid("rgb(100% 50% 0% / 25%)")), { r: 255, g: 128, b: 0, a: 0.25, format: "rgba" });
    assert.deepEqual(plain(I.parseSolid("rgba(1,2,3)")), { r: 1, g: 2, b: 3, a: 1, format: "rgba" });
    assert.deepEqual(plain(I.parseSolid("rgba(1,2,3,7)")).a, 1);
    for (const bad of ["rgb(1,2)", "rgb(1,2,3,4,5)", "rgb(a,b,c)", "rgb(1,2,3,x)", "rgb(1 2 3 / 4 / 5)"]) {
        assert.equal(I.parseSolid(bad), null, bad);
    }
});

test("parseSolid: hsl/hsla with units", () => {
    assert.deepEqual(plain(I.parseSolid("hsl(120, 100%, 50%)")), { r: 0, g: 255, b: 0, a: 1, format: "hsl" });
    assert.deepEqual(plain(I.parseSolid("hsl(0.5turn 100 50)")), { r: 0, g: 255, b: 255, a: 1, format: "hsl" });
    assert.deepEqual(plain(I.parseSolid("hsla(240deg, 100%, 50%, 0.3)")), { r: 0, g: 0, b: 255, a: 0.3, format: "hsl" });
    assert.equal(I.parseSolid("hsl(red, 1%, 1%)"), null);
    assert.equal(I.parseSolid("hsl(1, x, 1%)"), null);
});

test("parseSolid: transparent, names, keywords and junk", () => {
    assert.deepEqual(plain(I.parseSolid("Transparent")), { r: 0, g: 0, b: 0, a: 0, format: "transparent" });
    assert.deepEqual(plain(I.parseSolid("red")), { r: 255, g: 0, b: 0, a: 1, format: "name" });
    assert.deepEqual(plain(I.parseSolid("RebeccaPurple")), { r: 102, g: 51, b: 153, a: 1, format: "name" });
    assert.equal(I.parseSolid("notacolour"), null, "canvas keeps the sentinel");
    for (const kw of ["inherit", "currentColor", "none", "auto"]) { assert.equal(I.parseSolid(kw), null, kw); }
    assert.equal(I.parseSolid(""), null);
    assert.equal(I.parseSolid(null), null);
    assert.equal(I.parseSolid("12px"), null);
});

test("parseSolid: no canvas means names are not understood", () => {
    const s = setup();
    s.I.__eval("probeCtx = { set fillStyle(x) { throw new Error('no canvas'); } }");
    assert.equal(s.I.parseSolid("red"), null);
    // without a probe it creates one from a canvas element
    const t = setup();
    t.I.__eval("probeCtx = null");
    t.win.document.createElement = (tag) => ({ getContext: () => { throw new Error("no 2d"); }, tag });
    assert.equal(t.I.parseSolid("red"), null);
});

test("splitTop splits on top-level commas only", () => {
    assert.deepEqual(plain(I.splitTop("90deg, rgb(1, 2, 3) 10%, #fff")), ["90deg", "rgb(1, 2, 3) 10%", "#fff"]);
});

test("parseStop reads colour and up to two positions", () => {
    assert.deepEqual(plain(I.parseStop("rgba(1, 2, 3, 0.5) 10% 20%")).pos, [10, 20]);
    assert.deepEqual(plain(I.parseStop("#fff 0")).pos, [0]);
    assert.deepEqual(plain(I.parseStop("red")).pos, []);
    assert.equal(I.parseStop("#fff 10% 20% 30%"), null);
    assert.equal(I.parseStop("#fff 10px"), null);
    assert.equal(I.parseStop("#fff 120%"), null);
    assert.equal(I.parseStop("bogus 10%"), null);
});

test("parseGradient: linear angles, directions and stop positions", () => {
    const g = plain(I.parseGradient("linear-gradient(45deg, #ff0000, rgba(0, 0, 255, 0.5) 40%, #00ff00)"));
    assert.equal(g.kind, "gradient");
    assert.equal(g.type, "linear");
    assert.equal(g.angle, 45);
    assert.deepEqual(g.stops.map((s) => s.pos), [0, 40, 100]);
    assert.deepEqual(g.stops[1], { r: 0, g: 0, b: 255, a: 0.5, pos: 40 });
    assert.equal(I.parseGradient("linear-gradient(to top right, red, blue)").angle, 45);
    assert.equal(I.parseGradient("linear-gradient(TO  LEFT, red, blue)").angle, 270);
    assert.equal(I.parseGradient("linear-gradient(0, red, blue)").angle, 0);
    assert.equal(I.parseGradient("linear-gradient(0.25turn, red, blue)").angle, 90);
    assert.equal(I.parseGradient("linear-gradient(red, blue)").angle, 180);
    assert.equal(I.parseGradient("linear-gradient(to nowhere, red, blue)"), null);
});

test("parseGradient: positions are made monotonic and missing ones interpolated", () => {
    const g = I.parseGradient("linear-gradient(red 50%, white, blue 20%, red, white, blue)");
    assert.deepEqual(plain(g.stops.map((s) => s.pos)), [50, 50, 50, 66.66666666666667, 83.33333333333334, 100]);
    const two = I.parseGradient("linear-gradient(red 10% 30%, blue)");
    assert.deepEqual(plain(two.stops.map((s) => s.pos)), [10, 30, 100]);
});

test("parseGradient: radial shapes and failures", () => {
    const r = plain(I.parseGradient("radial-gradient(circle, #000, #fff)"));
    assert.equal(r.type, "radial");
    assert.equal(r.shape, "circle");
    assert.equal(I.parseGradient("radial-gradient(#000, #fff)").shape, "");
    assert.equal(I.parseGradient("radial-gradient(circle at top, #000, #fff)"), null);
    assert.equal(I.parseGradient("linear-gradient(90deg, red)"), null, "needs two stops");
    assert.equal(I.parseGradient("conic-gradient(red, blue)"), null);
    assert.equal(I.parseGradient(null), null);
});

test("parse dispatches to gradient or solid", () => {
    assert.equal(P.parse("linear-gradient(red, blue)").kind, "gradient");
    assert.equal(P.parse("#000").kind, "solid");
    assert.equal(P.parse("nope"), null);
});

/* ------------------------------------------------------------------ formatting */

test("formatSolid: hex, rgb, rgba and transparent", () => {
    assert.equal(I.formatSolid({ r: 255, g: 0, b: 0 }, "hex"), "#ff0000");
    assert.equal(I.formatSolid({ r: 255, g: 0, b: 0, a: 1 }, "rgb"), "rgb(255, 0, 0)");
    assert.equal(I.formatSolid({ r: 255, g: 0, b: 0, a: 0.5 }, "hex"), "rgba(255, 0, 0, 0.5)");
    assert.equal(I.formatSolid({ r: 1, g: 2, b: 3, a: 0 }, "hex"), "transparent");
    assert.equal(I.formatSolid({ r: 1, g: 2, b: 3, a: -1 }, "hex", true), "rgba(1, 2, 3, 0)");
});

test("format keeps the incoming format and honours a preferred one", () => {
    assert.equal(P.format(null), "");
    assert.equal(P.format(P.parse("rgb(1,2,3)")), "rgb(1, 2, 3)");
    assert.equal(P.format(P.parse("#010203"), "rgb"), "rgb(1, 2, 3)");
    assert.equal(P.format(P.parse("rgba(1,2,3,0)")), "rgba(1, 2, 3, 0)");
    assert.equal(P.format(P.parse("hsl(0, 100%, 50%)")), "#ff0000");
    assert.equal(P.format(P.parse("red")), "#ff0000");
    assert.equal(P.format(P.parse("linear-gradient(90deg, #ff0000 0%, rgba(0, 0, 255, 0.5) 100%)")),
        "linear-gradient(90deg, #ff0000 0%, rgba(0, 0, 255, 0.5) 100%)");
    assert.equal(P.format({ stops: [{ r: 0, g: 0, b: 0, a: 1, pos: 100 }, { r: 255, g: 255, b: 255, a: 1, pos: 0 }], type: "radial", shape: "" }),
        "radial-gradient(#ffffff 0%, #000000 100%)");
    assert.equal(I.formatGradient({ type: "radial", shape: "ellipse", stops: [{ r: 0, g: 0, b: 0, a: 1, pos: 0 }, { r: 0, g: 0, b: 0, a: 0.25, pos: 33.33 }] }),
        "radial-gradient(ellipse, #000000 0%, rgba(0, 0, 0, 0.25) 33.3%)");
});

test("gradient text round-trips through parse and format", () => {
    for (const css of [
        "linear-gradient(135deg, #112233 0%, #445566 50%, rgba(1, 2, 3, 0.4) 100%)",
        "radial-gradient(circle, #ffffff 0%, #000000 100%)",
    ]) {
        assert.equal(P.format(P.parse(css)), css);
    }
});

test("isTransparent", () => {
    assert.equal(P.isTransparent("transparent"), true);
    assert.equal(P.isTransparent("rgba(0,0,0,0.5)"), true);
    assert.equal(P.isTransparent("#fff"), false);
    assert.equal(P.isTransparent("linear-gradient(red, rgba(0,0,0,0.2))"), true);
    assert.equal(P.isTransparent("linear-gradient(red, blue)"), false);
    // unparseable gradients are scanned for transparent stops
    assert.equal(P.isTransparent("radial-gradient(circle at top, red, transparent)"), true);
    assert.equal(P.isTransparent("radial-gradient(circle at top, red, rgba(1,2,3,0.1))"), true);
    assert.equal(P.isTransparent("radial-gradient(circle at top, red, #ff000080)"), true);
    assert.equal(P.isTransparent("radial-gradient(circle at top, red, #ff0000ff)"), false);
    assert.equal(P.isTransparent(null), false);
    assert.equal(P.isTransparent("url(x.png)"), false);
});

test("swatchBackground is always safe CSS", () => {
    assert.equal(P.swatchBackground("#abc"), "rgb(170, 187, 204)");
    assert.equal(P.swatchBackground("rgba(1,2,3,0)"), "transparent");
    assert.equal(P.swatchBackground("rgba(1,2,3,0.5)"), "rgba(1, 2, 3, 0.5)");
    assert.equal(P.swatchBackground("linear-gradient(red, blue)"), "linear-gradient(180deg, #ff0000 0%, #0000ff 100%)");
    assert.equal(P.swatchBackground("radial-gradient(circle at top, red, blue)"), "radial-gradient(circle at top, red, blue)", "CSS.supports accepts it");
    assert.equal(P.swatchBackground(""), "transparent");
    assert.equal(P.swatchBackground(null), "transparent");
    const s = setup();
    s.win.CSS = { supports: () => false };
    assert.equal(s.P.swatchBackground("url(x)"), "transparent");
    s.win.CSS = { supports: () => { throw new Error("x"); } };
    assert.equal(s.P.swatchBackground("url(x)"), "transparent");
    s.win.CSS = undefined;
    assert.equal(s.P.swatchBackground("url(x)"), "transparent");
});

test("_internal exposes the parsers for other modules", () => {
    assert.deepEqual(Object.keys(P._internal).sort(), ["formatGradient", "formatSolid", "parseGradient", "parseSolid"]);
});

/* ------------------------------------------------------------------ recent colours */

test("recent colours: load filters junk, push de-duplicates and caps at 10", () => {
    const s = setup({ localStorage: { "mycelian.colorPicker.recent": JSON.stringify(["#fff", 5, "nope", "rgb(1,2,3)"]) } });
    assert.deepEqual(plain(s.I.loadRecent()), ["#fff", "rgb(1,2,3)"]);
    s.I.pushRecent("not a colour");
    s.I.pushRecent("#FFF");
    assert.deepEqual(plain(s.I.loadRecent()), ["#FFF", "rgb(1,2,3)"]);
    for (let i = 0; i < 12; i++) { s.I.pushRecent("#00000" + (i % 10)); }
    assert.equal(s.I.loadRecent().length, 10);
    s.win.localStorage.setItem("mycelian.colorPicker.recent", "{bad");
    assert.deepEqual(plain(s.I.loadRecent()), []);
    s.win.localStorage.setItem("mycelian.colorPicker.recent", '{"a":1}');
    assert.deepEqual(plain(s.I.loadRecent()), []);
    s.win.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
    assert.doesNotThrow(() => s.I.pushRecent("#fff"));
});

test("arrowStep maps keys to steps", () => {
    assert.deepEqual(plain(I.arrowStep({ key: "ArrowLeft" })), { x: -1, y: 0 });
    assert.deepEqual(plain(I.arrowStep({ key: "ArrowRight", shiftKey: true })), { x: 10, y: 0 });
    assert.deepEqual(plain(I.arrowStep({ key: "ArrowUp" })), { x: 0, y: 1 });
    assert.deepEqual(plain(I.arrowStep({ key: "ArrowDown" })), { x: 0, y: -1 });
    assert.deepEqual(plain(I.arrowStep({ key: "PageUp" })), { x: 10, y: 10 });
    assert.deepEqual(plain(I.arrowStep({ key: "PageDown" })), { x: -10, y: -10 });
    assert.equal(I.arrowStep({ key: "a" }), null);
});

/* ------------------------------------------------------------------ the picker */

function openPicker(opts = {}, env = {}) {
    const s = setup(env);
    const anchor = s.doc.createElement("button");
    anchor._rect = { left: 100, top: 100, width: 40, height: 20 };
    s.doc.body.appendChild(anchor);
    const log = { input: [], done: [], cancel: 0 };
    const h = s.P.open(Object.assign({
        anchor,
        onInput: (v) => log.input.push(v),
        onDone: (v) => log.done.push(v),
        onCancel: () => log.cancel++,
    }, opts));
    const root = h ? h.element : null;
    const q = (sel) => root.querySelector(sel);
    const qa = (sel) => root.querySelectorAll(sel);
    return Object.assign(s, { anchor, h, root, q, qa, log });
}
const input = (s, el, value) => { el.value = value; fire(s.win, el, "input"); };

test("open builds a solid picker for the value; untouched Done returns the original text", () => {
    const s = openPicker({ value: "rgb(255, 0, 0)", title: "Text colour", note: "A note" });
    assert.ok(s.P.isOpen());
    assert.equal(s.root.getAttribute("aria-label"), "Text colour");
    assert.equal(s.q(".mcp-title").textContent, "Text colour");
    assert.equal(s.q(".mcp-tabs"), null, "no gradient tabs by default");
    assert.equal(s.q("input.is-text").value, "rgb(255, 0, 0)");
    assert.equal(s.q("input.is-num").value, "100");
    assert.equal(s.q(".mcp-hint").textContent, "A note");
    assert.ok(s.anchor.classList.contains("is-picking"));
    assert.deepEqual([s.root.style.left, s.root.style.top], ["100px", "126px"], "below the anchor");
    assert.equal(s.h.value(), "rgb(255, 0, 0)");
    s.q(".mcp-btn.is-primary").click();
    assert.deepEqual(s.log.done, ["rgb(255, 0, 0)"]);
    assert.ok(!s.P.isOpen());
    assert.equal(s.root.parentNode, null);
    assert.ok(!s.anchor.classList.contains("is-picking"));
    assert.equal(s.win.localStorage.getItem("mycelian.colorPicker.recent"), null, "unchanged colours are not remembered");
});

test("typing a colour updates the value, throttles onInput and remembers it on Done", () => {
    const s = openPicker({ value: "#000000" });
    const txt = s.q("input.is-text");
    input(s, txt, "00ff00");
    assert.deepEqual(s.log.input, ["#00ff00"], "first change goes out at once");
    input(s, txt, "#0000ff");
    input(s, txt, "#ff0000");
    assert.equal(s.log.input.length, 1, "throttled");
    s.clock.tick(60);
    assert.deepEqual(s.log.input, ["#00ff00", "#ff0000"]);
    input(s, txt, "zzz");
    assert.ok(txt.classList.contains("is-bad"));
    input(s, txt, "");
    assert.ok(!txt.classList.contains("is-bad"));
    fire(s.win, txt, "blur", { bubbles: false });
    assert.equal(txt.value, "#ff0000");
    s.h.close();
    assert.deepEqual(s.log.done, ["#ff0000"]);
    assert.deepEqual(JSON.parse(s.win.localStorage.getItem("mycelian.colorPicker.recent")), ["#ff0000"]);
});

test("typed rgb switches the output format; alpha follows rgba and the % box", () => {
    const s = openPicker({ value: "#336699" });
    input(s, s.q("input.is-text"), "rgb(10, 20, 30)");
    assert.equal(s.h.value(), "rgb(10, 20, 30)");
    assert.ok(s.qa(".mcp-seg button")[1].classList.contains("is-on"));
    input(s, s.q("input.is-text"), "rgba(10, 20, 30, 0.5)");
    assert.equal(s.h.value(), "rgba(10, 20, 30, 0.5)");
    const pct = s.q("input.is-num");
    input(s, pct, "25");
    assert.equal(s.h.value(), "rgba(10, 20, 30, 0.25)");
    input(s, pct, "abc");
    assert.equal(s.h.value(), "rgba(10, 20, 30, 0.25)");
    input(s, pct, "250");
    assert.equal(s.h.value(), "rgb(10, 20, 30)");
    fire(s.win, pct, "blur", { bubbles: false });
    s.qa(".mcp-seg button")[0].click();
    assert.equal(s.h.value(), "#0a141e");
    s.qa(".mcp-seg button")[1].click();
    assert.equal(s.h.value(), "rgb(10, 20, 30)");
    input(s, s.q("input.is-text"), "#0a141e80");
    assert.equal(s.h.value(), "rgba(10, 20, 30, 0.502)");
    input(s, s.q("input.is-text"), "#ffffff");
    assert.equal(s.h.value(), "rgba(255, 255, 255, 0.502)", "plain hex keeps the current opacity");
    input(s, s.q("input.is-text"), "transparent");
    assert.equal(s.h.value(), "transparent");
    input(s, s.q("input.is-text"), "#000");
    assert.equal(s.h.value(), "#000000", "after fully transparent a plain colour is opaque again");
});

test("opacity chips set alpha and mark the active one", () => {
    const s = openPicker({ value: "#ff0000" });
    const chips = s.qa(".mcp-chip");
    assert.equal(chips.length, 5);
    assert.ok(chips[0].classList.contains("is-on"));
    chips[2].click();
    assert.equal(s.h.value(), "rgba(255, 0, 0, 0.5)");
    assert.ok(chips[2].classList.contains("is-on"));
    chips[4].click();
    assert.equal(s.h.value(), "transparent");
    assert.equal(chips[4].title, "Fully transparent");
});

test("allowAlpha false hides opacity controls and forces opaque output", () => {
    const s = openPicker({ value: "rgba(1, 2, 3, 0.5)", allowAlpha: false });
    assert.equal(s.q("input.is-num"), null);
    assert.equal(s.qa(".mcp-chip").length, 0, "chips are not attached");
    input(s, s.q("input.is-text"), "rgba(9, 9, 9, 0.2)");
    assert.equal(s.h.value(), "rgb(9, 9, 9)");
});

test("preferredFormat rgb applies to a hex value", () => {
    const s = openPicker({ value: "#0a141e", preferredFormat: "rgb" });
    assert.equal(s.q("input.is-text").value, "rgb(10, 20, 30)");
    input(s, s.q("input.is-text"), "#ffffff");
    assert.equal(s.h.value(), "#ffffff", "typing a hex switches back to hex");
});

test("transparent and unparseable starting values", () => {
    const s = openPicker({ value: "transparent" }, {});
    s.doc.documentElement.style.setProperty("--color-primary", "#7c3aed");
    assert.equal(s.q("input.is-num").value, "0");
    s.h.close("cancel");
    assert.equal(s.log.cancel, 1);
    assert.deepEqual(s.log.done, []);
    const t = openPicker({ value: "url(x)" });
    assert.equal(t.q("input.is-text").value, "#ffffff");
    const u = openPicker({ value: null });
    assert.equal(u.h.value(), "");
});

test("transparent starts from the theme accent hue", () => {
    const s = setup();
    s.doc.documentElement.style.setProperty("--color-primary", "#ff0000");
    const h = s.P.open({ value: "transparent" });
    s.P.close();
    assert.ok(h);
    const t = setup();
    t.doc.documentElement.style.setProperty("--color-primary", "#00ff00");
    const h2 = t.P.open({ value: "transparent" });
    h2.element.querySelectorAll(".mcp-chip")[0].click();
    assert.equal(h2.value(), "#00ff00");
    t.win.getComputedStyle = () => { throw new Error("x"); };
    assert.deepEqual(plain(t.I.themeAccent()), { h: 0, s: 0, v: 1 });
});

test("Before swatch resets to the starting colour", () => {
    const s = openPicker({ value: "#123456" });
    input(s, s.q("input.is-text"), "#ffffff");
    s.q(".mcp-compare .is-click").click();
    assert.equal(s.h.value(), "#123456");
});

test("presets: grid, show all toggle, click applies, the matching one is marked", () => {
    const presets = Array.from({ length: 25 }, (_, i) => "#0000" + String(i).padStart(2, "0"));
    presets.push("#000000", "#000000", "", null, "rgba(255, 0, 0, 0)");
    const s = openPicker({ value: "#000000", presets, presetsLabel: "Theme" });
    const grid = s.qa(".mcp-swatches")[0];
    assert.equal(grid.children.length, 20);
    const showAll = s.q(".mcp-link");
    assert.equal(showAll.textContent, "Show all 26", "blank and duplicate presets are dropped");
    showAll.click();
    assert.equal(grid.children.length, 26);
    assert.equal(showAll.textContent, "Show fewer");
    assert.ok(grid.classList.contains("is-all"));
    grid.children[5].click();
    assert.equal(s.h.value(), "#000005");
    assert.ok(grid.children[5].classList.contains("is-on"));
    grid.children[25].click(); // transparent preset keeps the hue, sets alpha 0
    assert.equal(s.h.value(), "transparent");
    fire(s.win, grid, "click"); // a click on the grid gap does nothing
    assert.ok(s.root.textContent.includes("Theme"));
});

test("preset with an invalid value is ignored; recent colours are shown and clickable", () => {
    const s = openPicker({ value: "#000000", presets: ["bogus"] }, { localStorage: { "mycelian.colorPicker.recent": '["#ff0000"]' } });
    s.qa(".mcp-swatches")[0].children[0].click();
    assert.equal(s.h.value(), "#000000");
    const recent = s.qa(".mcp-swatches")[1];
    recent.children[0].click();
    assert.equal(s.h.value(), "#ff0000");
});

test("SV square, hue and alpha bars respond to pointer drags", () => {
    const s = openPicker({ value: "#ff0000" });
    const [sv] = s.qa(".mcp-sv");
    const [hue, alpha] = s.qa(".mcp-bar");
    sv._rect = { left: 0, top: 0, width: 100, height: 100 };
    hue._rect = { left: 0, top: 0, width: 360, height: 10 };
    alpha._rect = { left: 0, top: 0, width: 100, height: 10 };
    fire(s.win, sv, "pointerdown", { button: 2 });
    assert.equal(s.h.value(), "#ff0000", "right button ignored");
    fire(s.win, sv, "pointerdown", { button: 0, clientX: 100, clientY: 50 });
    assert.equal(s.h.value(), "#800000");
    fire(s.win, sv, "pointermove", { clientX: 0, clientY: 0 });
    assert.equal(s.h.value(), "#ffffff");
    fire(s.win, sv, "pointerup", {});
    fire(s.win, sv, "pointermove", { clientX: 100, clientY: 100 });
    assert.equal(s.h.value(), "#ffffff", "no moves after release");
    fire(s.win, sv, "pointerdown", { button: 0, clientX: 100, clientY: 0 });
    fire(s.win, sv, "pointercancel", {});
    fire(s.win, hue, "pointerdown", { button: 0, clientX: 120, clientY: 0 });
    assert.equal(s.h.value(), "#00ff00");
    fire(s.win, alpha, "pointerdown", { button: 0, clientX: 30, clientY: 0 });
    assert.equal(s.h.value(), "rgba(0, 255, 0, 0.3)");
    sv.setPointerCapture = () => { throw new Error("old"); };
    sv.releasePointerCapture = () => { throw new Error("old"); };
    fire(s.win, sv, "pointerdown", { button: 0, clientX: 50, clientY: 50 });
    fire(s.win, sv, "pointerup", {});
});

test("dragging from fully transparent wakes the colour up", () => {
    const s = openPicker({ value: "rgba(255, 0, 0, 0)" });
    const [hue] = s.qa(".mcp-bar");
    hue._rect = { left: 0, top: 0, width: 360, height: 10 };
    fire(s.win, hue, "pointerdown", { button: 0, clientX: 240 });
    assert.equal(s.h.value(), "rgb(0, 0, 255)", "alpha back to 1; rgba input keeps rgb() output");
});

test("knobs move with the keyboard", () => {
    const s = openPicker({ value: "#808080" });
    const [svKnob, hueKnob, alphaKnob] = s.qa(".mcp-knob");
    assert.equal(s.doc.activeElement, svKnob, "SV knob focused on open");
    fire(s.win, svKnob, "keydown", { key: "ArrowUp", shiftKey: true });
    assert.equal(s.h.value(), "#9a9a9a");
    fire(s.win, svKnob, "keydown", { key: "x" });
    fire(s.win, svKnob, "keydown", { key: "ArrowRight" });
    fire(s.win, hueKnob, "keydown", { key: "PageUp" });
    fire(s.win, hueKnob, "keydown", { key: "q" });
    fire(s.win, alphaKnob, "keydown", { key: "ArrowLeft", shiftKey: true });
    assert.match(s.h.value(), /^rgba\(\d+, \d+, \d+, 0\.9\)$/);
    fire(s.win, alphaKnob, "keydown", { key: "Home" });
    assert.equal(hueKnob.getAttribute("aria-valuenow"), "10");
});

test("EyeDropper button applies the sampled colour when supported", async () => {
    const s = openPicker({ value: "#000000" }, { extra: { EyeDropper: class { open() { return Promise.resolve({ sRGBHex: "#abcdef" }); } } } });
    const eye = s.q('button[title="Pick a colour from the screen"]');
    assert.ok(eye);
    eye.click();
    await new Promise((r) => setImmediate(r));
    assert.equal(s.h.value(), "#abcdef");
    const t = openPicker({ value: "#000000" }, { extra: { EyeDropper: class { open() { return Promise.reject(new Error("dismissed")); } } } });
    t.q('button[title="Pick a colour from the screen"]').click();
    await new Promise((r) => setImmediate(r));
    assert.equal(t.h.value(), "#000000");
    const u = openPicker({ value: "#000000" }, { extra: { EyeDropper: function () { throw new Error("nope"); } } });
    assert.doesNotThrow(() => u.q('button[title="Pick a colour from the screen"]').click());
    const v = openPicker({ value: "#000000" }, { extra: { EyeDropper: class { open() { return Promise.resolve(null); } } } });
    v.q('button[title="Pick a colour from the screen"]').click();
    assert.equal(openPicker({ value: "#000" }).q('button[title="Pick a colour from the screen"]'), null);
});

test("Escape cancels, Enter in a field finishes, Enter elsewhere does not", () => {
    const s = openPicker({ value: "#000000" });
    fire(s.win, s.q(".mcp-chip"), "keydown", { key: "Enter" });
    assert.ok(s.P.isOpen());
    fire(s.win, s.q("input.is-text"), "keydown", { key: "Enter" });
    assert.deepEqual(s.log.done, ["#000000"]);
    const t = openPicker({ value: "#000000" });
    fire(t.win, t.doc.body, "keydown", { key: "Escape" });
    assert.equal(t.log.cancel, 1);
    t.h.close(); // already closed
    assert.deepEqual(t.log.done, []);
});

test("a click outside finishes; clicking the anchor closes and the immediate reopen is suppressed", () => {
    const s = openPicker({ value: "#000000" });
    fire(s.win, s.q(".mcp-sv"), "pointerdown", { button: 1 });
    assert.ok(s.P.isOpen(), "inside clicks keep it open");
    fire(s.win, s.anchor, "pointerdown", {});
    assert.ok(!s.P.isOpen());
    assert.deepEqual(s.log.done, ["#000000"]);
    assert.equal(s.P.open({ anchor: s.anchor, value: "#fff" }), null, "anchor click toggles it shut");
    const again = s.P.open({ anchor: s.anchor, value: "#fff" });
    assert.ok(again);
    fire(s.win, s.doc.body, "pointerdown", {});
    assert.ok(!s.P.isOpen());
    s.P.open({ anchor: s.anchor, value: "#fff" });
    s.clock.tick(600);
    fire(s.win, s.anchor, "pointerdown", {});
    s.clock.tick(600);
    assert.ok(s.P.open({ anchor: s.anchor, value: "#fff" }), "after 500ms the anchor opens it again");
});

test("opening another picker finishes the first; P.close passes how through", () => {
    const s = openPicker({ value: "#000000" });
    const second = s.P.open({ value: "#ffffff", onCancel: () => s.log.cancel++ });
    assert.deepEqual(s.log.done, ["#000000"]);
    assert.ok(second);
    s.P.close("cancel");
    assert.equal(s.log.cancel, 1);
    s.P.close();
});

test("callback errors are logged, not thrown", () => {
    const s = openPicker({ value: "#000", onInput: () => { throw new Error("in"); }, onDone: () => { throw new Error("done"); } });
    input(s, s.q("input.is-text"), "#fff");
    s.h.close();
    const errors = s.win.__console.filter(([k]) => k === "error").map(([, a]) => a[0]);
    assert.deepEqual(errors, ["[MycelianColorPicker] onInput", "[MycelianColorPicker] callback"]);
});

test("no onInput: hint says Enter or Done; pending input is dropped on close", () => {
    const s = openPicker({ value: "#000", onInput: undefined });
    assert.equal(s.q(".mcp-hint").textContent, "Enter or Done applies the colour");
    const t = openPicker({ value: "#000" });
    input(t, t.q("input.is-text"), "#111");
    input(t, t.q("input.is-text"), "#222");
    t.h.close();
    t.clock.tick(100);
    assert.deepEqual(t.log.input, ["#111111"]);
});

test("positioning flips above, clamps to the viewport and centres without an anchor", () => {
    const s = openPicker({ value: "#000" });
    s.root._rect = { width: 300, height: 400 };
    s.anchor._rect = { left: 900, top: 700, width: 40, height: 20 };
    s.win.dispatchEvent(new s.win.Event("resize"));
    assert.deepEqual([s.root.style.left, s.root.style.top], ["640px", "294px"], "right-aligned, above");
    s.anchor._rect = { left: 10, top: 100, width: 40, height: 20 };
    fire(s.win, s.doc.body, "scroll", { bubbles: false });
    assert.equal(s.root.style.top, "126px");
    s.anchor._rect = { left: 10, top: 300, width: 40, height: 100 };
    s.root._rect = { width: 300, height: 700 };
    s.win.dispatchEvent(new s.win.Event("resize"));
    assert.equal(s.root.style.top, "92px", "no room above or below: as low as fits");
    s.anchor._rect = { left: 0, top: 0, width: 0, height: 0 };
    s.win.dispatchEvent(new s.win.Event("resize"));
    assert.equal(s.root.style.top, "92px", "zero-size anchor keeps the last rect");
    s.h.close();
    const c = setup();
    const h = c.P.open({ value: "#000", autoFocus: false });
    assert.deepEqual([h.element.style.left, h.element.style.top], ["500px", "400px"]);
});

test("pickers inside a Quasar dialog are attached to the dialog", () => {
    const s = setup();
    s.doc.body.innerHTML = '<div class="q-dialog__inner"><button id="a"></button></div>';
    const h = s.P.open({ anchor: s.doc.getElementById("a"), value: "#000" });
    assert.equal(h.element.parentNode.className, "q-dialog__inner");
    const t = s.P.open({ anchor: { nodeType: 3 }, value: "#000" });
    assert.equal(t.element.parentNode, s.doc.body, "non-element anchors are ignored");
});

test("closing moves focus back to the anchor when focus was inside", () => {
    const s = openPicker({ value: "#000" });
    s.q("input.is-text").focus();
    s.h.close();
    assert.equal(s.doc.activeElement, s.anchor);
    const t = openPicker({ value: "#000" });
    t.q("input.is-text").focus();
    t.anchor.focus = () => { throw new Error("x"); };
    assert.doesNotThrow(() => t.h.close());
});

/* ------------------------------------------------------------------ gradient mode */

function gradPicker(value, extra = {}) {
    const s = openPicker(Object.assign({ value, allowGradient: true }, extra));
    s.tabs = s.qa(".mcp-tab");
    s.gbody = s.qa(".mcp-body")[1];
    s.g = (sel) => s.gbody.querySelector(sel);
    s.ga = (sel) => s.gbody.querySelectorAll(sel);
    return s;
}

test("a gradient value opens on the Gradient tab with stops, angle and result", () => {
    const s = gradPicker("linear-gradient(90deg, #ff0000 0%, #0000ff 100%)");
    assert.ok(s.tabs[1].classList.contains("is-on"));
    assert.equal(s.tabs[1].getAttribute("aria-selected"), "true");
    assert.ok(s.qa(".mcp-body")[0].classList.contains("mcp-hide"));
    assert.equal(s.ga(".mcp-stop").length, 2);
    const nums = s.ga("input.is-num");
    assert.equal(nums[0].value, "90");
    assert.equal(s.ga("input.is-text")[0].value, "#ff0000");
    assert.equal(s.g(".mcp-ib").disabled, true, "two stops can't lose one");
    assert.equal(s.g("div.mcp-css").textContent, "linear-gradient(90deg, #ff0000 0%, #0000ff 100%)");
    assert.equal(s.q(".mcp-hint").textContent, "Click the bar to add a stop · drag to move");
    s.h.close();
    assert.deepEqual(s.log.done, ["linear-gradient(90deg, #ff0000 0%, #0000ff 100%)"]);
    assert.equal(s.win.localStorage.getItem("mycelian.colorPicker.recent"), null);
});

test("switching tabs converts between solid and a default gradient", () => {
    const s = gradPicker("#ff0000");
    assert.ok(s.tabs[0].classList.contains("is-on"));
    s.tabs[0].click(); // already solid
    s.tabs[1].click();
    assert.match(s.h.value(), /^linear-gradient\(90deg, #ff0000 0%, #/);
    s.tabs[1].click();
    // edit the selected stop then go back: solid takes the stop's colour
    s.input = s.ga("input.is-text")[0];
    input(s, s.input, "#00ff00");
    s.tabs[0].click();
    assert.equal(s.h.value(), "#00ff00");
});

test("linear/radial switch, angle input and dial keys", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    const [lin, rad] = s.ga(".mcp-seg button");
    rad.click();
    assert.equal(s.h.value(), "radial-gradient(#000000 0%, #ffffff 100%)");
    assert.ok(s.g(".mcp-angle").classList.contains("mcp-hide"));
    lin.click();
    const ang = s.ga("input.is-num")[0];
    input(s, ang, "45");
    assert.equal(s.h.value(), "linear-gradient(45deg, #000000 0%, #ffffff 100%)");
    input(s, ang, "x");
    input(s, ang, "725");
    assert.equal(s.h.value().slice(0, 21), "linear-gradient(5deg,");
    fire(s.win, ang, "blur", { bubbles: false });
    const dial = s.g(".mcp-angle");
    fire(s.win, dial, "keydown", { key: "ArrowRight" });
    assert.equal(dial.getAttribute("aria-valuenow"), "6");
    fire(s.win, dial, "keydown", { key: "ArrowDown", shiftKey: true });
    assert.equal(dial.getAttribute("aria-valuenow"), "-9");
    fire(s.win, dial, "keydown", { key: "z" });
});

test("dragging the angle dial sets the angle (snapping with Shift)", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    const dial = s.g(".mcp-angle");
    dial._rect = { left: 0, top: 0, width: 20, height: 20 };
    fire(s.win, dial, "pointerdown", { button: 0, clientX: 10, clientY: 0 });
    assert.equal(dial.getAttribute("aria-valuenow"), "0");
    fire(s.win, dial, "pointermove", { clientX: 0, clientY: 10 });
    assert.equal(dial.getAttribute("aria-valuenow"), "270");
    fire(s.win, dial, "pointermove", { clientX: 19, clientY: 3, shiftKey: true });
    assert.equal(dial.getAttribute("aria-valuenow"), "45", "52 degrees snaps to 45");
    fire(s.win, dial, "pointermove", { clientX: 10, clientY: 10 });
    assert.equal(dial.getAttribute("aria-valuenow"), "45", "centre is ignored");
    fire(s.win, dial, "pointerup", {});
});

test("clicking the bar adds an interpolated stop; dragging moves it; keys move/remove stops", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    const bar = s.g(".mcp-gbar");
    bar._rect = { left: 0, top: 0, width: 200, height: 10 };
    fire(s.win, bar, "pointerdown", { button: 2 });
    fire(s.win, bar, "pointerdown", { button: 0, clientX: 100 });
    assert.equal(s.ga(".mcp-stop").length, 3);
    assert.equal(s.h.value(), "linear-gradient(90deg, #000000 0%, #808080 50%, #ffffff 100%)");
    fire(s.win, bar, "pointermove", { clientX: 50 });
    assert.equal(s.h.value(), "linear-gradient(90deg, #000000 0%, #808080 25%, #ffffff 100%)");
    fire(s.win, bar, "pointerup", {});
    fire(s.win, bar, "pointermove", { clientX: 150 });
    assert.match(s.h.value(), /#808080 25%/, "no drag after release");
    const stops = s.ga(".mcp-stop");
    fire(s.win, stops[2], "keydown", { key: "ArrowLeft", shiftKey: true });
    assert.match(s.h.value(), /#808080 15%/);
    fire(s.win, stops[2], "keydown", { key: "ArrowUp" });
    fire(s.win, bar, "keydown", { key: "ArrowLeft" }); // not on a stop
    fire(s.win, stops[2], "keydown", { key: "Delete" });
    assert.equal(s.h.value(), "linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    fire(s.win, s.ga(".mcp-stop")[0], "keydown", { key: "Backspace" });
    assert.equal(s.ga(".mcp-stop").length, 2, "never fewer than two");
    // grabbing an existing stop selects it without adding one
    fire(s.win, s.ga(".mcp-stop")[1], "pointerdown", { button: 0, clientX: 200 });
    assert.equal(s.ga(".mcp-stop").length, 2);
    assert.ok(s.ga(".mcp-stop")[1].classList.contains("is-on"));
    fire(s.win, bar, "pointercancel", {});
    bar.setPointerCapture = () => { throw new Error("x"); };
    bar.releasePointerCapture = () => { throw new Error("x"); };
    fire(s.win, bar, "pointerdown", { button: 0, clientX: 300 });
    fire(s.win, bar, "pointerup", {});
    assert.equal(s.ga(".mcp-stop").length, 3, "clicks past the end add a stop at 100%");
});

test("sampleAt interpolates between stops and clamps at the ends", () => {
    const s = gradPicker("linear-gradient(90deg, rgba(0, 0, 0, 0) 20%, #ffffff 80%)");
    const bar = s.g(".mcp-gbar");
    bar._rect = { left: 0, top: 0, width: 100, height: 10 };
    fire(s.win, bar, "pointerdown", { button: 0, clientX: 10 });
    fire(s.win, bar, "pointerup", {});
    fire(s.win, bar, "pointerdown", { button: 0, clientX: 95 });
    fire(s.win, bar, "pointerup", {});
    assert.equal(s.h.value(), "linear-gradient(90deg, rgba(0, 0, 0, 0) 10%, rgba(0, 0, 0, 0) 20%, #ffffff 80%, #ffffff 95%)");
});

test("stop inputs edit colour, opacity and position; delete button removes", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #888888 50%, #ffffff 100%)");
    const [txt] = s.ga("input.is-text");
    const nums = s.ga("input.is-num"); // angle, opacity, pos
    input(s, txt, "f00");
    assert.match(s.h.value(), /#ff0000 0%/);
    input(s, txt, "nope");
    assert.ok(txt.classList.contains("is-bad"));
    input(s, txt, "rgba(0, 0, 255, 0.5)");
    assert.match(s.h.value(), /rgba\(0, 0, 255, 0.5\) 0%/);
    input(s, txt, "#000000");
    assert.match(s.h.value(), /rgba\(0, 0, 0, 0.5\) 0%/, "plain colours keep the stop opacity");
    input(s, nums[1], "100");
    input(s, nums[1], "?");
    input(s, nums[2], "30.04");
    input(s, nums[2], "?");
    assert.equal(s.h.value(), "linear-gradient(90deg, #000000 30%, #888888 50%, #ffffff 100%)");
    [txt, nums[1], nums[2]].forEach((el) => fire(s.win, el, "blur", { bubbles: false }));
    s.g(".mcp-ib").click();
    assert.equal(s.ga(".mcp-stop").length, 2);
    assert.equal(s.h.value(), "linear-gradient(90deg, #888888 50%, #ffffff 100%)");
});

test("gradient colour controls edit the selected stop", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    const alphaKnob = s.gbody.querySelectorAll(".mcp-knob")[2];
    fire(s.win, alphaKnob, "keydown", { key: "PageDown" });
    assert.equal(s.h.value(), "linear-gradient(90deg, rgba(0, 0, 0, 0.9) 0%, #ffffff 100%)");
});

test("Edit as CSS: valid gradients replace the stops, non-editable ones are kept as written", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    const cssBtn = s.g(".mcp-sect .mcp-link");
    cssBtn.click();
    assert.equal(cssBtn.textContent, "Hide CSS");
    const area = s.g("textarea.mcp-css");
    assert.ok(!area.classList.contains("mcp-hide"));
    assert.equal(s.doc.activeElement, area);
    input(s, area, "radial-gradient(circle, #ff0000 0%, #00ff00 100%)");
    assert.equal(s.h.value(), "radial-gradient(circle, #ff0000 0%, #00ff00 100%)");
    input(s, area, "not css");
    assert.ok(area.classList.contains("is-bad"));
    assert.equal(s.g(".mcp-note").textContent, "Not a CSS gradient yet — the last valid one is kept.");
    assert.equal(s.h.value(), "radial-gradient(circle, #ff0000 0%, #00ff00 100%)");
    input(s, area, "repeating-linear-gradient(red 0 10px, blue 10px 20px)");
    assert.equal(s.h.value(), "repeating-linear-gradient(red 0 10px, blue 10px 20px)");
    assert.equal(s.g(".mcp-note").textContent, "Kept as written — the stop editor can’t show this one.");
    cssBtn.click();
    assert.equal(cssBtn.textContent, "Edit as CSS");
});

test("a gradient the stop editor can't read opens in the CSS box and is returned untouched", () => {
    const raw = "conic-gradient(red, blue)";
    const s = gradPicker(raw);
    assert.ok(!s.g("textarea.mcp-css").classList.contains("mcp-hide"));
    assert.equal(s.g("textarea.mcp-css").value, raw);
    assert.match(s.g(".mcp-note").textContent, /kept as written/);
    assert.equal(s.doc.activeElement, s.g("textarea.mcp-css"));
    s.h.close();
    assert.deepEqual(s.log.done, [raw]);
    const t = gradPicker(raw);
    t.ga(".mcp-seg button")[0].click(); // editing drops the raw text
    assert.match(t.h.value(), /^linear-gradient\(90deg, /);
});

test("gradient presets switch to gradient mode; without allowGradient a gradient value seeds a solid", () => {
    const s = gradPicker("#000000", { presets: ["linear-gradient(red, blue)"] });
    s.qa(".mcp-swatches")[0].children[0].click();
    assert.ok(s.tabs[1].classList.contains("is-on"));
    assert.equal(s.h.value(), "linear-gradient(180deg, #ff0000 0%, #0000ff 100%)");
    const t = openPicker({ value: "linear-gradient(90deg, #ff0000 0%, #0000ff 100%)" });
    assert.equal(t.q("input.is-text").value, "#ff0000");
    t.qa(".mcp-chip")[1].click();
    assert.equal(t.h.value(), "rgba(255, 0, 0, 0.75)");
});

test("gradient autofocus goes to the colour knob", () => {
    const s = gradPicker("linear-gradient(90deg, #000000 0%, #ffffff 100%)");
    assert.equal(s.doc.activeElement, s.gbody.querySelectorAll(".mcp-knob")[0]);
});

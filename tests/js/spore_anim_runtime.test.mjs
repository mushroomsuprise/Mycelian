// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// templates/_boilerplates/spore_anim_runtime.js: the reference copy of the Spore Studio show/hide
// animation runtime (pasted into queue_alert.html / instant_alert.html). Loaded as a classic
// script so its top-level declarations become window globals.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, plain, xfail } from "./helpers/env.mjs";

function setup(html = "") {
    const win = makeWindow();
    win.document.body.innerHTML = html;
    loadScript(win, "templates/_boilerplates/spore_anim_runtime.js", { expose: false });
    return { win, doc: win.document, clock: win.__clock };
}

test("declares the runtime as globals", () => {
    const { win } = setup();
    for (const name of ["SPORE_ANIM_CLASS_MAP", "sporeAnimClassFor", "sporeReadAnimConfig", "sporeAnimateIn", "sporeAnimateOut", "sporeShow", "sporeHide"]) {
        assert.ok(win[name], name);
    }
});

test("sporeAnimClassFor maps types and directions, unknown -> none", () => {
    const { win } = setup();
    assert.equal(win.sporeAnimClassFor("fade", "in"), "spore-anim-fade-in");
    assert.equal(win.sporeAnimClassFor("slideOut", "out"), "spore-anim-slideout");
    assert.equal(win.sporeAnimClassFor("scaleIn", "in"), "spore-anim-scalein");
    assert.equal(win.sporeAnimClassFor("bogus", "in"), "");
    assert.equal(win.sporeAnimClassFor("none", "out"), "");
});

test("sporeReadAnimConfig reads data attributes, overrides win, bad numbers fall back", () => {
    const { win, doc } = setup('<div id="e" data-spore-anim-in="fade" data-spore-anim-out-ms="900" data-spore-anim-delay-ms="x" data-spore-anim-easing="linear"></div>');
    const el = doc.getElementById("e");
    assert.deepEqual(plain(win.sporeReadAnimConfig(el)), { animIn: "fade", animOut: "none", animInMs: 300, animOutMs: 900, delayMs: 0, easing: "linear" });
    assert.deepEqual(plain(win.sporeReadAnimConfig(el, { animIn: "scaleIn", animInMs: "120", animOutMs: "zz", delayMs: 50, easing: "", animOut: null })),
        { animIn: "scaleIn", animOut: "none", animInMs: 120, animOutMs: 300, delayMs: 50, easing: "linear" });
});

test("sporeAnimateIn adds the class for the duration then cleans up", () => {
    const { win, doc, clock } = setup('<div id="e" data-spore-anim-in="fade" data-spore-anim-in-ms="200"></div>');
    const el = doc.getElementById("e");
    let done = 0;
    win.sporeAnimateIn(el, null, () => done++);
    assert.ok(el.classList.contains("spore-anim-fade-in"));
    assert.equal(el.style.animationDuration, "0.2s");
    assert.equal(el.style.animationTimingFunction, "ease-out");
    clock.tick(199);
    assert.equal(done, 0);
    clock.tick(1);
    assert.equal(done, 1);
    assert.ok(!el.classList.contains("spore-anim-fade-in"));
    assert.equal(el.style.animationDuration, "");
});

test("sporeAnimateIn/Out call done at once without an element or animation; ms 0 means 300", () => {
    const { win, doc, clock } = setup('<div id="e"></div><div id="z" data-spore-anim-out="fade" data-spore-anim-out-ms="0"></div>');
    let done = 0;
    win.sporeAnimateIn(null, null, () => done++);
    win.sporeAnimateOut(null, null, () => done++);
    win.sporeAnimateIn(doc.getElementById("e"), null, () => done++);
    win.sporeAnimateOut(doc.getElementById("e"), null, () => done++);
    win.sporeAnimateIn(null);
    win.sporeAnimateIn(doc.getElementById("e"));
    win.sporeAnimateOut(null);
    win.sporeAnimateOut(doc.getElementById("e"));
    assert.equal(done, 4);
    const z = doc.getElementById("z");
    win.sporeAnimateOut(z, null, () => done++);
    assert.equal(z.style.animationTimingFunction, "ease-out", "config default easing applies to the way out too");
    clock.tick(300);
    assert.equal(done, 5);
    win.sporeAnimateOut(z, { easing: "linear" });
    clock.tick(300);
});

test("animation cleanup survives a classList that throws", () => {
    const { win, doc, clock } = setup('<div id="e" data-spore-anim-in="fade" data-spore-anim-out="fade"></div>');
    const el = doc.getElementById("e");
    win.sporeAnimateIn(el);
    win.sporeAnimateOut(el);
    const real = el.classList;
    Object.defineProperty(el, "classList", { get: () => ({ remove() { throw new Error("detached"); }, add() {} }), configurable: true });
    assert.doesNotThrow(() => clock.tick(300));
    void real;
});

test("sporeShow unhides and animates in, after an optional delay", () => {
    const { win, doc, clock } = setup('<div id="e" data-spore-hidden="true" data-spore-anim-in="fade" data-spore-anim-delay-ms="100"></div>');
    const el = doc.getElementById("e");
    win.sporeShow("missing");
    win.sporeShow("e");
    assert.equal(el.hasAttribute("data-spore-hidden"), false);
    assert.ok(!el.classList.contains("spore-anim-fade-in"));
    clock.tick(100);
    assert.ok(el.classList.contains("spore-anim-fade-in"));
    win.sporeShow("e", { delayMs: 0, animIn: "scaleIn" });
    assert.ok(el.classList.contains("spore-anim-scalein"));
});

test("sporeHide without animation hides at once; already hidden is a no-op", () => {
    const { win, doc } = setup('<div id="e"></div>');
    const el = doc.getElementById("e");
    win.sporeHide("missing");
    win.sporeHide("e");
    assert.equal(el.getAttribute("data-spore-hidden"), "true");
    const token = el._sporeVisToken;
    win.sporeHide("e");
    assert.equal(el._sporeVisToken, token);
});

test("sporeHide animates out and only hides if no show came in between", () => {
    const { win, doc, clock } = setup('<div id="e" data-spore-anim-out="fade" data-spore-anim-out-ms="300"></div>');
    const el = doc.getElementById("e");
    win.sporeHide("e");
    assert.ok(el.classList.contains("spore-anim-fade-out"));
    clock.tick(300);
    assert.equal(el.getAttribute("data-spore-hidden"), "true");
    win.sporeShow("e");
    win.sporeHide("e");
    clock.tick(100);
    win.sporeShow("e");
    clock.tick(300);
    assert.equal(el.hasAttribute("data-spore-hidden"), false, "the newer show wins");
});

test("sporeHide pauses audio (element or child) with no fade", () => {
    const { win, doc } = setup('<audio id="a"></audio><div id="w"><audio></audio></div>');
    const a = doc.getElementById("a");
    a.paused = false;
    win.sporeHide("a");
    assert.equal(a.paused, true);
    assert.equal(a.getAttribute("data-spore-hidden"), "true");
    const inner = doc.querySelector("#w audio");
    inner.pause = () => { throw new Error("not loaded"); };
    assert.doesNotThrow(() => win.sporeHide("w"));
    doc.getElementById("w").setAttribute("data-spore-hidden", "true");
    win.sporeHide("w");
});

test("sporeHide fades audio volume out in steps, restores it, pauses and hides", () => {
    const { win, doc, clock } = setup('<audio id="a" data-spore-audio-fade-out="400"></audio>');
    const a = doc.getElementById("a");
    a.volume = 0.8;
    a.paused = false;
    win.sporeHide("a");
    clock.tick(40);
    assert.ok(Math.abs(a.volume - 0.72) < 1e-9, String(a.volume));
    clock.tick(360);
    assert.equal(a.volume, 0.8, "volume restored for the next play");
    assert.equal(a.paused, true);
    assert.equal(a.getAttribute("data-spore-hidden"), "true");
    assert.equal(clock.pending(), 0);
});

test("audio fade uses at least 8 steps and volume 1 when unknown; volume errors are ignored", () => {
    const { win, doc, clock } = setup('<audio id="a" data-spore-audio-fade-out="80"></audio><audio id="b" data-spore-audio-fade-out="80"></audio>');
    const a = doc.getElementById("a");
    const seen = [];
    Object.defineProperty(a, "volume", { get: () => undefined, set: (v) => seen.push(v), configurable: true });
    win.sporeHide("a");
    clock.tick(80);
    assert.equal(seen.length, 9, "8 steps + restore");
    assert.equal(seen.at(-1), 1);
    const b = doc.getElementById("b");
    Object.defineProperty(b, "volume", { get: () => 1, set: () => { throw new Error("ro"); }, configurable: true });
    b.pause = () => { throw new Error("x"); };
    win.sporeHide("b");
    assert.doesNotThrow(() => clock.tick(80));
    assert.equal(b.getAttribute("data-spore-hidden"), "true");
});

xfail(test, "a show during an audio fade-out keeps the element visible",
    "BUG: spore_anim_runtime.js:107 (and the same sporeHide in templates/_boilerplates/queue_alert.html:310 / instant_alert.html) applyHidden() ignores the visibility token when there is no out-animation, so a show during an audio fade is undone when the fade ends",
    () => {
        const { win, doc, clock } = setup('<audio id="a" data-spore-audio-fade-out="400"></audio>');
        win.sporeHide("a");
        clock.tick(100);
        win.sporeShow("a");
        clock.tick(400);
        assert.equal(doc.getElementById("a").hasAttribute("data-spore-hidden"), false);
    });

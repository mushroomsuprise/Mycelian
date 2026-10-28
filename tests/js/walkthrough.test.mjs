// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/walkthrough.js on the real editor.html markup: first-run
// prompt and "seen" prefs (localStorage + server), the step state machine (Next/Back/Skip/keys),
// mode/tab switching, spotlight and card placement.

import test from "node:test";
import assert from "node:assert/strict";
import { makeFetch, makeWindow, loadScript, fire, settle } from "./helpers/env.mjs";
import { makeStudioWindow, fireDomReady } from "./helpers/studio.mjs";

const PREFS = "/api/spore-studio/ui-prefs";

function setup(opts = {}) {
    const fetch = makeFetch([[PREFS, opts.prefs !== undefined ? opts.prefs : {}]]);
    const { win, internals } = makeStudioWindow({ scripts: ["walkthrough.js"], fetch, localStorage: opts.localStorage, innerWidth: opts.innerWidth, innerHeight: opts.innerHeight });
    const W = internals.walkthrough;
    const doc = win.document;
    // stand-ins for editor.js: mode buttons toggle the root class, tabs become active on click
    const root = doc.getElementById("ss-root");
    doc.getElementById("ss-mode-containers").addEventListener("click", () => root.classList.add("ss-root--containers"));
    doc.getElementById("ss-mode-templates").addEventListener("click", () => root.classList.remove("ss-root--containers"));
    for (const tab of doc.querySelectorAll(".ss-tab")) {
        tab.addEventListener("click", () => {
            for (const t of doc.querySelectorAll(".ss-tab")) { t.classList.remove("active"); }
            tab.classList.add("active");
        });
    }
    return { win, W, doc, fetch, clock: win.__clock };
}
const card = (doc) => doc.querySelector(".ss-tour-card");
const tour = (doc, what) => card(doc).querySelector("[data-tour=" + what + "]");
const frames = (clock) => clock.tick(40);

test("every step points at markup that exists in editor.html", () => {
    const { W, doc } = setup();
    assert.equal(W.STEPS.length, 19);
    for (const step of W.STEPS) {
        const sels = step.sels || [step.sel];
        for (const sel of sels) { assert.ok(doc.querySelector(sel), step.title + ": " + sel); }
        if (step.tab) { assert.ok(doc.querySelector('.ss-tab[data-tab="' + step.tab + '"]'), step.tab); }
        assert.ok(["templates", "containers"].includes(step.mode));
        assert.ok(step.title && step.body);
    }
});

test("checkSeen: localStorage wins, otherwise the server prefs decide", async () => {
    assert.equal(await setup({ localStorage: { "mycelian-spore-tour-seen": "1" } }).W.checkSeen(), true);
    const s = setup({ prefs: { "mycelian-spore-tour-seen": true } });
    assert.equal(await s.W.checkSeen(), true);
    assert.equal(s.fetch.calls[0].url, PREFS);
    assert.equal(await setup({ prefs: {} }).W.checkSeen(), false);
    assert.equal(await setup({ prefs: null }).W.checkSeen(), false);
    assert.equal(await setup({ prefs: { status: 500, body: {} } }).W.checkSeen(), false);
    assert.equal(await setup({ prefs: new Error("offline") }).W.checkSeen(), false);
    const broken = setup();
    Object.defineProperty(broken.win, "localStorage", { get() { throw new Error("denied"); }, configurable: true });
    assert.equal(await broken.W.checkSeen(), false);
});

test("markSeen stores the flag locally and posts it to the server, ignoring failures", async () => {
    const { W, win, fetch } = setup({ prefs: new Error("offline") });
    W.markSeen();
    await settle();
    assert.equal(win.localStorage.getItem("mycelian-spore-tour-seen"), "1");
    const post = fetch.calls.find((c) => c.method === "POST");
    assert.equal(post.url, PREFS);
    assert.deepEqual(JSON.parse(post.body), { "mycelian-spore-tour-seen": true });
    assert.equal(post.init.headers["Content-Type"], "application/json");
    Object.defineProperty(win, "localStorage", { get() { throw new Error("denied"); }, configurable: true });
    win.fetch = () => { throw new Error("sync"); };
    assert.doesNotThrow(() => W.markSeen());
});

test("first run offers the tour after 700ms and marks it seen once shown", async () => {
    const { W, doc, clock, win, fetch } = setup();
    W.init();
    clock.tick(699);
    await settle();
    assert.equal(doc.getElementById("ss-tour-prompt"), null);
    clock.tick(1);
    await settle();
    const prompt = doc.getElementById("ss-tour-prompt");
    assert.ok(prompt);
    assert.match(prompt.textContent, /New here\?/);
    assert.equal(win.localStorage.getItem("mycelian-spore-tour-seen"), "1");
    assert.ok(fetch.calls.some((c) => c.method === "POST"));
    // a second offer does not duplicate the prompt
    W.offerFirstRun();
    await settle();
    assert.equal(doc.querySelectorAll("#ss-tour-prompt").length, 1);
});

test("no prompt when already seen; 'Not now' removes the prompt", async () => {
    const seen = setup({ prefs: { "mycelian-spore-tour-seen": true } });
    seen.W.offerFirstRun();
    await settle();
    assert.equal(seen.doc.getElementById("ss-tour-prompt"), null);

    const { W, doc } = setup();
    W.offerFirstRun();
    await settle();
    doc.querySelector("#ss-tour-prompt [data-act=later]").click();
    assert.equal(doc.getElementById("ss-tour-prompt"), null);
    assert.ok((doc.querySelector(".ss-tour-card")) === null, "tour not started");
});

test("'Start tour' from the prompt runs the tour; the Tour button works after DOMContentLoaded", async () => {
    const { W, doc, win } = setup();
    W.offerFirstRun();
    await settle();
    doc.querySelector("#ss-tour-prompt [data-act=start]").click();
    assert.equal(doc.getElementById("ss-tour-prompt"), null);
    assert.ok(doc.body.classList.contains("ss-tour-running"));
    fire(win, doc.body, "keydown", { key: "Escape" });

    const s2 = setup({ localStorage: { "mycelian-spore-tour-seen": "1" } });
    fireDomReady(s2.win);
    s2.doc.getElementById("ss-btn-tour").click();
    assert.ok(s2.doc.body.classList.contains("ss-tour-running"));
});

test("init copes with a page without the Tour button", () => {
    const { W, doc } = setup();
    doc.getElementById("ss-btn-tour").remove();
    assert.doesNotThrow(() => W.init());
});

test("startTour shows step 1 with counter, disabled Back and the blocker", () => {
    const { W, doc, clock } = setup();
    doc.querySelector(".ss-mode")._rect = { left: 10, top: 10, width: 200, height: 40 };
    W.startTour();
    assert.equal(doc.querySelector(".ss-tour-blocker").style.display, "block");
    assert.equal(card(doc).style.display, "block");
    assert.equal(tour(doc, "title").textContent, "Templates and containers");
    assert.equal(tour(doc, "count").textContent, "1 of 19");
    assert.equal(tour(doc, "back").disabled, true);
    assert.equal(tour(doc, "next").textContent, "Next");
    frames(clock);
    const spot = doc.querySelector(".ss-tour-spot");
    assert.equal(spot.style.display, "block");
    assert.deepEqual([spot.style.top, spot.style.left, spot.style.width, spot.style.height], ["4px", "4px", "212px", "52px"]);
    // card goes to the right of a left-side highlight
    assert.equal(card(doc).style.left, (4 + 212 + 14) + "px");
    assert.equal(card(doc).style.transform, "none");
    W.startTour(); // already running: no second set of nodes
    assert.equal(doc.querySelectorAll(".ss-tour-card").length, 1);
});

test("blocker swallows clicks", () => {
    const { W, doc, win } = setup();
    W.startTour();
    const blocker = doc.querySelector(".ss-tour-blocker");
    const md = fire(win, blocker, "mousedown");
    const ck = fire(win, blocker, "click");
    assert.ok(md.defaultPrevented && ck.defaultPrevented);
});

test("Next/Back/arrow keys walk the steps, switching tabs and modes; Done ends and restores", () => {
    const { W, doc, win } = setup();
    const root = doc.getElementById("ss-root");
    doc.querySelector('.ss-tab[data-tab="bindings"]').click(); // user was on Bindings
    W.startTour();
    tour(doc, "next").click();
    assert.equal(tour(doc, "count").textContent, "2 of 19");
    assert.equal(tour(doc, "back").disabled, false);
    tour(doc, "back").click();
    assert.equal(tour(doc, "count").textContent, "1 of 19");
    fire(win, doc.body, "keydown", { key: "ArrowLeft" }); // already at the start
    assert.equal(tour(doc, "count").textContent, "1 of 19");
    for (let i = 0; i < 7; i++) { fire(win, doc.body, "keydown", { key: "ArrowRight" }); }
    assert.equal(tour(doc, "title").textContent, "Properties");
    assert.ok(doc.querySelector('.ss-tab[data-tab="properties"]').classList.contains("active"));
    doc.querySelector(".ss-tabs-wrap")._rect = { left: 900, top: 50, width: 300, height: 30 };
    doc.getElementById("ss-pane-properties")._rect = { left: 900, top: 80, width: 300, height: 500 };
    win.__clock.tick(40);
    const spot = doc.querySelector(".ss-tour-spot");
    assert.deepEqual([spot.style.top, spot.style.height], ["44px", "542px"], "tab strip + pane spotlighted together");
    for (let i = 0; i < 5; i++) { tour(doc, "next").click(); }
    assert.equal(tour(doc, "title").textContent, "Canvas settings");
    assert.ok(doc.querySelector('.ss-tab[data-tab="canvas"]').classList.contains("active"));
    for (let i = 0; i < 4; i++) { tour(doc, "next").click(); }
    assert.equal(tour(doc, "title").textContent, "Container library");
    assert.ok(root.classList.contains("ss-root--containers"));
    fire(win, doc.body, "keydown", { key: "x" }); // other keys ignored
    tour(doc, "next").click();
    tour(doc, "next").click();
    assert.equal(tour(doc, "next").textContent, "Done");
    tour(doc, "next").click();
    assert.ok(!doc.body.classList.contains("ss-tour-running"));
    assert.equal(card(doc).style.display, "none");
    assert.equal(doc.querySelector(".ss-tour-spot").style.display, "none");
    assert.ok(!root.classList.contains("ss-root--containers"), "back to templates mode");
    assert.ok(doc.querySelector('.ss-tab[data-tab="bindings"]').classList.contains("active"), "original tab restored");
    // keys after the end do nothing
    fire(win, doc.body, "keydown", { key: "ArrowRight" });
    W.onKey({ key: "Escape", preventDefault() { throw new Error("should not run"); } });
});

test("Skip ends the tour; starting in containers mode returns there", () => {
    const { W, doc } = setup();
    doc.getElementById("ss-mode-containers").click();
    W.startTour();
    assert.ok(!doc.getElementById("ss-root").classList.contains("ss-root--containers"), "tour starts in templates");
    tour(doc, "skip").click();
    assert.ok(doc.getElementById("ss-root").classList.contains("ss-root--containers"));
    W.endTour(); // second end is a no-op
});

test("steps without a visible target centre the card", () => {
    const { W, doc, clock } = setup();
    W.startTour();
    frames(clock);
    assert.equal(doc.querySelector(".ss-tour-spot").style.display, "none");
    assert.equal(card(doc).style.top, "80px");
    assert.equal(card(doc).style.left, "50%");
    assert.equal(card(doc).style.transform, "translateX(-50%)");
});

test("resize re-places the spotlight while running", () => {
    const { W, doc, win, clock } = setup();
    W.startTour();
    frames(clock);
    doc.querySelector(".ss-mode")._rect = { left: 100, top: 100, width: 50, height: 50 };
    win.dispatchEvent(new win.Event("resize"));
    assert.equal(doc.querySelector(".ss-tour-spot").style.top, "94px");
    W.endTour();
    doc.querySelector(".ss-mode")._rect = { left: 300, top: 300, width: 50, height: 50 };
    W.onReflow();
    assert.equal(doc.querySelector(".ss-tour-spot").style.top, "94px");
});

test("unionRect merges several targets and uses the parent of a collapsed one", () => {
    const { W, doc } = setup();
    doc.getElementById("ss-btn-undo")._rect = { left: 100, top: 20, width: 30, height: 30 };
    doc.getElementById("ss-btn-redo")._rect = { left: 140, top: 10, width: 30, height: 20 };
    assert.deepEqual(JSON.parse(JSON.stringify(W.unionRect(["#ss-btn-undo", "#ss-btn-redo", "#missing"]))),
        { top: 10, left: 100, width: 70, height: 40, bottom: 50, right: 170 });
    const list = doc.getElementById("ss-outline-list");
    list.parentElement._rect = { left: 5, top: 6, width: 100, height: 200 };
    assert.equal(W.unionRect(["#ss-outline-list"]).height, 200);
    assert.equal(W.unionRect([]), null);
    assert.equal(W.unionRect(), null);
    list.scrollIntoView = () => { throw new Error("detached"); };
    assert.equal(W.unionRect(["#ss-outline-list"]).width, 100);
    assert.equal(W.visibleRect(null), null);
});

test("placeCard picks right, left, below, above, then docks inside a huge highlight", () => {
    const { W, doc } = setup({ innerWidth: 1000, innerHeight: 800 });
    W.ensureNodes();
    const c = card(doc);
    c._rect = { width: 300, height: 100 };
    const at = (rect) => { W.placeCard(Object.assign({ width: rect.right - rect.left, height: rect.bottom - rect.top }, rect)); return [c.style.left, c.style.top]; };
    assert.deepEqual(at({ left: 10, right: 110, top: 100, bottom: 200 }), ["124px", "100px"], "right of a left-half target");
    assert.deepEqual(at({ left: 800, right: 900, top: 100, bottom: 200 }), ["486px", "100px"], "left of a right-half target");
    assert.deepEqual(at({ left: 10, right: 990, top: 100, bottom: 200 }), ["10px", "214px"], "below a full-width bar");
    assert.deepEqual(at({ left: 10, right: 990, top: 600, bottom: 780 }), ["10px", "486px"], "above when below has no room");
    assert.deepEqual(at({ left: 0, right: 1000, top: 0, bottom: 800 }), ["686px", "686px"], "docked bottom-right inside");
    assert.equal(W.clamp(5, 10, 20), 10);
    assert.equal(W.clamp(25, 10, 20), 20);
    c._rect = null;
    assert.deepEqual(at({ left: 10, right: 110, top: 100, bottom: 200 }), ["124px", "70px"], "default 340x160 card");
});

test("showTab ignores empty names, missing and already-active tabs; goMode handles a missing button", () => {
    const { W, doc } = setup();
    W.showTab("");
    W.showTab("nope");
    const props = doc.querySelector('.ss-tab[data-tab="properties"]');
    let clicks = 0;
    props.addEventListener("click", () => clicks++);
    W.showTab("properties");
    assert.equal(clicks, 0);
    doc.getElementById("ss-mode-containers").remove();
    W.goMode("containers");
    assert.equal(W.currentMode(), "templates");
    doc.getElementById("ss-root").remove();
    assert.equal(W.currentMode(), "templates");
});

test("on an already-loaded page init runs immediately", async () => {
    const win = makeWindow({ fetch: makeFetch([[PREFS, {}]]) });
    win.document.body.innerHTML = '<button id="ss-btn-tour">Tour</button>';
    loadScript(win, "assets/default_assets/spore_studio/walkthrough.js");
    win.__clock.tick(700);
    await settle();
    assert.ok(win.document.getElementById("ss-tour-prompt"));
});

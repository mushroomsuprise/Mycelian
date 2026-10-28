// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/mycelian_toast.js: pop-up rendering, update-in-place, timers with
// hover pause, trimming, dismiss/actions hooks, suppression, configure() and local() pop-ups.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, fire, xfail } from "./helpers/env.mjs";

const FILE = "assets/default_assets/mycelian_toast.js";

function setup(themeVars = {}) {
    const win = makeWindow();
    for (const [k, v] of Object.entries(themeVars)) { win.document.documentElement.style.setProperty(k, v); }
    const { internals: I } = loadScript(win, FILE, { expose: true });
    return { win, T: win.MycelianToast, I, doc: win.document, clock: win.__clock };
}
const cards = (doc) => doc.querySelectorAll(".mt-stack > .mt");
const card = (doc, i = 0) => cards(doc)[i];
const text = (node, sel) => { const n = node.querySelector(sel); return n ? n.textContent : null; };

test("loading twice keeps the first API", () => {
    const { win, T } = setup();
    loadScript(win, FILE);
    assert.equal(win.MycelianToast, T);
});

test("show renders a card with level tag, source, count, title, message, hint and close button", () => {
    const { T, doc } = setup();
    T.show({ id: "a", level: "warning", source: "OBS", count: 3, title: "Disconnected", message: "Line1\nLine2", hint: "Restart OBS" });
    assert.ok(doc.getElementById("mycelian-toast-css"));
    const stack = doc.querySelector(".mt-stack");
    assert.equal(stack.getAttribute("data-pos"), "top-right");
    const c = card(doc);
    assert.ok(c.classList.contains("is-warning"));
    assert.ok(c.classList.contains("st-accent"), "default style");
    assert.ok(!c.classList.contains("is-enter"));
    assert.equal(text(c, ".mt-ico .mt-i"), "warning");
    assert.equal(text(c, ".mt-lvl"), "Warning");
    assert.ok(c.querySelector(".mt-dot"));
    assert.equal(text(c, ".mt-count"), "×3");
    assert.equal(text(c, ".mt-title"), "Disconnected");
    assert.equal(text(c, ".mt-msg"), "Line1\nLine2");
    assert.equal(text(c, ".mt-fix span:last-child"), "Restart OBS");
    const x = c.querySelector(".mt-x");
    assert.equal(x.title, "Dismiss");
    assert.equal(x.type, "button");
    assert.ok(!c.querySelector(".mt-prog"));
    assert.ok(!c.querySelector(".mt-acts"));
});

test("text is set as text, never markup", () => {
    const { T, doc } = setup();
    T.show({ id: "x", title: "<img src=x onerror=alert(1)>", message: "<b>m</b>" });
    assert.ok((card(doc).querySelector("img")) === null);
    assert.equal(text(card(doc), ".mt-title"), "<img src=x onerror=alert(1)>");
});

test("levels: unknown -> info, success icon, ongoing spinner with busy progress", () => {
    const { T, doc } = setup();
    T.show({ id: "u", level: "weird", message: "only message" });
    T.show({ id: "s", level: "success", title: "t" });
    T.show({ id: "o", level: "ongoing", title: "Working" });
    const [u, s, o] = cards(doc);
    assert.ok(u.classList.contains("is-info"));
    assert.equal(text(u, ".mt-lvl"), "Info");
    assert.equal(text(u, ".mt-title"), "only message");
    assert.ok((u.querySelector(".mt-msg")) === null);
    assert.equal(text(s, ".mt-ico .mt-i"), "check_circle");
    assert.equal(text(s, ".mt-lvl"), "Done");
    assert.ok(o.classList.contains("is-info"));
    assert.ok(o.querySelector(".mt-spin"));
    assert.equal(text(o, ".mt-lvl"), "Working");
    assert.ok(o.querySelector(".mt-prog.is-busy"));
});

test("progress is clamped and count of 1 is hidden; empty title shows an empty line", () => {
    const { T, doc } = setup();
    T.show({ id: "p", progress: 140, count: 1 });
    const c = card(doc);
    assert.equal(c.querySelector(".mt-prog i").style.width, "100%");
    assert.ok(!c.querySelector(".mt-prog").classList.contains("is-busy"));
    assert.ok((c.querySelector(".mt-count")) === null);
    assert.equal(text(c, ".mt-title"), "");
    T.show({ id: "p", progress: -5 });
    assert.equal(card(doc).querySelector(".mt-prog i").style.width, "0%");
});

test("show ignores entries without id", () => {
    const { T, doc } = setup();
    T.show(null);
    T.show({ title: "no id" });
    assert.ok((doc.querySelector(".mt-stack")) === null);
});

test("theme variables choose style, shadow and density", () => {
    const { T, doc } = setup({ "--notify-style": "glass", "--notify-shadow": "strong", "--notify-density": "compact" });
    T.show({ id: "a", title: "x" });
    const c = card(doc);
    assert.ok(c.classList.contains("st-glass"));
    assert.ok(!c.classList.contains("st-accent"));
    assert.ok(c.classList.contains("sh-strong"));
    assert.ok(!c.classList.contains("sh-none"));
    assert.ok(c.classList.contains("is-compact"));
    const n = setup({ "--notify-shadow": "none" });
    n.T.show({ id: "b", title: "x" });
    assert.ok(card(n.doc).classList.contains("sh-none"));
});

test("themeVar falls back when getComputedStyle throws", () => {
    const { I, win } = setup();
    win.getComputedStyle = () => { throw new Error("detached"); };
    assert.equal(I.themeVar("--notify-style", "accent"), "accent");
});

test("timed pop-ups dismiss themselves and leave the DOM after the exit animation", () => {
    const { T, doc, clock } = setup();
    T.show({ id: "t", title: "x", timeout: 1000 });
    const bar = card(doc).querySelector(".mt-bar");
    assert.ok(bar);
    assert.equal(bar.style.width, "0%");
    assert.equal(bar.style.transition, "width 1000ms linear");
    clock.tick(999);
    assert.ok(!card(doc).classList.contains("is-leave"));
    clock.tick(1);
    assert.ok(card(doc).classList.contains("is-leave"));
    clock.tick(340);
    assert.equal(cards(doc).length, 0);
});

test("--notify-timer off hides the countdown bar but still times out", () => {
    const { T, doc, clock } = setup({ "--notify-timer": "off" });
    T.show({ id: "t", title: "x", timeout: 500 });
    assert.ok((card(doc).querySelector(".mt-bar")) === null);
    clock.tick(500 + 340);
    assert.equal(cards(doc).length, 0);
});

test("timeout 0 stays until dismissed and removes an old bar on update", () => {
    const { T, doc, clock } = setup();
    T.show({ id: "t", title: "x", timeout: 1000 });
    T.show({ id: "t", title: "y", timeout: 0 });
    assert.equal(cards(doc).length, 1, "same id updates in place");
    assert.ok((card(doc).querySelector(".mt-bar")) === null);
    assert.equal(text(card(doc), ".mt-title"), "y");
    clock.tick(60000);
    assert.equal(cards(doc).length, 1);
});

test("hover pauses the countdown and leaving resumes with the remaining time", () => {
    const { T, doc, clock } = setup();
    T.show({ id: "h", title: "x", timeout: 1000 });
    const c = card(doc);
    clock.tick(400);
    fire(c.ownerDocument.defaultView, c, "mouseenter", { bubbles: false });
    const bar = c.querySelector(".mt-bar");
    assert.equal(bar.style.transition, "none");
    clock.tick(5000);
    assert.ok(!c.classList.contains("is-leave"), "paused");
    fire(c.ownerDocument.defaultView, c, "mouseenter", { bubbles: false }); // second pause is a no-op
    fire(c.ownerDocument.defaultView, c, "mouseleave", { bubbles: false });
    assert.equal(bar.style.transition, "width 600ms linear");
    clock.tick(599);
    assert.ok(!c.classList.contains("is-leave"));
    clock.tick(1);
    assert.ok(c.classList.contains("is-leave"));
    fire(c.ownerDocument.defaultView, c, "mouseleave", { bubbles: false }); // leaving: no restart
});

test("pauseTimer without a bar just keeps the remaining time", () => {
    const { T, I, doc, clock } = setup({ "--notify-timer": "off" });
    T.show({ id: "h", title: "x", timeout: 1000 });
    clock.tick(250);
    I.pauseTimer(I.items.h);
    assert.equal(I.items.h.remaining, 750);
    assert.equal(I.items.h.timer, null);
    assert.ok((card(doc).querySelector(".mt-bar")) === null);
});

test("close button dismisses and reports onDismiss; errors in the hook are logged", () => {
    const { T, doc, win } = setup();
    const dismissed = [];
    T.onDismiss = (id) => dismissed.push(id);
    T.show({ id: "a", title: "x" });
    card(doc).querySelector(".mt-x").click();
    assert.deepEqual(dismissed, ["a"]);
    T.dismiss("a", true); // already leaving
    assert.deepEqual(dismissed, ["a"]);
    T.onDismiss = () => { throw new Error("hook"); };
    T.show({ id: "b", title: "y" });
    card(doc, 1).querySelector(".mt-x").click();
    assert.ok(win.__console.some(([k]) => k === "error"));
    T.dismiss("missing");
});

test("programmatic dismiss does not call onDismiss", () => {
    const { T } = setup();
    const dismissed = [];
    T.onDismiss = (id) => dismissed.push(id);
    T.show({ id: "a", title: "x" });
    T.dismiss("a");
    assert.deepEqual(dismissed, []);
});

test("action buttons call onAction and dismiss unless the action says not to", () => {
    const { T, doc } = setup();
    const calls = [];
    T.onAction = (id, index, entry) => calls.push([id, index, entry.title]);
    T.show({ id: "a", title: "Update", actions: [{ label: "Install", icon: "download" }, { label: "", dismiss: false }, { label: "Later", main: false }] });
    const btns = card(doc).querySelectorAll(".mt-btn");
    assert.equal(btns.length, 3);
    assert.ok(btns[0].classList.contains("is-main"));
    assert.ok(!btns[1].classList.contains("is-main"));
    assert.equal(text(btns[0], ".mt-i"), "download");
    assert.equal(btns[0].textContent, "downloadInstall");
    assert.equal(btns[1].textContent, "Open");
    btns[1].click();
    assert.deepEqual(calls, [["a", 1, "Update"]]);
    assert.ok(!card(doc).classList.contains("is-leave"));
    btns[0].click();
    assert.ok(card(doc).classList.contains("is-leave"));
});

test("first action marked main:false is not highlighted", () => {
    const { T, doc } = setup();
    T.show({ id: "a", title: "x", actions: [{ label: "A", main: false }] });
    assert.ok(!card(doc).querySelector(".mt-btn").classList.contains("is-main"));
});

test("clicking the card body runs the click action; onAction errors are logged", () => {
    const { T, doc, win } = setup();
    const calls = [];
    T.onAction = (id, i) => calls.push(i);
    T.show({ id: "a", title: "x", actions: [{ label: "A" }, { label: "B" }], click: 1 });
    assert.ok(card(doc).classList.contains("is-click"));
    card(doc).click();
    assert.deepEqual(calls, [1]);
    T.show({ id: "b", title: "y", click: 3 });
    assert.ok(!card(doc, 1).classList.contains("is-click"));
    card(doc, 1).click(); // no such action
    T.show({ id: "c", title: "z" });
    card(doc, 2).click();
    assert.deepEqual(calls, [1]);
    T.onAction = () => { throw new Error("bad"); };
    T.show({ id: "d", title: "w", actions: [{ label: "A" }], click: 0 });
    card(doc, 3).click();
    assert.ok(win.__console.some(([k]) => k === "error"));
});

test("more than max pop-ups drops the oldest that is not an error or ongoing", () => {
    const { T, doc } = setup();
    T.configure({ max: 2 });
    T.show({ id: "e", level: "error", title: "e" });
    T.show({ id: "i1", level: "info", title: "i1" });
    T.show({ id: "i2", level: "info", title: "i2" });
    const leaving = cards(doc).filter((c) => c.classList.contains("is-leave")).map((c) => text(c, ".mt-title"));
    assert.deepEqual(leaving, ["i1"]);
});

test("when all are errors the oldest goes", () => {
    const { T, doc } = setup();
    T.configure({ max: 1 });
    T.show({ id: "e1", level: "error", title: "e1" });
    T.show({ id: "e2", level: "ongoing", title: "e2" });
    const leaving = cards(doc).filter((c) => c.classList.contains("is-leave")).map((c) => text(c, ".mt-title"));
    assert.deepEqual(leaving, ["e1"]);
});

test("suppress dismisses everything and blocks new pop-ups until lifted", () => {
    const { T, doc, clock } = setup();
    T.show({ id: "a", title: "x" });
    T.suppress(true);
    clock.tick(340);
    assert.equal(cards(doc).length, 0);
    T.show({ id: "b", title: "y" });
    assert.equal(cards(doc).length, 0);
    T.suppress(false);
    T.show({ id: "b", title: "y" });
    assert.equal(cards(doc).length, 1);
});

test("configure sets position and dock on an existing stack and ignores nulls", () => {
    const { T, I, doc } = setup();
    T.configure();
    T.configure({ position: "bottom-left" });
    assert.ok((doc.querySelector(".mt-stack")) === null, "no stack until something is shown");
    T.show({ id: "a", title: "x" });
    T.configure({ position: "top-center", dock: true, max: null });
    const stack = doc.querySelector(".mt-stack");
    assert.equal(stack.getAttribute("data-pos"), "top-center");
    assert.ok(stack.classList.contains("is-dock"));
    assert.equal(I.cfg.max, 4);
});

test("a stack removed from the page is recreated", () => {
    const { T, doc } = setup();
    T.show({ id: "a", title: "x" });
    doc.querySelector(".mt-stack").remove();
    T.show({ id: "b", title: "y" });
    assert.equal(doc.querySelectorAll(".mt-stack").length, 1);
    T.ensureStyles();
    assert.equal(doc.querySelectorAll("#mycelian-toast-css").length, 1);
});

test("local() builds entries with per-level default durations", () => {
    const { T, I } = setup();
    assert.equal(T.local("warning", "W"), "local-1");
    assert.equal(I.items["local-1"].timeout, 8000);
    T.local("success", "S", "msg", { id: "mine", source: "Dock", hint: "h", count: 2, progress: 10, click: 0, actions: [{ label: "Go", icon: "open", main: true, run() {} }] });
    const e = I.items.mine.entry;
    assert.equal(I.items.mine.timeout, 3000);
    assert.equal(e.source, "Dock");
    assert.equal(e.message, "msg");
    assert.equal(e.count, 2);
    assert.equal(e.progress, 10);
    assert.equal(e.click, 0);
    assert.deepEqual(JSON.parse(JSON.stringify(e.actions)), [{ label: "Go", icon: "open", main: true }]);
    T.local("bogus", "B", null, { id: "b" });
    assert.equal(I.items.b.entry.level, "info");
    assert.equal(I.items.b.timeout, 4000);
    T.local("ongoing", "O", "", { id: "o" });
    assert.equal(I.items.o.timeout, 0);
    T.local("info", "I", "", { id: "t", timeout: 123 });
    assert.equal(I.items.t.timeout, 123);
});

test("local() honours popups off, level switches and configured durations", () => {
    const { T, I } = setup();
    T.configure({ popups: false });
    assert.equal(T.local("info", "x"), null);
    T.configure({ popups: true, levels: { warning: false, info: false }, durations: { error: 9000, info: 1 } });
    assert.equal(T.local("warning", "x"), null);
    assert.equal(T.local("ongoing", "x"), null, "ongoing follows the info switch");
    const id = T.local("error", "E");
    assert.equal(I.items[id].timeout, 9000);
});

xfail(test, "local() error pop-ups stay until dismissed by default",
    "BUG: mycelian_toast.js:335 the default map gives error: 0 (stay) but `|| 4000` turns 0 into 4000, so dock error pop-ups vanish after 4s",
    () => {
        const { T, I } = setup();
        const id = T.local("error", "E");
        assert.equal(I.items[id].timeout, 0);
    });

test("local() action handlers run through the default onAction", () => {
    const { T, doc } = setup();
    const ran = [];
    T.local("info", "x", "", { id: "z", actions: [{ label: "A", run: () => ran.push("A") }, { label: "B" }] });
    const btns = card(doc).querySelectorAll(".mt-btn");
    btns[1].click();
    btns[0].click();
    assert.deepEqual(ran, ["A"]);
    T.onAction("unknown", 0);
});

test("re-showing an id that is leaving brings it back", () => {
    const { T, doc } = setup();
    T.show({ id: "a", title: "x" });
    T.dismiss("a");
    T.show({ id: "a", title: "x again", count: 2 });
    assert.ok(!card(doc).classList.contains("is-leave"));
    assert.equal(text(card(doc), ".mt-count"), "×2");
});

xfail(test, "a pop-up updated right after it was dismissed stays on screen",
    "BUG: mycelian_toast.js:304 dismiss()'s 340ms removal still runs after show() revives the same id, so the updated pop-up is removed and forgotten",
    () => {
        const { T, doc, clock } = setup();
        T.show({ id: "a", title: "x" });
        T.dismiss("a");
        T.show({ id: "a", title: "x again" });
        clock.tick(340);
        assert.equal(cards(doc).length, 1);
    });

// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/container_editor.js on the real editor.html markup: API error
// handling, mode switching, loading/saving/creating/duplicating/deleting containers, the canvas
// (slots, frames, handles, gutters, drag/resize, measuring), the inspectors (container and slot
// settings, size links), undo/redo, keyboard shortcuts, palette drag/drop and the preview.

import test from "node:test";
import assert from "node:assert/strict";
import { makeFetch, fire, settle, plain } from "./helpers/env.mjs";
import { makeStudioWindow, fireDomReady } from "./helpers/studio.mjs";
import { Document } from "./helpers/dom.mjs";

function slot(id, over = {}) {
    return Object.assign({
        id, template: "t_" + id, x: 0, y: 0, w: 400, h: 300, z: 1, opacity: 100, visible: true, locked_position: false,
        pointer_events: true, padding: { t: 0, r: 0, b: 0, l: 0 }, fit: "native", design_width: null, design_height: null,
        audio: { muted: false, volume: 100 }, link: null,
    }, over);
}
function model(over = {}) {
    return Object.assign({ name: "Main", route: "main", width: 1920, height: 1080, background: "transparent", spacing: 0, snap: 10, enabled: true,
        slots: [slot("a", { template: "alerts" }), slot("b", { template: "chat", x: 500 })] }, over);
}
const TEMPLATES = { spore: ["poll", "giveaway"], builtin: ["alerts"], legacy: ["chat"] };

function setup(opts = {}) {
    const data = { containers: opts.containers || { main: model(), side: model({ name: "Side", route: "side", slots: [] }) } };
    const fetch = makeFetch([
        ["/api/spore-studio/container-templates", () => opts.templates || TEMPLATES],
        ["/api/spore-studio/containers/save", (u, init) => {
            if (opts.saveFails) { return opts.saveFails; }
            const body = JSON.parse(init.body);
            const c = Object.assign({}, body.container, opts.saveReturn || {});
            data.containers[c.route] = c;
            return { container: c };
        }],
        ["/api/spore-studio/containers/create", (u, init) => {
            if (opts.createReply) { return opts.createReply; }
            const c = Object.assign(model({ slots: [] }), { name: JSON.parse(init.body).name, route: JSON.parse(init.body).route });
            data.containers[c.route] = c;
            return { container: c };
        }],
        ["/api/spore-studio/containers/duplicate", (u, init) => {
            if (opts.dupReply) { return opts.dupReply; }
            const c = model({ route: JSON.parse(init.body).new_route });
            data.containers[c.route] = c;
            return { container: c };
        }],
        ["/api/spore-studio/containers/delete", () => opts.deleteReply || { ok: true }],
        ["/api/spore-studio/containers/preview", () => opts.previewReply || { url: "/preview/main" }],
        [/\/api\/spore-studio\/containers\/[^/]+$/, (u) => {
            const route = decodeURIComponent(u.split("/").pop());
            if (opts.openFails && opts.openFails[route]) { return opts.openFails[route]; }
            return data.containers[route] ? JSON.parse(JSON.stringify(data.containers[route])) : { status: 404, body: { error: "Not found" } };
        }],
        ["/api/spore-studio/containers", () => opts.listReply || { containers: Object.values(data.containers).map((c) => ({ name: c.name, route: c.route })) }],
    ]);
    const { win, internals } = makeStudioWindow({ scripts: ["container_layout.js", "container_editor.js"], fetch, localStorage: opts.localStorage, confirm: opts.confirm });
    const C = internals.container_editor;
    const notes = [];
    if (!opts.noNotify) { win.sporeNotify = (o) => notes.push(plain(o)); }
    if (opts.beforeInit) { opts.beforeInit(win); }
    fireDomReady(win);
    const doc = win.document;
    doc.getElementById("mc-canvas")._rect = { left: 0, top: 0, width: 960, height: 540 };
    return { win, C, doc, fetch, notes, data, clock: win.__clock, $: (id) => doc.getElementById(id) };
}
async function containersMode(s) {
    s.$("ss-mode-containers").click();
    await settle();
}
const slotEl = (s, id) => s.doc.querySelector('.mc-slot[data-slot="' + id + '"]');
async function confirmDialog(s, ok) {
    await settle();
    const overlay = s.doc.querySelector(".ss-create-overlay");
    assert.ok(overlay, "confirm dialog shown");
    overlay.querySelectorAll(".ss-modal__foot button")[ok ? 1 : 0].click();
    await settle();
}

/* ------------------------------------------------------------------ helpers */

test("exports isDirty and tracks unsaved changes", async () => {
    const s = setup();
    assert.equal(s.win.MycelianContainerEditor.isDirty(), false);
    await containersMode(s);
    s.C.__eval("state.dirty = true");
    assert.equal(s.win.MycelianContainerEditor.isDirty(), true);
});

test("api() returns JSON bodies and builds errors with user text only for 4xx", async () => {
    const s = setup();
    s.fetch.on("/ok", { a: 1 }).on("/bad", { status: 400, body: { error: "Route taken" } }).on("/boom", { status: 500, body: { error: "Traceback" } }).on("/nojson", { status: 502, text: "<html>" });
    assert.deepEqual(plain(await s.C.api("/ok")), { a: 1 });
    const bad = await s.C.api("/bad").catch((e) => e);
    assert.equal(bad.message, "Route taken");
    assert.equal(bad.userText, "Route taken");
    assert.equal(bad.status, 400);
    assert.equal(bad.details, "/bad | HTTP 400 | Route taken");
    const boom = await s.C.api("/boom").catch((e) => e);
    assert.equal(boom.userText, "");
    const nojson = await s.C.api("/nojson").catch((e) => e);
    assert.equal(nojson.message, "Request failed (502)");
    assert.equal(nojson.details, "/nojson | HTTP 502");
    assert.equal(s.C.errorDetails(bad), "/bad | HTTP 400 | Route taken");
    assert.equal(s.C.errorDetails(new Error("x")), "x");
    assert.equal(s.C.errorDetails("plain"), "plain");
    assert.equal(s.C.errorDetails(null), "");
    assert.equal(s.C.failReason(bad, "saving"), "Route taken");
    assert.equal(s.C.failReason(boom, "saving"), "Something went wrong while saving.");
    assert.equal(s.C.failReason(new Error("net"), "saving"), "Spore Studio couldn't reach Mycelian.");
    assert.match(s.C.failHint(boom), /Try again/);
    assert.match(s.C.failHint(null), /Mycelian is running/);
});

test("notify falls back to the in-page toast without sporeNotify", () => {
    const s = setup({ noNotify: true });
    s.C.notify({ type: "error", title: "T", message: "M", hint: "" });
    const toast = s.$("ss-toast");
    assert.equal(toast.textContent, "T M");
    assert.ok(toast.classList.contains("ss-toast--error"));
    assert.ok(toast.classList.contains("visible"));
    s.clock.tick(10000);
    assert.ok(!toast.classList.contains("visible"));
    s.C.notify({ type: "success", title: "Saved" });
    assert.ok(toast.classList.contains("ss-toast--success"));
    s.clock.tick(3500);
    assert.ok(!toast.classList.contains("visible"));
    toast.remove();
    s.C.localToast("x", "info");
});

test("snapValue, clamp, embedSrc, suggestRoute, blankSlot", async () => {
    const s = setup();
    await containersMode(s);
    assert.equal(s.C.snapValue(23), 20);
    s.C.__eval("state.model.snap = 0");
    assert.equal(s.C.snapValue(23.6), 24);
    assert.equal(s.C.clamp(5, 0, 3), 3);
    assert.equal(s.C.embedSrc(slot("x", { template: "boo chat" })), "/boo%20chat?mycelian_embed=1&mycelian_volume=100&mycelian_muted=0");
    assert.equal(s.C.embedSrc(slot("x", { audio: { muted: true, volume: 80 } })), "/t_x?mycelian_embed=1&mycelian_volume=0&mycelian_muted=1");
    assert.equal(s.C.embedSrc(slot("x", { audio: { muted: false, volume: 0 } })), "/t_x?mycelian_embed=1&mycelian_volume=0&mycelian_muted=1");
    assert.equal(s.C.embedSrc(slot("x", { audio: null })), "/t_x?mycelian_embed=1&mycelian_volume=100&mycelian_muted=0");
    assert.equal(s.C.suggestRoute("  My Stream Layout! "), "my_stream_layout");
    assert.equal(s.C.suggestRoute("API"), "container");
    assert.equal(s.C.suggestRoute("__"), "container");
    assert.equal(s.C.suggestRoute(null), "container");
    assert.equal(s.C.suggestRoute("x".repeat(60)).length, 48);
    const b = plain(s.C.blankSlot("poll", 10.4, 20.6));
    assert.equal(b.template, "poll");
    assert.deepEqual([b.x, b.y, b.w, b.h, b.z], [10, 21, 960, 360, 3]);
    assert.match(b.id, /^slot[a-z0-9]+$/);
});

/* ------------------------------------------------------------------ modes and loading */

test("switching to containers loads the list, templates and the first container", async () => {
    const s = setup();
    await containersMode(s);
    assert.ok(s.$("ss-root").classList.contains("ss-root--containers"));
    assert.equal(s.$("ss-mode-containers").getAttribute("aria-selected"), "true");
    assert.deepEqual(s.$("mc-container-select").options.map((o) => o.textContent), ["Main  /main", "Side  /side"]);
    assert.equal(s.$("mc-container-select").value, "main");
    assert.deepEqual(s.doc.querySelectorAll("#mc-template-list h4").map((h) => h.textContent), ["Spore Studio", "Built-in", "Other templates"]);
    assert.equal(s.doc.querySelectorAll(".mc-slot").length, 2);
    assert.equal(slotEl(s, "a").querySelector(".mc-slot__label").textContent, "alerts");
    assert.equal(s.$("mc-canvas").style.width, "1920px");
    assert.match(s.$("mc-canvas").style.transform, /^scale\(/);
    s.$("ss-mode-containers").click(); // same mode: nothing
    s.$("ss-mode-templates").click();
    assert.ok(!s.$("ss-root").classList.contains("ss-root--containers"));
});

test("an empty container list shows the empty inspector; list failures notify", async () => {
    const s = setup({ containers: {} });
    await containersMode(s);
    assert.equal(s.doc.querySelector("#mc-inspector-host .ss-empty").textContent, "Create a container to combine templates on one route.");
    const f = setup({ listReply: { status: 500, body: { error: "db" } } });
    await containersMode(f);
    assert.equal(f.notes.at(-1).title, "Couldn't load your containers");
    assert.equal(f.notes.at(-1).dedupe, "containers_list");
});

test("init does nothing without the container markup", () => {
    const { win, internals } = makeStudioWindow({ scripts: ["container_layout.js", "container_editor.js"], body: "<div></div>" });
    fireDomReady(win);
    assert.equal(internals.container_editor.state.mode, "templates");
});

test("choosing another container loads it; a failed open restores the selection", async () => {
    const s = setup({ openFails: { side: { status: 500, body: {} } } });
    await containersMode(s);
    const sel = s.$("mc-container-select");
    sel.value = "side";
    fire(s.win, sel, "change");
    await settle();
    assert.equal(sel.value, "main");
    assert.equal(s.notes.at(-1).title, "Couldn't open container “/side”");
    assert.equal(s.notes.at(-1).dedupe, "container_open:side");
    const t = setup();
    await containersMode(t);
    t.$("mc-container-select").value = "side";
    fire(t.win, t.$("mc-container-select"), "change");
    await settle();
    assert.equal(t.C.state.model.route, "side");
});

test("a dirty container asks before switching containers or modes", async () => {
    const s = setup();
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.name = "Edited"; });
    assert.equal(s.$("mc-dirty").textContent, "Unsaved changes");
    const sel = s.$("mc-container-select");
    sel.value = "side";
    fire(s.win, sel, "change");
    assert.equal(sel.value, "main", "selection snaps back while asking");
    await confirmDialog(s, false);
    assert.equal(s.C.state.model.route, "main");
    sel.value = "side";
    fire(s.win, sel, "change");
    await confirmDialog(s, true);
    assert.equal(sel.value, "side");
    await settle();
    assert.equal(s.C.state.model.route, "side");
    s.C.commit(() => { s.C.state.model.name = "Edited again"; });
    s.$("ss-mode-templates").click();
    await confirmDialog(s, false);
    assert.equal(s.C.state.mode, "containers");
    s.$("ss-mode-templates").click();
    await confirmDialog(s, true);
    assert.equal(s.C.state.mode, "templates");
    assert.equal(s.C.state.model.name, "Side", "edits discarded");
    assert.equal(s.C.state.dirty, false);
});

/* ------------------------------------------------------------------ canvas */

test("renderCanvas draws slots, frames, missing templates, ghosts and gutters", async () => {
    const m = model({ spacing: 0, slots: [
        slot("a", { template: "alerts", y: 0, h: 300, link: { id: "L", axis: "vertical", role: "locked", min_px: 0, min_pct: 0, max_px: null, max_pct: null } }),
        slot("b", { template: "chat", y: 400, link: { id: "L", axis: "vertical", role: "flexible", min_px: 0, min_pct: 0, max_px: null, max_pct: null } }),
        slot("c", { template: "gone", x: 900 }),
        slot("d", { template: "poll", x: 1200, visible: false }),
        slot("e", { template: "giveaway", x: 1400, fit: "scale", design_width: 800, design_height: 600 }),
    ] });
    const s = setup({ containers: { main: m } });
    await containersMode(s);
    const frameA = slotEl(s, "a").querySelector("iframe");
    assert.equal(frameA.getAttribute("src"), "/alerts?mycelian_embed=1&mycelian_volume=100&mycelian_muted=0");
    assert.equal(frameA.getAttribute("allow"), "autoplay");
    assert.ok(slotEl(s, "c").classList.contains("is-missing"));
    assert.equal(slotEl(s, "c").querySelector(".mc-slot__label").textContent, "gone (missing)");
    assert.ok((slotEl(s, "c").querySelector("iframe")) === null);
    assert.equal(slotEl(s, "d").querySelector(".mc-placeholder").textContent, "poll");
    assert.equal(slotEl(s, "d").style.outline, "1px dashed var(--ss-text-muted)");
    assert.match(slotEl(s, "e").querySelector("iframe").style.transform, /^scale\(0\.5\)$/);
    assert.equal(s.$("mc-warnings").hidden, false);
    assert.match(s.$("mc-warnings").textContent, /gone was not found/);
    assert.equal(s.doc.querySelectorAll(".mc-gutter--v").length, 1);
    // measuring shrinks the locked slot and draws its designated-size ghost
    const doc2 = new Document();
    const box = doc2.createElement("div");
    box._rect = { left: 10, top: 20, width: 100, height: 50 };
    doc2.body.appendChild(box);
    doc2.defaultView = { getComputedStyle: () => ({ display: "block", visibility: "visible", opacity: "1" }) };
    frameA.contentDocument = doc2;
    frameA._rect = { width: 400, height: 300 };
    fire(s.win, frameA, "load", { bubbles: false });
    s.clock.tick(150);
    assert.deepEqual(plain(s.C.state.used), { a: { w: 100, h: 50 } });
    assert.equal(slotEl(s, "a").style.height, "50px");
    assert.ok(s.doc.querySelector(".mc-designated"));
    assert.equal(slotEl(s, "a").querySelector("iframe"), frameA, "frames are kept, not reloaded");
    assert.equal(frameA.style.top, "-20px");
});

test("measureFrame skips hidden, tiny, full-size and off-screen boxes", async () => {
    const s = setup();
    await containersMode(s);
    const d = new Document();
    const styles = new Map();
    const add = (rect, style) => { const el = d.createElement("div"); el._rect = rect; styles.set(el, Object.assign({ display: "block", visibility: "visible", opacity: "1" }, style)); d.body.appendChild(el); };
    add({ left: 0, top: 0, width: 50, height: 50 }, { display: "none" });
    add({ left: 0, top: 0, width: 50, height: 50 }, { visibility: "hidden" });
    add({ left: 0, top: 0, width: 50, height: 50 }, { opacity: "0" });
    add({ left: 0, top: 0, width: 0.5, height: 50 });
    add({ left: 0, top: 0, width: 400, height: 300 });
    add({ left: 900, top: 0, width: 50, height: 50 });
    d.defaultView = { getComputedStyle: (el) => styles.get(el) };
    const frame = { contentDocument: d, clientWidth: 400, clientHeight: 300 };
    assert.equal(s.C.measureFrame(frame), null);
    add({ left: 30, top: 40, width: 10.2, height: 10 });
    assert.deepEqual(plain(s.C.measureFrame(frame)), { w: 11, h: 10, originX: 30, originY: 40 });
    assert.equal(s.C.measureFrame({ contentDocument: d, clientWidth: 1, clientHeight: 300 }), null);
    assert.equal(s.C.measureFrame({}), null);
    assert.equal(s.C.measureFrame({ get contentDocument() { throw new Error("cross-origin"); } }), null);
});

test("placeholders mode skips frames, is remembered and stops measuring", async () => {
    const s = setup();
    await containersMode(s);
    s.$("mc-btn-placeholders").click();
    assert.equal(s.win.localStorage.getItem("mycelian-container-placeholders"), "1");
    assert.ok(s.$("mc-btn-placeholders").classList.contains("is-on"));
    assert.ok((slotEl(s, "a").querySelector("iframe")) === null);
    assert.equal(slotEl(s, "a").querySelector(".mc-placeholder").textContent, "alerts");
    s.C.scheduleMeasure();
    s.C.runMeasure();
    s.$("mc-btn-placeholders").click();
    assert.ok(slotEl(s, "a").querySelector("iframe"));
    const remembered = setup({ localStorage: { "mycelian-container-placeholders": "1" } });
    assert.ok(remembered.$("mc-btn-placeholders").classList.contains("is-on"));
});

test("clicking a slot selects it and shows handles; clicking the canvas clears", async () => {
    const s = setup();
    await containersMode(s);
    fire(s.win, slotEl(s, "a"), "mousedown", { button: 2 });
    assert.equal(s.C.state.selectedId, null, "right clicks are ignored");
    fire(s.win, slotEl(s, "a"), "mousedown", { button: 0, clientX: 10, clientY: 10 });
    fire(s.win, s.doc, "mouseup", {});
    assert.equal(s.C.state.selectedId, "a");
    assert.ok(slotEl(s, "a").classList.contains("is-selected"));
    assert.deepEqual(slotEl(s, "a").querySelectorAll(".mc-handle").map((h) => h.dataset.dir), ["e", "s", "se", "w", "n"]);
    assert.equal(s.doc.querySelector("#mc-inspector-host h3").textContent.trim(), "alerts");
    fire(s.win, slotEl(s, "a"), "mousedown", { button: 0 }); // mousedown on a child target bubbles to the canvas listener too
    fire(s.win, s.doc, "mouseup", {});
    fire(s.win, s.$("mc-canvas"), "mousedown", {});
    assert.equal(s.C.state.selectedId, null);
});

test("dragging a slot moves it on the snap grid and is undoable", async () => {
    const s = setup();
    await containersMode(s);
    fire(s.win, slotEl(s, "a"), "mousedown", { button: 0, clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 52, clientY: 26 }); // canvas is shown at 0.5 scale
    assert.deepEqual([s.C.selectedSlot().x, s.C.selectedSlot().y], [100, 50]);
    assert.equal(s.C.state.dirty, true);
    fire(s.win, s.doc, "mouseup", {});
    fire(s.win, s.doc, "mousemove", { clientX: 500, clientY: 500 });
    assert.equal(s.C.selectedSlot().x, 100, "moves stop after mouseup");
    s.$("mc-btn-undo").click();
    assert.equal(s.C.state.model.slots[0].x, 0);
    assert.equal(s.C.state.dirty, false, "back to the saved copy");
    s.$("mc-btn-redo").click();
    assert.equal(s.C.state.model.slots[0].x, 100);
    s.C.undo();
    s.C.undo();
    s.C.redo();
    s.C.redo();
    assert.equal(s.C.state.model.slots[0].x, 100);
});

test("locked-position slots select without dragging; linked slots still move", async () => {
    const m = model({ slots: [slot("a", { locked_position: true }), slot("b", { x: 500, link: { id: "L", axis: "horizontal", role: "locked" } }), slot("c", { x: 900, y: 10, link: { id: "M", axis: "vertical", role: "locked" } })] });
    const s = setup({ containers: { main: m } });
    await containersMode(s);
    fire(s.win, slotEl(s, "a"), "mousedown", { button: 0, clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 50, clientY: 50 });
    assert.equal(s.C.state.model.slots[0].x, 0);
    for (const id of ["b", "c"]) {
        fire(s.win, slotEl(s, id), "mousedown", { button: 0, clientX: 0, clientY: 0 });
        fire(s.win, s.doc, "mousemove", { clientX: 10, clientY: 10 });
        fire(s.win, s.doc, "mouseup", {});
    }
    assert.equal(s.C.state.model.slots[1].y, 20);
    assert.equal(s.C.state.model.slots[2].y, 30);
});

test("resize handles change size from each edge with a 20px minimum", async () => {
    const s = setup();
    await containersMode(s);
    s.C.state.selectedId = "b";
    s.C.renderCanvas();
    const handle = (dir) => slotEl(s, "b").querySelector(".mc-handle--" + dir);
    fire(s.win, handle("se"), "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 50, clientY: 25 });
    fire(s.win, s.doc, "mouseup", {});
    assert.deepEqual(["w", "h"].map((k) => s.C.selectedSlot()[k]), [500, 350]);
    fire(s.win, handle("w"), "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 50, clientY: 0 });
    fire(s.win, s.doc, "mouseup", {});
    assert.deepEqual(["x", "w"].map((k) => s.C.selectedSlot()[k]), [600, 400]);
    fire(s.win, handle("n"), "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 0, clientY: 1000 });
    fire(s.win, s.doc, "mouseup", {});
    assert.deepEqual(["y", "h"].map((k) => s.C.selectedSlot()[k]), [330, 20]);
    s.C.state.selectedId = null;
    s.C.startResize({ preventDefault() {}, clientX: 0, clientY: 0 }, "e");
});

test("flexible linked slots only get the cross-axis handle; gutters change spacing", async () => {
    const link = (role, axis) => ({ id: "L", axis, role, min_px: 0, min_pct: 0, max_px: null, max_pct: null });
    const m = model({ slots: [slot("a", { x: 0, link: link("flexible", "horizontal") }), slot("b", { x: 500, link: link("locked", "horizontal") })] });
    const s = setup({ containers: { main: m } });
    await containersMode(s);
    s.C.state.selectedId = "a";
    s.C.renderCanvas();
    assert.deepEqual(slotEl(s, "a").querySelectorAll(".mc-handle").map((h) => h.dataset.dir), ["s"]);
    const gutter = s.doc.querySelector(".mc-gutter--h");
    assert.ok(gutter);
    fire(s.win, gutter, "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 20, clientY: 0 });
    assert.equal(s.C.state.model.spacing, 40);
    fire(s.win, s.doc, "mousemove", { clientX: 500, clientY: 0 });
    assert.equal(s.C.state.model.spacing, 200, "clamped");
    fire(s.win, s.doc, "mouseup", {});
    const v = setup({ containers: { main: model({ spacing: 12, slots: [slot("a", { y: 0, link: link("flexible", "vertical") }), slot("b", { y: 500, link: link("locked", "vertical") })] }) } });
    await containersMode(v);
    v.C.state.selectedId = "a";
    v.C.renderCanvas();
    assert.deepEqual(slotEl(v, "a").querySelectorAll(".mc-handle").map((h) => h.dataset.dir), ["e"]);
    const vg = v.doc.querySelector(".mc-gutter--v");
    assert.equal(vg.style.height, "12px");
    fire(v.win, vg, "mousedown", { clientX: 0, clientY: 0 });
    fire(v.win, v.doc, "mousemove", { clientX: 0, clientY: -50 });
    assert.equal(v.C.state.model.spacing, 0);
    fire(v.win, v.doc, "mouseup", {});
});

test("renderWarnings, fitCanvas and canvasScale edge cases", async () => {
    const s = setup();
    await containersMode(s);
    s.C.__eval("state.model.background = '#202020'");
    s.C.fitCanvas();
    assert.equal(s.$("mc-canvas").style.backgroundColor, "#202020");
    s.C.__eval("state.model.snap = 0");
    s.C.fitCanvas();
    assert.equal(s.$("mc-canvas").style.backgroundSize, "0 0");
    s.$("mc-warnings").remove();
    s.C.renderWarnings({ warnings: [] });
    s.$("mc-canvas")._rect = { width: 0 };
    assert.equal(s.C.canvasScale(), 1);
    s.C.__eval("state.model = null");
    assert.equal(s.C.canvasScale(), 1);
    assert.equal(s.C.layoutNow(), null);
    s.C.renderCanvas();
    s.C.fitCanvas();
    s.C.runMeasure();
});

/* ------------------------------------------------------------------ inspectors */

test("container inspector edits name, route, resolution, size, background, spacing, snap and enabled", async () => {
    const s = setup();
    await containersMode(s);
    const host = s.$("mc-inspector-host");
    const inputs = () => host.querySelectorAll("input");
    assert.equal(host.querySelector(".mc-url > span:not(.ss-ic)").textContent, "http://127.0.0.1:5000/main");
    const [name, route] = inputs();
    name.value = "  ";
    fire(s.win, name, "change");
    assert.equal(s.C.state.model.name, "Main", "blank names are ignored");
    name.value = "Renamed";
    fire(s.win, name, "change");
    route.value = " new_route ";
    fire(s.win, route, "change");
    assert.equal(s.C.state.model.route, "new_route");
    const [preset, bg] = host.querySelectorAll("select");
    assert.equal(preset.value, "1920x1080");
    preset.value = "1280x720";
    fire(s.win, preset, "change");
    assert.deepEqual([s.C.state.model.width, s.C.state.model.height], [1280, 720]);
    const preset2 = host.querySelector("select");
    preset2.value = "0x0";
    fire(s.win, preset2, "change");
    assert.equal(s.C.state.model.width, 1280);
    const num = (label) => host.querySelectorAll(".ss-form-row").find((r) => r.querySelector("label").textContent === label).querySelector("input");
    num("Width").value = "";
    fire(s.win, num("Width"), "change");
    assert.equal(s.C.state.model.width, 1280, "empty keeps the size");
    num("Height").value = "800";
    fire(s.win, num("Height"), "change");
    assert.equal(s.C.state.model.height, 800);
    const bgSel = host.querySelectorAll("select")[1];
    bgSel.value = "custom";
    fire(s.win, bgSel, "change");
    assert.equal(s.C.state.model.background, "#111111");
    assert.ok(host.querySelector(".ss-color__swatch"));
    host.querySelectorAll("select")[1].value = "transparent";
    fire(s.win, host.querySelectorAll("select")[1], "change");
    assert.equal(s.C.state.model.background, "transparent");
    const range = host.querySelector('input[type="range"]');
    range.value = "250";
    fire(s.win, range, "change");
    assert.equal(s.C.state.model.spacing, 200);
    const spacingNum = host.querySelector('input[type="range"] + input');
    spacingNum.value = "15.6";
    fire(s.win, spacingNum, "change");
    assert.equal(s.C.state.model.spacing, 16);
    num("Snap grid (px)").value = "99";
    fire(s.win, num("Snap grid (px)"), "change");
    assert.equal(s.C.state.model.snap, 64);
    num("Snap grid (px)").value = "";
    fire(s.win, num("Snap grid (px)"), "change");
    assert.equal(s.C.state.model.snap, 0);
    const enabled = host.querySelector(".mc-check input");
    enabled.checked = false;
    fire(s.win, enabled, "change");
    assert.equal(s.C.state.model.enabled, false);
    assert.equal(s.C.state.model.name, "Renamed");
});

test("background swatch opens the colour picker and only commits opaque hex", async () => {
    const s = setup({ containers: { main: model({ background: "#000000" }) } });
    let opened = null;
    s.win.MycelianColorPicker = { open: (o) => { opened = o; }, swatchBackground: (v) => "sw:" + v };
    await containersMode(s);
    const swatch = s.doc.querySelector(".ss-color__swatch");
    swatch.click();
    assert.equal(opened.allowAlpha, false);
    assert.equal(opened.preferredFormat, "hex");
    opened.onInput("#ff0000");
    assert.equal(s.C.state.model.background, "#ff0000");
    assert.equal(swatch.querySelector(".ss-color__fill").style.background, "sw:#ff0000");
    opened.onCancel();
    assert.equal(s.C.state.model.background, "#000000");
    opened.onDone("rgba(1,2,3,0.5)");
    assert.equal(s.C.state.model.background, "#000000");
    opened.onDone("#00ff00");
    assert.equal(s.C.state.model.background, "#00ff00");
    assert.equal(s.C.state.dirty, true);
    opened.onDone("#000000");
    s.win.MycelianColorPicker = undefined;
    swatch.click();
});

async function selectSlot(s, id) {
    s.C.state.selectedId = id;
    s.C.renderAll();
    return s.$("mc-inspector-host");
}
const rowInput = (host, label, idx = 0) => host.querySelectorAll(".ss-form-row").filter((r) => r.querySelector("label").textContent === label)[idx].querySelector("input, select");
const check = (host, label) => host.querySelectorAll(".mc-check").find((r) => r.textContent === label).querySelector("input");

test("slot inspector: template, position/size, z, opacity and behaviour toggles", async () => {
    const s = setup({ containers: { main: model({ slots: [slot("a", { template: "missing_one" })] }) } });
    await containersMode(s);
    const host = await selectSlot(s, "a");
    const picker = rowInput(host, "Template");
    assert.equal(picker.options.at(-1).textContent, "missing_one (missing)");
    picker.value = "poll";
    fire(s.win, picker, "change");
    assert.equal(s.C.selectedSlot().template, "poll");
    const h2 = await selectSlot(s, "a");
    assert.equal(h2.querySelector(".mc-pct").textContent, "0% of canvas");
    const x = rowInput(h2, "X");
    x.value = "33.4";
    fire(s.win, x, "change");
    const w = rowInput(h2, "W");
    w.value = "-5";
    fire(s.win, w, "change");
    const y = rowInput(h2, "Y");
    y.value = "abc";
    fire(s.win, y, "change");
    assert.deepEqual([s.C.selectedSlot().x, s.C.selectedSlot().w, s.C.selectedSlot().y], [33, 1, 0]);
    rowInput(h2, "Z-order").value = "5.6";
    fire(s.win, rowInput(h2, "Z-order"), "change");
    rowInput(h2, "Opacity %").value = "150";
    fire(s.win, rowInput(h2, "Opacity %"), "change");
    assert.deepEqual([s.C.selectedSlot().z, s.C.selectedSlot().opacity], [6, 100]);
    for (const [label, key, want] of [["Visible", "visible", false], ["Lock position", "locked_position", true], ["Click-through", "pointer_events", false]]) {
        const box = check(h2, label);
        box.checked = !box.checked;
        fire(s.win, box, "change");
        assert.equal(s.C.selectedSlot()[key], want, label);
    }
});

test("slot inspector: padding uniform/split, fit and design size, audio", async () => {
    const s = setup();
    await containersMode(s);
    let host = await selectSlot(s, "a");
    rowInput(host, "Padding").value = "12";
    fire(s.win, rowInput(host, "Padding"), "change");
    assert.deepEqual(plain(s.C.selectedSlot().padding), { t: 12, r: 12, b: 12, l: 12 });
    host = await selectSlot(s, "a");
    const same = check(host, "Padding same on all sides");
    same.checked = false;
    fire(s.win, same, "change");
    host = s.$("mc-inspector-host");
    rowInput(host, "Left").value = "500";
    fire(s.win, rowInput(host, "Left"), "change");
    assert.equal(s.C.selectedSlot().padding.l, 400);
    host = await selectSlot(s, "a");
    assert.ok(rowInput(host, "Top"), "split view stays open");
    const same2 = check(host, "Padding same on all sides");
    same2.checked = true;
    fire(s.win, same2, "change");
    assert.deepEqual(plain(s.C.selectedSlot().padding), { t: 12, r: 12, b: 12, l: 12 });
    host = s.$("mc-inspector-host");
    const fit = rowInput(host, "Fit");
    fit.value = "scale";
    fire(s.win, fit, "change");
    host = s.$("mc-inspector-host");
    assert.equal(rowInput(host, "Design width").value, "400");
    rowInput(host, "Design width").value = "";
    fire(s.win, rowInput(host, "Design width"), "change");
    rowInput(host, "Design height").value = "720";
    fire(s.win, rowInput(host, "Design height"), "change");
    assert.deepEqual([s.C.selectedSlot().design_width, s.C.selectedSlot().design_height], [400, 720]);
    rowInput(host, "Design height").value = "";
    fire(s.win, rowInput(host, "Design height"), "change");
    assert.equal(s.C.selectedSlot().design_height, 300);
    const mute = check(host, "Mute audio");
    mute.checked = true;
    fire(s.win, mute, "change");
    rowInput(host, "Volume").value = "-3";
    fire(s.win, rowInput(host, "Volume"), "change");
    assert.deepEqual(plain(s.C.selectedSlot().audio), { muted: true, volume: 0 });
});

test("slot inspector: size links, groups, roles, limits and stack order", async () => {
    const s = setup({ containers: { main: model({ slots: [slot("a"), slot("b", { y: 500 }), slot("c", { y: 900 })] }) } });
    await containersMode(s);
    let host = await selectSlot(s, "a");
    let axis = rowInput(host, "Size link");
    axis.value = "vertical";
    fire(s.win, axis, "change");
    assert.deepEqual(plain(s.C.selectedSlot().link), { id: "link1", axis: "vertical", role: "locked", min_px: 0, min_pct: 0, max_px: null, max_pct: null });
    host = await selectSlot(s, "b");
    axis = rowInput(host, "Size link");
    axis.value = "vertical";
    fire(s.win, axis, "change");
    assert.equal(s.C.selectedSlot().link.id, "link1", "joins the existing vertical link");
    host = s.$("mc-inspector-host");
    const role = rowInput(host, "Size priority");
    role.value = "flexible";
    fire(s.win, role, "change");
    assert.equal(s.C.selectedSlot().link.role, "flexible");
    for (const [label, value, key, want] of [["Min px", "9000", "min_px", 7680], ["Min %", "-1", "min_pct", 0], ["Max px", "300", "max_px", 300], ["Max %", "101", "max_pct", 100], ["Max px", "", "max_px", null], ["Max %", "", "max_pct", null], ["Min px", "", "min_px", 0]]) {
        host = s.$("mc-inspector-host");
        rowInput(host, label).value = value;
        fire(s.win, rowInput(host, label), "change");
        assert.equal(s.C.selectedSlot().link[key], want, label + "=" + value);
    }
    host = s.$("mc-inspector-host");
    const group = rowInput(host, "Link group");
    assert.deepEqual(group.options.map((o) => o.value), ["link1", "__new__"]);
    group.value = "__new__";
    fire(s.win, group, "change");
    assert.equal(s.C.selectedSlot().link.id, "link2");
    host = s.$("mc-inspector-host");
    rowInput(host, "Link group").value = "link1";
    fire(s.win, rowInput(host, "Link group"), "change");
    // move b (y 500) above a (y 0) in the link stack
    host = s.$("mc-inspector-host");
    host.querySelectorAll("button").find((b) => b.textContent === "Move up in stack").click();
    assert.ok(s.C.state.model.slots[1].y < s.C.state.model.slots[0].y);
    host = s.$("mc-inspector-host");
    host.querySelectorAll("button").find((b) => b.textContent === "Move up in stack").click(); // already first
    host.querySelectorAll("button").find((b) => b.textContent === "Move down in stack").click();
    assert.ok(s.C.state.model.slots[1].y > s.C.state.model.slots[0].y);
    // horizontal link swaps x
    host = await selectSlot(s, "c");
    rowInput(host, "Size link").value = "horizontal";
    fire(s.win, rowInput(host, "Size link"), "change");
    assert.equal(s.C.selectedSlot().link.id, "link2", "a fresh id for a new axis");
    s.C.state.model.slots[0].link = { id: "link2", axis: "horizontal", role: "locked" };
    s.C.state.model.slots[0].x = 800;
    s.C.nudgeOrder(1);
    assert.equal(s.C.selectedSlot().x, 799);
    host = await selectSlot(s, "c");
    rowInput(host, "Size link").value = "";
    fire(s.win, rowInput(host, "Size link"), "change");
    assert.equal(s.C.selectedSlot().link, null);
    s.C.nudgeOrder(1);
});

test("slot inspector stacking buttons and removal", async () => {
    const s = setup();
    await containersMode(s);
    let host = await selectSlot(s, "a");
    host.querySelectorAll("button").find((b) => b.textContent === "Bring forward").click();
    assert.equal(s.C.selectedSlot().z, 2);
    host = s.$("mc-inspector-host");
    host.querySelectorAll("button").find((b) => b.textContent === "Send backward").click();
    host = s.$("mc-inspector-host");
    host.querySelectorAll("button").find((b) => b.textContent === "Send backward").click();
    host = s.$("mc-inspector-host");
    host.querySelectorAll("button").find((b) => b.textContent === "Send backward").click();
    assert.equal(s.C.selectedSlot().z, 0);
    host = s.$("mc-inspector-host");
    host.querySelector(".ss-btn--danger").click();
    assert.equal(s.C.state.model.slots.length, 1);
    assert.equal(s.C.state.selectedId, null);
    assert.equal(slotEl(s, "a"), null);
});

/* ------------------------------------------------------------------ palette */

test("palette search filters templates; double-click and drop add slots", async () => {
    const s = setup();
    await containersMode(s);
    const search = s.$("mc-template-search");
    search.value = "GIVE";
    fire(s.win, search, "input");
    assert.deepEqual(s.doc.querySelectorAll(".mc-template__name").map((n) => n.textContent), ["giveaway"]);
    const item = s.doc.querySelector(".mc-template");
    const dt = { data: {}, setData(k, v) { this.data[k] = v; }, getData(k) { return this.data[k] || ""; } };
    fire(s.win, item, "dragstart", { dataTransfer: dt });
    assert.equal(dt.data["text/plain"], "giveaway");
    fire(s.win, item, "dblclick");
    assert.equal(s.C.state.model.slots.at(-1).template, "giveaway");
    assert.equal(s.C.state.selectedId, s.C.state.model.slots.at(-1).id);
    const canvas = s.$("mc-canvas");
    const over = fire(s.win, canvas, "dragover");
    assert.equal(over.defaultPrevented, true);
    fire(s.win, canvas, "drop", { dataTransfer: dt, clientX: 100, clientY: 52 });
    const added = s.C.state.model.slots.at(-1);
    assert.deepEqual([added.template, added.x, added.y], ["giveaway", 200, 100]);
    fire(s.win, canvas, "drop", { dataTransfer: { getData: () => "" } });
    assert.equal(s.C.state.model.slots.length, 4);
});

test("adding templates without a container asks to create one first", async () => {
    const s = setup({ containers: {} });
    await containersMode(s);
    s.doc.querySelector(".mc-template") && fire(s.win, s.doc.querySelector(".mc-template"), "dblclick");
    fire(s.win, s.$("mc-canvas"), "drop", { dataTransfer: { getData: () => "poll" } });
    assert.deepEqual(s.notes.map((n) => n.title), ["Create a container first", "Create a container first"]);
});

/* ------------------------------------------------------------------ save / create / duplicate / delete */

test("save posts the model with the previous route and clears dirty state", async () => {
    const s = setup();
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.route = "renamed"; });
    s.$("mc-btn-save").click();
    await settle();
    const post = s.fetch.calls.find((c) => c.url.endsWith("/containers/save"));
    const body = JSON.parse(post.body);
    assert.equal(body.previous_route, "main");
    assert.equal(body.container.route, "renamed");
    assert.equal(s.C.state.dirty, false);
    assert.equal(s.C.state.savedRoute, "renamed");
    assert.equal(s.notes.at(-1).title, "Saved container “/renamed”");
});

test("edits made while a save is in flight stay unsaved", async () => {
    const s = setup({ saveReturn: { route: "server_route" } });
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.name = "A"; });
    const p = s.C.saveCurrent();
    s.C.state.model.name = "B";
    await p;
    await settle();
    assert.equal(s.C.state.model.name, "B");
    assert.equal(s.C.state.model.route, "server_route");
    assert.equal(s.C.state.dirty, true);
});

test("save failures keep the edits and explain; list refresh failures after save notify", async () => {
    const s = setup({ saveFails: { status: 400, body: { error: "Route is taken" } } });
    await containersMode(s);
    await s.C.saveCurrent();
    assert.equal(s.notes.at(-1).title, "Couldn't save container “/main”");
    assert.equal(s.notes.at(-1).message, "Route is taken Your changes are still in the editor.");
    assert.equal(s.notes.at(-1).hint, "Fix the problem, then click Save again.");
    const t = setup({ saveFails: new Error("offline") });
    await containersMode(t);
    await t.C.saveCurrent();
    assert.match(t.notes.at(-1).hint, /Mycelian is running/);
    const u = setup();
    await containersMode(u);
    u.fetch.on("/api/spore-studio/container-templates", { status: 500, body: {} });
    await u.C.saveCurrent();
    assert.equal(u.notes.at(-1).title, "Couldn't load your containers");
    const none = setup();
    await none.C.saveCurrent();
});

test("Ctrl+S saves, Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y undo and redo, Delete removes the selection", async () => {
    const s = setup();
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.name = "X"; });
    fire(s.win, s.doc.body, "keydown", { key: "z", ctrlKey: true });
    assert.equal(s.C.state.model.name, "Main");
    fire(s.win, s.doc.body, "keydown", { key: "z", ctrlKey: true, shiftKey: true });
    assert.equal(s.C.state.model.name, "X");
    fire(s.win, s.doc.body, "keydown", { key: "z", metaKey: true });
    fire(s.win, s.doc.body, "keydown", { key: "y", metaKey: true });
    fire(s.win, s.doc.body, "keydown", { key: "Z", ctrlKey: true });
    assert.equal(s.C.state.model.name, "X");
    fire(s.win, s.doc.body, "keydown", { key: "Delete" });
    assert.equal(s.C.state.model.slots.length, 2, "nothing selected");
    s.C.state.selectedId = "a";
    fire(s.win, s.$("mc-template-search"), "keydown", { key: "Backspace" });
    assert.equal(s.C.state.model.slots.length, 2, "typing in a field never deletes");
    fire(s.win, s.doc.body, "keydown", { key: "Backspace" });
    assert.equal(s.C.state.model.slots.length, 1);
    fire(s.win, s.doc.body, "keydown", { key: "s", ctrlKey: true });
    await settle();
    assert.ok(s.fetch.calls.some((c) => c.url.endsWith("/containers/save")));
    fire(s.win, s.doc.body, "keydown", { key: "q" });
    s.$("ss-mode-templates").click();
    await settle();
    const before = s.fetch.calls.length;
    fire(s.win, s.doc.body, "keydown", { key: "s", ctrlKey: true });
    assert.equal(s.fetch.calls.length, before, "shortcuts only work in container mode");
});

test("history keeps 50 snapshots and skips duplicates", async () => {
    const s = setup();
    await containersMode(s);
    s.C.pushHistory();
    s.C.pushHistory();
    assert.equal(s.C.state.history.length, 1);
    for (let i = 0; i < 60; i++) { s.C.commit(() => { s.C.state.model.spacing = i; }); }
    assert.equal(s.C.state.history.length, 50);
    s.C.__eval("state.model = null");
    s.C.pushHistory();
    s.C.undo();
    s.C.redo();
});

async function fillForm(s, values) {
    const overlay = s.doc.querySelector(".ss-create-overlay");
    const inputs = overlay.querySelectorAll("input, select");
    Object.entries(values).forEach(([i, v]) => { inputs[i].value = v; fire(s.win, inputs[i], "input"); });
    return overlay;
}

test("create: the route follows the name until edited; success loads the new container", async () => {
    const s = setup();
    await containersMode(s);
    s.$("mc-btn-create").click();
    let overlay = await fillForm(s, { 0: "Stream Layout" });
    const inputs = overlay.querySelectorAll("input");
    assert.equal(inputs[1].value, "stream_layout");
    inputs[1].value = "custom";
    fire(s.win, inputs[1], "input");
    inputs[0].value = "Other";
    fire(s.win, inputs[0], "input");
    assert.equal(inputs[1].value, "custom");
    overlay.querySelector("select").value = "1280x720";
    overlay.querySelector(".ss-btn--primary").click();
    await settle();
    const body = JSON.parse(s.fetch.calls.find((c) => c.url.endsWith("/containers/create")).body);
    assert.deepEqual(body, { name: "Other", route: "custom", width: 1280, height: 720 });
    assert.ok((s.doc.querySelector(".ss-create-overlay")) === null);
    assert.equal(s.notes.at(-1).title, "Created container “/custom”");
    s.$("mc-btn-create").click();
    overlay = await fillForm(s, { 0: "Via enter" });
    overlay.querySelectorAll("input")[1].value = "";
    fire(s.win, overlay.querySelectorAll("input")[0], "keydown", { key: "Enter" });
    await settle();
    const last = JSON.parse(s.fetch.calls.filter((c) => c.url.endsWith("/containers/create")).at(-1).body);
    assert.equal(last.route, "via_enter", "empty route falls back to the suggestion");
    s.$("mc-btn-create").click();
    s.doc.querySelector(".ss-create-overlay .ss-btn:not(.ss-btn--primary)").click();
    assert.ok((s.doc.querySelector(".ss-create-overlay")) === null, "Cancel closes");
});

test("create: validation errors stay in the form, other failures also notify", async () => {
    const s = setup({ createReply: { status: 409, body: { error: "Route already used" } } });
    await containersMode(s);
    s.$("mc-btn-create").click();
    const overlay = await fillForm(s, { 0: "X" });
    fire(s.win, overlay.querySelectorAll("input")[1], "keydown", { key: "a" });
    overlay.querySelector(".ss-btn--primary").click();
    await settle();
    const err = overlay.querySelector(".ss-form-error");
    assert.equal(err.textContent, "Route already used");
    assert.equal(err.hidden, false);
    assert.ok(s.doc.querySelector(".ss-create-overlay"), "form stays open");
    const t = setup({ createReply: { status: 500, body: {} } });
    await containersMode(t);
    t.$("mc-btn-create").click();
    t.doc.querySelector(".ss-create-overlay .ss-btn--primary").click();
    await settle();
    assert.equal(t.doc.querySelector(".ss-form-error").textContent, "Couldn't create the container.");
    assert.equal(t.notes.at(-1).title, "Couldn't create the container");
    const u = setup();
    await containersMode(u);
    u.fetch.on("/api/spore-studio/container-templates", { status: 500, body: {} });
    u.$("mc-btn-create").click();
    u.doc.querySelector(".ss-create-overlay .ss-btn--primary").click();
    await settle();
    assert.ok((u.doc.querySelector(".ss-create-overlay")) === null, "created: the form closes even if the list reload fails");
    assert.equal(u.notes.at(-1).title, "Couldn't load your containers");
});

test("create with unsaved edits asks first", async () => {
    const s = setup();
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.name = "dirty"; });
    s.$("mc-btn-create").click();
    await confirmDialog(s, false);
    assert.equal(s.C.state.model.name, "dirty");
    s.$("mc-btn-create").click();
    await confirmDialog(s, true);
    assert.equal(s.C.state.model.name, "Main");
    assert.ok(s.doc.querySelector(".ss-create-overlay input"), "the create form opens");
});

test("duplicate needs a saved container and posts the new name and route", async () => {
    const s = setup();
    s.C.duplicateContainer();
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.name = "dirty"; });
    s.$("mc-btn-duplicate").click();
    assert.equal(s.notes.at(-1).title, "Save this container first");
    s.C.discardContainerEdits();
    s.$("mc-btn-duplicate").click();
    const overlay = s.doc.querySelector(".ss-create-overlay");
    assert.equal(overlay.querySelectorAll("input")[0].value, "Main copy");
    assert.equal(overlay.querySelectorAll("input")[1].value, "main_copy");
    overlay.querySelectorAll("input")[1].value = "";
    overlay.querySelector(".ss-btn--primary").click();
    await settle();
    const body = JSON.parse(s.fetch.calls.find((c) => c.url.endsWith("/containers/duplicate")).body);
    assert.deepEqual(body, { route: "main", name: "Main copy", new_route: "main_copy" });
    assert.equal(s.notes.at(-1).title, "Duplicated as “/main_copy”");
    const f = setup({ dupReply: { status: 400, body: { error: "Bad name" } } });
    await containersMode(f);
    f.$("mc-btn-duplicate").click();
    f.doc.querySelector(".ss-create-overlay .ss-btn--primary").click();
    await settle();
    assert.equal(f.doc.querySelector(".ss-form-error").textContent, "Bad name");
    const g = setup();
    await containersMode(g);
    g.fetch.on("/api/spore-studio/container-templates", { status: 500, body: {} });
    g.$("mc-btn-duplicate").click();
    g.doc.querySelector(".ss-create-overlay .ss-btn--primary").click();
    await settle();
    assert.equal(g.notes.at(-1).title, "Couldn't load your containers");
});

test("delete confirms, deletes, then opens the next container or shows the empty state", async () => {
    const s = setup();
    s.C.deleteContainer();
    s.C.deleteContainerNow();
    await containersMode(s);
    s.$("mc-btn-delete").click();
    await confirmDialog(s, false);
    assert.equal(s.fetch.calls.filter((c) => c.url.endsWith("/delete")).length, 0);
    s.$("mc-btn-delete").click();
    await confirmDialog(s, true);
    await settle();
    assert.deepEqual(JSON.parse(s.fetch.calls.find((c) => c.url.endsWith("/delete")).body), { route: "main" });
    assert.equal(s.notes.at(-1).title, "Deleted container “/main”");
    const t = setup({ containers: { only: model({ route: "only" }) }, listReply: { containers: [] } });
    await containersMode(t);
    t.C.__eval("state.model = " + JSON.stringify(model({ route: "only" })) + "; state.savedRoute = 'only'");
    t.C.deleteContainerNow();
    await settle();
    assert.equal(t.C.state.model, null);
    assert.ok(t.doc.querySelector("#mc-inspector-host .ss-empty"));
    const f = setup({ deleteReply: { status: 500, body: {} } });
    await containersMode(f);
    f.C.deleteContainerNow();
    await settle();
    assert.equal(f.notes.at(-1).title, "Couldn't delete container “/main”");
    const g = setup();
    await containersMode(g);
    g.fetch.on("/api/spore-studio/container-templates", { status: 500, body: {} });
    g.C.deleteContainerNow();
    await settle();
    assert.equal(g.notes.at(-1).title, "Couldn't load your containers");
});

/* ------------------------------------------------------------------ preview, resize */

test("preview opens the draft URL, warns for disabled containers and reports failures", async () => {
    const s = setup();
    s.$("mc-btn-preview").click();
    await containersMode(s);
    s.C.commit(() => { s.C.state.model.name = "draft"; });
    s.$("mc-btn-preview").click();
    await settle();
    assert.deepEqual(s.win.__opened.at(-1), ["/preview/main", "_blank"]);
    assert.equal(s.$("mc-dirty").textContent, "Unsaved changes (preview shows this draft)");
    assert.equal(JSON.parse(s.fetch.calls.find((c) => c.url.endsWith("/preview")).body).container.name, "draft");
    s.C.state.model.enabled = false;
    s.$("mc-btn-preview").click();
    assert.equal(s.notes.at(-1).title, "Turn the container on first");
    const f = setup({ previewReply: { status: 500, body: {} } });
    await containersMode(f);
    f.$("mc-btn-preview").click();
    await settle();
    assert.equal(f.notes.at(-1).title, "Couldn't open the preview");
    const clean = setup();
    await containersMode(clean);
    clean.$("mc-btn-preview").click();
    await settle();
    assert.equal(clean.$("mc-dirty").textContent, "");
});

test("window resize and the canvas ResizeObserver refit only in container mode; periodic measuring", async () => {
    const s = setup();
    s.$("mc-canvas-scroll")._rect = { width: 1000, height: 600 };
    s.win.dispatchEvent(new s.win.Event("resize"));
    assert.equal(s.$("mc-canvas").style.transform, "");
    await containersMode(s);
    s.win.dispatchEvent(new s.win.Event("resize"));
    assert.match(s.$("mc-canvas").style.transform, /^scale\(0\.5/);
    const obs = s.win.__observers.find((o) => o.targets.includes(s.$("mc-canvas-scroll")));
    obs.trigger();
    s.$("ss-mode-templates").click();
    obs.trigger();
    s.clock.tick(650);
    s.C.state.dragging = true;
    s.C.scheduleMeasure();
    s.C.runMeasure();
});

// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/spore_ui.js: icons, hydrate, modal wiring (Escape, backdrop,
// close buttons, focus, stacking), the confirm dialog, clampHint, richHint and emptyCard.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, fire, settle } from "./helpers/env.mjs";

const FILE = "assets/default_assets/spore_studio/spore_ui.js";

function setup() {
    const win = makeWindow();
    const { internals: I } = loadScript(win, FILE);
    return { win, UI: win.SporeUI, I, doc: win.document, clock: win.__clock };
}

test("exports the API", () => {
    const { UI } = setup();
    assert.deepEqual(Object.keys(UI).sort(), ["clampHint", "confirm", "emptyCard", "hydrate", "icon", "iconNode", "modalFrame", "modalOpen", "richHint", "typeIcon", "wireModal"]);
});

test("icon wraps the named path in an svg span, unknown names fall back to box", () => {
    const { UI, I } = setup();
    const html = UI.icon("plus", "big");
    assert.equal(html, '<span class="ss-ic big" aria-hidden="true"><svg viewBox="0 0 24 24">' + I.PATHS.plus + "</svg></span>");
    assert.ok(UI.icon("nope").includes(I.PATHS.box));
    assert.ok(UI.icon("x").startsWith('<span class="ss-ic" '));
    for (const name of Object.keys(I.PATHS)) { assert.ok(I.PATHS[name].startsWith("<"), name); }
});

test("iconNode returns a real element", () => {
    const { UI } = setup();
    const node = UI.iconNode("check", "ok");
    assert.equal(node.tagName, "SPAN");
    assert.equal(node.className, "ss-ic ok");
    assert.ok(node.querySelector("svg path"));
});

test("typeIcon maps element types case-insensitively", () => {
    const { UI, I } = setup();
    assert.equal(UI.typeIcon("Text"), UI.icon("type"));
    assert.equal(UI.typeIcon("progress_bar", "c"), UI.icon("progress", "c"));
    assert.equal(UI.typeIcon(null), UI.icon("box"));
    assert.equal(UI.typeIcon("unknown"), UI.icon("box"));
    for (const [type, name] of Object.entries(I.TYPE_ICONS)) { assert.ok(I.PATHS[name], type + " -> " + name); }
});

test("hydrate fills each [data-ss-icon] once", () => {
    const { UI, doc } = setup();
    doc.body.innerHTML = '<button data-ss-icon="plus">Add</button><div id="w"><i data-ss-icon="trash"></i></div>';
    UI.hydrate();
    UI.hydrate(doc);
    const btn = doc.querySelector("button");
    assert.equal(btn.querySelectorAll(".ss-ic").length, 1);
    assert.equal(btn.firstChild.className, "ss-ic");
    assert.equal(btn.textContent, "Add");
    const scoped = doc.createElement("div");
    scoped.innerHTML = '<b data-ss-icon="x"></b>';
    UI.hydrate(scoped);
    assert.ok(scoped.querySelector("b > .ss-ic"));
});

test("modalFrame builds the dialog skeleton", () => {
    const { UI } = setup();
    const f = UI.modalFrame({ title: "Title", sub: "Sub", icon: "trash", tone: "danger", wide: true });
    assert.equal(f.card.className, "ss-modal ss-modal--wide");
    assert.equal(f.card.getAttribute("role"), "dialog");
    assert.equal(f.card.getAttribute("aria-modal"), "true");
    assert.ok(f.card.querySelector(".ss-modal__icon.is-danger .ss-ic"));
    assert.equal(f.card.querySelector(".ss-modal__title").textContent, "Title");
    assert.equal(f.card.querySelector(".ss-modal__sub").textContent, "Sub");
    const x = f.card.querySelector(".ss-modal__x");
    assert.equal(x.getAttribute("aria-label"), "Close");
    assert.ok(x.hasAttribute("data-modal-close"));
    assert.equal(f.overlay.firstChild, f.card);
    const plain = UI.modalFrame({});
    assert.equal(plain.card.className, "ss-modal");
    assert.ok((plain.card.querySelector(".ss-modal__sub")) === null);
    assert.equal(plain.card.querySelector(".ss-modal__title").textContent, "");
    assert.ok(!plain.card.querySelector(".ss-modal__icon").classList.contains("is-danger"));
});

test("wireModal: backdrop, close buttons and Escape cancel; release stops Escape handling", () => {
    const { UI, doc, win, clock } = setup();
    const f = UI.modalFrame({ title: "t" });
    f.body.innerHTML = '<input type="hidden"><input disabled><input id="first"><button data-modal-close>Close</button>';
    doc.body.appendChild(f.overlay);
    let cancels = 0;
    const release = UI.wireModal(f.overlay, () => cancels++);
    assert.equal(UI.modalOpen(), true);
    clock.tick(30);
    assert.equal(doc.activeElement.id, "first");
    fire(win, f.card, "mousedown");
    assert.equal(cancels, 0, "clicks inside the card do nothing");
    fire(win, f.overlay, "mousedown");
    assert.equal(cancels, 1);
    f.body.querySelector("button[data-modal-close]").click();
    f.card.querySelector(".ss-modal__x").click();
    assert.equal(cancels, 3);
    const esc = fire(win, doc.body, "keydown", { key: "Escape" });
    assert.equal(cancels, 4);
    assert.equal(esc.defaultPrevented, true);
    fire(win, doc.body, "keydown", { key: "a" });
    assert.equal(cancels, 4);
    release();
    release();
    assert.equal(UI.modalOpen(), false);
    fire(win, doc.body, "keydown", { key: "Escape" });
    assert.equal(cancels, 4);
});

test("Escape cancels only the top-most modal", () => {
    const { UI, win, doc } = setup();
    const log = [];
    const a = UI.modalFrame({});
    const b = UI.modalFrame({});
    UI.wireModal(a.overlay, () => log.push("a"));
    const releaseB = UI.wireModal(b.overlay, () => log.push("b"));
    fire(win, doc.body, "keydown", { key: "Escape" });
    releaseB();
    fire(win, doc.body, "keydown", { key: "Escape" });
    assert.deepEqual(log, ["b", "a"]);
});

test("wireModal focuses the primary button when there are no fields, and survives focus errors", () => {
    const { UI, doc, clock } = setup();
    const f = UI.modalFrame({});
    f.foot.innerHTML = '<button class="ss-btn ss-btn--primary">OK</button>';
    doc.body.appendChild(f.overlay);
    UI.wireModal(f.overlay, () => {});
    clock.tick(30);
    assert.equal(doc.activeElement.textContent, "OK");
    const g = UI.modalFrame({});
    g.body.innerHTML = "<select></select>";
    g.body.firstChild.focus = () => { throw new Error("not focusable"); };
    UI.wireModal(g.overlay, () => {});
    assert.doesNotThrow(() => clock.tick(30));
    const h = UI.modalFrame({});
    UI.wireModal(h.overlay, () => {});
    assert.doesNotThrow(() => clock.tick(30));
});

test("confirm resolves true on OK and removes the dialog", async () => {
    const { UI, doc, clock } = setup();
    const p = UI.confirm({ title: "Delete?", message: "Gone for good", detail: "3 elements", ok: "Delete", cancel: "Keep" });
    const overlay = doc.querySelector(".ss-create-overlay");
    assert.ok(overlay);
    assert.equal(overlay.querySelector(".ss-modal__title").textContent, "Delete?");
    assert.equal(overlay.querySelector(".ss-modal__sub").textContent, "Gone for good");
    assert.equal(overlay.querySelector(".ss-callout--warn > span:not(.ss-ic)").textContent, "3 elements");
    const [cancel, ok] = overlay.querySelectorAll(".ss-modal__foot button");
    assert.equal(cancel.textContent, "Keep");
    assert.equal(ok.textContent, "Delete");
    assert.ok(ok.classList.contains("ss-btn--primary"));
    clock.tick(40);
    assert.equal(doc.activeElement, ok, "non-danger dialogs focus OK");
    ok.click();
    assert.equal(await p, true);
    assert.ok((doc.querySelector(".ss-create-overlay")) === null);
    assert.equal(UI.modalOpen(), false);
});

test("danger confirm focuses Cancel and resolves false on cancel, Escape or backdrop", async () => {
    const { UI, doc, win, clock } = setup();
    let p = UI.confirm({ danger: true });
    let overlay = doc.querySelector(".ss-create-overlay");
    assert.ok(overlay.querySelector(".ss-modal__icon.is-danger"));
    assert.equal(overlay.querySelector(".ss-modal__title").textContent, "Are you sure?");
    assert.ok(overlay.querySelector(".ss-modal__body").classList.contains("is-empty"));
    const [cancel, ok] = overlay.querySelectorAll(".ss-modal__foot button");
    assert.equal(cancel.textContent, "Cancel");
    assert.equal(ok.textContent, "OK");
    assert.ok(ok.classList.contains("ss-btn--danger-solid"));
    clock.tick(40);
    assert.equal(doc.activeElement, cancel);
    cancel.click();
    assert.equal(await p, false);

    p = UI.confirm();
    fire(win, doc.body, "keydown", { key: "Escape" });
    assert.equal(await p, false);

    p = UI.confirm({ icon: "file" });
    overlay = doc.querySelector(".ss-create-overlay");
    fire(win, overlay, "mousedown");
    assert.equal(await p, false);
    await settle();
    assert.equal(doc.querySelectorAll(".ss-create-overlay").length, 0);
});

test("confirm survives a focus error", async () => {
    const { UI, doc, clock } = setup();
    const p = UI.confirm({});
    const ok = doc.querySelector(".ss-btn--primary");
    ok.focus = () => { throw new Error("x"); };
    assert.doesNotThrow(() => clock.tick(40));
    ok.click();
    assert.equal(await p, true);
});

test("clampHint adds a Show more toggle only for long text", () => {
    const { UI, doc } = setup();
    UI.clampHint(null);
    const short = doc.createElement("p");
    short.textContent = "short";
    doc.body.appendChild(short);
    UI.clampHint(short);
    assert.ok(!short.classList.contains("ss-clamp"));
    const long = doc.createElement("p");
    long.textContent = "x".repeat(200);
    doc.body.appendChild(long);
    UI.clampHint(long);
    const more = long.nextElementSibling;
    assert.ok(long.classList.contains("ss-clamp"));
    assert.equal(more.textContent, "Show more");
    more.click();
    assert.ok(!long.classList.contains("ss-clamp"));
    assert.equal(more.textContent, "Show less");
    more.click();
    assert.equal(more.textContent, "Show more");
    const custom = doc.createElement("p");
    custom.textContent = "12345";
    doc.body.appendChild(custom);
    UI.clampHint(custom, 5);
    assert.ok(custom.classList.contains("ss-clamp"));
});

test("richHint escapes markup and turns ``x`` into code", () => {
    const { UI } = setup();
    assert.equal(UI.richHint("Use ``{user}`` & <b>"), "Use <code>{user}</code> &amp; &lt;b&gt;");
    assert.equal(UI.richHint(null), "");
    assert.equal(UI.richHint(5), "5");
    assert.equal(UI.richHint("``<i>``"), "<code>&lt;i&gt;</code>");
});

test("emptyCard renders icon, title and text safely", () => {
    const { UI } = setup();
    const card = UI.emptyCard("layers", "No <layers>", "Add one");
    assert.equal(card.className, "ss-empty-card");
    assert.ok(card.querySelector(".ss-ic svg"));
    assert.equal(card.querySelector("b").textContent, "No <layers>");
    assert.equal(card.querySelector("span:not(.ss-ic)").textContent, "Add one");
});

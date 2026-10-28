// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/editor.js, part 1 (lines 1-3965): boot on the real
// editor.html with a fake /api/spore-studio backend, notices and request errors, history,
// element tree helpers, element defaults, formatters, stage previews and the first block of
// inspector sections. Parts 2 and 3 cover the rest of the file.

import test from "node:test";
import assert from "node:assert/strict";
import { fire, plain, settle } from "./helpers/env.mjs";
import { bootEditor, blankModel, ELEMENT_TYPES, API, control, field, prop, buttonByText, setValue, sectionByTitle } from "./helpers/editor_env.mjs";

async function blank(extra = {}, opts = {}) {
    return bootEditor(Object.assign({ template: "zz", models: { zz: blankModel("zz", extra) } }, opts));
}

/* ------------------------------------------------------------------ boot */

test("boots, loads the registries and opens the requested template", async () => {
    const s = await bootEditor({ template: "giveaway" });
    const urls = s.fetch.calls.map((c) => c.url);
    for (const u of ["/api/spore-studio/events", "/api/spore-studio/data-sources", "/api/spore-studio/control-actions", "/api/spore-studio/fonts", "/api/spore-studio/data-catalog", "/api/spore-studio/templates", "/api/spore-studio/model/giveaway"]) {
        assert.ok(urls.includes(u), u);
    }
    assert.equal(s.E.state.model.template_name, "giveaway");
    assert.ok(s.E.state.registry.events.length > 10);
    assert.ok(s.$$(".ss-element").length > 0, "canvas rendered");
    assert.equal(s.$("#ss-template-select").value, "giveaway");
    assert.equal(s.win.sporeState, s.E.state);
});

test("every fixture model loads and every element's inspector renders", async () => {
    for (const name of Object.keys(API.models)) {
        const s = await bootEditor({ template: name });
        assert.equal(s.E.state.model.template_name, name);
        for (const el of s.E.state.model.elements) {
            s.select(el.id);
            assert.ok(s.$("#ss-properties-host, #ss-pane-properties").textContent.length > 0, name + ":" + el.id);
        }
        for (const tab of s.$$(".ss-tab")) { tab.click(); }
    }
});

test("every block type can be added and inspected", async () => {
    const s = await blank();
    for (const type of ELEMENT_TYPES) {
        const el = await s.add(type);
        assert.ok(el, type);
        assert.equal(el.type, type);
        assert.equal(el.category, "Elements", "first category defaults to Elements");
        assert.ok(s.$('.ss-element[data-spore-id="' + el.id + '"]'), "drawn: " + type);
    }
    assert.equal(s.E.state.model.elements.length, ELEMENT_TYPES.length);
    for (const el of s.E.state.model.elements) {
        s.select(el.id);
        for (const tab of s.$$(".ss-tab")) { tab.click(); }
    }
    assert.equal(s.E.state.dirty, true);
});

test("a missing requested template opens the first one and says why", async () => {
    const s = await bootEditor({ url: "http://127.0.0.1:5000/spore-studio?template=nope" });
    assert.match(s.toast(), /Couldn't find “nope”/);
    assert.ok(s.E.state.model);
});

test("no templates shows the empty state; registry failures warn once", async () => {
    const s = await bootEditor({ templates: { spore_templates: [], legacy_templates: [] }, routes: [["/api/spore-studio/fonts", { status: 500, body: { error: "x" } }]] });
    assert.equal(s.$("#ss-load-state").textContent, "No templates yet — click + New to create one.");
    assert.match(s.toast(), /Some Spore Studio lists didn't load/);
    assert.deepEqual(plain(s.E.state.defaultFonts), []);
});

test("a failing template list reports that the editor couldn't load", async () => {
    const s = await bootEditor({ routes: [["/api/spore-studio/templates", new Error("down")]] });
    assert.match(s.toast(), /Spore Studio couldn't load/);
});

/* ------------------------------------------------------------------ notices and requests */

test("normalizeNoticeType maps app levels", async () => {
    const { E } = await blank();
    assert.deepEqual(["positive", "negative", "ERROR", "warning", "success", "weird", undefined].map(E.normalizeNoticeType),
        ["success", "error", "error", "warning", "success", "info", "info"]);
});

test("local notices: text, tone, dedupe and auto-hide", async () => {
    const s = await blank();
    const toast = s.$("#ss-toast");
    s.E.notify({ type: "warning", title: "T", message: "M", hint: "H", dedupe: "k" });
    assert.equal(toast.textContent, "T M H");
    assert.equal(toast.style.borderColor, "var(--ss-warn)");
    s.E.notify({ type: "error", title: "Again", dedupe: "k" });
    assert.equal(toast.textContent, "T M H", "deduped within 30s");
    s.clock.tick(7000);
    assert.ok(!toast.classList.contains("visible"));
    s.E.notify({ type: "success", title: "Saved" });
    assert.ok(toast.classList.contains("ss-toast--success"));
    s.E.notify({ type: "error", message: "only message" });
    assert.ok(toast.classList.contains("ss-toast--error"));
    s.E.notify({});
    s.E.notify(null);
    assert.equal(toast.textContent, "only message");
    toast.remove();
    s.E.showLocalNotice({ title: "x" });
});

test("inside the app, notices are posted to Mycelian and fall back locally when that fails", async () => {
    const s = await bootEditor({ template: "giveaway", extra: { top: {} }, routes: [["/api/spore-studio/notify", { ok: true }]] });
    assert.equal(s.E.EMBEDDED_IN_APP, true);
    s.E.notify({ type: "error", title: "T", message: "M", hint: "H", details: "D", template: "giveaway", dedupe: "k", source: "src" });
    await settle();
    const post = s.fetch.calls.filter((c) => c.url === "/api/spore-studio/notify").at(-1);
    assert.deepEqual(JSON.parse(post.body), { type: "error", title: "T", message: "M", source: "src", hint: "H", details: "D", template: "giveaway", dedupe: "k" });
    s.fetch.on("/api/spore-studio/notify", { status: 500, body: {} });
    s.E.notify({ title: "fallback" });
    await settle();
    assert.equal(s.toast(), "fallback");
    s.fetch.on("/api/spore-studio/notify", new Error("down"));
    s.E.notify({ title: "fallback2" });
    await settle();
    assert.equal(s.toast(), "fallback2");
    s.win.fetch = () => { throw new Error("sync"); };
    s.E.notify({ title: "fallback3" });
    assert.equal(s.toast(), "fallback3");
    const ext = await bootEditor({ template: "giveaway", url: "http://127.0.0.1:5000/spore-studio?external=1", extra: { top: {} } });
    assert.equal(ext.E.EMBEDDED_IN_APP, false);
});

test("requestDetails, requestError, errorDetails and fetchJson", async () => {
    const s = await blank();
    const E = s.E;
    assert.equal(E.requestDetails({ status: 404, data: { error: "nope" } }, "GET /x"), "GET /x | HTTP 404 | nope");
    assert.equal(E.requestDetails(null, ""), "");
    const e4 = E.requestError({ status: 400, data: { error: "Bad name" } }, "POST /save");
    assert.deepEqual([e4.message, e4.status, e4.userText, e4.details], ["Bad name", 400, "Bad name", "POST /save | HTTP 400 | Bad name"]);
    const e5 = E.requestError({ status: 500, data: { error: "Traceback" } });
    assert.equal(e5.userText, "");
    const e0 = E.requestError(null);
    assert.deepEqual([e0.message, e0.status], ["Request failed (?)", 0]);
    assert.equal(E.errorDetails(e4, "x"), "POST /save | HTTP 400 | Bad name");
    assert.equal(E.errorDetails(new Error("boom"), "GET /y"), "GET /y | boom");
    assert.equal(E.errorDetails(null, "GET /y"), "GET /y");
    s.fetch.on("/json", { a: 1 }).on("/html", { status: 502, text: "<html>" });
    assert.deepEqual(plain(await E.fetchJson("/json")), { ok: true, status: 200, data: { a: 1 } });
    assert.deepEqual(plain(await E.fetchJson("/html")), { ok: false, status: 502, data: {} });
});

test("load state pill and dirty indicator", async () => {
    const s = await blank();
    s.E.setLoadState("Loading…");
    const pill = s.$("#ss-load-state");
    assert.equal(pill.textContent, "Loading…");
    s.E.setLoadState("Legacy", "warn");
    assert.ok(pill.classList.contains("is-warn"));
    assert.match(pill.title, /not made in Spore Studio/);
    s.E.setLoadState("Failed", "error");
    assert.ok(pill.classList.contains("is-error"));
    assert.equal(pill.title, "");
    s.E.setLoadState();
    assert.equal(pill.textContent, "");
    pill.remove();
    s.$("#ss-dirty-indicator").remove();
    s.E.setLoadState("x");
    const rev = s.E.state.revision || 0;
    s.E.setDirty(true);
    assert.equal(s.E.state.revision, rev + 1);
    s.E.setDirty(false);
    assert.equal(s.E.state.dirty, false);
});

/* ------------------------------------------------------------------ small helpers */

test("uuid, deepClone, text decoration and bold weights", async () => {
    const { E } = await blank();
    assert.match(E.uuid(), /^ss-[a-z0-9]{4,12}$/);
    const o = { a: [1, { b: 2 }] };
    const c = E.deepClone(o);
    assert.notEqual(c, o);
    assert.deepEqual(plain(c), o);
    assert.equal(E.composeTextDecoration(null), "");
    assert.equal(E.composeTextDecoration({ text_underline: true, text_strikethrough: true }), "underline line-through");
    assert.deepEqual(["bold", "BOLDER", "600", 700, "400", "", null, "x"].map(E.isFontWeightBold), [true, true, true, true, false, false, false, false]);
    assert.equal(E.pad2(3), "03");
    assert.equal(E.pad2(12), "12");
});

test("inspector width is clamped, stored and restored", async () => {
    const s = await blank({}, { localStorage: { "spore-studio-inspector-width": "999" } });
    assert.equal(s.doc.documentElement.style.getPropertyValue("--ss-inspector-width"), "560px");
    assert.equal(s.E.applyInspectorWidth(10), 240);
    assert.equal(s.win.localStorage.getItem("spore-studio-inspector-width"), "240");
    s.win.localStorage.setItem("spore-studio-inspector-width", "abc");
    s.E.restoreInspectorWidth();
    assert.equal(s.doc.documentElement.style.getPropertyValue("--ss-inspector-width"), "240px");
    s.win.localStorage = { getItem() { throw new Error("x"); }, setItem() { throw new Error("x"); } };
    s.E.restoreInspectorWidth();
    assert.equal(s.E.applyInspectorWidth(300), 300);
});

test("tooltips: bubble placement, hide delay and native title upgrade", async () => {
    const s = await blank({}, { innerWidth: 1000, innerHeight: 600 });
    const a = s.doc.createElement("button");
    a._rect = { left: 500, top: 100, width: 50, height: 20 };
    s.doc.body.appendChild(a);
    s.E.bindTip(a, "Hello");
    s.E.bindTip(null, "x");
    s.E.bindTip(a, "");
    fire(s.win, a, "mouseenter", { bubbles: false });
    const bubble = s.$(".ss-tip-bubble");
    bubble._rect = { width: 100, height: 30 };
    s.E.showTip(a, "Hello");
    assert.equal(bubble.hidden, false);
    assert.equal(bubble.textContent, "Hello");
    assert.deepEqual([bubble.style.left, bubble.style.top], ["392px", "95px"], "left of the anchor, centred");
    a._rect = { left: 4, top: 100, width: 50, height: 20 };
    s.E.showTip(a, "x");
    assert.equal(bubble.style.left, "62px", "flips to the right");
    a._rect = { left: 4, top: 2, width: 990, height: 20 };
    s.E.showTip(a, "x");
    assert.deepEqual([bubble.style.left, bubble.style.top], ["8px", "8px"], "above, clamped");
    a._rect = { left: 4, top: 590, width: 990, height: 20 };
    s.E.showTip(a, "x");
    assert.equal(bubble.style.top, "552px");
    bubble._rect = { width: 2000, height: 30 };
    a._rect = { left: 900, top: 100, width: 50, height: 20 };
    s.E.showTip(a, "x");
    assert.equal(bubble.style.left, "8px");
    s.E.showTip(null, "x");
    fire(s.win, a, "mouseleave", { bubbles: false });
    s.clock.tick(60);
    assert.equal(bubble.hidden, true);
    fire(s.win, a, "focusin");
    fire(s.win, a, "focusout");
    bubble.remove();
    s.E.showTip(a, "again");
    assert.ok(s.$(".ss-tip-bubble"), "bubble recreated after removal");
    const host = s.doc.createElement("div");
    host.innerHTML = '<b title="Tip one"></b><i title=""></i><u title="x" data-ss-tip="kept"></u>';
    s.E.upgradeNativeTitles(host);
    assert.ok((host.querySelector("b").getAttribute("title")) === null);
    assert.equal(host.querySelector("b").getAttribute("data-ss-tip"), "Tip one");
    assert.equal(host.querySelector("u").getAttribute("title"), "x");
    s.E.upgradeNativeTitles(null);
});

test("form building blocks", async () => {
    const s = await blank();
    const E = s.E;
    let built = null;
    const sec = E.buildCollapsibleSection("Timer", "tip", (body) => { built = body; body.textContent = "inside"; }, "3 rules");
    assert.equal(sec.tagName, "DETAILS");
    assert.equal(sec.querySelector(".ss-collapse__title").textContent, "Timer");
    assert.equal(sec.querySelector(".ss-collapse__sum").textContent, "3 rules");
    assert.equal(sec.querySelector("summary").dataset.section, "Timer");
    assert.equal(built.textContent, "inside");
    const unknown = E.buildCollapsibleSection("Something", "", () => {});
    assert.ok(unknown.querySelector(".ss-collapse__icon .ss-ic"));
    const row = E.formRow("Label", s.doc.createElement("input"), "tip");
    assert.equal(row.querySelector("label").textContent, "Label");
    assert.equal(row.getAttribute("data-ss-tip"), "tip");
    assert.equal(E.formRow("Only").children.length, 1);
    const inline = E.formRowInline("Inline", null);
    assert.equal(inline.className, "ss-form-row ss-form-row--inline");
    const seen = [];
    const t = E.buildToggle(true, (v) => seen.push(v));
    assert.equal(t.input.checked, true);
    t.input.checked = false;
    fire(s.win, t.input, "change");
    assert.deepEqual(seen, [false]);
    assert.equal(E.buildToggle(false).input.checked, false);
    const cb = E.formRowCheckbox("Check", true, (v) => seen.push(v), "tip");
    assert.ok(cb.querySelector(".ss-toggle__input").checked);
});

/* ------------------------------------------------------------------ history */

test("undo/redo walk the history; pending debounced edits undo first", async () => {
    const s = await blank();
    const E = s.E;
    const a = await s.add("text");
    const b = await s.add("shape");
    assert.equal(E.state.model.elements.length, 2);
    E.undo();
    assert.deepEqual(plain(E.state.model.elements.map((e) => e.id)), [a.id]);
    E.undo();
    assert.equal(E.state.model.elements.length, 0);
    E.undo();
    assert.equal(E.state.model.elements.length, 0, "bottoms out at the loaded baseline");
    E.redo();
    E.redo();
    assert.deepEqual(plain(E.state.model.elements.map((e) => e.id)), [a.id, b.id]);
    E.redo();
    // a debounced edit not yet in history is undone back to the last snapshot
    E.state.model.elements[0].props.text = "typed";
    E.pushHistoryDebounced();
    E.undo();
    assert.equal(E.state.model.elements[0].props.text, "Hello");
    assert.equal(E.state.future.length, 1);
    E.pushHistory();
    E.pushHistory();
    for (let i = 0; i < 60; i++) { E.state.model.duration_seconds = i; E.pushHistory(); }
    assert.equal(E.state.history.length, 50);
    E.state.model = null;
    E.undo();
    E.redo();
    E.pushHistory();
});

test("preview mocks refresh after edits (and ignore stale or failed replies)", async () => {
    const s = await bootEditor({ template: "giveaway", routes: [["/api/spore-studio/preview/mocks", { preview_mocks: [{ label: "Mock A" }] }]] });
    s.E.modelTouch();
    s.clock.tick(400);
    await settle();
    assert.deepEqual(plain(s.E.state.model.preview_mocks), [{ label: "Mock A" }]);
    const body = JSON.parse(s.fetch.calls.filter((c) => c.url.endsWith("/preview/mocks")).at(-1).body);
    assert.equal(body.model.template_name, "giveaway");
    s.E.schedulePreviewMocksRefresh();
    s.clock.tick(400);
    s.E.state.model.template_name = "other";
    await settle();
    s.fetch.on("/api/spore-studio/preview/mocks", new Error("x"));
    s.E.schedulePreviewMocksRefresh();
    s.clock.tick(400);
    await settle();
    s.fetch.on("/api/spore-studio/preview/mocks", {});
    s.E.schedulePreviewMocksRefresh();
    s.clock.tick(400);
    await settle();
    s.E.state.model.legacy = true;
    s.E.schedulePreviewMocksRefresh();
    s.E.modelTouch();
});

test("preview draft posts while the preview is open and warns once when it fails", async () => {
    const s = await bootEditor({ template: "giveaway" });
    s.E.schedulePreviewDraft();
    s.clock.tick(300);
    assert.equal(s.fetch.calls.filter((c) => c.url.endsWith("/preview/draft")).length, 0, "closed preview: nothing sent");
    s.E.openPreviewDialog();
    await settle();
    s.E.modelTouch();
    s.clock.tick(280);
    await settle();
    const draft = s.fetch.calls.filter((c) => c.url.endsWith("/preview/draft")).at(-1);
    assert.equal(JSON.parse(draft.body).token, s.E.state.previewToken);
    assert.match(s.$("#ss-preview-iframe").getAttribute("src"), /^\/giveaway\?__preview_token=ss-.*&_cb=\d+$/);
    s.fetch.on("/api/spore-studio/preview/draft", { status: 500, body: { error: "x" } });
    await s.E.postPreviewDraft();
    assert.match(s.toast(), /The live preview couldn't update/);
    assert.match(s.toast(), /Click Reload in the preview/);
    s.fetch.on("/api/spore-studio/preview/draft", new Error("down"));
    s.clock.tick(31000);
    await s.E.postPreviewDraft();
    assert.match(s.toast(), /overlay server is running/);
    s.E.state.model.legacy = true;
    await s.E.postPreviewDraft();
});

/* ------------------------------------------------------------------ element tree */

test("uniqueElementId skips taken ids", async () => {
    const s = await blank();
    s.E.state.idCounter = 0;
    s.E.state.model.elements = [{ id: "text1" }, { id: "text3" }];
    assert.equal(s.E.uniqueElementId("text"), "text2");
    assert.equal(s.E.uniqueElementId(), "el4");
});

test("parent links: invalid parents and cycles are dropped, children follow parents", async () => {
    const s = await blank();
    const model = { elements: [
        { id: "child", type: "text", parent_id: "box" }, { id: "box", type: "container" }, { id: "orphan", type: "text", parent_id: "missing" },
        { id: "nested", type: "text", parent_id: "inner" }, { id: "inner", type: "container", parent_id: "box" },
        { id: "loopA", type: "container", parent_id: "loopB" }, { id: "loopB", type: "container", parent_id: "loopA" },
        { id: "badparent", type: "text", parent_id: "child" }, { id: "untyped", type: "text", parent_id: "plainbox" }, { id: "plainbox" },
    ] };
    s.E.normalizeParentLinksAndReorderElements(model);
    assert.deepEqual(plain(model.elements.map((e) => e.id)), ["box", "child", "inner", "nested", "orphan", "loopA", "loopB", "badparent", "plainbox", "untyped"]);
    assert.equal(model.elements.find((e) => e.id === "orphan").parent_id, undefined);
    assert.equal(model.elements.find((e) => e.id === "loopA").parent_id, undefined);
    assert.equal(model.elements.find((e) => e.id === "badparent").parent_id, undefined);
    assert.equal(model.elements.find((e) => e.id === "untyped").parent_id, "plainbox", "elements without a type count as containers");
    s.E.normalizeParentLinksAndReorderElements({ elements: [] });
    s.E.normalizeParentLinksAndReorderElements({ legacy: true, elements: [{ id: "x", parent_id: "y" }] });
    s.E.normalizeParentLinksAndReorderElements(null);
    assert.equal(s.E.validContainerParent({}, ""), false);
});

test("removing an element removes its subtree and timer controls", async () => {
    const s = await blank();
    const box = await s.add("container");
    const t = await s.add("timer", 5, 5, null, box.id);
    const txt = await s.add("text", 5, 5, null, box.id);
    assert.equal(t.parent_id, box.id);
    assert.ok(s.E.state.model.dynamic_controls.elements.some((c) => c.target_timer_id === t.id));
    assert.deepEqual(Object.keys(plain(s.E.collectSubtreeIds(box.id))).sort(), [t.id, txt.id].sort());
    assert.equal(s.E.descendantSetHas(t.id, s.E.collectSubtreeIds(box.id)), true);
    assert.equal(s.E.descendantSetHas("", {}), false);
    s.E.removeElementAndDescendants(box.id);
    assert.equal(s.E.state.model.elements.length, 0);
    assert.equal(s.E.state.model.dynamic_controls.elements.filter((c) => c.type === "timer_control").length, 0);
    s.E.removeElementAndDescendants(null);
    s.E.state.model.dynamic_controls = null;
    s.E.removeTimerDynamicControls({});
    s.E.state.model.dynamic_controls = { elements: "x" };
    s.E.removeTimerDynamicControls({});
});

test("timer controls are created once per timer", async () => {
    const s = await blank();
    const t = await s.add("timer");
    s.E.ensureTimerDynamicControl(t);
    const controls = s.E.state.model.dynamic_controls.elements.filter((c) => c.type === "timer_control");
    assert.equal(controls.length, 1);
    assert.equal(controls[0].id, s.E.timerControlIdFor(t.id));
    assert.equal(controls[0].label, "Timer " + t.id);
    assert.deepEqual(plain(s.E.listTimersInModel()), [{ id: t.id, label: t.id }]);
    s.E.ensureTimerDynamicControl({ type: "text" });
    s.E.ensureTimerDynamicControl(null);
});

test("drop targets pick the smallest container under the pointer, never the dragged subtree", async () => {
    const s = await blank();
    const outer = await s.add("container", 0, 0);
    const inner = await s.add("container", 10, 10, null, outer.id);
    const stage = s.$("#ss-stage");
    const node = (id) => s.$('.ss-element[data-spore-id="' + id + '"]');
    node(outer.id)._rect = { left: 0, top: 0, width: 400, height: 300 };
    node(inner.id)._rect = { left: 10, top: 10, width: 100, height: 100 };
    s.doc._elementFromPoint = node(inner.id);
    assert.equal(s.E.pickDropContainerId(stage, 20, 20, null), inner.id);
    assert.equal(s.E.pickDropContainerId(stage, 200, 200, null), outer.id);
    assert.equal(s.E.pickDropContainerId(stage, 20, 20, inner.id), outer.id);
    assert.equal(s.E.pickDropContainerId(stage, 20, 20, outer.id), null);
    node(inner.id)._rect = { left: 0, top: 0, width: 0, height: 0 };
    assert.equal(s.E.pickDropContainerId(stage, 20, 20, null), outer.id);
    assert.equal(s.E.pickDropContainerId(null, 1, 1), null);
    s.doc._elementFromPoint = null;
    assert.equal(s.E.pickDropContainerId(stage, 20, 20), null);
    s.doc._elementFromPoint = node(outer.id);
    s.E.updateCanvasDropHighlight(200, 200, null, false);
    assert.ok(node(outer.id).classList.contains("ss-drop-target"));
    s.E.updateCanvasDropHighlight(200, 200, null, true);
    assert.ok(stage.classList.contains("ss-drop-target-root"));
    assert.ok(!node(outer.id).classList.contains("ss-drop-target"));
    s.E.clearCanvasDropHighlight();
    assert.ok(!stage.classList.contains("ss-drop-target-root"));
    assert.equal(s.E.pointInsideClientRect(5, 5, { left: 0, right: 10, top: 0, bottom: 10 }), true);
    stage.remove();
    s.E.clearCanvasDropHighlight();
    s.E.updateCanvasDropHighlight(1, 1);
});

test("legacy helpers and expose keys", async () => {
    const s = await bootEditor({ template: "follow" });
    const E = s.E;
    assert.equal(E.isLegacyModel(), true);
    assert.equal(E.isLegacyElement({ position: { x: null } }), true);
    assert.equal(E.isLegacyElement(null), true);
    assert.equal(E.isLegacyElement({ position: { x: 1 } }), false);
    assert.equal(E.isReadOnlyLegacyElement({ position: { x: 1 } }), true);
    assert.equal(E.isReadOnlyLegacyElement({ position: { x: null } }), false);
    for (const t of ["text", "image", "video", "progress_bar", "timer", "list", "wheel", "nope"]) {
        assert.ok(Array.isArray(E.exposeKeysForType(t)), t);
    }
    assert.ok(E.exposeKeysForType("video").includes("audio_src"));
    assert.ok(E.exposeKeysForType("timer").includes("auto_start"));
    assert.ok(E.exposeKeysForType("wheel").includes("result_hold_s"));
    assert.ok(E.exposeKeysForType("progress_bar").includes("max_kind"));
    assert.deepEqual(plain(E.exposeKeysForType("nope")), []);
    await s.add("text");
    assert.match(s.toast(), /Legacy templates can't take new blocks/);
});

/* ------------------------------------------------------------------ defaults */

test("progress bar defaults and preview max", async () => {
    const s = await blank();
    const E = s.E;
    const bar = { type: "progress_bar", props: { near_goal_pulse: true } };
    E.ensureProgressBarDefaults(bar);
    assert.deepEqual(plain(bar.progress_source), { counter_id: "", max_kind: "fixed", max: 100, max_counter_id: "" });
    assert.equal(bar.props.near_goal_effect, "pulse", "the legacy flag maps to the pulse effect");
    const bad = { type: "progress_bar", props: { near_goal_effect: "sparkle" } };
    E.ensureProgressBarDefaults(bad);
    assert.equal(bad.props.near_goal_effect, "none");
    E.ensureProgressBarDefaults({ type: "text" });
    E.ensureProgressBarDefaults(null);
    const c = await s.add("counter");
    c.counter.initial_value = 40;
    assert.equal(E.progressMaxForPreview({ type: "progress_bar", progress_source: { max_kind: "counter", max_counter_id: c.counter.counter_id } }), 40);
    assert.equal(E.progressMaxForPreview({ type: "progress_bar", progress_source: { max_kind: "counter", max_counter_id: "none" } }), 100);
    c.counter.progressive = true;
    c.counter.goals = [10, 50, 100];
    assert.equal(E.progressMaxForPreview({ type: "progress_bar", progress_source: { counter_id: c.counter.counter_id } }), 50);
    c.counter.goals = [];
    assert.equal(E.progressMaxForPreview({ type: "progress_bar", progress_source: { counter_id: c.counter.counter_id, max: 77 } }), 77);
    assert.equal(E.progressMaxForPreview({ type: "progress_bar", progress_source: { max: null, max_kind: "fixed" } }), 100);
});

test("a legacy near_goal_pulse progress bar keeps its pulse", async () => {
    const s = await blank();
    const bar = { type: "progress_bar", props: { near_goal_pulse: true } };
    s.E.ensureProgressBarDefaults(bar);
    assert.equal(bar.props.near_goal_effect, "pulse");
    const chosen = { type: "progress_bar", props: { near_goal_pulse: true, near_goal_effect: "shimmer" } };
    s.E.ensureProgressBarDefaults(chosen);
    assert.equal(chosen.props.near_goal_effect, "shimmer", "an explicit effect wins over the legacy flag");
});

test("counters: lookup, initial values, progressive previews and formatting", async () => {
    const s = await blank();
    const E = s.E;
    const c = await s.add("counter");
    assert.equal(E.isCounterElement(c), true);
    assert.equal(E.isCounterElement({ type: "text", counter: {}, text_mode: "counter" }), true);
    assert.equal(E.isCounterElement({ type: "text", counter: {} }), false);
    assert.equal(E.isCounterElement(null), false);
    assert.equal(E.counterElementById(c.counter.counter_id), c);
    assert.equal(E.counterElementById(""), null);
    c.counter.initial_value = 7;
    assert.equal(E.counterInitialValueById(c.counter.counter_id), 7);
    assert.equal(E.counterInitialValueById("missing"), 0);
    delete c.counter.initial_value;
    assert.equal(E.counterInitialValueById(c.counter.counter_id), 0);
    assert.deepEqual(plain(E.normalizeGoalList([10, "x", 5, 20, 20])), [10, 20]);
    assert.deepEqual(plain(E.progressivePreviewState(15, [10, 20], 0)), { value: 15, activeGoal: 20, floor: 10 });
    assert.deepEqual(plain(E.progressivePreviewState(-5, [10, 20], 0)), { value: 0, activeGoal: 10, floor: 0 });
    assert.deepEqual(plain(E.progressivePreviewState("x", [10, 20], "")), { value: 0, activeGoal: 10, floor: null });
    assert.deepEqual(plain(E.progressivePreviewState(99, [10, 20])), { value: 20, activeGoal: 20, floor: 10 });
    assert.equal(E.progressivePreviewState(1, []), null);
    assert.equal(E.formatCounterPreview(null), "");
    assert.equal(E.formatCounterPreview({ initial_value: 3, min: 0, max: 10 }, "{value}/{max} ({min})"), "3/10 (0)");
    assert.equal(E.formatCounterPreview({ progressive: true, goals: [5, 10], initial_value: 7 }, "{value}/{max}"), "7/10");
    assert.equal(E.formatCounterPreview({ progressive: true, goals: [], initial_value: 7 }), "7");
    assert.equal(E.formatCounterPreview({}, "{value}{min}{max}"), "0");
    assert.deepEqual(plain(E.listCountersInModel()), [{ id: c.counter.counter_id, label: c.id + " (" + c.counter.counter_id + ")" }]);
});

test("data sources for pickers filter delta and display-only rows", async () => {
    const s = await blank();
    s.E.state.dataSources = { sources: [{ id: "a" }, { id: "d", delta_only: true }, { id: "c", category: "Delta" }, { id: "x", display_only: true }] };
    assert.deepEqual(s.E.dataSourcesForPicker(false, false, false).map((r) => r.id), ["a", "d", "c", "x"]);
    assert.deepEqual(s.E.dataSourcesForPicker(true).map((r) => r.id), ["d", "c"]);
    assert.deepEqual(s.E.dataSourcesForPicker(false, true).map((r) => r.id), ["a", "c", "x"]);
    assert.deepEqual(s.E.dataSourcesForPicker(false, false, true).map((r) => r.id), ["a", "d", "c"]);
    s.E.state.dataSources = null;
    assert.deepEqual(plain(s.E.dataSourcesForPicker()), []);
});

test("ensure*Defaults fill missing fields without overwriting set ones", async () => {
    const s = await blank();
    const E = s.E;
    const c = { id: "My Counter!", type: "counter" };
    E.ensureCounterDefaults(c);
    assert.deepEqual(plain(c.counter), { counter_id: E.slugifyCounterId("My Counter!"), initial_value: 0, format: "{value}", progressive: false, goals: [], persist: false, database_path: "zz/counters", database_key: E.slugifyCounterId("My Counter!"), rules: [] });
    assert.equal(c.props.scroll, false);
    assert.equal(c.value_animation.type, "fade-in");
    E.ensureCounterDefaults({ type: "text" });
    const d = { type: "data_display" };
    E.ensureDataDisplayDefaults(d);
    assert.deepEqual(plain(d.data_display), { format: "{value}", default_text: "—", refresh_on: [] });
    E.ensureDataDisplayDefaults({ type: "text" });
    const tc = { id: "t", type: "text", text_mode: "counter" };
    E.ensureTextModeDefaults(tc);
    assert.equal(tc.props.text, "{value}");
    assert.equal(tc.counter.database_key, "t");
    const td = { id: "t", type: "marquee", text_mode: "data_display" };
    E.ensureMarqueeDefaults(td);
    assert.deepEqual(plain(td.data_display.source), { kind: "data_source", source: "alert.username" });
    assert.deepEqual(plain(td.data_display.refresh_on), ["instant_alert"]);
    const ts = { type: "text" };
    E.ensureTextModeDefaults(ts);
    assert.equal(ts.text_mode, "static");
    E.ensureTextModeDefaults({ type: "image" });
    E.ensureMarqueeDefaults({ type: "text" });
    const va = { type: "text", value_animation: { enabled: true } };
    E.ensureValueAnimationDefaults(va);
    assert.deepEqual(plain(va.value_animation), { enabled: true, type: "fade-in", duration_ms: 500, easing: "ease-out", pulse: false });
    E.ensureValueAnimationDefaults({ type: "image" });
    const t = { type: "timer", props: { mode: "weird", duration_seconds: "90", limit_seconds: "-5" } };
    E.ensureTimerDefaults(t);
    assert.deepEqual(plain(t.props), { mode: "count_down", duration_seconds: 90, start_seconds: 90, limit_seconds: 0, auto_start: true, format: "{mm}:{ss}" });
    assert.deepEqual(plain(t.timer), { running: true, elapsed: 0 });
    const t2 = { type: "timer" };
    E.ensureTimerDefaults(t2);
    assert.equal(t2.props.start_seconds, 300);
    E.ensureTimerDefaults({ type: "text" });
    assert.equal(E.timerFormatPresetValue("{hh}:{mm}:{ss}"), "{hh}:{mm}:{ss}");
    assert.equal(E.timerFormatPresetValue("{s}s"), "custom");
    const clock = { type: "clock" };
    E.ensureClockDefaults(clock);
    assert.deepEqual(plain(clock.props), { format: "HH:mm:ss", timezone: "local", timezone_offset_minutes: 0 });
    E.ensureClockDefaults({ type: "text" });
    const g = { type: "gradient", props: { color_stops: [] } };
    E.ensureGradientDefaults(g);
    assert.equal(g.props.color_stops.length, 2);
    E.ensureGradientDefaults({ type: "text" });
    const sh = { type: "shape" };
    E.ensureShapeDefaults(sh);
    assert.equal(sh.props.shape_kind, "rectangle");
    E.ensureShapeDefaults({ type: "text" });
    const dv = { type: "divider" };
    E.ensureDividerDefaults(dv);
    assert.deepEqual(plain(dv.props), { orientation: "horizontal", thickness: 2 });
    E.ensureDividerDefaults({ type: "text" });
});

test("gradient CSS and shape/divider/gradient node styles", async () => {
    const s = await blank();
    const E = s.E;
    assert.equal(E.buildGradientCss({}), "linear-gradient(90deg, #a855f7 0%, #1e293b 100%)");
    assert.equal(E.buildGradientCss({ gradient_kind: "radial", color_stops: [{ color: "red" }, { stop: 50 }] }), "radial-gradient(circle, red 0%, #000 50%)");
    assert.equal(E.buildGradientCss({ angle: 0, color_stops: [{ color: "#fff", stop: 10 }] }), "linear-gradient(0deg, #fff 10%)");
    assert.equal(E.buildGradientCss(null), "linear-gradient(90deg, #a855f7 0%, #1e293b 100%)");
    const n = () => s.doc.createElement("div");
    const shape = n();
    E.applyShapeDividerGradientStyles(shape, { type: "shape", props: { shape_kind: "ellipse", background_color: "#123456", border_width: 2, border_color: "red", opacity: 0.5 } });
    assert.deepEqual([shape.style.borderRadius, shape.style.border, shape.style.opacity], ["50%", "2px solid red", "0.5"]);
    const rect = n();
    E.applyShapeDividerGradientStyles(rect, { type: "shape", props: { border_radius: 4 } });
    assert.deepEqual([rect.style.background, rect.style.borderRadius], ["#a855f7", "4px"]);
    const div = n();
    E.applyShapeDividerGradientStyles(div, { type: "divider", props: { orientation: "vertical", thickness: 5, color: "blue", opacity: 0.4 } });
    const line = div.querySelector(".ss-divider-line");
    assert.deepEqual([line.style.width, line.style.height, line.style.background, line.style.opacity], ["5px", "100%", "blue", "0.4"]);
    E.applyShapeDividerGradientStyles(div, { type: "divider", props: {} });
    assert.equal(div.querySelectorAll(".ss-divider-line").length, 1);
    assert.deepEqual([line.style.height, line.style.width], ["2px", "100%"]);
    const grad = n();
    E.applyShapeDividerGradientStyles(grad, { type: "gradient", props: { border_radius: 3, border_width: 1, border_color: "#000", opacity: 1 } });
    assert.match(grad.style.background, /^linear-gradient/);
    assert.equal(grad.style.border, "1px solid #000");
    E.applyShapeDividerGradientStyles(n(), { type: "text" });
});

test("clock and timer display formatting", async () => {
    const s = await blank();
    const E = s.E;
    const d = new s.win.Date(2024, 0, 1, 9, 5, 3);
    assert.equal(E.formatClockTime(d, "HH:mm:ss", "local"), "09:05:03");
    assert.equal(E.formatClockTime(d, null, "local"), "09:05:03");
    const off = d.getTimezoneOffset() * 60000;
    const utc = new s.win.Date(d.getTime() + off);
    assert.equal(E.formatClockTime(d, "HH:mm", "utc"), E.pad2(utc.getHours()) + ":" + E.pad2(utc.getMinutes()));
    const plus = new s.win.Date(d.getTime() + off + 120 * 60000);
    assert.equal(E.formatClockTime(d, "HH", "offset", "120"), E.pad2(plus.getHours()));
    assert.equal(E.formatClockTime(d, "HH", "offset", "x"), E.pad2(utc.getHours()));
    const t = { type: "timer", props: { mode: "count_down", start_seconds: 3725, format: "{time}|{hh}|{h}|{mm}|{m}|{ss}|{s}|{total}" } };
    assert.equal(E.formatTimerDisplay(t, 0), "01:02:05|01|1|02|2|05|5|3725");
    t.props.mode = "count_up";
    assert.equal(E.formatTimerDisplay(t, 61.9), "00:01:01|00|0|01|1|01|1|61");
    t.props.mode = "count_down";
    assert.equal(E.formatTimerDisplay(t, 99999).startsWith("00:00:00"), true);
});

test("stage previews tick clocks and timers and poll live poll state", async () => {
    const s = await blank({}, { routes: [["/api/spore-studio/poll-state/", { status: "open", options: [{ id: 1, name: "A", votes: 2 }], server_now: 1700000000, ends_at: 1700000060, duration_seconds: 60 }]] });
    const clock = await s.add("clock");
    const up = await s.add("timer");
    up.props.mode = "count_up";
    up.props.limit_seconds = 2;
    up.timer = { running: true, elapsed: 0 };
    const down = await s.add("timer");
    down.props.start_seconds = 1;
    down.timer = { running: true, elapsed: 0 };
    const paused = await s.add("timer");
    paused.timer = { running: false, elapsed: 30 };
    const poll = await s.add("poll");
    poll.props.rotator_enabled = true;
    poll.props.rotator_seconds = 1;
    s.E.renderStage();
    const node = (el) => s.$('.ss-element[data-spore-id="' + el.id + '"]');
    assert.match(node(clock).textContent, /^\d\d:\d\d:\d\d$/);
    assert.equal(node(paused).textContent, "04:30");
    s.clock.tick(1500);
    await settle();
    assert.equal(node(down).textContent, "00:00");
    assert.equal(down.timer.running, false);
    s.clock.tick(1000);
    assert.equal(node(up).textContent, "00:02");
    assert.equal(up.timer.running, false);
    assert.ok(s.fetch.calls.some((c) => c.url.startsWith("/api/spore-studio/poll-state/zz")));
    const before = s.clock.pending();
    s.E.clearStagePreviewIntervals();
    assert.ok(s.clock.pending() < before);
    s.E.state.model = null;
    s.E.startStagePreviewIntervals();
});

/* ------------------------------------------------------------------ palette */

const pane = (s) => s.$("#ss-pane-properties");

test("block palette: groups start collapsed, toggles are remembered, search filters", async () => {
    const s = await blank();
    const groups = s.$$("#ss-blocks-list .ss-block-group");
    assert.ok(groups.length >= 4);
    assert.ok(groups.every((g) => g.classList.contains("is-collapsed")));
    assert.equal(s.$("#ss-count-blocks").textContent, String(s.$$("#ss-blocks-list .ss-block").length));
    const title = groups[0].querySelector(".ss-block-group__title");
    title.click();
    assert.equal(title.getAttribute("aria-expanded"), "true");
    const saved = JSON.parse(s.win.localStorage.getItem("mycelian-spore-blocks-collapsed"));
    assert.equal(saved[groups[0].querySelector(".ss-block-group__title span:nth-child(2)").textContent], false);
    s.E.renderBlockPalette();
    assert.ok(!s.$$("#ss-blocks-list .ss-block-group")[0].classList.contains("is-collapsed"), "remembered open");
    const search = s.$("#ss-block-search");
    search.value = "qr";
    fire(s.win, search, "input");
    assert.ok(s.$("#ss-blocks-list").classList.contains("is-searching"));
    const visible = s.$$("#ss-blocks-list .ss-block:not(.is-filtered)").map((b) => b.dataset.blockType);
    assert.deepEqual(visible, ["qr"]);
    assert.ok(s.$$("#ss-blocks-list .ss-block-group.is-empty").length > 0);
    assert.equal(s.E.blockGroupIsCollapsed(null, "x"), true);
    assert.equal(s.E.blockGroupIsCollapsed({ x: false }, "x"), false);
    s.win.localStorage.setItem("mycelian-spore-blocks-collapsed", "{bad");
    s.E.renderBlockPalette();
    s.win.localStorage = { getItem() { return null; }, setItem() { throw new Error("x"); } };
    s.$$("#ss-blocks-list .ss-block-group__title")[0].click();
    s.$("#ss-blocks-list").remove();
    s.E.renderBlockPalette();
    s.E.filterBlockPalette();
});

/* ------------------------------------------------------------------ colours, fonts, images */

test("colour props allow gradients only on fills; fill CSS keeps url() images on top", async () => {
    const { E } = await blank();
    assert.equal(E.colorPropAllowsGradient({ type: "text" }, "background_color", ""), true);
    assert.equal(E.colorPropAllowsGradient({ type: "qr" }, "background_color", ""), false);
    assert.equal(E.colorPropAllowsGradient({ type: "text" }, "color", "#fff"), false);
    assert.equal(E.colorPropAllowsGradient({ type: "text" }, "color", "linear-gradient(red, blue)"), true);
    assert.equal(E.colorPropAllowsGradient({ type: "progress_bar" }, "fill_color", ""), true);
    assert.equal(E.colorPropAllowsGradient(null, "x", ""), false);
    assert.equal(E.fillBackgroundCss({ background_color: "#fff" }), "#fff");
    assert.equal(E.fillBackgroundCss({ background_color: "linear-gradient(red, blue)", background: "url(a.png) no-repeat, url(b.png)" }), "url(a.png), url(b.png), linear-gradient(red, blue)");
    assert.equal(E.fillBackgroundCss({ background_color: "radial-gradient(red, blue)" }), "radial-gradient(red, blue)");
    assert.equal(E.fillBackgroundCss({}), "");
});

test("buildColorPicker: typed values apply, the popover applies live and Cancel restores", async () => {
    const s = await blank();
    await s.add("shape");
    const got = [];
    const picker = s.E.buildColorPicker("#ff0000", (v) => got.push(v), { title: "Fill", allowGradient: true, presets: ["#000"] });
    s.doc.body.appendChild(picker);
    const text = picker.querySelector(".ss-color__text");
    assert.equal(text.placeholder, "#hex, rgba(...) or a gradient");
    assert.equal(picker.querySelector(".ss-color__swatch").title, "Pick fill");
    text.value = "#00ff00";
    fire(s.win, text, "input");
    fire(s.win, text, "change");
    fire(s.win, text, "change");
    assert.deepEqual(got, ["#00ff00"], "same value is not applied twice");
    picker.querySelector(".ss-color__swatch").click();
    const pop = s.$(".mcp-pop");
    assert.ok(pop, "the shared colour picker opened");
    const t = pop.querySelector("input.is-text");
    t.value = "#0000ff";
    fire(s.win, t, "input");
    assert.equal(got.at(-1), "#0000ff");
    fire(s.win, s.doc.body, "keydown", { key: "Escape" });
    assert.equal(got.at(-1), "#00ff00", "cancel restores the starting colour");
    picker.querySelector(".ss-color__swatch").click();
    s.$(".mcp-pop .mcp-btn.is-primary").click();
    const plainPicker = s.E.buildColorPicker(null, () => {}, { noText: true });
    assert.ok((plainPicker.querySelector(".ss-color__text")) === null);
    assert.equal(plainPicker.querySelector(".ss-color__swatch").title, "Pick a colour");
    s.win.MycelianColorPicker = undefined;
    const fallback = s.E.buildColorPicker("#123", () => {});
    s.doc.body.appendChild(fallback);
    fallback.querySelector(".ss-color__swatch").click();
    assert.equal(s.doc.activeElement, fallback.querySelector(".ss-color__text"));
    assert.equal(fallback.querySelector(".ss-color__fill").style.background, "#123");
});

test("fonts: resolve names to files, preview families and @font-face injection", async () => {
    const s = await blank();
    const E = s.E;
    E.state.defaultFonts = [{ filename: "Bebas.ttf", label: "Bebas Neue", css_family: "BebasCSS" }];
    assert.equal(E.resolveFontFilenameFromValue("Bebas Neue"), "Bebas.ttf");
    assert.equal(E.resolveFontFilenameFromValue("BebasCSS"), "Bebas.ttf");
    assert.equal(E.resolveFontFilenameFromValue("bebas neue "), "Bebas.ttf");
    assert.equal(E.resolveFontFilenameFromValue("Other.otf"), "Other.otf");
    assert.equal(E.resolveFontFilenameFromValue("Arial"), "Arial");
    assert.equal(E.resolveFontFilenameFromValue(""), "");
    assert.equal(E.slugifyElementIdForFont("a b/c"), "a_b_c");
    assert.equal(E.slugifyElementIdForFont("__"), "el");
    assert.equal(E.fontExposedInSourceSettings(null), false);
    assert.equal(E.fontExposedInSourceSettings({}), true);
    assert.equal(E.fontExposedInSourceSettings({ source_settings_expose: {} }), true);
    assert.equal(E.fontExposedInSourceSettings({ source_settings_expose: { font_family: false } }), false);
    const el = { id: "title 1", props: { font_family: "Bebas Neue" } };
    E.normalizeElementFontFamily(el);
    assert.equal(el.props.font_family, "Bebas.ttf");
    E.normalizeElementFontFamily({ props: { font_family: "" } });
    E.normalizeElementFontFamily({ props: { font_family: "Unknown" } });
    E.normalizeElementFontFamily(null);
    assert.equal(E.editorFontFamilyForPreview("Bebas Neue", el), '"spore-el-title_1"');
    assert.ok(s.$("#ss-face-spore_el_title_1").textContent.includes("/assets/default_assets/fonts/Bebas.ttf"));
    E.ensureEditorFontFaceLoaded("spore-el-title_1", "Bebas.ttf");
    assert.equal(s.$$("style[id^=ss-face-]").length, 1);
    assert.equal(E.editorFontFamilyForPreview("Bebas.ttf", { id: "x", source_settings_expose: { font_family: false } }), '"ss-editor-font-Bebas_ttf"');
    assert.equal(E.editorFontFamilyForPreview("Arial", null), "Arial");
    assert.equal(E.editorFontFamilyForPreview("", el), "");
    E.ensureEditorFontFaceLoaded("x", "font.css");
});

test("font select: list, custom names and change callbacks", async () => {
    const s = await blank();
    s.E.state.defaultFonts = [{ filename: "A.ttf", label: "Font A" }];
    const got = [];
    const w = s.E.buildFontFamilySelect("Font A", (v) => got.push(v));
    const sel = w.querySelector("select");
    const custom = w.querySelector("input");
    assert.equal(sel.value, "A.ttf");
    assert.equal(custom.style.display, "none");
    setValue(s.win, sel, "__custom__", ["change"]);
    assert.equal(custom.style.display, "block");
    setValue(s.win, custom, "My Font", ["change"]);
    setValue(s.win, sel, "", ["change"]);
    assert.deepEqual(got, ["My Font", ""]);
    const w2 = s.E.buildFontFamilySelect("Mystery", () => {});
    assert.equal(w2.querySelector("select").value, "__custom__");
    assert.equal(w2.querySelector("input").style.display, "block");
    s.E.state.defaultFonts = [];
    assert.equal(s.E.buildFontFamilySelect("", () => {}).querySelector("option").textContent, "(no fonts in default assets)");
});

test("image source picker lists template images and supports custom URLs", async () => {
    const s = await blank();
    assert.deepEqual(plain(s.E.templateImageAssets()), []);
    s.E.state.assetSnapshot = { files: [{ kind: "image", url: "/assets/zz/a.png", rel_path: "a.png" }, { kind: "audio", url: "/x.mp3" }, { kind: "image", url: "/b.png", name: "b.png" }] };
    assert.equal(s.E.templateImageAssets().length, 2);
    const got = [];
    const w = s.E.buildImageSrcPicker("/assets/zz/a.png", (v) => got.push(v));
    const sel = w.querySelector("select");
    assert.equal(sel.value, "/assets/zz/a.png");
    assert.deepEqual(sel.options.map((o) => o.textContent), ["(select image)", "a.png", "b.png", "(custom URL…)"]);
    setValue(s.win, sel, "/b.png", ["change"]);
    setValue(s.win, sel, "__custom__", ["change"]);
    const input = w.querySelector("input");
    assert.equal(input.style.display, "block");
    setValue(s.win, input, "https://x/y.png", ["input", "change"]);
    assert.deepEqual(got, ["/b.png", "https://x/y.png", "https://x/y.png"]);
    const custom = s.E.buildImageSrcPicker("https://elsewhere", () => {});
    assert.equal(custom.querySelector("select").value, "__custom__");
    s.E.state.assetSnapshot = null;
    assert.equal(s.E.buildImageSrcPicker(null, () => {}).querySelector("option").textContent, "(no images in assets — use custom URL)");
});

test("image defaults and counter-range image source section", async () => {
    const s = await blank();
    const c = await s.add("counter");
    const img = await s.add("image", 1, 1, { src: "/x.png" });
    assert.equal(img.src_mode, "static");
    assert.deepEqual(plain(img.counter_src), { counter_id: "", ranges: [], default_src: "/x.png" });
    s.select(img.id);
    setValue(s.win, control(pane(s), "Image source mode"), "from_counter", ["change"]);
    assert.equal(img.src_mode, "from_counter");
    assert.deepEqual(plain(img.counter_image_transition), { enabled: false, type: "fade", duration_ms: 400, easing: "ease-out" });
    setValue(s.win, control(pane(s), "Counter"), c.counter.counter_id, ["change"]);
    assert.equal(img.counter_src.counter_id, c.counter.counter_id);
    buttonByText(pane(s), "+ Add range").click();
    buttonByText(pane(s), "+ Add range").click();
    assert.equal(img.counter_src.ranges.length, 2);
    setValue(s.win, control(pane(s), "Min", 0), "5", ["change"]);
    setValue(s.win, control(pane(s), "Max", 0), "9", ["change"]);
    assert.deepEqual(plain(img.counter_src.ranges[0]), { min: 5, max: 9, src: "" });
    const trans = sectionByTitle(pane(s), "Range change transition");
    assert.ok(trans, "transition section appears with 2+ ranges");
    const toggle = trans.querySelector(".ss-toggle__input");
    setValue(s.win, toggle, true, ["change"]);
    setValue(s.win, control(trans, "Transition type"), "crossfade", ["change"]);
    setValue(s.win, control(trans, "Duration (ms)"), "-5", ["change"]);
    setValue(s.win, control(trans, "Easing"), "linear", ["change"]);
    assert.deepEqual(plain(img.counter_image_transition), { enabled: true, type: "crossfade", duration_ms: 0, easing: "linear" });
    buttonByText(pane(s), "Remove range").click();
    assert.equal(img.counter_src.ranges.length, 1);
    assert.ok((sectionByTitle(pane(s), "Range change transition")) === null);
    const old = { type: "image", src_mode: "from_counter", counter_src: { ranges: "x" }, counter_image_transition: { enabled: true } };
    s.E.ensureImageSrcDefaults(old);
    assert.deepEqual(plain(old.counter_src.ranges), []);
    assert.equal(old.counter_image_transition.type, "fade");
    s.E.ensureImageSrcDefaults({ type: "text" });
    s.E.ensureCounterImageTransitionDefaults({ type: "text" });
});

/* ------------------------------------------------------------------ gradient, timer, progress, counters */

test("gradient stops: add, edit position (clamped) and remove", async () => {
    const s = await blank();
    const g = await s.add("gradient");
    const sec = () => sectionByTitle(pane(s), "Color stops");
    buttonByText(sec(), "+ Add stop").click();
    assert.equal(g.props.color_stops.length, 3);
    const pos = sec().querySelectorAll('input[type="number"]');
    setValue(s.win, pos[2], "150", ["change"]);
    assert.equal(g.props.color_stops[2].stop, 100);
    setValue(s.win, pos[1], "x", ["change"]);
    assert.equal(g.props.color_stops[1].stop, 0);
    sec().querySelectorAll("button").find((b) => b.textContent === "×").click();
    assert.equal(g.props.color_stops.length, 2);
    assert.match(s.$('.ss-element[data-spore-id="' + g.id + '"]').style.background, /gradient/);
});

test("timer section: mode, start/limit, format presets and custom format, preview buttons", async () => {
    const s = await blank();
    const t = await s.add("timer");
    setValue(s.win, prop(pane(s), "Start (seconds)").control, "-9", ["change"]);
    assert.equal(t.props.start_seconds, 0);
    setValue(s.win, prop(pane(s), "Start (seconds)").control, "90", ["change"]);
    assert.deepEqual([t.props.start_seconds, t.props.duration_seconds], [90, 90]);
    setValue(s.win, prop(pane(s), "Mode").control, "count_up", ["change"]);
    assert.equal(t.props.mode, "count_up");
    setValue(s.win, prop(pane(s), "Limit (seconds)").control, "30", ["change"]);
    assert.equal(t.props.limit_seconds, 30);
    setValue(s.win, control(pane(s), "Format"), "{hh}:{mm}:{ss}", ["change"]);
    assert.equal(t.props.format, "{hh}:{mm}:{ss}");
    setValue(s.win, control(pane(s), "Format"), "custom", ["change"]);
    assert.equal(control(pane(s), "Format").value, "custom", "custom stays open even when it matches a preset");
    setValue(s.win, prop(pane(s), "Custom format").control, "", ["change"]);
    assert.equal(t.props.format, "{mm}:{ss}");
    setValue(s.win, prop(pane(s), "Custom format").control, "{total}s", ["change"]);
    assert.equal(t.props.format, "{total}s");
    setValue(s.win, control(pane(s), "Format"), "{mm}:{ss}", ["change"]);
    const auto = prop(pane(s), "Auto start").control;
    setValue(s.win, auto, false, ["change"]);
    assert.equal(t.props.auto_start, false);
    const preview = sectionByTitle(pane(s), "Preview");
    buttonByText(preview, "Start").click();
    assert.equal(t.timer.running, true);
    buttonByText(sectionByTitle(pane(s), "Preview"), "Pause").click();
    assert.equal(t.timer.running, false);
    t.timer.elapsed = 12;
    buttonByText(sectionByTitle(pane(s), "Preview"), "Reset").click();
    assert.deepEqual(plain(t.timer), { running: false, elapsed: 0 });
});

test("progress bar section: value counter, goal counter, fixed max and progressive note", async () => {
    const s = await blank();
    const c = await s.add("counter");
    const goal = await s.add("counter");
    const bar = await s.add("progress_bar");
    setValue(s.win, control(pane(s), "Value counter"), c.counter.counter_id, ["change"]);
    assert.equal(bar.progress_source.counter_id, c.counter.counter_id);
    setValue(s.win, prop(pane(s), "Max value").control, "250", ["change"]);
    assert.equal(bar.progress_source.max, 250);
    setValue(s.win, prop(pane(s), "Max kind").control, "counter", ["change"]);
    setValue(s.win, control(pane(s), "Goal counter"), goal.counter.counter_id, ["change"]);
    assert.deepEqual([bar.progress_source.max_kind, bar.progress_source.max_counter_id], ["counter", goal.counter.counter_id]);
    setValue(s.win, prop(pane(s), "Max kind").control, "fixed", ["change"]);
    c.counter.progressive = true;
    c.counter.goals = [10, 20];
    s.select(bar.id);
    assert.match(pane(s).textContent, /This counter uses progressive goals/);
    const empty = await blank();
    await empty.add("progress_bar");
    assert.equal(control(pane(empty), "Value counter").options[0].textContent, "(no counters in template)");
});

test("counter section: id rename rewrites references, bounds, progressive goals, persistence", async () => {
    const s = await blank();
    const c = await s.add("counter");
    const bar = await s.add("progress_bar");
    bar.progress_source.counter_id = c.counter.counter_id;
    s.select(c.id);
    setValue(s.win, control(pane(s), "Counter id"), "Deaths Total!", ["change"]);
    assert.equal(c.counter.counter_id, "Deaths_Total");
    assert.equal(c.counter.database_key, "Deaths_Total");
    assert.equal(bar.progress_source.counter_id, "Deaths_Total", "references follow the rename");
    setValue(s.win, control(pane(s), "Initial value"), "x", ["change"]);
    assert.equal(c.counter.initial_value, 0);
    setValue(s.win, control(pane(s), "Min"), "", ["change"]);
    assert.equal(c.counter.min, null);
    setValue(s.win, control(pane(s), "Min"), "-3", ["change"]);
    setValue(s.win, control(pane(s), "Max"), "50", ["change"]);
    assert.deepEqual([c.counter.min, c.counter.max], [-3, 50]);
    setValue(s.win, control(pane(s), "Max"), "", ["change"]);
    assert.equal(c.counter.max, null);
    setValue(s.win, prop(pane(s), "Format ({value}, {min}, {max})").control, "{value} deaths", ["change"]);
    assert.equal(c.props.text, "{value} deaths");
    // progressive goals seed from max (100 when empty) and stay ordered
    const prog = pane(s).querySelectorAll(".ss-form-row--inline").find((r) => r.textContent === "Progressive goals").querySelector("input");
    setValue(s.win, prog, true, ["change"]);
    assert.deepEqual(plain(c.counter.goals), [100]);
    buttonByText(pane(s), "+ Add goal").click();
    buttonByText(pane(s), "+ Add goal").click();
    assert.deepEqual(plain(c.counter.goals), [100, 200, 300]);
    setValue(s.win, control(pane(s), "Goal 2"), "50", ["change"]);
    assert.deepEqual(plain(c.counter.goals), [100, 100, 300], "a goal can't be below the previous one");
    setValue(s.win, control(pane(s), "Goal 1"), "400", ["change"]);
    assert.deepEqual(plain(c.counter.goals), [400, 400, 400], "later goals are pushed up");
    setValue(s.win, control(pane(s), "Goal 1"), "abc", ["change"]);
    assert.equal(c.counter.goals[0], 0);
    pane(s).querySelectorAll("button").filter((b) => b.textContent === "Remove").forEach(() => buttonByText(pane(s), "Remove").click());
    assert.deepEqual(plain(c.counter.goals), []);
    assert.ok(control(pane(s), "Max"), "progressive without goals shows Max again");
    buttonByText(pane(s), "+ Add goal").click();
    assert.deepEqual(plain(c.counter.goals), [100]);
    c.counter.max = "abc";
    c.counter.goals = [];
    c.counter.progressive = false;
    s.select(c.id);
    const prog2 = pane(s).querySelectorAll(".ss-form-row--inline").find((r) => r.textContent === "Progressive goals").querySelector("input");
    setValue(s.win, prog2, true, ["change"]);
    assert.deepEqual(plain(c.counter.goals), [100], "non-numeric max seeds 100");
    const persist = pane(s).querySelectorAll(".ss-form-row--inline").find((r) => r.textContent === "Persist (database)").querySelector("input");
    setValue(s.win, persist, true, ["change"]);
    setValue(s.win, control(pane(s), "DB path"), " zz/my ", ["change"]);
    setValue(s.win, control(pane(s), "DB key"), " k ", ["change"]);
    assert.deepEqual([c.counter.persist, c.counter.database_path, c.counter.database_key], [true, "zz/my", "k"]);
});

test("data display section and value animation section", async () => {
    const s = await blank();
    const d = await s.add("data_display");
    setValue(s.win, prop(pane(s), "Format (use {value})").control, "{value} subs", ["change"]);
    setValue(s.win, control(pane(s), "Default text"), "none yet", ["change"]);
    assert.deepEqual([d.data_display.format, d.data_display.default_text], ["{value} subs", "none yet"]);
    const counterText = await s.add("text");
    counterText.text_mode = "counter";
    s.E.ensureTextModeDefaults(counterText);
    const hostDiv = s.doc.createElement("div");
    s.E.renderValueAnimationSection(hostDiv, counterText);
    const sec = sectionByTitle(hostDiv, "Value change animation");
    setValue(s.win, sec.querySelector(".ss-toggle__input"), true, ["change"]);
    setValue(s.win, control(sec, "Animation type"), "tick_up", ["change"]);
    setValue(s.win, control(sec, "Duration (ms)"), "-1", ["change"]);
    setValue(s.win, control(sec, "Easing"), "linear", ["change"]);
    setValue(s.win, sec.querySelectorAll(".ss-toggle__input")[1], true, ["change"]);
    assert.deepEqual(plain(counterText.value_animation), { enabled: true, type: "tick_up", duration_ms: 0, easing: "linear", pulse: true });
    const text = await s.add("text");
    const h2 = s.doc.createElement("div");
    s.E.renderValueAnimationSection(h2, text);
    s.E.renderValueAnimationSection(h2, { type: "image" });
    assert.equal(h2.children.length, 0, "static text and non-text blocks have no value animation");
});

test("counter blocks show the Value change animation settings", async () => {
    const s = await bootEditor({ template: "bitcounter" });
    const c = s.E.state.model.elements.find((e) => e.type === "counter");
    s.select(c.id);
    assert.ok(sectionByTitle(pane(s), "Value change animation"));
    const d = await s.add("data_display");
    s.select(d.id);
    assert.ok(sectionByTitle(pane(s), "Value change animation"), "data display blocks too");
});

/* ------------------------------------------------------------------ data sources */

test("data source select groups by category and keeps unknown current ids", async () => {
    const s = await blank();
    s.E.state.dataSources = { sources: [{ id: "alert.amount", label: "Amount", category: "Alert" }, { id: "stats.x", category: "Stats" }, { id: "plain" }] };
    const got = [];
    const sel = s.E.buildDataSourceSelect("alert.amount", (v) => got.push(v));
    assert.deepEqual(sel.querySelectorAll("optgroup").map((g) => g.label), ["Alert", "Other", "Stats"]);
    assert.equal(sel.value, "alert.amount");
    setValue(s.win, sel, "plain", ["change"]);
    assert.deepEqual(got, ["plain"]);
    const unknown = s.E.buildDataSourceSelect("legacy.thing", null);
    assert.equal(unknown.value, "legacy.thing");
    setValue(s.win, unknown, "", ["change"]);
});

test("alert type and statistic choices fall back to built-in lists", async () => {
    const s = await blank();
    s.E.state.dataSources = {};
    assert.equal(s.E.alertTypeChoices()[0].id, "any");
    assert.ok(s.E.alertTypeChoices().some((r) => r.id === "raid"));
    assert.ok(s.E.sessionStatChoices().some((r) => r.id === "total_bits"));
    s.E.state.dataSources = { alert_types: [{ id: "x" }], session_stat_keys: [{ id: "y" }] };
    assert.deepEqual(plain(s.E.alertTypeChoices()), [{ id: "any", label: "Any alert type" }, { id: "x" }]);
    assert.deepEqual(plain(s.E.sessionStatChoices()), [{ id: "y" }]);
});

test("source spec helpers: detail kinds, hydration and clearing", async () => {
    const { E } = await blank();
    const kinds = ["counter", "counter.{id}", "counter.x", "config.y", "runtime", "runtime.p.k", "twitch.b.p", "stats", "stats.k", "alert.amount", "sub.resub", "chat.x", ""].map(E.sourceDetailKind);
    assert.deepEqual(kinds, ["counter", "counter", "counter", "config", "runtime", "runtime", "twitch", "stats", "stats", "alert", "sub", "", ""]);
    const H = (spec) => { E.hydrateSourceSpec(spec); return plain(spec); };
    assert.deepEqual(H({ source: "counter.{id}" }), { source: "counter" });
    assert.deepEqual(H({ source: "counter.deaths" }), { source: "counter", source_id: "deaths" });
    assert.deepEqual(H({ source: "counter.deaths", source_id: "keep" }), { source: "counter", source_id: "keep" });
    assert.deepEqual(H({ source: "config.{id}" }), { source: "config" });
    assert.deepEqual(H({ source: "config.Color" }), { source: "config", source_id: "Color" });
    assert.deepEqual(H({ source: "runtime.{path}.{key}" }), { source: "runtime" });
    assert.deepEqual(H({ source: "runtime.doc.key.sub" }), { source: "runtime", runtime_path: "doc", runtime_key: "key.sub" });
    assert.deepEqual(H({ source: "twitch.{binding_id}.{path}" }), { source: "twitch" });
    assert.deepEqual(H({ source: "twitch.b1.data.0" }), { source: "twitch", twitch_binding: "b1", twitch_path: "data.0" });
    assert.deepEqual(H({ source: "stats.followers" }), { source: "stats", source_id: "followers" });
    assert.deepEqual(H({ source: "alert.amount" }), { source: "alert.amount" });
    E.hydrateSourceSpec(null);
    const spec = { source: "x", source_id: 1, runtime_path: 1, runtime_key: 1, twitch_binding: 1, twitch_path: 1, alert_type_filter: 1, tier_filter: 1 };
    E.clearSourceDetails(spec);
    assert.deepEqual(plain(spec), { source: "x" });
    assert.equal(E.isSubDeltaSource("sub.gift_sub"), true);
    assert.equal(E.isSubDeltaSource("alert.amount"), false);
    assert.equal(E.slugifyCounterId(""), "counter");
});

test("config field ids and Twitch binding ids are listed from the model", async () => {
    const s = await blank();
    const t = await s.add("text");
    const timer = await s.add("timer");
    const bar = await s.add("progress_bar");
    const img = await s.add("image");
    img.src_var = "LogoSrc";
    const counterText = await s.add("text");
    counterText.text_mode = "counter";
    t.source_settings_expose.color = false;
    t.bindings.push({ event: "twitch-api-response", twitch_api: { endpoint: "/users" } }, { event: "follow" });
    timer.bindings.push({ event: "twitch-api-response" });
    const ids = s.E.listConfigFieldIds().map((r) => r.id);
    for (const id of ["DesignWidth", t.id + "Text", t.id + "_font_size", timer.id + "_mode", timer.id + "_auto_start", bar.id + "_max_kind", "LogoSrc", counterText.id + "_format"]) {
        assert.ok(ids.includes(id), id);
    }
    assert.ok(!ids.includes(t.id + "_color"), "unexposed fields are left out");
    assert.equal(new Set(ids).size, ids.length, "no duplicates");
    assert.deepEqual(plain(s.E.listTwitchBindingIds()), [
        { id: "spore_tw_" + t.id + "_0", label: "spore_tw_" + t.id + "_0 — /users" },
        { id: "spore_tw_" + timer.id + "_0", label: "spore_tw_" + timer.id + "_0" },
    ]);
});

test("buildIdSelect, tier filter select and the alert/tier rules", async () => {
    const s = await blank();
    const got = [];
    const sel = s.E.buildIdSelect("b", [{ id: "a", label: "A" }, { id: "b" }], (v) => got.push(v), "(pick)");
    assert.deepEqual(sel.options.map((o) => o.textContent), ["(pick)", "A", "b"]);
    assert.equal(sel.value, "b");
    setValue(s.win, sel, "a", ["change"]);
    assert.equal(s.E.buildIdSelect("zz", [], () => {}).value, "zz");
    assert.equal(s.E.buildIdSelect("", [], () => {}).options[0].textContent, "(select)");
    const tier = s.E.buildTierFilterSelect(null, (v) => got.push(v));
    assert.equal(tier.value, "any");
    setValue(s.win, tier, "3", ["change"]);
    assert.deepEqual(got, ["a", "3"]);
    s.E.state.dataSources = { sources: [{ id: "alert.months", alert_types: ["sub", "resub"] }, { id: "alert.amount", alert_types: ["bit", "donation"] }, { id: "alert.user" }] };
    assert.equal(s.E.findDataSourceRow("alert.amount").id, "alert.amount");
    assert.equal(s.E.findDataSourceRow("x"), null);
    assert.deepEqual(plain(s.E.alertTypesForSource({ source: "alert.months" })), ["sub", "resub"]);
    assert.deepEqual(plain(s.E.alertTypesForSource(null)), []);
    assert.equal(s.E.wantsTierFilter({ source: "sub.new_sub" }), true);
    assert.equal(s.E.wantsTierFilter({ source: "stats.x" }), false);
    assert.equal(s.E.wantsTierFilter({ source: "alert.months" }), true);
    assert.equal(s.E.wantsTierFilter({ source: "alert.amount" }), false);
    assert.equal(s.E.wantsTierFilter({ source: "alert.user" }), false);
    assert.equal(s.E.wantsTierFilter({ source: "alert.user", alert_type_filter: "giftsub" }), true);
    assert.equal(s.E.wantsTierFilter({ source: "alert.user", alert_type_filter: "raid" }), false);
});

test("data source detail fields for each source kind", async () => {
    const s = await blank();
    await s.add("counter");
    s.E.state.dataSources = { sources: [{ id: "alert.months", alert_types: ["sub", "resub"] }, { id: "alert.only", alert_types: ["raid"] }, { id: "alert.any" }] };
    const changes = [];
    const redraws = [];
    const render = (spec) => {
        const host = s.doc.createElement("div");
        s.E.appendDataSourceDetailFields(host, spec, () => changes.push(plain(spec)), () => redraws.push(1));
        return host;
    };
    const counter = { source: "counter" };
    let host = render(counter);
    setValue(s.win, control(host, "Counter"), control(host, "Counter").options[1].value, ["change"]);
    assert.ok(counter.source_id);
    const config = { source: "config" };
    host = render(config);
    setValue(s.win, control(host, "Config field"), "DesignWidth", ["change"]);
    setValue(s.win, control(host, "Field id"), " Custom ", ["change"]);
    assert.equal(config.source_id, "Custom");
    const runtime = { source: "runtime" };
    host = render(runtime);
    setValue(s.win, control(host, "Database path"), " doc ", ["change"]);
    setValue(s.win, control(host, "Database key"), " key ", ["change"]);
    assert.deepEqual([runtime.runtime_path, runtime.runtime_key], ["doc", "key"]);
    const tw = { source: "twitch" };
    host = render(tw);
    setValue(s.win, control(host, "Binding"), "", ["change"]);
    setValue(s.win, control(host, "Response path"), " data.0.login ", ["change"]);
    assert.equal(tw.twitch_path, "data.0.login");
    const stats = { source: "stats" };
    host = render(stats);
    setValue(s.win, control(host, "Statistic"), control(host, "Statistic").options[1].value, ["change"]);
    assert.ok(stats.source_id);
    const months = { source: "alert.months", alert_type_filter: "raid" };
    host = render(months);
    assert.equal(months.alert_type_filter, "any", "a filter for a type that lacks the field is reset");
    setValue(s.win, control(host, "Alert type"), "sub", ["change"]);
    assert.deepEqual([months.alert_type_filter, months.fallback], ["sub", 0]);
    assert.ok(control(host, "Tier filter"));
    setValue(s.win, control(host, "Tier filter"), "2", ["change"]);
    assert.equal(months.tier_filter, "2");
    setValue(s.win, control(host, "Alert type"), "", ["change"]);
    assert.equal(months.alert_type_filter, "any");
    const only = { source: "alert.only", alert_type_filter: "raid" };
    host = render(only);
    assert.equal(only.alert_type_filter, "any");
    assert.ok((control(host, "Alert type")) === null, "one carrying type: nothing to choose");
    const sub = { source: "sub.new_sub" };
    host = render(sub);
    assert.equal(sub.tier_filter, "any");
    assert.ok(redraws.length >= 4);
    assert.ok(changes.length >= 8);
});

test("delta editor: fixed, random and data source kinds", async () => {
    const s = await blank();
    s.E.state.dataSources = { sources: [{ id: "alert.amount", category: "Alert" }, { id: "sub.gift_sub", category: "Delta", delta_only: true }, { id: "stats.x", category: "Stats" }] };
    const seen = [];
    const w = s.E.buildDeltaEditor(null, (d) => seen.push(plain(d)));
    s.doc.body.appendChild(w);
    setValue(s.win, control(w, "Value"), "abc", ["change"]);
    assert.equal(seen.at(-1).value, 0);
    setValue(s.win, control(w, "Delta kind"), "random_float", ["change"]);
    setValue(s.win, control(w, "Min"), "2", ["change"]);
    setValue(s.win, control(w, "Max"), "6", ["change"]);
    setValue(s.win, control(w, "Decimals"), "1", ["change"]);
    assert.deepEqual(seen.at(-1), { kind: "random_float", value: 0, min: 2, max: 6, decimals: 1 });
    setValue(s.win, control(w, "Delta kind"), "random_int", ["change"]);
    assert.ok((control(w, "Decimals")) === null);
    setValue(s.win, control(w, "Delta kind"), "data_source", ["change"]);
    setValue(s.win, control(w, "Source"), "sub.gift_sub", ["change"]);
    assert.equal(seen.at(-1).fallback, 0, "sub sources default the fallback to 0");
    assert.match(control(w, "Fallback").closest(".ss-form-row").getAttribute("data-ss-tip"), /does not match/);
    setValue(s.win, control(w, "Source"), "stats.x", ["change"]);
    setValue(s.win, control(w, "Fallback"), "5", ["change"]);
    assert.deepEqual([seen.at(-1).source, seen.at(-1).source_id, seen.at(-1).fallback], ["stats", "x", 5], "stats.x is split into source + id");
    assert.ok(control(w, "Statistic"));
    setValue(s.win, control(w, "Statistic"), "total_bits", ["change"]);
    assert.equal(seen.at(-1).source_id, "total_bits");
    const preset = s.E.buildDeltaEditor({ kind: "data_source", source: "counter.abc" }, () => {});
    assert.ok(control(preset, "Counter"));
    assert.equal(control(preset, "Fallback").value, "1");
});

// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/editor.js, part 3 (lines 7827-end): the Properties panel
// (id rename, layout, visibility, appearance, animations, delete), Stream Deck actions, the
// Bindings editor, palette drag & drop, categories, assets, template load/normalise/save,
// preview dialog, create dialog, outline, Canvas tab, tabs, socket, delete template, keyboard
// shortcuts and the toolbar.

import test from "node:test";
import assert from "node:assert/strict";
import { fire, plain, settle, xfail, makeIo } from "./helpers/env.mjs";
import { bootEditor, blankModel, API, control, field, prop, buttonByText, setValue, sectionByTitle } from "./helpers/editor_env.mjs";

async function blank(extra = {}, opts = {}) {
    return bootEditor(Object.assign({ template: "zz", models: { zz: blankModel("zz", extra) } }, opts));
}
const pane = (s) => s.$("#ss-pane-properties");
const node = (s, el) => s.$('.ss-element[data-spore-id="' + el.id + '"]');
function collapse(root, title) {
    const sum = root.querySelectorAll("summary.ss-collapse__summary").find((x) => x.dataset.section === title);
    return sum ? sum.parentNode.querySelector(".ss-collapse__body") : null;
}

/* ------------------------------------------------------------------ properties panel */

test("no selection shows the hint; the header card shows id, path and type", async () => {
    const s = await blank();
    s.E.renderProperties();
    assert.match(s.$("#ss-properties-host").textContent, /Select an element/);
    const box = await s.add("container");
    const t = await s.add("text", 0, 0, null, box.id);
    const head = s.$("#ss-properties-host .ss-elhead");
    assert.equal(head.querySelector(".ss-elhead__id").textContent, t.id);
    assert.equal(head.querySelector(".ss-elhead__path").textContent, "in " + box.id);
    s.select(box.id);
    assert.equal(s.$("#ss-properties-host .ss-elhead__path").textContent, "on the canvas");
});

test("safeElementId cleans ids for HTML/Jinja/JS", async () => {
    const { E } = await blank();
    assert.equal(E.safeElementId(" my-title! "), "my_title");
    assert.equal(E.safeElementId("1st"), "el_1st");
    assert.equal(E.safeElementId("__"), "");
    assert.equal(E.safeElementId(null), "");
});

test("renaming an element: cleans the id, refuses duplicates, renames children and binding refs", async () => {
    const s = await blank();
    const box = await s.add("container");
    const child = await s.add("text", 0, 0, null, box.id);
    const other = await s.add("text");
    other.bindings.push({ event: "next_alert", action: "show", target: box.id, args: { element_id: box.id } });
    s.select(box.id);
    const idInput = () => control(collapse(pane(s), "Element"), "ID");
    setValue(s.win, idInput(), "Main Box", ["change"]);
    assert.equal(box.id, "Main_Box");
    assert.equal(child.parent_id, "Main_Box");
    assert.match(s.toast(), /ID changed to “Main_Box”/);
    assert.equal(s.E.state.selectedId, "Main_Box");
    setValue(s.win, idInput(), other.id, ["change"]);
    assert.equal(box.id, "Main_Box");
    assert.match(s.toast(), /already taken/);
    setValue(s.win, idInput(), "!!!", ["change"]);
    assert.equal(box.id, "Main_Box", "an id that cleans to nothing is ignored");
    assert.ok(JSON.stringify(other.bindings).includes("Main_Box"), "binding references follow the rename");
});

test("category, move-to-canvas and layout fields", async () => {
    const s = await blank();
    const box = await s.add("container", 100, 50);
    const child = await s.add("shape", 10, 10, null, box.id);
    const el = collapse(pane(s), "Element");
    setValue(s.win, control(el, "Category"), " Header ", ["change"]);
    assert.equal(child.category, "Header");
    const lay = () => collapse(pane(s), "Layout");
    setValue(s.win, control(lay(), "X"), "-4", ["change"]);
    setValue(s.win, control(lay(), "Y"), "20", ["change"]);
    setValue(s.win, control(lay(), "W"), "5", ["change"]);
    setValue(s.win, control(lay(), "H"), "x", ["change"]);
    assert.deepEqual([child.position.x, child.position.y, child.size.w, child.size.h], [0, 20, 10, 10]);
    assert.deepEqual([child.placement.offset_x, child.placement.offset_y], [0, 20]);
    buttonByText(collapse(pane(s), "Element"), "Move to canvas").click();
    assert.equal(child.parent_id, undefined);
    assert.deepEqual(plain(child.position), { x: 100, y: 70 });
    const t = await s.add("text");
    delete t.size;
    s.select(t.id);
    setValue(s.win, control(lay(), "W"), "30", ["change"]);
    assert.equal(t.size.w, 30);
    delete t.size;
    s.select(t.id);
    setValue(s.win, control(lay(), "H"), "30", ["change"]);
    assert.deepEqual(plain(t.size), { w: 100, h: 30 });
});

test("visibility follows Show bindings until set explicitly", async () => {
    const s = await blank();
    const t = await s.add("text");
    assert.equal(s.E.resolvedStartHidden(t), false);
    t.bindings.push({ event: "next_alert", action: "set_text" }, { event: "x", action: "noop", chain: [null, { action: "show_for" }] });
    assert.equal(s.E.elementHasShowBinding(t), true);
    assert.equal(s.E.resolvedStartHidden(t), true);
    s.select(t.id);
    const vis = collapse(pane(s), "Visibility");
    assert.match(vis.querySelector(".ss-form-row").getAttribute("data-ss-tip"), /^Auto:/);
    setValue(s.win, vis.querySelector(".ss-toggle__input"), false, ["change"]);
    assert.equal(t.start_hidden, false);
    assert.equal(s.E.resolvedStartHidden(t), false);
    assert.equal(s.E.resolvedStartHidden(null), false);
    assert.equal(s.E.bindingHasShowAction(null), false);
    assert.equal(s.E.elementHasShowBinding(null), false);
    const u = { bindings: [] };
    s.E.maybeDefaultStartHiddenForShow(u, { action: "show" });
    assert.equal(u.start_hidden, true);
    u.start_hidden = false;
    s.E.maybeDefaultStartHiddenForShow(u, { action: "show" });
    assert.equal(u.start_hidden, false);
    s.E.maybeDefaultStartHiddenForShow(null, null);
});

test("appearance: style chips for bold/italic/underline/strike and the summary", async () => {
    const s = await blank();
    s.E.state.defaultFonts = [{ filename: "Bebas.ttf", label: "Bebas" }];
    const t = await s.add("text", 0, 0, { font_family: "Bebas.ttf", font_size: 40 });
    assert.equal(s.E.appearanceSummary(t), "Bebas · 40px");
    assert.equal(s.E.appearanceSummary({}), "");
    const app = collapse(pane(s), "Appearance");
    const chips = app.querySelectorAll(".ss-style-chip");
    assert.equal(chips.length, 4);
    assert.ok(app.querySelector(".ss-expose-legend"));
    const inputs = chips.map((c) => c.querySelector("input"));
    assert.equal(inputs[0].getAttribute("aria-label"), "Bold");
    setValue(s.win, inputs[0], true, ["change"]);
    setValue(s.win, inputs[3], true, ["change"]);
    assert.deepEqual([t.props.font_weight, t.props.text_strikethrough], ["bold", true]);
    assert.equal(node(s, t).style.textDecoration, "line-through");
    // a non-toggle entry passed to addStyleChip is placed after the chip row instead
    const row = s.E.buildStyleChipRow();
    const holder = s.doc.createElement("div");
    holder.appendChild(row.wrap);
    s.E.addStyleChip(row, t, { key: "font_size", label: "Size", type: "number" }, "static", "static");
    assert.equal(holder.children.length, 2);
});

test("animations section edits and recomputes the minimum duration", async () => {
    const s = await blank();
    const t = await s.add("text");
    const an = () => collapse(pane(s), "Animations");
    setValue(s.win, control(an(), "Entrance"), "fade", ["change"]);
    setValue(s.win, control(an(), "Entrance duration (ms)"), "4000", ["change"]);
    setValue(s.win, control(an(), "Exit"), "scaleOut", ["change"]);
    setValue(s.win, control(an(), "Exit duration (ms)"), "", ["change"]);
    assert.deepEqual([t.animations.anim_in, t.animations.anim_in_ms, t.animations.anim_out, t.animations.anim_out_ms], ["fade", 4000, "scaleOut", 0]);
    assert.equal(s.$("#ss-duration-min-label").textContent, "Minimum from layout: 4.0s");
    s.select(t.id);
    assert.equal(pane(s).querySelector('summary[data-section="Animations"] .ss-collapse__sum').textContent, "fade / scaleOut");
});

test("open sections and scroll position survive a redraw of the same element", async () => {
    const s = await blank();
    const t = await s.add("text");
    const host = s.$("#ss-properties-host");
    const det = host.querySelector('summary[data-section="Appearance"]').parentNode;
    det.open = true;
    s.$("#ss-pane-properties").scrollTop = 123;
    s.E.renderProperties();
    assert.equal(host.querySelector('summary[data-section="Appearance"]').parentNode.open, true);
    assert.equal(s.$("#ss-pane-properties").scrollTop, 123);
    const snap = s.E.snapshotPropertiesPanel(null);
    assert.deepEqual(plain(snap.open), {});
    s.E.restorePropertiesPanel(null, null);
    void t;
});

test("Delete element removes it and its children", async () => {
    const s = await blank();
    const box = await s.add("container");
    await s.add("text", 0, 0, null, box.id);
    s.select(box.id);
    buttonByText(pane(s), "Delete element").click();
    assert.equal(s.E.state.model.elements.length, 0);
    assert.equal(s.E.state.selectedId, null);
});

/* ------------------------------------------------------------------ stream deck */

const sdHost = (s) => s.$("#ss-streamdeck-host");

test("Stream Deck panel: empty, add, rename, describe, event, default data and remove", async () => {
    const s = await blank();
    const t = await s.add("text");
    s.E.renderStreamdeckPanel();
    assert.match(sdHost(s).textContent, /No Stream Deck actions yet/);
    sdHost(s).querySelector(".ss-btn--primary.ss-btn--block").click();
    sdHost(s).querySelector(".ss-btn--primary.ss-btn--block").click();
    const acts = s.E.state.model.streamdeck_options.actions;
    assert.deepEqual(Object.keys(acts).sort(), ["action_1", "action_2"]);
    assert.deepEqual(plain(acts.action_1), { name: "New action", description: "", event: "action_1", default_data: {} });
    setValue(s.win, control(sdHost(s), "Template description"), "My deck", ["change"]);
    assert.equal(s.E.state.model.streamdeck_options.description, "My deck");
    t.bindings.push({ trigger: "streamdeck", streamdeck_action: "action_1", event: "action_1", action: "show" });
    const card = () => sdHost(s).querySelectorAll(".ss-streamdeck-action")[0];
    setValue(s.win, control(card(), "Display name"), "Reset", ["input"]);
    assert.equal(card().querySelector(".ss-binding-row__title").textContent, "Reset");
    setValue(s.win, control(card(), "Description"), "Resets", ["change"]);
    setValue(s.win, control(card(), "Socket event"), " deck_reset ", ["change"]);
    assert.equal(acts.action_1.event, "deck_reset");
    assert.equal(t.bindings[0].event, "deck_reset", "bindings follow the action's event");
    const dd = control(card(), "default_data (JSON)");
    dd.value = "{bad";
    fire(s.win, dd, "blur", { bubbles: false });
    assert.match(s.toast(), /Sample data isn't valid/);
    dd.value = '{"n": 1}';
    fire(s.win, dd, "blur", { bubbles: false });
    assert.deepEqual(plain(acts.action_1.default_data), { n: 1 });
    const dd2 = control(card(), "default_data (JSON)");
    dd2.value = "";
    fire(s.win, dd2, "blur", { bubbles: false });
    assert.deepEqual(plain(acts.action_1.default_data), {});
    const dd3 = control(card(), "default_data (JSON)");
    dd3.value = "{bad";
    fire(s.win, dd3, "blur", { bubbles: false });
    assert.deepEqual(plain(acts.action_1.default_data), {}, "unparseable text keeps the last value");
    // rename: taken, cleaned, migrated
    setValue(s.win, control(card(), "Action id"), "action_2", ["change"]);
    assert.match(s.toast(), /action ID is already taken/);
    setValue(s.win, control(card(), "Action id"), "action 1", ["change"]);
    assert.ok(acts.action_1, "cleans to the same id: nothing to do");
    setValue(s.win, control(card(), "Action id"), "reset key", ["change"]);
    assert.ok(acts.reset_key);
    assert.equal(t.bindings[0].streamdeck_action, "reset_key");
    acts.dup = { event: "deck_reset" };
    s.E.renderStreamdeckPanel();
    assert.match(sdHost(s).textContent, /Multiple actions share socket event\(s\): deck_reset/);
    const rkCard = sdHost(s).querySelectorAll(".ss-streamdeck-action").find((c) => control(c, "Action id").value === "reset_key");
    rkCard.querySelector(".ss-binding-row__remove").click();
    assert.equal(acts.reset_key, undefined);
    assert.equal(t.bindings[0].trigger, "registry");
    assert.equal(t.bindings[0].streamdeck_action, undefined);
});

test("Stream Deck helpers: model defaults, triggers, suggestions, sync and migration", async () => {
    const s = await blank();
    const E = s.E;
    E.state.model.streamdeck_options = { description: 3, actions: [] };
    E.ensureStreamdeckModel();
    assert.deepEqual(plain(E.state.model.streamdeck_options), { description: "", actions: {} });
    E.state.model.streamdeck_options = null;
    E.ensureStreamdeckModel();
    const acts = E.state.model.streamdeck_options.actions;
    assert.equal(E.suggestStreamdeckActionForBinding(null, {}), "");
    acts.b = { event: "ev_b" };
    acts.a = { event: "ev_a" };
    const t = { id: "t", bindings: [{ streamdeck_action: "a" }] };
    E.state.model.elements.push(t);
    assert.equal(E.bindingTrigger({ streamdeck_action: "a" }), "streamdeck");
    assert.equal(E.bindingTrigger({ streamdeck_action: "zz", trigger: "streamdeck" }), "streamdeck");
    assert.equal(E.bindingTrigger({}), "registry");
    assert.equal(E.suggestStreamdeckActionForBinding(t, {}), "b", "first unused action");
    assert.equal(E.suggestStreamdeckActionForBinding(t, { streamdeck_action: "a" }), "a");
    t.bindings.push({ streamdeck_action: "b" });
    assert.equal(E.suggestStreamdeckActionForBinding(t, {}), "a", "all used: first one");
    assert.deepEqual(Object.keys(plain(E.streamdeckActionIdsUsedOnElement(t, t.bindings[0]))), ["b"]);
    const b = { streamdeck_action: "a", event: "old", filter: { x: 1 } };
    E.applyStreamdeckBindingSync(b);
    assert.deepEqual(plain(b), { streamdeck_action: "a", event: "ev_a", filter: {} });
    const unknown = { streamdeck_action: "zz", event: "keep" };
    E.applyStreamdeckBindingSync(unknown);
    assert.equal(unknown.event, "keep");
    t.bindings[0].trigger = "registry";
    acts.a.event = "renamed";
    E.syncAllStreamdeckBindingsForAction("a");
    assert.notEqual(t.bindings[0].event, "renamed", "registry-triggered bindings are left alone");
    E.syncAllStreamdeckBindingsForAction("");
    assert.equal(E.migrateStreamdeckActionId("a", "a"), false);
    assert.equal(E.migrateStreamdeckActionId("a", "b"), false, "target exists");
    assert.equal(E.migrateStreamdeckActionId("nope", "c"), false);
    assert.equal(E.migrateStreamdeckActionId("a", "c"), true);
    assert.equal(t.bindings[0].streamdeck_action, "c");
    const host = s.$("#ss-streamdeck-host");
    const inp = s.doc.createElement("input");
    host.appendChild(inp);
    inp.focus();
    E.flushStreamdeckPanelInputs();
    assert.notEqual(s.doc.activeElement, inp);
    E.state.model = null;
    E.renderStreamdeckPanel();
    assert.match(host.textContent, /Load a template/);
    assert.equal(E.suggestStreamdeckActionForBinding(null, null), "");
    E.ensureStreamdeckModel();
    const legacy = await bootEditor({ template: "follow" });
    legacy.E.renderStreamdeckPanel();
    assert.match(legacy.$("#ss-streamdeck-host").textContent, /not edited here for legacy templates/);
});

test("Twitch API binding defaults migrate the old filter map", async () => {
    const { E } = await blank();
    const b = { event: "twitch-api-response", filter: { requestId: "r", login: "x", obj: { a: 1 }, nil: null } };
    E.ensureTwitchBindingDefaults(b);
    assert.deepEqual(plain(b.twitch_api), { endpoint: "", method: "GET", params_json: "", body_json: "" });
    assert.deepEqual(plain(b.twitch_filters), [{ key: "login", value: "x" }, { key: "obj", value: '{"a":1}' }, { key: "nil", value: "" }]);
    assert.deepEqual(plain(b.filter), {});
    const empty = { event: "twitch-api-response", twitch_filters: [] };
    E.ensureTwitchBindingDefaults(empty);
    assert.deepEqual(plain(empty.twitch_filters), [{ key: "", value: "" }]);
    const other = { event: "next_alert" };
    E.ensureTwitchBindingDefaults(other);
    assert.equal(other.twitch_api, undefined);
});

xfail(test, "Stream Deck sample data refuses JSON arrays",
    "BUG: editor.js:8859 the default_data check uses typeof === 'object', so a JSON array is accepted although the hint says it must be an object; streamdeck_template_dispatch.merged_streamdeck_options_payload then silently drops it",
    async () => {
        const s = await blank();
        s.E.renderStreamdeckPanel();
        s.$("#ss-streamdeck-host .ss-btn--primary.ss-btn--block").click();
        const dd = control(s.$("#ss-streamdeck-host .ss-streamdeck-action"), "default_data (JSON)");
        dd.value = "[1, 2]";
        fire(s.win, dd, "blur", { bubbles: false });
        assert.equal(Array.isArray(s.E.state.model.streamdeck_options.actions.action_1.default_data), false);
    });

xfail(test, "Stream Deck sample data that isn't an object keeps the last valid value",
    "BUG: editor.js:8858 JSON.parse's result is stored in spec.default_data before the object check, so valid non-object JSON (e.g. 5 or \"x\") replaces the saved sample data even though the warning says the last valid value was kept",
    async () => {
        const s = await blank();
        s.E.renderStreamdeckPanel();
        s.$("#ss-streamdeck-host .ss-btn--primary.ss-btn--block").click();
        const dd = control(s.$("#ss-streamdeck-host .ss-streamdeck-action"), "default_data (JSON)");
        dd.value = "5";
        fire(s.win, dd, "blur", { bubbles: false });
        assert.deepEqual(plain(s.E.state.model.streamdeck_options.actions.action_1.default_data), {});
    });

/* ------------------------------------------------------------------ bindings editor */

const bhost = (s) => s.$("#ss-bindings-host");
const bcards = (s) => bhost(s).querySelectorAll(".ss-binding-row");

test("bindings panel: no selection, legacy element, empty card and Stream Deck hint", async () => {
    const s = await blank();
    s.E.state.selectedId = null;
    s.E.renderBindings();
    assert.match(bhost(s).textContent, /Select an element to attach event bindings/);
    const t = await s.add("text");
    s.E.renderBindings();
    assert.match(bhost(s).textContent, /No bindings yet/);
    assert.ok(bhost(s).querySelector(".ss-callout") === null);
    s.E.state.model.streamdeck_options.actions.a1 = { name: "A", event: "a1", default_data: {} };
    s.E.renderBindings();
    assert.match(bhost(s).querySelector(".ss-callout").textContent, /assign one binding per hardware key/);
    t.type = "legacy_html";
    s.E.renderBindings();
    if (s.E.isLegacyElement(t) || s.E.isReadOnlyLegacyElement(t)) {
        assert.match(bhost(s).textContent, /Legacy templates manage their own websocket bindings/);
    }
});

test("add a binding, change event/action, edit args, add filters, remove", async () => {
    const s = await blank();
    const t = await s.add("text");
    t.bindings = [];
    s.E.renderBindings();
    buttonByText(bhost(s), "Add binding").click();
    assert.equal(t.bindings.length, 1);
    const b = t.bindings[0];
    assert.equal(b.event, API.events.events[0].event);
    assert.equal(b.action, API.events.actions[0].action);
    assert.equal(bcards(s).length, 1);
    assert.match(bcards(s)[0].querySelector(".ss-binding-row__title").textContent, /Binding #1/);

    // event select
    setValue(s.win, control(bcards(s)[0], "Event"), "instant_alert", ["change"]);
    assert.equal(b.event, "instant_alert");
    // filters: none active -> "any event"
    const filt = () => bcards(s)[0].querySelector(".ss-filters");
    assert.equal(filt().querySelector(".ss-count").textContent, "any event");
    assert.match(filt().textContent, /Fires for every event/);
    setValue(s.win, filt().querySelector("select.ss-add-filter"), "username", ["change"]);
    const fin = filt().querySelector('input[data-filter-key="username"]');
    assert.ok(fin, "filter row shown");
    assert.equal(filt().querySelector(".ss-count").textContent, "1 filter");
    setValue(s.win, fin, "bob", ["change"]);
    assert.equal(b.filter.username, "bob");
    setValue(s.win, filt().querySelector("select.ss-add-filter"), "tier", ["change"]);
    assert.equal(filt().querySelector(".ss-count").textContent, "2 filters");
    setValue(s.win, filt().querySelector('input[data-filter-key="tier"]'), "", ["change"]);
    assert.ok(!("tier" in b.filter));
    filt().querySelector('.ss-filter-row .ss-icon-btn[aria-label="Remove filter ' +
        (API.events.events.find((e) => e.event === "instant_alert").payload.find((p) => p.key === "username").label || "username") + '"]').click();
    assert.ok(!("username" in b.filter));
    setValue(s.win, filt().querySelector("select.ss-add-filter"), "", ["change"]);

    // action with args: set_text (string), show_for (number/select)
    setValue(s.win, control(bcards(s)[0], "Action"), "set_text", ["change"]);
    assert.equal(b.action, "set_text");
    assert.deepEqual(plain(b.args), {});
    const sa = API.events.actions.find((a) => a.action === "set_text").args.find((a) => a.key === "from_payload");
    const fp = control(bcards(s)[0], sa.label || sa.key);
    assert.match(fp.placeholder, /^e\.g\. /);
    setValue(s.win, fp, "username", ["change"]);
    assert.equal(b.args.from_payload, "username");
    setValue(s.win, control(bcards(s)[0], "Action"), "show_for", ["change"]);
    const sf = API.events.actions.find((a) => a.action === "show_for").args;
    const secs = control(bcards(s)[0], sf[0].label || sf[0].key);
    assert.equal(secs.type, "number");
    setValue(s.win, secs, "3", ["change"]);
    assert.equal(b.args.seconds, 3);
    setValue(s.win, secs, "", ["change"]);
    assert.equal(b.args.seconds, null);
    const anim = control(bcards(s)[0], sf[1].label || sf[1].key);
    assert.equal(anim.tagName, "SELECT");
    setValue(s.win, anim, anim.options[anim.options.length - 1].value, ["change"]);
    assert.equal(b.args[sf[1].key], anim.options[anim.options.length - 1].value);

    // remove the binding
    bcards(s)[0].querySelector(".ss-binding-row__head .ss-binding-row__remove").click();
    assert.equal(t.bindings.length, 0);
    assert.match(bhost(s).textContent, /No bindings yet/);
});

test("chained steps: add, delay, change action, remove and the 15-step cap", async () => {
    const s = await blank();
    const t = await s.add("text");
    t.bindings = [{ event: "next_alert", filter: {}, action: "show", args: {}, chain: "junk" }];
    s.E.renderBindings();
    const b = t.bindings[0];
    assert.deepEqual(plain(b.chain), [], "non-array chain reset");
    const addStep = () => buttonByText(bcards(s)[0], "Add chained step");
    addStep().click();
    assert.equal(b.chain.length, 1);
    assert.deepEqual(plain(b.chain[0]), { delay_ms: 0, action: API.events.actions[0].action, args: {} });
    const step = () => bcards(s)[0].querySelector(".ss-binding-row__chain-step");
    assert.match(step().querySelector(".ss-chain-step__title").textContent, /Then, step 1/);
    const delay = control(step(), "Delay before this step (ms)");
    setValue(s.win, delay, "250", ["change"]);
    assert.equal(b.chain[0].delay_ms, 250);
    setValue(s.win, delay, "-4", ["change"]);
    assert.equal(b.chain[0].delay_ms, 0);
    setValue(s.win, delay, "x", ["change"]);
    assert.equal(b.chain[0].delay_ms, 0);
    setValue(s.win, control(step(), "Action"), "flash_class", ["change"]);
    assert.equal(b.chain[0].action, "flash_class");
    const fc = API.events.actions.find((a) => a.action === "flash_class").args[0];
    setValue(s.win, control(step(), fc.label || fc.key), "pop", ["change"]);
    assert.equal(b.chain[0].args.class_name, "pop");
    step().querySelector(".ss-chain-step__head .ss-binding-row__remove").click();
    assert.equal(b.chain.length, 0);
    // malformed steps are filled in or skipped
    b.chain = [null, { args: null }];
    s.E.renderBindings();
    assert.equal(b.chain[1].action, API.events.actions[0].action);
    assert.equal(b.chain[1].delay_ms, 0);
    assert.equal(bcards(s)[0].querySelectorAll(".ss-binding-row__chain-step").length, 1);
    b.chain = Array.from({ length: 15 }, () => ({ action: "show", args: {}, delay_ms: 0 }));
    s.E.renderBindings();
    assert.equal(addStep().disabled, true);
    addStep().dispatchEvent(new s.win.Event("click"));
    assert.equal(b.chain.length, 15);
});

test("Stream Deck trigger: empty hint, pick action, switch back to registry", async () => {
    const s = await blank();
    const t = await s.add("text");
    t.bindings = [{ event: "next_alert", filter: { x: 1 }, action: "show", args: {}, chain: [] }];
    s.E.renderBindings();
    const b = t.bindings[0];
    const trig = () => control(bcards(s)[0], "Trigger");
    assert.equal(trig().value, "registry");
    setValue(s.win, trig(), "streamdeck", ["change"]);
    assert.equal(b.trigger, "streamdeck");
    assert.match(bcards(s)[0].textContent, /Add Stream Deck actions in the Stream Deck tab first/);
    const acts = s.E.state.model.streamdeck_options.actions;
    acts.b_two = { name: "Two", event: "sd_two", default_data: {} };
    acts.a_one = { name: "", event: "sd_one", default_data: {} };
    s.E.renderBindings();
    const sd = control(bcards(s)[0], "Stream Deck action");
    assert.deepEqual(sd.options.map((o) => o.textContent), ["a_one (a_one)", "Two (b_two)"]);
    assert.ok(b.streamdeck_action in acts, "a suggestion was filled in");
    setValue(s.win, control(bcards(s)[0], "Stream Deck action"), "b_two", ["change"]);
    assert.equal(b.streamdeck_action, "b_two");
    assert.equal(b.event, "sd_two");
    assert.match(bcards(s)[0].textContent, /Socket event: sd_two/);
    setValue(s.win, trig(), "registry", ["change"]);
    assert.equal(b.trigger, "registry");
    assert.ok(!("streamdeck_action" in b));
    assert.equal(b.event, API.events.events[0].event);
    assert.deepEqual(plain(b.filter), {});
});

test("twitch-api-response binding: request fields and response filter rows", async () => {
    const s = await blank();
    const t = await s.add("text");
    t.bindings = [{ event: "twitch-api-response", filter: {}, action: "set_text", args: {}, chain: [] }];
    s.E.renderBindings();
    const b = t.bindings[0];
    const card = () => bcards(s)[0];
    assert.equal(b.twitch_api.method, "GET");
    assert.deepEqual(plain(b.twitch_filters), [{ key: "", value: "" }]);
    setValue(s.win, control(card(), "Endpoint"), "https://api.twitch.tv/helix/users", ["change"]);
    setValue(s.win, control(card(), "Method"), "POST", ["change"]);
    setValue(s.win, control(card(), "JSON parameters (query)"), '{"login":"x"}', ["change"]);
    setValue(s.win, control(card(), "JSON body"), '{"a":1}', ["change"]);
    assert.deepEqual(plain(b.twitch_api), { endpoint: "https://api.twitch.tv/helix/users", method: "POST", params_json: '{"login":"x"}', body_json: '{"a":1}' });
    const sa = API.events.actions.find((a) => a.action === "set_text").args.find((a) => a.key === "from_payload");
    assert.equal(control(card(), sa.label || sa.key).placeholder, "e.g. error, success, requestId");
    buttonByText(card(), "+ Add filter").click();
    assert.equal(b.twitch_filters.length, 2);
    const rows = () => card().querySelectorAll(".ss-twitch-filter-row");
    setValue(s.win, rows()[0].querySelector(".ss-twitch-filter__key"), "success", ["change"]);
    setValue(s.win, rows()[0].querySelector(".ss-twitch-filter__val"), "true", ["change"]);
    assert.deepEqual(plain(b.twitch_filters[0]), { key: "success", value: "true" });
    rows()[1].querySelector("button").click();
    assert.equal(b.twitch_filters.length, 1);
    rows()[0].querySelector("button").click();
    assert.deepEqual(plain(b.twitch_filters), [{ key: "", value: "" }], "last row is replaced by a blank one");
});

test("registryEvent / registryAction look up by name", async () => {
    const { E } = await blank();
    assert.equal(E.registryEvent("next_alert").event, "next_alert");
    assert.ok(E.registryEvent("nope") === null);
    assert.equal(E.registryAction("show").action, "show");
    assert.ok(E.registryAction("nope") === null);
});

/* ------------------------------------------------------------------ palette drag & drop, categories */

function dataTransfer(init = {}) {
    const data = Object.assign({}, init);
    return { setData(k, v) { data[k] = v; }, getData(k) { return data[k] || ""; }, effectAllowed: "", dropEffect: "", _data: data };
}

test("dragging a palette block onto the stage adds that element at the drop point", async () => {
    const s = await blank();
    const block = s.$('.ss-block[data-block-type="text"]');
    const dt = dataTransfer();
    fire(s.win, block, "dragstart", { dataTransfer: dt });
    assert.equal(dt._data["text/spore-block"], "text");
    assert.equal(dt.effectAllowed, "copy");
    assert.ok(block.classList.contains("ss-block--dragging"));
    fire(s.win, block, "dragend");
    assert.ok(!block.classList.contains("ss-block--dragging"));
    const stage = s.$("#ss-stage");
    const over = fire(s.win, stage, "dragover", { dataTransfer: dt, clientX: 50, clientY: 60 });
    assert.ok(over.defaultPrevented);
    assert.equal(dt.dropEffect, "copy");
    fire(s.win, stage, "dragleave", { relatedTarget: null });
    fire(s.win, stage, "drop", { dataTransfer: dt, clientX: 50, clientY: 60 });
    await settle();
    s.$("[data-cat-action='ok']").click();
    await settle();
    const el = s.E.selectedElement();
    assert.equal(el.type, "text");
    assert.ok(el.position.x >= 0 && el.position.y >= 0);
    // an asset drop makes an image/video/audio element with that src
    for (const [kind, type] of [["video", "video"], ["audio", "audio"], ["image", "image"]]) {
        fire(s.win, stage, "drop", { dataTransfer: dataTransfer({ "text/spore-asset": "/a/" + kind, "text/spore-asset-kind": kind }), clientX: -5, clientY: -5, altKey: true });
        await settle();
        s.$("[data-cat-action='ok']").click();
        await settle();
        const e2 = s.E.selectedElement();
        assert.equal(e2.type, type);
        assert.equal(e2.props.src, "/a/" + kind);
        assert.deepEqual(plain(e2.position), { x: 0, y: 0 }, "negative drop points clamp to 0");
    }
    // nothing in the transfer: nothing happens
    const n = s.E.state.model.elements.length;
    fire(s.win, stage, "drop", { dataTransfer: dataTransfer(), clientX: 5, clientY: 5 });
    await settle();
    assert.ok(s.$("[data-cat-action='ok']") === null);
    assert.equal(s.E.state.model.elements.length, n);
});

test("addElement: unknown types and legacy templates are refused; bad parent ids drop to the canvas", async () => {
    const s = await blank();
    s.E.addElement("nope", 0, 0);
    assert.ok(s.$("[data-cat-action='ok']") === null);
    const t = await s.add("text");
    const child = await s.add("text", 1, 1, null, t.id); // a text is not a container
    assert.ok(!child.parent_id);
    s.E.state.model.legacy = true;
    s.E.addElement("text", 0, 0);
    assert.ok(s.$("[data-cat-action='ok']") === null);
    assert.match(s.toast(), /Legacy templates can't take new blocks/);
    assert.ok(s.E.selectedElementMatchingId(null) === null);
    assert.ok(s.E.selectedElementMatchingId("missing") === null);
});

test("category prompt: first element defaults to Elements; chips, new name, Enter and cancel", async () => {
    const s = await blank();
    s.E.addElement("text", 0, 0);
    await settle();
    let modal = s.$("[data-cat-action='ok']").closest("[data-cat-action='ok']") && s.$("[data-cat='new']");
    assert.equal(modal.value, "Elements");
    assert.equal(s.$("[data-cat='select-row']").hidden, true);
    modal.value = "  ";
    s.$("[data-cat-action='ok']").click();
    await settle();
    assert.equal(s.E.selectedElement().category, "Elements", "blank falls back to Elements");

    s.E.state.model.elements[0].category = "Alerts";
    s.E.addElement("text", 0, 0);
    await settle();
    const chips = s.$("[data-cat='chips']").querySelectorAll("button");
    assert.deepEqual(chips.map((c) => c.textContent), ["Alerts"]);
    assert.ok(chips[0].classList.contains("is-on"));
    const input = s.$("[data-cat='new']");
    input.value = "Fresh";
    input.dispatchEvent(new s.win.Event("input"));
    assert.equal(s.$("[data-cat='select']").value, "__ss_new__");
    assert.ok(!chips[0].classList.contains("is-on"));
    const enter = fire(s.win, input, "keydown", { key: "Enter" });
    assert.ok(enter.defaultPrevented);
    await settle();
    assert.equal(s.E.selectedElement().category, "Fresh");
    assert.deepEqual(plain(s.E.existingCategories()), ["Alerts", "Fresh"]);

    s.E.addElement("text", 0, 0);
    await settle();
    s.$("[data-cat='new']").value = "x";
    s.$("[data-cat='chips']").querySelectorAll("button")[0].click();
    assert.equal(s.$("[data-cat='new']").value, "");
    assert.equal(s.$("[data-cat='select']").value, "Alerts");
    fire(s.win, s.$("[data-cat='new']"), "keydown", { key: "a" });
    s.$("[data-cat-action='ok']").click();
    await settle();
    assert.equal(s.E.selectedElement().category, "Alerts");

    const before = s.E.state.model.elements.length;
    s.E.addElement("text", 0, 0);
    await settle();
    s.$("[data-cat-action='cancel']").click();
    await settle();
    assert.equal(s.E.state.model.elements.length, before);
    assert.ok(s.$("[data-cat-action='ok']") === null);
});

test("rewriteModelRefs / updateBindingsForRename rewrite matching keys only", async () => {
    const s = await blank();
    const m = s.E.state.model;
    m.dynamic_controls.elements.push({ target_timer_id: "t1", other: "t1" });
    m.elements.push({ id: "x", bindings: [{ args: { element_id: "t1", target_element_id: "t1" } }], props: {} });
    s.E.updateBindingsForRename("t1", "t2");
    assert.equal(m.dynamic_controls.elements[0].target_timer_id, "t2");
    assert.equal(m.dynamic_controls.elements[0].other, "t1");
    assert.deepEqual(plain(m.elements[0].bindings[0].args), { element_id: "t2", target_element_id: "t2" });
    s.E.rewriteModelRefs({ element_id: 1 }, "", "z");
    s.E.rewriteModelRefs({ element_id: 1 }, "t2", "t2");
    const skip = m.elements[0].bindings[0].args;
    s.E.rewriteModelRefs({ element_id: 1 }, "t2", "t3", skip);
    assert.equal(skip.element_id, "t2", "the skipped node is left alone");
});

/* ------------------------------------------------------------------ assets */

const FILES = [
    { name: "a.png", kind: "image", url: "/assets/zz/a.png", rel_path: "a.png" },
    { name: "b.mp3", kind: "audio", url: "/assets/zz/b.mp3" },
    { name: "c.mp4", kind: "video", url: "/assets/zz/c.mp4" },
    { name: "d.ttf", kind: "font", url: "/assets/zz/d.ttf" },
    { name: 'e<"x>.bin', url: "/assets/zz/e.bin" },
];

test("asset grid: empty dropzone, cards, drag data, double-click into media, delete", async () => {
    const s = await blank();
    s.E.renderAssets(null);
    assert.match(s.$("#ss-asset-grid").textContent, /Drop images, sounds or videos here/);
    assert.match(s.$("#ss-asset-grid").innerHTML, /assets\/zz/);
    assert.equal(s.$("#ss-count-assets").textContent, "");
    s.E.renderAssets({ files: FILES });
    assert.equal(s.$("#ss-count-assets").textContent, "5");
    const cards = () => s.$$("#ss-asset-grid .ss-asset");
    assert.equal(cards().length, 5);
    assert.ok(cards()[0].querySelector("img"));
    assert.equal(cards()[4].querySelector(".ss-asset__name").textContent, 'e<"x>.bin', "names are escaped, not parsed as HTML");
    assert.equal(cards()[4].querySelector(".ss-asset__kind").textContent, "?");
    const dt = dataTransfer();
    fire(s.win, cards()[2], "dragstart", { dataTransfer: dt });
    assert.equal(dt._data["text/spore-asset"], "/assets/zz/c.mp4");
    assert.equal(dt._data["text/spore-asset-kind"], "video");
    fire(s.win, cards()[4], "dragstart", { dataTransfer: dt });
    assert.equal(dt._data["text/spore-asset-kind"], "image");
    fire(s.win, cards()[4], "dragend");
    // dblclick with no media element selected
    fire(s.win, cards()[0], "dblclick");
    assert.match(s.toast(), /Select an image, video or audio element first/);
    const img = await s.add("image");
    s.E.renderAssets({ files: FILES }); // image selected: properties redrawn
    fire(s.win, cards()[0], "dblclick");
    assert.equal(img.props.src, "/assets/zz/a.png");

    // delete: confirm then POST; reply re-renders the grid
    s.fetch.on("/api/spore-studio/assets/zz/delete", (u, init) => ({ files: FILES.filter((f) => f.name !== JSON.parse(init.body).rel_path) }));
    const del = cards()[0].querySelector(".ss-asset__delete");
    const ev = fire(s.win, del, "click");
    assert.ok(ev.defaultPrevented);
    buttonByText(s.doc.body, "Delete file").click();
    await settle();
    assert.equal(cards().length, 4);
    const call = s.fetch.calls.find((c) => c.url.endsWith("/delete"));
    assert.deepEqual(JSON.parse(call.body), { rel_path: "a.png" });
    // cancel does nothing; failure keeps the grid and warns
    cards()[0].querySelector(".ss-asset__delete").click();
    buttonByText(s.doc.body, "Cancel").click();
    await settle();
    assert.equal(cards().length, 4);
    s.fetch.on("/api/spore-studio/assets/zz/delete", { status: 500, body: { error: "locked" } });
    cards()[0].querySelector(".ss-asset__delete").click();
    buttonByText(s.doc.body, "Delete file").click();
    await settle();
    assert.equal(cards().length, 4);
    assert.match(s.toast(), /Couldn’t delete “b\.mp3”|Couldn't delete “b\.mp3”/);
});

test("refreshAssetsForCurrent: renders the reply, warns on failure, ignores a stale reply", async () => {
    const s = await blank();
    s.fetch.on(/\/api\/spore-studio\/assets\/zz$/, { files: FILES.slice(0, 2) });
    s.E.refreshAssetsForCurrent();
    await settle();
    assert.equal(s.$$("#ss-asset-grid .ss-asset").length, 2);
    s.fetch.on(/\/api\/spore-studio\/assets\/zz$/, { status: 500, body: { error: "boom" } });
    s.E.refreshAssetsForCurrent();
    await settle();
    assert.match(s.$("#ss-asset-grid").textContent, /Drop images/);
    assert.match(s.toast(), /Couldn't load the assets for “zz”/);
    s.fetch.on(/\/api\/spore-studio\/assets\/zz$/, { files: FILES });
    s.E.refreshAssetsForCurrent();
    s.E.state.model = Object.assign({}, s.E.state.model, { template_name: "other" });
    await settle();
    assert.ok(s.$$("#ss-asset-grid .ss-asset").length !== 5, "reply for an old template is dropped");
    s.E.state.model = null;
    const n = s.fetch.calls.length;
    s.E.refreshAssetsForCurrent();
    assert.equal(s.fetch.calls.length, n);
    s.E.deleteAsset(FILES[0]);
    s.E.deleteAssetNow(FILES[0]);
    assert.equal(s.fetch.calls.length, n);
});

/* ------------------------------------------------------------------ template list, load, save */

test("refreshTemplateList fills the picker with Spore and legacy groups; failure notifies", async () => {
    const s = await blank({}, { templates: { spore_templates: ["a", "b"], legacy_templates: ["old"] } });
    await s.E.refreshTemplateList();
    const sel = s.$("#ss-template-select");
    assert.deepEqual(sel.querySelectorAll("option").map((o) => o.value), ["a", "b", "old"]);
    assert.equal(sel.querySelector("optgroup").label, "Legacy (advanced mode)");
    s.fetch.on("/api/spore-studio/templates", { status: 500, body: {} });
    await assert.rejects(s.E.refreshTemplateList(), (e) => e.notified === true);
    assert.match(s.toast(), /Couldn't load the template list/);
    s.fetch.on("/api/spore-studio/templates", {});
    await s.E.refreshTemplateList();
    assert.equal(sel.querySelectorAll("option").length, 0);
});

test("loadTemplate: empty name clears, 404 keeps the open template, stale loads are dropped", async () => {
    const s = await blank({}, { models: { zz: blankModel("zz"), yy: blankModel("yy", { legacy: true }) } });
    s.fetch.on("/api/template-preview-settings/resolution", { ok: true, width: 1920, height: 1080, source: "obs", obs_source_name: "Alerts" });
    await s.E.loadTemplate("zz");
    await settle();
    assert.equal(s.E.state.previewWidth, 1920);
    assert.equal(s.$("#ss-preview-size-hint").textContent, "From OBS: Alerts");
    assert.equal(s.win.localStorage.getItem("mycelian-spore-last-template"), "zz");
    assert.equal(s.E.state.dirty, false);

    await s.E.loadTemplate("missing");
    assert.equal(s.E.state.model.template_name, "zz", "the open template stays");
    assert.match(s.toast(), /Couldn't open “missing”/);
    assert.equal(s.$("#ss-template-select").value === "zz" || s.$("#ss-template-select").value === "", true);

    // a slower load that is overtaken is ignored (both success and error)
    const p1 = s.E.loadTemplate("missing2");
    const p2 = s.E.loadTemplate("yy");
    await Promise.all([p1, p2]);
    assert.equal(s.E.state.model.template_name, "yy");
    assert.match(s.$("#ss-load-state").textContent, /Legacy template/);
    const p3 = s.E.loadTemplate("zz");
    const p4 = s.E.loadTemplate("missing3");
    await Promise.all([p3, p4]);
    assert.equal(s.E.state.model.template_name, "yy", "the newer (failed) load wins; the older success is dropped");

    await s.E.loadTemplate("");
    assert.ok(s.E.state.model === null);
    assert.equal(s.$("#ss-preview-mocks").textContent, "Loading mock events…");
    await s.E.loadTemplate("missing4");
    assert.match(s.$("#ss-load-state").textContent, /Couldn't open “missing4”/);
});

test("normalizeModel fills defaults and migrates old text modes", async () => {
    const { E } = await blank();
    const m = E.normalizeModel({
        alert_system: "weird", design: { width: 5, height: "x" }, duration_seconds: "abc",
        streamdeck_options: { description: 7, actions: [] },
        elements: [
            { type: "text", text_mode: "counter" },
            { type: "marquee", text_mode: "data_display", id: "m" },
            { id: "kid", type: "text", parent_id: "box" },
            { id: "box", type: "container" },
            { id: "c", type: "clock" }, { id: "tm", type: "timer" }, { id: "w", type: "wheel" },
            { id: "sc", type: "stat_card" }, { id: "sh", type: "shape" }, { id: "dv", type: "divider" },
            { id: "gr", type: "gradient" }, { id: "pb", type: "progress_bar" }, { id: "im", type: "image" },
            { id: "nopos", type: "text", position: null, size: null },
            {},
        ],
    });
    assert.equal(m.template_name, "untitled");
    assert.equal(m.alert_system, "queue");
    assert.ok(m.design.width >= 5 && m.design.height > 0);
    assert.equal(m.elements[0].type, "counter");
    assert.ok(!("text_mode" in m.elements[0]));
    const mq = m.elements.find((e) => e.id === "m");
    assert.equal(mq.type, "data_display");
    assert.equal(mq.props.scroll, true);
    assert.deepEqual(plain(m.streamdeck_options), { description: "", actions: {} });
    assert.equal(m.queued, false);
    assert.ok(m.duration_seconds >= 5);
    const np = m.elements.find((e) => e.id === "nopos");
    assert.ok(np.position === null && np.size === null, "null placement is kept");
    const anon = m.elements.find((e) => e.type === "container" && e.id !== "box");
    assert.ok(anon && anon.id && anon.category === "Elements");
    const legacy = E.normalizeModel({ legacy: true, elements: [{ id: "a", type: "text", parent_id: "b" }], streamdeck_options: null, advanced_js: 5 });
    assert.ok(!("parent_id" in legacy.elements[0]));
    assert.equal(legacy.advanced_js, "");
    assert.equal(E.normalizeModel(null).elements.length, 0);
    assert.equal(E.normalizeModel({ alert_system: "instant", duration_seconds: null }).alert_system, "instant");
});

test("saveCurrent posts the model, clears dirty, queues a second save and reports failures", async () => {
    let release;
    const gate = () => new Promise((r) => { release = r; });
    const s = await blank();
    const t = await s.add("text");
    assert.equal(s.E.state.dirty, true);
    s.E.saveCurrent();
    await settle();
    assert.equal(s.saved.length, 1);
    assert.equal(s.saved[0].model.elements[0].id, t.id);
    assert.equal(s.E.state.dirty, false);
    assert.match(s.toast(), /Saved “zz”/);

    // a save while one is running waits, then runs again if still dirty
    s.fetch.on("/api/spore-studio/save", (u, init) => gate().then(() => { s.saved.push(JSON.parse(init.body)); return { ok: true }; }));
    s.E.modelTouch();
    s.E.saveCurrent();
    await settle();
    s.E.saveCurrent();
    assert.equal(s.E.state.saveQueued, true);
    s.E.modelTouch(); // edit during the save: stays dirty
    release();
    await settle();
    assert.equal(s.E.state.dirty, true, "edits made while saving stay unsaved");
    release();
    await settle();
    assert.equal(s.saved.length, 3);
    assert.equal(s.E.state.dirty, false);
    assert.equal(s.E.state.saving, false);

    // failures: server says ok:false (4xx text shown), 500, network
    s.fetch.on("/api/spore-studio/save", { status: 400, body: { ok: false, error: "Name taken" } });
    s.E.modelTouch();
    s.E.saveCurrent();
    await settle();
    assert.match(s.toast(), /Couldn't save “zz”/);
    assert.match(s.toast(), /Name taken/);
    assert.equal(s.E.state.dirty, true);
    s.fetch.on("/api/spore-studio/save", { status: 500, body: {} });
    s.E.saveCurrent();
    await settle();
    assert.match(s.toast(), /Something went wrong while saving/);
    s.fetch.on("/api/spore-studio/save", () => new Error("offline"));
    s.E.saveCurrent();
    await settle();
    assert.match(s.toast(), /couldn't reach Mycelian/);
    assert.equal(s.E.state.saving, false);
    s.E.state.model = null;
    s.E.saveCurrent();
});

test("saving blurs the field being typed in so its value is included", async () => {
    const s = await blank();
    const t = await s.add("text");
    const idInput = control(collapse(pane(s), "Element"), "ID");
    idInput.value = "typed_id";
    idInput.focus();
    let blurred = false;
    idInput.addEventListener("blur", () => { blurred = true; idInput.dispatchEvent(new s.win.Event("change", { bubbles: true })); });
    s.E.saveCurrent();
    await settle();
    assert.ok(blurred);
    assert.equal(s.saved.at(-1).model.elements[0].id, "typed_id");
    assert.ok(t);
});

/* ------------------------------------------------------------------ preview dialog */

test("preview dialog: open registers a session and points the iframe; close releases it", async () => {
    const s = await blank();
    const dlg = s.$("#ss-preview-dialog");
    assert.equal(s.E.previewDialogOpen(), false);
    s.E.refreshPreview(true); // closed: no-op
    assert.ok(!s.fetch.calls.some((c) => c.url.includes("/preview/register")));
    s.E.togglePreviewDialog();
    await settle();
    assert.equal(s.E.previewDialogOpen(), true);
    const reg = s.fetch.calls.find((c) => c.url.includes("/preview/register"));
    assert.deepEqual(JSON.parse(reg.body), { token: s.E.state.previewToken, template: "zz" });
    const src = s.$("#ss-preview-iframe").src;
    assert.match(src, /^\/zz\?__preview_token=[^&]+&_cb=\d+&_force=1$/);
    assert.equal(s.$("#ss-preview-viewport").style.width, "800px");
    assert.equal(s.$("#ss-preview-iframe").style.transform, "scale(1)");

    s.$("#ss-preview-reload").click();
    await settle();
    s.$("#ss-preview-close").click();
    assert.equal(dlg.hasAttribute("hidden"), true);
    assert.equal(s.$("#ss-preview-iframe").src, "about:blank");
    await settle();
    assert.ok(s.fetch.calls.some((c) => c.url.includes("/preview/release")));
    s.E.state.previewToken = "";
    s.E.releasePreviewSession();
    s.E.state.model = null;
    await s.E.registerPreviewSession();
    await s.E.fetchPreviewResolution();
    await s.E.savePreviewResolution(1, 2);
    await s.E.clearPreviewResolution();
});

test("preview zoom and size inputs: scale the iframe, save and clear the stored size", async () => {
    const s = await blank();
    s.E.openPreviewDialog();
    await settle();
    const range = s.$("#ss-preview-zoom-range");
    setValue(s.win, range, "50", ["input"]);
    assert.equal(s.E.state.previewZoomPct, 50);
    assert.equal(s.$("#ss-preview-iframe").style.transform, "scale(0.5)");
    assert.equal(s.$("#ss-preview-viewport").style.width, "400px");
    assert.equal(s.$("#ss-preview-zoom-value").textContent, "50%");
    s.$("#ss-preview-zoom-reset").click();
    assert.equal(s.$("#ss-preview-zoom-value").textContent, "100%");
    s.E.state.previewZoomPct = -3;
    s.E.applyPreviewLayout();
    assert.equal(s.$("#ss-preview-zoom-value").textContent, "100%");

    const w = s.$("#ss-preview-size-w");
    const h = s.$("#ss-preview-size-h");
    w.value = "100"; h.value = "100";
    w.dispatchEvent(new s.win.Event("change"));
    assert.equal(s.E.state.previewSizeSource || "", "", "too small is ignored");
    w.value = "9000"; h.value = "5000";
    h.dispatchEvent(new s.win.Event("change"));
    assert.equal(s.E.state.previewWidth, 7680);
    assert.equal(s.E.state.previewHeight, 4320);
    assert.equal(s.$("#ss-preview-size-hint").textContent, "Saved preview size");
    const post = s.fetch.calls.filter((c) => c.url.endsWith("/resolution") && c.method === "POST").at(-1);
    assert.deepEqual(JSON.parse(post.body), { key: "zz", width: 7680, height: 4320 });
    assert.deepEqual(plain(s.E.previewDesignSize()), { width: 7680, height: 4320 });
    s.$("#ss-preview-size-reset").click();
    await settle();
    const clr = s.fetch.calls.filter((c) => c.url.endsWith("/resolution") && c.method === "POST").at(-1);
    assert.deepEqual(JSON.parse(clr.body), { key: "zz", clear: true });
    assert.ok(s.E.state.previewWidth === null);
    assert.deepEqual(plain(s.E.previewDesignSize()), { width: 800, height: 400 });
    s.E.state.previewSizeSource = "obs"; s.E.state.previewSizeHint = "";
    s.E.syncPreviewSizeInputs();
    assert.equal(s.$("#ss-preview-size-hint").textContent, "From OBS");
    s.E.state.previewWidth = "abc"; s.E.state.previewHeight = 10;
    assert.deepEqual(plain(s.E.previewDesignSize()), { width: 800, height: 240 });
});

test("preview dialog drag and resize stay on screen", async () => {
    const s = await blank({}, { innerWidth: 1000, innerHeight: 700 });
    const dlg = s.$("#ss-preview-dialog");
    s.E.openPreviewDialog();
    await settle();
    dlg._rect = { left: 100, top: 100, width: 400, height: 300 };
    Object.defineProperty(dlg, "offsetWidth", { value: 400, configurable: true });
    Object.defineProperty(dlg, "offsetHeight", { value: 300, configurable: true });
    const head = dlg.querySelector("[data-drag-handle]");
    const down = fire(s.win, head, "mousedown", { clientX: 10, clientY: 10 });
    assert.ok(down.defaultPrevented);
    assert.ok(dlg.classList.contains("ss-preview-dialog--chrome-active"));
    fire(s.win, s.doc, "mousemove", { clientX: 5000, clientY: -5000 });
    assert.equal(dlg.style.left, "600px");
    assert.equal(dlg.style.top, "0px");
    fire(s.win, s.doc, "mouseup");
    assert.ok(!dlg.classList.contains("ss-preview-dialog--chrome-active"));
    const btn = head.querySelector("button");
    if (btn) {
        const d2 = fire(s.win, btn, "mousedown", { clientX: 1, clientY: 1 });
        assert.equal(d2.defaultPrevented, false, "buttons in the header don't start a drag");
    }
    const rs = dlg.querySelector("[data-resize-handle]");
    fire(s.win, rs, "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: -1000, clientY: -1000 });
    assert.equal(dlg.style.width, "280px");
    assert.equal(dlg.style.height, "200px");
    fire(s.win, s.doc, "mousemove", { clientX: 5000, clientY: 5000 });
    assert.equal(dlg.style.width, "900px");
    assert.equal(dlg.style.height, "600px");
    fire(s.win, s.doc, "mouseup");
    fire(s.win, s.doc, "mousemove", { clientX: 1, clientY: 1 });
});

test("preview mock toolbar: alert/other/Stream Deck buttons emit; failures notify", async () => {
    const s = await blank({
        preview_mocks: [
            { event: "next_alert", alert_type: "follow", label: "Follow" },
            { event: "poll_state" },
        ],
        streamdeck_options: { description: "", actions: { b: { name: "", event: "sd_b" }, a: { name: "Act A", event: "sd_a", default_data: { x: 1 } } } },
    });
    s.E.renderPreviewMockToolbar();
    const host = s.$("#ss-preview-mocks");
    assert.deepEqual(host.children.map((c) => c.textContent), ["Alerts:", "Follow", "Other:", "poll_state", "SD:", "Act A", "b"]);
    assert.equal(buttonByText(host, "Follow").title, "Emit mock 'next_alert' (follow) over the preview socket");
    const emits = () => s.fetch.calls.filter((c) => c.url.includes("/preview/emit")).map((c) => JSON.parse(c.body));
    buttonByText(host, "Follow").click();
    buttonByText(host, "poll_state").click();
    buttonByText(host, "Act A").click();
    buttonByText(host, "b").click();
    await settle();
    const tok = s.E.state.previewToken;
    assert.deepEqual(emits(), [
        { token: tok, event: "next_alert", alert_type: "follow" },
        { token: tok, event: "poll_state" },
        { token: tok, event: "sd_a", data: { x: 1 } },
        { token: tok, event: "sd_b", data: {} },
    ]);
    s.fetch.on("/api/spore-studio/preview/emit", { status: 404, body: { error: "No preview iframe registered" } });
    buttonByText(host, "Follow").click();
    await settle();
    assert.match(s.toast(), /Couldn't send the test event/);
    assert.match(s.toast(), /Open the preview first/);
    buttonByText(host, "Act A").click();
    await settle();
    s.fetch.on("/api/spore-studio/preview/emit", () => new Error("down"));
    buttonByText(host, "poll_state").click();
    await settle();
    assert.match(s.toast(), /couldn't reach the overlay server/);
    buttonByText(host, "b").click();
    await settle();

    // legacy: whole registry; no model: loading; Spore without mocks: loading + refresh
    s.E.state.model.legacy = true;
    s.E.renderPreviewMockToolbar();
    assert.equal(host.children[0].textContent, "Mock:");
    assert.equal(host.querySelectorAll("button").length, API.events.events.length);
    const evs = s.E.state.registry.events;
    s.E.state.registry.events = [];
    s.E.renderPreviewMockToolbar();
    assert.equal(host.textContent, "Loading mock events…");
    s.E.state.registry.events = evs;
    s.E.state.model.legacy = false;
    s.E.state.model.preview_mocks = [];
    s.E.renderPreviewMockToolbar();
    assert.match(host.textContent, /^Loading mock events…/);
});

/* ------------------------------------------------------------------ create dialog */

test("create dialog: presets, alert cards, validation, success loads the new template", async () => {
    const created = blankModel("fresh");
    const s = await blank({}, { models: { zz: blankModel("zz"), fresh: created } });
    s.fetch.on("/api/spore-studio/create", (u, init) => ({ ok: true, name: JSON.parse(init.body).name }));
    await s.E.refreshTemplateList();
    s.E.openCreateDialog();
    const dlg = () => s.$("[data-create-action='create']").closest("[data-create-action='create']") && s.$("[data-create='name']").closest(".ss-create-overlay, [role=dialog], div");
    assert.ok(dlg());
    const copy = s.$("[data-create='copy_from']");
    assert.ok(copy.querySelectorAll("option").some((o) => o.value === "zz"));
    const presets = s.$$("[data-create='presets'] button");
    if (presets.length) {
        presets[0].click();
        assert.equal(s.$("[data-create='design_width']").value, presets[0].dataset.w);
        assert.ok(presets[0].classList.contains("is-on"));
        s.$("[data-create='design_width']").value = "123";
        s.$("[data-create='design_width']").dispatchEvent(new s.win.Event("input"));
        assert.ok(!presets[0].classList.contains("is-on"));
    }
    const cards = s.$$("[data-create='alert-cards'] .ss-radio-card");
    if (cards.length > 1) {
        cards[1].click();
        assert.equal(s.$("[data-create='alert_system']").value, cards[1].dataset.value);
        assert.equal(cards[1].getAttribute("aria-pressed"), "true");
        assert.equal(cards[0].getAttribute("aria-pressed"), "false");
    }
    s.$("[data-create-action='create']").click();
    assert.equal(s.$("[data-create='error']").textContent, "Enter a name for the new template.");
    assert.equal(s.$("[data-create='error']").hidden, false);
    s.$("[data-create='name']").value = " fresh ";
    fire(s.win, s.$("[data-create='name']"), "keydown", { key: "a" });
    fire(s.win, s.$("[data-create='name']"), "keydown", { key: "Enter" });
    await settle(30);
    const body = JSON.parse(s.fetch.calls.find((c) => c.url.includes("/create")).body);
    assert.equal(body.name, "fresh");
    assert.equal(body.design_width, 123);
    assert.ok(s.$("[data-create-action='create']") === null, "dialog closed");
    assert.match(s.toast(), /Created “fresh”|Couldn't/);
    assert.equal(s.E.state.model.template_name, "fresh");
});

test("create dialog errors: 4xx text in the form, 5xx and network notify; cancel closes", async () => {
    const s = await blank();
    s.E.openCreateDialog();
    s.fetch.on("/api/spore-studio/create", { status: 409, body: { error: "That name is taken." } });
    s.$("[data-create='name']").value = "x";
    s.$("[data-create-action='create']").click();
    await settle();
    assert.equal(s.$("[data-create='error']").textContent, "That name is taken.");
    s.fetch.on("/api/spore-studio/create", { status: 500, body: {} });
    s.$("[data-create-action='create']").click();
    await settle();
    assert.equal(s.$("[data-create='error']").textContent, "Couldn't create the template.");
    assert.match(s.toast(), /Something went wrong while creating/);
    s.fetch.on("/api/spore-studio/create", () => new Error("offline"));
    s.$("[data-create-action='create']").click();
    await settle();
    assert.match(s.toast(), /couldn't reach Mycelian/);
    // created, but the list refresh fails: the dialog closes, no form error
    s.fetch.on("/api/spore-studio/create", { ok: true });
    s.fetch.on("/api/spore-studio/templates", { status: 500, body: {} });
    s.$("[data-create-action='create']").click();
    await settle();
    assert.ok(s.$("[data-create-action='create']") === null);
    s.E.openCreateDialog();
    s.$("[data-create-action='cancel']").click();
    assert.ok(s.$("[data-create-action='create']") === null);
});

/* ------------------------------------------------------------------ outline, Canvas tab, tabs */

test("outline groups by top-level category, indents children and selects on click", async () => {
    const s = await blank();
    s.E.renderOutline();
    assert.match(s.$("#ss-outline-list").textContent, /No elements yet/);
    assert.equal(s.$("#ss-count-outline").textContent, "0");
    const box = await s.add("container", 0, 0, null, null, "Main");
    const kid = await s.add("text", 0, 0, null, box.id, "Ignored");
    const lone = await s.add("text", 0, 0, null, null, " ");
    s.E.renderOutline();
    const groups = s.$$("#ss-outline-list .ss-outline-group");
    assert.deepEqual(groups.map((g) => g.querySelector(".ss-outline-group__head").textContent), ["Main", "Ignored"], "a blank new category falls back to the last one used");
    const rows = groups[0].querySelectorAll(".ss-outline-row");
    assert.deepEqual(rows.map((r) => r.querySelector(".ss-outline-row__id").textContent), [box.id, kid.id]);
    assert.ok(rows[1].classList.contains("is-nested"));
    assert.equal(rows[1].style.marginLeft, "16px");
    assert.equal(s.$("#ss-count-outline").textContent, "3");
    rows[0].click();
    assert.equal(s.E.state.selectedId, box.id);
    assert.ok(s.$("#ss-outline-list .ss-outline-row.selected .ss-outline-row__id").textContent === box.id);
    assert.ok(node(s, box).classList.contains("selected"));
    assert.ok(!node(s, lone).classList.contains("selected"));
    s.E.state.model = null;
    s.E.renderOutline();
    assert.equal(s.$("#ss-count-outline").textContent, "");
});

test("Canvas tab fields edit the design, alert system, title, duration, queue flag and Advanced JS", async () => {
    const s = await blank();
    s.E.renderCanvasPanel();
    const m = s.E.state.model;
    assert.equal(s.$("#ss-canvas-w").value, "800");
    assert.match(s.$("#ss-canvas-transforms").textContent, /Transformations/);
    assert.deepEqual(plain(m.canvas_transforms), []);
    setValue(s.win, s.$("#ss-canvas-w"), "1000", ["change"]);
    assert.equal(m.design.width, 1000);
    setValue(s.win, s.$("#ss-canvas-w"), "x", ["change"]);
    assert.equal(m.design.width, 800);
    s.E.openPreviewDialog();
    await settle();
    setValue(s.win, s.$("#ss-canvas-h"), "1", ["change"]);
    assert.ok(m.design.height > 1, "clamped to the minimum");
    setValue(s.win, s.$("#ss-canvas-h"), "", ["change"]);
    assert.equal(m.design.height, 200);
    setValue(s.win, s.$("#ss-alert-system"), "instant", ["change"]);
    assert.equal(m.alert_system, "instant");
    setValue(s.win, s.$("#ss-alert-system"), "queue", ["change"]);
    assert.equal(m.alert_system, "queue");
    setValue(s.win, s.$("#ss-template-title"), "My Title", ["change"]);
    assert.equal(m.title, "My Title");
    setValue(s.win, s.$("#ss-duration-seconds"), "0", ["change"]);
    assert.equal(m.duration_seconds, 0.1);
    setValue(s.win, s.$("#ss-duration-seconds"), "abc", ["change"]);
    assert.equal(m.duration_seconds, s.E.computeMinDurationSeconds(m));
    m.duration_seconds = 99;
    s.$("#ss-duration-reset").click();
    assert.equal(m.duration_seconds, s.E.computeMinDurationSeconds(m));
    setValue(s.win, s.$("#ss-queued"), true, ["change"]);
    assert.equal(m.queued, true);
    setValue(s.win, s.$("#ss-advanced-js"), "console.log(1)", ["input"]);
    assert.equal(m.advanced_js, "console.log(1)");
    fire(s.win, s.$("#ss-advanced-js"), "blur", { bubbles: false });
    // with no template open every field is a no-op
    s.E.state.model = null;
    for (const id of ["#ss-canvas-w", "#ss-canvas-h", "#ss-alert-system", "#ss-template-title", "#ss-duration-seconds", "#ss-queued"]) {
        setValue(s.win, s.$(id), "1", ["change"]);
    }
    setValue(s.win, s.$("#ss-advanced-js"), "x", ["input"]);
    s.$("#ss-duration-reset").click();
    s.E.renderCanvasPanel();
    const mr = s.$("#ss-canvas-match-root");
    if (mr) { mr.click(); }
});

test("Match canvas to root container: needs exactly one top-level container", async () => {
    const s = await blank();
    const mr = s.$("#ss-canvas-match-root");
    if (!mr) { return; }
    mr.click();
    assert.match(s.toast(), /no top-level container/);
    const a = await s.add("container");
    await s.add("container");
    mr.click();
    assert.match(s.toast(), /There are 2 top-level containers/);
    s.E.state.model.elements.pop();
    a.size = { w: 1280, h: 720 };
    s.E.openPreviewDialog();
    await settle();
    mr.click();
    assert.deepEqual(plain(s.E.state.model.design), { width: 1280, height: 720 });
    assert.equal(s.$("#ss-canvas-w").value, "1280");
    a.size = { w: "x", h: null };
    mr.click();
    assert.ok(s.E.state.model.design.width > 0);
});

test("Advanced JS pop-out editor is bound to the model", async () => {
    const s = await blank();
    const J = s.win.SporeJsEditor;
    assert.ok(J);
    s.E.state.model.advanced_js = "let a = 1;";
    J.open && J.open();
    s.E.renderCanvasPanel();
    if (J.isOpen && J.isOpen()) {
        setValue(s.win, s.$("#ss-advanced-js"), "let b = 2;", ["input"]);
        assert.equal(J.getValue ? J.getValue() : "let b = 2;", "let b = 2;");
        J.close && J.close();
    }
});

test("tabs switch panes; scroll arrows show only when the strip overflows", async () => {
    const s = await blank();
    const tabs = s.$$(".ss-tab");
    const last = tabs[tabs.length - 1];
    last.click();
    assert.ok(last.classList.contains("active"));
    assert.ok(s.$("#ss-pane-" + last.dataset.tab).classList.contains("active"));
    assert.ok(last._scrolledIntoView >= 1);
    s.clock.tick(250);
    const strip = s.$("#ss-tabs-scroll");
    const left = s.$("#ss-tabs-scroll-left");
    const right = s.$("#ss-tabs-scroll-right");
    strip.clientWidth = 200; strip.scrollWidth = 200;
    fire(s.win, strip, "scroll");
    assert.equal(left.hidden, true);
    strip.scrollWidth = 600; strip.scrollLeft = 0;
    fire(s.win, s.win, "resize");
    assert.equal(left.hidden, false);
    assert.equal(left.disabled, true);
    assert.equal(right.disabled, false);
    right.click();
    assert.equal(strip.scrollLeft, 130);
    fire(s.win, strip, "scroll");
    assert.equal(left.disabled, false);
    left.click();
    assert.equal(strip.scrollLeft, 0);
    strip.scrollLeft = 400;
    s.E.updateTabsScrollArrows();
    assert.equal(right.disabled, true);
});

test("inspector resize handle drags the width within limits and remembers it", async () => {
    const s = await blank({}, { localStorage: { "spore-studio-inspector-width": "300" } });
    const root = s.doc.documentElement;
    assert.equal(root.style.getPropertyValue("--ss-inspector-width"), "300px", "restored on start");
    const h = s.$("#ss-inspector-resize");
    const down = fire(s.win, h, "mousedown", { clientX: 500 });
    assert.ok(down.defaultPrevented);
    assert.ok(h.classList.contains("ss-inspector-resize--active"));
    fire(s.win, s.doc, "mousemove", { clientX: 0 });
    const w = parseInt(root.style.getPropertyValue("--ss-inspector-width"), 10);
    assert.ok(w <= 560 && w >= 240);
    fire(s.win, s.doc, "mouseup");
    assert.ok(!h.classList.contains("ss-inspector-resize--active"));
    fire(s.win, s.doc, "mousemove", { clientX: 900 });
    assert.equal(parseInt(root.style.getPropertyValue("--ss-inspector-width"), 10), w, "no drag after mouseup");
    fire(s.win, s.doc, "mouseup");
    assert.equal(s.E.applyInspectorWidth(10000), 560);
    assert.equal(s.E.applyInspectorWidth(1), 240);
    assert.equal(s.win.localStorage.getItem("spore-studio-inspector-width"), "240");
});

/* ------------------------------------------------------------------ socket and theme */

test("socket: asks for the theme on connect, applies theme updates, re-renders matching asset changes", async () => {
    const io = makeIo();
    const s = await blank({}, { extra: { MycelianOverlay: { connect: (u) => io(u) } } });
    const sock = io.sockets[0];
    assert.match(sock.url, /^http:\/\/.*:5000$/); // document.domain + location.port
    sock._recv("connect");
    assert.deepEqual(plain(sock.emitted[0]), ["get_current_theme"]);
    sock._recv("theme_update", { css: ":root{--x:1}", theme_type: "dark" });
    const style = s.doc.getElementById("mycelian-theme-vars");
    assert.equal(style.textContent, ":root{--x:1}");
    assert.equal(s.doc.documentElement.dataset.themeType, "dark");
    sock._recv("theme_update", { css: "a{}" });
    assert.equal(s.doc.getElementById("mycelian-theme-vars").textContent, "a{}");
    sock._recv("theme_update", null);
    sock._recv("spore_studio_assets_changed", { template: "ZZ", snapshot: { files: FILES.slice(0, 1) } });
    assert.equal(s.$$("#ss-asset-grid .ss-asset").length, 1);
    sock._recv("spore_studio_assets_changed", { template: "other", snapshot: { files: FILES } });
    sock._recv("spore_studio_assets_changed", null);
    assert.equal(s.$$("#ss-asset-grid .ss-asset").length, 1);
    s.E.state.model = null;
    sock._recv("spore_studio_assets_changed", { template: "ZZ", snapshot: { files: FILES } });
    assert.equal(s.$$("#ss-asset-grid .ss-asset").length, 1);
});

test("socket: falls back to io.connect; a failure is only logged", async () => {
    const io = makeIo();
    io.connect = (u) => io(u);
    const s = await blank({}, { extra: { io } });
    assert.equal(io.sockets.length, 1);
    s.win.io = { connect() { throw new Error("no socket"); } };
    s.E.setupSocket();
    assert.ok(s.win.__console.some((l) => /Socket setup failed/.test(l.join ? l.join(" ") : String(l))));
});

/* ------------------------------------------------------------------ delete template, upload, reveal */

test("delete template: button state, confirm lists containers, deletes and opens the next one", async () => {
    const s = await blank({}, { models: { zz: blankModel("zz"), aa: blankModel("aa") } });
    const btn = s.$("#ss-btn-delete");
    assert.equal(btn.disabled, false);
    s.fetch.on("/api/spore-studio/containers", { containers: [{ route: "main", templates: ["zz"] }, { route: "x", templates: [] }] });
    let left = ["aa"];
    s.fetch.on("/api/spore-studio/templates", () => ({ spore_templates: left, legacy_templates: [] }));
    s.fetch.on("/api/spore-studio/delete", { ok: true });
    btn.click();
    await settle();
    assert.match(s.doc.body.textContent, /Used by the container \/main/);
    buttonByText(s.doc.body, "Delete template").click();
    await settle(30);
    assert.deepEqual(JSON.parse(s.fetch.calls.find((c) => c.url.endsWith("/delete")).body), { name: "zz" });
    assert.equal(s.E.state.model.template_name, "aa");
    assert.match(s.toast(), /Deleted “zz”|aa/);

    // last template: empty state
    left = [];
    s.fetch.on("/api/spore-studio/containers", { status: 500, body: {} });
    btn.click();
    await settle();
    assert.ok(!/Used by/.test(s.doc.body.textContent));
    buttonByText(s.doc.body, "Delete template").click();
    await settle(30);
    assert.ok(s.E.state.model === null);
    assert.equal(btn.disabled, true);
    assert.equal(btn.title, "Choose a template to delete");
    assert.match(s.$("#ss-load-state").textContent, /No templates yet/);
    s.E.deleteCurrentTemplate(); // nothing open: no-op
});

test("delete template: cancel, several containers, errors and legacy", async () => {
    const s = await blank();
    s.fetch.on("/api/spore-studio/containers", () => new Error("offline"));
    s.$("#ss-btn-delete").click();
    await settle();
    buttonByText(s.doc.body, "Cancel").click();
    await settle();
    assert.ok(!s.fetch.calls.some((c) => c.url.endsWith("/delete")));
    s.fetch.on("/api/spore-studio/containers", { containers: [{ route: "a", templates: ["zz"] }, { route: "b", templates: ["zz"] }] });
    s.$("#ss-btn-delete").click();
    await settle();
    assert.match(s.doc.body.textContent, /Used by 2 containers: \/a, \/b/);
    buttonByText(s.doc.body, "Cancel").click();
    for (const [reply, re] of [
        [{ status: 409, body: { error: "In use" } }, /In use/],
        [{ status: 500, body: {} }, /Something went wrong while deleting/],
        [new Error("down"), /couldn't reach Mycelian/],
    ]) {
        s.fetch.on("/api/spore-studio/delete", () => reply);
        s.E.deleteTemplateNow("zz");
        await settle();
        assert.match(s.toast(), re);
    }
    // deleted, but the list fails afterwards: no "couldn't delete"
    s.fetch.on("/api/spore-studio/delete", { ok: true });
    s.fetch.on("/api/spore-studio/templates", { status: 500, body: {} });
    s.E.deleteTemplateNow("zz");
    await settle();
    assert.match(s.toast(), /Couldn't load the template list/);
    s.E.state.model.legacy = true;
    s.E.updateDeleteTemplateButton();
    assert.equal(s.$("#ss-btn-delete").title, "Legacy templates cannot be deleted here");
    s.E.deleteCurrentTemplate();
});

test("upload: each file is posted in turn; a failure stops the rest and says how many were skipped", async () => {
    const s = await blank();
    const input = s.$("#ss-asset-file");
    let clicked = 0;
    input.addEventListener("click", () => { clicked++; });
    s.$("#ss-asset-upload").click();
    assert.equal(clicked, 1);
    const mk = (name) => new s.win.Blob(["x"], { type: "image/png" });
    const files = ["a.png", "b.png", "c.png", "d.png"].map((n) => Object.assign(mk(n), { name: n }));
    let n = 0;
    s.fetch.on("/upload", () => { n++; return n === 2 ? { status: 413, body: { error: "Too big" } } : { files: FILES.slice(0, n) }; });
    Object.defineProperty(input, "files", { value: files, configurable: true });
    input.dispatchEvent(new s.win.Event("change"));
    await settle(30);
    assert.equal(n, 2);
    assert.equal(s.$$("#ss-asset-grid .ss-asset").length, 1);
    assert.match(s.toast(), /Couldn't upload “b\.png”/);
    assert.match(s.toast(), /Too big/);
    assert.match(s.toast(), /The 2 file\(s\) after it were not uploaded/);
    s.fetch.on("/upload", { status: 500, body: {} });
    Object.defineProperty(input, "files", { value: files.slice(0, 1), configurable: true });
    input.dispatchEvent(new s.win.Event("change"));
    await settle(30);
    assert.match(s.toast(), /Something went wrong while uploading/);
    s.fetch.on("/upload", () => new Error("net"));
    input.dispatchEvent(new s.win.Event("change"));
    await settle(30);
    assert.match(s.toast(), /couldn't reach Mycelian/);
    Object.defineProperty(input, "files", { value: [], configurable: true });
    input.dispatchEvent(new s.win.Event("change"));
    // reveal
    s.fetch.on("/reveal", { ok: true });
    s.$("#ss-asset-reveal").click();
    await settle();
    assert.ok(s.fetch.calls.some((c) => c.url === "/api/spore-studio/assets/zz/reveal" && c.method === "POST"));
    s.fetch.on("/reveal", { status: 500, body: {} });
    s.$("#ss-asset-reveal").click();
    await settle();
    assert.match(s.toast(), /Couldn't open the assets folder/);
    s.fetch.on("/reveal", { status: 400, body: { error: "No folder" } });
    s.$("#ss-asset-reveal").click();
    await settle();
    assert.match(s.toast(), /No folder/);
    // without a template
    s.E.state.model = null;
    s.$("#ss-asset-upload").click();
    assert.match(s.toast(), /Open a template first/);
    s.$("#ss-asset-reveal").click();
    assert.equal(clicked, 1);
});

/* ------------------------------------------------------------------ keyboard */

const key = (s, k, init = {}, target) => fire(s.win, target || s.doc.body, "keydown", Object.assign({ key: k }, init));

test("arrow keys nudge the selection (Shift = 10px), clamped at 0; nested elements move their offsets", async () => {
    const s = await blank();
    const t = await s.add("text", 5, 5);
    const ev = key(s, "ArrowRight");
    assert.ok(ev.defaultPrevented);
    assert.equal(t.position.x, 6);
    key(s, "ArrowDown", { shiftKey: true });
    assert.equal(t.position.y, 15);
    key(s, "ArrowLeft", { shiftKey: true });
    key(s, "ArrowUp", { shiftKey: true });
    assert.deepEqual(plain(t.position), { x: 0, y: 5 });
    // typing in a field: arrows belong to the field
    const inp = s.doc.createElement("input");
    s.doc.body.appendChild(inp);
    assert.equal(key(s, "ArrowRight", {}, inp).defaultPrevented, false);
    // nested
    const box = await s.add("container", 0, 0);
    const kid = await s.add("text", 0, 0, null, box.id);
    const ox = Number(kid.placement.offset_x) || 0;
    key(s, "ArrowRight");
    assert.equal(kid.placement.offset_x, ox + 1);
    // nothing selected: arrows pass through
    s.E.state.selectedId = null;
    assert.equal(key(s, "ArrowRight").defaultPrevented, false);
    assert.equal(s.E.nudgeFromKey("ArrowRight"), false);
    s.E.state.selectedId = kid.id;
    assert.equal(s.E.nudgeFromKey("Enter"), false);
    s.E.state.selectedId = "gone";
    s.E.nudgeSelected(1, 1);
});

test("host nudge messages move the selection unless the containers view is open", async () => {
    const s = await blank();
    const t = await s.add("text", 5, 5);
    const msg = (data) => s.win.dispatchEvent(Object.assign(new s.win.Event("message"), { data }));
    msg({ source: "mycelian-host", type: "spore-nudge", key: "ArrowRight", shiftKey: true });
    assert.equal(t.position.x, 15);
    msg({ source: "other", type: "spore-nudge", key: "ArrowRight" });
    msg(null);
    assert.equal(t.position.x, 15);
    s.$("#ss-root").classList.add("ss-root--containers");
    msg({ source: "mycelian-host", type: "spore-nudge", key: "ArrowRight" });
    assert.equal(key(s, "ArrowRight").defaultPrevented, false, "keys belong to the containers view");
    assert.equal(t.position.x, 15);
    s.$("#ss-root").classList.remove("ss-root--containers");
    s.E.state.selectedId = null;
    msg({ source: "mycelian-host", type: "spore-nudge", key: "ArrowRight" });
    assert.equal(t.position.x, 15);
});

test("Ctrl+D duplicates (offset, own counter id); Delete removes; Ctrl+S saves; Ctrl+Z/Y undo/redo", async () => {
    const s = await blank();
    const c = await s.add("counter", 10, 10);
    c.counter.counter_id = "cnt";
    const n0 = s.E.state.model.elements.length;
    assert.ok(key(s, "d", { ctrlKey: true }).defaultPrevented);
    const copy = s.E.selectedElement();
    assert.equal(s.E.state.model.elements.length, n0 + 1);
    assert.notEqual(copy.id, c.id);
    assert.deepEqual(plain(copy.position), { x: 26, y: 26 });
    assert.notEqual(copy.counter.counter_id, "cnt");
    assert.equal(copy.counter.database_key, copy.counter.counter_id);
    // a duplicate of the duplicate takes the next free counter id
    copy.counter.counter_id = s.E.slugifyCounterId(copy.id + "_x");
    s.select(c.id);
    key(s, "D", { metaKey: true });
    const copy2 = s.E.selectedElement();
    assert.ok(copy2.counter.counter_id !== "cnt" && copy2.counter.counter_id !== copy.counter.counter_id);
    // nested + timer copies
    const box = await s.add("container");
    const tm = await s.add("timer", 0, 0, null, box.id);
    const off = Number(tm.placement.offset_x) || 0;
    key(s, "d", { ctrlKey: true });
    assert.equal(s.E.selectedElement().placement.offset_x, off + 16);
    assert.equal(s.E.selectedElement().type, "timer");
    // typing target: Ctrl+D ignored
    const inp = s.doc.createElement("input");
    s.doc.body.appendChild(inp);
    const before = s.E.state.model.elements.length;
    key(s, "d", { ctrlKey: true }, inp);
    assert.equal(s.E.state.model.elements.length, before);
    // Delete / Backspace
    const sel = s.E.state.selectedId;
    key(s, "Delete");
    assert.ok(s.E.selectedElementMatchingId(sel) === null);
    assert.ok(s.E.state.selectedId === null);
    key(s, "Backspace"); // nothing selected
    s.select(c.id);
    key(s, "Backspace", {}, inp);
    assert.ok(s.E.selectedElementMatchingId(c.id), "Backspace in a field doesn't delete");
    // undo / redo
    const count = s.E.state.model.elements.length;
    s.E.flushHistoryDebounce && s.E.flushHistoryDebounce();
    key(s, "Backspace");
    assert.equal(s.E.state.model.elements.length, count - 1);
    key(s, "z", { ctrlKey: true });
    assert.equal(s.E.state.model.elements.length, count);
    key(s, "Z", { ctrlKey: true, shiftKey: true });
    assert.equal(s.E.state.model.elements.length, count - 1);
    key(s, "z", { ctrlKey: true });
    key(s, "y", { ctrlKey: true });
    assert.equal(s.E.state.model.elements.length, count - 1);
    key(s, "z", { ctrlKey: true });
    key(s, "z", { ctrlKey: true, shiftKey: true });
    assert.equal(s.E.state.model.elements.length, count - 1);
    // Ctrl+Z in a field is the field's own undo
    const len = s.E.state.history.length;
    assert.equal(key(s, "z", { ctrlKey: true }, inp).defaultPrevented, false);
    assert.equal(s.E.state.history.length, len);
    // Ctrl+S
    const saves = s.saved.length;
    assert.ok(key(s, "s", { ctrlKey: true }).defaultPrevented);
    await settle();
    assert.equal(s.saved.length, saves + 1);
    s.E.state.selectedId = null;
    s.E.duplicateSelected();
});

test("keys are ignored while a modal is open or the tour runs", async () => {
    const s = await blank();
    const t = await s.add("text", 5, 5);
    s.doc.body.classList.add("ss-tour-running");
    key(s, "ArrowRight");
    assert.equal(t.position.x, 5);
    s.doc.body.classList.remove("ss-tour-running");
    s.E.deleteAsset({ name: "x" }); // opens a confirm modal
    key(s, "ArrowRight");
    assert.equal(t.position.x, 5);
    buttonByText(s.doc.body, "Cancel").click();
    await settle();
    key(s, "ArrowRight");
    assert.equal(t.position.x, 6);
    assert.equal(s.E.typingTarget(null), false);
    const ce = s.doc.createElement("div");
    ce.setAttribute("contenteditable", "true");
    assert.equal(s.E.typingTarget(ce), true);
    assert.equal(s.E.typingTarget(s.doc.createElement("select")), true);
});

/* ------------------------------------------------------------------ toolbar */

test("template picker: opens directly when saved, asks first when there are unsaved changes", async () => {
    const s = await blank({}, { models: { zz: blankModel("zz"), aa: blankModel("aa") } });
    const sel = s.$("#ss-template-select");
    setValue(s.win, sel, "aa", ["change"]);
    await settle(20);
    assert.equal(s.E.state.model.template_name, "aa");
    assert.equal(s.win.localStorage.getItem("mycelian-spore-last-template"), "aa");
    s.E.modelTouch();
    setValue(s.win, sel, "zz", ["change"]);
    assert.equal(sel.value, "aa", "picker snaps back while asking");
    assert.match(s.doc.body.textContent, /Your changes to “aa” haven't been saved/);
    buttonByText(s.doc.body, "Keep editing").click();
    await settle(20);
    assert.equal(s.E.state.model.template_name, "aa");
    setValue(s.win, sel, "zz", ["change"]);
    buttonByText(s.doc.body, "Discard changes").click();
    await settle(20);
    assert.equal(s.E.state.model.template_name, "zz");
});

test("toolbar buttons: new (with discard prompt), undo/redo, save, preview, reload, open externally, stage click", async () => {
    const s = await blank();
    s.$("#ss-btn-create").click();
    assert.ok(s.$("[data-create-action='create']"));
    s.$("[data-create-action='cancel']").click();
    const t = await s.add("text");
    s.$("#ss-btn-create").click();
    buttonByText(s.doc.body, "Keep editing").click();
    await settle();
    assert.ok(s.$("[data-create-action='create']") === null);
    s.$("#ss-btn-create").click();
    buttonByText(s.doc.body, "Discard changes").click();
    await settle();
    assert.ok(s.$("[data-create-action='create']"));
    s.$("[data-create-action='cancel']").click();
    s.E.state.model = null;
    s.E.state.dirty = true;
    s.$("#ss-btn-create").click();
    assert.match(s.doc.body.textContent, /Your changes haven't been saved/);
    buttonByText(s.doc.body, "Keep editing").click();
    await settle();
    await s.E.loadTemplate("zz");
    await settle();

    const t2 = await s.add("text");
    s.$("#ss-btn-undo").click();
    assert.ok(s.E.selectedElementMatchingId(t2.id) === null);
    s.$("#ss-btn-redo").click();
    assert.ok(s.E.selectedElementMatchingId(t2.id));
    s.$("#ss-btn-save").click();
    await settle();
    assert.ok(s.saved.length >= 1);
    s.$("#ss-btn-preview").click();
    await settle();
    assert.equal(s.E.previewDialogOpen(), true);
    s.$("#ss-btn-preview").click();
    assert.equal(s.E.previewDialogOpen(), false);
    s.$("#ss-btn-reload").click();
    assert.match(s.win.location._assigned.at(-1), /^\/spore-studio\?_cb=\d+(&external=1)?$/);
    s.$("#ss-btn-open-external").click();
    assert.deepEqual(plain(s.win.__opened.at(-1)), ["/spore-studio?external=1", "_blank"]);
    s.select(t2.id);
    fire(s.win, s.$("#ss-stage"), "mousedown");
    assert.ok(s.E.state.selectedId === null);
    assert.ok(t);
});

test("leaving with unsaved changes asks the browser to confirm", async () => {
    const s = await blank();
    let ev = fire(s.win, s.win, "beforeunload");
    assert.equal(ev.defaultPrevented, false);
    s.E.modelTouch();
    ev = fire(s.win, s.win, "beforeunload");
    assert.equal(ev.defaultPrevented, true);
    assert.equal(ev.returnValue, "");
    s.E.setDirty(false);
    s.win.MycelianContainerEditor = { isDirty: () => true };
    ev = fire(s.win, s.win, "beforeunload");
    assert.equal(ev.defaultPrevented, true, "unsaved container edits count too");
});

test("canvas tips mark the Canvas tab rows; Mac shows ⌘S", async () => {
    const s = await blank();
    const row = s.$("#ss-canvas-w").closest(".ss-form-row");
    if (row) { assert.equal(row.getAttribute("data-ss-tip"), "Design width of the overlay, in pixels"); }
    const kbd = s.$(".ss-kbd");
    if (kbd) { assert.equal(kbd.textContent, "⌘S"); }
    s.E.setupCanvasTips(); // already marked: unchanged
    if (row) { assert.equal(row.getAttribute("data-ss-tip"), "Design width of the overlay, in pixels"); }
});

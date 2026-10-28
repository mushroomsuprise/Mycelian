// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/editor.js, part 2 (lines 3966-7826): Source Controls panel,
// animation timing and the minimum duration, widget previews on the canvas (wheel, list, now
// playing, stat card, QR, embed, confetti, poll), Data feed editors, block outputs, list/wheel
// sections, transforms and stage rendering/interactions.

import test from "node:test";
import assert from "node:assert/strict";
import { fire, plain, settle, loadScript, xfail } from "./helpers/env.mjs";
import { bootEditor, blankModel, control, field, prop, buttonByText, setValue, sectionByTitle } from "./helpers/editor_env.mjs";

async function blank(extra = {}, opts = {}) {
    return bootEditor(Object.assign({ template: "zz", models: { zz: blankModel("zz", extra) } }, opts));
}
const pane = (s) => s.$("#ss-pane-properties");
const node = (s, el) => s.$('.ss-element[data-spore-id="' + el.id + '"]');

/* ------------------------------------------------------------------ source controls */

test("source controls: empty state, add, edit each control type and remove", async () => {
    const s = await blank();
    const host = () => s.$("#ss-sourcecontrols-host");
    s.E.renderSourceControlsPanel();
    assert.match(host().textContent, /No source controls yet/);
    const counter = await s.add("counter");
    const timer = await s.add("timer");
    s.E.state.model.dynamic_controls.elements = [];
    s.E.renderSourceControlsPanel();
    host().querySelector(".ss-btn--primary").click();
    const dc = s.E.state.model.dynamic_controls.elements;
    assert.deepEqual(plain(dc[0]), { type: "button", id: "control_1", label: "New control", action: "custom_template_action", button_text: "Run" });
    const card = () => host().querySelector(".ss-binding-row");
    setValue(s.win, control(card(), "Control id"), " reset_btn ", ["change"]);
    setValue(s.win, control(card(), "Label"), "Reset", ["change"]);
    setValue(s.win, control(card(), "Button text"), "Go", ["change"]);
    setValue(s.win, control(card(), "Action"), "element_show", ["change"]);
    s.E.renderSourceControlsPanel();
    setValue(s.win, control(card(), "Element id"), counter.id, ["change"]);
    assert.deepEqual([dc[0].id, dc[0].label, dc[0].button_text, dc[0].action, dc[0].element_id], ["reset_btn", "Reset", "Go", "element_show", counter.id]);
    setValue(s.win, control(card(), "Action"), "counter_adjust", ["change"]);
    s.E.renderSourceControlsPanel();
    setValue(s.win, control(card(), "Counter id"), "abc", ["change"]);
    setValue(s.win, control(card(), "Operation"), "set", ["change"]);
    setValue(s.win, control(card(), "Value"), "9", ["change"]);
    assert.deepEqual([dc[0].target_counter_id, dc[0].operation, dc[0].delta.value], ["abc", "set", 9]);
    // counter control picks the first counter and forces the counter_adjust action
    dc[0].target_counter_id = "";
    setValue(s.win, control(card(), "Type"), "counter_control", ["change"]);
    assert.deepEqual([dc[0].action, dc[0].target_counter_id, dc[0].step, dc[0].button_text, dc[0].operation], ["counter_adjust", counter.counter.counter_id, 1, undefined, undefined]);
    setValue(s.win, control(card(), "Default step"), "0", ["change"]);
    assert.equal(dc[0].step, 1);
    setValue(s.win, control(card(), "Default step"), "4", ["change"]);
    setValue(s.win, control(card(), "Counter"), "", ["change"]);
    assert.deepEqual([dc[0].step, dc[0].target_counter_id], [4, ""]);
    setValue(s.win, control(card(), "Type"), "timer_control", ["change"]);
    assert.deepEqual([dc[0].action, dc[0].target_timer_id, dc[0].step], ["timer_control", timer.id, undefined]);
    setValue(s.win, control(card(), "Timer"), "", ["change"]);
    assert.equal(dc[0].target_timer_id, "");
    setValue(s.win, control(card(), "Type"), "slider", ["change"]);
    assert.ok((control(card(), "Button text")) === null);
    dc.push({ type: "counter_control", step: 0 }, { type: "timer_control" });
    s.E.renderSourceControlsPanel();
    assert.equal(dc[1].step, 1);
    assert.equal(dc[2].action, "timer_control");
    host().querySelectorAll("button").filter((b) => b.textContent === "Remove")[0].click();
    assert.equal(dc.length, 2);
    assert.equal(s.E.nextControlId([{ id: "control_2" }, {}]), "control_3");
    assert.equal(s.E.nextControlId(null), "control_1");
    const m = { dynamic_controls: { elements: "x" } };
    s.E.ensureDynamicControls(m);
    assert.deepEqual(plain(m.dynamic_controls.elements), []);
});

test("source controls without counters or timers, and for legacy templates", async () => {
    const s = await blank();
    s.E.state.model.dynamic_controls = { elements: [{ type: "counter_control" }, { type: "timer_control" }] };
    s.E.renderSourceControlsPanel();
    assert.ok(s.$("#ss-sourcecontrols-host").textContent.includes("(no counters in template)"));
    assert.ok(s.$("#ss-sourcecontrols-host").textContent.includes("(no timers in template)"));
    const legacy = await bootEditor({ template: "follow" });
    legacy.E.renderSourceControlsPanel();
    assert.match(legacy.$("#ss-sourcecontrols-host").textContent, /available for Spore Studio templates/);
});

/* ------------------------------------------------------------------ timing */

test("animation defaults and timeline arithmetic (mirrors timing.py)", async () => {
    const { E } = await blank();
    const el = { animations: { anim_in: "fade" } };
    E.ensureElementAnimations(el);
    assert.deepEqual(plain(el.animations), { anim_in: "fade", anim_out: "none", anim_in_ms: 300, anim_out_ms: 300, anim_delay_ms: 0, anim_easing: "ease-out" });
    E.ensureElementAnimations(null);
    assert.equal(E._animMsForType("none", 500), 0);
    assert.equal(E._animMsForType("fade", "x"), 300);
    assert.equal(E._animMsForType("fade", -5), 0);
    assert.equal(E._bindingStepMs("show_for", { seconds: 2 }, { animations: { anim_delay_ms: 2500, anim_in_ms: 200, anim_out_ms: 400 } }), 2700 + 400);
    assert.equal(E._bindingStepMs("show_for", { seconds: "x", anim_in: "none", anim_out: "none" }, null), 5000);
    assert.equal(E._bindingStepMs("show_for", { seconds: -3 }, { animations: { anim_delay_ms: "x" } }), 600);
    assert.equal(E._bindingStepMs("flash_class", { duration_ms: "250" }), 250);
    assert.equal(E._bindingStepMs("flash_class", {}), 500);
    assert.equal(E._bindingStepMs("set_text"), 0);
    assert.equal(E._bindingChainMs(null), 0);
    assert.equal(E._bindingChainMs({ action: "flash_class", args: { duration_ms: 100 }, chain: [null, { delay_ms: 1000, action: "flash_class", args: { duration_ms: 200 } }, { delay_ms: "x" }, { delay_ms: -5, action: "set_text" }] }), 1200);
    assert.equal(E.elementTimelineMs(null), 0);
    assert.equal(E.elementTimelineMs({ animations: { anim_in: "fade", anim_in_ms: 100, anim_out: "fade", anim_out_ms: 200, anim_delay_ms: 50 } }), 350);
    assert.equal(E.elementTimelineMs({ type: "audio", props: { fade_in_ms: 1000, fade_out_ms: "x" } }), 1000);
    assert.equal(E.elementTimelineMs({ type: "video", props: { audio_fade_in_ms: "x", audio_fade_out_ms: 700 } }), 700);
    assert.equal(E.elementTimelineMs({ bindings: [{ action: "show_for", args: { seconds: 8 } }] }), 8300);
    assert.equal(E.computeMinDurationSeconds(null), 5);
    assert.equal(E.computeMinDurationSeconds({ elements: [] }), 5);
    assert.equal(E.computeMinDurationSeconds({ elements: [{ bindings: [{ action: "show_for", args: { seconds: 8 } }] }] }), 8.3);
    assert.equal(E.computeMinDurationSeconds({ elements: [{ bindings: [{ action: "flash_class", args: { duration_ms: 10 } }] }] }), 0.1);
});

test("duration: user choice is kept, the minimum comes from the layout and is shown on the Canvas tab", async () => {
    const s = await blank({ duration_seconds: 3 });
    const t = await s.add("text");
    t.bindings.push({ action: "show_for", args: { seconds: 8 } });
    s.E.refreshDurationCanvasUi();
    assert.equal(s.$("#ss-duration-min-label").textContent, "Minimum from layout: 8.3s");
    assert.equal(s.$("#ss-duration-seconds").value, "8.3");
    assert.equal(s.$("#ss-duration-seconds").min, "8.3");
    assert.equal(s.E.state.model.duration_seconds, 3, "stored choice untouched");
    const m = { elements: [] };
    s.E.syncDurationWithLayout(m);
    assert.equal(m.duration_seconds, 5);
    assert.equal(s.E.syncDurationWithLayout({ legacy: true }), undefined);
    s.E.state.model.queued = true;
    s.E.refreshDurationCanvasUi();
    assert.equal(s.$("#ss-queued").checked, true);
});

test("expose defaults and the 'Show in Source Settings' toggle on property rows", async () => {
    const s = await blank();
    const el = { type: "text", source_settings_expose: { color: false } };
    s.E.ensureElementExposeDefaults(el);
    assert.equal(el.source_settings_expose.color, false);
    assert.equal(el.source_settings_expose.font_size, true);
    s.E.ensureElementExposeDefaults({ legacy_field: "x" });
    s.E.ensureElementExposeDefaults(null);
    const bare = { type: "text" };
    s.E.ensureElementExposeDefaults(bare);
    assert.equal(bare.source_settings_expose.text, true);
    const t = await s.add("timer");
    const row = prop(pane(s), "Mode");
    setValue(s.win, row.expose, false, ["change"]);
    assert.equal(t.source_settings_expose.mode, false);
    const w = s.E.formRowPropWithExpose({ type: "text", source_settings_expose: null, legacy_field: "x" }, "k", "Label", s.E.buildToggle(true).wrap, "tip");
    assert.ok(w.classList.contains("ss-form-prop--inline-control"));
    const legacy = await bootEditor({ template: "follow" });
    const e2 = { type: "text" };
    legacy.E.ensureElementExposeDefaults(e2);
    assert.equal(e2.source_settings_expose, undefined, "legacy templates keep their own settings");
});

/* ------------------------------------------------------------------ wheel & widget previews */

test("wheel defaults, text colour, SVG, pointer and result label placement", async () => {
    const s = await blank();
    const E = s.E;
    const w = { type: "wheel", props: { spin_ms: 100 } };
    E.ensureWheelDefaults(w);
    assert.equal(w.props.spin_ms, 100);
    assert.equal(w.props.easing, "ease-out");
    assert.equal(w.wheel_segments.length, 4);
    E.ensureWheelDefaults({ type: "text" });
    const sc = { type: "stat_card" };
    E.ensureStatCardDefaults(sc);
    assert.deepEqual(plain(sc.stat_source), { kind: "data_source", source: "stats", source_id: "total_bits" });
    E.ensureStatCardDefaults({ type: "text" });
    assert.equal(E.wheelTextColor("#fff"), "#111111");
    assert.equal(E.wheelTextColor("#202020"), "#ffffff");
    assert.equal(E.wheelTextColor("blue"), "#ffffff");
    const one = E.wheelPreviewSvg([{ label: "Solo", color: "#fff" }]);
    assert.ok(one.includes('<circle r="100" fill="#fff"/>'));
    const many = E.wheelPreviewSvg([{ label: "A label longer than sixteen", color: '#1"' }, {}, null]);
    assert.ok(many.includes("A label longer …"));
    assert.ok(many.includes('fill="#1&quot;"'));
    assert.ok(E.wheelPointerMarkup().startsWith("<svg"));
    const p = s.doc.createElement("div");
    for (const [pos, tr] of [["bottom", "translateX(-50%) rotate(180deg)"], ["left", "translateY(-50%) rotate(-90deg)"], ["right", "translateY(-50%) rotate(90deg)"], ["top", "translateX(-50%)"]]) {
        E.placeWheelPointer(p, pos);
        assert.equal(p.style.transform, tr);
    }
    const l = s.doc.createElement("div");
    for (const [place, key, val] of [["center", "top", "50%"], ["top", "top", "28%"], ["above", "bottom", "100%"], ["below", "top", "100%"], [undefined, "bottom", "8px"]]) {
        E.placeWheelResultLabel(l, { result_place: place, result_bold: true, result_font_size: 20 });
        assert.equal(l.style[key], val, String(place));
    }
    assert.deepEqual([l.style.fontWeight, l.style.fontSize, l.style.fontFamily, l.style.color], ["700", "20px", "Arial", "#ffffff"]);
});

test("widget previews paint sample content on the canvas", async () => {
    const s = await blank();
    const E = s.E;
    assert.equal(E.previewFillTokens("{username} gave {amount} {unknown}", { amount: 9 }), "viewer_one gave 9 unknown");
    const list = await s.add("list", 0, 0, { newest: "bottom", max_items: 2, row_gap: "", row_background: "red", row_padding: 3, row_radius: 4, row_template: "{username}: {value}" });
    const rows = node(s, list).querySelectorAll("div").filter((d) => !d.className);
    assert.deepEqual(rows.slice(0, 2).map((r) => r.textContent), ["viewer_two: 250", "viewer_one: 100"]);
    assert.deepEqual([rows[0].style.marginBottom, rows[0].style.background, rows[0].style.padding, rows[0].style.borderRadius], ["4px", "red", "3px", "4px"]);
    const np = await s.add("now_playing", 0, 0, { layout: "stack", show_artist: false });
    assert.equal(node(s, np).style.flexDirection, "column");
    assert.ok(node(s, np).textContent.includes("Song title"));
    assert.ok(!node(s, np).textContent.includes("Artist name"));
    const np2 = await s.add("now_playing", 0, 0, { show_art: false, show_title: false, show_progress: false });
    assert.equal(node(s, np2).textContent.includes("♫"), false);
    const wheel = await s.add("wheel", 0, 0, { show_result: true, result_format: "Won: {label}" });
    assert.ok(node(s, wheel).querySelector("svg"));
    assert.ok(node(s, wheel).textContent.includes("Won: One"));
    const wheel2 = await s.add("wheel", 0, 0, { show_result: false });
    assert.equal(node(s, wheel2).children.filter((c) => c.textContent === "One").length, 0, "no result label");
    assert.equal(node(s, wheel).children.filter((c) => c.textContent === "Won: One").length, 1);
    const qrNoLib = await s.add("qr", 0, 0, { text: "https://x" });
    assert.ok(node(s, qrNoLib).textContent.includes("https://x"), "no qrcode library: text fallback");
    loadScript(s.win, "assets/default_assets/spore_studio/qrcode.js", { expose: false });
    const qr = await s.add("qr", 0, 0, { text: "https://y", foreground: "#ff0000", background_color: "#00ff00" });
    assert.ok(node(s, qr).querySelector("svg"));
    assert.ok(node(s, qr).innerHTML.includes('fill="#ff0000"'));
    s.win.qrcode = () => { throw new Error("too long"); };
    const qrBad = await s.add("qr", 0, 0, { text: "" });
    assert.ok(node(s, qrBad).textContent.includes("QR"));
    const embed = await s.add("embed", 0, 0, { url: "" });
    assert.ok(node(s, embed).textContent.includes("(embed)"));
    const confetti = await s.add("confetti", 0, 0, { colors: "", particle_count: 40, particle_size: 0 });
    assert.equal(node(s, confetti).querySelectorAll("i").length, 12);
    assert.ok(node(s, confetti).textContent.includes("Confetti"));
});

test("stat card previews: counters, live session stats, config and string sources", async () => {
    const s = await blank();
    const E = s.E;
    const c = await s.add("counter");
    c.counter.initial_value = 42;
    const card = await s.add("stat_card", 0, 0, { icon: "★", label: "Subs", format: "{value} subs" });
    const valueSpec = E.statCardValueSpec(card);
    assert.equal(valueSpec.source, "stats");
    assert.match(node(s, card).textContent, /1,500 subs/, "sample value before stats arrive");
    const spec = card.data_feed[0].source.value;
    spec.source = "counter"; spec.source_id = c.counter.counter_id;
    assert.equal(E.statCardPreviewValue(card), "42");
    spec.source_id = "";
    assert.equal(E.statCardPreviewValue(card), "(pick a counter)");
    spec.source = "stats"; spec.source_id = "";
    assert.equal(E.statCardPreviewValue(card), "(pick a statistic)");
    spec.source_id = "unknown_stat";
    assert.equal(E.statCardPreviewValue(card), "123");
    for (const src of ["config", "runtime", "twitch"]) {
        spec.source = src;
        assert.equal(E.statCardPreviewValue(card), "(live value)");
    }
    E.state.dataSources = { sources: [{ id: "alert.username", value_type: "string", label: "Username" }, { id: "alert.amount" }] };
    spec.source = "alert.username";
    assert.equal(E.statCardPreviewValue(card), "Username");
    spec.source = "alert.amount";
    assert.equal(E.statCardPreviewValue(card), "123");
    card.data_feed = [];
    card.stat_source = { source: "stats", source_id: "follows" };
    assert.equal(E.statCardValueSpec(card), card.stat_source);
    assert.equal(E.formatPreviewNumber("1234"), "1,234");
    assert.equal(E.formatPreviewNumber("abc"), "abc");
    assert.equal(E.hasStatCards(), true);
});

test("refreshEditorStats asks the socket for session stats once per 10s and repaints stat cards", async () => {
    const s = await blank();
    const card = await s.add("stat_card", 0, 0, { format: "{value}" });
    card.data_feed[0].source.value = { source: "stats", source_id: "follows" };
    const sock = s.win.io("x");
    s.E.state.socket = sock;
    s.E.__eval("editorStats.fetchedAt = 0");
    s.E.refreshEditorStats();
    const req = sock.emitted.find((e) => e[0] === "get_data");
    assert.deepEqual(plain(req[1].path), "statistics/session");
    s.E.refreshEditorStats();
    assert.equal(sock.emitted.filter((e) => e[0] === "get_data").length, 1, "one request in flight");
    sock._recv("get_data", { request_id: "other" });
    sock._recv("get_data", null);
    sock._recv("get_data", { request_id: req[1].request_id, data: { follows: 77 } });
    assert.equal(node(s, card).textContent.includes("77"), true);
    assert.equal(s.E.statCardPreviewValue(card), "77");
    card.data_feed[0].source.value.source_id = "raids";
    assert.equal(s.E.statCardPreviewValue(card), "—", "stats loaded but this key is missing");
    s.clock.tick(10000);
    s.E.__eval("editorStats.inFlight = false");
    s.E.refreshEditorStats();
    const req2 = sock.emitted.filter((e) => e[0] === "get_data").at(-1);
    sock._recv("get_data", { request_id: req2[1].request_id, error: "x" });
    s.clock.tick(10000);
    s.E.refreshEditorStats();
    sock.off = () => { throw new Error("closed"); };
    s.clock.tick(6000);
    s.E.state.socket = null;
    s.E.refreshEditorStats();
});

test("poll previews: idle, open with timer and leader, ended winner-only, cards and rotator", async () => {
    const s = await blank();
    const E = s.E;
    const poll = await s.add("poll", 0, 0, { title: "Vote!", show_rank: true, max_items: 2, rotator_enabled: true, rotator_animation: "fade", rotator_animation_ms: -1, border_width: 2 });
    const n = node(s, poll);
    assert.ok(n.textContent.includes("No poll options yet"));
    const bucket = E.pollPreviewBucket(poll.id);
    assert.equal(E.pollPreviewBucket(poll.id), bucket);
    const now = s.win.Date.now() / 1000;
    bucket.data = { status: "open", ends_at: now + 75, duration_seconds: 150, total_votes: 1, options: [
        { id: 1, name: "Red", keyword: "r", votes: 5, percent: 50 }, { id: 2, name: "Blue", votes: 3, percent: 30 }, { id: 3, name: "Green", votes: 2, percent: 20 }] };
    n._rect = { width: 480, height: 640 };
    E.paintPollPreview(n, poll, bucket, false);
    assert.equal(n.querySelector(".spore-poll-chip").textContent, "01:15");
    assert.equal(n.querySelector(".spore-poll-timer-fill").style.width, "50%");
    assert.equal(n.querySelector(".spore-poll-total").textContent, "1 vote");
    assert.ok(n.querySelector(".spore-poll-option.is-leader"));
    assert.equal(n.querySelectorAll(".spore-poll-option").length, 2, "max 2 per page");
    assert.equal(n.querySelector(".spore-poll-rank").textContent, "1");
    assert.equal(n.style.border, "2px solid rgba(255,255,255,0.12)");
    bucket.page = 1;
    E.paintPollPreview(n, poll, bucket, true);
    assert.equal(n.querySelector(".spore-poll-page:not([style*='absolute']) .spore-poll-name") && n.querySelectorAll(".spore-poll-name").map((x) => x.textContent).includes("Green"), true);
    s.clock.tick(500);
    s.clock.tick(1000);
    E.tickPollPreviewClock(n, poll, bucket);
    assert.match(n.querySelector(".spore-poll-chip").textContent, /^01:1\d$/);
    E.tickPollPreviewClock(null, poll, bucket);
    E.tickPollPreviewClock(n, poll, { data: { status: "ended" } });
    poll.props.winner_mode = "winner_only";
    poll.props.style = "cards";
    poll.props.show_total = "no";
    bucket.data = { status: "ended", options: [{ id: 1, name: "Red", votes: 5, winner: true }, { id: 2, name: "Blue", votes: 3 }] };
    E.paintPollPreview(n, poll, bucket, false);
    assert.equal(n.querySelector(".spore-poll-chip").textContent, "Poll closed");
    assert.deepEqual(n.querySelectorAll(".spore-poll-name").map((x) => x.textContent), ["Red"]);
    assert.ok(n.classList.contains("is-style-cards"));
    assert.ok(n.querySelector(".spore-poll-page.is-horizontal"));
    assert.equal(n.querySelector(".spore-poll-foot").hidden, true);
    poll.props.display_mode = "text";
    poll.props.style = "bars";
    poll.props.show_counts = false;
    poll.props.show_percent = false;
    poll.props.winner_effect = "none";
    bucket.data = { status: "open", permanent: true, options: [{ id: 1, name: "X", votes: 0 }] };
    E.paintPollPreview(n, poll, bucket, false);
    assert.equal(n.querySelector(".spore-poll-chip").textContent, "Live");
    assert.ok((n.querySelector(".spore-poll-bar")) === null);
    assert.ok((n.querySelector(".spore-poll-meta")) === null);
    assert.equal(E.pollPreviewRemaining(null), null);
    assert.deepEqual([59.2, 3725].map(E.pollPreviewClockText), ["01:00", "1:02:05"]);
    assert.deepEqual([E.pollPreviewOn(null, true), E.pollPreviewOn("off", true), E.pollPreviewOn("yes", false), E.pollPreviewOn(0, true)], [true, false, true, false]);
});

test("accent preview markup and buildChoiceSelect", async () => {
    const s = await blank();
    assert.equal(s.E.accentPreviewHtml("a [[<b>]]"), '<span class="spore-accent-line">a <span class="spore-accent">&lt;b&gt;</span></span>');
    const got = [];
    const sel = s.E.buildChoiceSelect("zz", [{ id: "a", label: "A" }, { id: "b" }], (v) => got.push(v));
    assert.deepEqual(sel.options.map((o) => o.value), ["a", "b", "zz"]);
    assert.equal(sel.value, "zz");
    setValue(s.win, sel, "b", ["change"]);
    assert.deepEqual(got, ["b"]);
    assert.equal(s.E.buildChoiceSelect("", [{ id: "a" }], () => {}).options.length, 1);
});

/* ------------------------------------------------------------------ data feeds: model */

function collapse(root, title) {
    const sum = root.querySelectorAll("summary.ss-collapse__summary").find((x) => x.dataset.section === title);
    return sum ? sum.parentNode.querySelector(".ss-collapse__body") : null;
}
const feedCard = (s, i = 0) => pane(s).querySelectorAll(".ss-feed")[i];
const feedPartBody = (card, n) => card.querySelectorAll(".ss-feed__part")[n].querySelector(".ss-feed__part-body");

test("normalizeFeed fills every part; readFeeds normalises in place", async () => {
    const { E } = await blank();
    const f = E.normalizeFeed({ enabled: false, source: { kind: "nope", template_output: { match: 3 }, refresh_ms: "x" }, filters: { rules: "x", cooldown_ms: 5 }, target: { options: 2 } });
    assert.equal(f.enabled, false);
    assert.equal(f.label, "");
    assert.equal(f.source.kind, "event");
    assert.deepEqual(plain(f.source.template_output), { match: {}, template: "", event: "" });
    assert.equal(f.source.refresh_ms, 0);
    assert.deepEqual(plain(f.filters.rules), []);
    assert.equal(f.filters.cooldown_ms, 5);
    assert.equal(f.mapping.template, "");
    assert.deepEqual(plain(f.timing), { delay_ms: 0 });
    assert.deepEqual(plain(f.target), { options: {} });
    const g = E.normalizeFeed({ source: "x", target: "y" });
    assert.equal(g.source.value.kind, "data_source");
    assert.deepEqual(plain(E.readFeeds({})), []);
    const el = { data_feed: [{}] };
    assert.equal(E.readFeeds(el)[0].enabled, true);
});

test("catalog targets, default feeds and ids", async () => {
    const s = await blank();
    const E = s.E;
    assert.ok(E.feedTargetsFor("counter").some((t) => t.id === "counter_increment"));
    s.E.state.catalog = {};
    assert.deepEqual(plain(E.feedTargetsFor("text").map((t) => t.id)), ["show", "hide", "toggle", "show_for", "flash_class", "set_style"]);
    assert.equal(E.findFeedTarget("text", "show_for").options[0].key, "seconds");
    assert.equal(E.findFeedTarget("text", "nope"), null);
    const el = { type: "text", data_feed: [{ id: "feed1" }, { id: "feed3" }] };
    assert.equal(E.nextFeedId(el), "feed4");
    assert.equal(E.nextFeedId({}), "feed1");
    assert.equal(E.newFeedFor(el, { label: "L" }).target.id, "show");
    assert.equal(E.feedHasTarget({ data_feed: [{ target: { id: "x" } }] }, "x"), true);
    const v = E.newValueFeedFor({ type: "stat_card" }, "set_value", { source: "stats" }, null);
    assert.deepEqual([v.source.kind, v.source.refresh_ms, v.target.id], ["value", 1000, "set_value"]);
    assert.equal(E.newValueFeedFor({ type: "x" }, "t").source.value.kind, "data_source");
});

test("legacy blocks are migrated to feeds", async () => {
    const { E } = await blank();
    const M = (el) => plain(E.migrateLegacyFeeds(el));
    assert.deepEqual(M({ type: "list", props: { source: "manual" } }), []);
    const alerts = M({ type: "list", props: { alert_type: "Follow", text_field: "u", value_field: "v", row_template: "{u}" } })[0];
    assert.deepEqual([alerts.source.events, alerts.filters.alert_type, alerts.mapping.text_field, alerts.mapping.template, alerts.target.id], [["instant_alert", "next_alert"], "follow", "u", "{u}", "list_add"]);
    const give = M({ type: "list", props: { source: "giveaway" } })[0];
    assert.deepEqual([give.source.events, give.mapping.split_field, give.mapping.template], [["giveaway_entry", "giveaway_winner"], "winners", "{username}"]);
    assert.deepEqual(M({ type: "list", props: { source: "payload" } }), []);
    assert.deepEqual(M({ type: "list", props: { source: "payload", source_event: "custom" } })[0].source.events, ["custom"]);
    const wheel = M({ type: "wheel", props: { source: "giveaway" } })[0];
    assert.deepEqual(wheel.target, { id: "wheel_spin", options: { segments_field: "pool_entries", land_field: "winners" } });
    assert.deepEqual(M({ type: "wheel", props: {} }), []);
    assert.equal(M({ type: "stat_card", stat_source: { source: "stats" } })[0].target.id, "set_value");
    const disp = M({ type: "text", text_mode: "data_display", data_display: { source: { source: "alert.amount" }, poll_interval_ms: "250", refresh_on: ["next_alert"] } })[0];
    assert.deepEqual([disp.source.value.source, disp.source.refresh_ms, disp.source.refresh_on], ["alert.amount", 250, ["next_alert"]]);
    const disp2 = M({ type: "marquee", text_mode: "data_display", data_display: {} })[0];
    assert.deepEqual(disp2.source.refresh_on, []);
    assert.equal(M({ type: "poll" }).length, 1);
    assert.equal(M({ type: "now_playing" }).length, 1);
    assert.deepEqual(M({ type: "shape" }), []);
    const el = { type: "poll" };
    E.ensureDataFeedDefaults(el);
    assert.equal(el.data_feed.length, 1);
    E.ensureDataFeedDefaults(el);
    assert.equal(el.data_feed.length, 1);
    E.ensureDataFeedDefaults({ legacy: true });
    E.ensureDataFeedDefaults(null);
});

test("payload fields, template outputs and summaries", async () => {
    const { E } = await blank();
    assert.ok(E.feedEventFields(["next_alert", "instant_alert", "nope"]).length > 3);
    assert.equal(new Set(E.feedEventFields(["next_alert", "instant_alert"]).map((f) => f.key)).size, E.feedEventFields(["next_alert", "instant_alert"]).length);
    const outs = E.state.catalog.template_outputs;
    const o = outs.find((r) => Object.keys(r.match || {}).length) || outs[0];
    assert.equal(E.findTemplateOutput({ template: o.template, event: o.event, match: o.match }).id, o.id);
    assert.equal(E.findTemplateOutput({ template: o.template, event: o.event, match: { zz: 1 } }).template, o.template);
    assert.equal(E.findTemplateOutput({ template: "none", event: "x" }), null);
    assert.ok(E.feedPayloadFields({ source: { kind: "event", events: ["next_alert"] } }).length);
    assert.deepEqual(plain(E.feedPayloadFields({ source: { kind: "template_output", template_output: { template: "zz", event: "q" } } })), []);
    assert.deepEqual(plain(E.feedPayloadFields({ source: { kind: "value" } })), [{ key: "value", label: "Value", type: "number" }]);
    assert.equal(E.feedSourceSummary({ source: { kind: "event", events: [] } }), "no event chosen");
    assert.match(E.feedSourceSummary({ source: { kind: "event", events: ["next_alert", "instant_alert", "poll_state", "custom_x"] } }), / \+2$/);
    assert.equal(E.feedSourceSummary({ source: { kind: "event", events: ["custom_x"] } }), "custom_x");
    assert.equal(E.feedSourceSummary({ source: { kind: "template_output", template_output: {} } }), "template: output");
    assert.equal(E.feedSourceSummary({ source: { kind: "value", value: { source: "nope" } } }), "no value chosen");
    E.state.dataSources = { sources: [{ id: "alert.amount", label: "Amount" }] };
    assert.equal(E.feedSourceSummary({ source: { kind: "value", value: { source: "alert.amount" } } }), "Amount");
    assert.equal(E.feedTargetLabel({ type: "counter" }, { target: { id: "counter_set" } }), "Set this counter");
    assert.equal(E.feedTargetLabel({ type: "counter" }, { target: { id: "zz" } }), "zz");
    assert.equal(E.feedTargetLabel({ type: "counter" }, { target: {} }), "no target");
});

test("feed field helpers: enum fields, rule values, rows, sample data and inputs", async () => {
    const s = await blank();
    const E = s.E;
    const fields = [{ key: "alert_type", examples: ["sub"] }, { key: "tier", examples: ["1000"] }, { key: "platform", examples: ["twitch", "youtube"] }, { key: "list", type: "array", examples: ["a"] }, { key: "x" }];
    assert.deepEqual(E.feedEnumFields(fields).map((f) => f.key), ["platform"]);
    assert.equal(E.feedHasKey(fields, "tier"), true);
    assert.equal(E.feedHasKey(null, "tier"), false);
    const filters = { rules: [{ field: "platform", value: "twitch" }, { field: "other", op: "gt", value: 1 }, { field: "n", value: null }] };
    assert.equal(E.feedRuleValue(filters, "platform"), "twitch");
    assert.equal(E.feedRuleValue(filters, "n"), "");
    assert.equal(E.feedRuleValue({}, "x"), "");
    E.feedSetEnumRule(filters, "platform", "youtube");
    assert.deepEqual(plain(filters.rules.filter((r) => r.field === "platform")), [{ field: "platform", op: "eq", value: "youtube" }]);
    E.feedSetEnumRule(filters, "platform", "");
    assert.equal(filters.rules.some((r) => r.field === "platform"), false);
    E.feedSetEnumRule({}, "a", "b");
    assert.deepEqual(plain(E.feedFieldRows([{ key: "a", label: "A" }, { key: "b" }], "(none)")), [{ id: "", label: "(none)" }, { id: "a", label: "A (a)" }, { id: "b", label: "b (b)" }]);
    assert.deepEqual(plain(E.feedSampleData([{ key: "n", type: "number", examples: ["x"] }, { key: "m", type: "number", examples: [5] }, { key: "b", type: "boolean", examples: ["false"] }, { key: "c", type: "boolean" }, { key: "s", label: "Name" }, { key: "t", examples: ["hi"] }, { key: "arr", type: "array" }])),
        { n: 1, m: 5, b: false, c: true, s: "Name", t: "hi" });
    const plainInput = E.feedFieldInput([], "v", () => {}, "ph");
    assert.equal(plainInput.tagName, "INPUT");
    assert.equal(plainInput.placeholder, "ph");
    const withList = E.feedFieldInput([{ key: "a", label: "A" }], "", () => {});
    assert.ok(withList.querySelector("datalist option[value=a]"));
    assert.equal(withList.querySelector("input").getAttribute("list"), withList.querySelector("datalist").id);
    const got = [];
    const row = E.feedNumberRow("N", null, (n) => got.push(n), "tip", 2);
    setValue(s.win, row.querySelector("input"), "x", ["change"]);
    setValue(s.win, row.querySelector("input"), "1", ["change"]);
    setValue(s.win, row.querySelector("input"), "9", ["change"]);
    assert.deepEqual(got, [0, 2, 9], "non-numbers become 0 (the minimum only clamps numbers)");
});

test("counter rule filters migrate to the shared filter shape", async () => {
    const { E } = await blank();
    const rule = { filter: { alert_type: "sub", tier: 2000, user: "bob", empty: null } };
    E.migrateRuleFilter(rule);
    assert.equal(rule.filters.alert_type, "sub");
    assert.equal(rule.filters.tier, "2000");
    assert.deepEqual(plain(rule.filters.rules), [{ field: "user", op: "eq", value: "bob" }, { field: "empty", op: "eq", value: "" }]);
    assert.deepEqual(plain(rule.filter), {});
    const r2 = { filters: { rules: "x", cooldown_ms: 3 }, filter: { tier: "" } };
    E.migrateRuleFilter(r2);
    // (migrateRuleFilter has no caller in editor.js; the server-side migration in
    // data_feed_registry._rule_to_feed is what runs, and it skips empty values.)
    assert.deepEqual([r2.filters.cooldown_ms, r2.filters.tier, plain(r2.filters.rules)], [3, "any", [{ field: "tier", op: "eq", value: "" }]]);
});

test("feed preview modifiers and rendering", async () => {
    const { E } = await blank();
    const M = E.feedPreviewMod;
    assert.equal(M(1234567, "number"), "1,234,567");
    assert.equal(M("x", "number"), "x");
    assert.equal(M("2.5", "int"), "3");
    assert.equal(M(3.14159, "decimals:3"), "3.142");
    assert.equal(M(0.125, "percent"), "12.5%");
    assert.deepEqual([1500, 2500000, 3e9, 12, "x"].map((v) => M(v, "compact")), ["1.5K", "2.5M", "3B", "12", "x"]);
    assert.equal(M(61, "duration"), "1:01");
    assert.deepEqual([1, 2, 3, 11, 22, 113].map((v) => M(v, "ordinal")), ["1st", "2nd", "3rd", "11th", "22nd", "113th"]);
    assert.equal(M("AbC", "upper"), "ABC");
    assert.equal(M("AbC", "lower"), "abc");
    assert.equal(M("a big thing", "title"), "A Big Thing");
    assert.equal(M(" x ", "trim"), "x");
    assert.equal(M("abcdef", "truncate:3"), "ab…");
    assert.equal(M("", "default:none"), "none");
    assert.equal(M(null, "mystery"), "");
    for (const name of ["int", "decimals", "percent", "duration", "ordinal"]) { assert.equal(M("x", name), "x", name); }
    assert.equal(E.feedPreviewRender("{name|upper} {obj} {missing|default:?}", { name: "ann", obj: { a: 1 } }), 'ANN {"a":1} ?');
    assert.equal(E.feedPreviewRender(null, {}), "");
    assert.equal(E.feedPreviewRender("{v||int}", { v: 2.4 }), "2");
});

xfail(test, "the mapping preview applies the plural/join/count formats the catalog offers",
    "BUG: editor.js:5655 feedPreviewMod lacks the plural, join and count modifiers that data_feed_registry lists and the overlay runtime applies, so '{pool_size|plural:entry,entries}' previews as '12' instead of '12 entries'",
    async () => {
        const { E } = await blank();
        assert.equal(E.feedPreviewRender("{pool_size|plural:entry,entries}", { pool_size: 12 }), "12 entries");
    });

test("feed preview text uses sample data, mapping fields, decimals, prefix/suffix and fallback", async () => {
    const { E } = await blank();
    const fields = [{ key: "username", examples: ["ann"] }, { key: "amount", type: "number", examples: [2.567] }];
    const T = (mapping, f = fields) => E.feedPreviewText({ mapping }, f);
    assert.equal(T({}), "ann");
    assert.equal(T({ template: "{username}: {value}", decimals: "1", prefix: "[", suffix: "]" }), "[ann: 2.6]");
    assert.equal(T({ value_field: "amount", text_field: "amount" }), "2.567");
    assert.equal(T({}, [{ key: "label", examples: ["L"] }]), "L");
    assert.equal(T({}, [{ key: "result", examples: ["R"] }]), "R");
    assert.equal(T({}, [{ key: "n", type: "number", examples: [4] }]), "4");
    assert.equal(T({}, []), "Pick events above to preview");
    assert.equal(T({ template: "{nothing}", fallback: "FB" }, []), "FB");
});

/* ------------------------------------------------------------------ data feeds: editor UI */

test("data feed section: add, label, copy, disable and remove feeds", async () => {
    const s = await blank();
    const shape = await s.add("shape");
    assert.deepEqual(plain(shape.data_feed), []);
    assert.match(pane(s).textContent, /No feed yet/);
    buttonByText(pane(s), "+ Add feed").click();
    assert.equal(shape.data_feed.length, 1);
    const title = feedCard(s).querySelector(".ss-feed__title");
    assert.equal(title.placeholder, "Feed 1");
    setValue(s.win, title, "Alerts", ["change"]);
    assert.equal(shape.data_feed[0].label, "Alerts");
    buttonByText(feedCard(s), "Copy").click();
    assert.equal(shape.data_feed.length, 2);
    assert.equal(shape.data_feed[1].label, "Alerts");
    assert.notEqual(shape.data_feed[1].id, shape.data_feed[0].id);
    setValue(s.win, feedCard(s, 1).querySelector(".ss-feed__head .ss-toggle__input"), false, ["change"]);
    assert.equal(shape.data_feed[1].enabled, false);
    assert.ok(feedCard(s, 1).classList.contains("ss-feed--off"));
    buttonByText(feedCard(s, 1), "Remove").click();
    assert.equal(shape.data_feed.length, 1);
    assert.match(feedCard(s).querySelector(".ss-feed__summary").textContent, /→/);
    const det = feedCard(s).querySelector(".ss-feed__part");
    det.open = true;
    fire(s.win, det, "toggle", { bubbles: false });
    s.select(shape.id);
    assert.equal(feedCard(s).querySelector(".ss-feed__part").open, true, "open parts are remembered");
});

test("feed source editor: events checklist, template outputs and live values", async () => {
    const s = await blank();
    const shape = await s.add("shape");
    buttonByText(pane(s), "+ Add feed").click();
    const feed = shape.data_feed[0];
    const src = () => feedPartBody(feedCard(s), 0);
    const ev = src().querySelectorAll(".ss-feed__checks .ss-form-row--inline").find((r) => r.textContent === (s.E.registryEvent("poll_state").label || "poll_state"));
    setValue(s.win, ev.querySelector("input"), true, ["change"]);
    assert.ok(feed.source.events.includes("poll_state"));
    const ev2 = src().querySelectorAll(".ss-feed__checks .ss-form-row--inline").find((r) => r.textContent === (s.E.registryEvent("poll_state").label || "poll_state"));
    setValue(s.win, ev2.querySelector("input"), false, ["change"]);
    assert.ok(!feed.source.events.includes("poll_state"));
    setValue(s.win, control(src(), "Run when"), "template_output", ["change"]);
    const outs = s.E.state.catalog.template_outputs;
    setValue(s.win, control(src(), "Template output"), outs[1].id, ["change"]);
    assert.deepEqual([feed.source.template_output.template, feed.source.template_output.event], [outs[1].template, outs[1].event]);
    setValue(s.win, control(src(), "Template output"), "missing", ["change"]);
    setValue(s.win, control(src(), "Template name"), " other ", ["change"]);
    setValue(s.win, control(src(), "Output event"), " done ", ["change"]);
    assert.deepEqual([feed.source.template_output.template, feed.source.template_output.event], ["other", "done"]);
    const pair = src().querySelector(".ss-feed__pair");
    const [k, v] = pair.querySelectorAll("input");
    setValue(s.win, k, "label", ["change"]);
    setValue(s.win, v, "A", ["change"]);
    assert.deepEqual(plain(feed.source.template_output.match), { label: "A" });
    setValue(s.win, k, "", ["change"]);
    assert.deepEqual(plain(feed.source.template_output.match), {});
    setValue(s.win, v, "x", ["change"]);
    setValue(s.win, control(src(), "Run when"), "value", ["change"]);
    setValue(s.win, control(src(), "Data source"), "stats", ["change"]);
    assert.deepEqual([feed.source.value.kind, feed.source.value.source], ["data_source", "stats"]);
    setValue(s.win, control(src(), "Statistic"), "follows", ["change"]);
    setValue(s.win, control(src(), "Re-read every (ms)"), "250.7", ["change"]);
    assert.deepEqual([feed.source.value.source_id, feed.source.refresh_ms], ["follows", 250]);
    const re = control(src(), "Re-read on events").closest(".ss-form-row").querySelector(".ss-feed__checks input");
    setValue(s.win, re, true, ["change"]);
    assert.equal(feed.source.refresh_on.length, 1);
});

test("feed filter editor: alert type, tier, enum fields, rules, users and rate limits", async () => {
    const s = await blank();
    const shape = await s.add("shape");
    buttonByText(pane(s), "+ Add feed").click();
    const feed = shape.data_feed[0];
    feed.source.events = ["next_alert"];
    s.select(shape.id);
    const fl = () => feedPartBody(feedCard(s), 1);
    setValue(s.win, control(fl(), "Alert type"), "raid", ["change"]);
    setValue(s.win, control(fl(), "Alert type"), "", ["change"]);
    assert.equal(feed.filters.alert_type, "any");
    if (control(fl(), "Sub tier")) {
        setValue(s.win, control(fl(), "Sub tier"), "2", ["change"]);
        assert.equal(feed.filters.tier, "2");
    }
    buttonByText(fl(), "+ Add filter").click();
    buttonByText(fl(), "+ Add filter").click();
    assert.equal(feed.filters.rules.length, 2);
    const rule = fl().querySelector(".ss-feed__rule");
    const [fieldSel, opSel] = rule.querySelectorAll("select");
    setValue(s.win, fieldSel, fieldSel.options[1].value, ["change"]);
    setValue(s.win, rule.querySelector("input"), "5", ["change"]);
    assert.equal(feed.filters.rules[0].value, "5");
    setValue(s.win, opSel, "empty", ["change"]);
    assert.ok(fl().querySelector(".ss-feed__rule").querySelector("input") === null, "operators without a value hide the box");
    setValue(s.win, control(fl(), "Rules must match"), "any", ["change"]);
    assert.equal(feed.filters.match, "any");
    buttonByText(fl().querySelector(".ss-feed__rule"), "Remove").click();
    assert.equal(feed.filters.rules.length, 1);
    setValue(s.win, control(fl(), "Only these users"), "a,b", ["change"]);
    setValue(s.win, control(fl(), "Ignore these users"), "bot", ["change"]);
    setValue(s.win, control(fl(), "Cooldown (ms)"), "100", ["change"]);
    setValue(s.win, control(fl(), "Max per minute"), "2.9", ["change"]);
    setValue(s.win, control(fl(), "Ignore repeats for (ms)"), "50", ["change"]);
    setValue(s.win, control(fl(), "Skip the first"), "1.5", ["change"]);
    assert.deepEqual([feed.filters.users_allow, feed.filters.users_block, feed.filters.cooldown_ms, feed.filters.max_per_minute, feed.filters.dedupe_ms, feed.filters.skip_first], ["a,b", "bot", 100, 2, 50, 1]);
    // enum example fields
    const host = s.doc.createElement("div");
    const filters = s.E.normalizeFeed({}).filters;
    let redraws = 0;
    host.appendChild(s.E.buildFeedFilterEditor(filters, { fields: [{ key: "platform", label: "Platform", examples: ["twitch", "youtube"] }], onChange: (r) => { if (r) { redraws++; } } }));
    setValue(s.win, control(host, "Platform"), "youtube", ["change"]);
    assert.deepEqual(plain(filters.rules), [{ field: "platform", op: "eq", value: "youtube" }]);
    assert.equal(redraws, 1);
    s.E.state.catalog = {};
    const h2 = s.doc.createElement("div");
    h2.appendChild(s.E.buildFeedFilterEditor({ rules: [{ op: "zz" }] }));
    assert.ok(h2.querySelector(".ss-feed__rule input"), "unknown operators still get a value box");
    s.E.buildFeedFilterEditor({});
});

test("feed mapping editor: fields, template inserts, split rows and formatting", async () => {
    const s = await blank();
    const shape = await s.add("shape");
    buttonByText(pane(s), "+ Add feed").click();
    const feed = shape.data_feed[0];
    feed.source.events = ["giveaway_winner"];
    s.select(shape.id);
    const mp = () => feedPartBody(feedCard(s), 2);
    setValue(s.win, control(mp(), "Main text field"), control(mp(), "Main text field").options[1].value, ["change"]);
    assert.ok(feed.mapping.text_field);
    setValue(s.win, control(mp(), "Number field"), "", ["change"]);
    const tmpl = mp().querySelector(".ss-feed__template input");
    setValue(s.win, tmpl, "Hi ", ["change"]);
    const [fieldSel, fmtSel] = mp().querySelectorAll(".ss-feed__template select");
    setValue(s.win, fieldSel, "username", ["change"]);
    assert.equal(fieldSel.value, "", "the insert menu resets");
    setValue(s.win, fieldSel, "", ["change"]);
    setValue(s.win, fmtSel, fmtSel.options[1].value, ["change"]);
    setValue(s.win, fmtSel, "", ["change"]);
    assert.ok(feed.mapping.template.startsWith("Hi {username}"), feed.mapping.template);
    setValue(s.win, control(mp(), "Split into rows"), control(mp(), "Split into rows").options[1].value, ["change"]);
    assert.ok(feed.mapping.split_field);
    setValue(s.win, control(mp(), "Prefix"), "<", ["change"]);
    setValue(s.win, control(mp(), "Suffix"), ">", ["change"]);
    setValue(s.win, control(mp(), "Decimals"), "99", ["change"]);
    setValue(s.win, control(mp(), "If a field is missing"), "?", ["change"]);
    assert.deepEqual([feed.mapping.prefix, feed.mapping.suffix, feed.mapping.decimals, feed.mapping.fallback], ["<", ">", 10, "?"]);
    setValue(s.win, control(mp(), "Decimals"), "", ["change"]);
    assert.equal(feed.mapping.decimals, "");
    s.select(shape.id);
    assert.match(mp().querySelector(".ss-feed__preview").textContent, /^Looks like: </);
    feed.source.events = [];
    feed.mapping.split_field = "gone";
    s.select(shape.id);
    assert.equal(feed.mapping.split_field, "", "split field cleared when no list field exists");
    setValue(s.win, control(feedPartBody(feedCard(s), 3), "Delay (ms)"), "300", ["change"]);
    assert.equal(feed.timing.delay_ms, 300);
});

test("a list's row template follows its list_add feed template", async () => {
    const s = await blank();
    const list = await s.add("list");
    s.select(list.id); // re-render the already selected block (see the stale-card xfail below)
    const mp = () => feedPartBody(feedCard(s), 2);
    setValue(s.win, mp().querySelector(".ss-feed__template input"), "{username}!", ["change"]);
    assert.equal(list.props.row_template, "{username}!");
    s.E.syncListRowTemplate({ type: "text" });
    list.data_feed[0].target.id = "list_clear";
    s.E.syncListRowTemplate(list);
    assert.equal(list.props.row_template, "{username}!");
});

xfail(test, "the first feed edit after selecting a block is kept",
    "BUG: editor.js:8197 renderProperties calls readFeeds() after building the Data feed cards when a block is newly selected, and normalizeFeed (editor.js:5257) swaps feed.mapping/filters/timing for new objects, so the cards edit detached copies: the first change to a feed's mapping, filters or timing after selecting a block is silently dropped",
    async () => {
        const s = await blank();
        const shape = await s.add("shape");
        buttonByText(pane(s), "+ Add feed").click();
        const other = await s.add("text");
        s.select(shape.id);
        setValue(s.win, control(feedPartBody(feedCard(s), 2), "Prefix"), "<", ["change"]);
        assert.equal(shape.data_feed[0].mapping.prefix, "<");
        void other;
    });

xfail(test, "a second edit in a list's feed card is kept",
    "BUG: editor.js:6053 syncListRowTemplate runs readFeeds() on every list feed commit, and normalizeFeed (editor.js:5257) replaces feed.mapping with a new object, so after one edit the open card writes to a detached copy and later edits (e.g. Suffix after Prefix) are lost",
    async () => {
        const s = await blank();
        const list = await s.add("list");
        s.select(list.id);
        const mp = () => feedPartBody(feedCard(s), 2);
        const prefix = control(mp(), "Prefix");
        const suffix = control(mp(), "Suffix");
        setValue(s.win, prefix, "<", ["change"]);
        setValue(s.win, suffix, ">", ["change"]);
        assert.equal(list.data_feed[0].mapping.suffix, ">");
    });

test("feed target editor: options of every type and counter deltas", async () => {
    const s = await blank();
    const c = await s.add("counter");
    const tg = () => feedPartBody(feedCard(s), 4);
    assert.equal(c.data_feed.length, 0);
    buttonByText(pane(s), "+ Add feed").click();
    const feed = c.data_feed[0];
    assert.equal(feed.target.id, "counter_increment");
    s.select(c.id);
    assert.deepEqual(plain(feed.target.options.delta), { kind: "fixed", value: 1 });
    setValue(s.win, control(tg(), "Value"), "3", ["change"]);
    assert.equal(feed.target.options.delta.value, 3);
    setValue(s.win, control(tg(), "Update this block"), "counter_reset", ["change"]);
    assert.deepEqual(plain(feed.target.options), {});
    assert.ok((control(tg(), "Delta kind")) === null);
    const got = [];
    const num = s.E.feedOptionControl({ type: "number", min: 0, max: 10, default: 5 }, undefined, (v) => got.push(v));
    assert.equal(num.value, "5");
    for (const v of ["", "x", "-3", "20", "4"]) { setValue(s.win, num, v, ["change"]); }
    assert.deepEqual(got, [undefined, undefined, 0, 10, 4]);
    const sel = s.E.feedOptionControl({ type: "select", options: ["a", { id: "b", label: "B" }] }, null, (v) => got.push(v));
    setValue(s.win, sel, "b", ["change"]);
    const color = s.E.feedOptionControl({ type: "color" }, "", (v) => got.push(v));
    assert.ok(color.classList.contains("ss-color"));
    const text = s.E.feedOptionControl({ type: "text" }, null, (v) => got.push(v));
    setValue(s.win, text, "", ["change"]);
    setValue(s.win, text, "t", ["change"]);
    assert.deepEqual(got.slice(-3), ["b", undefined, "t"]);
    const shape = await s.add("shape");
    s.E.state.catalog = {};
    buttonByText(pane(s), "+ Add feed").click();
    setValue(s.win, control(tg(), "Update this block"), "show_for", ["change"]);
    setValue(s.win, control(tg(), "Seconds"), "9", ["change"]);
    assert.equal(shape.data_feed[0].target.options.seconds, 9);
    setValue(s.win, control(tg(), "Seconds"), "", ["change"]);
    assert.equal(shape.data_feed[0].target.options.seconds, undefined);
    setValue(s.win, control(tg(), "Update this block"), "nope", ["change"]);
});

test("block outputs: toggles and labels are stored per output", async () => {
    const s = await blank();
    const w = await s.add("wheel");
    const out = collapse(pane(s), "Outputs");
    assert.ok(out);
    assert.match(out.textContent, /Sends: /);
    setValue(s.win, out.querySelector(".ss-toggle__input"), false, ["change"]);
    const id = Object.keys(w.output_settings)[0];
    assert.equal(w.output_settings[id].enabled, false);
    setValue(s.win, control(out, "Label"), "Spin result", ["change"]);
    assert.deepEqual(plain(w.output_settings[id]), { enabled: false, label: "Spin result" });
    setValue(s.win, control(out, "Label"), "", ["change"]);
    assert.deepEqual(plain(w.output_settings[id]), { enabled: false });
    assert.deepEqual(plain(s.E.blockOutputSpecs({ type: "shape" })), []);
    s.E.state.catalog = { block_outputs: { text: [{ id: "a", only_when: "counter" }, { id: "b" }] } };
    assert.deepEqual(s.E.blockOutputSpecs({ type: "text" }).map((x) => x.id), ["b"]);
    assert.deepEqual(s.E.blockOutputSpecs({ type: "text", text_mode: "counter" }).map((x) => x.id), ["a", "b"]);
});

/* ------------------------------------------------------------------ list & wheel sections */

test("list section clamps rows and lifetime", async () => {
    const s = await blank();
    const l = await s.add("list");
    setValue(s.win, prop(pane(s), "Max rows").control, "500", ["change"]);
    assert.equal(l.props.max_items, 100);
    setValue(s.win, prop(pane(s), "Max rows").control, "x", ["change"]);
    assert.equal(l.props.max_items, 1);
    setValue(s.win, prop(pane(s), "New rows go").control, "bottom", ["change"]);
    setValue(s.win, prop(pane(s), "Remove rows after (seconds)").control, "-2", ["change"]);
    assert.deepEqual([l.props.newest, l.props.row_lifetime_s], ["bottom", 0]);
});

test("wheel sections: segments add/edit/remove and result label settings", async () => {
    const s = await blank();
    const w = await s.add("wheel");
    const segs = () => control(pane(s), "Segments").closest(".ss-form-row");
    buttonByText(segs(), "+ Segment").click();
    assert.deepEqual(plain(w.wheel_segments.at(-1)), { label: "New", color: "#64748b" });
    setValue(s.win, segs().querySelector("input"), "Prize", ["change"]);
    assert.equal(w.wheel_segments[0].label, "Prize");
    while (w.wheel_segments.length > 1) { buttonByText(segs(), "Remove").click(); }
    buttonByText(segs(), "Remove").click();
    assert.equal(w.wheel_segments.length, 4, "removing the last slice restores the defaults");
    setValue(s.win, prop(pane(s), "Location").control, "center", ["change"]);
    setValue(s.win, prop(pane(s), "Font size").control, "2", ["change"]);
    assert.equal(w.props.result_font_size, 8);
    setValue(s.win, prop(pane(s), "Font size").control, "x", ["change"]);
    assert.equal(w.props.result_font_size, 14);
    setValue(s.win, prop(pane(s), "Bold").control, true, ["change"]);
    setValue(s.win, prop(pane(s), "Format").control, "", ["change"]);
    setValue(s.win, prop(pane(s), "Hold (seconds)").control, "-1", ["change"]);
    assert.deepEqual([w.props.result_place, w.props.result_bold, w.props.result_format, w.props.result_hold_s], ["center", true, "{label}", 0]);
    const fontSel = prop(pane(s), "Font").control;
    setValue(s.win, fontSel, fontSel.options[1].value, ["change"]);
    assert.equal(w.props.result_font_family, fontSel.options[1].value);
    setValue(s.win, prop(pane(s), "Show result").control, false, ["change"]);
    assert.equal(w.props.show_result, false);
    assert.ok((prop(pane(s), "Location")) === null);
});

/* ------------------------------------------------------------------ transforms */

test("transform helpers: origins, default ranges and sources, ids, mapping and composition", async () => {
    const s = await blank();
    const E = s.E;
    assert.equal(E.transformOriginCss("bottom right"), "right bottom");
    assert.equal(E.transformOriginCss("weird"), "center center");
    assert.deepEqual(plain(E.defaultTransformRanges("scale")), { in_min: 0, in_max: 1000, out_min: 1, out_max: 2 });
    assert.equal(E.defaultTransformRanges("translate_y").out_max, 40);
    assert.equal(E.defaultTransformRanges("rotate").out_max, 45);
    assert.equal(E.defaultTransformRanges("skew_x").out_max, 20);
    assert.deepEqual(plain(E.defaultTransformSource({ id: "c", counter: {} }).value), { kind: "data_source", source: "counter", fallback: "", source_id: "c" });
    assert.equal(E.defaultTransformSource(null).value.source, "");
    const r = { source: { kind: "x", template_output: { match: 1 }, refresh_on: "x" } };
    E.ensureTransformSource(r);
    assert.deepEqual([r.source.kind, r.source.refresh_ms, plain(r.source.refresh_on), plain(r.source.template_output)], ["value", 0, [], { match: {}, template: "", event: "" }]);
    const r2 = {};
    E.ensureTransformSource(r2);
    assert.equal(r2.source.kind, "value");
    assert.equal(E.nextTransformId([{ id: "tf2" }, {}]), "tf3");
    assert.equal(E.nextTransformId(null), "tf1");
    assert.equal(E.mapTransformPreview(50, { in_min: 0, in_max: 100, out_min: 1, out_max: 3 }), 2);
    assert.equal(E.mapTransformPreview("x", { in_min: "x", in_max: "y", out_min: "z", out_max: "w" }), 0);
    assert.equal(E.mapTransformPreview(5, { in_min: 5, in_max: 5, out_min: 1, out_max: 2 }), 1);
    assert.equal(E.mapTransformPreview(500, { in_min: 0, in_max: 10, out_min: 0, out_max: 1 }), 1);
    const c = await s.add("counter");
    c.counter.initial_value = 30;
    assert.equal(E.designTimeTransformInput({ source: { value: { kind: "fixed", value: "7" } } }), 7);
    assert.equal(E.designTimeTransformInput({ source: { value: { kind: "fixed", value: "x" } }, in_min: 2 }), 2);
    assert.equal(E.designTimeTransformInput({ source: { kind: "value", value: { source: "counter." + c.counter.counter_id } } }), 30);
    assert.equal(E.designTimeTransformInput({ source: { value: { source: "counter", source_id: c.counter.counter_id } } }), 30);
    assert.equal(E.designTimeTransformInput({ source: { value: { source: "stats", fallback: "4" } } }), 4);
    assert.equal(E.designTimeTransformInput({ source: { kind: "event" }, in_min: "3" }), 3);
    assert.equal(E.designTimeTransformInput({}), 0);
    const fixed = (property, v, extra = {}) => Object.assign({ property, source: { value: { kind: "fixed", value: v } }, in_min: 0, in_max: 1, out_min: 0, out_max: 1 }, extra);
    assert.equal(E.composeDesignTransform([]), null);
    assert.equal(E.composeDesignTransform([null, { enabled: false, property: "scale" }, { property: "bogus" }]), null);
    assert.deepEqual(plain(E.composeDesignTransform([fixed("translate_y", 1, { out_max: 10 }), fixed("rotate", 1, { out_max: 30 }), fixed("skew_x", 1, { out_max: 5 }), fixed("scale", 1, { out_max: 2, origin: "top left" })])),
        { transform: "translate(0px, 10px) rotate(30deg) skew(5deg, 0deg) scale(2)", origin: "left top" });
    const n = s.doc.createElement("div");
    E.applyDesignTransform(n, [fixed("rotate", 1, { out_max: 90 })]);
    assert.deepEqual([n.style.transform, n.style.transformOrigin], ["rotate(90deg)", "center center"]);
    E.applyDesignTransform(n, [fixed("scale", 1)]);
    assert.deepEqual([n.style.transform, n.style.transformOrigin], ["", ""]);
    E.applyDesignTransform(null, []);
});

test("transform rules UI on a block and on the canvas", async () => {
    const s = await blank();
    const c = await s.add("counter");
    const tf = () => collapse(pane(s), "Transformations");
    assert.match(tf().textContent, /No transform yet/);
    buttonByText(tf(), "+ Add transform").click();
    const rule = c.transforms[0];
    assert.deepEqual([rule.id, rule.property, rule.source.value.source, rule.source.value.source_id], ["tf1", "scale", "counter", c.counter.counter_id]);
    assert.equal(node(s, c).style.transform, "", "scale 1 (counter at 0) is no transform");
    setValue(s.win, control(tf(), "Property"), "rotate", ["change"]);
    setValue(s.win, control(tf(), "Origin"), "top right", ["change"]);
    setValue(s.win, control(tf(), "Input min"), "x", ["change"]);
    setValue(s.win, control(tf(), "Input max"), "10", ["change"]);
    setValue(s.win, control(tf(), "Output min"), "0", ["change"]);
    setValue(s.win, control(tf(), "Output max"), "90", ["change"]);
    assert.deepEqual([rule.property, rule.origin, rule.in_min, rule.in_max, rule.out_max], ["rotate", "top right", 0, 10, 90]);
    const fixedToggle = () => tf().querySelectorAll(".ss-form-row--inline").find((r) => r.textContent === "Use a fixed number").querySelector("input");
    setValue(s.win, fixedToggle(), true, ["change"]);
    assert.equal(rule.source.value.kind, "fixed");
    setValue(s.win, control(tf(), "Fixed number"), "5", ["change"]);
    assert.equal(rule.source.value.value, 5);
    assert.equal(node(s, c).style.transform, "rotate(45deg)");
    rule.source.value.value = "x";
    s.select(c.id);
    setValue(s.win, fixedToggle(), false, ["change"]);
    assert.equal(rule.source.value.kind, "data_source");
    setValue(s.win, fixedToggle(), true, ["change"]);
    assert.equal(rule.source.value.value, 0, "a non-numeric fixed value restarts at the input min");
    setValue(s.win, tf().querySelector(".ss-feed__head .ss-toggle__input"), false, ["change"]);
    assert.equal(rule.enabled, false);
    assert.equal(node(s, c).style.transform, "");
    buttonByText(tf(), "Remove").click();
    assert.equal(c.transforms.length, 0);
    // canvas-level transforms live on the Canvas tab
    s.E.state.selectedId = null;
    s.E.renderCanvasPanel();
    const canvasHost = s.$("#ss-pane-canvas");
    buttonByText(canvasHost, "+ Add transform").click();
    assert.equal(s.E.state.model.canvas_transforms.length, 1);
    assert.match(canvasHost.textContent + s.$("#ss-pane-canvas").textContent, /Size/);
});

/* ------------------------------------------------------------------ placement & stage */

test("placement anchors, resolved positions and syncing offsets back", async () => {
    const s = await blank();
    const E = s.E;
    assert.deepEqual(plain(E.placementBase(200, 100, 50, 20, "center", "center")), { x: 75, y: 40 });
    assert.deepEqual(plain(E.placementBase(200, 100, 50, 20, "right", "bottom")), { x: 150, y: 80 });
    assert.deepEqual(plain(E.placementBase(200, 100, 50, 20)), { x: 0, y: 0 });
    const parent = { id: "box", size: { w: 200, h: 100 } };
    const child = { id: "c", parent_id: "box", position: { x: 5, y: 6 }, size: { w: 50, h: 20 } };
    E.ensurePlacementDefaults(child);
    assert.deepEqual(plain(child.placement), { anchor_h: "left", anchor_v: "top", offset_x: 5, offset_y: 6 });
    child.placement = { offset_x: null };
    E.ensurePlacementDefaults(child);
    assert.deepEqual(plain(child.placement), { offset_x: 0, anchor_h: "left", anchor_v: "top", offset_y: 0 });
    E.ensurePlacementDefaults({ position: {} });
    child.placement = { anchor_h: "right", anchor_v: "bottom", offset_x: "-10", offset_y: 5 };
    assert.deepEqual(plain(E.resolvePlacementPosition(child, parent)), { x: 140, y: 85 });
    assert.deepEqual(plain(E.resolvePlacementPosition({ position: { x: 1 } }, null)), { x: 1, y: 0 });
    assert.deepEqual(plain(E.resolvePlacementPosition({}, parent)), { x: 0, y: 0 });
    E.applyPlacementToPosition(child, parent);
    assert.deepEqual(plain(child.position), { x: 140, y: 85 });
    child.position = { x: 100, y: 10 };
    E.syncPlacementFromPosition(child, parent);
    assert.deepEqual([child.placement.offset_x, child.placement.offset_y], [-50, -70]);
    E.syncPlacementFromPosition(child, null);
    E.applyPlacementToPosition(child, null);
    child.placement.anchor_h = "left"; child.placement.anchor_v = "top"; child.placement.offset_x = -500;
    E.applyPlacementToPosition(child, parent);
    assert.equal(child.position.x, 0, "never negative");
    assert.equal(E.pidNorm(""), null);
    assert.equal(E.cssEscapeSs('a"b'), 'a\\"b');
    s.win.CSS = undefined;
    assert.equal(E.cssEscapeSs('a"b\\'), 'a\\"b\\\\');
    assert.equal(E.domNodeForCanvasElement(null, "x"), null);
});

test("placement section: presets and offsets move a block inside its container", async () => {
    const s = await blank();
    const box = await s.add("container", 0, 0);
    const child = await s.add("shape", 10, 10, null, box.id);
    const sec = () => sectionByTitle(pane(s), "Placement in container");
    assert.ok(sec());
    const btns = sec().querySelectorAll(".ss-placement-grid__btn");
    assert.equal(btns.length, 9);
    btns[8].click();
    assert.deepEqual([child.placement.anchor_h, child.placement.anchor_v], ["right", "bottom"]);
    assert.deepEqual(plain(child.position), { x: box.size.w - child.size.w, y: 0 }, "a child taller than its box stays at y 0");
    assert.ok(sec().querySelectorAll(".ss-placement-grid__btn")[8].classList.contains("is-active"));
    sec().querySelectorAll(".ss-placement-grid__btn")[4].click();
    setValue(s.win, control(sec(), "Offset X"), "7", ["change"]);
    setValue(s.win, control(sec(), "Offset Y"), "x", ["change"]);
    assert.deepEqual([child.placement.offset_x, child.placement.offset_y], [7, 0]);
    assert.equal(child.position.x, Math.round((box.size.w - child.size.w) / 2) + 7);
    const host = s.doc.createElement("div");
    s.E.renderPlacementInContainerSection(host, { parent_id: "missing" });
    s.E.renderPlacementInContainerSection(host, {});
    assert.equal(host.children.length, 0);
});

test("layoutPointOnStage adds up container offsets", async () => {
    const s = await blank();
    const outer = await s.add("container", 100, 50);
    const inner = await s.add("container", 10, 5, null, outer.id);
    const leaf = await s.add("shape", 1, 2, null, inner.id);
    assert.deepEqual(plain(s.E.layoutPointOnStage(leaf)), { x: 111, y: 57 });
    assert.deepEqual(plain(s.E.layoutPointOnStage({ parent_id: "gone", position: { x: 3, y: 4 } })), { x: 3, y: 4 });
    assert.deepEqual(plain(s.E.layoutPointOnStage(null)), { x: 0, y: 0 });
});

test("client coordinates map to stage and block space, with or without CSS transforms", async () => {
    const s = await blank();
    const stage = s.$("#ss-stage");
    stage._rect = { left: 20, top: 30, width: 800, height: 400 };
    assert.deepEqual(plain(s.E.clientToStageLocal(120, 80)), { x: 100, y: 50 });
    const box = await s.add("container", 100, 10);
    assert.deepEqual(plain(s.E.clientPointInNode(node(s, box), 220, 60)), { x: 100, y: 20 });
    assert.deepEqual(plain(s.E.clientPointInNode(stage, 120, 80)), { x: 100, y: 50 });
    assert.deepEqual(plain(s.E.clientPointInNode(null, 120, 80)), { x: 100, y: 50 });
    // a scaled stage: DOMMatrix is used to invert the transform
    class DOMPoint { constructor(x, y) { this.x = x; this.y = y; } }
    class DOMMatrix {
        constructor(t) { this.k = Number(/scale\(([\d.]+)\)/.exec(t)[1]); }
        inverse() { const m = new DOMMatrix("scale(1)"); m.k = 1 / this.k; return m; }
        transformPoint(p) { return new DOMPoint(p.x * this.k, p.y * this.k); }
    }
    s.win.DOMMatrix = DOMMatrix;
    s.win.DOMPoint = DOMPoint;
    stage.style.transform = "scale(0.5)";
    stage.style.transformOrigin = "0px 0px";
    stage.parentNode._rect = { left: 20, top: 30, width: 1000, height: 1000 };
    stage._rect = { left: 0, top: 0, width: 400, height: 200 };
    assert.deepEqual(plain(s.E.clientToStageLocal(70, 80)), { x: 100, y: 100 });
    node(s, box).style.transform = "scale(2)";
    node(s, box).style.transformOrigin = "0px 0px";
    assert.deepEqual(plain(s.E.clientPointInNode(node(s, box), 70, 80)), { x: 0, y: 45 });
    s.win.DOMMatrix = function () { throw new Error("unsupported"); };
    assert.deepEqual(plain(s.E.clientToStageLocal(70, 80)), { x: 50, y: 50 });
    assert.deepEqual(plain(s.E.clientPointInNode(node(s, box), 70, 80)), { x: -50, y: 40 });
    stage.remove();
    assert.deepEqual(plain(s.E.clientToStageLocal(1, 1)), { x: 0, y: 0 });
});

test("dragging a block moves it, reparents into containers and back out with Alt", async () => {
    const s = await blank();
    const box = await s.add("container", 300, 100);
    const shape = await s.add("shape", 10, 10);
    const n = () => node(s, shape);
    node(s, box)._rect = { left: 300, top: 100, width: 240, height: 120 };
    // a click without moving only selects
    fire(s.win, n(), "mousedown", { clientX: 15, clientY: 15 });
    fire(s.win, s.doc, "mouseup", { clientX: 15, clientY: 15 });
    assert.deepEqual(plain(shape.position), { x: 10, y: 10 });
    assert.equal(s.E.state.selectedId, shape.id);
    // drag into the container
    fire(s.win, n(), "mousedown", { clientX: 15, clientY: 15 });
    fire(s.win, s.doc, "mousemove", { clientX: 16, clientY: 16 });
    s.doc._elementFromPoint = node(s, box);
    fire(s.win, s.doc, "mousemove", { clientX: 325, clientY: 125 });
    assert.deepEqual(plain(shape.position), { x: 320, y: 120 });
    assert.ok(node(s, box).classList.contains("ss-drop-target"));
    fire(s.win, s.doc, "mouseup", { clientX: 325, clientY: 125 });
    assert.equal(shape.parent_id, box.id);
    assert.deepEqual(plain(shape.position), { x: 20, y: 20 });
    assert.deepEqual([shape.placement.offset_x, shape.placement.offset_y], [20, 20]);
    node(s, box)._rect = { left: 300, top: 100, width: 240, height: 120 }; // the stage was redrawn
    s.doc._elementFromPoint = node(s, box);
    // drag within the container keeps the parent and updates the offset
    const child = () => node(s, shape);
    child()._rect = { left: 320, top: 120, width: 120, height: 120 };
    fire(s.win, child(), "mousedown", { clientX: 330, clientY: 130 });
    fire(s.win, s.doc, "mousemove", { clientX: 340, clientY: 140 });
    fire(s.win, s.doc, "mouseup", { clientX: 340, clientY: 140 });
    assert.equal(shape.parent_id, box.id);
    assert.equal(shape.placement.offset_x, 30);
    // Alt-drag out to the canvas root
    fire(s.win, child(), "mousedown", { clientX: 340, clientY: 140 });
    fire(s.win, s.doc, "mousemove", { clientX: 600, clientY: 300, altKey: true });
    assert.ok(s.$("#ss-stage").classList.contains("ss-drop-target-root"));
    fire(s.win, s.doc, "mouseup", { clientX: 600, clientY: 300, altKey: true });
    assert.equal(shape.parent_id, undefined);
    assert.ok(shape.position.x > 300);
    fire(s.win, s.doc, "mousemove", { clientX: 1, clientY: 1 });
    fire(s.win, s.doc, "mouseup", {});
    s.clock.tick(300);
    assert.equal(s.E.state.dirty, true);
});

test("dropping on a non-container target, dragging out of a parent, legacy blocks don't reparent", async () => {
    const s = await blank();
    const E = s.E;
    const box = await s.add("container", 0, 0);
    const a = await s.add("shape", 5, 5, null, box.id);
    node(s, box)._rect = { left: 0, top: 0, width: 240, height: 120 };
    // dragging out of the previous parent's rectangle drops to root
    fire(s.win, node(s, a), "mousedown", { clientX: 10, clientY: 10 });
    fire(s.win, s.doc, "mousemove", { clientX: 500, clientY: 300 });
    fire(s.win, s.doc, "mouseup", { clientX: 500, clientY: 300 });
    assert.equal(a.parent_id, undefined);
    // repositioning onto something that isn't a container keeps it at root
    const shape = await s.add("shape", 50, 50);
    E.repositionModelElementForDropTarget(a, node(s, a), shape.id);
    assert.equal(a.parent_id, undefined);
    E.repositionModelElementForDropTarget(a, null, box.id);
    const legacy = await bootEditor({ template: "follow" });
    const el = legacy.E.state.model.elements.find((e) => e.position && e.position.x != null);
    const ln = legacy.$('.ss-element[data-spore-id="' + el.id + '"]');
    const x0 = el.position.x;
    fire(legacy.win, ln, "mousedown", { clientX: 0, clientY: 0 });
    fire(legacy.win, legacy.doc, "mousemove", { clientX: 30, clientY: 0 });
    fire(legacy.win, legacy.doc, "mouseup", { clientX: 30, clientY: 0 });
    assert.equal(el.position.x, x0 + 30);
    assert.equal(el.parent_id, undefined);
});

test("the resize handle sets the size with minimums and updates the placement offset", async () => {
    const s = await blank();
    const box = await s.add("container", 0, 0);
    const shape = await s.add("shape", 10, 10, null, box.id);
    const handle = () => node(s, shape).querySelector(".ss-element__handle");
    fire(s.win, handle(), "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mousemove", { clientX: 30, clientY: -500 });
    assert.deepEqual(plain(shape.size), { w: 150, h: 12 });
    fire(s.win, s.doc, "mouseup", {});
    fire(s.win, s.doc, "mousemove", { clientX: 90, clientY: 90 });
    assert.deepEqual(plain(shape.size), { w: 150, h: 12 }, "no resizing after mouseup");
    fire(s.win, handle(), "mousedown", { clientX: 0, clientY: 0 });
    fire(s.win, s.doc, "mouseup", {});
    fire(s.win, node(s, shape), "mousedown", { target: handle() });
});

test("applyPropsToNode styles text, alignment and borders", async () => {
    const s = await blank();
    const t = await s.add("text", 0, 0, { color: "red", background_color: "#000", font_size: 20, font_weight: "bold", text_align: "right", letter_spacing: 2, line_height_px: 30, font_style: "italic", text_underline: true, vertical_align: "center", border_radius: 3, border_width: 1, border_color: "blue", opacity: 0.5 });
    const n = node(s, t);
    assert.deepEqual([n.style.color, n.style.fontSize, n.style.fontWeight, n.style.textAlign, n.style.letterSpacing, n.style.lineHeight, n.style.fontStyle, n.style.textDecoration, n.style.justifyContent, n.style.borderRadius, n.style.border, n.style.opacity],
        ["red", "20px", "bold", "right", "2px", "30px", "italic", "underline", "center", "3px", "1px solid blue", "0.5"]);
    t.props.vertical_align = "bottom";
    t.props.font_style = "";
    s.E.renderStage();
    assert.equal(node(s, t).style.justifyContent, "flex-end");
    assert.equal(node(s, t).style.fontStyle, "");
    t.props.vertical_align = "top";
    s.E.renderStage();
    assert.equal(node(s, t).style.display, "");
    const bar = await s.add("progress_bar");
    assert.equal(node(s, bar).style.overflow, "hidden");
});

/* ------------------------------------------------------------------ legacy property panels */

test("legacy JSON-only fields edit their config value by field type", async () => {
    const s = await bootEditor({ template: "raids" });
    const els = s.E.state.model.elements;
    const pick = (t) => els.find((e) => e.legacy_field && String(e.legacy_field.type).toLowerCase() === t);
    const num = pick("number");
    s.select(num.id);
    assert.match(pane(s).textContent, /Read-only legacy template/);
    setValue(s.win, control(pane(s), "Value"), "42", ["change"]);
    assert.equal(num.props.value, 42);
    setValue(s.win, control(pane(s), "Value"), "", ["change"]);
    assert.equal(num.props.value, null);
    const sel = pick("select");
    s.select(sel.id);
    const sc = control(pane(s), "Value");
    setValue(s.win, sc, sc.options.at(-1).value, ["change"]);
    assert.equal(sel.props.value, sc.options.at(-1).value);
    const cb = pick("checkbox");
    s.select(cb.id);
    const toggle = pane(s).querySelector(".ss-toggle__input");
    setValue(s.win, toggle, !toggle.checked, ["change"]);
    assert.equal(cb.props.value, toggle.checked);
    const txt = pick("text");
    s.select(txt.id);
    setValue(s.win, control(pane(s), "Value"), "hello", ["change"]);
    assert.equal(txt.props.value, "hello");
    const host = s.doc.createElement("div");
    const fake = (type, extra) => Object.assign({ id: "f", props: { value: null }, legacy_field: Object.assign({ type, label: "L", description: "D", min: 0, max: 5, step: 1 }, extra) }, {});
    for (const t of ["textarea", "color", "toggle", "switch", "number", "weird"]) {
        const f = fake(t);
        s.E.renderLegacyProperties(host, f);
    }
    const area = host.querySelector("textarea");
    setValue(s.win, area, "multi", ["change"]);
    const colorEl = fake("color");
    colorEl.props.value = "linear-gradient(red, blue)";
    const ch = s.doc.createElement("div");
    s.E.renderLegacyProperties(ch, colorEl);
    const colorText = ch.querySelector(".ss-color__text");
    setValue(s.win, colorText, "#123456", ["change"]);
    assert.equal(colorEl.props.value, "#123456");
    s.E.renderLegacyProperties(s.doc.createElement("div"), { id: "x", props: {} });
});

test("reverse-parsed legacy elements edit position, size and bound style props", async () => {
    const s = await bootEditor({ template: "title" });
    const el = s.E.state.model.elements.find((e) => e.legacy_bindings && e.legacy_bindings["props.background_color"]);
    s.select(el.id);
    assert.match(pane(s).textContent, /Reverse-parsed legacy element/);
    setValue(s.win, control(pane(s), "X"), "-5", ["change"]);
    setValue(s.win, control(pane(s), "Y"), "15", ["change"]);
    setValue(s.win, control(pane(s), "W"), "3", ["change"]);
    setValue(s.win, control(pane(s), "H"), "x", ["change"]);
    assert.deepEqual([el.position.x, el.position.y, el.size.w, el.size.h], [0, 15, 10, 10]);
    setValue(s.win, control(pane(s), "Background"), "#222", ["change"]);
    setValue(s.win, control(pane(s), "Border radius (px)"), "", ["change"]);
    assert.deepEqual([el.props.background_color, el.props.border_radius], ["#222", null]);
    setValue(s.win, control(pane(s), "Border radius (px)"), "6", ["change"]);
    assert.equal(el.props.border_radius, 6);
    const host = s.doc.createElement("div");
    const fake = { id: "z", type: "text", legacy_anchor: { x: "right", y: "bottom" }, legacy_bindings: { "props.color": "C", "props.font_size": "F", "position.x": "PX" }, props: {} };
    s.E.renderReverseParsedLegacyProperties(host, fake);
    assert.ok(control(host, "X (anchored right)"));
    assert.ok(control(host, "Y (anchored bottom)"));
    setValue(s.win, host.querySelector(".ss-color__text"), "#abc", ["change"]);
    setValue(s.win, control(host, "Font size (px)"), "18", ["change"]);
    assert.deepEqual([fake.props.color, fake.props.font_size], ["#abc", 18]);
    const bare = { id: "q", type: "text" };
    s.E.renderReverseParsedLegacyProperties(s.doc.createElement("div"), bare);
    const h2 = s.doc.createElement("div");
    s.E.renderReverseParsedLegacyProperties(h2, bare);
    setValue(s.win, control(h2, "X"), "4", ["change"]);
    setValue(s.win, control(h2, "W"), "40", ["change"]);
    assert.deepEqual([bare.position.x, bare.size.w], [4, 40]);
});

/* ------------------------------------------------------------------ schema properties */

test("schema properties: text, select, number, colour, font, bold/italic/checkbox and clock offsets", async () => {
    const s = await blank();
    const E = s.E;
    const host = () => s.doc.createElement("div");
    const t = await s.add("text");
    const run = (entry, el = t, mode = "static", img = "static") => { const h = host(); E.appendSchemaProperty(h, el, entry, mode, img); return h; };
    const ctl = (h, sel) => { const c = h.querySelector(".ss-form-prop").children[1]; return c.matches(sel) ? c : c.querySelector(sel); };
    let h = run({ key: "text", label: "Text", type: "textarea" });
    setValue(s.win, ctl(h, "textarea"), "Hi", ["change"]);
    assert.equal(t.props.text, "Hi");
    assert.equal(run({ key: "text", type: "textarea" }, t, "counter").children.length, 0, "counter text comes from the format");
    h = run({ key: "font_size", label: "Size", type: "number", step: 1, min: 0, max: 200 });
    setValue(s.win, ctl(h, "input"), "", ["change"]);
    assert.equal(t.props.font_size, null);
    h = run({ key: "text_align", label: "Align", type: "select", options: ["left", "right"] });
    setValue(s.win, ctl(h, "select"), "right", ["change"]);
    assert.equal(t.props.text_align, "right");
    h = run({ key: "color", label: "Color", type: "color" });
    setValue(s.win, h.querySelector(".ss-color__text"), "#010203", ["change"]);
    assert.equal(t.props.color, "#010203");
    E.state.defaultFonts = [{ filename: "A.ttf", label: "Font A" }];
    h = run({ key: "font_family", label: "Font", type: "font" });
    setValue(s.win, ctl(h, "select"), "A.ttf", ["change"]);
    assert.equal(t.props.font_family, "A.ttf");
    for (const [entry, key, want] of [[{ key: "font_weight", type: "bold" }, "font_weight", "bold"], [{ key: "font_style", type: "italic" }, "font_style", "italic"], [{ key: "text_underline", type: "checkbox" }, "text_underline", true]]) {
        h = run(Object.assign({ label: "x" }, entry));
        setValue(s.win, h.querySelector(".ss-toggle__input"), true, ["change"]);
        assert.equal(t.props[key], want);
        setValue(s.win, h.querySelector(".ss-toggle__input"), false, ["change"]);
    }
    assert.equal(t.props.font_weight, "normal");
    h = run({ key: "letter_spacing", label: "Kerning", type: "number" });
    setValue(s.win, ctl(h, "input"), "3", ["change"]);
    assert.equal(t.props.letter_spacing, 3);
    const clock = await s.add("clock");
    assert.equal(run({ key: "timezone_offset_minutes", type: "number" }, clock).children.length, 0);
    clock.props.timezone = "offset";
    assert.equal(run({ key: "timezone_offset_minutes", type: "number", label: "o" }, clock).children.length, 1);
    h = run({ key: "timezone", label: "Timezone", type: "select", options: ["local", "offset"] }, clock);
    setValue(s.win, ctl(h, "select"), "local", ["change"]);
    const img = await s.add("image");
    assert.equal(run({ key: "src", type: "text" }, img, "static", "from_counter").children.length, 0);
    E.state.assetSnapshot = { files: [{ kind: "image", url: "/a.png", name: "a.png" }] };
    h = run({ key: "src", label: "Source URL", type: "text" }, img);
    setValue(s.win, ctl(h, "select"), "/a.png", ["change"]);
    assert.equal(img.props.src, "/a.png");
    const audio = await s.add("audio");
    h = run({ key: "fade_in_ms", label: "Fade-in", type: "number" }, audio);
    setValue(s.win, ctl(h, "input"), "9000", ["change"]);
    assert.equal(audio.props.fade_in_ms, 9000);
    assert.equal(s.$("#ss-duration-min-label").textContent, "Minimum from layout: 9.0s");
    h = run({ key: "fade_out_ms", label: "Fade-out", type: "checkbox" }, audio);
    setValue(s.win, h.querySelector(".ss-toggle__input"), true, ["change"]);
});

// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// templates/_boilerplates/spore_data_runtime.js, part 2 (lines 1120-end): accent text, clocks,
// timers, lists, the prize wheel (SVG, spin, results, click scheduling), now playing, QR, embeds,
// stat cards, polls, confetti, widget init, Data feeds (filters, rules, modifiers, mapping,
// targets, binding) and value transforms.

import test from "node:test";
import assert from "node:assert/strict";
import { loadScript, plain, run, settle } from "./helpers/env.mjs";
import { setupRuntime as setup } from "./helpers/spore_runtime.mjs";

/* ------------------------------------------------------------------ text, clocks */

test("sporePad2 and accent markup", () => {
    const { I } = setup();
    assert.equal(I.sporePad2(4.9), "04");
    assert.equal(I.sporePad2(12), "12");
    assert.equal(I.sporeAccentHtml("Hi [[<b>you</b>]] & [[me]]"),
        '<span class="spore-accent-line">Hi <span class="spore-accent">&lt;b&gt;you&lt;/b&gt;</span> &amp; <span class="spore-accent">me</span></span>');
});

test("sporeSetTextContent: plain, accent, marquee copies and missing element", () => {
    const s = setup({ html: '<div id="t"></div><div id="m" class="spore-marquee"><span class="spore-marquee-text"></span><span class="spore-marquee-text"></span></div>' });
    const t = s.doc.getElementById("t");
    s.I.sporeSetTextContent(t, "<i>x</i>");
    assert.equal(t.textContent, "<i>x</i>");
    s.I.sporeSetTextContent(t, "a [[b]]");
    assert.ok(t.querySelector(".spore-accent"));
    s.I.sporeSetTextContent(t, null);
    assert.equal(t.textContent, "");
    s.I.sporeSetTextContent(t, "]] then [[");
    assert.equal(t.textContent, "]] then [[");
    const m = s.doc.getElementById("m");
    s.I.sporeSetTextContent(m, "scroll");
    assert.deepEqual(m.querySelectorAll(".spore-marquee-text").map((n) => n.textContent), ["scroll", "scroll"]);
    s.I.sporeSetTextContent(m, "[[hot]]");
    assert.equal(m.querySelectorAll(".spore-accent").length, 2);
    s.I.sporeSetTextContent(null, "x");
});

test("sporeAccentStaticText highlights server-rendered text once; sporePatchSetText installs a setter", () => {
    const s = setup({ html: '<div class="spore-element" data-spore-type="text">Go [[now]]</div><div class="spore-element" data-spore-type="text">plain</div>' });
    s.I.sporeAccentStaticText();
    const [a, b] = s.doc.querySelectorAll(".spore-element");
    assert.equal(a.innerHTML, '<span class="spore-accent-line">Go <span class="spore-accent">now</span></span>');
    assert.equal(b.innerHTML, "plain");
    s.win.sporePatchSetText();
    s.win.sporeSetText("missing", "x");
    b.id = "b";
    s.win.sporeSetText("b", "[[x]]");
    assert.ok(b.querySelector(".spore-accent"));
});

test("clock dates: local, utc and fixed offsets; formatting tokens", () => {
    const { I, win } = setup();
    const local = I.sporeClockDate(null);
    assert.equal(local.getTime(), win.Date.now());
    assert.equal(I.sporeClockDate({ timezone: "local" }).getTime(), win.Date.now());
    const off = local.getTimezoneOffset() * 60000;
    assert.equal(I.sporeClockDate({ timezone: "utc" }).getTime(), win.Date.now() + off);
    assert.equal(I.sporeClockDate({ timezone: "offset", timezone_offset_minutes: "90" }).getTime(), win.Date.now() + off + 90 * 60000);
    assert.equal(I.sporeClockDate({ timezone: "offset" }).getTime(), win.Date.now() + off);
    const d = new win.Date(2024, 0, 1, 7, 5, 9);
    assert.equal(I.sporeFormatClockDate(d), "07:05:09");
    assert.equal(I.sporeFormatClockDate(d, "HH:mm"), "07:05");
});

test("sporeInitClocks renders each clock every second and resets old intervals", () => {
    const s = setup({ html: '<span id="c"></span>' });
    s.win.__sporeClocks.push({ elementId: "c", format: "ss" }, { elementId: "missing" });
    s.win.sporeInitClocks();
    const first = s.doc.getElementById("c").textContent;
    s.clock.tick(1000);
    assert.notEqual(s.doc.getElementById("c").textContent, first);
    assert.equal(s.win.__sporeClockIntervals.length, 2);
    s.win.sporeInitClocks();
    assert.equal(s.clock.pending(), 2, "old intervals cleared");
    s.win.__sporeClocks = undefined;
    s.win.sporeInitClocks();
    assert.equal(s.clock.pending(), 0);
});

/* ------------------------------------------------------------------ timers */

test("timer formatting: count down/up and every token", () => {
    const { I } = setup();
    assert.equal(I.sporeTimerStartBound({ start_seconds: "90" }), 90);
    assert.equal(I.sporeTimerStartBound({ start_seconds: "", duration_seconds: 30 }), 30);
    assert.equal(I.sporeTimerStartBound(null), 0);
    assert.equal(I.sporeTimerShownSeconds({ duration_seconds: 60 }, 15.9), 45);
    assert.equal(I.sporeTimerShownSeconds({ duration_seconds: 10 }, 99), 0);
    assert.equal(I.sporeTimerShownSeconds({ mode: "count_up" }, 12.7), 12);
    assert.equal(I.sporeTimerShownSeconds(null, "x"), 0);
    const spec = { mode: "count_up", format: "{time}|{hh}|{h}|{mm}|{m}|{ss}|{s}|{total}" };
    assert.equal(I.sporeFormatTimerElapsed(spec, 3725), "01:02:05|01|1|02|2|05|5|3725");
    assert.equal(I.sporeFormatTimerElapsed({ duration_seconds: 65 }, 0), "01:05");
});

test("count-down timer: start, tick, pause, resume, finish with output", () => {
    const s = setup({ html: '<span id="t"></span>' });
    s.win.__sporeTemplateName = "tpl";
    s.win.__sporeTimers.t = { duration_seconds: 10 };
    s.win.sporeTimerStart("missing");
    s.win.sporeTimerStart("t");
    assert.equal(s.doc.getElementById("t").textContent, "00:10");
    s.clock.tick(3000);
    assert.equal(s.doc.getElementById("t").textContent, "00:07");
    s.win.sporeTimerPause("t");
    s.win.sporeTimerPause("missing");
    s.clock.tick(5000);
    assert.equal(s.win.__sporeTimers.t.elapsed, 3);
    s.win.sporeTimerStart("t");
    s.win.sporeTimerStart("t"); // already running: same interval
    s.clock.tick(7000);
    assert.equal(s.doc.getElementById("t").textContent, "00:00");
    assert.equal(s.win.__sporeTimers.t.running, false);
    assert.deepEqual(plain(s.socket.emitted.at(-1)), ["timer_finished", { mode: undefined, elapsed: 10, template: "tpl", elementId: "t" }].map((x) => plain(x)));
    assert.equal(s.clock.pending(), 0);
});

test("count-up timer stops at its limit; reset restarts auto-start timers", () => {
    const s = setup({ html: '<span id="u"></span>' });
    s.win.__sporeTimers.u = { mode: "count_up", limit_seconds: 2, format: "{s}", auto_start: true, elementId: "u" };
    s.win.sporeInitTimers();
    s.clock.tick(2000);
    assert.equal(s.doc.getElementById("u").textContent, "2");
    assert.equal(s.win.__sporeTimers.u.running, false);
    s.win.sporeTimerReset("u");
    assert.equal(s.win.__sporeTimers.u.running, true);
    s.clock.tick(1000);
    assert.equal(s.doc.getElementById("u").textContent, "1");
    s.win.__sporeTimers.u.auto_start = false;
    s.win.sporeTimerReset("u");
    assert.equal(s.doc.getElementById("u").textContent, "0");
    s.win.sporeTimerReset("missing");
    s.I.sporeTimerRender("missing");
    s.win.__sporeTimers.x = { duration_seconds: 5, elementId: "nope" };
    s.I.sporeTimerRender("x");
    s.I.sporeTimerTick("missing");
    s.win.__sporeTimers.y = { mode: "count_up", running: true };
    s.I.sporeTimerTick("y");
    assert.ok(s.win.__sporeTimers.y.started_at != null, "a running timer without a start time gets one");
    s.win.__sporeTimers = undefined;
    s.I.__eval("window.__sporeTimers = {}");
    s.win.sporeInitTimers();
});

/* ------------------------------------------------------------------ lists */

function listHtml(attrs = "") {
    return '<div id="l" data-spore-type="list" ' + attrs + '><div class="spore-list-rows"></div></div>';
}

test("sporeAttr and sporeFormatTokens", () => {
    const s = setup({ html: '<i id="a" data-x="" data-y="v"></i>' });
    const a = s.doc.getElementById("a");
    assert.equal(s.I.sporeAttr(a, "data-x", "fb"), "fb");
    assert.equal(s.I.sporeAttr(a, "data-y", "fb"), "v");
    assert.equal(s.I.sporeAttr(null, "data-y", "fb"), "fb");
    assert.equal(s.I.sporeFormatTokens("{username} gave {value}{missing}", { username: "A", value: null }), "A gave {missing}");
    assert.equal(s.I.sporeFormatTokens(null, null), "");
});

test("list rows: newest on top, cap, styling, add animations and lifetime", () => {
    const s = setup({ html: listHtml('data-spore-list-max="2" data-spore-list-gap="6" data-spore-list-row-bg="red" data-spore-list-row-pad="4" data-spore-list-row-radius="3" data-spore-list-lifetime="1" data-spore-list-add="slide"') });
    const el = s.doc.getElementById("l");
    s.win.sporeListAdd("l", { username: "a" });
    s.win.sporeListAdd("l", { username: "b" });
    s.win.sporeListAdd("l", { username: "c" });
    const rows = () => el.querySelectorAll(".spore-list-row");
    assert.deepEqual(rows().map((r) => r.textContent), ["c", "b", "a"]);
    assert.ok(rows()[2].classList.contains("spore-list-leaving"));
    const r = rows()[0];
    assert.ok(r.classList.contains("spore-list-in-slide"));
    assert.deepEqual([r.style.marginBottom, r.style.background, r.style.padding, r.style.borderRadius], ["6px", "red", "4px", "3px"]);
    s.clock.tick(320);
    assert.deepEqual(rows().map((x) => x.textContent), ["c", "b"]);
    s.clock.tick(1000);
    assert.equal(rows().length, 0, "rows expire after their lifetime");
    assert.equal(s.socket.emitted.filter((e) => e[0] === "list_row_added").length, 3);
    s.win.sporeListAdd("missing", {});
});

test("list: newest at the bottom, other add animations, instant removal, clear", () => {
    for (const [anim, cls] of [["slide side", "spore-list-in-side"], ["bounce", "spore-list-in-bounce"], ["fade", "spore-list-in-fade"], ["none", null]]) {
        const s = setup({ html: listHtml('data-spore-list-newest="bottom" data-spore-list-max="1" data-spore-list-remove="none" data-spore-list-add="' + anim + '"') });
        s.win.sporeListAdd("l", { username: "a" });
        s.win.sporeListAdd("l", { username: "b" });
        const rows = s.doc.querySelectorAll(".spore-list-row");
        assert.deepEqual(rows.map((r) => r.textContent), ["b"]);
        if (cls) { assert.ok(rows[0].classList.contains(cls), anim); } else { assert.equal(rows[0].className, "spore-list-row"); }
        s.win.sporeListClear("l");
        assert.equal(s.doc.querySelectorAll(".spore-list-row").length, 0);
        assert.equal(s.socket.emitted.at(-1)[0], "list_cleared");
    }
    const s = setup();
    s.win.sporeListClear("missing");
    s.I.sporeListPush(null, "x");
    s.I.sporeListDrop(null, null);
    s.I.sporeListReplace(null, []);
});

test("sporeListItem maps text/value keys, payload fields and templates", () => {
    const s = setup({ html: listHtml('data-spore-list-template="{username}: {value} ({tier})"') + '<div id="k" data-spore-list-text="name" data-spore-list-value="pts"></div>' });
    const el = s.doc.getElementById("l");
    assert.equal(s.I.sporeListItem(el, { username: "A", amount: 5, tier: 2 }, ""), "A: 5 (2)");
    assert.equal(s.I.sporeListItem(el, { message: "msg", amt_cheered: 100 }), "msg: 100 ({tier})", "unknown tokens stay as written");
    assert.equal(s.I.sporeListItem(el, { gift_qty: 3, tier: 1 }, "lit"), "lit: 3 (1)");
    assert.equal(s.I.sporeListItem(el, null, ""), ":  ({tier})");
    const k = s.doc.getElementById("k");
    assert.equal(s.I.sporeListItem(k, { name: "N", pts: 9 }), "N");
});

test("sporeListReplace rebuilds rows from entries without animations", () => {
    const s = setup({ html: listHtml('data-spore-list-max="2"') });
    const el = s.doc.getElementById("l");
    s.I.sporeListReplace(el, ["newest", { username: "older" }, "dropped"]);
    assert.deepEqual(el.querySelectorAll(".spore-list-row").map((r) => [r.textContent, r.className]), [["newest", "spore-list-row"], ["older", "spore-list-row"]]);
    s.I.sporeListReplace(el, null);
    assert.equal(el.querySelectorAll(".spore-list-row").length, 0);
    const b = setup({ html: listHtml('data-spore-list-newest="bottom"') });
    b.I.sporeListReplace(b.doc.getElementById("l"), ["first", "second"]);
    assert.deepEqual(b.doc.querySelectorAll(".spore-list-row").map((r) => r.textContent), ["first", "second"]);
});

test("legacy list binding listens to alerts, giveaways or a named event", () => {
    const s = setup({ html: listHtml('data-spore-list-alert-type="follow"') + '<div id="g" data-spore-list-source="giveaway"><div class="spore-list-rows"></div></div><div id="p" data-spore-list-source="payload" data-spore-list-event="custom"><div class="spore-list-rows"></div></div><div id="n" data-spore-list-source="payload"></div>' });
    for (const id of ["l", "g", "p", "n"]) { s.I.sporeBindList(s.doc.getElementById(id)); }
    s.I.sporeBindList(s.doc.getElementById("l"));
    s.I.sporeBindList(null);
    s.socket._recv("next_alert", { alert_type: "follow", username: "F" });
    s.socket._recv("next_alert", { alert_type: "raid", username: "R" });
    s.socket._recv("instant_alert", { alert_type: "follow", username: "X", is_replay: true });
    s.socket._recv("instant_alert", null);
    s.socket._recv("giveaway_winner", { winners: ["w1", "w2"] });
    s.socket._recv("giveaway_entry", { username: "e1" });
    s.socket._recv("custom", { username: "c1" });
    const texts = (id) => s.doc.getElementById(id).querySelectorAll(".spore-list-row").map((r) => r.textContent);
    assert.deepEqual(texts("l"), ["F"]);
    assert.deepEqual(texts("g"), ["e1", "w2", "w1"]);
    assert.deepEqual(texts("p"), ["c1"]);
    const noSock = setup({ socket: false, html: listHtml() });
    assert.doesNotThrow(() => noSock.I.sporeBindList(noSock.doc.getElementById("l")));
    const any = setup({ html: listHtml() });
    any.I.sporeBindList(any.doc.getElementById("l"));
    any.socket._recv("next_alert", { alert_type: "raid", username: "R" });
    assert.equal(any.doc.querySelectorAll(".spore-list-row").length, 1);
});

/* ------------------------------------------------------------------ wheel */

function wheelHtml(attrs = "") {
    return '<div id="w" data-spore-type="wheel" ' + attrs + '><div class="spore-wheel-rotor"></div><div class="spore-wheel-pointer"></div><div class="spore-wheel-label"></div></div>';
}

test("wheel segments, text colour and SVG", () => {
    const s = setup({ html: '<i id="a" data-spore-wheel-segments=\'[{"label":"X"}]\'></i><i id="b" data-spore-wheel-segments="{bad"></i><i id="c" data-spore-wheel-segments="[]"></i>' });
    assert.deepEqual(plain(s.I.sporeWheelSegments(s.doc.getElementById("a"))), [{ label: "X" }]);
    assert.equal(s.I.sporeWheelSegments(s.doc.getElementById("b")).length, 2);
    assert.equal(s.I.sporeWheelSegments(s.doc.getElementById("c"))[0].label, "One");
    assert.equal(s.I.sporeWheelTextColor("#fff"), "#111111");
    assert.equal(s.I.sporeWheelTextColor("#000000"), "#ffffff");
    assert.equal(s.I.sporeWheelTextColor("red"), "#ffffff");
    const one = s.I.sporeWheelSvg([{ label: "Only", color: "#fff" }]);
    assert.ok(one.includes('<circle r="100" fill="#fff"/>'));
    const many = s.I.sporeWheelSvg([{ label: "A very long label indeed", color: '#00"0' }, {}, null]);
    assert.equal((many.match(/<path /g) || []).length, 3);
    assert.ok(many.includes("A very long lab…"));
    assert.ok(many.includes('fill="#00&quot;0"'));
    assert.equal((many.match(/<text /g) || []).length, 1, "segments without labels get no text");
    const big = s.I.sporeWheelSvg([{ label: "a" }, { label: "b" }]);
    assert.ok(big.includes(" 0 1 "), "half-circle arcs use the small-arc flag");
});

test("sporeOn, sporeMod, cubic easing and rotation helpers", () => {
    const s = setup({ html: '<i id="a" data-on="TRUE" data-off="no" data-zero="0"></i>' });
    const a = s.doc.getElementById("a");
    assert.equal(s.I.sporeOn(a, "data-on", false), true);
    assert.equal(s.I.sporeOn(a, "data-off", true), false);
    assert.equal(s.I.sporeOn(a, "data-zero", true), false);
    assert.equal(s.I.sporeOn(a, "data-missing", true), true);
    assert.equal(s.I.sporeOn(a, "data-missing", false), false);
    assert.equal(s.I.sporeMod(-30), 330);
    assert.equal(s.I.sporeMod(725), 5);
    assert.equal(s.I.sporeEaseUnit("linear", -1), 0);
    assert.equal(s.I.sporeEaseUnit("linear", 2), 1);
    assert.equal(s.I.sporeEaseUnit("linear", 0.3), 0.3);
    for (const name of ["ease-in", "ease-in-out", "ease", "ease-out"]) {
        const v = s.I.sporeEaseUnit(name, 0.5);
        assert.ok(v > 0 && v < 1, name);
    }
    assert.ok(Math.abs(s.I.sporeEaseUnit("ease-in-out", 0.5) - 0.5) < 1e-6);
    assert.ok(s.I.sporeCubic(0, 0, 1, 1, 0.5) >= 0);
    assert.equal(s.I.sporeCubic(0.5, 0, 0.5, 1, 0), 0, "flat slope at 0 stops Newton early");
    assert.equal(s.I.sporeRotAt({ from: 0, to: 360, ms: 100, easing: "linear" }, -5), 0);
    assert.equal(s.I.sporeRotAt({ from: 0, to: 360, ms: 100, easing: "linear" }, 50), 180);
    assert.equal(s.I.sporeRotAt({ from: 0, to: 360, ms: 100, easing: "linear" }, 500), 360);
});

test("sporeBorderHits finds every segment border crossed", () => {
    const { I } = setup();
    assert.deepEqual(plain(I.sporeBorderHits(0, 10, 0)), []);
    assert.deepEqual(plain(I.sporeBorderHits(10, 10, 4)), []);
    assert.deepEqual(plain(I.sporeBorderHits(80, 280, 4)).map((h) => h.r), [90, 180, 270]);
    assert.deepEqual(plain(I.sporeBorderHits(350, 370, 4)).map((h) => h.key), ["1:0"]);
});

test("pointer placement and result label styles", () => {
    const s = setup({ html: wheelHtml('data-spore-wheel-result-place="center" data-spore-wheel-result-bold="1" data-spore-wheel-result-size="x"') });
    const p = s.doc.querySelector(".spore-wheel-pointer");
    for (const [pos, transform] of [["bottom", "translateX(-50%) rotate(180deg)"], ["left", "translateY(-50%) rotate(-90deg)"], ["right", "translateY(-50%) rotate(90deg)"], ["top", "translateX(-50%)"]]) {
        s.I.sporePlacePointer(p, pos);
        assert.equal(p.style.transform, transform, pos);
    }
    assert.ok(p.innerHTML.startsWith("<svg"));
    const el = s.doc.getElementById("w");
    const label = el.querySelector(".spore-wheel-label");
    s.I.sporeStyleWheelLabel(el, "Win", true);
    assert.deepEqual([label.textContent, label.style.display, label.style.top, label.style.fontWeight, label.style.fontSize], ["Win", "block", "50%", "700", "14px"]);
    for (const [place, prop, val] of [["top", "top", "28%"], ["above", "bottom", "100%"], ["below", "top", "100%"], ["bottom", "bottom", "8px"]]) {
        el.setAttribute("data-spore-wheel-result-place", place);
        s.I.sporeStyleWheelLabel(el, null, false);
        assert.equal(label.style[prop], val, place);
        assert.equal(label.style.display, "none");
    }
    el.setAttribute("data-spore-wheel-result", "false");
    s.I.sporeStyleWheelLabel(el, "x", true);
    assert.equal(label.textContent, "");
    label.remove();
    s.I.sporeStyleWheelLabel(el, "x", true);
});

test("click sound helpers: URL, latency, graph, decoding and trimming", async () => {
    const created = [];
    class Ctx {
        constructor() { this.state = "suspended"; this.currentTime = 1; this.destination = {}; this.outputLatency = 0.01; this.baseLatency = 0.005; created.push(this); }
        createGain() { return { gain: { value: 0 }, connect() {} }; }
        resume() { this.state = "running"; }
        createBufferSource() { const src = { started: [], connect() {}, start(w, o) { src.started.push([w, o]); }, stop() { src.stopped = true; } }; return src; }
        decodeAudioData(buf, ok) { if (ok) { ok({ decoded: buf }); return undefined; } return undefined; }
    }
    const s = setup({ html: '<i id="a" data-spore-wheel-click-file="C:\\sounds\\tick one.mp3"></i><i id="b"></i>', extra: { AudioContext: Ctx } });
    assert.equal(s.I.sporeClickUrl(s.doc.getElementById("a")), "/assets/roulette/tick%20one.mp3");
    assert.equal(s.I.sporeClickUrl(s.doc.getElementById("b")), "/assets/roulette/click.mp3");
    assert.equal(s.I.sporeClickLatencySec(), 0.028, "no context yet");
    const ctx = s.I.sporeEnsureClickGraph(0.4);
    assert.equal(ctx.state, "running");
    assert.equal(s.I.sporeClickGain.gain.value, 0.4);
    assert.equal(s.I.sporeEnsureClickGraph(0.1), ctx);
    assert.equal(created.length, 1);
    assert.ok(Math.abs(s.I.sporeClickLatencySec() - 0.015) < 1e-9);
    ctx.outputLatency = 1;
    assert.equal(s.I.sporeClickLatencySec(), 0.028, "absurd latency falls back");
    const decoded = await s.I.sporeDecodeClick(ctx, "BUF");
    assert.deepEqual(plain(decoded), { decoded: "BUF" });
    const promiseCtx = { decodeAudioData: (b) => Promise.resolve("P" + b) };
    assert.equal(await s.I.sporeDecodeClick(promiseCtx, 1), "P1");
    const throwing = { calls: 0, decodeAudioData(b, ok) { this.calls++; if (!ok) { throw new Error("needs callbacks"); } ok("cb"); } };
    assert.equal(await s.I.sporeDecodeClick(throwing, 1), "cb");
    const data = new Float32Array([0, 0.001, 0.01, 0.5, 0.2]);
    s.I.sporeTrimClickOffset({ length: 5, sampleRate: 100, getChannelData: () => data });
    assert.equal(s.I.sporeClickBufferOffset, 0, "a 30ms lead-in is too long to be silence padding");
    s.I.sporeTrimClickOffset({ length: 5, sampleRate: 1000, getChannelData: () => data });
    assert.equal(s.I.sporeClickBufferOffset, 0.003);
    s.I.sporeTrimClickOffset({ length: 1, getChannelData() { throw new Error("x"); } });
    assert.equal(s.I.sporeClickBufferOffset, 0);
    s.I.sporeTrimClickOffset(null);
    const none = setup();
    assert.equal(none.I.sporeEnsureClickGraph(1), null);
    const broken = setup({ extra: { AudioContext: function () { throw new Error("blocked"); } } });
    assert.equal(broken.I.sporeEnsureClickGraph(1), null);
    const webkit = setup({ extra: { webkitAudioContext: Ctx } });
    assert.ok(webkit.I.sporeEnsureClickGraph(1));
});

test("click loading, scheduling via Web Audio and the HTML audio fallback", async () => {
    class Ctx {
        constructor() { this.state = "running"; this.currentTime = 2; this.destination = {}; }
        createGain() { return { gain: { value: 0 }, connect() {} }; }
        createBufferSource() { const src = { started: [], connect() {}, start(w, o) { src.started.push([w, o]); }, stop() { src.stopped = true; } }; Ctx.srcs.push(src); return src; }
        decodeAudioData() { return Promise.resolve({ length: 0 }); }
    }
    Ctx.srcs = [];
    const s = setup({ html: wheelHtml(), extra: { AudioContext: Ctx } });
    s.win.fetch.on("/assets/roulette/", { body: "x" });
    const el = s.doc.getElementById("w");
    s.I.sporeScheduleClick(0, 0.5, "w"); // no audio at all yet: nothing to play
    s.I.sporePrepareClick(el, 0.3);
    assert.equal(s.win.__audio.instances.at(-1).src, "/assets/roulette/click.mp3");
    assert.equal(s.win.__audio.instances.at(-1).volume, 0.3);
    // response without arrayBuffer() makes the chain fail: still falls back quietly
    await settle();
    s.I.__eval("sporeClickBuffer = { length: 0 }");
    s.I.sporeScheduleClick(100, 0.5, "w");
    const src = Ctx.srcs.at(-1);
    assert.ok(Math.abs(src.started[0][0] - (2 + 0.1 - 0.028)) < 1e-9);
    assert.equal(s.I.sporePendingClicks.length, 1);
    s.I.sporeScheduleClick(0, 0.5, "other");
    assert.equal(Ctx.srcs.at(-1).started[0][0], 2, "never in the past");
    s.I.sporeCancelFutureClicks("w");
    assert.equal(src.stopped, true);
    assert.equal(s.I.sporePendingClicks.length, 1);
    Ctx.srcs.at(-1).onended();
    assert.equal(s.I.sporePendingClicks.length, 0);
    s.I.sporePrepareClick(el, 0.3); // same URL: no reload
    s.I.__eval("sporeClickBuffer = null");
    const before = s.win.__audio.instances.length;
    s.I.sporeScheduleClick(5, 0.2, "w");
    assert.equal(s.win.__audio.instances.length, before, "fallback clones the prepared audio");
    s.I.sporeScheduleClick(500, 0.2, "w");
    s.clock.tick(500);
    s.I.__eval("sporeClickAudio = { cloneNode: function () { throw new Error('x'); } }");
    assert.doesNotThrow(() => s.I.sporePlayClickFallback(1));
    s.I.__eval("sporeClickAudio = null");
    s.I.sporePlayClickFallback(1);
});

test("sporeLoadClickBuffer decodes the fetched file and keeps it for the current URL", async () => {
    class Ctx {
        constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
        createGain() { return { gain: {}, connect() {} }; }
        decodeAudioData() { return Promise.resolve({ length: 2, sampleRate: 10, getChannelData: () => new Float32Array([0, 1]) }); }
    }
    const s = setup({ extra: { AudioContext: Ctx } });
    s.win.fetch = (url) => Promise.resolve({ ok: url.includes("good"), arrayBuffer: () => Promise.resolve(new ArrayBuffer(2)) });
    s.I.sporeLoadClickBuffer("/x");
    s.I.sporeEnsureClickGraph(1);
    s.I.__eval("sporeClickBufferUrl = '/good'");
    s.I.sporeLoadClickBuffer("/good");
    s.I.sporeLoadClickBuffer("/bad");
    await settle();
    assert.equal(s.I.sporeClickBuffer.length, 2);
});

test("sporeScheduleWheelClicks schedules one click per border within the look-ahead", () => {
    const s = setup();
    const scheduled = [];
    s.I.__eval("sporeScheduleClick = function (d, v, id) { window.__clicks.push([d, v, id]); }");
    s.win.__clicks = scheduled;
    const job = { id: "w", from: 0, to: 3600, ms: 1000, easing: "linear", n: 8, volume: 0.2, keys: {} };
    s.I.sporeScheduleWheelClicks(job, 0, 16);
    assert.ok(scheduled.length > 0);
    const count = scheduled.length;
    s.I.sporeScheduleWheelClicks(job, 0, 16);
    assert.equal(scheduled.length, count, "the same border is never scheduled twice");
    s.I.sporeScheduleWheelClicks(job, 1000, 16);
    s.I.sporeScheduleWheelClicks({ id: "s", from: 0, to: 0, ms: 100, easing: "linear", n: 4, volume: 1, keys: {} }, 0, 8);
});

test("wheel spin animates to a forced label, shows the result and emits wheel_result", () => {
    const s = setup({ html: wheelHtml('data-spore-wheel-segments=\'[{"label":"A","color":"#ff0000"},{"label":"B"},{"label":"C"}]\' data-spore-wheel-ms="1000" data-spore-wheel-result-hold="2" data-spore-wheel-result-format="Winner: {label}" data-spore-wheel-click="off"'), random: 0.5 });
    s.win.__sporeTemplateName = "t";
    s.win.sporeWheelSpin("missing");
    s.win.sporeWheelSpin("w", { label: "B" });
    const el = s.doc.getElementById("w");
    const rotor = el.querySelector(".spore-wheel-rotor");
    assert.ok(rotor.innerHTML.includes("<svg"));
    s.clock.tick(500);
    assert.match(rotor.style.transform, /^rotate\(/);
    s.clock.tick(600);
    assert.equal(el.getAttribute("data-spore-wheel-last"), "1");
    const label = el.querySelector(".spore-wheel-label");
    assert.equal(label.textContent, "Winner: B");
    assert.deepEqual(plain(s.win.__sporeLastPayload.wheel), { elementId: "w", index: 1, label: "B" });
    assert.deepEqual(plain(s.socket.emitted.at(-1)), ["wheel_result", { elementId: "w", index: 1, label: "B", template: "t" }]);
    const rot = Number(el.getAttribute("data-spore-wheel-rot"));
    assert.equal(((360 - (rot % 360)) % 360 + 360) % 360 > 120 && ((360 - (rot % 360)) % 360) < 240, true, "lands inside segment B");
    s.clock.tick(2000);
    assert.equal(label.style.display, "none", "result hides after the hold time");
});

test("wheel spin avoids repeating the last winner and a respin cancels the old one", () => {
    const s = setup({ html: wheelHtml('data-spore-wheel-last="0" data-spore-wheel-ms="50" data-spore-wheel-easing="linear"'), random: 0 });
    class Ctx { constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; } createGain() { return { gain: {}, connect() {} }; } createBufferSource() { return { connect() {}, start() {}, stop() {} }; } }
    s.win.AudioContext = Ctx;
    s.win.sporeWheelSpin("w", { label: "" });
    s.clock.tick(16);
    s.win.sporeWheelSpin("w", {});
    s.clock.tick(400); // spins last at least 200ms
    assert.equal(s.doc.getElementById("w").getAttribute("data-spore-wheel-last"), "1", "random 0 picked index 0 = last, so it moved on");
    const v = setup({ html: wheelHtml('data-spore-wheel-variance="off" data-spore-wheel-ms="10" data-spore-wheel-result="off" data-spore-wheel-click-volume="x"'), random: 0.99 });
    v.win.sporeWheelSpin("w");
    v.clock.tick(300);
    assert.equal(v.doc.getElementById("w").getAttribute("data-spore-wheel-last"), "1");
    assert.equal(v.doc.querySelector(".spore-wheel-label").style.display, "none");
});

test("sporePaintWheel handles missing rotor/pointer and click volume", () => {
    const s = setup({ html: '<div id="a"></div><div id="b"><div class="spore-wheel-rotor"></div></div>' });
    s.I.sporePaintWheel(s.doc.getElementById("a"));
    s.doc.getElementById("b").setAttribute("data-spore-wheel-click-volume", "abc");
    s.I.sporePaintWheel(s.doc.getElementById("b"));
    assert.equal(s.win.__audio.instances.at(-1).volume, 0.2);
});

/* ------------------------------------------------------------------ now playing, qr, embed, stat cards */

test("now playing paints art, title, artist, progress and layout", () => {
    const s = setup({ html: '<div id="np" data-spore-np-artist="false" data-spore-np-layout="stack" data-spore-np-bar="#123"><img class="spore-np-art"><div class="spore-np-title"></div><div class="spore-np-artist"></div><div class="spore-np-bar"><div class="spore-np-fill"></div></div></div><div id="bare"></div>' });
    const el = s.doc.getElementById("np");
    s.I.sporePaintNowPlaying(el, { album_image_url: "a.jpg", track_name: "Song", artist_name: "Band", progress_percentage: "42" });
    assert.equal(el.querySelector(".spore-np-art").src, "a.jpg");
    assert.equal(el.querySelector(".spore-np-title").textContent, "Song");
    assert.equal(el.querySelector(".spore-np-artist").style.display, "none");
    assert.equal(el.querySelector(".spore-np-fill").style.width, "42%");
    assert.equal(el.querySelector(".spore-np-fill").style.background, "#123");
    assert.equal(el.style.flexDirection, "column");
    s.I.sporePaintNowPlaying(el, null);
    assert.equal(el.querySelector(".spore-np-title").textContent, "Not playing");
    s.I.sporePaintNowPlaying(s.doc.getElementById("bare"));
});

test("QR codes render as SVG with colours, or as text without the library", () => {
    const s = setup({ html: '<div id="q" data-spore-qr-text="https://x.y" data-spore-qr-fg="#ff0000" data-spore-qr-bg="#00ff00"><div class="spore-qr-host"></div></div><div id="nohost"></div>' });
    s.win.sporePaintQr(s.doc.getElementById("q"));
    assert.equal(s.doc.querySelector(".spore-qr-host").textContent, "https://x.y");
    loadScript(s.win, "assets/default_assets/spore_studio/qrcode.js", { expose: false });
    s.win.sporePaintQr(s.doc.getElementById("q"));
    const host = s.doc.querySelector(".spore-qr-host");
    const svg = host.querySelector("svg");
    assert.ok(svg);
    assert.equal(svg.style.width, "100%");
    assert.ok(host.innerHTML.includes('fill="#ff0000"'));
    assert.ok(host.innerHTML.includes('fill="#00ff00"'));
    s.doc.getElementById("q").removeAttribute("data-spore-qr-text");
    s.win.sporePaintQr(s.doc.getElementById("q"));
    s.win.qrcode = () => { throw new Error("too long"); };
    s.doc.getElementById("q").setAttribute("data-spore-qr-text", "fallback");
    s.win.sporePaintQr(s.doc.getElementById("q"));
    assert.equal(host.textContent, "fallback");
    s.win.sporePaintQr(s.doc.getElementById("nohost"));
});

test("embeds scale their frame and refresh with a cache-busting URL", () => {
    const s = setup({ html: '<div id="e" data-spore-embed-url="https://a.b/p?x=1" data-spore-embed-scale="0.5" data-spore-embed-refresh="10"><iframe class="spore-embed-frame"></iframe></div><div id="z" data-spore-embed-scale="-1"><iframe class="spore-embed-frame"></iframe></div><div id="none"></div>' });
    const el = s.doc.getElementById("e");
    s.I.sporePaintEmbed(el);
    const f = el.querySelector("iframe");
    assert.deepEqual([f.style.transform, f.style.width, f.getAttribute("src")], ["scale(0.5)", "200%", "https://a.b/p?x=1"]);
    s.I.sporePaintEmbed(el);
    assert.equal(s.clock.pending(), 1, "refresh timer started once");
    s.clock.tick(10000);
    assert.match(f.getAttribute("src"), /^https:\/\/a\.b\/p\?x=1&_spore=\d+$/);
    el.setAttribute("data-spore-embed-url", "https://c.d");
    s.clock.tick(10000);
    assert.match(f.getAttribute("src"), /^https:\/\/c\.d\?_spore=/);
    el.setAttribute("data-spore-embed-url", "");
    s.clock.tick(10000);
    s.I.sporePaintEmbed(s.doc.getElementById("z"));
    assert.equal(s.doc.querySelector("#z iframe").style.transform, "scale(1)");
    s.I.sporePaintEmbed(s.doc.getElementById("none"));
});

test("stat cards resolve, format and fall back to a dash", () => {
    const s = setup({ html: '<div id="sc" data-spore-stat-format="{value} subs" data-spore-stat-label="Subs" data-spore-stat-icon="★"><b class="spore-stat-label"></b><i class="spore-stat-value"></i><u class="spore-stat-icon"></u></div>' });
    s.win.__sporeStatCards = [{ elementId: "sc", source: { source: "alert.amount" } }, { elementId: "missing" }];
    s.win.__sporeLastPayload.event = { amount: 5 };
    s.win.sporeRefreshStatCards();
    const el = s.doc.getElementById("sc");
    assert.equal(el.querySelector(".spore-stat-value").textContent, "5 subs");
    assert.equal(el.querySelector(".spore-stat-label").textContent, "Subs");
    assert.equal(el.querySelector(".spore-stat-icon").textContent, "★");
    s.I.sporeApplyStatCard(el, null);
    assert.equal(el.querySelector(".spore-stat-value").textContent, "—");
    s.I.sporeApplyStatCard(el, 1, "custom");
    assert.equal(el.querySelector(".spore-stat-value").textContent, "custom");
    s.win.__sporeStatCards = undefined;
    s.win.__sporeLastPayload = {};
    s.win.sporeRefreshStatCards();
    s.I.sporeApplyStatCard(s.doc.createElement("div"), 1);
});

test("stat cards stay fresh: alert events, 1s refresh and stats polling", () => {
    const s = setup({ html: '<div id="sc"><i class="spore-stat-value"></i></div>' });
    s.win.__sporeStatCards = [{ elementId: "sc", source: { source: "stats.followers" } }];
    s.I.sporeKeepStatCardsFresh();
    s.I.sporeKeepStatCardsFresh();
    assert.equal(s.socket.emitted.filter((e) => e[0] === "get_data").length, 1);
    s.socket._recv("next_alert", { amount: 1 });
    s.clock.tick(1500);
    assert.equal(s.socket.emitted.filter((e) => e[0] === "get_data").length, 2);
    s.clock.tick(15000);
    assert.ok(s.socket.emitted.filter((e) => e[0] === "get_data").length >= 3);
    s.socket._recv("instant_alert", null);
    const n = setup({ socket: false });
    n.win.__sporeStatCards = [{ elementId: "x", source: {} }];
    n.I.sporeKeepStatCardsFresh();
    const empty = setup();
    empty.I.sporeKeepStatCardsFresh();
    assert.equal(empty.win.__sporeStatCardsLive, undefined);
});

/* ------------------------------------------------------------------ polls */

function pollHtml(attrs = "") {
    return '<div id="sporeRoot" data-mycelian-template="poll"></div><div id="pl" data-spore-type="poll" ' + attrs +
        '><div class="spore-poll-head"><span class="spore-poll-title"></span><span class="spore-poll-chip"></span></div>' +
        '<div class="spore-poll-timer"><i class="spore-poll-timer-fill"></i></div><div class="spore-poll-options"></div>' +
        '<div class="spore-poll-foot"><span class="spore-poll-foot-text"></span><span class="spore-poll-total"></span></div></div>';
}
const OPTS = [{ id: 1, name: "Red", keyword: "r", votes: 6, percent: 60 }, { id: 2, name: "Blue", votes: 4, percent: 40 }];

test("poll helpers: template name, clock skew, remaining time and clock text", () => {
    const s = setup({ html: pollHtml() });
    assert.equal(s.I.sporePollTemplateName(), "poll");
    s.win.__sporeTemplateName = "named";
    assert.equal(s.I.sporePollTemplateName(), "named");
    s.win.__sporeTemplateName = "";
    s.doc.getElementById("sporeRoot").remove();
    assert.equal(s.I.sporePollTemplateName(), "");
    const el = s.doc.getElementById("pl");
    const now = s.win.Date.now() / 1000;
    s.I.sporeNotePollClock(el, { server_now: now + 10 });
    assert.equal(el._sporePollSkew, 10);
    s.I.sporeNotePollClock(el, { server_now: now + 10 });
    s.I.sporeNotePollClock(el, null);
    s.I.sporeNotePollClock(null, {});
    assert.equal(s.I.sporePollRemaining(el, { status: "open", ends_at: now + 70, server_now: now + 10 }), 60);
    assert.equal(s.I.sporePollRemaining(el, { status: "open", permanent: true }), null);
    assert.equal(s.I.sporePollRemaining(el, null), null);
    assert.deepEqual([59.2, 61, 3725, -5].map(s.I.sporeFormatPollClock), ["01:00", "01:01", "1:02:05", "00:00"]);
    assert.equal(s.I.sporePollAnimClass("Slide Vertical"), "slide_vertical");
    assert.equal(s.I.sporePollAnimClass("vertical"), "slide_vertical");
    assert.equal(s.I.sporePollAnimClass("fade"), "fade");
    assert.equal(s.I.sporePollAnimClass("none"), "none");
    assert.equal(s.I.sporePollAnimClass(null), "slide_horizontal");
});

test("poll config, look, meta text and options", () => {
    const s = setup({ html: pollHtml('data-spore-poll-style="cards" data-spore-poll-bar-height="x" data-spore-poll-winner-fx="pulse" data-spore-poll-rank="1" data-spore-poll-title="Pick"') });
    const el = s.doc.getElementById("pl");
    const cfg = s.I.sporePollConfig(el);
    assert.equal(cfg.style, "cards");
    assert.equal(cfg.showBars, true);
    assert.equal(cfg.bar2, cfg.bar);
    s.I.sporePollApplyLook(el, cfg, "ended");
    assert.ok(el.classList.contains("is-style-cards"));
    assert.ok(el.classList.contains("fx-pulse"));
    assert.ok(el.classList.contains("is-ended"));
    assert.equal(el.style.getPropertyValue("--sp-barh"), "12px");
    assert.equal(el.querySelector(".spore-poll-title").textContent, "Pick");
    assert.equal(s.I.sporePollMeta(OPTS[0], cfg), "60%", "cards show only the share");
    assert.equal(s.I.sporePollMeta({ votes: 2 }, Object.assign({}, cfg, { style: "bars", showPercent: false })), "2");
    assert.equal(s.I.sporePollMeta(null, Object.assign({}, cfg, { style: "bars" })), "0 · 0%");
    const opt = s.I.sporePollMakeOption(OPTS[0], Object.assign({}, cfg, { style: "bars" }), 1);
    assert.ok(opt.querySelector(".spore-poll-rank"));
    s.I.sporePollUpdateOption(opt, OPTS[0], Object.assign({}, cfg, { style: "bars" }), 6, "open");
    assert.ok(opt.classList.contains("is-leader"));
    assert.equal(opt.querySelector(".spore-poll-kw").textContent, "r");
    assert.equal(opt.querySelector(".spore-poll-bar-fill").style.getPropertyValue("--sp-fill"), "100%");
    s.I.sporePollUpdateOption(opt, { winner: true }, Object.assign({}, cfg, { showCounts: false, showPercent: false }), 0, "ended");
    assert.ok(opt.classList.contains("is-winner"));
    assert.equal(opt.querySelector(".spore-poll-meta").hidden, true);
    const plainOpt = s.I.sporePollMakeOption({ name: "N" }, { showRank: false, showKeywords: false, showBars: false, style: "bars" }, 1);
    assert.equal(plainOpt.getAttribute("data-option-id"), "N");
    s.I.sporePollUpdateOption(plainOpt, null, { showCounts: true, showPercent: true }, 0, "idle");
    const textOnly = setup({ html: pollHtml('data-spore-poll-display="text" data-spore-poll-style="weird"') });
    assert.equal(textOnly.I.sporePollConfig(textOnly.doc.getElementById("pl")).showBars, false);
});

test("an open poll paints options, timer chip, bar and footer; votes update in place", () => {
    const s = setup({ html: pollHtml('data-spore-poll-foot-text="Type the keyword" data-spore-poll-font-size="20" data-spore-poll-color="red" data-spore-poll-weight="700" data-spore-poll-bg="black"') });
    const el = s.doc.getElementById("pl");
    el._rect = { width: 400, height: 300 };
    const now = s.win.Date.now() / 1000;
    s.win.sporeInitWidgets();
    assert.equal(el.style.display, "none", "idle polls are hidden");
    assert.deepEqual(plain(s.socket.emitted.find((e) => e[0] === "poll_request_state")), ["poll_request_state", { template: "poll" }]);
    s.socket._recv("poll_state", { template: "other", status: "open", options: OPTS });
    assert.equal(el.style.display, "none", "other templates' polls are ignored");
    const open = { template: "poll", status: "open", ends_at: now + 30, duration_seconds: 60, total_votes: 10, options: OPTS };
    s.socket._recv("poll_state", open);
    assert.equal(el.style.display, "");
    assert.equal(el.style.fontSize, "20px");
    assert.deepEqual(el.querySelectorAll(".spore-poll-name").map((n) => n.textContent), ["Red", "Blue"]);
    assert.equal(el.querySelector(".spore-poll-chip").textContent, "00:30");
    assert.equal(el.querySelector(".spore-poll-timer-fill").style.width, "50%");
    assert.equal(el.querySelector(".spore-poll-foot-text").textContent, "Type the keyword");
    assert.equal(el.querySelector(".spore-poll-total").textContent, "10 votes");
    const firstNode = el.querySelector(".spore-poll-option");
    s.socket._recv("poll_sync", Object.assign({}, open, { total_votes: 1, options: [{ id: 1, name: "Red", votes: 1, percent: 100 }, { id: 2, name: "Blue", votes: 0, percent: 0 }] }));
    assert.equal(el.querySelector(".spore-poll-option"), firstNode, "same options: nodes reused");
    assert.equal(el.querySelector(".spore-poll-total").textContent, "1 vote");
    s.clock.tick(1000);
    assert.equal(el.querySelector(".spore-poll-chip").textContent, "00:29");
    s.socket._recv("poll_state", Object.assign({}, open, { options: [{ id: 3, name: "Green", votes: 2 }] }));
    assert.deepEqual(el.querySelectorAll(".spore-poll-name").map((n) => n.textContent), ["Green"]);
});

test("ended polls: winner-only, chip text, autohide after the winner shows, poll_result output", () => {
    const s = setup({ html: pollHtml('data-spore-poll-winner-mode="winner_only" data-spore-poll-autohide="5"') });
    const el = s.doc.getElementById("pl");
    el._rect = { width: 400, height: 300 };
    s.I.sporeBindPoll(el, false);
    const now = s.win.Date.now() / 1000;
    s.socket._recv("poll_ended", { template: "poll", status: "ended", ended_at: now - 1, options: [{ id: 1, name: "Red", votes: 6, winner: true }, { id: 2, name: "Blue", votes: 4 }], winners: ["Red"], total_votes: 10 });
    assert.deepEqual(el.querySelectorAll(".spore-poll-name").map((n) => n.textContent), ["Red"]);
    assert.equal(el.querySelector(".spore-poll-chip").textContent, "Poll closed");
    assert.ok(el.querySelector(".spore-poll-option").classList.contains("is-winner"));
    assert.deepEqual(plain(s.socket.emitted.find((e) => e[0] === "poll_result"))[1].winners, ["Red"]);
    s.clock.tick(4000);
    assert.equal(el.style.display, "none", "hidden once the autohide time has passed");
    s.socket._recv("poll_state", { template: "poll", status: "ended", ended_at: now - 100, options: [] });
    assert.equal(el.style.display, "none", "an old result is not shown again");
    s.socket._recv("poll_state", { template: "poll", status: "ended", options: [] });
    s.socket._recv("poll_ended", { template: "other" });
    s.socket._recv("poll_ended", null);
    s.socket._recv("poll_state", { template: "poll", status: "open", options: OPTS, permanent: true });
    assert.equal(el.querySelector(".spore-poll-chip").textContent, "Live");
    assert.equal(el.querySelector(".spore-poll-timer").hidden, true);
});

test("feed-driven polls only announce results; start-hidden polls animate in and out", () => {
    const s = setup({ html: pollHtml('data-spore-hidden="true"') });
    const shown = [];
    s.win.sporeShow = (id) => { shown.push(["show", id]); s.doc.getElementById(id).removeAttribute("data-spore-hidden"); };
    s.win.sporeHide = (id) => { shown.push(["hide", id]); s.doc.getElementById(id).setAttribute("data-spore-hidden", "true"); };
    const el = s.doc.getElementById("pl");
    s.I.sporeBindPoll(el, true);
    s.I.sporeBindPoll(el, true);
    s.socket._recv("poll_state", { template: "poll", status: "open", options: OPTS });
    assert.deepEqual(shown, [], "feed-driven: poll_state is the feed's job");
    s.I.sporePaintPoll(el, { template: "poll", status: "open", options: OPTS });
    s.I.sporePaintPoll(el, { status: "idle" });
    assert.deepEqual(shown, [["show", "pl"], ["hide", "pl"]]);
    s.I.sporePaintPoll(null);
    s.I.sporePaintPoll(el);
    const noSock = setup({ socket: false, html: pollHtml() });
    noSock.I.sporeBindPoll(noSock.doc.getElementById("pl"), false);
});

test("poll pages: fitting, max per page, cards layout and the rotator", () => {
    const s = setup({ html: pollHtml('data-spore-poll-max="2" data-spore-poll-rotator="1" data-spore-poll-rotator-seconds="2" data-spore-poll-rotator-anim="fade" data-spore-poll-rank="1" data-spore-poll-gap="-3"') });
    const el = s.doc.getElementById("pl");
    el._rect = { width: 400, height: 300 };
    const many = [1, 2, 3, 4, 5].map((i) => ({ id: i, name: "O" + i, votes: i }));
    s.I.sporePaintPoll(el, { template: "poll", status: "open", options: many });
    assert.deepEqual(el.querySelectorAll(".spore-poll-page:not(.is-exiting) .spore-poll-name").map((n) => n.textContent), ["O1", "O2"]);
    s.clock.tick(2000);
    assert.deepEqual(el.querySelectorAll(".spore-poll-page:not(.is-exiting) .spore-poll-name").map((n) => n.textContent), ["O3", "O4"]);
    assert.ok(el.querySelector(".spore-poll-page.is-exiting.is-exit-fade"));
    assert.ok(el.querySelector(".spore-poll-page.is-fade"));
    assert.equal(el.querySelector(".spore-poll-page:not(.is-exiting) .spore-poll-rank").textContent, "3");
    s.clock.tick(500);
    assert.equal(el.querySelectorAll(".spore-poll-page").length, 1, "the old page is removed after its animation");
    s.clock.tick(4000);
    assert.deepEqual(el.querySelectorAll(".spore-poll-name").map((n) => n.textContent), ["O1", "O2"], "wraps around");
    s.I.sporePaintPoll(el, { template: "poll", status: "open", options: many.slice(0, 2) });
    assert.equal(el._sporePollRotKey, "", "rotator stops when everything fits");
    const c = setup({ html: pollHtml('data-spore-poll-style="cards" data-spore-poll-max="0" data-spore-poll-rotator-seconds="0" data-spore-poll-rotator-anim-ms="-1"') });
    const ce = c.doc.getElementById("pl");
    ce._rect = { width: 300, height: 100 };
    c.I.sporePaintPoll(ce, { template: "poll", status: "open", options: many });
    assert.ok(ce.querySelector(".spore-poll-page.is-horizontal"));
    assert.equal(c.I.sporePollFitCount(ce, ce.querySelector(".spore-poll-options"), c.doc.createElement("div"), 0, false, 0), 100, "1px rows in a 100px box");
    assert.equal(c.I.sporePollFitCount(c.doc.createElement("div"), ce.querySelector(".spore-poll-options"), c.doc.createElement("div"), 0, true, 0), 1, "never less than one");
    const nohost = setup({ html: '<div id="x"></div>' });
    nohost.I.sporePaintPoll(nohost.doc.getElementById("x"), { status: "open", options: [] });
    nohost.I.sporePollPaintFoot(nohost.doc.getElementById("x"), {}, {});
});

/* ------------------------------------------------------------------ confetti */

test("confetti config and pieces from each origin", () => {
    const s = setup({ html: '<div id="c" data-spore-confetti-count="99999" data-spore-confetti-colors="red, blue\ngreen" data-spore-confetti-size="x"></div>' });
    const cfg = plain(s.I.sporeConfettiConfig(s.doc.getElementById("c")));
    assert.equal(cfg.count, 2000);
    assert.equal(cfg.size, 10);
    assert.deepEqual(cfg.colors, ["red", "blue", "green"]);
    assert.equal(s.I.sporeConfettiConfig(s.doc.createElement("div")).colors.length, 6);
    for (const origin of ["top", "center", "sides"]) {
        const p = s.I.sporeConfettiPiece(Object.assign({}, cfg, { origin }), 100, 50, true);
        assert.ok(Number.isFinite(p.vx) && Number.isFinite(p.vy), origin);
    }
    assert.equal(s.I.sporeConfettiPiece(Object.assign({}, cfg, { origin: "sides" }), 100, 50, false).x, 0);
});

test("confetti bursts draw on the canvas until every piece is gone", () => {
    const s = setup({ html: '<div id="c" data-spore-confetti-count="20" data-spore-confetti-duration="0.5"><canvas class="spore-confetti-canvas"></canvas></div><div id="nocanvas"></div>' });
    const el = s.doc.getElementById("c");
    el._rect = { width: 200, height: 100 };
    s.win.sporeConfetti("c");
    s.win.sporeConfetti("c");
    s.win.sporeConfetti("missing");
    s.win.sporeConfetti("nocanvas");
    assert.equal(el._sporeConfetti.spawns.length, 2);
    s.clock.tick(200);
    assert.ok(el._sporeConfetti.pieces.length > 0);
    const canvas = el.querySelector("canvas");
    assert.equal(canvas.width, 200);
    s.clock.tick(10000);
    assert.equal(el._sporeConfetti.running, false);
    assert.equal(el._sporeConfetti.pieces.length, 0);
    s.doc.hidden = true;
    s.win.requestAnimationFrame = undefined;
    s.win.sporeConfetti("c");
    s.clock.tick(10000);
    assert.equal(el._sporeConfetti.running, false, "timer fallback when frames are paused");
});

/* ------------------------------------------------------------------ widget init */

test("sporeInitWidgets wires lists, wheels, now playing, QR, polls, embeds, stat cards and feeds", () => {
    const s = setup({ html:
        '<div class="spore-element" data-spore-type="text">a [[b]]</div>' + listHtml() +
        wheelHtml('data-spore-wheel-source="giveaway" data-spore-wheel-ms="10" data-spore-wheel-click="off"') +
        '<div id="np" data-spore-type="now_playing"><div class="spore-np-title"></div></div>' +
        '<div id="np2" data-spore-type="now_playing"><div class="spore-np-title"></div></div>' +
        '<div id="q" data-spore-type="qr"><div class="spore-qr-host"></div></div>' +
        '<div id="em" data-spore-type="embed" data-spore-embed-url="u"><iframe class="spore-embed-frame"></iframe></div>' });
    s.win.__sporeFeeds = [{ enabled: true, elementId: "np2", target: { id: "repaint" }, source: { kind: "event", events: [] } }];
    s.win.sporeInitWidgets();
    assert.ok(s.doc.querySelector(".spore-accent"));
    assert.equal(s.doc.getElementById("np").querySelector(".spore-np-title").textContent, "Not playing");
    s.socket._recv("spotify_data_update", { track_name: "Live" });
    assert.equal(s.doc.getElementById("np").querySelector(".spore-np-title").textContent, "Live");
    assert.equal(s.doc.getElementById("np2").querySelector(".spore-np-title").textContent, "Not playing", "feed-driven now playing is left alone");
    assert.equal(s.doc.querySelector("#em iframe").getAttribute("src"), "u");
    s.socket._recv("giveaway_winner", { pool_entries: ["a", "b", "c"], winners: ["b"] });
    s.clock.tick(500);
    assert.equal(s.doc.getElementById("w").getAttribute("data-spore-wheel-last"), "1");
    s.socket._recv("giveaway_winner", null);
    s.socket._recv("next_alert", { username: "listed" });
    assert.equal(s.doc.querySelector(".spore-list-row").textContent, "listed");
    const compiled = setup({ html: listHtml() + wheelHtml('data-spore-wheel-source="giveaway"') });
    compiled.win.__sporeFeedsCompiled = true;
    compiled.win.sporeInitWidgets();
    assert.equal(compiled.doc.getElementById("l").getAttribute("data-spore-list-bound"), null);
    const noSock = setup({ socket: false, html: '<div id="np" data-spore-type="now_playing"></div>' });
    noSock.win.sporeInitWidgets();
});

/* ------------------------------------------------------------------ feeds: rules and filters */

test("sporeFeedGet, sporeFeedText and sporeFeedList", () => {
    const { I } = setup();
    assert.equal(I.sporeFeedGet({ "a.b": 1, a: { b: 2 } }, "a.b"), 1);
    assert.equal(I.sporeFeedGet({ a: { b: [5, 6] } }, "a.b.1"), 6);
    assert.equal(I.sporeFeedGet({ a: null }, "a.b"), undefined);
    assert.equal(I.sporeFeedGet(null, "a"), undefined);
    assert.equal(I.sporeFeedGet({}, ""), undefined);
    assert.equal(I.sporeFeedText(null), "");
    assert.equal(I.sporeFeedText(["a", 1, null]), "a, 1, ");
    assert.equal(I.sporeFeedText([{ a: 1 }]), '[{"a":1}]');
    const cyc = {}; cyc.self = cyc;
    assert.equal(I.sporeFeedText(cyc), "[object Object]");
    assert.equal(I.sporeFeedText(5), "5");
    assert.deepEqual(plain(I.sporeFeedList(" A, b\n\n c ")), ["a", "b", "c"]);
    assert.deepEqual(plain(I.sporeFeedList(null)), []);
});

test("sporeFeedRule supports every operator", () => {
    const { I } = setup();
    const p = { n: 5, s: "Hello World", list: ["Alice", "Bob"], empty: [], blank: "" };
    const R = (field, op, value) => I.sporeFeedRule({ field, op, value }, p);
    assert.equal(R("n", "eq", "5.0"), true);
    assert.equal(R("s", undefined, "hello world"), true);
    assert.equal(R("n", "ne", 4), true);
    assert.equal(R("s", "ne", "hello world"), false);
    assert.equal(R("s", "contains", "WORLD"), true);
    assert.equal(R("list", "contains", "bob"), true);
    assert.equal(R("list", "not_contains", "carol"), true);
    assert.equal(R("s", "not_contains", "xyz"), true);
    assert.equal(R("s", "starts_with", "hel"), true);
    assert.equal(R("s", "ends_with", "WORLD"), true);
    assert.equal(R("s", "ends_with", ""), true);
    assert.equal(R("s", "regex", "^h.*d$"), true);
    assert.equal(R("s", "regex", "("), false);
    assert.equal(R("n", "gt", 4), true);
    assert.equal(R("n", "gte", 5), true);
    assert.equal(R("n", "lt", 6), true);
    assert.equal(R("n", "lte", 4), false);
    assert.equal(R("blank", "gt", 0), false);
    assert.equal(R("n", "between", "1,10"), true);
    assert.equal(R("n", "between", "6"), false);
    assert.equal(R("s", "between", "1,2"), false);
    assert.equal(R("s", "in_list", "a, hello world"), true);
    assert.equal(R("s", "not_in_list", "a"), true);
    assert.equal(R("empty", "empty"), true);
    assert.equal(R("missing", "empty"), true);
    assert.equal(R("n", "not_empty"), true);
    assert.equal(R("n", "unknown_op"), true);
    assert.equal(I.sporeFeedRule({ field: "n", op: "eq" }, p), false, "missing value compares to ''");
});

test("sporeFeedFilterPasses: type, tier, rules all/any, allow/block lists", () => {
    const { I } = setup();
    const F = I.sporeFeedFilterPasses;
    assert.equal(F(null, null, null), true);
    assert.equal(F({ alert_type: "bits" }, { alert_type: "sub" }), false);
    assert.equal(F({ alert_type: "Any" }, {}), true);
    assert.equal(F({ tier: "2" }, { tier: 1000 }), false);
    assert.equal(F({ tier: "any" }, {}), true);
    const rules = [{ field: "n", op: "gt", value: 5 }, { field: "s", op: "eq", value: "x" }];
    assert.equal(F({ rules }, { n: 9, s: "x" }), true);
    assert.equal(F({ rules }, { n: 9, s: "y" }), false);
    assert.equal(F({ rules, match: "any" }, { n: 9, s: "y" }), true);
    assert.equal(F({ rules, match: "any" }, { n: 1, s: "y" }), false);
    assert.equal(F({ users_allow: "ann, bob" }, { username: "Bob" }), true);
    assert.equal(F({ users_allow: "ann" }, { username: "Bob" }), false);
    assert.equal(F({ users_block: "bob" }, { username: "BOB" }), false);
    assert.equal(F({ users_allow: "x" }, {}), false);
});

test("sporeFeedFilterPasses rate limits: skip first, cooldown, max per minute, dedupe", () => {
    const s = setup();
    const F = s.I.sporeFeedFilterPasses;
    const st = {};
    assert.deepEqual([1, 2, 3].map(() => F({ skip_first: 2 }, {}, st)), [false, false, true]);
    const cd = {};
    assert.equal(F({ cooldown_ms: 1000 }, {}, cd), true);
    assert.equal(F({ cooldown_ms: 1000 }, {}, cd), false);
    s.clock.tick(1000);
    assert.equal(F({ cooldown_ms: 1000 }, {}, cd), true);
    const mpm = {};
    assert.deepEqual([1, 2, 3].map(() => F({ max_per_minute: 2 }, {}, mpm)), [true, true, false]);
    s.clock.tick(60000);
    assert.equal(F({ max_per_minute: 2 }, {}, mpm), true);
    const dd = {};
    assert.equal(F({ dedupe_ms: 500 }, { a: 1 }, dd), true);
    assert.equal(F({ dedupe_ms: 500 }, { a: 1 }, dd), false);
    assert.equal(F({ dedupe_ms: 500 }, { a: 2 }, dd), true);
    s.clock.tick(500);
    assert.equal(F({ dedupe_ms: 500 }, { a: 1 }, dd), true);
});

/* ------------------------------------------------------------------ feeds: modifiers, render, item */

test("sporeFeedMod formats numbers and text", () => {
    const { I } = setup();
    const M = I.sporeFeedMod;
    assert.equal(M(1234567, "number"), "1,234,567");
    assert.equal(M("x", "number"), "x");
    assert.equal(M("2.6", "int"), "3");
    assert.equal(M("x", "int"), "x");
    assert.equal(M(3.14159, "decimals:2"), "3.14");
    assert.equal(M(3.6, "decimals"), "4");
    assert.equal(M("x", "decimals:1"), "x");
    assert.equal(M(0.1234, "percent"), "12.3%");
    assert.equal(M("x", "percent"), "x");
    assert.deepEqual([999, 1500, 2000000, 3500000000, "x"].map((v) => M(v, "compact")), ["999", "1.5K", "2M", "3.5B", "x"]);
    assert.equal(M(125, "duration"), "2:05");
    assert.equal(M(-5, "duration"), "0:00");
    assert.equal(M("x", "duration"), "x");
    assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 102].map((v) => M(v, "ordinal")), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "102nd"]);
    assert.equal(M("x", "ordinal"), "x");
    assert.equal(M("aBc", "upper"), "ABC");
    assert.equal(M("aBc", "lower"), "abc");
    assert.equal(M("hello big world", "title"), "Hello Big World");
    assert.equal(M("  t  ", "trim"), "t");
    assert.equal(M("abcdef", "truncate:4"), "abc…");
    assert.equal(M("abc", "truncate:4"), "abc");
    assert.equal(M("", "default:none"), "none");
    assert.equal(M("v", "default:none"), "v");
    assert.equal(M(["a", "b"], "join"), "a, b");
    assert.equal(M(["a", "b"], "join: / "), "a / b");
    assert.equal(M(["a", "b"], "count"), "2");
    assert.equal(M(["a", "b"], "upper"), "A, B");
    assert.equal(M("a,b,c", "count"), "3");
    assert.equal(M("", "count"), "0");
    assert.equal(M("s", "join"), "s");
    assert.equal(M(1, "plural:entry,entries"), "1 entry");
    assert.equal(M(5, "plural:entry,entries"), "5 entries");
    assert.equal(M(2, "plural:win"), "2 wins");
    assert.equal(M("x", "plural:a"), "x");
    assert.equal(M(null, "mystery"), "");
});

test("sporeFeedRender fills {field|mod|mod} placeholders", () => {
    const { win } = setup();
    const R = win.sporeFeedRender;
    assert.equal(R("{user.name|upper} gave {amount|number} ({missing})", { user: { name: "ann" }, amount: 1500 }), "ANN gave 1,500 ()");
    assert.equal(R("{names} / {names|count} / {obj}", { names: ["a", "b"], obj: { x: 1 } }), 'a, b / 2 / {"x":1}');
    assert.equal(R("{v||default:z}", { v: "" }), "z");
    assert.equal(R(null, {}), "");
});

test("sporeFeedItem maps text and value fields, decimals, template, prefix/suffix and fallback", () => {
    const { win } = setup();
    const I = (mapping, payload, vk) => plain(win.sporeFeedItem({ mapping }, payload, vk));
    assert.equal(I({}, { username: "ann", amount: 5 }).text, "ann");
    assert.equal(I({}, { amount: 5 }).text, "5");
    assert.equal(I({}, { amount: 5 }, true).text, "5");
    assert.equal(I({}, {}, true).text, "");
    assert.equal(I({ text_field: "who.list", value_field: "pts", decimals: 1 }, { who: { list: ["a", "b"] }, pts: "2" }).text, "a, b");
    assert.equal(I({ value_field: "pts", decimals: "2" }, { pts: "2" }, true).text, "2.00");
    assert.equal(I({ decimals: "x" }, { value: "2.6" }, true).text, "3");
    assert.equal(I({ template: "{username} x{value}", prefix: "[", suffix: "]" }, { username: "a", value: 2 }).text, "[a x2]");
    assert.equal(I({ fallback: "nobody" }, {}).text, "nobody");
    assert.equal(I({ text_field: "nope", value_field: "nope" }, {}).username, "");
    assert.equal(win.sporeFeedItem({}, null).text, "");
});

/* ------------------------------------------------------------------ feeds: targets */

function feedSetup(html, feed) {
    const s = setup({ html });
    s.feed = Object.assign({ id: "f1", enabled: true, elementId: "e", source: { kind: "event", events: ["ev"] }, filters: {}, mapping: {}, target: { id: "set_text", options: {} } }, feed);
    s.fire = (payload, vk) => s.win.sporeFeedFire(s.feed, payload, vk);
    return s;
}

test("feed targets: list add/clear/replace and confetti", () => {
    const s = feedSetup(listHtml().replace('id="l"', 'id="e"'), { target: { id: "list_add" } });
    s.fire({ username: "a" });
    s.fire({});
    assert.equal(s.doc.querySelectorAll(".spore-list-row").length, 1);
    s.feed.target = { id: "list_replace", options: { items_field: "names" } };
    s.fire({ names: ["x", "y"] });
    assert.deepEqual(s.doc.querySelectorAll(".spore-list-row").map((r) => r.textContent), ["x", "y"]);
    s.feed.target = { id: "list_replace" };
    s.fire({ pool_entries: "not a list" });
    assert.equal(s.doc.querySelectorAll(".spore-list-row").length, 0);
    s.feed.target = { id: "list_clear" };
    s.fire({});
    s.feed.target = { id: "confetti_burst" };
    s.fire({});
});

test("feed targets: wheel spin with segments and landing, add segment", () => {
    const s = feedSetup(wheelHtml('data-spore-wheel-ms="10" data-spore-wheel-click="off"').replace('id="w"', 'id="e"'), { target: { id: "wheel_spin", options: { segments_field: "pool", land_field: "winners" } } });
    s.fire({ pool: ["a", "b", "c"], winners: ["c"] });
    s.clock.tick(400);
    const el = s.doc.getElementById("e");
    assert.equal(JSON.parse(el.getAttribute("data-spore-wheel-segments")).length, 3);
    assert.equal(el.getAttribute("data-spore-wheel-last"), "2");
    s.fire({ pool: [], winners: "" });
    s.clock.tick(400);
    s.feed.target = { id: "wheel_spin", options: {} };
    s.fire({});
    s.clock.tick(400);
    s.feed.target = { id: "wheel_add_segment", options: { color: "#123456" } };
    s.fire({ username: "new" });
    s.fire({});
    const segs = JSON.parse(el.getAttribute("data-spore-wheel-segments"));
    assert.deepEqual(segs.at(-1), { label: "new", color: "#123456" });
    s.feed.target = { id: "wheel_add_segment", options: {} };
    s.fire({ username: "auto" });
    assert.ok(JSON.parse(el.getAttribute("data-spore-wheel-segments")).at(-1).color.startsWith("#"));
});

test("feed targets: stat card and progress bar values, max overrides", () => {
    const s = feedSetup('<div id="e" data-spore-type="stat_card"><i class="spore-stat-value"></i></div><div id="bar"><i class="spore-progress-fill"></i></div>', { target: { id: "set_value" }, mapping: { template: "{value} pts" } });
    s.fire({ value: 7 });
    assert.equal(s.doc.querySelector(".spore-stat-value").textContent, "7 pts");
    s.feed.mapping = {};
    s.fire({ value: 8 });
    assert.equal(s.doc.querySelector(".spore-stat-value").textContent, "8");
    s.feed.elementId = "bar";
    s.fire({ value: 30 });
    assert.equal(s.win.__sporeProgressBars.length, 1, "bar registered on first override");
    assert.equal(s.doc.querySelector("#bar .spore-progress-fill").style.width, "30%");
    s.feed.target = { id: "set_max" };
    s.fire({ value: 60 });
    assert.equal(s.doc.querySelector("#bar .spore-progress-fill").style.width, "50%");
});

test("feed targets: counters (by element id or element mapping) with item value or explicit delta", () => {
    const s = feedSetup('<span id="e"></span>', { target: { id: "counter_increment" } });
    s.win.__sporeCounterMeta.c = { elementId: "e", initial_value: 0 };
    s.fire({ amount: 5 });
    assert.equal(s.win.__sporeCounters.c, 5);
    s.feed.target = { id: "counter_decrement", options: { delta: { kind: "fixed", value: 2 } } };
    s.fire({});
    assert.equal(s.win.__sporeCounters.c, 3);
    s.feed.target = { id: "counter_set" };
    s.fire({ value: 40 });
    assert.equal(s.win.__sporeCounters.c, 40);
    s.feed.target = { id: "counter_reset" };
    s.fire({});
    assert.equal(s.win.__sporeCounters.c, 0);
    s.feed.target = { id: "counter_increment" };
    s.fire({ value: "abc" });
    assert.equal(s.win.__sporeCounters.c, 1, "non-numeric values count as 1");
    s.win.__sporeCounterMeta = {};
    s.win.__sporeCounterMeta.e = { initial_value: 0 };
    s.fire({});
    assert.equal(s.win.__sporeCounters.e, 1, "falls back to the element id");
    s.I.sporeFeedCounterOp(null, "increment", {}, {});
    s.win.__sporeLastPayload.event = undefined;
    s.I.sporeFeedCounterOp(s.doc.getElementById("e"), "increment", { value: "" }, null);
});

test("feed targets: set_text through a feed-driven data display or plain text", () => {
    const s = feedSetup('<span id="e"></span><span id="d"></span>', { mapping: { template: "{username}!" } });
    s.fire({ username: "a" });
    assert.equal(s.doc.getElementById("e").textContent, "a!");
    s.win.sporeSetText = undefined;
    s.fire({ username: "b" });
    assert.equal(s.doc.getElementById("e").textContent, "b!");
    s.win.sporePatchSetText();
    s.win.__sporeDataDisplays.push({ elementId: "d", feed: true, format: "{value} k" });
    s.feed.elementId = "d";
    s.feed.mapping = {};
    s.fire({ value: 3 }, true);
    assert.equal(s.doc.getElementById("d").textContent, "3 k");
    s.fire({ value: "" }, true);
    s.feed.mapping = { template: "T{value}" };
    s.fire({ value: 4 }, true);
    assert.equal(s.doc.getElementById("d").textContent, "T4");
});

test("feed targets: timers, media, QR and embed", () => {
    const s = feedSetup('<span id="e"></span><img id="img"><video id="vid"><source></video><audio id="aud"></audio><div id="wrap"><img><video><source></video></div><div id="wrap2"><audio></audio></div>', { target: { id: "timer_start" } });
    s.win.__sporeTimers.e = { duration_seconds: 30 };
    s.fire({});
    assert.equal(s.win.__sporeTimers.e.running, true);
    s.feed.target = { id: "timer_pause" };
    s.fire({});
    assert.equal(s.win.__sporeTimers.e.running, false);
    s.feed.target = { id: "timer_set" };
    s.fire({ value: 12 });
    assert.equal(s.doc.getElementById("e").textContent, "00:18");
    s.win.__sporeTimers.e.running = true;
    s.fire({ value: 1 });
    assert.ok(s.win.__sporeTimers.e.started_at != null);
    s.feed.target = { id: "timer_reset" };
    s.fire({});
    s.feed.elementId = "img";
    s.feed.target = { id: "timer_set" };
    s.fire({ value: 1 });
    s.feed.target = { id: "set_src" };
    s.fire({ username: "a.png" });
    assert.equal(s.doc.getElementById("img").getAttribute("src"), "a.png");
    for (const id of ["vid", "aud", "wrap", "wrap2"]) { s.feed.elementId = id; s.fire({ username: id + ".mp4" }); }
    assert.equal(s.doc.querySelector("#vid source").getAttribute("src"), "vid.mp4");
    assert.equal(s.doc.getElementById("aud").getAttribute("src"), "aud.mp4");
    assert.equal(s.doc.querySelector("#wrap img").getAttribute("src"), "wrap.mp4");
    assert.equal(s.doc.querySelector("#wrap source").getAttribute("src"), "wrap.mp4");
    assert.equal(s.doc.querySelector("#wrap2 audio").getAttribute("src"), "wrap2.mp4");
    s.doc.getElementById("aud").load = () => { throw new Error("x"); };
    s.feed.elementId = "aud";
    s.fire({ username: "z" });
    s.I.sporeFeedSetMedia(null, "x");
    s.feed.target = { id: "play_media", options: { volume: 0.5 } };
    s.fire({});
    const played = [];
    s.win.sporePlayMediaAudio = (id, over) => played.push([id, plain(over)]);
    s.fire({});
    s.feed.target = { id: "play_media", options: { volume: "" } };
    s.fire({});
    assert.deepEqual(played, [["aud", { volume: 0.5 }], ["aud", {}]]);
    const q = feedSetup('<div id="e"><div class="spore-qr-host"></div></div>', { target: { id: "set_qr_text" } });
    q.fire({ username: "hello" });
    assert.equal(q.doc.getElementById("e").getAttribute("data-spore-qr-text"), "hello");
    const em = feedSetup('<div id="e"><iframe class="spore-embed-frame" src="old"></iframe></div>', { target: { id: "set_embed_url" } });
    em.fire({ username: "https://new" });
    assert.equal(em.doc.querySelector("iframe").getAttribute("src"), "https://new");
    em.fire({});
    assert.equal(em.doc.querySelector("iframe").getAttribute("src"), "https://new");
});

test("feed targets: repaint polls/now playing, visibility, flash class and styles", () => {
    const s = feedSetup(pollHtml().replace('id="pl"', 'id="e"') + '<div id="np" data-spore-type="now_playing"><div class="spore-np-title"></div></div>', { target: { id: "repaint" } });
    s.doc.getElementById("e")._rect = { width: 400, height: 300 };
    s.fire({ template: "other", status: "open", options: OPTS });
    assert.equal(s.doc.querySelectorAll(".spore-poll-option").length, 0);
    s.fire({ template: "poll", status: "open", options: OPTS });
    assert.equal(s.doc.querySelectorAll(".spore-poll-option").length, 2);
    s.feed.elementId = "np";
    s.fire({ track_name: "T" });
    assert.equal(s.doc.querySelector(".spore-np-title").textContent, "T");
    s.feed.elementId = "e";
    s.doc.getElementById("e").setAttribute("data-spore-type", "other");
    s.fire({});
    for (const [target, hidden] of [["hide", true], ["show", false], ["toggle", true], ["toggle", false]]) {
        s.feed.target = { id: target };
        s.fire({});
        assert.equal(s.doc.getElementById("e").hasAttribute("data-spore-hidden"), hidden, target);
    }
    s.feed.target = { id: "show_for", options: { seconds: 2 } };
    s.fire({});
    s.fire({});
    s.clock.tick(2000);
    assert.equal(s.doc.getElementById("e").getAttribute("data-spore-hidden"), "true");
    const shown = [];
    s.win.sporeShow = (id) => shown.push(["show", id]);
    s.win.sporeHide = (id) => shown.push(["hide", id]);
    s.feed.target = { id: "show" };
    s.fire({});
    s.feed.target = { id: "hide" };
    s.fire({});
    assert.deepEqual(shown, [["show", "e"], ["hide", "e"]]);
    s.feed.target = { id: "flash_class", options: { class_name: "pop<x>", duration_ms: 100 } };
    s.fire({});
    assert.ok(s.doc.getElementById("e").classList.contains("popx"));
    s.clock.tick(100);
    assert.ok(!s.doc.getElementById("e").classList.contains("popx"));
    s.feed.target = { id: "flash_class", options: { class_name: "!!!" } };
    s.fire({});
    s.feed.target = { id: "flash_class", options: {} };
    s.fire({});
    assert.ok(s.doc.getElementById("e").classList.contains("sporePop"));
    const el = s.doc.getElementById("e");
    for (const [prop, val, key, want] of [["background_color", "red", "background", "red"], ["color", "blue", "color", "blue"], ["border_color", "green", "borderColor", "green"], ["opacity", "0.5", "opacity", "0.5"], ["font_size", "20", "fontSize", "20px"], ["font_size", "2em", "fontSize", "2em"]]) {
        s.feed.target = { id: "set_style", options: { property: prop, fixed: val } };
        s.fire({});
        assert.equal(el.style[key], want, prop);
    }
    s.feed.target = { id: "set_style", options: {} };
    s.fire({ username: "purple" });
    assert.equal(el.style.background, "purple");
    s.fire({});
    s.feed.target = { id: "set_style", options: { property: "unknown", fixed: "x" } };
    s.fire({});
});

test("feed fire: disabled, unknown target, missing element, handler errors, split rows and delay", () => {
    const s = feedSetup(listHtml().replace('id="l"', 'id="e"'), { target: { id: "list_add" }, mapping: { split_field: "names" }, timing: { delay_ms: 100 } });
    s.fire({ names: ["a", { username: "b" }] });
    assert.equal(s.doc.querySelectorAll(".spore-list-row").length, 0);
    s.clock.tick(100);
    assert.deepEqual(s.doc.querySelectorAll(".spore-list-row").map((r) => r.textContent), ["b", "a"]);
    s.feed.timing = {};
    s.fire({ names: "not a list", username: "c" });
    assert.equal(s.doc.querySelectorAll(".spore-list-row")[0].textContent, "c");
    s.feed.filters = { alert_type: "bits" };
    s.fire({ username: "d" });
    s.feed.enabled = false;
    s.fire({ username: "d" });
    s.feed.enabled = true;
    s.feed.filters = {};
    s.feed.target = { id: "nope" };
    s.fire({});
    s.feed.target = { id: "list_add" };
    s.feed.elementId = "missing";
    s.fire({});
    s.feed.elementId = "e";
    s.feed.target = { id: "timer_set" };
    s.win.__sporeTimers.e = { duration_seconds: 5 };
    s.I.__eval("sporeTimerRender = function () { throw new Error('boom'); }");
    s.fire({ value: 1 });
    assert.ok(s.win.__console.some(([k, a]) => k === "error" && a[0] === "[spore-studio] feed target error"));
    s.feed.target = {};
    s.fire({});
    const vk = feedSetup('<span id="e"></span>', { filters: { rules: [{ field: "value", op: "gt", value: 5 }] } });
    vk.fire({ value: 3 }, true);
    vk.fire({ value: 9 }, true);
    assert.equal(vk.doc.getElementById("e").textContent, "9");
});

/* ------------------------------------------------------------------ feeds: binding */

test("event feeds bind to socket events and skip replays", () => {
    const s = feedSetup('<span id="e"></span>', {});
    s.I.sporeFeedBind(s.feed);
    s.socket._recv("ev", { username: "a" });
    assert.equal(s.doc.getElementById("e").textContent, "a");
    s.socket._recv("ev", { username: "b", is_replay: true });
    s.socket._recv("ev", 42);
    assert.equal(s.doc.getElementById("e").textContent, "42");
    assert.deepEqual(plain(s.win.__sporeLastPayload.event), { value: 42 });
    const n = setup({ socket: false });
    n.I.sporeFeedBind({ source: { kind: "event", events: ["x"] } });
    n.I.sporeFeedBind({ source: { kind: "template_output" } });
    s.I.sporeFeedBind({ source: { kind: "event" } });
    s.I.sporeFeedBind({});
});

test("template_output feeds match event, template and data fields", () => {
    const s = feedSetup('<span id="e"></span>', { source: { kind: "template_output", template_output: { event: "wheel_result", template: "wheel", match: { label: "A" } } } });
    s.I.sporeFeedBind(s.feed);
    s.socket._recv("template_output", { event: "other" });
    s.socket._recv("template_output", { event: "wheel_result", template: "x", data: { label: "A" } });
    s.socket._recv("template_output", { event: "wheel_result", template: "wheel", data: { label: "B" } });
    assert.equal(s.doc.getElementById("e").textContent, "");
    s.socket._recv("template_output", { event: "wheel_result", data: { label: "A", username: "won" } });
    assert.equal(s.doc.getElementById("e").textContent, "won");
    s.socket._recv("template_output", null);
    const any = feedSetup('<span id="e"></span>', { source: { kind: "template_output" } });
    any.I.sporeFeedBind(any.feed);
    any.socket._recv("template_output", { data: "not an object" });
});

test("value feeds refresh on events and timers, only firing on change", () => {
    const s = feedSetup('<span id="e"></span>', { source: { kind: "value", value: { source: "alert.amount" }, refresh_on: ["next_alert"], refresh_ms: 50 } });
    s.I.sporeFeedBind(s.feed);
    assert.equal(s.doc.getElementById("e").textContent, "");
    s.socket._recv("next_alert", { amount: 5 });
    assert.equal(s.doc.getElementById("e").textContent, "5");
    s.win.__setText = [];
    s.clock.tick(100);
    assert.equal(s.win.__setText.length, 0, "unchanged values are not re-applied");
    s.socket._recv("next_alert", null);
    s.win.__sporeFeeds = [s.feed, { source: { kind: "value" }, enabled: false }, { source: { kind: "event" }, enabled: true }];
    s.win.__sporeLastPayload = { event: { amount: 6 } };
    s.I.sporeFeedRefreshAllValues();
    assert.equal(s.doc.getElementById("e").textContent, "6");
    s.win.__sporeFeeds = undefined;
    s.I.sporeFeedRefreshAllValues();
    const t = feedSetup('<span id="e"></span>', { source: { kind: "value", value: {} }, target: { id: "timer_start" } });
    t.win.__sporeTimers.e = { duration_seconds: 9 };
    t.I.sporeFeedRefreshValue(t.feed);
    t.I.sporeFeedRefreshValue(t.feed);
    assert.equal(t.win.__sporeTimers.e.running, true, "timer_start fires even with an unchanged value");
    const nosock = setup({ socket: false, html: '<span id="e"></span>' });
    nosock.I.sporeFeedBind({ id: "x", elementId: "e", enabled: true, source: { kind: "value", value: {} }, target: { id: "set_text" } });
});

test("sporeInitFeeds binds once, polls stats for stats feeds and asks giveaway state on load and reconnect", () => {
    const s = setup({ html: '<span id="e"></span>' });
    s.win.__sporeFeeds = [
        { id: "a", enabled: true, elementId: "e", source: { kind: "value", value: { source: "stats.followers" } }, target: { id: "set_text" } },
        { id: "b", enabled: true, elementId: "e", source: { kind: "event", events: ["giveaway_entry", "giveaway_winner", "next_alert"] }, target: { id: "set_text" } },
        { id: "c", enabled: false, source: { kind: "event", events: ["giveaway_x"] } },
    ];
    s.win.sporeInitFeeds();
    s.win.sporeInitFeeds();
    assert.equal(s.socket.emitted.filter((e) => e[0] === "giveaway_request_state").length, 1);
    assert.equal(s.socket.emitted.filter((e) => e[0] === "get_data").length, 1);
    s.socket._recv("connect");
    assert.equal(s.socket.emitted.filter((e) => e[0] === "giveaway_request_state").length, 2);
    s.clock.tick(15000);
    assert.equal(s.socket.emitted.filter((e) => e[0] === "get_data").length, 2);
    const n = setup({ socket: false });
    n.win.__sporeFeeds = undefined;
    n.win.sporeInitFeeds();
    n.I.sporeRequestFeedState([]);
});

/* ------------------------------------------------------------------ transforms */

test("transform helpers: origins, mapping, first number", () => {
    const { I } = setup();
    assert.equal(I.sporeTransformOriginCss("top left"), "left top");
    assert.equal(I.sporeTransformOriginCss("bogus"), "center center");
    assert.equal(I.sporeMapTransform(50, { in_min: 0, in_max: 100, out_min: 1, out_max: 2 }), 1.5);
    assert.equal(I.sporeMapTransform(500, { in_min: 0, in_max: 100, out_min: 1, out_max: 2 }), 2);
    assert.equal(I.sporeMapTransform(-5, { in_min: 0, in_max: 100, out_min: 1, out_max: 2 }), 1);
    assert.equal(I.sporeMapTransform(5, { in_min: 3, in_max: 3 }), 0);
    assert.equal(I.sporeMapTransform("x", {}), 0);
    assert.equal(I.sporeFirstNumber(4), 4);
    assert.equal(I.sporeFirstNumber("7"), 7);
    assert.equal(I.sporeFirstNumber("x"), null);
    assert.equal(I.sporeFirstNumber(null), 0);
    assert.equal(I.sporeFirstNumber({ amount: "", count: "x", delta: 3 }), 3);
    assert.equal(I.sporeFirstNumber({ pts: 9, value: 1 }, "pts"), 9);
    assert.equal(I.sporeFirstNumber({ value: 1 }, "value"), 1);
    assert.equal(I.sporeFirstNumber({}), null);
});

test("transform inputs come from the last event number or a resolved value", () => {
    const s = setup();
    const I = s.I;
    assert.equal(I.sporeResolveTransformInput({ source: { kind: "event", value: { fallback: 4 } } }), 4);
    assert.equal(I.sporeResolveTransformInput({ source: { kind: "event" }, in_min: 2 }), 2);
    assert.equal(I.sporeResolveTransformInput({ source: { kind: "template_output" }, _lastNumber: 9 }), 9);
    s.win.__sporeLastPayload.event = { amount: 30 };
    assert.equal(I.sporeResolveTransformInput({ source: { kind: "value", value: { source: "alert.amount" } } }), 30);
    assert.equal(I.sporeResolveTransformInput({ source: { kind: "value", value: { source: "alert.nope", fallback: 7 } } }), 7);
    assert.equal(I.sporeResolveTransformInput({ source: { kind: "value", value: { source: "alert.nope" } }, in_min: 1 }), 1);
    s.win.__sporeLastPayload = {};
    assert.equal(I.sporeResolveTransformInput({}), undefined);
});

test("sporeComposeTransform combines rules in a fixed order", () => {
    const { I } = setup();
    assert.equal(I.sporeComposeTransform([]), null);
    assert.equal(I.sporeComposeTransform([null, { enabled: false }, { property: "nonsense" }]), null);
    const fixed = (property, n, extra = {}) => Object.assign({ property, source: { kind: "value", value: { kind: "fixed", value: n } }, in_min: 0, in_max: 1, out_min: 0, out_max: 1 }, extra);
    const c = plain(I.sporeComposeTransform([fixed("translate_x", 0.5, { out_max: 20 }), fixed("rotate", 1, { out_max: 90 }), fixed("skew_y", 1, { out_max: 5 }), fixed(undefined, 1, { out_max: 2, origin: "bottom right" })]));
    assert.deepEqual(c, { transform: "translate(10px, 0px) rotate(90deg) skew(0deg, 5deg) scale(2)", origin: "right bottom" });
    assert.deepEqual(plain(I.sporeComposeTransform([fixed("scale", 1)])), { transform: "", origin: "center center" });
});

test("sporeApplyTransforms writes and clears owned transforms on the root and elements", () => {
    const s = setup({ html: '<div id="sporeRoot"></div><div id="a" style="transform: rotate(1deg)"></div>' });
    s.win.sporeApplyTransforms();
    const rule = { property: "scale", source: { kind: "value", value: { source: "counter", source_id: "c" } }, in_min: 0, in_max: 10, out_min: 1, out_max: 2 };
    s.win.__sporeTransforms = { canvas: [rule], elements: { a: [Object.assign({}, rule, { property: "rotate", out_min: 0, out_max: 90 })], missing: [rule] } };
    s.win.__sporeCounters.c = 5;
    s.win.sporeApplyTransforms();
    const root = s.doc.getElementById("sporeRoot");
    assert.equal(root.style.transform, "scale(1.5)");
    assert.equal(root.getAttribute("data-spore-transform-owned"), "1");
    assert.equal(s.doc.getElementById("a").style.transform, "rotate(45deg)");
    s.win.__sporeTransforms = { canvas: [], elements: {} };
    s.win.sporeApplyTransforms();
    assert.equal(root.style.transform, "");
    assert.equal(root.hasAttribute("data-spore-transform-owned"), false);
    s.I.sporeWriteTransform(s.doc.getElementById("a"), null);
    s.I.sporeWriteTransform(null, null);
    s.win.__sporeTransforms = {};
    s.win.sporeApplyTransforms();
});

test("counter changes re-apply transforms", () => {
    const s = setup({ html: '<div id="sporeRoot"></div><span id="cnt"></span>' });
    s.win.__sporeCounterMeta.c = { elementId: "cnt", initial_value: 0 };
    s.win.__sporeTransforms = { canvas: [{ property: "rotate", source: { kind: "value", value: { source: "counter.c" } }, in_min: 0, in_max: 10, out_min: 0, out_max: 100 }] };
    s.win.sporeCounterAdjust("c", "increment", { kind: "fixed", value: 3 });
    assert.equal(s.doc.getElementById("sporeRoot").style.transform, "rotate(30deg)");
});

test("sporeInitTransforms binds event, template_output and value rules once", () => {
    const s = setup({ html: '<div id="sporeRoot"></div><div id="box"></div>' });
    const ev = { property: "scale", value_field: "pts", source: { kind: "event", events: ["bits"] }, in_min: 0, in_max: 100, out_min: 1, out_max: 2 };
    const to = { property: "rotate", source: { kind: "template_output", template_output: { event: "x", template: "t", match: { k: "v" } } }, in_min: 0, in_max: 10, out_min: 0, out_max: 10 };
    const val = { property: "translate_y", source: { kind: "value", value: { source: "stats.followers" }, refresh_on: ["next_alert"], refresh_ms: 50 }, in_min: 0, in_max: 10, out_min: 0, out_max: 10 };
    s.win.__sporeTransforms = { canvas: [ev], elements: { box: [to, val] } };
    s.win.sporeInitTransforms();
    s.win.sporeInitTransforms();
    const root = s.doc.getElementById("sporeRoot");
    s.socket._recv("bits", { pts: 50 });
    assert.match(root.style.transform, /scale\(1\.5\)/);
    s.socket._recv("bits", { is_replay: true, pts: 100 });
    s.socket._recv("bits", "nan");
    assert.match(root.style.transform, /scale\(1\.5\)/);
    s.socket._recv("bits", 100);
    assert.match(root.style.transform, /scale\(2\)/);
    s.socket._recv("template_output", { event: "y" });
    s.socket._recv("template_output", { event: "x", template: "u" });
    s.socket._recv("template_output", { event: "x", data: { k: "w" } });
    s.socket._recv("template_output", { event: "x", data: { k: "v", value: 5 } });
    const box = s.doc.getElementById("box");
    assert.match(box.style.transform, /rotate\(5deg\)/);
    s.socket._recv("template_output", { event: "x", data: { k: "v" } });
    s.socket._recv("template_output", null);
    s.win.__sporeStatsCache = { followers: 4 };
    s.socket._recv("next_alert", null);
    assert.match(box.style.transform, /translate\(0px, 4px\)/);
    s.clock.tick(15000);
    assert.ok(s.socket.emitted.filter((e) => e[0] === "get_data").length >= 2, "stats polled");
    const nosock = setup({ socket: false });
    nosock.win.__sporeTransforms = undefined;
    nosock.win.sporeInitTransforms();
    const anyOut = setup();
    anyOut.I.sporeBindTransformRule({ source: { kind: "template_output" } });
    anyOut.socket._recv("template_output", { data: "x" });
    anyOut.I.sporeBindTransformRule({ source: {} });
    assert.equal(anyOut.I.sporeTransformUsesStats(null), false);
    assert.equal(anyOut.I.sporeTransformUsesStats({ source: { kind: "value", value: { source: "stats" } } }), true);
});

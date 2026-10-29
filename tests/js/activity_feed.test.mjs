// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/activity_feed.js: settings/queue normalisation, row building from alert
// payloads, filters and search, rendering of live / history / by-person views (rows, cards,
// ticker), the now-playing strip and timers, the multi-month tag, user actions sent through
// send(action, payload), NiceGUI host binding and the health probe. All text must be escaped.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, fire, plain } from "./helpers/env.mjs";

const FILE = "assets/default_assets/activity_feed.js";
const NOW = 1_700_000_000; // FakeClock wall start, in seconds

function setup(opts = {}) {
    const win = makeWindow(opts);
    const { internals: I } = loadScript(win, FILE);
    return { win, F: win.MycelianFeed, I, doc: win.document, clock: win.__clock };
}
function feed(opts = {}) {
    const s = setup();
    const mount = s.doc.createElement("div");
    s.doc.body.appendChild(mount);
    const sent = [];
    const f = s.F.create(mount, Object.assign({ send: (a, p) => sent.push([a, plain(p)]) }, opts));
    return Object.assign(s, { f, mount, sent, q: (sel) => f.root.querySelector(sel), qa: (sel) => f.root.querySelectorAll(sel) });
}
function row(over = {}) {
    return Object.assign({
        id: "r1", alert_id: "a1", kind: "follow", icon: "favorite", color: "var(--color-primary)", label: "Follow", filter: "follows",
        user: "Alice", what: "followed", amount: "", tier: null, msg: "", ts: NOW - 10, platform: "twitch", restored: false, seq: 1,
    }, over);
}

/* ------------------------------------------------------------------ pure helpers */

test("esc, mi, num, ordinal, kindOf, meta", () => {
    const { I, F } = setup();
    assert.equal(I.esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
    assert.equal(I.esc(null), "");
    assert.equal(I.mi("star"), '<span class="mi">star</span>');
    assert.equal(I.num(1234567), "1,234,567");
    assert.equal(I.num("abc"), "abc");
    assert.equal(I.num(null), "0");
    assert.equal(I.num(undefined), "");
    assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111, 0, "x"].map(F.ordinal),
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th", "0th", "0th"]);
    assert.equal(F.kindOf(" Cheer "), "bits");
    assert.equal(F.kindOf("Hype Train"), "hype");
    assert.equal(F.kindOf("member milestone"), "milestone");
    assert.equal(F.kindOf("whatever"), "alert");
    assert.equal(F.kindOf(null), "alert");
    assert.equal(I.meta("nope")[2], "Alert");
    for (const k of Object.values(I.ALIASES)) { assert.ok(I.KINDS[k], k); }
});

test("normSettings coerces types and rejects unknown choices", () => {
    const { F, I } = setup();
    assert.deepEqual(plain(F.normSettings(null)), plain(I.DEFAULTS));
    const s = plain(F.normSettings({ layout: "CARDS", density: "huge", icons: 0, earlier_minutes: "45", new_minutes: "x", times: "clock", wrap: "yes", up_next: null }));
    assert.equal(s.layout, "cards");
    assert.equal(s.density, "comfortable");
    assert.equal(s.icons, false);
    assert.equal(s.earlier_minutes, 45);
    assert.equal(s.new_minutes, 5);
    assert.equal(s.times, "clock");
    assert.equal(s.wrap, true);
    assert.equal(s.up_next, 1);
});

test("normFilters and modeOf", () => {
    const { I } = setup();
    const f = plain(I.normFilters({ bits: false, follows: 0 }));
    assert.equal(f.bits, false);
    assert.equal(f.follows, true, "only an explicit false turns a filter off");
    assert.equal(Object.keys(f).length, I.FILTERS.length);
    assert.equal(I.modeOf(null), "live");
    assert.equal(I.modeOf({ mode: "person" }), "person");
    assert.equal(I.modeOf({ tab: "previous" }), "history");
    assert.equal(I.modeOf({ condensed: true }), "person");
    assert.equal(I.modeOf({ mode: "bogus" }), "live");
});

test("normQueue normalises entries, clock offset and the contract flag", () => {
    const { F } = setup();
    const q = plain(F.normQueue({
        paused: 1, muted: 0, playing: { alert_id: 7, alert_type: "follow", username: "Bob", started_at_ms: "100", duration_ms: "5000" },
        queue: [null, "x", { type: "raid", is_replay: 1 }], revision: "3", server_now_ms: 1_700_000_000_000 + 2500,
    }));
    assert.deepEqual(q.playing, { alert_id: "7", type: "follow", username: "Bob", is_replay: false, started_at_ms: 100, duration_ms: 5000 });
    assert.deepEqual(q.queue, [{ alert_id: null, type: "raid", username: "", is_replay: true, started_at_ms: null, duration_ms: null }]);
    assert.equal(q.paused, true);
    assert.equal(q.muted, false);
    assert.equal(q.revision, 3);
    assert.equal(q.offset, 2500);
    assert.equal(q.contract, true);
    const empty = plain(F.normQueue(null));
    assert.deepEqual(empty, { paused: false, muted: false, playing: null, queue: [], revision: -1, offset: 0, contract: false });
    assert.equal(F.normQueue({ contract: true }).contract, true);
});

test("filterMatch: filter keys and case-insensitive search across fields", () => {
    const { F, I } = setup();
    const all = I.normFilters(null);
    const r = row({ msg: "Hello World", amount: "$5" });
    assert.equal(F.filterMatch(r, all, ""), true);
    assert.equal(F.filterMatch(r, all, "  WORLD "), true);
    assert.equal(F.filterMatch(r, all, "$5"), true);
    assert.equal(F.filterMatch(r, all, "zzz"), false);
    const noFollows = Object.assign(plain(all), { all: false, follows: false });
    assert.equal(F.filterMatch(r, noFollows, ""), false);
    assert.equal(F.filterMatch(row({ filter: "", kind: "bits" }), noFollows, ""), true, "falls back to the kind's filter");
    assert.equal(F.filterMatch(row({ filter: "", kind: "alert" }), noFollows, ""), false, "generic alerts have no filter key");
});

test("tipPlatform reads the donation source", () => {
    const { I } = setup();
    assert.equal(I.tipPlatform({ donation_source: "StreamLabs" }), "streamlabs");
    assert.equal(I.tipPlatform({ source: "streamelements" }), "streamelements");
    assert.equal(I.tipPlatform({ stored_alert_data: { donation_source: "streamlabs" } }), "streamlabs");
    assert.equal(I.tipPlatform({ source: "paypal" }), "");
});

test("rowFromAlert passes prebuilt rows through and builds fallbacks", () => {
    const { F } = setup();
    const pre = row();
    assert.equal(F.rowFromAlert({ row: pre }), pre);
    assert.equal(F.rowFromAlert(pre), pre);
    const r = plain(F.rowFromAlert({ type: "follow", username: "Zed", message: "Zed followed!!", timestamp: NOW - 5, alert_id: "abc", seq: 4, user_message: "hi" }));
    assert.equal(r.kind, "follow");
    assert.equal(r.user, "Zed");
    assert.equal(r.what, "followed");
    assert.equal(r.alert_id, "abc");
    assert.equal(r.id, "abc");
    assert.equal(r.msg, "hi");
    assert.equal(r.seq, 4);
    assert.equal(r.platform, "twitch");
    const g = plain(F.rowFromAlert({ alert_type: "cheer", message: "Ann", alert_id: "generated_1", created_at: NOW }));
    assert.equal(g.user, "Ann");
    assert.equal(g.what, "cheered", "empty remainder uses the verb");
    assert.equal(g.alert_id, null);
    assert.equal(g.id, "row-" + NOW * 1000);
    const badge = plain(F.rowFromAlert({ badge_type: "raid", type: "follow", is_restored: true }));
    assert.equal(badge.kind, "raid");
    assert.equal(badge.user, "Someone");
    assert.equal(badge.restored, true);
    assert.equal(badge.ts, NOW);
    const tip = plain(F.rowFromAlert({ type: "tip", username: "T", donation_source: "streamlabs" }));
    assert.equal(tip.platform, "streamlabs");
    assert.equal(plain(F.rowFromAlert(null)).kind, "alert");
});

test("rowFromAlert: multi-month subs, resubs and gifts get duration captions", () => {
    const { F } = setup();
    const sub = plain(F.rowFromAlert({ type: "sub", username: "S", message: "S subscribed", duration_months: "3" }));
    assert.deepEqual([sub.months, sub.duration, sub.duration_short, sub.duration_kind], [3, "3 months in advance", "3 mo up front", "sub"]);
    assert.equal(sub.what_full, "subscribed · 3 months in advance");
    const resub = plain(F.rowFromAlert({ type: "resub", username: "R", duration_months: 6 }));
    assert.equal(resub.duration_kind, "resub");
    const gift = plain(F.rowFromAlert({ type: "giftsub", username: "G", duration_months: 2, gift_qty: 5 }));
    assert.equal(gift.duration, "2 months each");
    const one = plain(F.rowFromAlert({ type: "giftsub", username: "G", duration_months: 2, gift_qty: 1 }));
    assert.equal(one.duration_short, "2 mo gift");
    const notSub = plain(F.rowFromAlert({ type: "bits", username: "B", duration_months: 4 }));
    assert.equal(notSub.months, 0);
    assert.equal(notSub.duration, "");
});

test("queueRow builds synthetic rows for queued alerts", () => {
    const { I } = setup();
    const r = plain(I.queueRow({ alert_id: "x", type: "raid", username: "", is_replay: true }, 2));
    assert.equal(r.id, "q-x");
    assert.equal(r.user, "Someone");
    assert.equal(r.what, "replay · raid");
    assert.equal(r.synthetic, true);
    assert.equal(I.queueRow({ type: "hype_train" }, 3).user, "Hype Train");
    assert.equal(I.queueRow({ type: "follow" }, 3).id, "q-3");
});

test("time helpers", () => {
    const { I } = setup();
    assert.equal(I.relTime(NOW - 30), "just now");
    assert.equal(I.relTime(NOW - 120), "2m");
    assert.equal(I.relTime(NOW - 7200), "2h");
    assert.equal(I.relTime(NOW - 3 * 86400), "3d");
    assert.equal(I.minutesLabel(30), "30 min");
    assert.equal(I.minutesLabel(60), "1 hour");
    assert.equal(I.minutesLabel(90), "1.5 hours");
    assert.equal(I.mmss(61000), "1:01");
    assert.equal(I.mmss(-5), "0:00");
    assert.equal(I.mmss(1), "0:01");
    assert.equal(I.dayLabel(NOW), "Today");
    assert.equal(I.dayLabel(NOW - 86400), "Yesterday");
    assert.match(I.dayLabel(NOW - 5 * 86400), /\w/);
    assert.match(I.clockTime(NOW), /\d/);
});

test("monthsTag renders each style and stays empty below two months", () => {
    const { F } = setup();
    const a = { months: 3, duration: "3 months in advance", duration_short: "3 mo up front", duration_kind: "sub" };
    assert.equal(F.monthsTag({ months: 1, duration: "x" }, "split"), "");
    assert.equal(F.monthsTag({ months: 3, duration: "" }, "split"), "");
    const split = F.monthsTag(a, "split");
    assert.match(split, /^<span class="mm mm-split" title="Paid for 3 months at once"><b class="n">3<\/b>/);
    assert.ok(split.includes('<span class="l">months in advance</span><span class="s">mo up front</span>'));
    assert.ok(F.monthsTag(a, "chip").includes('<span class="l">3 months in advance</span>'));
    assert.ok(F.monthsTag(a, "calendar").includes('<span class="cal"><i></i><b class="n">3</b></span>'));
    assert.equal((F.monthsTag(Object.assign({}, a, { months: 20 }), "dots").match(/<i><\/i>/g) || []).length, 12);
    assert.ok(F.monthsTag(Object.assign({}, a, { duration_kind: "resub" }), "split").includes("Renewed 3 months at once"));
    assert.ok(F.monthsTag(Object.assign({}, a, { duration_kind: "each" }), "split").includes("Each gifted sub lasts 3 months"));
    assert.ok(F.monthsTag(Object.assign({}, a, { duration_kind: "gift", duration_short: "" }), "split").includes("This gift lasts 3 months"));
});

/* ------------------------------------------------------------------ the Feed */

test("app feed builds the shell with header pill, tabs, search and filter menu", () => {
    const s = feed();
    assert.equal(s.f.root.className, "afx");
    assert.equal(s.q(".afx-title").textContent, "Activity Feed");
    assert.equal(s.qa('[data-a="mode"]').length, 3);
    assert.equal(s.qa("[data-filter]").length, 12);
    assert.equal(s.q('[data-r="pill"]').textContent, "Ready");
    assert.ok(s.q('[data-a="mode"][data-mode="live"]').classList.contains("is-on"));
    assert.equal(s.f.mode(), "live");
    assert.equal(s.q(".afx-empty").textContent.includes("No events yet this session"), true);
    assert.ok(s.f.isAlive());
    assert.equal(s.F.instances.includes(s.f), true);
    s.f.destroy();
    s.f.destroy();
    assert.equal(s.F.instances.includes(s.f), false);
});

test("dock feed has the compact head and no app-only controls", () => {
    const s = feed({ dock: true });
    assert.equal(s.f.root.className, "afx is-dock");
    assert.equal(s.q('[data-r="pill"]'), null);
    assert.equal(s.q('[data-a="help"]'), null);
    assert.equal(s.q('[data-a="copy-dock"]'), null);
    s.f.set("meta", { connected: false });
    assert.ok(s.q('[data-r="strip"]').textContent.includes("Reconnecting to Mycelian"));
});

test("live rows render escaped text, icons, platform, seq, time and replay hint", () => {
    const s = feed();
    s.f.set("live", [
        row({ user: "<script>x</script>", msg: 'He said "hi"', tier: "T2", seq: 9 }),
        row({ id: "r2", alert_id: null, user: "Old", ts: NOW - 4000, restored: true, platform: "youtube" }),
    ]);
    const rows = s.qa(".afx-row");
    assert.equal(rows.length, 2);
    assert.ok((s.f.root.querySelector("script")) === null);
    assert.equal(rows[0].querySelector(".user").textContent, "<script>x</script>");
    assert.equal(rows[0].querySelector(".msg").textContent, "“He said \"hi\"”");
    assert.equal(rows[0].querySelector(".seq").textContent, "#9");
    assert.equal(rows[0].querySelector(".tb").textContent, "T2");
    assert.ok(rows[0].querySelector('svg[aria-label="Twitch"]'));
    assert.ok(rows[0].classList.contains("is-click"));
    assert.ok(rows[0].classList.contains("is-new"));
    assert.equal(rows[0].querySelector(".time").textContent, "just now");
    assert.ok(!rows[1].classList.contains("is-click"), "no alert id: not replayable");
    assert.ok(rows[1].classList.contains("is-old"));
    assert.match(rows[1].getAttribute("title"), /can't be replayed/);
    assert.ok(rows[1].querySelector('svg[aria-label="YouTube"]'));
    const heads = s.qa(".afx-day");
    assert.deepEqual(heads.map((h) => h.textContent), ["Recent · last 30 minnorthnewest first", "Earlier"]);
});

test("settings switch off icons, platform marks, messages, seq numbers and times", () => {
    const s = feed();
    s.f.set("settings", { icons: false, platform: false, messages: false, order_numbers: false, times: "hidden", new_minutes: 0, earlier_minutes: 0, wrap: true, color: "bar" });
    s.f.set("live", [row({ msg: "m" })]);
    const r = s.q(".afx-row");
    assert.ok((r.querySelector(".ico")) === null);
    assert.ok((r.querySelector("svg.plat")) === null);
    assert.ok((r.querySelector(".msg")) === null);
    assert.ok((r.querySelector(".seq")) === null);
    assert.ok((r.querySelector(".time")) === null);
    assert.ok(!r.classList.contains("is-new"));
    assert.ok(s.f.root.classList.contains("no-icons"));
    assert.ok(s.f.root.classList.contains("is-wrap"));
    assert.equal(s.f.root.getAttribute("data-color"), "bar");
    assert.equal(s.q(".afx-day").textContent, "This sessionnorthnewest first");
    s.f.set("settings", { times: "clock" });
    s.f.set("live", [row()]);
    assert.match(s.q(".time").textContent, /\d/);
});

test("cards layout and the months tag placements", () => {
    const s = feed();
    const r = row({ kind: "sub", months: 3, duration: "3 months in advance", duration_short: "3 mo up front", duration_kind: "sub", what: "subscribed", what_tagged: "subscribed", amount: "$5", tier: "T1" });
    s.f.set("settings", { layout: "cards", sub_duration_place: "right" });
    s.f.set("live", [r]);
    assert.ok(s.q(".afx-row .top .user"));
    assert.ok(s.q(".afx-row .mm-push + .mm"));
    assert.equal(s.q(".afx-row .amt").textContent, "$5");
    assert.equal(s.q(".afx-row .tier").textContent, "T1");
    s.f.set("settings", { layout: "cards", sub_duration_place: "below" });
    assert.ok(s.q(".afx-row .mm-below .mm"));
    s.f.set("settings", { layout: "rows", sub_duration_place: "inline" });
    assert.ok(s.q(".afx-row .line .mm"));
    s.f.set("settings", { sub_duration: false });
    assert.equal(s.q(".afx-row .mm"), null);
});

test("dock rows put an inline months tag below; ticker never puts it below", () => {
    const s = feed({ dock: true });
    const r = row({ kind: "sub", months: 2, duration: "2 months in advance", duration_kind: "sub" });
    s.f.set("live", [r]);
    assert.ok(s.q(".mm-below .mm"));
    const t = feed();
    t.f.set("settings", { layout: "ticker", sub_duration_place: "below" });
    t.f.set("live", [r]);
    assert.ok(t.q(".afx-row .line .mm"));
    assert.equal(t.q(".mm-below"), null);
});

test("fitMonths marks cramped tags short and re-fits on resize", () => {
    const s = feed();
    s.f.set("live", [row({ months: 3, duration: "3 months in advance", duration_kind: "sub" })]);
    const what = s.q(".afx-row .what");
    what.scrollWidth = 200;
    what.clientWidth = 100;
    s.f.fitMonths();
    assert.ok(s.q(".afx-row .mm").classList.contains("is-short"));
    what.scrollWidth = 100;
    s.f.fitMonths();
    assert.ok(!s.q(".afx-row .mm").classList.contains("is-short"));
    const obs = s.win.__observers.find((o) => o.targets.includes(s.f.r.list));
    assert.ok(obs, "ResizeObserver on the list");
    s.f.r.list.clientWidth = 300;
    s.q(".afx-row .line").scrollWidth = 500;
    s.q(".afx-row .line").clientWidth = 100;
    obs.trigger();
    assert.ok(s.q(".afx-row .mm").classList.contains("is-short"));
    assert.equal(s.f.mmWidth, 300);
    obs.trigger(); // same width: no refit
    const b = feed();
    b.f.set("settings", { sub_duration_place: "below" });
    b.f.set("live", [row({ months: 3, duration: "3 months in advance" })]);
    const below = b.q(".mm-below");
    below.scrollWidth = 50;
    below.clientWidth = 10;
    b.f.fitMonths();
    assert.ok(b.q(".mm").classList.contains("is-short"));
});

test("add puts the newest row first, replaces same id and caps at 500", () => {
    const s = feed();
    s.f.set("live", Array.from({ length: 600 }, (_, i) => row({ id: "r" + i, alert_id: "a" + i })));
    assert.equal(s.f.s.live.length, 500);
    s.f.set("add", row({ id: "r5", user: "Updated" }));
    assert.equal(s.f.s.live[0].user, "Updated");
    assert.equal(s.f.s.live.filter((r) => r.id === "r5").length, 1);
    s.f.set("add", row({ id: "new" }));
    assert.equal(s.f.s.live.length, 500);
    s.f.set("live", null);
    assert.equal(s.f.s.live.length, 0);
});

test("queue: playing strip with timer, up-next chips, row tags and pill", () => {
    const s = feed();
    s.f.set("live", [row({ id: "p", alert_id: "p", user: "Pat", what: "raided", amount: "50" }), row({ id: "w", alert_id: "w", user: "Wes" })]);
    s.f.set("queue", {
        playing: { alert_id: "p", type: "raid", started_at_ms: Date.now() - 0, duration_ms: 10000, is_replay: true },
        queue: [{ alert_id: "w", type: "follow" }, { alert_id: "zz", type: "bits", username: "Ghost" }],
    });
    // Date.now in this realm is the fake clock; restart playback at the fake time
    s.f.s.queue.playing.started_at_ms = s.win.Date.now() - 2500;
    s.f.renderAll();
    const strip = s.q('[data-r="strip"]');
    assert.ok(strip.textContent.includes("Now playing"));
    assert.ok(strip.querySelector(".who b").textContent === "Pat");
    assert.ok(strip.textContent.includes("(replay)"));
    assert.equal(strip.querySelector("[data-afx-bar]").style.width, "25.0%");
    assert.equal(strip.querySelector("[data-afx-left]").textContent, "0:08");
    assert.equal(strip.querySelectorAll(".afx-upi").length, 1);
    assert.equal(strip.querySelector(".afx-upmore").textContent, "(replay)");
    assert.equal(s.q('[data-r="pill"]').textContent, "Playing · 2 up next");
    const tagged = s.qa(".afx-row");
    assert.ok(tagged.some((r) => r.classList.contains("is-playing")));
    assert.ok(tagged.some((r) => r.classList.contains("is-waiting") && r.textContent.includes("Up next · 1st")));
    assert.ok(tagged.some((r) => r.textContent.includes("Ghost") && r.textContent.includes("Up next · 2nd")), "unknown queued alerts get synthetic rows");
    assert.ok(s.q('[data-a="row-skip"][data-id="p"]'));
    s.clock.tick(1000);
    assert.equal(strip.querySelector("[data-afx-left]").textContent, "0:07", "the 1s ticker updates the countdown");
});

test("strip: idle 'Starting next', no-timer and indeterminate bars, unknown playing alert", () => {
    const s = feed();
    s.f.set("queue", { queue: [{ alert_id: "x", type: "follow", username: "Q" }] });
    assert.ok(s.q('[data-r="strip"]').textContent.includes("Starting next"));
    assert.equal(s.q('[data-r="pill"]').textContent, "1 up next");
    s.f.set("queue", { playing: { type: "bits", username: "<b>X</b>" } });
    const strip = s.q('[data-r="strip"]');
    assert.ok(strip.querySelector(".bar.is-indet"));
    assert.equal(strip.querySelector(".who b").textContent, "<b>X</b>");
    assert.ok(strip.textContent.includes("cheered"));
    s.f.set("queue", { playing: { type: "bits" } });
    assert.ok(strip.textContent.includes("An alert"));
    s.f.set("settings", { timer: "off" });
    s.f.set("queue", { playing: { type: "bits" } });
    assert.ok((strip.querySelector(".bar")) === null);
    s.f.set("settings", { now_playing: false });
    assert.equal(strip.innerHTML, "");
    s.f.set("settings", { up_next: 0 });
    s.f.set("queue", { queue: [{ type: "follow" }] });
    assert.ok((strip.querySelector(".afx-up")) === null);
});

test("paused queue: strip lists waiting alerts with a Resume button; pill warns", () => {
    const s = feed();
    s.f.set("queue", { paused: true, queue: Array.from({ length: 7 }, (_, i) => ({ alert_id: "q" + i, type: "follow", username: "U" + i })) });
    const strip = s.q('[data-r="strip"]');
    assert.ok(strip.textContent.includes("These play in this order when you resume"));
    assert.equal(strip.querySelectorAll(".afx-upi").length, 5);
    assert.equal(strip.querySelector(".afx-upmore").textContent, "+2 more");
    assert.equal(s.q('[data-r="pill"]').textContent, "Alerts paused · 7 waiting");
    assert.ok(s.f.root.classList.contains("is-paused-state"));
    assert.ok(s.qa(".afx-row").some((r) => r.textContent.includes("Waiting · 1st")));
    s.q('[data-a="resume"]').click();
    assert.deepEqual(s.sent.at(-1), ["pause", { paused: false }]);
    s.f.set("queue", { paused: true, muted: true });
    assert.ok(strip.textContent.includes("New alerts wait in order below"));
    assert.equal(s.q('[data-r="pill"]').textContent, "Alerts paused");
    assert.equal(s.q('[data-r="mute"]').textContent, "volume_offUnmute");
    s.f.set("queue", { muted: true });
    assert.equal(s.q('[data-r="pill"]').textContent, "Ready · muted");
});

test("flags update pause/mute and a playing flag for senders without the contract", () => {
    const s = feed();
    s.f.set("flags", { paused: true, muted: true, playing: true });
    assert.equal(s.f.s.queue.paused, true);
    assert.equal(s.f.s.queue.muted, true);
    assert.equal(s.f.s.queue.playingFlag, true);
    s.f.set("flags", { paused: false });
    assert.equal(s.q('[data-r="pill"]').textContent, "Playing");
    assert.equal(s.q('[data-r="pause"]').className, "is-breath");
    s.f.set("queue", { contract: true });
    s.f.set("flags", { playing: true });
    assert.equal(s.f.s.queue.playingFlag, undefined);
    s.f.set("flags", null);
});

test("ticker layout groups up next, now playing, newest and earlier", () => {
    const s = feed();
    s.f.set("settings", { layout: "ticker" });
    s.f.set("live", [row({ id: "a", alert_id: "a" }), row({ id: "b", alert_id: "b", ts: NOW - 4000 }), row({ id: "c", alert_id: "c" })]);
    s.f.set("queue", { playing: { alert_id: "c", type: "follow" }, queue: [{ alert_id: "a", type: "follow" }] });
    const heads = s.qa(".afx-day").map((h) => h.textContent.replace(/(south|north).*/, ""));
    assert.deepEqual(heads, ["Up next", "Now playing", "Feed · newest first", "Earlier"]);
    s.f.set("live", [row({ id: "b", alert_id: "b", ts: NOW - 4000 })]);
    s.f.set("queue", {});
    assert.ok(s.q(".afx-row .newest"), "the newest row is marked even when it is earlier");
    s.f.set("queue", { paused: true, queue: [{ type: "follow" }] });
    assert.ok(s.q(".afx-day").textContent.startsWith("Waiting to play"));
    s.f.set("queue", {});
    s.f.set("live", []);
    assert.ok(s.q(".afx-empty").textContent.includes("No events yet"));
    s.f.set("live", [row()]);
    s.f.set("view", { search: "nomatch" });
    assert.ok(s.q(".afx-empty").textContent.includes("Nothing matches"));
});

test("item timer bars for playing and queued rows", () => {
    const s = feed();
    s.f.set("settings", { timer: "item" });
    s.f.set("live", [row({ id: "p", alert_id: "p" }), row({ id: "q", alert_id: "q" })]);
    s.f.set("queue", { playing: { alert_id: "p", type: "follow", started_at_ms: 1, duration_ms: 4000 }, queue: [{ alert_id: "q", type: "follow" }] });
    s.f.s.queue.playing.started_at_ms = s.win.Date.now() - 1000;
    s.f.renderList();
    assert.equal(s.q(".tbar.is-run").getAttribute("style"), "animation-duration:4000ms;animation-delay:-1000ms");
    assert.ok(s.q(".tbar.is-full"));
    s.f.set("queue", { playing: { alert_id: "p", type: "follow" } });
    assert.ok(s.q(".tbar.is-indet"));
});

test("dock rows show the playing tag under the name", () => {
    const s = feed({ dock: true });
    s.f.set("live", [row({ alert_id: "p" })]);
    s.f.set("queue", { playing: { alert_id: "p", type: "follow" } });
    assert.ok(s.q(".afx-row .body .st .playtag"));
});

test("history view: loading, error, empty, filtered-empty, day heads and pager", () => {
    const s = feed();
    s.q('[data-a="mode"][data-mode="history"]').click();
    assert.deepEqual(s.sent.at(-1), ["mode", { mode: "history" }]);
    assert.ok(s.q(".afx-empty").textContent.includes("Loading history"));
    s.f.set("history", { error: "boom" });
    assert.ok(s.q(".afx-empty").textContent.includes("Couldn't load stored alerts"));
    s.f.set("history", { rows: [] });
    assert.ok(s.q(".afx-empty").textContent.includes("No stored alerts yet"));
    s.f.set("history", { alerts: [row({ ts: NOW }), row({ id: "y", ts: NOW - 86400 })], page: 2, total_pages: 3, total_count: 1234 });
    assert.deepEqual(s.qa(".afx-day").map((d) => d.textContent), ["Today", "Yesterday"]);
    const pager = s.q(".afx-pager");
    assert.ok(pager.textContent.includes("Page 1 of 3"), "page comes from the view, not the payload");
    assert.ok(pager.textContent.includes("(1,234 alerts)"));
    assert.ok(pager.querySelector('[data-a="storage"]'));
    s.q('[data-a="page"][data-page="2"]').click();
    assert.deepEqual(s.sent.at(-1), ["page", { page: 2 }]);
    s.f.set("history", { rows: [row()], page: 2, total_pages: 3 });
    s.q('[data-a="page"][data-page="1"]').click();
    assert.deepEqual(s.sent.at(-1), ["page", { page: 1 }]);
    s.f.set("history", { rows: [row()], total_pages: 3 });
    const before = s.sent.length;
    s.f.onClick({ target: Object.assign(s.doc.createElement("button"), {}), stopPropagation() {} });
    const fake = s.doc.createElement("button");
    fake.setAttribute("data-a", "page");
    fake.setAttribute("data-page", "9");
    s.q('[data-r="pager"]').appendChild(fake);
    fake.click();
    assert.equal(s.sent.length, before, "out-of-range pages are ignored");
    s.f.set("view", { mode: "history", search: "nomatch" });
    assert.ok(s.q(".afx-empty").textContent.includes("Nothing on this page matches"));
    const d = feed({ dock: true });
    d.f.set("view", { mode: "history" });
    d.f.set("history", { rows: [row()] });
    assert.equal(d.q('[data-a="storage"]'), null);
});

test("by-person view: loading, error, empty, filtered and grouped cards", () => {
    const s = feed();
    s.q('[data-a="mode"][data-mode="person"]').click();
    assert.ok(s.q(".afx-empty").textContent.includes("Grouping the last 12 hours by person"));
    s.f.set("person", { success: false });
    assert.ok(s.q(".afx-empty").textContent.includes("Couldn't group alerts by person"));
    s.f.set("person", { groups: [], hours: 6 });
    assert.ok(s.q(".afx-empty").textContent.includes("No alerts to group in the last 6 hours"));
    s.f.set("view", { mode: "person", filters: { all: false, bits: false } });
    s.f.set("person", { groups: [], hours: 6 });
    assert.ok(s.q(".afx-empty").textContent.includes("Nobody matches"));
    s.f.set("view", { mode: "person" });
    s.f.set("person", {
        alert_count: 5, user_count: 1, truncation: "showing 50 of 80", groups: [
            { username: "bob", events: 1, last_ts: NOW - 10, totals: [{ kind: "bits", text: "500 bits" }], items: [{ kind: "bits", text: "cheered", count: 2 }, { kind: "follow", text: "followed", count: 1, color: "red", icon: "x" }] },
            { username: "", lines: ["did <a> thing"], last_ts: NOW - 7200 },
        ],
    });
    assert.equal(s.q(".afx-cond-head").textContent, "history5 events from 1 person in the last 12 hoursshowing 50 of 80");
    const users = s.qa(".afx-user");
    assert.equal(users[0].querySelector(".afx-ava").textContent, "B");
    assert.equal(users[0].querySelector(".afx-user-sub").textContent, "1 event · just now");
    assert.equal(users[0].querySelector(".afx-ln .x").textContent, "×2");
    assert.equal(users[1].querySelector(".afx-ava").textContent, "?");
    assert.equal(users[1].querySelector(".afx-user-sub").textContent, "1 event · 2h ago");
    assert.equal(users[1].querySelector(".afx-ln .t").textContent, "did <a> thing");
    s.f.set("person", { groups: [{ username: "z", events: 3 }], alert_count: 3 });
    assert.ok(s.q(".afx-cond-head").textContent.includes("3 events from 1 person"), "user_count falls back to the number of groups");
    assert.equal(s.q(".afx-user-sub").textContent, "3 events");
});

test("view updates: older revisions are ignored, search is kept while typing", () => {
    const s = feed();
    s.f.set("view", { mode: "history", revision: 5, page: 3, search: "abc" });
    assert.equal(s.f.mode(), "history");
    assert.equal(s.f.s.view.page, 3);
    assert.equal(s.f.r.search.value, "abc");
    s.f.set("view", { mode: "live", revision: 4 });
    assert.equal(s.f.mode(), "history");
    s.f.r.search.focus();
    s.f.r.search.value = "typing";
    s.f.set("view", { mode: "live", revision: 6, search: "server" });
    assert.equal(s.f.s.view.search, "abc", "server search does not overwrite what the user is typing");
    assert.equal(s.f.r.search.value, "typing");
    s.f.set("view", { revision: 7, search: "typing" });
    assert.equal(s.f.s.view.search, "typing");
    s.f.set("view", null);
    assert.equal(s.f.mode(), "live");
});

test("buttons send pause, mute, skip, row-skip, settings, help, storage and copy-dock", () => {
    const s = feed();
    s.f.set("live", [row({ alert_id: "p" })]);
    s.f.set("queue", { playing: { alert_id: "p", type: "follow" } });
    s.q('[data-r="pause"]').click();
    s.q('[data-r="mute"]').click();
    s.q('[data-r="skip"]').click();
    s.q('[data-a="row-skip"]').click();
    s.q('[data-a="settings"]').click();
    s.q('[data-a="help"]').click();
    s.q('[data-a="copy-dock"]').click();
    assert.deepEqual(s.sent, [
        ["pause", { paused: true }], ["mute", { muted: true }], ["skip", { alert_id: null }], ["skip", { alert_id: "p" }],
        ["settings", {}], ["help", {}], ["copy-dock", {}],
    ]);
    s.q('[data-a="mode"][data-mode="live"]').click();
    assert.equal(s.sent.length, 7, "clicking the current tab sends nothing");
    const noSend = setup();
    const m = noSend.doc.createElement("div");
    const f = noSend.F.create(m);
    assert.doesNotThrow(() => f.root.querySelector('[data-a="skip"]').click());
});

test("clicking a replayable row asks for a replay; other clicks do nothing", () => {
    const s = feed();
    s.f.set("live", [row({ alert_id: "a9", user: "Ann", label: "Follow" }), row({ id: "x", alert_id: null })]);
    s.qa(".afx-row")[0].querySelector(".what").click();
    assert.deepEqual(s.sent, [["replay", { alert_id: "a9", user: "Ann", label: "Follow" }]]);
    s.qa(".afx-row")[1].click();
    s.q(".afx-list").click();
    assert.equal(s.sent.length, 1);
});

test("filters menu: toggling, 'all', count badge, outside click closes, clear filters", () => {
    const s = feed();
    s.f.set("live", [row()]);
    s.q('[data-a="filters"]').click();
    assert.ok(s.q('[data-r="menu"]').classList.contains("is-open"));
    const box = s.q('[data-filter="follows"]');
    box.checked = false;
    fire(s.win, box, "change");
    assert.deepEqual(s.sent.at(-1), ["filter", { key: "follows", value: false }]);
    assert.equal(s.q('[data-r="fcount"]').textContent, "10/11");
    assert.equal(s.q('[data-r="fcount"]').style.display, "");
    assert.ok(s.q(".afx-empty").textContent.includes("Nothing matches"));
    box.checked = true;
    fire(s.win, box, "change");
    assert.equal(s.f.s.view.filters.all, true, "turning the last one back on re-enables All");
    const all = s.q('[data-filter="all"]');
    s.f.s.view.filters.bits = false;
    all.checked = true;
    fire(s.win, all, "change");
    assert.equal(s.f.s.view.filters.bits, true);
    fire(s.win, s.q('[data-r="search"]'), "change"); // not a filter box
    s.doc.body.click();
    assert.ok(!s.q('[data-r="menu"]').classList.contains("is-open"));
    s.f.set("view", { filters: { raids: false, all: false }, search: "x" });
    s.q('[data-a="clear-filters"]').click();
    assert.deepEqual(s.sent.at(-1), ["filter", { key: "all", value: true }]);
    assert.equal(s.f.r.search.value, "");
});

test("search input is debounced, Escape and the clear button empty it", () => {
    const s = feed();
    s.f.set("live", [row({ user: "Alice" }), row({ id: "2", user: "Bob" })]);
    const box = s.f.r.search;
    box.value = "bo";
    fire(s.win, box, "input");
    assert.equal(s.qa(".afx-row").length, 1, "filters locally at once");
    assert.ok(box.parentNode.classList.contains("has-text"));
    box.value = "bob";
    fire(s.win, box, "input");
    s.clock.tick(249);
    assert.equal(s.sent.length, 0);
    s.clock.tick(1);
    assert.deepEqual(s.sent, [["search", { text: "bob" }]]);
    fire(s.win, box, "keydown", { key: "a" });
    fire(s.win, box, "keydown", { key: "Escape" });
    assert.equal(box.value, "");
    box.value = "x";
    s.q('[data-a="clear-search"]').click();
    assert.equal(box.value, "");
    assert.equal(s.doc.activeElement, box);
    s.clock.tick(250);
    assert.deepEqual(s.sent.at(-1), ["search", { text: "" }]);
});

test("sync button reflects whether the OBS dock is open", () => {
    const s = feed();
    assert.ok(s.q('[data-r="sync"]').textContent.includes("In sync with the OBS dock"));
    s.f.set("meta", { dock_open: false });
    assert.ok(s.q('[data-r="sync"]').classList.contains("is-off"));
    assert.ok(s.q('[data-r="sync"]').textContent.includes("copy link"));
    s.f.set("meta", null);
});

test("ticker: relative times refresh every 15s, list re-renders every 60s, dead hosts are dropped", () => {
    const s = feed();
    s.f.set("live", [row({ ts: NOW })]);
    assert.equal(s.q(".time").textContent, "just now");
    s.clock.tick(15000);
    assert.equal(s.q(".time").textContent, "just now");
    s.clock.tick(120000);
    assert.equal(s.q(".time").textContent, "2m");
    s.f.tick = () => { throw new Error("broken"); };
    assert.doesNotThrow(() => s.clock.tick(1000));
    s.f.hostBound = true;
    s.mount.remove();
    s.clock.tick(1000);
    assert.equal(s.F.instances.includes(s.f), false);
});

test("bindHosts creates one Feed per host, applies changed slots and re-binds a new mount", () => {
    const s = setup();
    const emitted = [];
    s.win.emitEvent = (name, data) => emitted.push([name, plain(data)]);
    const slotHtml = (name, rev, json) => `<div class="afx-slot" data-slot="${name}" data-rev="${rev}" data-json='${JSON.stringify(json).replace(/'/g, "&#39;")}'></div>`;
    s.doc.body.innerHTML = '<div class="afx-host" data-afx-app="1"><div class="afx-mount"></div>' +
        slotHtml("settings", "1", { layout: "cards" }) + slotHtml("live", "1", [row({ user: "Host'User" })]) +
        '<div class="afx-slot" data-slot="queue" data-rev="1" data-json="{bad"></div><span>other</span></div>' +
        '<div class="afx-host" data-afx-app="1"></div>';
    s.F.bindHosts();
    const host = s.doc.querySelector(".afx-host");
    const inst = host.__afx;
    assert.ok(inst);
    assert.equal(host.getAttribute("data-afx-bound"), "4");
    assert.equal(inst.s.settings.layout, "cards");
    assert.equal(host.querySelector(".afx-row .user").textContent, "Host'User");
    assert.ok(s.win.__console.some(([k, a]) => k === "warn" && a[0] === "activity feed: bad slot"));
    host.querySelector('.afx-row .user').click();
    assert.deepEqual(emitted, [["mycelian-afx", { action: "replay", alert_id: "a1", user: "Host'User", label: "Follow" }]]);
    s.F.bindHosts();
    assert.equal(host.__afx, inst, "unchanged revisions keep the instance");
    host.querySelector('[data-slot="live"]').setAttribute("data-rev", "2");
    host.querySelector('[data-slot="live"]').setAttribute("data-json", "[]");
    s.clock.tick(250); // the 250ms binder
    assert.equal(inst.s.live.length, 0);
    const newMount = s.doc.createElement("div");
    newMount.className = "afx-mount";
    host.replaceChild(newMount, host.querySelector(".afx-mount"));
    s.F.bindHosts();
    assert.notEqual(host.__afx, inst);
    assert.equal(s.F.instances.includes(inst), false);
    s.win.emitEvent = undefined;
    assert.doesNotThrow(() => host.__afx.send("x", {}));
});

test("health reports bound and painted hosts", () => {
    const s = setup();
    assert.equal(s.F.health().ok, false);
    s.doc.body.innerHTML = '<div class="afx-host" data-afx-app="1"><div class="afx-mount"></div></div>';
    const host = s.doc.querySelector(".afx-host");
    host._rect = { width: 300, height: 400 };
    s.F.bindHosts();
    const h = plain(s.F.health());
    assert.deepEqual(h, { ok: true, version: 4, hosts: 1, bound: 1, painted: 1, rows: 1, visible: 1 });
});

test("a newer copy already on the page wins; the binder interval is installed once", () => {
    const win = makeWindow();
    win.MycelianFeed = { version: 99 };
    loadScript(win, FILE);
    assert.equal(win.MycelianFeed.version, 99);
    const s = setup();
    const binder = s.win.__mycelianFeedBinder;
    s.win.MycelianFeed = { version: 1 };
    loadScript(s.win, FILE);
    assert.equal(s.win.__mycelianFeedBinder, binder);
    assert.equal(s.win.MycelianFeed.version, 4);
});

test("a binder failure is swallowed until the next tick", () => {
    const s = setup();
    s.win.MycelianFeed.bindHosts = () => { throw new Error("not ready"); };
    assert.doesNotThrow(() => s.clock.tick(250));
});

test("by-person view shows the server's truncation note",
    () => {
        const s = feed();
        s.f.set("view", { mode: "person" });
        s.f.set("person", { alert_count: 2, user_count: 1, truncation: "Showing first 50 of 80 people", groups: [{ username: "a", events: 2 }] });
        assert.ok(s.q(".afx-cond-head").textContent.includes("Showing first 50 of 80 people"));
    });

test("personHtml renders a truncation note when one is present in state", () => {
    const s = feed();
    s.f.set("view", { mode: "person" });
    s.f.s.person = { groups: [{ username: "a", events: 2 }], hours: 12, alert_count: 2, user_count: 1, loading: false, error: "", truncation: "Showing first 50 of 80 people" };
    s.f.renderList();
    assert.ok(s.q(".afx-cond-head .afx-arrow").textContent.includes("Showing first 50 of 80 people"));
});

test("by-person cards show the Twitch profile picture over the initial", () => {
    const s = feed();
    s.f.set("view", { mode: "person" });
    const pic = "https://static-cdn.jtvnw.net/jtv_user_pictures/x-profile_image-300x300.png";
    s.f.set("person", {
        alert_count: 3, user_count: 3, groups: [
            { username: "bob", events: 1, avatar: pic },
            { username: "tipper", events: 1 },
            { username: "eve", events: 1, avatar: 'http://evil/x.png" onerror="alert(1)' },
        ],
    });
    const users = s.qa(".afx-user");
    const ava = users[0].querySelector(".afx-ava");
    assert.ok(ava.className.includes("has-photo"));
    assert.equal(ava.textContent, "B", "the initial stays underneath in case the picture fails");
    const img = ava.querySelector("img");
    assert.equal(img.getAttribute("src"), pic);
    assert.equal(img.getAttribute("onerror"), "this.remove()");
    assert.equal(img.getAttribute("referrerpolicy"), "no-referrer");
    assert.equal(img.getAttribute("loading"), null, "lazy images may never load once the page is open");
    assert.equal(users[1].querySelector(".afx-ava img"), null);
    assert.ok(!users[1].querySelector(".afx-ava").className.includes("has-photo"));
    assert.equal(users[2].querySelector(".afx-ava img"), null, "only https pictures are used");
});

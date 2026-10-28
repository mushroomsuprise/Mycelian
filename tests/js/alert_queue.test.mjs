// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/alert_queue.js: holder/spectator handshake guard, seq parsing, skip
// matching and the playing/progress/complete/started emitters with ack-driven retries.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, makeIo, plain } from "./helpers/env.mjs";

const FILE = "assets/default_assets/alert_queue.js";

function setup(url = "http://127.0.0.1:5000/alerts", extra) {
    const win = makeWindow({ url, extra });
    loadScript(win, FILE);
    const io = makeIo();
    return { win, AQ: win.MycelianOverlay.alertQueue, socket: io("http://x"), io };
}

test("currentRoute strips slashes and decodes the path", () => {
    assert.equal(setup("http://h/alerts").AQ.currentRoute(), "alerts");
    assert.equal(setup("http://h/boo%20chat/").AQ.currentRoute(), "boo chat");
    assert.equal(setup("http://h/a/b//").AQ.currentRoute(), "a/b");
    assert.equal(setup("http://h/").AQ.currentRoute(), "");
});

test("currentRoute keeps a path that is not valid percent-encoding", () => {
    const { AQ, win } = setup("http://h/x");
    Object.defineProperty(win.location, "pathname", { get: () => "/bad%E0%A4%A", configurable: true });
    assert.equal(AQ.currentRoute(), "bad%E0%A4%A");
});

test("currentRoute without location is empty", () => {
    const { AQ, win } = setup();
    win.location = undefined;
    assert.equal(AQ.currentRoute(), "");
});

test("parseSeq accepts ints and numeric strings only", () => {
    const { AQ } = setup();
    assert.equal(AQ.parseSeq(5), 5);
    assert.equal(AQ.parseSeq("12"), 12);
    assert.equal(AQ.parseSeq("7.9"), 7);
    assert.equal(AQ.parseSeq(0), 0);
    assert.equal(AQ.parseSeq(null), null);
    assert.equal(AQ.parseSeq(undefined), null);
    assert.equal(AQ.parseSeq(""), null);
    assert.equal(AQ.parseSeq("abc"), null);
});

test("isAllowedHolder defaults to the alerts route", () => {
    assert.equal(setup("http://h/alerts").AQ.isAllowedHolder({}), true);
    assert.equal(setup("http://h/alerts").AQ.isAllowedHolder(null), true);
    assert.equal(setup("http://h/alerts").AQ.isAllowedHolder({ queue_holders: [] }), true);
    assert.equal(setup("http://h/follow").AQ.isAllowedHolder({}), false);
});

test("isAllowedHolder matches exact, slashed and normalised holder names", () => {
    const { AQ } = setup("http://h/Boo%20Chat");
    assert.equal(AQ.isAllowedHolder({ queue_holders: ["Boo Chat"] }), true);
    assert.equal(AQ.isAllowedHolder({ queue_holders: ["/Boo Chat/"] }), true);
    assert.equal(AQ.isAllowedHolder({ queue_holders: ["boochat"] }), true);
    assert.equal(AQ.isAllowedHolder({ queue_holders: [null, "other"] }), false);
});

test("isAllowedHolder is false on the root page", () => {
    assert.equal(setup("http://h/").AQ.isAllowedHolder({ queue_holders: [""] }), false);
});

test("isAllowedHolder: a route of only punctuation needs an exact match", () => {
    const { AQ } = setup("http://h/--");
    assert.equal(AQ.isAllowedHolder({ queue_holders: ["__"] }), false);
    assert.equal(AQ.isAllowedHolder({ queue_holders: ["--"] }), true);
});

test("skipMatches: no seq means whatever plays, otherwise exact seq", () => {
    const { AQ } = setup();
    assert.equal(AQ.skipMatches({}, 3), true);
    assert.equal(AQ.skipMatches(null, 3), true);
    assert.equal(AQ.skipMatches({ queue_seq: "3" }, 3), true);
    assert.equal(AQ.skipMatches({ queue_seq: 4 }, "3"), false);
    assert.equal(AQ.skipMatches({ queue_seq: 4 }, null), false);
});

test("guardHandshakeEmits lets holders emit and blocks spectators", () => {
    const holder = setup("http://h/alerts");
    holder.AQ.guardHandshakeEmits(holder.socket);
    holder.socket.emit("alert_playing", { queue_seq: 1 });
    holder.socket.emit("chat", 1);
    assert.deepEqual(plain(holder.socket.emitted), [["alert_playing", { queue_seq: 1 }], ["chat", 1]]);

    const spectator = setup("http://h/follow");
    spectator.AQ.guardHandshakeEmits(spectator.socket);
    for (const ev of ["alert_playing", "alert_progress", "alert_complete", "alert_started"]) {
        assert.equal(spectator.socket.emit(ev, { queue_seq: 1 }), spectator.socket);
    }
    spectator.socket.emit("other_event");
    assert.deepEqual(plain(spectator.socket.emitted), [["other_event"]]);
});

test("guardHandshakeEmits uses queue_holders from the last next_alert, overridable by the payload", () => {
    const { AQ, socket } = setup("http://h/follow");
    AQ.guardHandshakeEmits(socket);
    socket._recv("next_alert", { queue_holders: ["follow"], user: "x" });
    socket.emit("alert_complete", { queue_seq: 2 });
    socket.emit("alert_complete", { queue_seq: 3, queue_holders: ["alerts"] });
    socket.emit("alert_complete");
    assert.deepEqual(plain(socket.emitted), [["alert_complete", { queue_seq: 2 }], ["alert_complete"]]);
    socket._recv("next_alert", null);
    socket.emit("alert_playing", "not-an-object");
    assert.equal(socket.emitted.length, 2, "null alert falls back to alerts holder");
});

test("guardHandshakeEmits is idempotent and tolerates odd sockets", () => {
    const { AQ, socket } = setup();
    assert.equal(AQ.guardHandshakeEmits(socket), socket);
    const wrapped = socket.emit;
    AQ.guardHandshakeEmits(socket);
    assert.equal(socket.emit, wrapped);
    assert.equal(AQ.guardHandshakeEmits(null), null);
    const noEmit = { on() {} };
    assert.equal(AQ.guardHandshakeEmits(noEmit), noEmit);
    assert.equal(noEmit.__mycelianHandshakeGuarded, true);
    const noOn = { emit() { return "sent"; } };
    AQ.guardHandshakeEmits(noOn);
    assert.equal(noOn.emit("alert_playing", {}), "sent", "on /alerts the default holder may emit");
});

test("attach: playing/progress/started emit with the current route", () => {
    const { AQ, socket } = setup("http://h/alerts");
    const q = AQ.attach(socket);
    q.playing("4");
    q.progress(4, { visual: 1, audio: 0, tts: "y", resolve: true, remaining_ms: 250 });
    q.progress(4);
    q.started(4, 1499.6);
    assert.deepEqual(plain(socket.emitted), [
        ["alert_playing", { queue_seq: 4, route: "alerts" }],
        ["alert_progress", { queue_seq: 4, route: "alerts", visual: true, audio: false, tts: true, resolve: true, remaining_ms: 250 }],
        ["alert_progress", { queue_seq: 4, route: "alerts", visual: false, audio: false, tts: false, resolve: false, remaining_ms: 0 }],
        ["alert_started", { queue_seq: 4, duration_ms: 1500, route: "alerts" }],
    ]);
});

test("attach: bad seq or duration emits nothing", () => {
    const { AQ, socket } = setup();
    const q = AQ.attach(socket);
    q.playing(null);
    q.progress("x", {});
    q.complete("");
    q.started(1, 0);
    q.started(1, -5);
    q.started(1, "abc");
    q.started(null, 100);
    assert.equal(socket.emitted.length, 0);
});

test("attach with no socket is inert", () => {
    const { AQ } = setup();
    const q = AQ.attach(null);
    assert.doesNotThrow(() => { q.playing(1); q.progress(1); q.complete(1); q.started(1, 10); q.cancelComplete(); });
});

test("complete retries every 500ms until the matching ack", () => {
    const { AQ, socket, win } = setup();
    const q = AQ.attach(socket);
    q.complete(7);
    assert.equal(socket.emitted.length, 1);
    win.__clock.tick(500);
    win.__clock.tick(500);
    assert.equal(socket.emitted.length, 3);
    socket._recv("alert_complete_ack", { queue_seq: 6 });
    socket._recv("alert_complete_ack", {});
    socket._recv("alert_complete_ack", null);
    win.__clock.tick(500);
    assert.equal(socket.emitted.length, 4, "wrong / missing ack keeps retrying");
    socket._recv("alert_complete_ack", { queue_seq: "7" });
    win.__clock.tick(5000);
    assert.equal(socket.emitted.length, 4);
    assert.deepEqual(plain(socket.emitted[0]), ["alert_complete", { queue_seq: 7, route: "alerts" }]);
    assert.equal(win.__clock.pending(), 0);
});

test("a newer complete replaces the pending one; cancelComplete stops retries", () => {
    const { AQ, socket, win } = setup();
    const q = AQ.attach(socket);
    q.complete(1);
    q.complete(2);
    win.__clock.tick(500);
    assert.deepEqual(socket.emitted.map((e) => e[1].queue_seq), [1, 2, 2]);
    q.cancelComplete();
    win.__clock.tick(2000);
    assert.equal(socket.emitted.length, 3);
    socket._recv("alert_complete_ack", { queue_seq: 2 });
});

test("an ack that arrives during the emit stops the scheduled retry", () => {
    const { AQ, socket, win } = setup();
    const q = AQ.attach(socket);
    const emit = socket.emit;
    socket.emit = function (ev, data) {
        const r = emit.apply(socket, arguments);
        if (ev === "alert_complete") { socket._recv("alert_complete_ack", { queue_seq: data.queue_seq }); }
        return r;
    };
    q.complete(9);
    win.__clock.tick(500);
    win.__clock.tick(500);
    assert.equal(socket.emitted.length, 1);
    assert.equal(win.__clock.pending(), 0);
});

test("an ack with nothing pending is ignored", () => {
    const { AQ, socket } = setup();
    AQ.attach(socket);
    assert.doesNotThrow(() => socket._recv("alert_complete_ack", { queue_seq: 1 }));
});

test("existing MycelianOverlay.connect is wrapped once so its sockets get the guard", () => {
    const io = makeIo();
    const win = makeWindow({ url: "http://h/follow" });
    win.MycelianOverlay = { connect: (url, opts) => io(url, opts), other: 1 };
    loadScript(win, FILE);
    const s = win.MycelianOverlay.connect("u", { a: 1 });
    assert.equal(s.url, "u");
    assert.equal(s.__mycelianHandshakeGuarded, true);
    s.emit("alert_playing", {});
    assert.equal(s.emitted.length, 0);
    assert.equal(win.MycelianOverlay.other, 1);
    const wrapped = win.MycelianOverlay.connect;
    assert.equal(wrapped.__mycelianHandshakeWrapped, true);
    loadScript(win, FILE);
    assert.equal(win.MycelianOverlay.connect, wrapped, "second load does not double-wrap");
});

test("exports the public API", () => {
    const { AQ } = setup();
    assert.deepEqual(Object.keys(AQ).sort(), ["attach", "currentRoute", "guardHandshakeEmits", "isAllowedHolder", "parseSeq", "skipMatches"]);
});

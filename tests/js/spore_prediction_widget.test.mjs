// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// templates/_boilerplates/spore_data_runtime.js: the Prediction widget (sporePaintPrediction,
// sporeBindPrediction): outcome rows, stats, the Live/locked/won/refunded chip, timer bar,
// autohide after the result, the prediction_result output and the feed "repaint" target.

import test from "node:test";
import assert from "node:assert/strict";
import { plain } from "./helpers/env.mjs";
import { setupRuntime as setup } from "./helpers/spore_runtime.mjs";

function predHtml(attrs = "") {
    return '<div id="sporeRoot" data-mycelian-template="predictions"></div><div id="pr" data-spore-type="prediction" ' + attrs +
        '><div class="spore-pred-head"><div class="spore-pred-title"></div><div class="spore-pred-chip"></div></div>' +
        '<div class="spore-pred-timer"><div class="spore-pred-timer-track"><div class="spore-pred-timer-fill"></div></div></div>' +
        '<div class="spore-pred-outcomes"></div>' +
        '<div class="spore-pred-foot"><span class="spore-pred-foot-text"></span><span class="spore-pred-total"></span></div></div>';
}

function outcomes(over = {}) {
    return [
        Object.assign({ id: "a", name: "Yes", color: "blue", users: 30, points: 75000, percent: 75, ratio: 1.33,
            top_predictors: [{ name: "Bob", points_used: 20000, points_won: null }] }, over.a || {}),
        Object.assign({ id: "b", name: "No", color: "pink", users: 1, points: 25000, percent: 25, ratio: 4,
            top_predictors: [] }, over.b || {}),
    ];
}

test("compact numbers, colours and the winner name", () => {
    const { I, doc } = setup({ html: predHtml('data-spore-pred-extra-colors="#111, #222"') });
    assert.equal(I.sporePredCompact(1234), "1,234");
    assert.equal(I.sporePredCompact(45678), "45.7K");
    assert.equal(I.sporePredCompact(2500000), "2.5M");
    assert.equal(I.sporePredCompact(3e9), "3B");
    assert.equal(I.sporePredCompact("x"), "0");
    const cfg = I.sporePredConfig(doc.getElementById("pr"));
    assert.equal(I.sporePredColor(cfg, 0, { color: "blue" }), "#387aff");
    assert.equal(I.sporePredColor(cfg, 1, { color: "pink" }), "#f5009b");
    assert.equal(I.sporePredColor(cfg, 2, {}), "#111");
    assert.equal(I.sporePredColor(cfg, 3, {}), "#222");
    assert.equal(I.sporePredWinnerName({ winning_outcome_id: "b", outcomes: outcomes() }), "No");
    assert.equal(I.sporePredWinnerName({ outcomes: [{ name: "Z", winner: true }] }), "Z");
    assert.equal(I.sporePredWinnerName(null), "");
});

test("binding asks for state once, paints an active prediction and counts down to the lock", () => {
    const s = setup({ html: predHtml('data-spore-pred-top="true"') });
    const el = s.doc.getElementById("pr");
    s.I.sporeBindPrediction(el, false);
    s.I.sporeBindPrediction(el, false);
    assert.deepEqual(plain(s.socket.emitted.filter((e) => e[0] === "prediction_request_state")), [["prediction_request_state", { template: "predictions" }]]);
    assert.equal(el.style.display, "none", "idle: hidden");
    const now = s.win.Date.now() / 1000;
    const active = { template: "predictions", prediction_id: "p1", status: "active", title: "Win?", outcomes: outcomes(),
        total_points: 100000, total_users: 31, locks_at: now + 60, server_now: now };
    s.socket._recv("prediction_state", active);
    assert.equal(el.style.display, "");
    assert.equal(el.querySelector(".spore-pred-title").textContent, "Win?");
    assert.deepEqual(el.querySelectorAll(".spore-pred-name").map((n) => n.textContent), ["Yes", "No"]);
    assert.deepEqual(el.querySelectorAll(".spore-pred-pct").map((n) => n.textContent), ["75%", "25%"]);
    assert.equal(el.querySelector(".spore-pred-bar-fill").style.width, "75%");
    assert.equal(el.querySelector(".spore-pred-stats").textContent, "75K301:1.33");
    assert.equal(el.querySelector(".spore-pred-top").textContent, "Top: Bob · 20K");
    assert.equal(el.querySelectorAll(".spore-pred-top")[1].hidden, true);
    assert.equal(el.querySelector(".spore-pred-outcome").style.getPropertyValue("--sp-pc"), "#387aff");
    assert.equal(el.querySelector(".spore-pred-total").textContent, "100K points · 31 predictors");
    const chip = el.querySelector(".spore-pred-chip");
    assert.equal(chip.textContent, "01:00");
    assert.ok(chip.classList.contains("is-live"));
    assert.equal(el.querySelector(".spore-pred-timer").hidden, false);
    s.clock.tick(30000);
    assert.equal(chip.textContent, "00:30");
    assert.equal(el.querySelector(".spore-pred-timer-fill").style.width, "50%");
    const firstRow = el.querySelector(".spore-pred-outcome");
    s.socket._recv("prediction_sync", Object.assign({}, active, { total_users: 1 }));
    assert.equal(el.querySelector(".spore-pred-outcome"), firstRow, "same outcomes: rows reused");
    assert.equal(el.querySelector(".spore-pred-total").textContent, "100K points · 1 predictor");
    s.socket._recv("prediction_state", Object.assign({}, active, { template: "other", title: "Not mine" }));
    assert.equal(el.querySelector(".spore-pred-title").textContent, "Win?", "other templates are ignored");
});

test("locked, resolved (winner, dim, output, autohide) and refunded", () => {
    const s = setup({ html: predHtml('data-spore-pred-autohide="5" data-spore-pred-title="Fixed title" data-spore-pred-ratio="false" data-spore-pred-users="0" data-spore-pred-points="no"') });
    const el = s.doc.getElementById("pr");
    s.I.sporeBindPrediction(el, false);
    const now = s.win.Date.now() / 1000;
    const base = { template: "predictions", prediction_id: "p1", outcomes: outcomes(), total_points: 100000, total_users: 31 };
    s.socket._recv("prediction_state", Object.assign({ status: "locked" }, base));
    assert.equal(el.querySelector(".spore-pred-chip").textContent, "Predictions locked");
    assert.equal(el.querySelector(".spore-pred-timer").hidden, true);
    assert.equal(el.querySelector(".spore-pred-title").textContent, "Fixed title");
    assert.equal(el.querySelector(".spore-pred-stats").hidden, true, "every stat off: row hidden");
    const resolved = Object.assign({}, base, { status: "resolved", winning_outcome_id: "b", ended_at: now - 1, server_now: now,
        outcomes: outcomes({ b: { winner: true } }) });
    s.socket._recv("prediction_ended", resolved);
    assert.equal(el.querySelector(".spore-pred-chip").textContent, "No won!");
    assert.ok(el.classList.contains("is-resolved"));
    assert.ok(el.querySelectorAll(".spore-pred-outcome")[1].classList.contains("is-winner"));
    assert.ok(!el.querySelectorAll(".spore-pred-outcome")[0].classList.contains("is-winner"));
    const out = plain(s.socket.emitted.find((e) => e[0] === "prediction_result"));
    assert.equal(out[1].winner, "No");
    assert.equal(out[1].status, "resolved");
    assert.equal(out[1].total_points, 100000);
    s.clock.tick(4500);
    assert.equal(el.style.display, "none", "hidden once the autohide time has passed");
    s.socket._recv("prediction_state", Object.assign({}, resolved, { ended_at: now - 100 }));
    assert.equal(el.style.display, "none", "an old result is not shown again");
    s.socket._recv("prediction_ended", Object.assign({}, base, { status: "canceled", ended_at: s.win.Date.now() / 1000, prediction_id: "p2" }));
    assert.equal(el.style.display, "");
    assert.equal(el.querySelector(".spore-pred-chip").textContent, "Refunded");
    assert.equal(plain(s.socket.emitted.filter((e) => e[0] === "prediction_result")).pop()[1].winner, null);
    s.socket._recv("prediction_ended", { template: "other" });
    s.socket._recv("prediction_ended", null);
    s.socket._recv("prediction_state", Object.assign({ status: "active" }, base));
    assert.equal(el.querySelector(".spore-pred-chip").textContent, "Live", "no lock time: just Live");
});

test("feed-driven widgets only announce results; start-hidden widgets animate in and out; repaint target", () => {
    const s = setup({ html: predHtml('data-spore-hidden="true" data-spore-pred-autohide="0"') });
    const shown = [];
    s.win.sporeShow = (id) => { shown.push(["show", id]); s.doc.getElementById(id).removeAttribute("data-spore-hidden"); };
    s.win.sporeHide = (id) => { shown.push(["hide", id]); s.doc.getElementById(id).setAttribute("data-spore-hidden", "true"); };
    const el = s.doc.getElementById("pr");
    s.I.sporeBindPrediction(el, true);
    s.socket._recv("prediction_state", { template: "predictions", status: "active", outcomes: outcomes() });
    assert.deepEqual(shown, [], "feed-driven: prediction_state is the feed's job");
    const repaint = s.I.sporeFeedTargets.repaint;
    s.I.sporePaintPrediction(el, { template: "predictions", status: "resolved", outcomes: outcomes({ a: { winner: true } }) });
    assert.equal(el.style.display, "", "autohide 0 leaves the result up");
    s.I.sporePaintPrediction(el, { status: "idle" });
    assert.deepEqual(shown, [["show", "pr"], ["hide", "pr"]]);
    {
        repaint({}, el, null, { template: "other", status: "active", outcomes: outcomes() });
        assert.equal(el.getAttribute("data-spore-hidden"), "true", "repaint ignores other templates");
        repaint({}, el, null, { template: "predictions", status: "active", outcomes: outcomes() });
        assert.equal(el.getAttribute("data-spore-hidden"), null);
    }
    s.I.sporePaintPrediction(null);
    s.I.sporePaintPrediction(el);
    const noSock = setup({ socket: false, html: predHtml() });
    noSock.I.sporeBindPrediction(noSock.doc.getElementById("pr"), false);
});

test("widget init binds prediction blocks", () => {
    const s = setup({ html: predHtml() });
    s.win.sporeInitWidgets();
    assert.equal(s.doc.getElementById("pr").getAttribute("data-spore-pred-bound"), "1");
    assert.ok(s.socket.emitted.some((e) => e[0] === "prediction_request_state"));
});

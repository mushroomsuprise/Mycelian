// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// templates/_spore/steam_achievements.spore.json advanced_js: the Steam achievements overlay
// (a port of the PSN trophies overlay). Runs the template's own Advanced JS against a fake DOM and socket.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, readSource, run, fire } from "./helpers/env.mjs";

const MODEL = JSON.parse(readSource("templates/_spore/steam_achievements.spore.json"));
const DOM = `<div id="root">
  <img id="game_icon" src=""><div id="game_title"></div><div id="ach_message"></div><div id="level_info"></div>
  <div id="progress"><div class="spore-progress-track"><div class="spore-progress-fill" style="width:0%"></div></div></div>
  <div id="progress_label"></div><img id="perfect_badge" data-spore-hidden="true"><div id="ach_change"></div>
</div>`;

function setup(opts = {}) {
    const win = makeWindow({ localStorage: opts.localStorage });
    win.document.body.innerHTML = DOM;
    run(win, "var socket = io('http://127.0.0.1:5000');");
    const socket = win.io.sockets[0];
    if (opts.runtime !== false) {
        // The Spore runtime's progress bar registry and show/hide helpers, reduced to what the widget uses.
        run(win, `
          window.__sporeProgressBars = [{ elementId: 'progress', counter_id: 'el' }];
          window.sporeUpdateProgressBars = function () {
            var s = window.__sporeProgressBars[0];
            document.querySelector('#progress .spore-progress-fill').style.width = (s.override_value / s.override_max * 100) + '%';
          };
          window.sporeShow = function (id) { document.getElementById(id).setAttribute('data-shown', '1'); };
          window.sporeHide = function (id) { document.getElementById(id).removeAttribute('data-shown'); };
          window.sporeSetText = function (id, t) { document.getElementById(id).textContent = t; };
        `);
    }
    loadScript(win, "templates/_spore/steam_achievements.spore.json", { source: MODEL.advanced_js, expose: false });
    const $ = (id) => win.document.getElementById(id);
    return { win, socket, $, clock: win.__clock || null, send: (d) => socket._recv("steam_data_update", d) };
}

const GAME = {
    connection_status: "Connected", current_game_appid: 620, current_game_name: "Portal 2",
    current_game_capsule_url: "https://cdn/620/library_600x900.jpg", current_game_art_url: "https://cdn/620/header.jpg",
    achievement_counts: { unlocked: 10, total: 40 }, current_game_progress: 25, steam_level: 12,
};

test("asks for Steam data on load, on reconnect, on a timer and when the page shows again", () => {
    const s = setup();
    const asks = () => s.socket.emitted.filter((e) => e[0] === "get_steam_data").length;
    assert.equal(asks(), 1);
    s.socket._recv("connect");
    assert.equal(asks(), 2);
    s.win.__clock.tick(30000);
    assert.equal(asks(), 3);
    fire(s.win, s.win.document, "visibilitychange");
    assert.equal(asks(), 4);
});

test("shows the game, the achievement message, level, progress and art like the trophy overlay", () => {
    const s = setup();
    s.send(GAME);
    assert.equal(s.$("game_title").textContent, "Portal 2");
    assert.equal(s.$("ach_message").textContent, "10 out of 40 achievements");
    assert.equal(s.$("level_info").textContent, "Steam Level: 12");
    assert.equal(s.$("progress_label").textContent, "25%");
    assert.equal(s.win.document.querySelector("#progress .spore-progress-fill").style.width, "25%");
    assert.equal(s.$("game_icon").getAttribute("src"), GAME.current_game_capsule_url);
    s.$("game_icon").onerror();  // no capsule art for this game: fall back to the store header
    assert.equal(s.$("game_icon").getAttribute("src"), GAME.current_game_art_url);
    assert.equal(s.$("perfect_badge").getAttribute("data-shown"), null);
    assert.ok(s.$("root").classList.contains("stm-root"));
    assert.ok(JSON.parse(s.win.localStorage.getItem("mycelian_steam_achievements_state")).current_game_appid === 620);
});

test("pops +N for new unlocks in the same game only, and shows the badge at 100%", () => {
    const s = setup();
    s.send(GAME);
    s.win.__clock.tick(2500);
    s.send(Object.assign({}, GAME, { achievement_counts: { unlocked: 12, total: 40 } }));
    s.win.__clock.tick(200);
    assert.equal(s.$("ach_change").textContent, "+2 achievements");
    assert.ok(s.$("ach_change").classList.contains("stm-show"));
    s.win.__clock.tick(6500);
    assert.ok(!s.$("ach_change").classList.contains("stm-show"));
    s.win.__clock.tick(2500);
    s.send(Object.assign({}, GAME, { current_game_appid: 400, achievement_counts: { unlocked: 30, total: 30 } }));
    s.win.__clock.tick(200);
    assert.equal(s.$("ach_change").textContent, "+2 achievements", "a different game is not a new unlock");
    assert.equal(s.$("perfect_badge").getAttribute("data-shown"), "1");
});

test("messages for private, achievement-less and missing games; auto-hide after the delay", () => {
    const s = setup();
    s.send(Object.assign({}, GAME, { achievements_private: true }));
    assert.equal(s.$("ach_message").textContent, "Achievements are private");
    s.win.__clock.tick(2500);
    s.send(Object.assign({}, GAME, { achievement_counts: {} }));
    s.win.__clock.tick(200);
    assert.equal(s.$("ach_message").textContent, "No achievements in this game");
    s.win.__clock.tick(2500);
    s.send({ connection_status: "Offline", current_game_appid: null, steam_level: null });
    s.win.__clock.tick(200);
    assert.equal(s.$("game_title").textContent, "No Game Detected");
    assert.equal(s.$("ach_message").textContent, "");
    assert.equal(s.$("progress_label").textContent, "0%");
    assert.ok(s.$("game_icon").getAttribute("src").endsWith("notingame.png"));
    assert.ok(!s.$("root").classList.contains("stm-hidden"));
    s.win.__clock.tick(30000);
    assert.ok(s.$("root").classList.contains("stm-hidden"));
    s.win.__clock.tick(2500);
    s.send(GAME);
    s.win.__clock.tick(200);
    assert.ok(!s.$("root").classList.contains("stm-hidden"));
    s.win.__clock.tick(2500);
    s.send({ connection_status: "Invalid API Key" });
    s.win.__clock.tick(200);
    assert.equal(s.$("ach_message").textContent, "Steam not connected");
    assert.ok(s.$("game_icon").getAttribute("src").endsWith("notconnected.png"));
    s.socket._recv("steam_data_error", { error: "x" });
    assert.ok(s.$("root").classList.contains("stm-error"));
    s.win.__clock.tick(3000);
    assert.ok(!s.$("root").classList.contains("stm-error"));
    s.send(null);  // ignored
});

test("paints the cached state at once on reload, and works without the Spore runtime helpers", () => {
    const cached = JSON.stringify(Object.assign({}, GAME, { achievement_counts: { unlocked: 40, total: 40 } }));
    const s = setup({ runtime: false, localStorage: { mycelian_steam_achievements_state: cached } });
    assert.equal(s.$("ach_message").textContent, "40 out of 40 achievements");
    assert.equal(s.win.document.querySelector("#progress .spore-progress-fill").style.width, "100%");
    assert.equal(s.$("perfect_badge").style.display, "");
    assert.equal(s.$("ach_change").style.display, "none");
    assert.equal(typeof s.win.steamAchievementsDebug.request, "function");
});

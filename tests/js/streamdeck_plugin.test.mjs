// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Stream Deck plugin (streamdeck-plugin/mycelian/src, TypeScript). The sources are transpiled with
// the plugin's own local TypeScript (node_modules) to CommonJS and run in a vm with a stub
// "@elgato/streamdeck", a fake fetch and a fake clock. Skipped when node_modules is missing.
// The built bundle (bin/plugin.js) is checked for the registered action UUIDs.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { ROOT, FakeClock, makeFetch, xfail } from "./helpers/env.mjs";

const PLUGIN = path.join(ROOT, "streamdeck-plugin", "mycelian");
const SRC = path.join(PLUGIN, "src");
let ts = null;
try { ts = createRequire(path.join(PLUGIN, "package.json"))("typescript"); } catch { ts = null; }
const skip = ts ? false : "streamdeck-plugin/mycelian/node_modules is not installed (no local typescript)";

function transpile(file) {
    const src = fs.readFileSync(file, "utf8");
    return ts.transpileModule(src, {
        fileName: file,
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
    }).outputText;
}

// Load a source module (path relative to src/, no extension) with stubs; returns {exports, ctx}.
function load(rel, opts = {}) {
    const clock = opts.clock || new FakeClock();
    const fetch = opts.fetch || makeFetch();
    const logs = [];
    const quiet = {};
    for (const k of ["log", "info", "warn", "error", "debug"]) { quiet[k] = (...a) => logs.push([k, a.map(String).join(" ")]); }
    const registered = [];
    const streamdeck = {
        SingletonAction: class SingletonAction {},
        action: (opts2) => (cls) => { cls.manifestUUID = opts2.UUID; return cls; },
        LogLevel: { TRACE: "trace" },
        default: {
            logger: { level: null, setLevel(l) { this.level = l; } },
            actions: { registerAction(a) { registered.push(a); } },
            connected: 0,
            connect() { this.connected++; },
        },
    };
    streamdeck.__esModule = true;
    const ctx = vm.createContext({
        console: quiet, fetch,
        setTimeout: (fn, ms) => clock.setTimeout(fn, ms), clearTimeout: (id) => clock.clear(id),
        setInterval: (fn, ms) => clock.setInterval(fn, ms), clearInterval: (id) => clock.clear(id),
        JSON, Promise, Error,
    });
    const cache = new Map();
    function req(from, spec) {
        if (spec === "@elgato/streamdeck") { return streamdeck; }
        const file = path.resolve(path.dirname(from), spec) + ".ts";
        if (cache.has(file)) { return cache.get(file).exports; }
        const module = { exports: {} };
        cache.set(file, module);
        const fn = vm.runInContext("(function (exports, require, module) {" + transpile(file) + "\n})", ctx, { filename: file });
        fn(module.exports, (s) => req(file, s), module);
        return module.exports;
    }
    const exports = req(path.join(SRC, "index.ts"), "./" + rel);
    return { exports, clock, fetch, logs, registered, streamdeck };
}

function fakeAction(settings) {
    return {
        titles: [],
        settingsCalls: 0,
        setTitle(t) { this.titles.push(t); return Promise.resolve(); },
        getSettings() { this.settingsCalls++; return Promise.resolve(settings || {}); },
    };
}
const ev = (action, settings) => ({ action, payload: { settings } });
const flush = async () => { for (let i = 0; i < 10; i++) { await new Promise((r) => setImmediate(r)); } };

/* ---------------------------------------------------------------- server-config */

test("parseServerConfig splits host and port", { skip }, () => {
    const { exports: m } = load("lib/server-config");
    assert.equal(m.DEFAULT_SERVER_URL, "127.0.0.1:5000");
    assert.deepEqual({ ...m.parseServerConfig("192.168.1.5:8080") }, { host: "192.168.1.5", port: 8080 });
    assert.deepEqual({ ...m.parseServerConfig("localhost") }, { host: "localhost", port: 5000 });
    assert.deepEqual({ ...m.parseServerConfig(m.DEFAULT_SERVER_URL) }, { host: "127.0.0.1", port: 5000 });
});

test("parseServerConfig falls back to defaults on extra colons and warns", { skip }, () => {
    const { exports: m, logs } = load("lib/server-config");
    assert.deepEqual({ ...m.parseServerConfig("http://host:5000") }, { host: "127.0.0.1", port: 5000 });
    assert.equal(logs.length, 1);
    assert.equal(logs[0][0], "warn");
    assert.match(logs[0][1], /Invalid server URL format: http:\/\/host:5000/);
});

if (ts) xfail(test, "parseServerConfig keeps a non-numeric port as NaN",
    "BUG: server-config.ts:15 parseInt of a bad port ('host:abc' or 'host:') gives NaN instead of the 5000 fallback, so every request goes to http://host:NaN",
    () => {
        const { exports: m } = load("lib/server-config");
        assert.equal(m.parseServerConfig("localhost:abc").port, 5000);
    });

test("apiUrl joins host, port and path", { skip }, () => {
    const { exports: m } = load("lib/server-config");
    assert.equal(m.apiUrl({ host: "h", port: 1 }, "/api/x"), "http://h:1/api/x");
});

/* ---------------------------------------------------------------- plugin entry */

test("plugin.ts registers the three actions, sets trace logging and connects", { skip }, () => {
    const { registered, streamdeck } = load("plugin");
    assert.deepEqual(registered.map((a) => a.constructor.manifestUUID), [
        "com.mushroomsuprise.mycelian.togglealerts",
        "com.mushroomsuprise.mycelian.templateaction",
        "com.mushroomsuprise.mycelian.connector",
    ]);
    assert.equal(streamdeck.default.logger.level, "trace");
    assert.equal(streamdeck.default.connected, 1);
});

/* ---------------------------------------------------------------- connector action */

function connector(fetchRoutes) {
    const r = load("actions/connector-action", { fetch: makeFetch(fetchRoutes || []) });
    return { ...r, action: new r.exports.ConnectorAction() };
}

test("connector: title shows NOT SET without a connector and the (wrapped) name otherwise", { skip }, async () => {
    const { action } = connector();
    const a = fakeAction();
    await action.onWillAppear(ev(a, {}));
    await action.onWillAppear(ev(a, { connectorId: "c1" }));
    await action.onWillAppear(ev(a, { connectorId: "c1", connectorName: "Short" }));
    await action.onDidReceiveSettings(ev(a, { connectorId: "c1", connectorName: "A very long connector name" }));
    assert.deepEqual(a.titles, ["NOT SET", "Connector", "Short", "A very long ...\nconnector na"]);
});

test("connector: updateButtonTitle reads settings from the action when none are given", { skip }, async () => {
    const { action } = connector();
    const a = fakeAction({ connectorId: "x", connectorName: "From SD" });
    await action.updateButtonTitle(a);
    assert.equal(a.settingsCalls, 1);
    assert.deepEqual(a.titles, ["From SD"]);
    const broken = fakeAction();
    broken.getSettings = () => Promise.reject(new Error("gone"));
    await action.updateButtonTitle(broken);
    assert.deepEqual(broken.titles, ["ERROR"]);
});

test("connector: key press without an id says NOT SET and makes no request", { skip }, async () => {
    const { action, fetch } = connector();
    const a = fakeAction();
    await action.onKeyDown(ev(a, {}));
    assert.deepEqual(a.titles, ["NOT SET"]);
    assert.equal(fetch.calls.length, 0);
});

test("connector: key press posts the id, shows a tick, then restores the title", { skip }, async () => {
    const { action, fetch, clock } = connector([["/api/streamdeck/trigger_connector", { success: true }]]);
    const a = fakeAction();
    const settings = { connectorId: "abc", connectorName: "A connector name that is long", serverUrl: "10.0.0.2:6000" };
    await action.onKeyDown(ev(a, settings));
    assert.equal(fetch.calls.length, 1);
    assert.equal(fetch.calls[0].url, "http://10.0.0.2:6000/api/streamdeck/trigger_connector");
    assert.equal(fetch.calls[0].method, "POST");
    assert.deepEqual(JSON.parse(fetch.calls[0].body), { connectorId: "abc" });
    assert.equal(fetch.calls[0].init.headers["Content-Type"], "application/json");
    assert.deepEqual(a.titles, ["A connector ...\n✓"]);
    clock.tick(149);
    await flush();
    assert.equal(a.titles.length, 1);
    clock.tick(1);
    await flush();
    assert.deepEqual(a.titles, ["A connector ...\n✓", "A connector ...\nname that is"]);
});

test("connector: short default name gets a tick", { skip }, async () => {
    const { action } = connector([["trigger_connector", { success: true }]]);
    const a = fakeAction();
    await action.onKeyDown(ev(a, { connectorId: "abc" }));
    assert.deepEqual(a.titles, ["Connector\n✓"]);
});

for (const [label, reply] of [
    ["HTTP error", { status: 500, body: { success: false } }],
    ["success false", { success: false, message: "nope" }],
    ["success false without message", { success: false }],
    ["network failure", new Error("ECONNREFUSED")],
]) {
    test("connector: " + label + " shows ERROR", { skip }, async () => {
        const { action, logs } = connector([["trigger_connector", reply]]);
        const a = fakeAction();
        await action.onKeyDown(ev(a, { connectorId: "abc" }));
        assert.deepEqual(a.titles, ["ERROR"]);
        assert.ok(logs.some(([k, msg]) => k === "error" && /Failed to trigger connector/.test(msg)));
    });
}

/* ---------------------------------------------------------------- template action */

function templateAction(fetchRoutes) {
    const r = load("actions/template-action", { fetch: makeFetch(fetchRoutes || []) });
    return { ...r, action: new r.exports.TemplateAction() };
}

test("template action: titles truncate template and action names to 8 characters", { skip }, async () => {
    const { action } = templateAction();
    const a = fakeAction();
    await action.onWillAppear(ev(a, {}));
    await action.onWillAppear(ev(a, { selectedTemplate: "poll" }));
    await action.onWillAppear(ev(a, { selectedTemplate: "poll", selectedAction: "start" }));
    await action.onDidReceiveSettings(ev(a, { selectedTemplate: "subcounter", selectedAction: "increment_all" }));
    await action.onDidReceiveSettings(ev(a, { selectedTemplate: "12345678", selectedAction: "abcdefgh" }));
    assert.deepEqual(a.titles, ["TEMPLATE\nNOT SET", "TEMPLATE\nNOT SET", "poll\nstart", "subcount...\nincremen...", "12345678\nabcdefgh"]);
});

test("template action: updateButtonTitle falls back to getSettings and handles failures", { skip }, async () => {
    const { action } = templateAction();
    const a = fakeAction({ selectedTemplate: "t", selectedAction: "a" });
    await action.updateButtonTitle(a);
    assert.deepEqual(a.titles, ["t\na"]);
    const broken = fakeAction();
    broken.getSettings = () => Promise.reject(new Error("x"));
    await action.updateButtonTitle(broken);
    assert.deepEqual(broken.titles, ["TEMPLATE\nERROR"]);
});

test("template action: unconfigured key press says NOT SET without a request", { skip }, async () => {
    const { action, fetch } = templateAction();
    const a = fakeAction();
    await action.onKeyDown(ev(a, { selectedTemplate: "poll" }));
    assert.deepEqual(a.titles, ["TEMPLATE\nNOT SET"]);
    assert.equal(fetch.calls.length, 0);
});

test("template action: key press posts template, action and data then resets the title", { skip }, async () => {
    const { action, fetch, clock } = templateAction([["/api/streamdeck/template_action", { success: true }]]);
    const a = fakeAction();
    const settings = { selectedTemplate: "poll", selectedAction: "start", actionData: { seconds: 30 } };
    await action.onKeyDown(ev(a, settings));
    assert.equal(fetch.calls[0].url, "http://127.0.0.1:5000/api/streamdeck/template_action");
    assert.equal(fetch.calls[0].method, "POST");
    assert.deepEqual(JSON.parse(fetch.calls[0].body), { templateName: "poll", actionName: "start", actionData: { seconds: 30 } });
    assert.deepEqual(a.titles, ["poll\nstart\n✓"]);
    clock.tick(150);
    await flush();
    assert.deepEqual(a.titles, ["poll\nstart\n✓", "poll\nstart"]);
});

test("template action: missing actionData is sent as an empty object", { skip }, async () => {
    const { action, fetch } = templateAction([["template_action", { success: true }]]);
    await action.onKeyDown(ev(fakeAction(), { selectedTemplate: "t", selectedAction: "a", serverUrl: "h:1" }));
    assert.equal(fetch.calls[0].url, "http://h:1/api/streamdeck/template_action");
    assert.deepEqual(JSON.parse(fetch.calls[0].body).actionData, {});
});

for (const [label, reply, logged] of [
    ["HTTP error logs the body", { status: 404, text: "no such template" }, /no such template/],
    ["success false", { success: false, message: "bad action" }, /bad action/],
    ["success false without message", { success: false }, /Unknown error/],
    ["network failure", new Error("down"), /down/],
]) {
    test("template action: " + label + " shows TEMPLATE ERROR", { skip }, async () => {
        const { action, logs } = templateAction([["template_action", reply]]);
        const a = fakeAction();
        await action.onKeyDown(ev(a, { selectedTemplate: "t", selectedAction: "a" }));
        assert.deepEqual(a.titles, ["TEMPLATE\nERROR"]);
        assert.ok(logs.some(([k, msg]) => k === "error" && logged.test(msg)), JSON.stringify(logs));
    });
}

/* ---------------------------------------------------------------- toggle alerts */

function toggle(fetchRoutes) {
    const r = load("actions/toggle-alerts", { fetch: makeFetch(fetchRoutes || []) });
    return { ...r, action: new r.exports.ToggleAlerts() };
}

test("toggle: appearing fetches the status, shows it and starts polling", { skip }, async () => {
    const { action, fetch, clock } = toggle([["get_pause_status", { paused: true, success: true }]]);
    const a = fakeAction();
    await action.onWillAppear(ev(a, { serverUrl: "h:7" }));
    assert.deepEqual(a.titles, ["TGL ALERTS\nPAUSED"]);
    assert.equal(fetch.calls[0].url, "http://h:7/api/streamdeck/get_pause_status");
    assert.equal(action.lastKnownStatus, "PAUSED");
    assert.ok(action.pollingTimer);
    assert.equal(clock.pending(), 1);
    // polling with an unchanged status does not retitle
    clock.tick(12000);
    await flush();
    assert.equal(fetch.calls.length, 2);
    assert.deepEqual(a.titles, ["TGL ALERTS\nPAUSED"]);
});

test("toggle: polling retitles only when the status changes", { skip }, async () => {
    let paused = false;
    const { action, clock } = toggle([["get_pause_status", () => ({ paused, success: true })]]);
    const a = fakeAction();
    await action.onWillAppear(ev(a, {}));
    assert.deepEqual(a.titles, ["TGL ALERTS\nACTIVE"]);
    paused = true;
    clock.tick(12000);
    await flush();
    assert.deepEqual(a.titles, ["TGL ALERTS\nACTIVE", "TGL ALERTS\nPAUSED"]);
});

test("toggle: a failed first status shows ERROR and polling keeps ERROR until the server answers", { skip }, async () => {
    let up = false;
    const { action, clock, logs } = toggle([["get_pause_status", () => (up ? { paused: false } : { status: 503 })]]);
    const a = fakeAction();
    await action.onWillAppear(ev(a, {}));
    assert.deepEqual(a.titles, ["TGL ALERTS\nERROR"]);
    assert.equal(action.lastKnownStatus, "ERROR");
    clock.tick(12000);
    await flush();
    assert.deepEqual(a.titles, ["TGL ALERTS\nERROR", "TGL ALERTS\nERROR"]);
    // warning is not repeated while already in the error state
    assert.equal(logs.filter(([k]) => k === "warn").length, 0);
    up = true;
    clock.tick(12000);
    await flush();
    assert.deepEqual(a.titles.at(-1), "TGL ALERTS\nACTIVE");
});

test("toggle: polling error before any status sets ERROR and ignores setTitle failures", { skip }, async () => {
    const { action, logs } = toggle([["get_pause_status", new Error("down")]]);
    const a = fakeAction();
    a.setTitle = () => Promise.reject(new Error("deck gone"));
    await action.pollStatus({ host: "h", port: 1 }, a);
    assert.equal(action.lastKnownStatus, "ERROR");
    assert.ok(logs.some(([k]) => k === "warn"));
});

if (ts) xfail(test, "toggle: repeated polling failures after ACTIVE never show ERROR",
    "BUG: toggle-alerts.ts:79 the error branch only fires when no status is known yet; once ACTIVE/PAUSED, a stopped Mycelian keeps showing the stale status forever",
    async () => {
        let up = true;
        const { action, clock } = toggle([["get_pause_status", () => (up ? { paused: false } : new Error("down"))]]);
        const a = fakeAction();
        await action.onWillAppear(ev(a, {}));
        up = false;
        for (let i = 0; i < 3; i++) { clock.tick(12000); await flush(); }
        assert.equal(a.titles.at(-1), "TGL ALERTS\nERROR");
    });

test("toggle: key press posts a toggle, shows the new status and restarts polling", { skip }, async () => {
    const { action, fetch, clock } = toggle([["toggle_alerts", { paused: true, success: true }], ["get_pause_status", { paused: true }]]);
    const a = fakeAction();
    await action.onKeyDown(ev(a, { serverUrl: "h:9" }));
    const post = fetch.calls.find((c) => c.url.endsWith("/api/streamdeck/toggle_alerts"));
    assert.equal(post.url, "http://h:9/api/streamdeck/toggle_alerts");
    assert.equal(post.method, "POST");
    assert.deepEqual(a.titles, ["TGL ALERTS\nPAUSED"]);
    assert.equal(action.lastKnownStatus, "PAUSED");
    assert.equal(clock.pending(), 1);
    const first = action.pollingTimer;
    await action.onKeyDown(ev(a, {}));
    assert.notEqual(action.pollingTimer, first, "polling restarted");
    assert.equal(clock.pending(), 1, "old timer cleared");
});

test("toggle: failed key press shows ERROR", { skip }, async () => {
    const { action } = toggle([["toggle_alerts", { status: 500 }]]);
    const a = fakeAction();
    await action.onKeyDown(ev(a, {}));
    assert.deepEqual(a.titles, ["TGL ALERTS\nERROR"]);
});

test("toggle: disappearing stops polling; stopping twice is harmless", { skip }, async () => {
    const { action, clock, logs } = toggle([["get_pause_status", { paused: false }]]);
    await action.onWillAppear(ev(fakeAction(), {}));
    assert.equal(clock.pending(), 1);
    await action.onWillDisappear(ev(fakeAction(), {}));
    assert.equal(clock.pending(), 0);
    assert.equal(action.pollingTimer, null);
    action.stopPolling();
    assert.equal(logs.filter(([, m]) => m === "Stopped status polling").length, 1);
});

test("toggle: new settings restart polling against the new server", { skip }, async () => {
    const { action, fetch, clock } = toggle([["get_pause_status", { paused: false }]]);
    const a = fakeAction();
    await action.onDidReceiveSettings(ev(a, { serverUrl: "new:1" }));
    assert.equal(fetch.calls[0].url, "http://new:1/api/streamdeck/get_pause_status");
    assert.deepEqual(a.titles, ["TGL ALERTS\nACTIVE"]);
    clock.tick(12000);
    await flush();
    assert.equal(fetch.calls[1].url, "http://new:1/api/streamdeck/get_pause_status");
});

test("toggle: forcePaused skips the request", { skip }, async () => {
    const { action, fetch } = toggle();
    const a = fakeAction();
    await action.updateButtonTitle(ev(a, {}), true);
    await action.updateButtonTitle(ev(a, {}), false);
    assert.deepEqual(a.titles, ["TGL ALERTS\nPAUSED", "TGL ALERTS\nACTIVE"]);
    assert.equal(fetch.calls.length, 0);
});

/* ---------------------------------------------------------------- built bundle */

test("built bin/plugin.js bundles every action UUID from the sources", () => {
    const uuids = [];
    for (const f of fs.readdirSync(path.join(SRC, "actions"))) {
        const m = /UUID:\s*"([^"]+)"/.exec(fs.readFileSync(path.join(SRC, "actions", f), "utf8"));
        if (m) { uuids.push(m[1]); }
    }
    assert.equal(uuids.length, 3);
    for (const dir of [path.join(PLUGIN, "com.mushroomsuprise.mycelian.sdPlugin"), path.join(ROOT, "sd_plugin", "com.mushroomsuprise.mycelian.sdPlugin")]) {
        const bundle = fs.readFileSync(path.join(dir, "bin", "plugin.js"), "utf8");
        for (const u of uuids) { assert.ok(bundle.includes(u), dir + " lacks " + u); }
        assert.ok(bundle.includes("/api/streamdeck/toggle_alerts"));
        assert.ok(bundle.includes("/api/streamdeck/trigger_connector"));
        assert.ok(bundle.includes("/api/streamdeck/template_action"));
        assert.match(bundle, /^import /, "ES module bundle (bin/package.json says type: module)");
        assert.equal(JSON.parse(fs.readFileSync(path.join(dir, "bin", "package.json"), "utf8")).type, "module");
    }
});

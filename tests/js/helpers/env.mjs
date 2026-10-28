// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Browser-ish window for running Mycelian scripts in node:vm, plus a fake clock and a
// script loader that can expose an IIFE's private functions to tests.
//
//   import { makeWindow, loadScript, plain } from "./helpers/env.mjs";
//   const win = makeWindow({ url: "http://127.0.0.1:5000/overlay/x" });
//   const { internals: I } = loadScript(win, "assets/default_assets/alert_queue.js", { expose: true });
//   I.someFunction(...)          // private function declared inside the IIFE
//   I.__eval("state.count = 3")  // run code inside the IIFE scope (read/write closure vars)
//   win.__clock.tick(1000)       // advance fake timers (setTimeout/setInterval/rAF, Date.now, performance.now)
//
// Objects created inside the vm realm have other prototypes than the test's own, so compare
// them with assert.deepEqual or with plain(x) (JSON round-trip) before assert.deepStrictEqual.

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import * as dom from "./dom.mjs";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

export function plain(x) { return x === undefined ? undefined : JSON.parse(JSON.stringify(x)); }

export function readSource(rel) { return fs.readFileSync(path.join(ROOT, rel), "utf8"); }

/* ------------------------------------------------------------------- clock */

export class FakeClock {
    constructor(start = 1_700_000_000_000) {
        this.wallStart = start;
        this.now = 0;
        this._id = 0;
        this.timers = new Map();
    }
    _add(fn, ms, repeat, args) {
        const id = ++this._id;
        const delay = Math.max(0, Number(ms) || 0);
        this.timers.set(id, { id, fn, at: this.now + delay, every: repeat ? Math.max(1, delay) : 0, args: args || [] });
        return id;
    }
    setTimeout(fn, ms, ...args) { return this._add(fn, ms, false, args); }
    setInterval(fn, ms, ...args) { return this._add(fn, ms, true, args); }
    clear(id) { this.timers.delete(id); }
    requestAnimationFrame(fn) { return this._add(() => fn(this.now), 16, false); }
    wallNow() { return this.wallStart + this.now; }
    pending() { return this.timers.size; }
    // Advance time, firing due timers in order. Timers a callback schedules fire too if due.
    tick(ms = 0) {
        const end = this.now + Math.max(0, ms);
        for (let guard = 0; guard < 100000; guard++) {
            let next = null;
            for (const t of this.timers.values()) { if (t.at <= end && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) { next = t; } }
            if (!next) { break; }
            this.now = Math.max(this.now, next.at);
            if (next.every) { next.at += next.every; } else { this.timers.delete(next.id); }
            if (typeof next.fn === "function") { next.fn(...next.args); }
        }
        this.now = end;
    }
    // Fire every pending one-shot timer (and each interval once) without looping forever.
    flush(maxRounds = 50) {
        for (let r = 0; r < maxRounds; r++) {
            const ones = Array.from(this.timers.values()).filter((t) => !t.every);
            if (!ones.length) { return; }
            const far = Math.max(...ones.map((t) => t.at));
            this.tick(Math.max(0, far - this.now));
        }
    }
}

/* ----------------------------------------------------------------- storage */

export class Storage {
    constructor(init) { this._m = new Map(Object.entries(init || {})); }
    get length() { return this._m.size; }
    key(i) { return Array.from(this._m.keys())[i] ?? null; }
    getItem(k) { k = String(k); return this._m.has(k) ? this._m.get(k) : null; }
    setItem(k, v) { this._m.set(String(k), String(v)); }
    removeItem(k) { this._m.delete(String(k)); }
    clear() { this._m.clear(); }
}

/* ----------------------------------------------------------------- network */

// A fetch stub: routes is a list of [matcher, responder]. matcher is a string (substring of the
// URL), a RegExp or a function(url, init). responder is a value (JSON body), a function returning
// a value / {status, body, text, headers} / a Promise, or an Error to reject with.
export function makeFetch(routes = []) {
    const calls = [];
    const fetch = (url, init = {}) => {
        const u = String(url && url.url ? url.url : url);
        calls.push({ url: u, init, method: (init.method || "GET").toUpperCase(), body: init.body });
        for (const [m, r] of fetch.routes) {
            const hit = typeof m === "string" ? u.includes(m) : m instanceof RegExp ? m.test(u) : m(u, init);
            if (!hit) { continue; }
            let res = typeof r === "function" ? r(u, init) : r;
            return Promise.resolve(res).then((val) => {
                if (val instanceof Error) { throw val; }
                return makeResponse(val);
            });
        }
        return Promise.resolve(makeResponse({ status: 404, body: { error: "no route" } }));
    };
    fetch.routes = routes;
    fetch.calls = calls;
    fetch.on = (m, r) => { fetch.routes.unshift([m, r]); return fetch; };
    return fetch;
}

export function makeResponse(val) {
    let status = 200; let body = val; let text = null; let headers = {};
    if (val && typeof val === "object" && ("status" in val || "body" in val || "text" in val) && !Array.isArray(val)) {
        status = val.status ?? 200; body = val.body; text = val.text ?? null; headers = val.headers || {};
    }
    const bodyText = text !== null ? text : (typeof body === "string" ? body : JSON.stringify(body ?? null));
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: String(status),
        headers: { get: (k) => headers[String(k).toLowerCase()] ?? headers[k] ?? null },
        json: () => Promise.resolve().then(() => JSON.parse(bodyText)),
        text: () => Promise.resolve(bodyText),
        blob: () => Promise.resolve({ size: bodyText.length, type: headers["content-type"] || "" }),
        clone() { return makeResponse(val); },
    };
}

// Fake Socket.IO client: io(url, opts) returns a socket recording emits; socket._recv(ev, data)
// delivers a server event to handlers.
export function makeIo() {
    const sockets = [];
    const io = (url, opts) => {
        const handlers = new Map();
        const s = {
            url, opts, connected: false, id: "sock" + (sockets.length + 1), emitted: [],
            io: { on() {}, opts: opts || {} },
            on(ev, fn) { const l = handlers.get(ev) || []; l.push(fn); handlers.set(ev, l); return s; },
            once(ev, fn) { const w = (...a) => { s.off(ev, w); fn(...a); }; return s.on(ev, w); },
            off(ev, fn) { if (!fn) { handlers.delete(ev); } else { handlers.set(ev, (handlers.get(ev) || []).filter((h) => h !== fn)); } return s; },
            emit(ev, ...args) { s.emitted.push([ev, ...args]); return s; },
            connect() { s.connected = true; s._recv("connect"); return s; },
            disconnect() { s.connected = false; s._recv("disconnect", "io client disconnect"); return s; },
            close() { return s.disconnect(); },
            _handlers: handlers,
            _recv(ev, ...args) { for (const h of (handlers.get(ev) || []).slice()) { h(...args); } },
        };
        sockets.push(s);
        return s;
    };
    io.sockets = sockets;
    return io;
}

/* ------------------------------------------------------------------ window */

class Observer {
    constructor(cb) { this.cb = cb; this.targets = []; Observer.all.push(this); }
    observe(t, opts) { this.targets.push(t); this.opts = opts; }
    unobserve(t) { this.targets = this.targets.filter((x) => x !== t); }
    disconnect() { this.targets = []; this.disconnected = true; }
    takeRecords() { return []; }
    trigger(entries) { this.cb(entries || this.targets.map((target) => ({ target, contentRect: target.getBoundingClientRect(), isIntersecting: true })), this); }
}
Observer.all = [];

class FakeAudio {
    constructor(src) {
        this.src = src || ""; this.volume = 1; this.paused = true; this.currentTime = 0; this.duration = NaN;
        this.muted = false; this.loop = false; this.preload = ""; this._listeners = {};
        FakeAudio.instances.push(this);
    }
    play() { this.paused = false; this.played = (this.played || 0) + 1; return FakeAudio.playResult ? FakeAudio.playResult(this) : Promise.resolve(); }
    pause() { this.paused = true; }
    load() {}
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
    removeEventListener(t, fn) { this._listeners[t] = (this._listeners[t] || []).filter((f) => f !== fn); }
    _emit(t, ev) { for (const fn of (this._listeners[t] || []).slice()) { fn(ev || { type: t, target: this }); } if (typeof this["on" + t] === "function") { this["on" + t](ev || { type: t, target: this }); } }
    canPlayType() { return "maybe"; }
}
FakeAudio.instances = [];
FakeAudio.playResult = null;

class FakeImage {
    constructor(w, h) { this.width = w || 0; this.height = h || 0; this.complete = false; this.naturalWidth = 0; this.naturalHeight = 0; FakeImage.instances.push(this); }
    set src(v) { this._src = v; }
    get src() { return this._src || ""; }
    addEventListener(t, fn) { this["on" + t] = fn; }
    removeEventListener() {}
    decode() { return Promise.resolve(); }
}
FakeImage.instances = [];

export function makeWindow(opts = {}) {
    const clock = opts.clock || new FakeClock();
    const document = new dom.Document();
    const url = new URL(opts.url || "http://127.0.0.1:5000/");
    const win = new dom.EventTarget();
    document.defaultView = win;
    const location = {
        get href() { return url.href; }, set href(v) { location._assigned.push(String(v)); },
        get origin() { return url.origin; }, get protocol() { return url.protocol; },
        get host() { return url.host; }, get hostname() { return url.hostname; }, get port() { return url.port; },
        get pathname() { return url.pathname; }, get search() { return url.search; }, get hash() { return url.hash; },
        set hash(v) { url.hash = v; },
        assign(v) { location._assigned.push(String(v)); }, replace(v) { location._assigned.push(String(v)); },
        reload() { location._reloads++; }, toString() { return url.href; },
        _assigned: [], _reloads: 0, _url: url,
    };
    const historyStack = [];
    const selection = { rangeCount: 0, removeAllRanges() { this.rangeCount = 0; }, addRange() { this.rangeCount = 1; }, getRangeAt() { return document.createRange(); }, toString() { return ""; }, collapse() {}, selectAllChildren() {} };
    const consoleLog = [];
    const quietConsole = {};
    for (const k of ["log", "info", "warn", "error", "debug", "trace"]) { quietConsole[k] = (...a) => { consoleLog.push([k, a]); }; }
    quietConsole.group = quietConsole.groupCollapsed = quietConsole.groupEnd = quietConsole.time = quietConsole.timeEnd = () => {};
    quietConsole.table = () => {};
    const props = {
        window: win, self: win, globalThis: win, document, location,
        top: win, parent: win, frameElement: null, opener: null, frames: [], name: "",
        navigator: {
            userAgent: "Mozilla/5.0 (node-test) AppleWebKit/537.36 Chrome/120 Safari/537.36", platform: "MacIntel", language: "en-US", languages: ["en-US"],
            onLine: true, clipboard: { written: [], writeText(t) { this.written.push(t); return Promise.resolve(); }, readText() { return Promise.resolve(this.written[this.written.length - 1] || ""); } },
            sendBeacon: (u, d) => { win.__beacons.push([u, d]); return true; }, maxTouchPoints: 0, hardwareConcurrency: 4,
        },
        history: { length: 1, state: null, _stack: historyStack, pushState(s, t, u) { historyStack.push(["push", s, u]); this.state = s; }, replaceState(s, t, u) { historyStack.push(["replace", s, u]); this.state = s; }, back() {}, forward() {}, go() {} },
        localStorage: new Storage(opts.localStorage), sessionStorage: new Storage(opts.sessionStorage),
        console: opts.console || quietConsole, __console: consoleLog,
        setTimeout: (fn, ms, ...a) => clock.setTimeout(fn, ms, ...a), clearTimeout: (id) => clock.clear(id),
        setInterval: (fn, ms, ...a) => clock.setInterval(fn, ms, ...a), clearInterval: (id) => clock.clear(id),
        requestAnimationFrame: (fn) => clock.requestAnimationFrame(fn), cancelAnimationFrame: (id) => clock.clear(id),
        requestIdleCallback: (fn) => clock.setTimeout(() => fn({ didTimeout: false, timeRemaining: () => 50 }), 1), cancelIdleCallback: (id) => clock.clear(id),
        queueMicrotask, structuredClone, URL, URLSearchParams, TextEncoder, TextDecoder, AbortController, AbortSignal,
        Blob: globalThis.Blob, FormData: globalThis.FormData, Headers: globalThis.Headers, atob, btoa,
        performance: { now: () => clock.now, mark() {}, measure() {}, getEntriesByName() { return []; }, timeOrigin: clock.wallStart },
        fetch: opts.fetch || makeFetch(), io: opts.io || makeIo(),
        __clock: clock, __beacons: [],
        innerWidth: opts.innerWidth ?? 1280, innerHeight: opts.innerHeight ?? 720, outerWidth: 1280, outerHeight: 720,
        devicePixelRatio: 1, scrollX: 0, scrollY: 0, pageXOffset: 0, pageYOffset: 0,
        screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1080 },
        getComputedStyle: (el) => {
            const st = el && el.style ? el.style : { getPropertyValue: () => "" };
            const comp = Object.create(null);
            return new Proxy(comp, {
                get(t, k) {
                    if (k === "getPropertyValue") { return (n) => { const v = st.getPropertyValue(n); if (v) { return v; } if (n.startsWith("--")) { let p = el && el.parentNode; while (p && p.style) { const pv = p.style.getPropertyValue(n); if (pv) { return pv; } p = p.parentNode; } } return ""; }; }
                    if (typeof k !== "string") { return undefined; }
                    const v = st[k];
                    if (v) { return v; }
                    if (k === "display") { return el && el.hidden ? "none" : "block"; }
                    if (k === "visibility") { return "visible"; }
                    if (k === "opacity") { return "1"; }
                    return "";
                },
            });
        },
        matchMedia: (q) => ({ matches: !!(opts.media && opts.media[q]), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
        getSelection: () => selection,
        scrollTo() {}, scrollBy() {}, focus() {}, blur() {}, print() {},
        open: (...a) => { win.__opened.push(a); return null; }, __opened: [],
        close() { win.__closed = true; },
        alert: (m) => { win.__alerts.push(m); }, __alerts: [],
        confirm: (m) => { win.__confirms.push(m); return opts.confirm !== undefined ? opts.confirm : true; }, __confirms: [],
        prompt: (m, d) => { win.__prompts.push(m); return opts.prompt !== undefined ? opts.prompt : d ?? null; }, __prompts: [],
        postMessage: (data, origin) => { win.__posted.push([data, origin]); }, __posted: [],
        MutationObserver: class extends Observer {}, ResizeObserver: class extends Observer {}, IntersectionObserver: class extends Observer {},
        __observers: Observer.all,
        Audio: FakeAudio, Image: FakeImage, __audio: FakeAudio, __image: FakeImage,
        CSS: { escape: (s) => String(s).replace(/([^\w-])/g, "\\$1"), supports: () => true },
        Node: dom.Node, Element: dom.Element, HTMLElement: dom.Element, SVGElement: dom.Element, HTMLInputElement: dom.Element,
        HTMLTextAreaElement: dom.Element, HTMLSelectElement: dom.Element, HTMLCanvasElement: dom.Element, HTMLImageElement: dom.Element,
        HTMLMediaElement: dom.Element, HTMLAudioElement: dom.Element, HTMLVideoElement: dom.Element, HTMLIFrameElement: dom.Element, HTMLButtonElement: dom.Element,
        Text: dom.Text, Comment: dom.Comment, DocumentFragment: dom.DocumentFragment, Document: dom.Document, EventTarget: dom.EventTarget,
        Event: dom.Event, CustomEvent: dom.CustomEvent, UIEvent: dom.UIEvent, MouseEvent: dom.MouseEvent, PointerEvent: dom.PointerEvent,
        KeyboardEvent: dom.KeyboardEvent, FocusEvent: dom.FocusEvent, InputEvent: dom.InputEvent, WheelEvent: dom.WheelEvent, DragEvent: dom.DragEvent,
        ErrorEvent: dom.ErrorEvent, PromiseRejectionEvent: dom.PromiseRejectionEvent,
        DOMParser: class { parseFromString(html) { const d = new dom.Document(); d.body.innerHTML = String(html); return d; } },
        XMLSerializer: class { serializeToString(n) { return n.outerHTML || ""; } },
        FileReader: class { readAsText(b) { this.result = String(b && b.text ? b.text : b); setTimeout(() => this.onload && this.onload({ target: this }), 0); } readAsDataURL(b) { this.result = "data:;base64,"; setTimeout(() => this.onload && this.onload({ target: this }), 0); } },
        crypto: { randomUUID: () => "00000000-0000-4000-8000-" + String(++win.__uuid).padStart(12, "0"), getRandomValues: (a) => { for (let i = 0; i < a.length; i++) { a[i] = (i * 2654435761) % 256; } return a; } },
        __uuid: 0,
    };
    for (const [k, v] of Object.entries(props)) { win[k] = v; }
    if (opts.extra) { Object.assign(win, opts.extra); }
    vm.createContext(win);
    // Date.now / new Date() follow the fake clock inside this realm only.
    vm.runInContext(`(function(){
        var RealDate = Date; var clock = __clock;
        function FakeDate(a, b, c, d, e, f, g) {
            if (!(this instanceof FakeDate)) { return new RealDate(clock.wallNow()).toString(); }
            var n = arguments.length;
            var dt = n === 0 ? new RealDate(clock.wallNow()) : n === 1 ? new RealDate(a) : new RealDate(a, b, c === undefined ? 1 : c, d || 0, e || 0, f || 0, g || 0);
            Object.setPrototypeOf(dt, FakeDate.prototype);
            return dt;
        }
        FakeDate.prototype = Object.create(RealDate.prototype);
        FakeDate.prototype.constructor = FakeDate;
        FakeDate.now = function () { return clock.wallNow(); };
        FakeDate.parse = RealDate.parse; FakeDate.UTC = RealDate.UTC;
        globalThis.Date = FakeDate;
    })();`, win);
    return win;
}

/* ------------------------------------------------------------------ loader */

function collectNames(src) {
    const names = new Set();
    const reFn = /(?:^|[^\w$.])function\s*\*?\s+([A-Za-z_$][\w$]*)\s*\(/g;
    const reVar = /(?:^|[\s;{(])(?:var|let|const)\s+([A-Za-z_$][\w$]*)/g;
    let m;
    while ((m = reFn.exec(src))) { names.add(m[1]); }
    while ((m = reVar.exec(src))) { names.add(m[1]); }
    // var a = 1, b = 2;  (comma lists at statement level)
    const reList = /(?:^|[\s;{])var\s+([^;]+);/g;
    while ((m = reList.exec(src))) {
        for (const part of m[1].split(",")) {
            const nm = /^\s*([A-Za-z_$][\w$]*)\s*(=|$)/.exec(part);
            if (nm) { names.add(nm[1]); }
        }
    }
    return Array.from(names).filter((n) => n !== "__internals" && n !== "eval" && n !== "arguments");
}

// The exporter does not embed the key, so every load of a file runs identical source (V8
// coverage merges per file URL and gets confused by different sources under one name).
function exporterCode(names) {
    const getters = names.map((n) => `get ${n}(){try{return ${n};}catch(e){return undefined;}}`).join(",");
    return `\n;try{globalThis[globalThis.__exposeKey||"__internals"]={${getters}${getters ? "," : ""}__eval:function(__c){return eval(__c);}};}catch(__e){}\n`;
}

// Index at which to inject the exporter: the closing brace of the outermost IIFE body.
function defaultAnchor(src) {
    const trimmedEnd = src.replace(/\s+$/, "");
    const patterns = [/\}\s*\)\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*;?$/, /\}\s*\(\s*\)\s*\)\s*;?$/, /\}\s*\)\s*;?$/];
    for (const p of patterns) {
        const m = p.exec(trimmedEnd);
        if (m) { return m.index; }
    }
    return -1;
}

const scriptCache = new Map();

// Run a repo script inside `win`. Options:
//   expose: true (default when possible) | false | string[]
//           inject an exporter giving tests the IIFE's private names as win[key]. With the
//           default, files whose end can't be found (UMD wrappers) load unchanged; with an
//           explicit true that is an error.
//   anchor: string | RegExp   insert the exporter before this text (default: end of the outer IIFE)
//   key: global name for the exporter (default "__internals")
//   source: override source text (e.g. to test a variant); transform: fn(src) => src
// Returns { win, internals, result }.
export function loadScript(win, rel, opts = {}) {
    let src = opts.source !== undefined ? opts.source : readSource(rel);
    if (opts.transform) { src = opts.transform(src); }
    const key = opts.key || "__internals";
    const wantExpose = opts.expose === undefined ? "auto" : opts.expose;
    let exposed = false;
    if (wantExpose) {
        const names = Array.isArray(wantExpose) ? wantExpose : collectNames(src);
        let at;
        if (opts.anchor !== undefined) {
            at = typeof opts.anchor === "string" ? src.lastIndexOf(opts.anchor) : (() => { let m; let last = -1; const re = new RegExp(opts.anchor.source, opts.anchor.flags.includes("g") ? opts.anchor.flags : opts.anchor.flags + "g"); while ((m = re.exec(src))) { last = m.index; } return last; })();
        } else {
            at = defaultAnchor(src);
        }
        if (at < 0 && wantExpose !== "auto") { throw new Error("loadScript: could not find where to inject the exporter in " + rel); }
        if (at >= 0) {
            src = src.slice(0, at) + exporterCode(names) + src.slice(at);
            exposed = true;
        }
    }
    const filename = path.join(ROOT, rel);
    const cacheKey = filename + "\u0000" + src;
    let script = scriptCache.get(cacheKey);
    if (!script) { script = new vm.Script(src, { filename }); scriptCache.set(cacheKey, script); }
    win.__exposeKey = key;
    let result;
    try { result = script.runInContext(win); } finally { win.__exposeKey = undefined; }
    return { win, internals: exposed ? win[key] : undefined, result };
}

// Run a snippet in the window realm (handy for building realm-native arrays/objects).
export function run(win, code) { return vm.runInContext(code, win); }

// Dispatch helpers.
export function fire(win, target, type, init = {}) {
    const Ctor = /^key/.test(type) ? win.KeyboardEvent : /^(pointer)/.test(type) ? win.PointerEvent
        : /^(click|dblclick|mouse|contextmenu)/.test(type) ? win.MouseEvent : /^drag|^drop/.test(type) ? win.DragEvent
            : /^wheel/.test(type) ? win.WheelEvent : win.Event;
    const ev = new Ctor(type, Object.assign({ bubbles: true, cancelable: true }, init));
    target.dispatchEvent(ev);
    return ev;
}

// Let pending promise callbacks (fetch chains etc.) run.
export async function settle(rounds = 10) {
    for (let i = 0; i < rounds; i++) { await new Promise((r) => setImmediate(r)); }
}

// Strict expected failure for a confirmed app bug (the JS twin of pytest's xfail(strict=True)):
// the body must throw (an assertion that shows the bug); if it passes, the test fails so the
// marker gets removed once the bug is fixed.
//   xfail(test, "rounds 0.5 down", "BUG: formatX rounds half down (file.js:123)", () => { ... });
export function xfail(testFn, name, reason, fn) {
    testFn("[xfail] " + name, async () => {
        let failed = false;
        try { await fn(); } catch (err) {
            // Only an assertion failure counts as the expected bug; a crash in the test is a real failure.
            if (!(err && (err.code === "ERR_ASSERTION" || err.name === "AssertionError"))) { throw err; }
            failed = true;
        }
        if (!failed) { throw new Error("XPASS (strict): " + reason); }
    });
}

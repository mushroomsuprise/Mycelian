// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Boots the Spore Studio editor (assets/default_assets/spore_studio/editor.js) on the real
// editor.html markup with a fake backend built from tests/js/fixtures/studio_api.json (a snapshot
// of the /api/spore-studio/* registries and a few real template models).
//
//   const s = await bootEditor({ template: "giveaway" });
//   s.E.state.model ...            editor internals (closure functions and vars)
//   s.fetch.calls                  every request the editor made
//   s.add("text")                  add an element of a type and select it

import fs from "node:fs";
import path from "node:path";
import { makeFetch, settle, plain } from "./env.mjs";
import { makeStudioWindow, fireDomReady } from "./studio.mjs";
import { ROOT } from "./env.mjs";

export const API = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "js", "fixtures", "studio_api.json"), "utf8"));

export const ELEMENT_TYPES = ["text", "counter", "data_display", "image", "video", "audio", "container", "progress_bar", "shape",
    "divider", "gradient", "marquee", "clock", "timer", "list", "wheel", "now_playing", "qr", "embed", "stat_card", "confetti", "poll"];

export function blankModel(name = "zz_test", extra = {}) {
    return Object.assign({
        template_name: name, alert_system: "queue", design: { width: 800, height: 400 }, duration_seconds: 5,
        elements: [], advanced_js: "", streamdeck_options: { description: "", actions: {} }, dynamic_controls: { elements: [] },
    }, extra);
}

// opts.template: name to load (from fixture models, or opts.models); opts.models: extra {name: model};
// opts.routes: extra [matcher, responder] fetch routes checked first; opts.init === false: don't boot.
export async function bootEditor(opts = {}) {
    const models = Object.assign({}, API.models, opts.models || {});
    const saved = [];
    const routes = (opts.routes || []).concat([
        ["/api/spore-studio/events", API.events],
        ["/api/spore-studio/data-sources", API.data_sources],
        ["/api/spore-studio/control-actions", API.control_actions],
        ["/api/spore-studio/fonts", API.fonts],
        ["/api/spore-studio/data-catalog", API.data_catalog],
        ["/api/spore-studio/templates", () => opts.templates || {
            spore_templates: Object.keys(models).filter((n) => !models[n].legacy),
            legacy_templates: Object.keys(models).filter((n) => models[n].legacy),
        }],
        [/\/api\/spore-studio\/model\//, (u) => {
            const name = decodeURIComponent(u.split("/api/spore-studio/model/")[1].split("?")[0]);
            return models[name] ? JSON.parse(JSON.stringify(models[name])) : { status: 404, body: { error: "No template " + name } };
        }],
        [/\/api\/spore-studio\/assets\/[^/]+$/, { template: opts.template || "", files: [], folders: [] }],
        ["/api/spore-studio/save", (u, init) => { const body = JSON.parse(init.body); saved.push(body); return opts.saveReply || { ok: true, template_name: body.template_name || (body.model && body.model.template_name) }; }],
        ["/api/spore-studio/preview/", { ok: true }],
        ["/api/spore-studio/notify", { ok: true }],
        ["/api/spore-studio/ui-prefs", { "mycelian-spore-tour-seen": true }],
        ["/api/template-preview-settings/resolution", { width: null, height: null }],
        ["/api/spore-studio/poll-state/", { status: "idle", options: [] }],
    ]);
    const fetch = makeFetch(routes);
    const localStorage = Object.assign({ "mycelian-spore-last-template": opts.template || "" }, opts.localStorage || {});
    const { win, internals } = makeStudioWindow({
        scripts: ["mycelian_color_picker.js".replace(/^/, "assets/default_assets/"), "container_layout.js", "advanced_js_editor.js", "editor.js"],
        fetch, localStorage, url: opts.url || "http://127.0.0.1:5000/spore-studio" + (opts.template ? "?template=" + encodeURIComponent(opts.template) : ""),
        confirm: opts.confirm, prompt: opts.prompt, extra: opts.extra, innerWidth: opts.innerWidth, innerHeight: opts.innerHeight,
    });
    const E = internals.editor;
    const doc = win.document;
    const stage = doc.getElementById("ss-stage");
    if (stage) { stage._rect = { left: 0, top: 0, width: 800, height: 400 }; }
    const s = {
        win, E, doc, fetch, saved, clock: win.__clock, models,
        $: (sel) => doc.querySelector(sel),
        $$: (sel) => doc.querySelectorAll(sel),
        settle,
        plain,
        // Add an element through the real flow (category prompt answered with OK) and return it.
        async add(type, x = 10, y = 10, props, parent, category) {
            E.addElement(type, x, y, props, parent);
            const ok = doc.querySelector("[data-cat-action='ok']");
            if (!ok) { return null; }
            if (category !== undefined) {
                const input = doc.querySelector("[data-cat='new']");
                input.value = category;
                input.dispatchEvent(new win.Event("input"));
            }
            ok.click();
            await settle();
            return E.selectedElement();
        },
        select(id) { E.state.selectedId = id; E.renderAll(); return E.selectedElement(); },
        toast() { const t = doc.getElementById("ss-toast"); return t ? t.textContent : ""; },
    };
    if (opts.init !== false) {
        fireDomReady(win);
        await settle(30);
    }
    return s;
}

/* ------------------------------------------------------------------ control helpers */

// The form control next to a label inside root (".ss-form-row" rows built by formRow()).
export function control(root, label, index = 0) {
    const rows = root.querySelectorAll(".ss-form-row").filter((r) => {
        const l = r.querySelector("label");
        return l && l.textContent.trim() === label;
    });
    const row = rows[index];
    return row ? row.querySelector("select, textarea, input:not([type=hidden])") : null;
}

export function buttonByText(root, text) {
    return root.querySelectorAll("button").find((b) => b.textContent.trim() === text) || null;
}

export function setValue(win, el, value, events = ["input", "change"]) {
    if (el.type === "checkbox") { el.checked = !!value; } else { el.value = String(value); }
    for (const ev of events) { el.dispatchEvent(new win.Event(ev, { bubbles: true })); }
}

// Touch every control under `rootSel` once: pick each option of each select, toggle checkboxes,
// type into text/number fields and click buttons whose text does not match `skip`. The panel is
// re-queried after every action because most controls re-render it. Returns the number of
// actions taken; any exception from a handler propagates (the caller's test fails).
export async function exerciseControls(s, rootSel, opts = {}) {
    const skip = opts.skip || /^$/;
    const seen = new Set();
    let actions = 0;
    for (let round = 0; round < (opts.rounds || 400); round++) {
        const root = s.doc.querySelector(rootSel);
        if (!root) { break; }
        const all = root.querySelectorAll("select, input, textarea, button");
        let did = false;
        const counts = new Map();
        for (let i = 0; i < all.length; i++) {
            const el = all[i];
            const row = el.closest(".ss-form-row");
            const lab = row ? row.querySelector("label") : null;
            const sig = [el.tagName, el.type, lab ? lab.textContent : "", el.textContent.trim().slice(0, 30), el.getAttribute("title") || el.getAttribute("data-ss-tip") || "", el.className].join("|");
            const nth = (counts.get(sig) || 0) + 1;
            counts.set(sig, nth);
            const key = sig + "#" + nth;
            if (seen.has(key)) { continue; }
            seen.add(key);
            if (el.disabled) { continue; }
            if (el.tagName === "BUTTON") {
                if (skip.test(el.textContent.trim()) || skip.test(el.getAttribute("title") || "")) { continue; }
                el.click();
            } else if (el.tagName === "SELECT") {
                const values = el.options.map((o) => o.value);
                for (const v of values.slice(0, opts.maxOptions || 12)) {
                    if (!el.isConnected) { break; }
                    setValue(s.win, el, v, ["change", "input"]);
                }
            } else if (el.type === "checkbox" || el.type === "radio") {
                setValue(s.win, el, !el.checked, ["change", "input"]);
                if (el.isConnected) { setValue(s.win, el, !el.checked, ["change", "input"]); }
            } else if (el.type === "file") {
                continue;
            } else {
                const v = el.type === "number" || el.type === "range" ? (opts.number || "7") : (opts.text || "a{value}b");
                setValue(s.win, el, v, ["input", "change", "blur"]);
            }
            actions++;
            did = true;
            await settle(2);
            break; // re-query: the panel may have been rebuilt
        }
        if (!did) { break; }
    }
    // close anything a control opened
    for (const ov of s.doc.querySelectorAll(".ss-create-overlay, .mcp-pop")) { ov.remove(); }
    return actions;
}

// A property row built by formRowPropWithExpose(): { wrap, control, expose } or null.
export function prop(root, label, index = 0) {
    const wraps = root.querySelectorAll(".ss-form-prop").filter((w) => {
        const l = w.querySelector(".ss-form-prop__label");
        return l && l.textContent.trim() === label;
    });
    const w = wraps[index];
    if (!w) { return null; }
    const ctrl = w.children[1];
    const c = ctrl && (ctrl.matches("select, textarea, input") ? ctrl : ctrl.querySelector("select, textarea, input"));
    return { wrap: w, control: c, expose: w.querySelector(".ss-form-prop__expose input") };
}

// control() or prop().control, whichever finds the label.
export function field(root, label, index = 0) {
    const c = control(root, label, index);
    if (c) { return c; }
    const p = prop(root, label, index);
    return p ? p.control : null;
}

export function sectionByTitle(root, title) {
    return root.querySelectorAll(".ss-form-section").find((sec) => {
        const t = sec.querySelector(".ss-form-section__title");
        return t && t.textContent.trim() === title;
    }) || null;
}

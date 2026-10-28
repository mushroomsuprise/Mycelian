// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Spore Studio page fixture: a window whose body is the real editor.html markup, with
// spore_ui.js (and optionally other studio scripts) loaded. Scripts load with
// document.readyState = "loading" so their init() does not run until the test calls it
// (via internals.init() or fireDomReady(win)).

import { makeWindow, loadScript, readSource, makeFetch } from "./env.mjs";

const STUDIO = "assets/default_assets/spore_studio/";

export function editorBodyHtml() {
    const html = readSource(STUDIO + "editor.html");
    const m = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html);
    return (m ? m[1] : html).replace(/<script\b[\s\S]*?<\/script>/gi, "");
}

// opts.scripts: list of studio file names to load after spore_ui.js, each exposed under
// win.__internals_<name without .js> (e.g. win.__internals_editor). opts.fetch / opts.url /
// any makeWindow option pass through. opts.body: custom body markup instead of editor.html.
export function makeStudioWindow(opts = {}) {
    const win = makeWindow(Object.assign({ url: "http://127.0.0.1:5000/spore-studio", fetch: opts.fetch || makeFetch() }, opts));
    win.document.body.innerHTML = opts.body !== undefined ? opts.body : editorBodyHtml();
    win.document.readyState = "loading";
    const internals = {};
    const ui = loadScript(win, STUDIO + "spore_ui.js", { key: "__internals_spore_ui" });
    internals.spore_ui = ui.internals;
    for (const name of opts.scripts || []) {
        const file = name.includes("/") ? name : STUDIO + name;
        const key = "__internals_" + file.split("/").pop().replace(/\.js$/, "");
        // qrcode.js (vendored UMD) and container_layout.js (UMD factory) publish their API on
        // window; loadScript's automatic mode leaves them unchanged.
        const exposeOpt = opts.expose === false ? false : undefined;
        const r = loadScript(win, file, { expose: exposeOpt, key });
        internals[key.replace("__internals_", "")] = r.internals;
    }
    win.document.readyState = "complete";
    return { win, internals };
}

export function fireDomReady(win) {
    win.document.dispatchEvent(new win.Event("DOMContentLoaded"));
}

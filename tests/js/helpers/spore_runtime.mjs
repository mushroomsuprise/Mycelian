// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Fixture for templates/_boilerplates/spore_data_runtime.js: a window with optional body markup,
// a fake Socket.IO `socket` global (as overlay pages declare one) and a sporeSetText stub that
// records [id, text] in win.__setText.

import { makeWindow, loadScript, makeIo, run } from "./env.mjs";

export const RUNTIME = "templates/_boilerplates/spore_data_runtime.js";

export function setupRuntime(opts = {}) {
    const win = makeWindow(opts);
    if (opts.html) { win.document.body.innerHTML = opts.html; }
    if (opts.socket !== false) { win.socket = makeIo()("http://x"); }
    win.sporeSetText = (id, text) => {
        const el = win.document.getElementById(id);
        if (el) { el.textContent = text; }
        (win.__setText = win.__setText || []).push([id, text]);
    };
    if (opts.before) { opts.before(win); }
    const { internals: I } = loadScript(win, RUNTIME);
    if (opts.random !== undefined) { run(win, "Math.random = function () { return " + opts.random + "; }"); }
    return { win, I, doc: win.document, clock: win.__clock, socket: win.socket };
}

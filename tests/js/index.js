// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Entry point for `node --test tests/js/`. Node 23 does not expand a directory argument to
// --test, so it resolves the directory as a module and loads this file, which imports every
// *.test.mjs here. Running the files directly (node --test "tests/js/*.test.mjs") works too.
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const files = fs.readdirSync(__dirname).filter((f) => f.endsWith(".test.mjs")).sort();
(async () => {
    for (const f of files) {
        await import(pathToFileURL(path.join(__dirname, f)).href);
    }
})().catch((err) => {
    console.error(err);
    process.exitCode = 1;
});

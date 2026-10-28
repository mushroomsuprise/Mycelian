// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/spore_studio/qrcode.js is a vendored library (Kazuhiko Arase). Smoke test
// only: it loads as a classic script, exposes qrcode(), and encodes the way editor.js and
// spore_data_runtime.js call it (qrcode(0, "M") + addData + make + createSvgTag).

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript } from "./helpers/env.mjs";

function setup() {
    const win = makeWindow();
    loadScript(win, "assets/default_assets/spore_studio/qrcode.js", { expose: false });
    return win;
}

test("loads as a global qrcode()", () => {
    const win = setup();
    assert.equal(typeof win.qrcode, "function");
});

test("encodes a URL with automatic version and draws an SVG", () => {
    const win = setup();
    const qr = win.qrcode(0, "M");
    qr.addData("https://twitch.tv/mycelian");
    qr.make();
    const n = qr.getModuleCount();
    assert.equal(n, 25, "26 bytes fit version 2 at level M");
    assert.equal((n - 17) % 4, 0);
    // finder pattern corners are dark, the separator next to them is light
    assert.equal(qr.isDark(0, 0), true);
    assert.equal(qr.isDark(0, n - 1), true);
    assert.equal(qr.isDark(n - 1, 0), true);
    assert.equal(qr.isDark(7, 7), false);
    const svg = qr.createSvgTag(4, 8);
    assert.match(svg, /^<svg /);
    assert.ok(svg.includes('width="' + (n * 4 + 16) + 'px"'));
    assert.match(svg, /<path /);
    assert.match(qr.createDataURL(2, 0), /^data:image\/gif;base64,/);
    assert.match(qr.createImgTag(2), /^<img src="data:image\/gif;base64,/);
});

test("the same data always yields the same matrix, and longer data a larger code", () => {
    const win = setup();
    const grid = (text, level = "M") => {
        const qr = win.qrcode(0, level);
        qr.addData(text);
        qr.make();
        const rows = [];
        for (let r = 0; r < qr.getModuleCount(); r++) {
            let row = "";
            for (let c = 0; c < qr.getModuleCount(); c++) { row += qr.isDark(r, c) ? "1" : "0"; }
            rows.push(row);
        }
        return rows;
    };
    assert.deepEqual(grid("hello"), grid("hello"));
    assert.notDeepEqual(grid("hello"), grid("hellp"));
    assert.equal(grid("hi").length, 21);
    assert.ok(grid("x".repeat(300)).length > 50);
    assert.ok(grid("x".repeat(100), "H").length > grid("x".repeat(100), "L").length);
});

test("too much data for the chosen version throws", () => {
    const win = setup();
    const qr = win.qrcode(1, "H");
    qr.addData("x".repeat(100));
    assert.throws(() => qr.make(), /code length overflow/);
});

test("UTF-8 conversion is available for non-ASCII text", () => {
    const win = setup();
    const bytes = win.qrcode.stringToBytesFuncs["UTF-8"]("é😀");
    assert.deepEqual(Array.from(bytes), [0xc3, 0xa9, 0xf0, 0x9f, 0x98, 0x80]);
});

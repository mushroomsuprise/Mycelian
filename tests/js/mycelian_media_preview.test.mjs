// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// assets/default_assets/mycelian_media_preview.js: play/stop toggling for sound, random-folder and
// TTS preview buttons, volume parsing, duration probing and the picker volume.

import test from "node:test";
import assert from "node:assert/strict";
import { makeWindow, loadScript, run, settle } from "./helpers/env.mjs";

const FILE = "assets/default_assets/mycelian_media_preview.js";

function setup(opts = {}) {
    const audios = [];
    class Audio {
        constructor() { this.src = ""; this.volume = 1; this.paused = true; audios.push(this); }
        play() { this.paused = false; return opts.playResult ? opts.playResult(this) : Promise.resolve(); }
        pause() { if (opts.pauseThrows) { throw new Error("x"); } this.paused = true; }
    }
    const spoken = [];
    let cancels = 0;
    const speech = opts.noSpeech ? undefined : { speak(u) { spoken.push(u); }, cancel() { cancels++; if (opts.cancelThrows) { throw new Error("c"); } } };
    class SpeechSynthesisUtterance { constructor(text) { this.text = text; } }
    const extra = { Audio, speechSynthesis: speech };
    if (!opts.noUtterance) { extra.SpeechSynthesisUtterance = SpeechSynthesisUtterance; }
    const win = makeWindow({ extra });
    if (opts.random !== undefined) { run(win, "Math.random = function () { return " + opts.random + "; }"); }
    loadScript(win, FILE);
    return { win, P: win.MycelianPreview, audios, spoken, cancels: () => cancels };
}

function button(win, attrs, icon = true) {
    const b = win.document.createElement("button");
    for (const [k, v] of Object.entries(attrs)) { b.setAttribute(k, v); }
    if (icon) { b.innerHTML = '<i class="q-icon">play_arrow</i><span class="lbl">Play</span>'; }
    win.document.body.appendChild(b);
    return b;
}
const click = (el) => ({ target: el });

test("loading twice keeps the first instance", () => {
    const { win, P } = setup();
    loadScript(win, FILE);
    assert.equal(win.MycelianPreview, P);
});

test("toggle ignores events without a preview button", () => {
    const { win, P, audios } = setup();
    assert.equal(P.toggle(null), null);
    assert.equal(P.toggle({}), null);
    assert.equal(P.toggle({ target: { nodeType: 3 } }), null);
    const plainBtn = win.document.createElement("button");
    win.document.body.appendChild(plainBtn);
    assert.equal(P.toggle(click(plainBtn)), null);
    assert.equal(P.toggle({ srcElement: plainBtn }), null);
    assert.equal(audios.length, 0);
});

test("sound button plays its URL at its volume and shows a stop icon", () => {
    const { win, P, audios } = setup();
    const b = button(win, { "data-pv": "sound", "data-src": "/a.mp3", "data-vol": "40" });
    assert.ok((P.toggle(click(b.querySelector(".lbl")))) === null);
    assert.equal(audios.length, 1);
    assert.equal(audios[0].src, "/a.mp3");
    assert.equal(audios[0].volume, 0.4);
    assert.equal(audios[0].preload, "auto");
    assert.equal(audios[0].paused, false);
    assert.ok(b.classList.contains("is-playing"));
    assert.equal(b.querySelector(".q-icon").textContent, "stop");
    // clicking the same button again stops it
    P.toggle(click(b));
    assert.equal(audios[0].paused, true);
    assert.equal(audios[0].src, "");
    assert.ok(!b.classList.contains("is-playing"));
    assert.equal(b.querySelector(".q-icon").textContent, "play_arrow");
});

test("volume is clamped and defaults to full", () => {
    const { win, P, audios } = setup();
    for (const [vol, want] of [["150", 1], ["-3", 0], ["abc", 1], [null, 1]]) {
        const attrs = { "data-pv": "sound", "data-src": "/x.mp3" };
        if (vol !== null) { attrs["data-vol"] = vol; }
        P.toggle(click(button(win, attrs)));
        assert.equal(audios.at(-1).volume, want, String(vol));
    }
});

test("a sound button without a URL does nothing", () => {
    const { win, P, audios } = setup();
    const b = button(win, { "data-pv": "sound" });
    P.toggle(click(b));
    assert.equal(audios.length, 0);
    assert.ok(!b.classList.contains("is-playing"));
});

test("starting another preview stops the last one; custom data-icon is restored", () => {
    const { win, P, audios } = setup();
    const a = button(win, { "data-pv": "sound", "data-src": "/1.mp3", "data-icon": "volume_up" });
    const b = button(win, { "data-pv": "sound", "data-src": "/2.mp3" });
    P.toggle(click(a));
    P.toggle(click(b));
    assert.equal(audios[0].paused, true);
    assert.equal(a.querySelector(".q-icon").textContent, "volume_up");
    assert.ok(b.classList.contains("is-playing"));
});

test("ended, error and rejected play() reset the button (only for the current audio)", async () => {
    let reject = false;
    const { win, P, audios } = setup({ playResult: () => (reject ? Promise.reject(new Error("blocked")) : Promise.resolve()) });
    const b = button(win, { "data-pv": "sound", "data-src": "/1.mp3" });
    P.toggle(click(b));
    audios[0].onended();
    assert.ok(!b.classList.contains("is-playing"));
    P.toggle(click(b));
    audios[1].onerror();
    assert.ok(!b.classList.contains("is-playing"));
    P.toggle(click(b));
    audios[0].onended(); // stale audio: ignored
    assert.ok(b.classList.contains("is-playing"));
    reject = true;
    P.toggle(click(b)); // stop
    P.toggle(click(b)); // play, rejected
    await settle();
    assert.ok(!b.classList.contains("is-playing"));
});

test("a play() that returns nothing and a pause() that throws are tolerated", () => {
    const { win, P } = setup({ playResult: () => undefined, pauseThrows: true });
    const b = button(win, { "data-pv": "sound", "data-src": "/1.mp3" }, false);
    P.toggle(click(b));
    assert.ok(b.classList.contains("is-playing"));
    assert.doesNotThrow(() => P.stop());
    assert.ok(!b.classList.contains("is-playing"));
});

test("random button picks a file, URL-encodes it and returns its name", () => {
    const { win, P, audios } = setup({ random: 0.99 });
    const b = button(win, { "data-pv": "random", "data-base": "/sounds/folder/", "data-files": JSON.stringify(["a.mp3", "b c#.mp3"]), "data-vol": "50" });
    assert.equal(P.toggle(click(b)), "b c#.mp3");
    assert.equal(audios[0].src, "/sounds/folder/b%20c%23.mp3");
    assert.equal(audios[0].volume, 0.5);
});

test("random button with no or bad file list does nothing", () => {
    const { win, P, audios } = setup({ random: 0 });
    assert.equal(P.toggle(click(button(win, { "data-pv": "random", "data-files": "[]" }))), null);
    assert.equal(P.toggle(click(button(win, { "data-pv": "random", "data-files": "{bad" }))), null);
    assert.equal(P.toggle(click(button(win, { "data-pv": "random", "data-files": '{"a":1}' }))), null);
    assert.equal(P.toggle(click(button(win, { "data-pv": "random" }))), null);
    assert.equal(audios.length, 0);
    assert.equal(P.toggle(click(button(win, { "data-pv": "random", "data-files": "[7]" }))), "7");
    assert.equal(audios[0].src, "7", "no data-base means a bare name");
});

test("tts button speaks a non-empty text with rate, pitch and clamped volume", () => {
    const { win, P, spoken, cancels } = setup({ random: 0.6 });
    const b = button(win, { "data-pv": "tts", "data-texts": JSON.stringify(["", "  ", "one", "two"]), "data-rate": "1.5", "data-pitch": "0.8", "data-tts-vol": "3" });
    assert.equal(P.toggle(click(b)), null);
    assert.equal(spoken.length, 1);
    assert.equal(spoken[0].text, "two");
    assert.equal(spoken[0].rate, 1.5);
    assert.equal(spoken[0].pitch, 0.8);
    assert.equal(spoken[0].volume, 1);
    assert.equal(cancels(), 1);
    assert.ok(b.classList.contains("is-playing"));
    spoken[0].onend();
    assert.ok(!b.classList.contains("is-playing"));
    spoken[0].onerror(); // already finished: no change
    assert.ok(!b.classList.contains("is-playing"));
});

test("tts defaults and stop() cancels speech", () => {
    const { win, P, spoken, cancels } = setup({ random: 0 });
    const b = button(win, { "data-pv": "tts", "data-texts": '["hi"]', "data-tts-vol": "-1" });
    P.toggle(click(b));
    assert.equal(spoken[0].rate, 1);
    assert.equal(spoken[0].pitch, 1);
    assert.equal(spoken[0].volume, 0);
    P.toggle(click(b));
    assert.equal(cancels(), 2);
    assert.ok(!b.classList.contains("is-playing"));
});

test("tts end of an older button does not reset the current one", () => {
    const { win, P, spoken } = setup({ random: 0 });
    const a = button(win, { "data-pv": "tts", "data-texts": '["a"]' });
    const b = button(win, { "data-pv": "tts", "data-texts": '["b"]' });
    P.toggle(click(a));
    P.toggle(click(b));
    spoken[0].onend();
    assert.ok(b.classList.contains("is-playing"));
});

test("tts without texts, without speechSynthesis or without the utterance class does nothing", () => {
    for (const opts of [{}, { noSpeech: true }, { noUtterance: true }]) {
        const { win, P, spoken } = setup(opts);
        const texts = Object.keys(opts).length ? '["x"]' : '["", null]';
        const b = button(win, { "data-pv": "tts", "data-texts": texts });
        P.toggle(click(b));
        assert.equal(spoken.length, 0);
        assert.ok(!b.classList.contains("is-playing"));
    }
});

test("stop() swallows a failing speechSynthesis.cancel()", () => {
    const { win, P } = setup({ random: 0, cancelThrows: false });
    const b = button(win, { "data-pv": "tts", "data-texts": '["x"]' });
    P.toggle(click(b));
    win.speechSynthesis.cancel = () => { throw new Error("x"); };
    assert.doesNotThrow(() => P.stop());
});

test("unknown mode only stops what was playing", () => {
    const { win, P, audios } = setup();
    P.toggle(click(button(win, { "data-pv": "sound", "data-src": "/1.mp3" })));
    assert.equal(P.toggle(click(button(win, { "data-pv": "video" }))), null);
    assert.equal(audios[0].paused, true);
});

test("duration resolves the metadata length, null for errors, zero/NaN, no URL or timeout", async () => {
    const { win, P, audios } = setup();
    assert.equal(await P.duration(""), null);
    const p1 = P.duration("/a.mp3");
    const a1 = audios.at(-1);
    assert.equal(a1.preload, "metadata");
    assert.equal(a1.src, "/a.mp3");
    a1.duration = 2.5;
    a1.onloadedmetadata();
    a1.onerror(); // second finish ignored
    assert.equal(await p1, 2.5);
    assert.equal(a1.src, "");
    const p2 = P.duration("/b.mp3");
    audios.at(-1).onerror();
    assert.equal(await p2, null);
    const p3 = P.duration("/c.mp3");
    audios.at(-1).duration = Infinity;
    audios.at(-1).onloadedmetadata();
    assert.equal(await p3, null);
    const p4 = P.duration("/d.mp3");
    win.__clock.tick(8000);
    assert.equal(await p4, null);
});

test("setPickerVolume updates every picker media element and unmutes videos above zero", () => {
    const { win, P } = setup();
    win.document.body.innerHTML = '<audio data-pv-media></audio><video data-pv-media></video><video></video>';
    const [audio, video, other] = win.document.querySelectorAll("audio, video");
    P.setPickerVolume("55.5");
    assert.equal(audio.getAttribute("data-vol"), "56");
    assert.equal(audio.volume, 0.555);
    assert.equal(video.muted, false);
    assert.equal(other.volume, undefined);
    P.setPickerVolume(0);
    assert.equal(video.muted, true);
    P.setPickerVolume("junk");
    assert.equal(audio.volume, 0);
    P.setPickerVolume(400);
    assert.equal(audio.getAttribute("data-vol"), "100");
    assert.equal(audio.muted, undefined, "audio elements are never muted by this");
});

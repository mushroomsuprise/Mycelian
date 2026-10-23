"""RT-05: chat overlay FFZ poll skips parsing byte-identical bodies, same emote lists."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CHAT_HTML = Path(__file__).resolve().parents[1] / "templates" / "chat.html"
FUNCS = (
    "ffzListSignature",
    "ffzGlobalEmotes",
    "applyFfzList",
    "fetchFfzJson",
    "ffzResultData",
    "rememberFfzRaw",
    "fetchFFZEmotesNow",
)


def _extract(src: str, name: str) -> str:
    start = re.search(r"function %s\(" % name, src).start()
    depth = 0
    i = src.index("{", start)
    while True:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1


HARNESS = r"""
var emoteCache = { ffz: { global: [], user: [] } };
var ffzGlobalSignature = null, ffzUserSignature = null, streamerID = '42';
var ffzRawCache = {};
var rebuilds = 0, parses = 0;
var realParse = JSON.parse;
JSON.parse = function (t) { parses++; return realParse(t); };
function rebuildEmoteLookupMap() { rebuilds++; }
function chatLog() {}
function noteFfzRateLimit() {}
var console = { error: function () {}, warn: function () {} };
var responses = [];
function fetch(url) {
  var r = responses.shift();
  var isGlobal = url.indexOf('/set/global') >= 0;
  var item = isGlobal ? r.g : r.r;
  return Promise.resolve({
    status: item.status || 200,
    ok: (item.status || 200) < 300,
    text: function () { return Promise.resolve(item.body); },
  });
}
%FUNCS%
function g(names) {
  return JSON.stringify({ default_sets: [3], sets: { '3': { emoticons: names.map(function (n, i) {
    return { id: i + 1, name: n, urls: { '1': 'https://x/' + n } }; }) } } });
}
function room(set, names) {
  var sets = {}; sets[set] = { emoticons: names.map(function (n, i) {
    return { id: 100 + i, name: n, urls: { '1': 'https://y/' + n } }; }) };
  return JSON.stringify({ room: { set: set }, sets: sets });
}
var steps = [];
for (var i = 0; i < 20; i++) steps.push({ g: { body: g(['A', 'B']) }, r: { body: room(7, ['U1']) } });
steps.push({ g: { body: g(['A', 'B']) }, r: { status: 404, body: '' } });
steps.push({ g: { body: g(['A', 'B']) }, r: { body: room(7, ['U1']) } });
steps.push({ g: { body: g(['A', 'B', 'C']) }, r: { body: room(7, ['U1']) } });
steps.push({ g: { status: 429, body: '' }, r: { body: room(7, ['U1']) } });
steps.push({ g: { body: g(['A', 'B', 'C']) }, r: { body: room(7, ['U1', 'U2']) } });
steps.push({ reset: true, g: { body: g(['A', 'B', 'C']) }, r: { body: room(7, ['U1', 'U2']) } });
steps.push({ g: { body: g(['A', 'B', 'C']) }, r: { body: '{"room": {}}' } });
steps.push({ g: { body: g(['A', 'B', 'C']) }, r: { body: '{"room": {}}' } });
steps.push({ g: { body: 'not json' }, r: { body: room(7, ['U1', 'U2']) } });
var alwaysParse = %ALWAYS%;
(async function () {
  var out = [];
  for (var s = 0; s < steps.length; s++) {
    if (alwaysParse) ffzRawCache = {};
    if (steps[s].reset) ffzUserSignature = null;  // streamer switched back
    responses = [steps[s], steps[s]];
    var res;
    try { res = await fetchFFZEmotesNow(); } catch (e) { res = 'rejected'; }
    out.push([res, JSON.stringify(emoteCache), rebuilds]);
  }
  process.stdout.write(JSON.stringify({ out: out, parses: parses }));
})();
"""


def _run(always_parse: bool) -> dict:
    src = CHAT_HTML.read_text(encoding="utf-8")
    funcs = "\n".join(_extract(src, n) for n in FUNCS)
    script = HARNESS.replace("%FUNCS%", funcs).replace(
        "%ALWAYS%", "true" if always_parse else "false"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ffz_poll_same_lists_fewer_parses():
    reference = _run(always_parse=True)
    cached = _run(always_parse=False)
    assert cached["out"] == reference["out"]
    assert cached["parses"] < reference["parses"] // 3

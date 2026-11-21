# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""modules/fast_json.py: orjson where the reader sees the same JSON, json otherwise (WE-51)."""

from __future__ import annotations

import dataclasses
import datetime
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from flask import Flask, jsonify  # noqa: E402

from modules import fast_json as fj  # noqa: E402

SAMPLES = [
    {"b": 1, "a": [1, 2.5, None, True], "nested": {"z": "é ✓ 😀", "y": ""}},
    [{"user": "Ünïcode", "amount": 100}],
    {1: "int key", "s": "x"},
    "plain",
    0,
]


@pytest.mark.parametrize("value", SAMPLES)
def test_socket_dumps_parses_to_the_same_value_and_key_order(value):
    ours = fj.dumps(value, separators=(",", ":"))
    theirs = json.dumps(value, separators=(",", ":"))
    assert json.loads(ours) == json.loads(theirs)
    if isinstance(value, dict):
        assert list(json.loads(ours)) == list(json.loads(theirs))
    assert fj.loads(ours) == json.loads(theirs)


def test_falls_back_to_json_for_what_orjson_cannot_do():
    huge = 2 ** 70
    assert fj.dumps({"n": huge}, separators=(",", ":")) == json.dumps({"n": huge}, separators=(",", ":"))
    assert fj.dumps({"a": 1}, indent=2) == json.dumps({"a": 1}, indent=2)  # other kwargs: json
    assert fj.loads('{"x": NaN}')["x"] != fj.loads('{"x": NaN}')["x"]  # NaN accepted like json
    with pytest.raises(TypeError):
        fj.dumps({"x": object()}, separators=(",", ":"))  # same error type as json


@dataclasses.dataclass
class _Row:
    a: int


def test_flask_responses_keep_sorted_keys_and_flask_conversions():
    plain = Flask("plain")
    fast = Flask("fast")
    assert fj.install_flask_provider(fast)
    when = datetime.datetime(2026, 11, 20, 12, 30, tzinfo=datetime.timezone.utc)
    payload = {"z": 1, "a": {"d": when, "u": uuid.UUID(int=5), "row": _Row(3), "t": "é"}, "m": [3, 2]}
    with plain.test_request_context():
        expected = jsonify(payload).get_json()
        expected_text = plain.json.dumps(payload)
    with fast.test_request_context():
        got = jsonify(payload).get_json()
        got_text = fast.json.dumps(payload)
    assert got == expected
    assert list(json.loads(got_text)) == list(json.loads(expected_text)) == ["a", "m", "z"]
    assert got["a"]["d"] == "Fri, 20 Nov 2026 12:30:00 GMT"  # Flask's HTTP date, not ISO
    assert fast.json.loads(b'{"x": 1}') == {"x": 1}
    assert fast.json.loads('{"x": NaN}', parse_constant=lambda c: c) == {"x": "NaN"}


def test_flask_debug_indent_uses_json():
    app = Flask("dbg")
    fj.install_flask_provider(app)
    assert app.json.dumps({"b": 1, "a": 2}, indent=2) == json.dumps({"a": 2, "b": 1}, indent=2)


def test_default_matches_json_for_unknown_types():
    when = datetime.datetime(2026, 1, 2, 3, 4, 5)
    class Thing:
        def __str__(self):
            return "thing"

    value = {"when": when, "obj": Thing(), "row": _Row(1), "n": 1}
    assert fj.dumps(value, default=str) == json.dumps(value, default=str).replace(", ", ",").replace(": ", ":")

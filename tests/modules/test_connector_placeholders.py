# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_placeholders: context building (hooks, action results), legacy braces, lookups,
date / number formatting, special tokens, JSON and shell substitution and mapping helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import datetime as dt
import json
import types
from types import SimpleNamespace

import pytest

import modules
from modules import connector_placeholders as cp


def fake_game_hooks(monkeypatch, **submodules):
    """Fake ``modules.game_hooks`` package with the given submodules."""
    pkg = types.ModuleType("modules.game_hooks")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "modules.game_hooks", pkg)
    monkeypatch.setattr(modules, "game_hooks", pkg, raising=False)
    for name, attrs in submodules.items():
        mod = types.ModuleType(f"modules.game_hooks.{name}")
        for k, v in attrs.items():
            setattr(mod, k, v)
        setattr(pkg, name, mod)
        monkeypatch.setitem(sys.modules, f"modules.game_hooks.{name}", mod)
    return pkg


def fake_hooks_service(monkeypatch, snapshots):
    svc = SimpleNamespace(get_hook_ui_snapshot=lambda hid: snapshots.get(hid, {}))
    mod = types.ModuleType("modules.game_hooks_service")
    mod.game_hooks_service = svc
    monkeypatch.setitem(sys.modules, "modules.game_hooks_service", mod)
    monkeypatch.setattr(modules, "game_hooks_service", mod, raising=False)


# --------------------------------------------------------------------------- clock + context


def test_clock_variables_render():
    now = dt.datetime(2026, 3, 4, 5, 6, 7)
    c = cp.clock_variables(now)
    assert str(c["time"]) == "05:06" and str(c["date"]) == "2026-03-04"
    assert str(c["datetime"]) == "2026-03-04 05:06"
    assert c["time"].second == 7
    assert isinstance(cp.clock_variables()["date"], dt.datetime)


def test_build_context_merges_and_maps_action_results(monkeypatch):
    fake_game_hooks(monkeypatch, registry={"registered_hook_ids": lambda: ["ff7", "ff8", "bad"]})
    fake_hooks_service(monkeypatch, {"ff7": {"ff7": {"gil": 5}}, "ff8": {"ff8": None}, "bad": {}})
    ctx = cp.build_connector_placeholder_context(
        {"username": "Bob", "time": "event time"},
        {"connector_name": "C", "action_results": {"1": {"x": 1}, "2": "skip"}, "action_index": 3},
    )
    assert ctx["username"] == "Bob" and ctx["connector_name"] == "C" and ctx["time"] == "event time"
    assert ctx["action1"] == {"x": 1} and "action2" not in ctx
    assert "action_results" not in ctx and "action_index" not in ctx
    assert ctx["hooks"] == {"ff7": {"gil": 5}} and isinstance(ctx["date"], dt.datetime)


def test_build_context_without_hooks(monkeypatch):
    fake_game_hooks(monkeypatch, registry={"registered_hook_ids": lambda: []})
    fake_hooks_service(monkeypatch, {})
    ctx = cp.build_connector_placeholder_context(None, None)
    assert "hooks" not in ctx and set(ctx) == {"time", "date", "datetime"}
    monkeypatch.setitem(sys.modules, "modules.game_hooks_service", None)
    assert "hooks" not in cp.build_connector_placeholder_context({}, {})


# --------------------------------------------------------------------------- legacy / lookup


def test_normalize_legacy_double_brace():
    f = cp.normalize_legacy_double_brace
    assert f("Hi {{ user name }}") == "Hi {username}"
    assert f("{{ amount :, }}") == "{amount:,}"
    assert f("{{ :x }}") == "{{ :x }}"
    assert f("{{  }}") == "{{  }}"
    assert f("no braces") == "no braces"


def test_ctx_lookup():
    ctx = {"a": {"b": [{"c": 1}, None]}, "t": (5, 6)}
    assert cp._ctx_lookup_found(ctx, "a.b.0.c") == (True, 1)
    assert cp._ctx_lookup_found(ctx, "a.b.1") == (True, None)
    assert cp._ctx_lookup_found(ctx, "a.b.5") == (False, None)
    assert cp._ctx_lookup_found(ctx, "t.1") == (True, 6)
    assert cp._ctx_lookup_found(ctx, "a.x") == (False, None)
    assert cp._ctx_lookup_found(ctx, "t.x") == (False, None)
    assert cp._ctx_lookup(ctx, "a.b.0.c") == 1


# --------------------------------------------------------------------------- dates


def test_portable_strftime():
    d = dt.datetime(2026, 1, 5, 9, 7, 3)
    assert cp.portable_strftime(d, "%-d/%-m %-H:%M 100%%") == "5/1 9:07 100%"
    assert cp.portable_strftime(d, "%#d") == "5"
    assert cp.portable_strftime(dt.datetime(2026, 1, 1, 0, 0), "%-H") == "0"
    assert cp.portable_strftime(d, "[%-e]") == "[5]"
    assert cp.portable_strftime(d, "%-B") == "January"


def test_portable_strftime_bad_directive(monkeypatch):
    class Picky:
        def strftime(self, fmt):
            if fmt == "%Q":
                raise ValueError("bad directive")
            return "X"

    assert cp.portable_strftime(Picky(), "a%Qb%Y") == "a%QbX"


def test_coerce_datetime():
    aware = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
    assert cp.coerce_datetime(aware).tzinfo is not None
    naive = dt.datetime(2026, 1, 1, 12)
    assert cp.coerce_datetime(naive) is naive
    assert cp.coerce_datetime(dt.date(2026, 2, 3)) == dt.datetime(2026, 2, 3)
    assert cp.coerce_datetime(None) is None and cp.coerce_datetime(True) is None
    epoch = 1_700_000_000
    assert cp.coerce_datetime(epoch) == dt.datetime.fromtimestamp(epoch)
    assert cp.coerce_datetime(epoch * 1000) == dt.datetime.fromtimestamp(epoch)
    assert cp.coerce_datetime(str(epoch)) == dt.datetime.fromtimestamp(epoch)
    assert cp.coerce_datetime("  ") is None
    assert cp.coerce_datetime("2026-01-02T03:04:05Z").tzinfo is not None
    assert cp.coerce_datetime("2026-01-02T03:04:05") == dt.datetime(2026, 1, 2, 3, 4, 5)
    assert cp.coerce_datetime("not a date") is None
    assert cp.coerce_datetime([1]) is None
    assert cp.coerce_datetime(float("nan")) is None
    assert cp.coerce_datetime(1e300) is None


# --------------------------------------------------------------------------- formatting


def test_stringify():
    assert cp._stringify(None) == "" and cp._stringify(True) == "true" and cp._stringify(False) == "false"
    assert cp._stringify({"a": "é"}) == '{"a": "é"}' and cp._stringify((1, 2)) == "[1, 2]"
    assert cp._stringify(3.0) == "3" and cp._stringify(3.5) == "3.5"

    bad = {"k": float("nan")}
    assert cp._stringify(bad) == '{"k": NaN}'
    circular = []
    circular.append(circular)
    assert cp._stringify(circular) == "[[...]]"


@pytest.mark.parametrize(
    "value,fmt,expected",
    [
        (None, ",", ""),
        (1234, ",", "1,234"),
        ("1234", ",", "1,234"),
        (" 12.5 ", ".2f", "12.50"),
        (5, ".1f", "5.0"),
        ("abc", ",", "abc"),
        (True, ",", "true"),
        (5, "bogus", "5"),
        ("x", "%Y", "x"),
        ({"a": 1}, ",", '{"a": 1}'),
        (7, "", "7"),
    ],
)
def test_format_placeholder_value(value, fmt, expected):
    assert cp.format_placeholder_value(value, fmt) == expected


def test_format_placeholder_value_dates():
    assert cp.format_placeholder_value(dt.datetime(2026, 3, 1, 8, 5), "%-I:%M") == "8:05"
    assert cp.format_placeholder_value(5, "x") == "5"  # int spec fails, float spec fails too


def test_format_int_falls_back_to_float_spec():
    assert cp.format_placeholder_value(5, ".2%") == "500.00%"
    assert cp.format_placeholder_value(5, ".1e") == "5.0e+00"
    assert cp.format_placeholder_value(5, "%") == "500.000000%"


# --------------------------------------------------------------------------- message tokens


def test_message_word_tokens():
    ctx = {"message": "one two three", "message_after_conditions": "alpha beta gamma"}
    s = cp.substitute_connector_placeholders
    assert s("{message.word.2}", ctx) == "two"
    assert s("[{message.word.0}][{message.word.9}]", ctx) == "[][]"
    assert s("{message_after_word.1}|{message_after_conditions.word.3}", ctx) == "alpha|gamma"
    assert s("[{message_after_word.0}][{message_after_word.7}]", ctx) == "[][]"
    assert s("{message_after_from_word.2}", ctx) == "beta gamma"
    assert s("{message_after_conditions.from_word.1}", ctx) == "alpha beta gamma"
    assert s("[{message_after_from_word.0}][{message_after_from_word.4}]", ctx) == "[][]"
    assert s("{message.word.1}", {}) == ""


def _hooks_ctx(party=None, enemies=None):
    return {"hooks": {"ff7": {"party": party, "enemies": enemies}}}


def test_random_character_and_enemy(monkeypatch):
    monkeypatch.setattr(cp.random, "choice", lambda seq: seq[-1])
    party = [{"name": "Cloud"}, {"name": "Tifa", "slot_empty": True}, {"name": "?"}, "row", {"name": " Aerith "}]
    assert cp.substitute_connector_placeholders("{random_character}", _hooks_ctx(party=party)) == "Aerith"
    assert cp.substitute_connector_placeholders("[{random_character}]", _hooks_ctx(party="x")) == "[]"
    enemies = ["row", {"name": ""}, {"name": "Guard Hound"}]
    assert cp.substitute_connector_placeholders("{random_enemy}", _hooks_ctx(enemies=enemies)) == "Guard Hound"
    assert cp.substitute_connector_placeholders("[{random_enemy}]", {}) == "[]"
    assert cp._party_names_from_ctx({}) == [] and cp._enemy_names_from_ctx(_hooks_ctx(enemies=[])) == []


def test_random_gear(monkeypatch):
    loads = []
    fake_game_hooks(monkeypatch, ff7_hook={
        "_load_ff7_gear_layout_assets": lambda: loads.append(1),
        "_WEAPON_NAMES_EN": {1: "Buster Sword", 2: " ", 3: None},
        "_ARMOR_NAMES_EN": {1: "Bronze Bangle"},
        "_ACCESSORY_NAMES_EN": {},
    })
    monkeypatch.setattr(cp.random, "choice", lambda seq: seq[0])
    s = cp.substitute_connector_placeholders
    assert s("{random_weapon}", {}) == "Buster Sword"
    assert s("{random_armor}", {}) == "Bronze Bangle"
    assert s("[{random_accessory}]", {}) == "[]"
    assert len(loads) == 3


def test_random_gear_failure(monkeypatch):
    def boom():
        raise RuntimeError("no assets")

    fake_game_hooks(monkeypatch, ff7_hook={"_load_ff7_gear_layout_assets": boom})
    assert cp.substitute_connector_placeholders("[{random_weapon}]", {}) == "[]"


def test_random_damage(monkeypatch):
    seen = []
    monkeypatch.setattr(cp.random, "randint", lambda lo, hi: seen.append((lo, hi)) or hi)
    s = cp.substitute_connector_placeholders
    assert s("{random_damage.50.10}", {}) == "50"
    assert s("{random_damage.0.99999}", {}) == "9999"
    assert seen == [(10, 50), (1, 9999)]


def test_resolve_placeholder():
    ctx = {"amount": 1500, "flag": None}
    assert cp.resolve_placeholder("amount", ctx, ",") == "1,500"
    assert cp.resolve_placeholder("amount", ctx) == "1500"
    assert cp.resolve_placeholder("flag", ctx) == ""
    assert cp.resolve_placeholder("missing", ctx) is None
    assert cp.resolve_placeholder("message.word.1", {"message": "1234 x"}, ",") == "1,234"


def test_substitute_connector_placeholders_basics():
    s = cp.substitute_connector_placeholders
    assert s("", {}) == "" and s(None, {}) is None and s("plain", {}) == "plain"
    assert s("{{ amount:, }} and {missing}", {"amount": 2000}) == "2,000 and {missing}"
    assert s("{{amount}}", {"amount": 1}) == "1"


# --------------------------------------------------------------------------- JSON


def test_json_bare_value():
    assert cp._json_bare_value(" 12 ") == "12" and cp._json_bare_value("true") == "true"
    assert cp._json_bare_value('{"a": 1}') == '{"a": 1}' and cp._json_bare_value("[1]") == "[1]"
    assert cp._json_bare_value("{bad") == '"{bad"' and cp._json_bare_value("hi") == '"hi"'


def test_substitute_json_placeholders():
    ctx = {"amount": 5, "name": 'Bo "B"', "obj": {"x": 1}}
    text = '{"amount": {amount}, "who": "Hi {name}", "o": {obj}, "keep": {missing}, "esc": "a\\"{amount}"}'
    out = cp.substitute_json_placeholders(text, ctx)
    assert '"keep": {missing}' in out
    data = json.loads(out.replace("{missing}", "null"))
    assert data == {"amount": 5, "who": 'Hi Bo "B"', "o": {"x": 1}, "keep": None, "esc": 'a"5'}
    assert cp.substitute_json_placeholders('{"a": {{ amount }}}', ctx) == '{"a": 5}'
    assert cp.substitute_json_placeholders("", ctx) == ""
    assert cp.substitute_json_placeholders("[1]", ctx) == "[1]"


def test_json_scan():
    assert cp._json_scan('"abc', False) is True
    assert cp._json_scan('\\"x"', True) is False
    assert cp._json_scan('a\\"', True) is True


# --------------------------------------------------------------------------- shell


def test_windows_quoting_helpers():
    assert cp.windows_argv_quote('a"b') == '"a\\"b"'
    assert cp.windows_argv_quote("dir\\") == '"dir\\\\"'
    assert cp.windows_argv_quote('x\\"') == '"x\\\\\\""'
    assert cp.windows_argv_quote("a\\b") == '"a\\b"'
    assert cp.windows_cmd_quote("a&b\r\nc") == '^"a^&b c^"'
    assert cp._windows_in_quotes_safe('a"b%c\r\nd') == "abc  d"


def test_substitute_shell_posix():
    ctx = {"m": "it's $x `y` \"z\" \\"}
    s = cp.substitute_shell_placeholders
    assert s("echo {m}", ctx, windows=False) == "echo " + "'it'\"'\"'s $x `y` \"z\" \\'"
    assert s('echo "{m}"', ctx, windows=False) == 'echo "it\'s \\$x \\`y\\` \\"z\\" \\\\"'
    assert s("echo '{m}'", ctx, windows=False) == "echo 'it'\\''s $x `y` \"z\" \\'"
    assert s("echo {nope}", ctx, windows=False) == "echo {nope}"
    assert s("echo \\'{m}", {"m": "a"}, windows=False) == "echo \\'a"
    assert s('echo "a\\"{m}"', {"m": "b"}, windows=False) == 'echo "a\\"b"'
    assert s("", ctx) == "" and s("ls", ctx) == "ls"


def test_substitute_shell_windows(monkeypatch):
    ctx = {"m": 'a&b"%c'}
    s = cp.substitute_shell_placeholders
    assert s("echo {m}", ctx, windows=True) == "echo " + cp.windows_cmd_quote('a&b"%c')
    assert s('echo "{m}"', ctx, windows=True) == 'echo "a&bc"'
    assert s('echo ^"{m}', ctx, windows=True) == 'echo ^"' + cp.windows_cmd_quote('a&b"%c')
    monkeypatch.setattr(cp.sys, "platform", "win32")
    assert s("x {m}", {"m": "1"}) == 'x ^"1^"'
    monkeypatch.setattr(cp.sys, "platform", "linux")
    assert s("x {m}", {"m": "1"}) == "x 1"


def test_shell_scan_states():
    assert cp._shell_scan("'abc", None, False) == "'"
    assert cp._shell_scan("x'", "'", False) is None
    assert cp._shell_scan('"a\\"b', None, False) == '"'
    assert cp._shell_scan('"a" ', None, False) is None
    assert cp._shell_scan('^"', None, True) is None
    assert cp._shell_scan('"^"', None, True) is None


# --------------------------------------------------------------------------- mapping


def test_substitute_mapping_recursive():
    ctx = {"u": "Bob"}
    data = {"a": "{u}", "b": ["x {u}", {"c": "{u}"}, 3], "d": 4, "e": "no braces"}
    assert cp.substitute_connector_placeholders_mapping(data, ctx) == {
        "a": "Bob", "b": ["x Bob", {"c": "Bob"}, 3], "d": 4, "e": "no braces"}
    assert data["a"] == "{u}"

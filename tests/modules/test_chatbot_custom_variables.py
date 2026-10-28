# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot_custom_variables.py: safe math, comparisons, durations, the
expression mini-language, structured definitions, Twitch variables, storage and previews."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import chatbot_custom_variables as ccv
from modules import chatbot_lookups, database_manager, notification_engine


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(ccv, "_cache", None)
    monkeypatch.setattr(ccv, "_cache_failed_at", 0.0)
    monkeypatch.setattr(ccv, "_twitch_cache", {})


class FakeDB:
    def __init__(self):
        self.stored = {}
        self.ok = True
        self.raises = False
        self.write_result = True
        self.write_raises = False
        self.writes = []

    def get_data_checked(self, path):
        assert path == ccv.CUSTOM_VARIABLES_PATH
        if self.raises:
            raise RuntimeError("db down")
        return self.stored, self.ok

    def set_data(self, path, payload):
        if self.write_raises:
            raise RuntimeError("write down")
        self.writes.append(payload)
        if self.write_result:
            self.stored = payload
        return self.write_result


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    notices = []
    monkeypatch.setattr(database_manager, "get_data_checked", fake.get_data_checked)
    monkeypatch.setattr(database_manager, "set_data", fake.set_data)
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: notices.append((msg, kw)))
    fake.notices = notices
    return fake


# --------------------------------------------------------------------------- preview mode


def test_preview_mode_nests_and_restores():
    assert ccv.in_preview() is False
    with ccv.preview_mode():
        assert ccv.in_preview() is True
        with ccv.preview_mode():
            assert ccv.in_preview() is True
        assert ccv.in_preview() is True
    assert ccv.in_preview() is False


# --------------------------------------------------------------------------- math


def test_round_and_random_helpers(monkeypatch):
    assert ccv._round(2.6) == 3
    assert ccv._round(2.567, 2) == 2.57
    monkeypatch.setattr(ccv.random, "randint", lambda a, b: ("int", a, b))
    monkeypatch.setattr(ccv.random, "uniform", lambda a, b: ("float", a, b))
    assert ccv._random(6, 1) == ("int", 1, 6)
    assert ccv._random(1.5, 2) == ("float", 1.5, 2)


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1 + 2 * 3", 7),
        ("-(2) + +3", 1),
        ("7 // 2 + 7 % 2", 4),
        ("2 ** 10", 1024),
        ("min(3, 1, 2) + max(1, 9) + abs(-2) + ceil(1.1) + int(2.9) + float(1)", 1 + 9 + 2 + 2 + 2 + 1.0),
        ("round(3.14159, 2)", 3.14),
    ],
)
def test_safe_eval_math_ok(expr, expected):
    assert ccv.safe_eval_math(expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr,msg",
    [
        ("", "empty expression"),
        ("1+" * 300 + "1", "expression too long"),
        ("1 +", "invalid expression"),
        ("True + 1", "only numbers are allowed"),
        ("'a'", "only numbers are allowed"),
        ("1 / 0", "division by zero"),
        ("2 ** 101", "power too large"),
        ("2000000 ** 2", "power too large"),
        ("(-8) ** 0.5", "complex result"),
        ("10.0 ** 99 * 10.0 ** 99 * 10.0 ** 99 * 10.0 ** 99", "result too large"),
        ("foo(1)", "function not allowed"),
        ("(1)(2)", "function not allowed"),
        ("round(1, ndigits=2)", "keyword arguments are not allowed"),
        ("random(1)", "wrong number of arguments for random"),
        ("x", "not allowed: Name"),
        ("not 1", "not allowed: UnaryOp"),
        ("int(1e400)", "cannot convert float infinity to integer"),
        ("max()", "wrong number of arguments for max"),
    ],
)
def test_safe_eval_math_rejects(expr, msg):
    with pytest.raises(ValueError) as err:
        ccv.safe_eval_math(expr)
    assert msg in str(err.value)


def test_safe_eval_math_type_error_becomes_value_error():
    with pytest.raises(ValueError):
        ccv.safe_eval_math("min(1)")  # TypeError inside: int is not iterable


@pytest.mark.parametrize(
    "value,decimals,expected",
    [
        (True, None, "1"),
        (False, None, "0"),
        (7, None, "7"),
        ("abc", None, "abc"),
        (None, None, "None"),
        (float("nan"), None, "0"),
        (float("inf"), None, "0"),
        (2.5, 0, "2"),
        (2.567, 2, "2.57"),
        (1.0, 20, "1.0000000000"),
        (3.0, None, "3"),
        (1.23456, None, "1.2346"),
        (0.00001, None, "0"),
        ("4.50", None, "4.5"),
        (2.5, -3, "2"),
    ],
)
def test_format_number(value, decimals, expected):
    assert ccv.format_number(value, decimals) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(True, None), (None, None), (3, 3.0), (2.5, 2.5), ("1,234", 1234.0), (" -1e3 ", -1000.0),
     (".5", 0.5), ("abc", None), ("1.2.3", None)],
)
def test_as_number(value, expected):
    assert ccv.as_number(value) == expected


# --------------------------------------------------------------------------- compare


@pytest.mark.parametrize(
    "left,op,right,expected",
    [
        ("10", ">", "9", True),
        ("10", ">=", "10", True),
        ("2", "<", "10", True),
        ("2", "<=", "1", False),
        ("1,000", "==", "1000", True),
        ("a", "=", "A", True),
        ("a", "!=", "b", True),
        ("abc", "greater_than", "abb", False),
        ("Hello World", "contains", "WORLD", True),
        ("Hello", "starts_with", "he", True),
        ("Hello", "ends_with", "LO", True),
        (None, "equal", None, True),
        ("5", "equals", "5.0", True),
    ],
)
def test_compare_values(left, op, right, expected):
    assert ccv.compare_values(left, op, right) is expected


def test_compare_values_unknown_operator():
    with pytest.raises(ValueError):
        ccv.compare_values(1, "~", 2)


# --------------------------------------------------------------------------- durations


def test_to_local_naive():
    assert ccv.to_local_naive(None) is None
    naive = ccv.to_local_naive("2024-01-02T03:04:05Z")
    assert naive.tzinfo is None and naive.year == 2024
    plain = datetime(2020, 1, 1)
    assert ccv.to_local_naive(plain) == plain


def test_friendly_duration():
    start = datetime(2020, 1, 31, 10, 0)
    assert ccv.friendly_duration(start, datetime(2022, 4, 1, 12, 30)) == "2 years, 2 months"
    assert ccv.friendly_duration(datetime(2020, 1, 1, 10), datetime(2020, 1, 1, 13, 5)) == "3 hours, 5 minutes"
    assert ccv.friendly_duration(datetime(2020, 1, 1), datetime(2020, 1, 1, 0, 0, 30)) == "less than a minute"
    # reversed order is fine
    assert ccv.friendly_duration(datetime(2021, 1, 1), datetime(2020, 1, 1)) == "1 year"
    assert ccv.friendly_duration(datetime(2020, 1, 1), datetime(2020, 2, 2, 1), max_units=3) == "1 month, 1 day, 1 hour"
    assert ccv.friendly_duration(None) == ""
    assert ccv.friendly_duration("2020-01-01", "junk") == ""
    assert ccv.friendly_duration(datetime.now() - timedelta(days=3, hours=1)).startswith("3 days")


def test_friendly_duration_month_anchor_overshoot():
    # Jan 31 -> Mar 1: the month count overshoots once and is corrected.
    assert ccv.friendly_duration(datetime(2021, 1, 31), datetime(2021, 3, 1)) == "1 month, 1 day"


def test_add_months_clamps_day():
    assert ccv._add_months(datetime(2020, 1, 31), 1) == datetime(2020, 2, 29)
    assert ccv._add_months(datetime(2020, 11, 15), 3) == datetime(2021, 2, 15)


def test_legacy_date_to_age():
    assert ccv._legacy_date_to_age("nonsense") == "0 days"
    ten_days = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S")
    assert ccv._legacy_date_to_age(f'"{ten_days}"') == "1 years, 35 days"
    assert ccv._evaluate_date_to_age(None) == "0 days"


# --------------------------------------------------------------------------- mini-language helpers


def test_find_close():
    assert ccv._find_close("math(1 + (2))", 4) == 12
    assert ccv._find_close("if('a)', b)", 2) == 10
    assert ccv._find_close("x({a)}, 1)", 1) == 9
    assert ccv._find_close("x(}1)", 1) == 4
    assert ccv._find_close("x(1", 1) == -1


def test_split_args():
    assert ccv._split_args("a, 'b,c', {x,y}, f(1, 2)") == ["a", " 'b,c'", " {x,y}", " f(1, 2)"]
    assert ccv._split_args("") == [""]
    assert ccv._split_args("}a, b") == ["}a", " b"]


def test_strip_quotes():
    assert ccv._strip_quotes(" 'a' ") == "a"
    assert ccv._strip_quotes('"a"') == "a"
    assert ccv._strip_quotes("'a\"") == "'a\""
    assert ccv._strip_quotes("'") == "'"


def test_math_operand_text():
    ctx = {"n": "4", "f": "2.5", "t": "hi"}
    assert ccv._math_operand_text("{n} + {f} + {t} + {missing}", ctx, 0) == "4 + 2.5 + 'hi' + '{missing}'"
    assert ccv._math_operand_text("math(1 + 1) * 2", ctx, 0) == "2 * 2"


def test_eval_call_variants():
    ctx = {"a": "5", "d": (datetime.now() - timedelta(days=370)).isoformat(), "yes": "yes"}
    assert ccv._eval_call("MATH", "{a} * 2", ctx, 0) == "10"
    assert ccv._eval_call("math", "{a} / 0", ctx, 0) == "0"
    assert ccv._eval_call("compare", "{a}, >, 3", ctx, 0) == "True"
    assert ccv._eval_call("compare", "'x', ~, 'x'", ctx, 0) == "False"
    assert ccv._eval_call("compare", "1, 2", ctx, 0) == "False"
    assert ccv._eval_call("date_to_age", "{d}", ctx, 0).startswith("1 years")
    assert ccv._eval_call("if", "{yes}, 'big', 'small'", ctx, 0) == "big"
    assert ccv._eval_call("if", "no, 'big', 'small'", ctx, 0) == "small"
    assert ccv._eval_call("if", "1, 2", ctx, 0) == ""
    assert ccv._eval_call("other", "x", ctx, 0) == ""


def test_evaluate_calls_and_expression():
    ctx = {"n": "3", "name": "{evil}"}
    assert ccv._evaluate_calls("x math(1+1) y math(2", ctx, 0) == "x \x002\x01 y math(2"
    assert ccv._evaluate_calls("math(1+1)", ctx, 0, math_mode=True) == "2"
    assert ccv.evaluate_expression("Total: math({n} * 2) for {name}", ctx) == "Total: 6 for {evil}"
    assert ccv.evaluate_expression("plain {n}", ctx) == "plain 3"
    assert ccv.evaluate_expression(None) == ""


# --------------------------------------------------------------------------- definitions


@pytest.mark.parametrize(
    "name,expected",
    [("Big Number", "custom_big_number"), ("{custom_x}", "custom_x"), ("a-b c", "custom_a_b_c"),
     ("CUSTOM_y", "custom_y")],
)
def test_normalize_name(name, expected):
    assert ccv.normalize_name(name) == expected


@pytest.mark.parametrize("name", ["", "custom_", "bad!name", None])
def test_normalize_name_rejects(name):
    with pytest.raises(ValueError):
        ccv.normalize_name(name)


def test_compile_custom_variable_valid():
    assert ccv.compile_custom_variable("text", {"template": "hi"}) == "hi"
    assert ccv.compile_custom_variable("text", None) == ""
    assert ccv.compile_custom_variable("expression", {"expression": "math(1)"}) == "math(1)"
    assert ccv.compile_custom_variable("math", {"left": "1", "right": "2"}) == "math(1 + 2)"
    assert ccv.compile_custom_variable("math", {"left": "1", "op": "/", "right": "3", "decimals": "2"}) == \
        "math(round(1 / 3, 2))"
    assert ccv.compile_custom_variable("if", {"left": "{a}", "op": "contains", "right": "x", "then": "y",
                                              "else": "n"}) == "if(compare({a}, contains, x), y, n)"
    assert ccv.compile_custom_variable("if", {}) == "if(compare(, ==, ), , )"
    assert ccv.compile_custom_variable("age", {"date": "{followed}"}) == "date_to_age({followed})"
    assert ccv.compile_custom_variable("twitch", {"source": "request", "path": "/users", "field": "login"}) == \
        "twitch(GET /users -> login)"
    assert ccv.compile_custom_variable("twitch", {"lookup": "uptime", "field": "uptime"}) == "twitch(uptime -> uptime)"


@pytest.mark.parametrize(
    "kind,params,msg",
    [
        ("math", {"op": "^", "left": "1", "right": "2"}, "Math operator must be"),
        ("math", {"op": "+", "left": "1"}, "Math needs a value on both sides"),
        ("math", {"op": "random", "left": "", "right": "2"}, "Random needs a lowest"),
        ("if", {"op": "matches"}, "Unknown comparison"),
        ("age", {"style": "weeks", "date": "x"}, "Unknown way"),
        ("age", {"date": " "}, "Age needs a date"),
        ("twitch", {"source": "request"}, "Enter the Twitch API path"),
        ("twitch", {"source": "request", "path": "/users"}, "Choose which value to use from"),
        ("twitch", {}, "Choose what to look up"),
        ("twitch", {"lookup": "uptime"}, "Choose which value to use"),
        ("nope", {}, "Unknown custom variable kind"),
    ],
)
def test_compile_custom_variable_errors(kind, params, msg):
    with pytest.raises(ValueError) as err:
        ccv.compile_custom_variable(kind, params)
    assert msg in str(err.value)


def test_normalize_definition():
    legacy = ccv.normalize_definition("{custom_x} - Custom: math(1+1) ")
    assert legacy == {"kind": "expression", "params": {"expression": "math(1+1)"}, "expression": "math(1+1)",
                      "description": ""}
    assert ccv.normalize_definition("just text")["expression"] == "just text"
    d = ccv.normalize_definition({"kind": "weird", "expression": "e"})
    assert d["kind"] == "expression" and d["params"] == {"expression": "e"}
    compiled = ccv.normalize_definition({"kind": "math", "params": {"left": "1", "right": "2"}, "description": 5})
    assert compiled["expression"] == "math(1 + 2)" and compiled["description"] == "5"
    bad = ccv.normalize_definition({"kind": "math", "params": {}})
    assert bad["expression"] == ""
    assert ccv.normalize_definition(42) is None


def test_shown_truncates():
    assert ccv._shown("a  b\nc") == "a b c"
    assert ccv._shown("x" * 50, 10) == "x" * 9 + "…"


def test_math_side():
    ctx = {"n": "5", "t": "hello"}
    assert ccv._math_side("{n}", ctx, 0, "L") == "5"
    assert ccv._math_side("1 + 2", ctx, 0, "L") == "1 + 2"
    with pytest.raises(ValueError, match="L {nope} isn't a variable here"):
        ccv._math_side("{nope}", ctx, 0, "L")
    with pytest.raises(ValueError, match="L is “hello”, not a number"):
        ccv._math_side("{t}", ctx, 0, "L")
    with pytest.raises(ValueError, match="L is “empty”, not a number"):
        ccv._math_side("", ctx, 0, "L")


# --------------------------------------------------------------------------- evaluate definitions


def _ev(definition, ctx=None, strict=False):
    return ccv._evaluate_definition(definition, ctx or {}, 1, strict=strict)


def test_evaluate_math_modes(monkeypatch):
    assert _ev({"kind": "math", "params": {"left": "7", "op": "-", "right": "2"}}) == "5"
    assert _ev({"kind": "math", "params": {"left": "1", "op": "/", "right": "3", "decimals": "2"}}) == "0.33"
    monkeypatch.setattr(ccv.random, "randint", lambda a, b: b)
    assert _ev({"kind": "math", "params": {"left": "1", "op": "random", "right": "6"}}) == "6"
    assert _ev({"kind": "math", "params": {"left": "1", "op": "/", "right": "0"}}) == "0"
    with pytest.raises(ValueError, match="Can't divide by zero"):
        _ev({"kind": "math", "params": {"left": "1", "op": "/", "right": "0"}}, strict=True)
    with pytest.raises(ValueError, match="Power too large"):
        _ev({"kind": "math", "params": {"left": "2", "op": "**", "right": "500"}}, strict=True)
    with pytest.raises(ValueError, match="Lowest"):
        _ev({"kind": "math", "params": {"left": "x", "op": "random", "right": "2"}}, strict=True)
    with pytest.raises(ValueError, match="The second value"):
        _ev({"kind": "math", "params": {"left": "1", "op": "+", "right": "{t}"}}, {"t": "a"}, strict=True)


def test_evaluate_if_bad_operator_takes_else():
    d = {"kind": "if", "params": {"left": "1", "op": "??", "right": "1", "then": "T", "else": "E {x}"}}
    assert _ev(d, {"x": "!"}) == "E !"


def test_evaluate_age_styles():
    moment = datetime.now() - timedelta(days=800, hours=5)
    ctx = {"d": moment.isoformat()}
    base = {"kind": "age", "params": {"date": "{d}"}}
    assert _ev(base, ctx).startswith("2 years")
    for style, expected in (("days", "800"), ("hours", str(800 * 24 + 5)), ("years", "2")):
        assert _ev({"kind": "age", "params": {"date": "{d}", "style": style}}, ctx) == expected
    months = int(_ev({"kind": "age", "params": {"date": "{d}", "style": "months"}}, ctx))
    assert 25 <= months <= 27
    future = {"d": (datetime.now() + timedelta(days=5)).isoformat()}
    assert _ev({"kind": "age", "params": {"date": "{d}", "style": "days"}}, future) == "0"


def test_evaluate_age_errors():
    assert _ev({"kind": "age", "params": {"date": "{nope}"}}) == ""
    with pytest.raises(ValueError, match="Choose a date"):
        _ev({"kind": "age", "params": {"date": " "}}, strict=True)
    with pytest.raises(ValueError, match="isn't available here"):
        _ev({"kind": "age", "params": {"date": "{nope}"}}, strict=True)
    with pytest.raises(ValueError, match="isn't available here"):
        _ev({"kind": "age", "params": {"date": "{empty}"}}, {"empty": ""}, strict=True)
    with pytest.raises(ValueError, match="Couldn't read “junk” as a date"):
        _ev({"kind": "age", "params": {"date": "{v}"}}, {"v": "junk"}, strict=True)


def test_evaluate_expression_kind_uses_definition_expression():
    assert _ev({"kind": "expression", "params": {}, "expression": "math(2*2)"}) == "4"
    assert _ev({"kind": "expression", "params": {"expression": "x{a}"}}, {"a": 1}) == "x1"
    assert _ev({"kind": "expression"}) == ""


# --------------------------------------------------------------------------- twitch variables


def test_twitch_lookup_config():
    ctx = {"who": "bob", "id": "9"}
    req = ccv.twitch_lookup_config({"source": "request", "path": "/users", "params": {"id": "{id}", " ": "x"}}, ctx)
    assert req == {"id": "advanced", "method": "GET", "path": "/users", "params": {"id": "9"}}
    assert ccv.twitch_lookup_config({"lookup": "uptime"}, ctx) == {"id": "uptime"}
    txt = ccv.twitch_lookup_config({"lookup": "channel_info", "target": {"from": "text", "text": "@{who}"}}, ctx)
    assert txt["target"] == {"from": "text", "text": "@bob"}
    ev = ccv.twitch_lookup_config({"lookup": "follow_age", "target": {"from": "event_user"}}, ctx)
    assert ev["target"] == {"from": "event_user"}
    other = ccv.twitch_lookup_config({"lookup": "follow_age", "target": {"from": "input"}, "period": "week"}, ctx)
    assert other == {"id": "follow_age", "target": {"from": "chatter"}, "period": "week"}
    assert "target" not in ccv.twitch_lookup_config({"lookup": "x", "target": "bad"}, ctx)


def test_pick_field():
    vars_ = {"a": 1, "api_b": 2, "api_c_d": 3}
    assert ccv._pick_field(vars_, "{a}") == 1
    assert ccv._pick_field(vars_, "b") == 2
    assert ccv._pick_field(vars_, "c.d") == 3
    assert ccv._pick_field(vars_, "zz") is None


def test_evaluate_twitch_preview_uses_examples(monkeypatch):
    def no_network(*_a, **_k):
        raise AssertionError("network in preview")

    monkeypatch.setattr(chatbot_lookups, "run_lookup_blocking", no_network)
    with ccv.preview_mode():
        assert ccv._evaluate_twitch({"lookup": "uptime", "field": "uptime"}, {}, 1)
        assert ccv._evaluate_twitch({"source": "request", "path": "/x", "field": "thing"}, {}, 1) == "(example)"
        assert ccv._evaluate_twitch({"lookup": "uptime", "field": "missing"}, {}, 1) == ""


def test_evaluate_twitch_live_cache_expiry_and_errors(monkeypatch):
    calls = []
    result = {"lookup_ok": True, "uptime": "1 hour"}

    def fake(config, context, **kw):
        calls.append((config, kw))
        return dict(result)

    clock = {"t": 100.0}
    monkeypatch.setattr(chatbot_lookups, "run_lookup_blocking", fake)
    monkeypatch.setattr(ccv.time, "monotonic", lambda: clock["t"])
    params = {"lookup": "uptime", "field": "uptime"}
    assert ccv._evaluate_twitch(params, {"username": "a"}, 1) == "1 hour"
    assert calls[0][1] == {"kind": "command", "timeout": ccv._TWITCH_TIMEOUT_SECONDS}
    assert ccv._evaluate_twitch(params, {"username": "a"}, 1) == "1 hour"
    assert len(calls) == 1
    clock["t"] += ccv._TWITCH_CACHE_SECONDS + 1
    result["uptime"] = None
    assert ccv._evaluate_twitch(params, {"username": "a"}, 1) == ""
    assert len(calls) == 2
    # failure: strict raises, otherwise fallback text with lookup vars
    result.clear()
    result.update({"lookup_ok": False, "lookup_error": "", "lookup_reason": "error"})
    with pytest.raises(ValueError, match="Twitch didn't answer"):
        ccv._evaluate_twitch({**params, "fallback": "x"}, {"username": "b"}, 1, strict=True)
    assert ccv._evaluate_twitch({**params, "fallback": "no ({lookup_reason})"}, {"username": "c"}, 1) == "no (error)"


def test_evaluate_twitch_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(chatbot_lookups, "run_lookup_blocking", lambda *a, **k: {"lookup_ok": True, "v": 1})
    for i in range(201):
        ccv._twitch_cache[f"k{i}"] = (0.0, "")
    ccv._evaluate_twitch({"lookup": "uptime", "field": "v"}, {"user_id": "1"}, 1)
    assert len(ccv._twitch_cache) == 1


def test_evaluate_definition_twitch_kind(monkeypatch):
    monkeypatch.setattr(chatbot_lookups, "run_lookup_blocking", lambda *a, **k: {"lookup_ok": True, "x": "v"})
    assert _ev({"kind": "twitch", "params": {"lookup": "uptime", "field": "x"}}, {"userid": "1"}) == "v"


# --------------------------------------------------------------------------- storage


def test_load_custom_variables_caches(db):
    db.stored = {"data": {"custom_a": "x"}}
    first = ccv.load_custom_variables()
    assert first == {"custom_a": "x"}
    first["mutated"] = 1
    db.stored = {"data": {"custom_b": "y"}}
    assert ccv.load_custom_variables() == {"custom_a": "x"}
    ccv.invalidate_custom_variable_cache()
    assert ccv.load_custom_variables() == {"custom_b": "y"}


def test_load_custom_variables_non_dict_data(db):
    db.stored = {"data": ["x"]}
    assert ccv.load_custom_variables() == {}
    ccv.invalidate_custom_variable_cache()
    db.stored = None
    assert ccv.load_custom_variables() == {}


def test_load_custom_variables_failed_read_notifies_and_backs_off(db, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(ccv.time, "monotonic", lambda: clock["t"])
    db.ok = False
    assert ccv.load_custom_variables() == {}
    msg, kw = db.notices[-1]
    assert kw["dedupe_key"] == "chatbot:custom_vars_load" and kw["type"] == "warning"
    # within the retry window: no new read
    db.ok = True
    db.stored = {"data": {"custom_a": "x"}}
    clock["t"] += 5
    assert ccv.load_custom_variables() == {}
    clock["t"] += ccv._FAILED_RETRY_SECONDS
    assert ccv.load_custom_variables() == {"custom_a": "x"}


def test_load_custom_variables_exception_and_notify_failure(db, monkeypatch):
    db.raises = True

    def boom(*_a, **_k):
        raise RuntimeError("no ui")

    monkeypatch.setattr(notification_engine, "notify", boom)
    assert ccv.load_custom_variables() == {}
    assert ccv._cache_failed_at > 0


def test_load_for_write(db):
    db.stored = {"data": {"a": 1}}
    assert ccv._load_custom_variables_for_write() == {"a": 1}
    db.stored = {}
    assert ccv._load_custom_variables_for_write() == {}
    db.ok = False
    assert ccv._load_custom_variables_for_write() is None
    db.ok = True
    db.raises = True
    assert ccv._load_custom_variables_for_write() is None


def test_save_custom_variables(db):
    assert ccv.save_custom_variables({"custom_a": "x"}) is True
    assert db.writes[-1]["data"] == {"custom_a": "x"} and db.writes[-1]["version"] == "1.0"
    db.stored = {}
    assert ccv.load_custom_variables() == {"custom_a": "x"}  # cache filled by the save
    db.write_result = False
    assert ccv.save_custom_variables({"custom_b": "y"}) is False
    assert ccv.load_custom_variables() == {"custom_a": "x"}
    db.write_raises = True
    assert ccv.save_custom_variables({}) is False


def test_add_and_remove_custom_variable(db):
    assert ccv.add_custom_variable(" score ", " math(1+1) ") is True
    assert db.stored["data"]["custom_score"] == "{custom_score} - Custom: math(1+1)"
    assert ccv.add_custom_variable("custom_x", "1") is True
    assert "custom_x" in db.stored["data"]
    assert ccv.remove_custom_variable("custom_missing") is True
    assert ccv.remove_custom_variable("custom_x") is True
    assert "custom_x" not in db.stored["data"]
    db.ok = False
    assert ccv.add_custom_variable("y", "1") is False
    assert ccv.remove_custom_variable("custom_score") is False


def test_add_and_remove_custom_variable_errors(db, monkeypatch):
    assert ccv.add_custom_variable(None, "x") is False  # .strip() on None

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ccv, "_load_custom_variables_for_write", boom)
    assert ccv.remove_custom_variable("custom_a") is False


def test_load_custom_definitions(monkeypatch):
    monkeypatch.setattr(ccv, "load_custom_variables", lambda: {"custom_a": "x", "custom_b": 5,
                                                               "custom_c": {"kind": "text", "params": {"template": "t"}}})
    defs = ccv.load_custom_definitions()
    assert set(defs) == {"custom_a", "custom_c"}
    assert defs["custom_c"]["expression"] == "t"


def test_validate_custom_definition():
    assert ccv.validate_custom_definition("x") == "Definition must be a dict"
    assert ccv.validate_custom_definition({"kind": "zzz"}) == "Unknown kind 'zzz'"
    assert ccv.validate_custom_definition({"kind": "age", "params": {}}) == "Age needs a date"
    assert ccv.validate_custom_definition({"kind": "text"}) is None


def test_save_and_delete_custom_definition(db):
    db.stored = {"data": {"custom_keep": "x"}}
    assert ccv.save_custom_definition("bad name!", {"kind": "text"}) is False
    assert ccv.save_custom_definition("ok", {"kind": "age", "params": {}}) is False
    assert ccv.save_custom_definition("Hi There", {"kind": "text", "params": {"template": "hey"},
                                                   "description": "Greeting"}) is True
    assert db.stored["data"]["custom_hi_there"] == {"kind": "text", "params": {"template": "hey"},
                                                    "expression": "hey", "description": "Greeting"}
    assert db.stored["data"]["custom_keep"] == "x"
    assert ccv.delete_custom_definition("!!") is False
    assert ccv.delete_custom_definition("hi there") is True
    assert "custom_hi_there" not in db.stored["data"]
    db.ok = False
    assert ccv.save_custom_definition("z", {"kind": "text"}) is False


# --------------------------------------------------------------------------- evaluation


def test_get_live_basic_variables(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.twitch", None)
    basics = ccv.get_live_basic_variables()
    assert basics["username"] == "PreviewUser" and basics["source"] == "Twitch"
    assert len(basics["date"]) == 10 and basics["time"]
    api = SimpleNamespace(is_connected=True, user=SimpleNamespace(display_name="Streamer"))
    monkeypatch.setitem(sys.modules, "modules.twitch", SimpleNamespace(twitch_api=api))
    assert ccv.get_live_basic_variables()["username"] == "Streamer"
    api.user = SimpleNamespace(display_name="")
    assert ccv.get_live_basic_variables()["username"] == "PreviewUser"
    api.is_connected = False
    assert ccv.get_live_basic_variables()["username"] == "PreviewUser"


def test_get_live_basic_variables_survives_errors(monkeypatch):
    class Weird:
        @property
        def twitch_api(self):
            raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "modules.twitch", Weird())
    assert ccv.get_live_basic_variables()["username"] == "PreviewUser"


def test_key_for():
    assert ccv._key_for("{x}") == "custom_x"
    assert ccv._key_for("custom_y") == "custom_y"
    assert ccv._key_for(None) == "custom_"


def test_evaluate_custom_variable(monkeypatch):
    store = {"custom_t": {"kind": "text", "params": {"template": "hi {username}"}},
             "custom_bad": {"kind": "math", "params": {"left": "1", "right": "2", "decimals": "x"}}}
    monkeypatch.setattr(ccv, "load_custom_variables", lambda: dict(store))
    assert ccv.evaluate_custom_variable("t", {"username": "Ann"}) == "hi Ann"
    assert ccv.evaluate_custom_variable("{custom_t}").startswith("hi ")
    assert ccv.evaluate_custom_variable("missing") is None
    assert ccv.evaluate_custom_variable("t", {}, _depth=ccv._MAX_DEPTH + 1) is None
    assert ccv.evaluate_custom_variable("bad", {}) == ""
    assert ccv.evaluate_custom_variable_expression("t", {"username": "Zed"}) == "hi Zed"


def test_preview_context_and_failures(monkeypatch):
    ctx = ccv.preview_context()
    assert ctx["username"] == "TestViewer"
    assert "followage" in ctx and "lookup_ok" not in ctx
    from modules import chatbot_variables

    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(chatbot_variables, "example_context", boom)
    monkeypatch.setattr(chatbot_lookups, "example_variables", boom)
    assert ccv.preview_context() == {}


def test_preview_custom_variable(monkeypatch):
    assert ccv.preview_custom_variable(5) == "Error: invalid definition"
    assert ccv.preview_custom_variable({"kind": "age", "params": {}}) == "Error: Age needs a date"
    assert ccv.preview_custom_variable("{custom_x} - Custom: math(2+3)", {}) == "5"
    assert ccv.preview_custom_variable({"kind": "text", "params": {"template": "{username}"}}) == "TestViewer"
    monkeypatch.setattr(ccv, "_evaluate_definition", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    assert ccv.preview_custom_variable({"kind": "text"}, {}) == "Error: bad"


# --------------------------------------------------------------------------- legacy helpers


def test_legacy_math_and_compare():
    assert ccv._evaluate_math("2, *, 3") == 6
    assert ccv._evaluate_math("1 + 1") == 2
    assert ccv._evaluate_math("1, 2") == 0  # "1, 2" is not valid math
    assert ccv._evaluate_math(None) == 0
    assert ccv._evaluate_compare("5, >, 3") is True
    assert ccv._evaluate_compare("5, ~, 3") is False
    assert ccv._evaluate_compare("5") is False


def test_validate_expression_variables(monkeypatch):
    monkeypatch.setattr(ccv, "load_custom_variables", lambda: {"custom_a": "x"})
    text = ("{username} {custom_a} {a} {stats.x} {youtube.y} {quote.1.text} {time:%H} {count} "
            "{command_word_3} {nope} {other:fmt}")
    assert ccv.validate_expression_variables(text) == ["nope", "other:fmt"]
    assert ccv.validate_expression_variables(None) == []

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ccv, "load_custom_variables", boom)
    assert ccv.validate_expression_variables("{x}") == []

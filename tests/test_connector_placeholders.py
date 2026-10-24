# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Connector placeholders: unknown tokens kept, always-vars, formats, shell / JSON quoting."""

from __future__ import annotations

import datetime as dt
import json
import shlex
import subprocess
import sys

import pytest

from modules.connector_placeholders import (
    build_connector_placeholder_context,
    portable_strftime,
    substitute_connector_placeholders as sub,
    substitute_json_placeholders,
    substitute_shell_placeholders,
    windows_argv_quote,
    windows_cmd_quote,
)


def test_unknown_tokens_and_json_are_untouched():
    ctx = {"username": "Bob", "nothing": None, "user": {"name": "Ann"}}
    assert sub('{"a":1}', ctx) == '{"a":1}'
    assert sub("${HOME} {UserName} {nope.deeper}", ctx) == "${HOME} {UserName} {nope.deeper}"
    assert sub("{username}|{nothing}|{user.name}", ctx) == "Bob||Ann"
    assert sub("{{username}} {{ username }} {{unknown}}", ctx) == "Bob Bob {{unknown}}"
    assert sub('{"amount": {username}}', ctx) == '{"amount": Bob}'
    assert sub("${username}", ctx) == "$Bob"


def test_special_tokens_still_work():
    ctx = {"message": "one two three", "message_after_conditions": "two three"}
    assert sub("{message.word.2}|{message.word.9}", ctx) == "two|"
    assert sub("{message_after_word.1} {message_after_from_word.1}", ctx) == "two two three"
    value = int(sub("{random_damage.1.5}", ctx))
    assert 1 <= value <= 5


def test_substituted_values_are_not_rescanned():
    assert sub("{a}", {"a": "{b}", "b": "x"}) == "{b}"


def test_always_variables_in_context():
    ctx = build_connector_placeholder_context(
        {"username": "Bob"},
        {"connector_name": "Greeter", "trigger_count": 3, "triggered_at": 0.0},
    )
    now = dt.datetime.now()
    assert sub("{connector_name} {trigger_count}", ctx) == "Greeter 3"
    assert sub("{date}", ctx) == now.strftime("%Y-%m-%d")
    assert len(sub("{time}", ctx)) == 5 and ":" in sub("{time}", ctx)
    assert sub("{datetime}", ctx).startswith(now.strftime("%Y-%m-%d "))
    # The event's own key wins over the clock variables.
    ctx2 = build_connector_placeholder_context({"date": "custom"}, {})
    assert sub("{date}", ctx2) == "custom"


def test_date_formats():
    moment = dt.datetime(2026, 3, 4, 9, 5, 7)
    ctx = {
        "when": moment,
        "epoch": moment.timestamp(),
        "epoch_ms": int(moment.timestamp() * 1000),
        "iso": "2026-03-04T09:05:07",
        "isoz": "2026-03-04T09:05:07Z",
        "word": "not a date",
        "amount": 1234.5,
    }
    assert sub("{when:%H:%M:%S}", ctx) == "09:05:07"
    assert sub("{epoch:%Y-%m-%d %H:%M}", ctx) == "2026-03-04 09:05"
    assert sub("{epoch_ms:%Y}", ctx) == "2026"
    assert sub("{iso:%b %-d, %Y}", ctx) == "Mar 4, 2026"
    local = dt.datetime(2026, 3, 4, 9, 5, 7, tzinfo=dt.timezone.utc).astimezone()
    assert sub("{isoz:%H:%M}", ctx) == local.strftime("%H:%M")
    assert sub("{word:%Y}", ctx) == "not a date"
    assert sub("{missing:%Y}", ctx) == "{missing:%Y}"
    assert sub("{{when:%-I:%M %p}}", ctx) == "9:05 AM"
    assert sub("{when:%A, %B %-d}", ctx) == "Wednesday, March 4"
    assert sub("{amount:,}", ctx) == "1,234.5"
    assert sub("{amount:.2f}", ctx) == "1234.50"
    assert sub("{word:,}", ctx) == "not a date"


def test_portable_no_pad_flags():
    moment = dt.datetime(2026, 1, 2, 3, 4, 5)
    assert portable_strftime(moment, "%-d/%-m %-H:%-M:%-S %-I") == "2/1 3:4:5 3"
    assert portable_strftime(moment, "%#d %d 100%%") == "2 02 100%"


def test_shell_quoting_posix_runs_safely(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX shell")
    marker = tmp_path / "pwned"
    nasty = f"a \"b\" $(touch {marker}) `touch {marker}` it's; touch {marker} & | \\"
    ctx = {"m": nasty}
    for template in ("printf %s {m}", 'printf %s "{m}"', "printf %s '{m}'"):
        command = substitute_shell_placeholders(template, ctx, windows=False)
        out = subprocess.run(command, shell=True, capture_output=True, text=True).stdout
        assert out == nasty, template
    assert not marker.exists()
    # literal text is not quoted
    assert substitute_shell_placeholders("echo hi > out.txt", ctx, windows=False) == (
        "echo hi > out.txt"
    )
    assert substitute_shell_placeholders("echo {m}", ctx, windows=False) == (
        "echo " + shlex.quote(nasty)
    )


def test_windows_quoting_strings():
    assert windows_argv_quote("plain") == '"plain"'
    assert windows_argv_quote('say "hi"') == '"say \\"hi\\""'
    assert windows_argv_quote("C:\\dir\\") == '"C:\\dir\\\\"'
    assert windows_cmd_quote("a & b") == '^"a ^& b^"'
    assert windows_cmd_quote("%PATH%") == '^"^%PATH^%^"'
    assert windows_cmd_quote('x"|y') == '^"x\\^"^|y^"'
    assert windows_cmd_quote("line1\nline2") == '^"line1 line2^"'
    ctx = {"m": 'bad " & %X%'}
    assert substitute_shell_placeholders("echo {m}", ctx, windows=True) == (
        "echo " + windows_cmd_quote(ctx["m"])
    )
    # inside user-typed quotes: quote and percent removed
    assert substitute_shell_placeholders('echo "hi {m}"', ctx, windows=True) == (
        'echo "hi bad  & X"'
    )


def test_json_substitution_keeps_json_valid():
    ctx = {"amount": 5, "name": 'Bob "B"', "flag": True, "obj": {"a": [1, 2]}, "num": "12"}
    text = '{"amount": {amount}, "msg": "Hi {name}!", "who": {name}, "f": {flag}, "o": {obj}, "n": {num}}'
    data = json.loads(substitute_json_placeholders(text, ctx))
    assert data == {
        "amount": 5,
        "msg": 'Hi Bob "B"!',
        "who": 'Bob "B"',
        "f": True,
        "o": {"a": [1, 2]},
        "n": 12,
    }
    assert substitute_json_placeholders('{"x": "{unknown}"}', ctx) == '{"x": "{unknown}"}'

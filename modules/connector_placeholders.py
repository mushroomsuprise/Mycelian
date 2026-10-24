# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Connector placeholder substitution.

Syntax
------
* ``{key}`` -- value of ``key`` from the context (case-sensitive). Dotted paths
  walk dicts / lists: ``{hooks.ff7.party.0.name}``.
* ``{{key}}`` / ``{{ key }}`` -- legacy spelling of ``{key}``.
* ``{key:FORMAT}`` -- formatted value:

  - FORMAT containing a strftime directive (``%H``, ``%-d`` ...) formats a date:
    the value may be a ``datetime``/``date``, an epoch number (seconds, or
    milliseconds when larger than 1e11; local time) or an ISO-8601 string
    (a trailing ``Z`` is accepted). Aware datetimes are converted to local time.
    ``%-d``/``%-m``/``%-H``/``%-I``/``%-M``/``%-S``/``%-j`` (and the Windows
    ``%#d`` spelling) drop the leading zero on every platform.
  - Otherwise FORMAT is a Python format spec applied to numeric values
    (``{amount:,}`` -> ``1,234``; ``{amount:.2f}`` -> ``12.50``).
  - A value that cannot be formatted is substituted plainly.

Unknown keys are left untouched, braces and all (``{UserName}``, ``${HOME}``,
``{"a": 1}``), so JSON and shell text survive. A known key whose value is
``None`` becomes an empty string. Special computed tokens (``message.word.N``,
``random_damage.LO.HI`` ...) are always known.

Context is a flat merge of dicts (event_data, trigger_data, hooks) built by
:func:`build_connector_placeholder_context`, which also adds the always-available
variables ``time``, ``date`` and ``datetime`` (local now; they render as
``HH:MM``, ``YYYY-MM-DD`` and ``YYYY-MM-DD HH:MM`` and accept a date FORMAT).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import random
import re
import shlex
import sys
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class _TimeValue(_dt.datetime):
    """A datetime that prints as local ``HH:MM`` (``{time}``)."""

    def __str__(self) -> str:
        return self.strftime("%H:%M")


class _DateValue(_dt.datetime):
    """A datetime that prints as ``YYYY-MM-DD`` (``{date}``)."""

    def __str__(self) -> str:
        return self.strftime("%Y-%m-%d")


class _DateTimeValue(_dt.datetime):
    """A datetime that prints as ``YYYY-MM-DD HH:MM`` (``{datetime}``)."""

    def __str__(self) -> str:
        return self.strftime("%Y-%m-%d %H:%M")


def clock_variables(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """``time`` / ``date`` / ``datetime`` values for *now* (local time)."""
    now = now or _dt.datetime.now()
    args = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    return {
        "time": _TimeValue(*args),
        "date": _DateValue(*args),
        "datetime": _DateTimeValue(*args),
    }


def build_connector_placeholder_context(
    event_data: Dict[str, Any],
    trigger_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge event + trigger data, attach live ``hooks.ff7``, and map ``action_results`` to
    ``{action1.field}`` … (slot key ``\"1\"`` -> ``action1`` in the context). Internal keys
    ``action_results`` and ``action_index`` are not exposed as placeholders.

    Always-available keys: ``connector_name`` / ``trigger_count`` / ``triggered_at`` /
    ``trigger_type`` / ``message_after_conditions`` come from *trigger_data*
    (see ``Connector.process_event``); ``time`` / ``date`` / ``datetime`` (local now)
    are added unless the event already carries a key of that name."""
    td = dict(trigger_data or {})
    ar = td.pop("action_results", None)
    td.pop("action_index", None)
    ctx: Dict[str, Any] = {**(event_data or {}), **td}
    for key, value in clock_variables().items():
        ctx.setdefault(key, value)
    if isinstance(ar, dict):
        for k, v in ar.items():
            if isinstance(v, dict):
                ctx[f"action{k}"] = v
    try:
        from .game_hooks_service import game_hooks_service

        from .game_hooks.registry import registered_hook_ids

        hooks_ctx: Dict[str, Any] = {}
        for hook_id in registered_hook_ids():
            info = game_hooks_service.get_hook_ui_snapshot(hook_id)
            hsnap = info.get(hook_id)
            if isinstance(hsnap, dict):
                hooks_ctx[hook_id] = hsnap
        if hooks_ctx:
            ctx["hooks"] = hooks_ctx
    except Exception:
        pass
    return ctx


_KEY = r"[\w.\-]+"
_LEGACY_DOUBLE_BRACE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
# One pass over both spellings so substituted values are never re-scanned.
_TOKEN = re.compile(
    r"\{\{\s*(?P<dkey>" + _KEY + r")\s*(?::(?P<dfmt>[^{}]*?))?\s*\}\}"
    r"|\{(?P<key>" + _KEY + r")(?::(?P<fmt>[^{}]*))?\}"
)
_DATE_DIRECTIVE = re.compile(r"%[-#]?[A-Za-z]")
_STRFTIME_TOKEN = re.compile(r"%([-#]?)([A-Za-z%])")
_NO_PAD_DIRECTIVES = frozenset("dmHIMSjUWy")


def normalize_legacy_double_brace(text: str) -> str:
    """Convert `{{key}}` to `{key}` (spaces dropped from the key, kept in a format)."""

    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        if not inner:
            return m.group(0)
        key, sep, fmt = inner.partition(":")
        key = key.replace(" ", "")
        if not key:
            return m.group(0)
        return "{" + key + sep + fmt + "}"

    return _LEGACY_DOUBLE_BRACE.sub(repl, text)


def _ctx_lookup_found(cur: Any, path: str) -> Tuple[bool, Any]:
    """Walk *path*; return (found, value). ``found`` is False for a missing key."""
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return False, None
        else:
            return False, None
    return True, cur


def _ctx_lookup(cur: Any, path: str) -> Any:
    return _ctx_lookup_found(cur, path)[1]


def portable_strftime(value: _dt.date, fmt: str) -> str:
    """``strftime`` with ``%-X`` / ``%#X`` (no leading zero) on every platform.

    Each directive is rendered on its own, so literal text never reaches the
    platform ``strftime``.
    """
    out = []
    pos = 0
    for m in _STRFTIME_TOKEN.finditer(fmt):
        out.append(fmt[pos : m.start()])
        flag, letter = m.group(1), m.group(2)
        if letter == "%":
            piece = "%"
        else:
            try:
                piece = value.strftime("%" + letter)
            except ValueError:
                piece = m.group(0)
            if flag and letter in _NO_PAD_DIRECTIVES:
                piece = piece.lstrip("0") or "0"
            elif flag and letter in "e":
                piece = piece.strip()
        out.append(piece)
        pos = m.end()
    out.append(fmt[pos:])
    return "".join(out)


def coerce_datetime(value: Any) -> Optional[_dt.datetime]:
    """Interpret *value* as a local datetime, or None.

    ``datetime`` (aware -> local), ``date``, epoch seconds/milliseconds (int, float
    or numeric text) and ISO-8601 text (trailing ``Z`` allowed).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, _dt.datetime):
        return value.astimezone() if value.tzinfo is not None else value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)):
        return _epoch_to_local(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return _epoch_to_local(float(text))
        except ValueError:
            pass
        iso = text[:-1] + "+00:00" if text[-1] in "Zz" else text
        try:
            parsed = _dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
        return parsed.astimezone() if parsed.tzinfo is not None else parsed
    return None


def _epoch_to_local(seconds: float) -> Optional[_dt.datetime]:
    if seconds != seconds:
        return None
    if abs(seconds) > 1e11:
        seconds /= 1000.0
    try:
        return _dt.datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_placeholder_value(value: Any, fmt: str) -> str:
    """Apply a ``{key:FORMAT}`` suffix; fall back to the plain value."""
    if value is None:
        return ""
    if fmt and _DATE_DIRECTIVE.search(fmt):
        moment = coerce_datetime(value)
        if moment is not None:
            return portable_strftime(moment, fmt)
        return _stringify(value)
    if fmt and not isinstance(value, bool):
        number: Any = None
        if isinstance(value, (int, float)):
            number = value
        elif isinstance(value, str):
            text = value.strip()
            try:
                number = int(text)
            except ValueError:
                try:
                    number = float(text)
                except ValueError:
                    number = None
        if number is not None:
            try:
                return format(number, fmt)
            except (TypeError, ValueError):
                if isinstance(number, int):
                    try:
                        return format(float(number), fmt)
                    except (TypeError, ValueError):
                        pass
    return _stringify(value)


def _message_word(ctx: Dict[str, Any], index_one_based: int) -> str:
    if index_one_based < 1:
        return ""
    msg = ctx.get("message")
    words = str(msg or "").split()
    if index_one_based > len(words):
        return ""
    return words[index_one_based - 1]


def _message_after_conditions_word(ctx: Dict[str, Any], index_one_based: int) -> str:
    """Nth word (1-based) of ``message_after_conditions`` (post-trigger-strip text)."""
    if index_one_based < 1:
        return ""
    msg = ctx.get("message_after_conditions")
    words = str(msg or "").split()
    if index_one_based > len(words):
        return ""
    return words[index_one_based - 1]


def _message_after_conditions_from_word(
    ctx: Dict[str, Any], index_one_based: int
) -> str:
    """Words from *N* through end of ``message_after_conditions``, joined with spaces (1-based *N*)."""
    if index_one_based < 1:
        return ""
    msg = ctx.get("message_after_conditions")
    words = str(msg or "").split()
    if index_one_based > len(words):
        return ""
    return " ".join(words[index_one_based - 1 :])


def _try_message_word_token(token: str, ctx: Dict[str, Any]) -> Optional[str]:
    m = re.fullmatch(r"message\.word\.(\d+)", token)
    if not m:
        return None
    return _message_word(ctx, int(m.group(1)))


def _try_message_after_word_token(token: str, ctx: Dict[str, Any]) -> Optional[str]:
    m = re.fullmatch(r"message_after_word\.(\d+)", token)
    if not m:
        m = re.fullmatch(r"message_after_conditions\.word\.(\d+)", token)
    if not m:
        return None
    return _message_after_conditions_word(ctx, int(m.group(1)))


def _try_message_after_from_word_token(token: str, ctx: Dict[str, Any]) -> Optional[str]:
    m = re.fullmatch(r"message_after_from_word\.(\d+)", token)
    if not m:
        m = re.fullmatch(r"message_after_conditions\.from_word\.(\d+)", token)
    if not m:
        return None
    return _message_after_conditions_from_word(ctx, int(m.group(1)))


def _party_names_from_ctx(ctx: Dict[str, Any]) -> list[str]:
    party = _ctx_lookup(ctx, "hooks.ff7.party")
    if not isinstance(party, list):
        return []
    names: list[str] = []
    for row in party:
        if not isinstance(row, dict):
            continue
        if row.get("slot_empty"):
            continue
        n = str(row.get("name", "") or "").strip()
        if n and n != "?":
            names.append(n)
    return names


def _enemy_names_from_ctx(ctx: Dict[str, Any]) -> list[str]:
    enemies = _ctx_lookup(ctx, "hooks.ff7.enemies")
    if not isinstance(enemies, list):
        return []
    names: list[str] = []
    for row in enemies:
        if not isinstance(row, dict):
            continue
        n = str(row.get("name", "") or "").strip()
        if n:
            names.append(n)
    return names


def _resolve_special_token(token: str, ctx: Dict[str, Any]) -> Optional[str]:
    """Return replacement string if token is special; None to fall through to ctx lookup."""
    mw = _try_message_word_token(token, ctx)
    if mw is not None:
        return mw

    maw = _try_message_after_word_token(token, ctx)
    if maw is not None:
        return maw

    ma_tail = _try_message_after_from_word_token(token, ctx)
    if ma_tail is not None:
        return ma_tail

    if token == "random_character":
        names = _party_names_from_ctx(ctx)
        if not names:
            logger.debug("random_character: no party names in hooks context")
            return ""
        return random.choice(names)

    if token == "random_enemy":
        names = _enemy_names_from_ctx(ctx)
        if not names:
            logger.debug("random_enemy: no enemy names in hooks context")
            return ""
        return random.choice(names)

    if token in ("random_weapon", "random_armor", "random_accessory"):
        kind = token.replace("random_", "")
        try:
            from .game_hooks import ff7_hook as fh

            fh._load_ff7_gear_layout_assets()
            pool: list[str] = []
            if kind == "weapon":
                pool = [n for n in fh._WEAPON_NAMES_EN.values() if n and str(n).strip()]
            elif kind == "armor":
                pool = [n for n in fh._ARMOR_NAMES_EN.values() if n and str(n).strip()]
            else:
                pool = [
                    n for n in fh._ACCESSORY_NAMES_EN.values() if n and str(n).strip()
                ]
            if not pool:
                return ""
            return random.choice(pool)
        except Exception as e:
            logger.warning("random_%s: %s", kind, e)
            return ""

    mrd = re.fullmatch(r"random_damage\.(\d+)\.(\d+)", token)
    if mrd:
        lo, hi = int(mrd.group(1)), int(mrd.group(2))
        lo = max(1, min(9999, lo))
        hi = max(1, min(9999, hi))
        if lo > hi:
            lo, hi = hi, lo
        return str(random.randint(lo, hi))

    return None


def resolve_placeholder(key: str, ctx: Dict[str, Any], fmt: str = "") -> Optional[str]:
    """Replacement text for one token, or None when *key* is unknown."""
    spec = _resolve_special_token(key, ctx)
    if spec is not None:
        return format_placeholder_value(spec, fmt) if fmt else spec
    found, value = _ctx_lookup_found(ctx, key)
    if not found:
        return None
    if fmt:
        return format_placeholder_value(value, fmt)
    return _stringify(value)


def substitute_connector_placeholders(text: str, ctx: Dict[str, Any]) -> str:
    """Replace `{token}` / `{token:FORMAT}` segments using ctx and built-in special tokens.

    Unknown tokens are left exactly as written.
    """
    if not text or "{" not in text:
        return text

    def repl(m: re.Match) -> str:
        if m.group("dkey") is not None:
            key, fmt = m.group("dkey"), m.group("dfmt") or ""
        else:
            key, fmt = m.group("key"), m.group("fmt") or ""
        out = resolve_placeholder(key, ctx, fmt)
        return m.group(0) if out is None else out

    return _TOKEN.sub(repl, text)


def _token_key_fmt(m: re.Match) -> Tuple[str, str]:
    if m.group("dkey") is not None:
        return m.group("dkey"), m.group("dfmt") or ""
    return m.group("key"), m.group("fmt") or ""


_JSON_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _json_bare_value(text: str) -> str:
    """JSON for a value written outside quotes: numbers / true / false / null /
    JSON objects stay bare, anything else becomes a JSON string."""
    stripped = text.strip()
    if _JSON_NUMBER.fullmatch(stripped) or stripped in ("true", "false", "null"):
        return stripped
    if stripped[:1] in ("{", "["):
        try:
            json.loads(stripped)
            return stripped
        except ValueError:
            pass
    return json.dumps(text, ensure_ascii=False)


def substitute_json_placeholders(text: str, ctx: Dict[str, Any]) -> str:
    """Substitute placeholders into JSON source text so the result stays valid JSON.

    Inside a JSON string (``"Hi {username}"``) values are escaped; outside one
    (``{"amount": {amount}}``) numbers / booleans stay bare and other text is
    quoted. Unknown tokens are left as written.
    """
    if not text or "{" not in text:
        return text
    out = []
    pos = 0
    in_string = False
    for m in _TOKEN.finditer(text):
        literal = text[pos : m.start()]
        out.append(literal)
        in_string = _json_scan(literal, in_string)
        key, fmt = _token_key_fmt(m)
        value = resolve_placeholder(key, ctx, fmt)
        if value is None:
            out.append(m.group(0))
        elif in_string:
            out.append(json.dumps(value, ensure_ascii=False)[1:-1])
        else:
            out.append(_json_bare_value(value))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _json_scan(segment: str, in_string: bool) -> bool:
    escaped = False
    for ch in segment:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
    return in_string


_CMD_META = re.compile(r'([()%!^"<>&|])')


def windows_argv_quote(value: str) -> str:
    """Quote one argument for ``CommandLineToArgvW`` / the MSVC runtime."""
    out = ['"']
    backslashes = 0
    for ch in value:
        if ch == "\\":
            backslashes += 1
            continue
        if ch == '"':
            out.append("\\" * (2 * backslashes + 1) + '"')
        else:
            out.append("\\" * backslashes + ch)
        backslashes = 0
    out.append("\\" * (2 * backslashes) + '"')
    return "".join(out)


def windows_cmd_quote(value: str) -> str:
    """Quote one argument for a ``cmd.exe /c`` command line (``shell=True`` on Windows).

    The value is first quoted for the program's argv parser, then every cmd
    metacharacter (``( ) % ! ^ " < > & |``) is caret-escaped, so cmd passes the
    quoted argument through unchanged and never runs ``&`` / ``|`` / redirects
    or expands ``%VAR%``. Line breaks are replaced by spaces (cmd ends the
    command at a newline).
    """
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return _CMD_META.sub(r"^\1", windows_argv_quote(text))


def _windows_in_quotes_safe(value: str) -> str:
    """Text placed inside a user-typed "..." on Windows: drop what could end the
    quote or expand a variable (``"`` ``%`` line breaks)."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.replace('"', "").replace("%", "")


def substitute_shell_placeholders(
    text: str, ctx: Dict[str, Any], windows: Optional[bool] = None
) -> str:
    """Build a shell command, quoting every substituted value; literal text is untouched.

    POSIX (``/bin/sh``): a value outside quotes is ``shlex.quote``-d; inside
    ``"..."`` it is backslash-escaped (``\\ " $ ` ``); inside ``'...'`` each ``'``
    becomes ``'\\''``. So ``echo {message}``, ``echo "{message}"`` and
    ``echo 'Hi {username}'`` are all safe.

    Windows (``cmd.exe /c``): outside quotes the value goes through
    :func:`windows_cmd_quote`; inside a user-typed ``"..."`` the characters
    ``"`` and ``%`` (and line breaks) are removed from the value, because cmd
    offers no escape there.
    """
    if not text or "{" not in text:
        return text
    if windows is None:
        windows = sys.platform == "win32"
    out = []
    pos = 0
    state = None  # None | "'" | '"'
    for m in _TOKEN.finditer(text):
        literal = text[pos : m.start()]
        out.append(literal)
        state = _shell_scan(literal, state, windows)
        key, fmt = _token_key_fmt(m)
        value = resolve_placeholder(key, ctx, fmt)
        if value is None:
            out.append(m.group(0))
        elif windows:
            out.append(
                _windows_in_quotes_safe(value) if state == '"' else windows_cmd_quote(value)
            )
        elif state == '"':
            out.append(re.sub(r'([\\"$`])', r"\\\1", value))
        elif state == "'":
            out.append(value.replace("'", "'\\''"))
        else:
            out.append(shlex.quote(value))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _shell_scan(segment: str, state: Optional[str], windows: bool) -> Optional[str]:
    escaped = False
    for ch in segment:
        if escaped:
            escaped = False
            continue
        if windows:
            if ch == '"':
                state = None if state == '"' else '"'
            elif ch == "^" and state is None:
                escaped = True
            continue
        if state == "'":
            if ch == "'":
                state = None
        elif state == '"':
            if ch == "\\":
                escaped = True
            elif ch == '"':
                state = None
        else:
            if ch == "\\":
                escaped = True
            elif ch in ("'", '"'):
                state = ch
    return state


def substitute_connector_placeholders_mapping(
    data: Dict[str, Any], ctx: Dict[str, Any]
) -> Dict[str, Any]:
    """Recursively substitute in string values (dicts and lists) when `{` is present."""
    out: Dict[str, Any] = {}
    for k, v in data.items():
        out[k] = _substitute_value(v, ctx)
    return out


def _substitute_value(value: Any, ctx: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        return substitute_connector_placeholders(value, ctx) if "{" in value else value
    if isinstance(value, dict):
        return substitute_connector_placeholders_mapping(value, ctx)
    if isinstance(value, list):
        return [_substitute_value(item, ctx) for item in value]
    return value

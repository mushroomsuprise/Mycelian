# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Chatbot custom variables (``{custom_<name>}``): storage, evaluation, preview.

No NiceGUI here: the chatbot runtime evaluates custom variables through this
module, the editor UI only re-exports / calls it.

Storage (``chatbot_custom_variables`` in the database)::

    {"data": {"custom_x": <definition>, ...}, "last_updated": ..., "version": "1.0"}

A definition is either the legacy string ``"{custom_x} - Custom: <expression>"``
or the structured form the editor writes::

    {"kind": "math"|"if"|"age"|"text"|"expression",
     "params": {...},              # see compile_custom_variable
     "expression": "<compiled>",   # display / legacy form
     "description": "<optional>"}

Evaluation renders variables single-pass with the chatbot template engine
(:func:`modules.chatbot_core.render_template`): values inserted from viewer
text are never scanned again for ``{...}`` or for functions. The context of the
command / event wins over the live basics, so ``{username}`` is the chatter.

Math never uses ``eval``: :func:`safe_eval_math` walks the Python AST and allows
numbers, ``+ - * / // % **``, parentheses, unary minus and the functions
``round min max abs floor ceil int float``.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CUSTOM_VARIABLES_PATH = "chatbot_custom_variables"
KINDS = ("math", "if", "age", "text", "expression")
_LEGACY_SEPARATOR = " - Custom: "
_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_MAX_DEPTH = 8

# --------------------------------------------------------------------------
# Safe math
# --------------------------------------------------------------------------

_BIN_OPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _round(value, digits=None):
    if digits is None:
        return round(value)
    return round(value, int(digits))


_FUNCS: Dict[str, Callable[..., Any]] = {
    "round": _round,
    "min": min,
    "max": max,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "int": int,
    "float": float,
}
_FUNC_ARITY = {"round": (1, 2), "abs": (1, 1), "floor": (1, 1), "ceil": (1, 1),
               "int": (1, 1), "float": (1, 1), "min": (1, 50), "max": (1, 50)}


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _eval_node(node.operand)
        return -value if isinstance(node.op, ast.USub) else +value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("power too large")
        result = _BIN_OPS[type(node.op)](left, right)
        if isinstance(result, complex):
            raise ValueError("complex result")
        if isinstance(result, (int, float)) and abs(result) > 1e300:
            raise ValueError("result too large")
        return result
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("function not allowed")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        low, high = _FUNC_ARITY[node.func.id]
        if not low <= len(node.args) <= high:
            raise ValueError(f"wrong number of arguments for {node.func.id}")
        args = [_eval_node(arg) for arg in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError(f"not allowed: {type(node).__name__}")


def safe_eval_math(expression: str) -> Any:
    """Evaluate arithmetic text safely. Raises ValueError for anything else."""
    text = str(expression or "").strip()
    if not text:
        raise ValueError("empty expression")
    if len(text) > 500:
        raise ValueError("expression too long")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc.msg}") from None
    try:
        return _eval_node(tree)
    except ZeroDivisionError:
        raise ValueError("division by zero") from None
    except (TypeError, OverflowError) as exc:
        raise ValueError(str(exc)) from None


def format_number(value: Any, decimals: Optional[int] = None) -> str:
    """Ints without ``.0``; floats rounded to *decimals* (default: up to 4)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number or number in (float("inf"), float("-inf")):
        return "0"
    if decimals is not None:
        decimals = max(0, min(10, int(decimals)))
        if decimals == 0:
            return str(int(round(number)))
        return f"{number:.{decimals}f}"
    if number.is_integer():
        return str(int(number))
    text = f"{round(number, 4):.4f}".rstrip("0").rstrip(".")
    return text or "0"


_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def as_number(value: Any) -> Optional[float]:
    """Float for numbers and numeric text (``"1,234"`` allowed), else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if _NUMBER_RE.match(text):
        try:
            return float(text)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Comparison helpers (shared with the "if" kind and legacy compare())
# --------------------------------------------------------------------------

_OP_ALIASES = {
    "==": "equal", "=": "equal", "equal": "equal", "equals": "equal",
    "!=": "not_equal", "not_equal": "not_equal",
    ">": "greater_than", "greater_than": "greater_than",
    ">=": "greater_than_or_equal", "greater_than_or_equal": "greater_than_or_equal",
    "<": "less_than", "less_than": "less_than",
    "<=": "less_than_or_equal", "less_than_or_equal": "less_than_or_equal",
    "contains": "contains", "starts_with": "starts_with", "ends_with": "ends_with",
}


def compare_values(left: Any, op: str, right: Any) -> bool:
    """Numeric compare when both sides are numbers, else case-insensitive text."""
    name = _OP_ALIASES.get(str(op or "").strip().lower())
    if name is None:
        raise ValueError(f"unknown operator {op!r}")
    ltext = "" if left is None else str(left).strip()
    rtext = "" if right is None else str(right).strip()
    if name in ("contains", "starts_with", "ends_with"):
        a, b = ltext.casefold(), rtext.casefold()
        if name == "contains":
            return b in a
        if name == "starts_with":
            return a.startswith(b)
        return a.endswith(b)
    lnum, rnum = as_number(ltext), as_number(rtext)
    if lnum is not None and rnum is not None:
        a, b = lnum, rnum
    else:
        a, b = ltext.casefold(), rtext.casefold()
        if name not in ("equal", "not_equal"):
            return False
    return {
        "equal": a == b,
        "not_equal": a != b,
        "greater_than": a > b,
        "greater_than_or_equal": a >= b,
        "less_than": a < b,
        "less_than_or_equal": a <= b,
    }[name]


# --------------------------------------------------------------------------
# Friendly durations
# --------------------------------------------------------------------------


def to_local_naive(value: Any) -> Optional[datetime]:
    """Local naive datetime for a date / epoch / ISO value, or None."""
    from .connector_placeholders import coerce_datetime

    moment = coerce_datetime(value)
    if moment is None:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def friendly_duration(start: Any, end: Any = None, *, max_units: int = 2) -> str:
    """``"2 years, 3 months"`` / ``"3 hours, 12 minutes"`` between two moments."""
    begin = to_local_naive(start)
    finish = to_local_naive(end) if end is not None else datetime.now()
    if begin is None or finish is None:
        return ""
    if begin > finish:
        begin, finish = finish, begin
    months = (finish.year - begin.year) * 12 + (finish.month - begin.month)
    anchor = _add_months(begin, months)
    if anchor > finish:
        months -= 1
        anchor = _add_months(begin, months)
    years, months = divmod(max(months, 0), 12)
    rest = finish - anchor
    days = rest.days
    hours, remainder = divmod(rest.seconds, 3600)
    minutes = remainder // 60
    parts = [
        (years, "year"), (months, "month"), (days, "day"),
        (hours, "hour"), (minutes, "minute"),
    ]
    out = [_plural(value, word) for value, word in parts if value][:max_units]
    return ", ".join(out) if out else "less than a minute"


def _add_months(moment: datetime, months: int) -> datetime:
    import calendar

    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _legacy_date_to_age(text: str) -> str:
    """Legacy ``date_to_age()`` output: ``"N years, M days"``."""
    moment = to_local_naive(str(text or "").strip().strip('"').strip("'"))
    if moment is None:
        return "0 days"
    days = (datetime.now() - moment).days
    return f"{days // 365} years, {days % 365} days"


# --------------------------------------------------------------------------
# Expression mini-language: math() / compare() / date_to_age() / if()
# --------------------------------------------------------------------------

_FUNC_NAMES = ("math", "compare", "date_to_age", "if")
_FUNC_START = re.compile(r"\b(math|compare|date_to_age|if)\(", re.IGNORECASE)


def _render(text: str, context: Dict[str, Any], depth: int) -> str:
    from .chatbot_core import render_template

    return render_template(text, context, _depth=depth)


def _find_close(text: str, open_index: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_index*, or -1."""
    level = 0
    braces = 0
    quote = ""
    for index in range(open_index, len(text)):
        ch = text[index]
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'" and braces == 0:
            quote = ch
        elif ch == "{":
            braces += 1
        elif ch == "}":
            braces = max(0, braces - 1)
        elif braces == 0 and ch == "(":
            level += 1
        elif braces == 0 and ch == ")":
            level -= 1
            if level == 0:
                return index
    return -1


def _split_args(text: str) -> List[str]:
    args: List[str] = []
    level = braces = 0
    quote = ""
    current = []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'" and braces == 0:
            quote = ch
        elif ch == "{":
            braces += 1
        elif ch == "}":
            braces = max(0, braces - 1)
        elif braces == 0 and ch == "(":
            level += 1
        elif braces == 0 and ch == ")":
            level -= 1
        elif ch == "," and level == 0 and braces == 0:
            args.append("".join(current))
            current = []
            continue
        current.append(ch)
    args.append("".join(current))
    return args


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _math_operand_text(template: str, context: Dict[str, Any], depth: int) -> str:
    """Render a math() argument: variables become numeric literals.

    A variable whose value is not a number becomes a quoted string, which the
    safe evaluator rejects, so viewer text can never add operators.
    """
    from .chatbot_core import TEMPLATE_TOKEN, resolve_template_token

    # Evaluate nested mini-language calls first (their results are template text).
    template = _evaluate_calls(template, context, depth, math_mode=True)

    def repl(match: re.Match) -> str:
        value = resolve_template_token(match.group(1), context, depth)
        if value is None:
            return repr(match.group(0))
        number = as_number(value)
        if number is None:
            return repr(str(value))
        return repr(int(number)) if float(number).is_integer() else repr(number)

    return TEMPLATE_TOKEN.sub(repl, template)


def _eval_call(name: str, raw_args: str, context: Dict[str, Any], depth: int) -> str:
    name = name.lower()
    if name == "math":
        try:
            return format_number(safe_eval_math(_math_operand_text(raw_args, context, depth)))
        except ValueError as exc:
            logger.debug("Custom variable math failed: %s", exc)
            return "0"
    args = _split_args(raw_args)
    if name == "compare":
        if len(args) != 3:
            return "False"
        left = _strip_quotes(evaluate_expression(args[0], context, depth))
        op = args[1].strip()
        right = _strip_quotes(evaluate_expression(args[2], context, depth))
        try:
            return "True" if compare_values(left, op, right) else "False"
        except ValueError:
            return "False"
    if name == "date_to_age":
        return _legacy_date_to_age(evaluate_expression(raw_args, context, depth))
    if name == "if":
        if len(args) != 3:
            return ""
        cond = _strip_quotes(evaluate_expression(args[0], context, depth)).lower()
        chosen = args[1] if cond in ("true", "yes", "1") else args[2]
        return _strip_quotes(evaluate_expression(chosen, context, depth))
    return ""


def _evaluate_calls(text: str, context: Dict[str, Any], depth: int, *, math_mode: bool = False) -> str:
    """Replace mini-language calls in *text* (template text) by their results.

    In math mode the remaining text is left as template text for the caller.
    """
    out: List[str] = []
    pos = 0
    while True:
        match = _FUNC_START.search(text, pos)
        if not match:
            break
        close = _find_close(text, match.end() - 1)
        if close < 0:
            break
        out.append(text[pos:match.start()])
        result = _eval_call(match.group(1), text[match.end():close], context, depth)
        out.append(result if math_mode else "\x00" + result + "\x01")
        pos = close + 1
    out.append(text[pos:])
    return "".join(out)


def evaluate_expression(expression: str, context: Optional[Dict[str, Any]] = None, depth: int = 0) -> str:
    """Evaluate a legacy custom expression (template text with function calls)."""
    context = context or {}
    text = str(expression or "")
    if not _FUNC_START.search(text):
        return _render(text, context, depth)
    # Function results are wrapped in \x00..\x01 so the surrounding template
    # text is rendered without touching them (single pass).
    marked = _evaluate_calls(text, context, depth)
    pieces = re.split(r"(\x00[^\x00\x01]*\x01)", marked)
    rendered = []
    for piece in pieces:
        if piece.startswith("\x00") and piece.endswith("\x01"):
            rendered.append(piece[1:-1])
        else:
            rendered.append(_render(piece, context, depth))
    return "".join(rendered)


# --------------------------------------------------------------------------
# Structured definitions
# --------------------------------------------------------------------------

_MATH_OPS = ("+", "-", "*", "/")
_IF_OPS = ("equal", "not_equal", "greater_than", "greater_than_or_equal",
           "less_than", "less_than_or_equal", "contains")
_AGE_STYLES = ("long", "days", "years")
_OP_SYMBOL = {"equal": "==", "not_equal": "!=", "greater_than": ">",
              "greater_than_or_equal": ">=", "less_than": "<",
              "less_than_or_equal": "<=", "contains": "contains"}


def normalize_name(name: str) -> str:
    """``"Big Number"`` -> ``"custom_big_number"``; raises ValueError if unusable."""
    clean = str(name or "").strip().strip("{}").strip().lower()
    if clean.startswith("custom_"):
        clean = clean[len("custom_"):]
    clean = re.sub(r"[\s\-]+", "_", clean)
    if not clean or not _NAME_RE.match(clean):
        raise ValueError("Use lowercase letters, digits and underscores only")
    return "custom_" + clean


def compile_custom_variable(kind: str, params: Dict[str, Any]) -> str:
    """Expression text for a structured definition (display / legacy form).

    Raises ValueError for an unknown kind or bad params.
    """
    params = dict(params or {})
    if kind == "text":
        return str(params.get("template") or "")
    if kind == "expression":
        return str(params.get("expression") or "")
    if kind == "math":
        op = str(params.get("op") or "+").strip()
        if op not in _MATH_OPS:
            raise ValueError("Math operator must be one of + - * /")
        left = str(params.get("left", "")).strip()
        right = str(params.get("right", "")).strip()
        if not left or not right:
            raise ValueError("Math needs a value on both sides")
        body = f"{left} {op} {right}"
        decimals = params.get("decimals")
        if decimals not in (None, ""):
            return f"math(round({body}, {int(decimals)}))"
        return f"math({body})"
    if kind == "if":
        op = str(params.get("op") or "equal").strip()
        if op not in _IF_OPS:
            raise ValueError("Unknown comparison")
        return (
            f"if(compare({params.get('left', '')}, {_OP_SYMBOL[op]}, "
            f"{params.get('right', '')}), {params.get('then', '')}, "
            f"{params.get('else', '')})"
        )
    if kind == "age":
        style = str(params.get("style") or "long")
        if style not in _AGE_STYLES:
            raise ValueError("Age style must be long, days or years")
        if not str(params.get("date") or "").strip():
            raise ValueError("Age needs a date")
        return f"date_to_age({params.get('date')})"
    raise ValueError(f"Unknown custom variable kind: {kind!r}")


def normalize_definition(raw: Any) -> Optional[Dict[str, Any]]:
    """Structured form of a stored definition (legacy strings become 'expression')."""
    if isinstance(raw, str):
        expression = raw.split(_LEGACY_SEPARATOR, 1)[1] if _LEGACY_SEPARATOR in raw else raw
        expression = expression.strip()
        return {
            "kind": "expression",
            "params": {"expression": expression},
            "expression": expression,
            "description": "",
        }
    if isinstance(raw, dict):
        kind = raw.get("kind") if raw.get("kind") in KINDS else "expression"
        params = dict(raw.get("params") or {})
        expression = str(raw.get("expression") or "")
        if kind == "expression" and "expression" not in params:
            params["expression"] = expression
        if not expression:
            try:
                expression = compile_custom_variable(kind, params)
            except ValueError:
                expression = ""
        return {
            "kind": kind,
            "params": params,
            "expression": expression,
            "description": str(raw.get("description") or ""),
        }
    return None


def _evaluate_definition(definition: Dict[str, Any], context: Dict[str, Any], depth: int) -> str:
    kind = definition.get("kind")
    params = definition.get("params") or {}
    if kind == "text":
        return _render(str(params.get("template") or ""), context, depth)
    if kind == "math":
        expr = f"({params.get('left', '')}) {params.get('op', '+')} ({params.get('right', '')})"
        try:
            value = safe_eval_math(_math_operand_text(expr, context, depth))
        except ValueError as exc:
            logger.debug("Custom math failed: %s", exc)
            return "0"
        decimals = params.get("decimals")
        return format_number(value, None if decimals in (None, "") else int(decimals))
    if kind == "if":
        left = _render(str(params.get("left", "")), context, depth)
        right = _render(str(params.get("right", "")), context, depth)
        try:
            ok = compare_values(left, params.get("op", "equal"), right)
        except ValueError:
            ok = False
        return _render(str(params.get("then" if ok else "else", "")), context, depth)
    if kind == "age":
        from .chatbot_core import resolve_raw_template_value

        raw = resolve_raw_template_value(str(params.get("date") or ""), context, depth)
        moment = to_local_naive(raw)
        if moment is None:
            return ""
        style = params.get("style") or "long"
        days = (datetime.now() - moment).days
        if style == "days":
            return str(max(days, 0))
        if style == "years":
            return str(max(days, 0) // 365)
        return friendly_duration(moment)
    expression = params.get("expression") if "expression" in params else definition.get("expression")
    return evaluate_expression(str(expression or ""), context, depth)


# --------------------------------------------------------------------------
# Storage (cached)
# --------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_failed_at = 0.0
_FAILED_RETRY_SECONDS = 30.0


def _db():
    from . import database_manager

    return database_manager


def invalidate_custom_variable_cache() -> None:
    global _cache, _cache_failed_at
    with _cache_lock:
        _cache = None
        _cache_failed_at = 0.0


def load_custom_variables() -> dict:
    """Raw stored definitions ``{name: str | dict}`` (cached; copy returned)."""
    global _cache, _cache_failed_at
    with _cache_lock:
        if _cache is not None:
            return dict(_cache)
        if _cache_failed_at and time.monotonic() - _cache_failed_at < _FAILED_RETRY_SECONDS:
            return {}
    try:
        stored, ok = _db().get_data_checked(CUSTOM_VARIABLES_PATH)
    except Exception as exc:
        logger.debug("Custom variables read failed: %s", exc)
        stored, ok = {}, False
    data = stored.get("data") if ok and isinstance(stored, dict) else None
    with _cache_lock:
        if not ok:
            _cache_failed_at = time.monotonic()
            return {}
        _cache = dict(data) if isinstance(data, dict) else {}
        logger.debug("Loaded %d custom variables", len(_cache))
        return dict(_cache)


def _load_custom_variables_for_write() -> Optional[dict]:
    """Stored custom variables for a read-modify-write, or None when the read failed.

    A failed read returns ``{}`` from ``get_data``; writing back from that would
    replace every stored variable, so callers skip the write instead.
    """
    try:
        stored_data, ok = _db().get_data_checked(CUSTOM_VARIABLES_PATH)
    except Exception:
        stored_data, ok = {}, False
    if not ok:
        logger.warning(
            "Custom variables could not be read from storage; not saving so the "
            "stored variables are not overwritten"
        )
        return None
    if stored_data and "data" in stored_data:
        return dict(stored_data["data"])
    return {}


def save_custom_variables(custom_variables: dict) -> bool:
    """Write the whole definitions dict."""
    global _cache, _cache_failed_at
    try:
        data_dict = {
            "data": custom_variables,
            "last_updated": time.time(),
            "version": "1.0",
        }
        success = _db().set_data(CUSTOM_VARIABLES_PATH, data_dict)
    except Exception as e:
        logger.error(f"Error saving custom variables: {e}", exc_info=True)
        return False
    if success:
        with _cache_lock:
            _cache = dict(custom_variables)
            _cache_failed_at = 0.0
        logger.info(f"Saved {len(custom_variables)} custom variables to storage")
    else:
        logger.error("Failed to save custom variables to storage")
    return bool(success)


def add_custom_variable(name: str, expression: str) -> bool:
    """Legacy add: stores ``"{custom_x} - Custom: <expression>"``."""
    try:
        custom_vars = _load_custom_variables_for_write()
        if custom_vars is None:
            return False
        clean_name = name.strip()
        if not clean_name.startswith("custom_"):
            clean_name = f"custom_{clean_name}"
        custom_vars[clean_name] = f"{{{clean_name}}}{_LEGACY_SEPARATOR}{expression.strip()}"
        return save_custom_variables(custom_vars)
    except Exception as e:
        logger.error(f"Error adding custom variable: {e}", exc_info=True)
        return False


def remove_custom_variable(name: str) -> bool:
    """Remove a stored definition (missing name counts as success)."""
    try:
        custom_vars = _load_custom_variables_for_write()
        if custom_vars is None:
            return False
        if name in custom_vars:
            del custom_vars[name]
            return save_custom_variables(custom_vars)
        return True
    except Exception as e:
        logger.error(f"Error removing custom variable: {e}", exc_info=True)
        return False


def load_custom_definitions() -> Dict[str, Dict[str, Any]]:
    """``{"custom_x": structured definition}`` for every stored variable."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, raw in load_custom_variables().items():
        definition = normalize_definition(raw)
        if definition is not None:
            out[str(name)] = definition
    return out


def validate_custom_definition(definition: Any) -> Optional[str]:
    """Error text for a definition the editor wants to save, or None."""
    if not isinstance(definition, dict):
        return "Definition must be a dict"
    kind = definition.get("kind")
    if kind not in KINDS:
        return f"Unknown kind {kind!r}"
    try:
        compile_custom_variable(kind, definition.get("params") or {})
    except ValueError as exc:
        return str(exc)
    return None


def save_custom_definition(name: str, definition: Dict[str, Any]) -> bool:
    """Store a structured definition under ``custom_<name>`` (name normalized)."""
    try:
        key = normalize_name(name)
    except ValueError as exc:
        logger.warning("Custom variable not saved: %s", exc)
        return False
    error = validate_custom_definition(definition)
    if error:
        logger.warning("Custom variable %s not saved: %s", key, error)
        return False
    custom_vars = _load_custom_variables_for_write()
    if custom_vars is None:
        return False
    kind = definition["kind"]
    params = dict(definition.get("params") or {})
    custom_vars[key] = {
        "kind": kind,
        "params": params,
        "expression": compile_custom_variable(kind, params),
        "description": str(definition.get("description") or ""),
    }
    return save_custom_variables(custom_vars)


def delete_custom_definition(name: str) -> bool:
    try:
        key = normalize_name(name)
    except ValueError:
        return False
    return remove_custom_variable(key)


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def get_live_basic_variables() -> Dict[str, str]:
    """Live basics for previews: the streamer's name and the clock."""
    username = "PreviewUser"
    try:
        import sys

        twitch = sys.modules.get("modules.twitch")
        api = getattr(twitch, "twitch_api", None) if twitch else None
        if api is not None and getattr(api, "is_connected", False):
            user = getattr(api, "user", None)
            if user is not None and getattr(user, "display_name", None):
                username = user.display_name
    except Exception:
        pass
    from .chatbot_core import format_time_variable

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "username": username,
        "timestamp": timestamp,
        "datetime": now.strftime("%Y-%m-%d %H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "time": format_time_variable(""),
        "source": "Twitch",
    }


def _key_for(var_name: str) -> str:
    name = str(var_name or "").strip().strip("{}")
    return name if name.startswith("custom_") else f"custom_{name}"


def evaluate_custom_variable(
    var_name: str,
    context: Optional[Dict[str, Any]] = None,
    _depth: int = 0,
) -> Optional[str]:
    """Value of ``{custom_x}`` for *context*, or None when it is not defined."""
    if _depth > _MAX_DEPTH:
        return None
    raw = load_custom_variables().get(_key_for(var_name))
    definition = normalize_definition(raw) if raw is not None else None
    if definition is None:
        return None
    ctx = {**get_live_basic_variables(), **(context or {})}
    try:
        return _evaluate_definition(definition, ctx, _depth + 1)
    except Exception as exc:
        logger.debug("Custom variable %s failed: %s", var_name, exc)
        return ""


def preview_custom_variable(definition: Any, context: Optional[Dict[str, Any]] = None) -> str:
    """Evaluate an unsaved definition (dict or legacy string) for the editor.

    ``context=None`` uses the example command context of the variable catalog.
    Errors come back as ``"Error: ..."``.
    """
    normalized = normalize_definition(definition)
    if normalized is None:
        return "Error: invalid definition"
    if isinstance(definition, dict) and definition.get("kind") in KINDS:
        error = validate_custom_definition(definition)
        if error:
            return f"Error: {error}"
    if context is None:
        try:
            from .chatbot_variables import example_context

            context = example_context("command")
        except Exception:
            context = {}
    ctx = {**get_live_basic_variables(), **context}
    try:
        return _evaluate_definition(normalized, ctx, 1)
    except Exception as exc:
        return f"Error: {exc}"


def evaluate_custom_variable_expression(
    var_name: str, context_data: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Compatibility name for :func:`evaluate_custom_variable`."""
    return evaluate_custom_variable(var_name, context_data)


# Legacy helpers kept for the old editor (all safe now).


def _evaluate_date_to_age(date_str: str) -> str:
    return _legacy_date_to_age(date_str)


def _evaluate_math(math_expr: str) -> float:
    text = str(math_expr or "")
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 3:
            text = f"{parts[0]} {parts[1]} {parts[2]}"
    try:
        return safe_eval_math(text)
    except ValueError:
        return 0


def _evaluate_compare(compare_expr: str) -> bool:
    parts = [p.strip() for p in str(compare_expr or "").split(",")]
    if len(parts) != 3:
        return False
    try:
        return compare_values(parts[0], parts[1], parts[2])
    except ValueError:
        return False


def validate_expression_variables(expression: str) -> list:
    """Variables in *expression* that are not known (old editor validation)."""
    try:
        invalid_vars = []
        basic_vars = get_live_basic_variables()
        custom_vars = load_custom_variables()
        command_vars = {
            "command_message", "command_first_word", "command_last_word",
            "cooldown", "usage_left", "count", "command_name", "last_used",
        }
        for var in re.findall(r"\{([^}]+)\}", expression or ""):
            key = var.split(":", 1)[0]
            if (
                key in basic_vars
                or key in custom_vars
                or f"custom_{key}" in custom_vars
                or key.startswith(("stats.", "youtube.", "quote.", "time"))
                or key in command_vars
                or re.fullmatch(r"command_word_\d+", key)
            ):
                continue
            invalid_vars.append(var)
        return invalid_vars
    except Exception as e:
        logger.error(f"Error validating expression variables: {e}", exc_info=True)
        return []

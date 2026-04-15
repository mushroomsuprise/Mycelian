"""
Connector placeholder substitution: single-brace `{token}` (no spaces inside).

Legacy `{{ ... }}` is normalized to `{...}` on each pass. Context is a flat merge
of dicts (e.g. event_data, trigger_data, hooks).
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LEGACY_DOUBLE_BRACE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_SINGLE_BRACE = re.compile(r"\{([^\s{}]+)\}")


def normalize_legacy_double_brace(text: str) -> str:
    """Convert `{{key}}` to `{key}` (inner whitespace stripped, no spaces inside braces)."""

    def repl(m: re.Match) -> str:
        inner = m.group(1).strip().replace(" ", "")
        if not inner:
            return m.group(0)
        return "{" + inner + "}"

    return _LEGACY_DOUBLE_BRACE.sub(repl, text)


def _ctx_lookup(cur: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            return None
    return cur


def _message_word(ctx: Dict[str, Any], index_one_based: int) -> str:
    if index_one_based < 1:
        return ""
    msg = ctx.get("message")
    words = str(msg or "").split()
    if index_one_based > len(words):
        return ""
    return words[index_one_based - 1]


def _try_message_word_token(token: str, ctx: Dict[str, Any]) -> Optional[str]:
    m = re.fullmatch(r"message\.word\.(\d+)", token)
    if not m:
        return None
    return _message_word(ctx, int(m.group(1)))


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


def substitute_connector_placeholders(text: str, ctx: Dict[str, Any]) -> str:
    """Replace `{token}` segments using ctx and built-in special tokens."""
    if not text or "{" not in text:
        return text

    text = normalize_legacy_double_brace(text)

    def repl(m: re.Match) -> str:
        key = m.group(1)
        spec = _resolve_special_token(key, ctx)
        if spec is not None:
            return spec
        v = _ctx_lookup(ctx, key)
        if v is None:
            return ""
        return str(v)

    return _SINGLE_BRACE.sub(repl, text)


def substitute_connector_placeholders_mapping(
    data: Dict[str, Any], ctx: Dict[str, Any]
) -> Dict[str, Any]:
    """Recursively substitute in string dict values when `{` is present."""
    out: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and "{" in v:
            out[k] = substitute_connector_placeholders(v, ctx)
        elif isinstance(v, dict):
            out[k] = substitute_connector_placeholders_mapping(v, ctx)
        else:
            out[k] = v
    return out

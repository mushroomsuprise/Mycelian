"""Pure helpers for Stream Deck template action HTTP dispatch (testable without Flask)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def coerce_streamdeck_action_data(raw: Any) -> Dict[str, Any]:
    """Normalize client ``actionData`` (dict or JSON string) to a plain dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
        return {}
    return {}


def merged_streamdeck_options_payload(
    action_config: dict, raw_action_data: Any
) -> Dict[str, Any]:
    """``default_data`` from template action spec, overridden by client ``actionData``."""
    coerced = coerce_streamdeck_action_data(raw_action_data)
    dd = action_config.get("default_data", {})
    if not isinstance(dd, dict):
        dd = {}
    merged: Dict[str, Any] = dict(dd)
    if coerced:
        merged.update(coerced)
    return merged


def resolve_streamdeck_options_action(
    streamdeck_actions: dict, action_name: str
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Resolve ``streamdeck_options.actions`` entry by dict key or display ``name``.
    Returns ``(action_config_dict_or_None, dict_key)``.
    """
    if not isinstance(streamdeck_actions, dict) or not action_name:
        return None, None
    if action_name in streamdeck_actions:
        spec = streamdeck_actions[action_name]
        return (spec if isinstance(spec, dict) else None), str(action_name)
    an = str(action_name).strip()
    for key, spec in streamdeck_actions.items():
        if not isinstance(spec, dict):
            continue
        if str(spec.get("name") or "").strip() == an:
            return spec, str(key)
    for key, spec in streamdeck_actions.items():
        if not isinstance(spec, dict):
            continue
        ev_raw = spec.get("event")
        if ev_raw is None:
            continue
        ev = str(ev_raw).strip()
        if not ev:
            continue
        if ev == an:
            return spec, str(key)
        if ev.endswith("_" + an):
            return spec, str(key)
    return None, None


def plan_streamdeck_template_action_emit(
    *,
    template_name: str,
    action_name: str,
    event_name_req: str,
    action_data_raw: Any,
    template_config: Optional[dict],
    use_client_event_name: bool = False,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Resolve compat action key, socket event name, and merged payload for emit.

    Template config is authoritative for ``event`` when the action exists;
    stale ``eventName`` from Stream Deck property-inspector settings is ignored.
    """
    coerced = coerce_streamdeck_action_data(action_data_raw)
    compat_key = action_name
    merged_ad = coerced
    resolved_event = event_name_req if event_name_req else action_name

    if template_config:
        sdo = template_config.get("streamdeck_options")
        if isinstance(sdo, dict):
            acts = sdo.get("actions")
            if isinstance(acts, dict):
                acfg, sd_key = resolve_streamdeck_options_action(acts, action_name)
                if acfg is not None:
                    compat_key = sd_key or action_name
                    merged_ad = merged_streamdeck_options_payload(acfg, coerced)
                    resolved_event = str(
                        acfg.get("event") or f"{template_name}_{compat_key}"
                    )
                elif use_client_event_name and event_name_req:
                    resolved_event = event_name_req

    return compat_key, resolved_event, merged_ad

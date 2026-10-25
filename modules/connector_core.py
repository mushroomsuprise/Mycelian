#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import contextvars
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# --- Why a connector action failed -------------------------------------------
# Connector.process_event opens a slot per action; the action (or anything it
# calls, including asyncio.to_thread work) fills it through report_action_problem.
# Warnings and errors logged while the action runs are kept as technical details.
_action_problem: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "connector_action_problem", default=None
)
_MAX_CAPTURED_LOG_LINES = 6


def report_action_problem(reason: str, hint: str = "", details: str = "") -> None:
    """Explain why the running connector action is about to return False.

    *reason* is plain language ("the overlay server isn't running"); *hint* says how
    to fix it; *details* is technical text for "Copy details". No-op outside an action.
    """
    slot = _action_problem.get()
    if slot is None:
        return
    slot["reason"] = str(reason or "").strip()
    if hint:
        slot["hint"] = str(hint).strip()
    if details:
        slot["details"] = str(details)


def current_action_connector() -> Optional[tuple]:
    """(connector_id, connector name) of the action running now, or None."""
    slot = _action_problem.get()
    if slot is None or "connector_id" not in slot:
        return None
    return slot["connector_id"], slot.get("connector_name", "")


def mark_action_skipped() -> None:
    """The running action returned False on purpose (nothing to do); don't report it."""
    slot = _action_problem.get()
    if slot is not None:
        slot["skipped"] = True


class _ActionLogCapture(logging.Handler):
    """Keeps warnings/errors logged while a connector action runs (details only)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            slot = _action_problem.get()
            if slot is None:
                return
            lines = slot.setdefault("log", [])
            if len(lines) < _MAX_CAPTURED_LOG_LINES:
                lines.append(f"{record.levelname}: {record.getMessage()}")
        except Exception:
            pass


_log_capture = _ActionLogCapture(level=logging.WARNING)
if not any(isinstance(h, _ActionLogCapture) for h in logging.getLogger().handlers):
    logging.getLogger().addHandler(_log_capture)

_ACTION_LABELS = {
    "websocket_emit": "Send overlay event",
    "api_call": "API call",
    "obs_control": "OBS control",
}


def action_label(action: Any) -> str:
    """Readable action name for messages."""
    name = str(getattr(action, "name", "") or "").strip()
    if name:
        return name
    value = getattr(getattr(action, "action_type", None), "value", "") or "action"
    return _ACTION_LABELS.get(value, value.replace("_", " ").capitalize())


def _notify_action_failure(connector: "Connector", failure: Dict[str, Any]) -> None:
    """One deduplicated notification per connector + action slot."""
    try:
        from .notification_engine import nav_actions_main_tab, notify

        reason = failure.get("reason") or ""
        label = failure["label"]
        index = failure["index"]
        message = f"Action {index} ({label}) didn't work" + (f": {reason}." if reason else ".")
        notify(
            message,
            type="warning",
            source="connectors",
            title=f"Connector “{connector.name}” had a problem",
            hint=failure.get("hint")
            or "Open the connector in the Connectors tab and check this action's settings, then use Test.",
            details=failure.get("details") or None,
            actions=nav_actions_main_tab("Connectors"),
            dedupe_key=f"connector:{connector.connector_id}:{index}",
            dedupe_cooldown_sec=600,
        )
    except Exception:
        logger.debug("Could not show the connector failure notification", exc_info=True)

_REGEX_CACHE_MAX = 256
_regex_match_cache: Dict[str, Any] = {}
_REGEX_INVALID = object()


def _compiled_regex_match(pattern: str, ignore_case: bool = False):
    key = ("i:" if ignore_case else "s:") + pattern
    cached = _regex_match_cache.get(key, _REGEX_INVALID)
    if cached is not _REGEX_INVALID:
        return cached
    try:
        compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error:
        compiled = None
    if len(_regex_match_cache) >= _REGEX_CACHE_MAX:
        _regex_match_cache.clear()
    _regex_match_cache[key] = compiled
    return compiled


class ComparisonOperator(Enum):
    """Comparison operators for trigger conditions"""

    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"  # First word (exact token match)
    BEGINS_WITH = "begins_with"  # Prefix match (legacy starts_with behavior)
    ENDS_WITH = "ends_with"
    REGEX_MATCH = "regex_match"


class TriggerType(Enum):
    """Types of triggers available in the connector system"""

    # Twitch Event Triggers
    TWITCH_BITS = "twitch_bits"
    TWITCH_SUB = "twitch_sub"
    TWITCH_RESUB = "twitch_resub"
    TWITCH_GIFTSUB = "twitch_giftsub"
    TWITCH_FOLLOW = "twitch_follow"
    TWITCH_RAID = "twitch_raid"
    TWITCH_POINTS = "twitch_points"
    TWITCH_CHAT_MESSAGE = "twitch_chat_message"
    TWITCH_HYPE_TRAIN_START = "twitch_hype_train_start"
    TWITCH_HYPE_TRAIN_END = "twitch_hype_train_end"
    TWITCH_STREAM_ONLINE = "twitch_stream_online"
    TWITCH_STREAM_OFFLINE = "twitch_stream_offline"

    # YouTube Live Chat Triggers
    YOUTUBE_CHAT_MESSAGE = "youtube_chat_message"
    YOUTUBE_MEMBER = "youtube_member"
    YOUTUBE_MEMBER_MILESTONE = "youtube_member_milestone"
    YOUTUBE_GIFT_MEMBERSHIP = "youtube_gift_membership"
    YOUTUBE_SUPERCHAT = "youtube_superchat"
    YOUTUBE_SUPERSTICKER = "youtube_supersticker"

    # Donation / tip triggers (e.g. connector webhooks)
    DONATION = "donation"

    # Timer/Schedule Triggers
    TIMER = "timer"
    SCHEDULE = "schedule"

    # Manual/Custom Triggers
    HOTKEY = "hotkey"
    STREAMDECK = "streamdeck"
    WEBHOOK = "webhook"

    # OBS Studio (WebSocket) triggers
    OBS_SCENE_CHANGED = "obs_scene_changed"
    OBS_STREAM_STATE = "obs_stream_state"
    OBS_RECORD_STATE = "obs_record_state"
    OBS_INPUT_MUTE = "obs_input_mute"

    # Matches any connector-processed event type
    ANY = "any"


class ActionType(Enum):
    """Types of actions available in the connector system"""

    # Template Control Actions
    TEMPLATE_CONTROL = "template_control"

    # WebSocket Actions
    WEBSOCKET_EMIT = "websocket_emit"

    # Alert System Actions
    TRIGGER_ALERT = "trigger_alert"

    # Chat Actions
    SEND_CHAT_MESSAGE = "send_chat_message"
    SEND_ANNOUNCEMENT = "send_announcement"
    DISPLAY_BANNER = "display_banner"
    SEND_DISCORD_MESSAGE = "send_discord_message"

    # Greeting Actions
    ADD_GREETING = "add_greeting"
    UPDATE_GREETING = "update_greeting"
    SEND_GREETING = "send_greeting"

    # API Actions
    API_CALL = "api_call"

    # File/System Actions
    WRITE_FILE = "write_file"
    EXECUTE_COMMAND = "execute_command"

    # Input Actions
    KEY_PRESS = "key_press"
    AUDIO_CONTROL = "audio_control"

    # Game memory (crowd control)
    GAME_HOOK = "game_hook"

    # OBS Studio WebSocket controls
    OBS_CONTROL = "obs_control"


@dataclass
class TriggerCondition:
    """A single condition that must be met for a trigger to fire"""

    field: str  # The field to check (e.g., "amount", "username", "message")
    operator: ComparisonOperator
    value: Any  # The value to compare against
    case_sensitive: bool = True
    # How this condition joins the one before it: "and" | "or". Ignored on the
    # first condition. AND binds tighter than OR (see BaseTrigger.evaluate_conditions).
    join: str = "and"

    def evaluate(self, event_data: Dict[str, Any]) -> bool:
        """Evaluate this condition against event data"""
        try:
            field_value = self._get_field_value(event_data, self.field)
            return self._compare_values(field_value, self.value, self.operator)
        except Exception as e:
            logger.error(
                f"Error evaluating condition {self.field} {self.operator.value} {self.value}: {e}"
            )
            return False

    def _get_field_value(self, data: Dict[str, Any], field_path: str) -> Any:
        """Get value from nested data using dot notation (e.g., 'user.name')"""
        keys = field_path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    def _compare_values(
        self, field_value: Any, expected_value: Any, operator: ComparisonOperator
    ) -> bool:
        """Compare an event field with the condition value.

        * Missing field (None): ``not_equal`` / ``not_contains`` are True, all
          other operators False.
        * ``equal`` / ``not_equal``: boolean comparison only when the event value
          is a real ``bool`` (or the condition value is a ``bool`` and the event
          value is the text "true"/"false"); otherwise numeric when both sides
          parse as numbers, else text.
        * ``greater_than`` etc.: numeric only; False when either side is not a number.
        * Text operators (and text equality) honour ``case_sensitive``; regex uses
          ``re.IGNORECASE`` when it is off.
        """
        op = operator
        if field_value is None:
            return op in (ComparisonOperator.NOT_EQUAL, ComparisonOperator.NOT_CONTAINS)

        if op in (ComparisonOperator.EQUAL, ComparisonOperator.NOT_EQUAL):
            same = self._values_equal(field_value, expected_value)
            return same if op == ComparisonOperator.EQUAL else not same

        if op in (
            ComparisonOperator.GREATER_THAN,
            ComparisonOperator.GREATER_THAN_OR_EQUAL,
            ComparisonOperator.LESS_THAN,
            ComparisonOperator.LESS_THAN_OR_EQUAL,
        ):
            fv = _as_number(field_value)
            ev = _as_number(expected_value)
            if fv is None or ev is None:
                return False
            if op == ComparisonOperator.GREATER_THAN:
                return fv > ev
            if op == ComparisonOperator.GREATER_THAN_OR_EQUAL:
                return fv >= ev
            if op == ComparisonOperator.LESS_THAN:
                return fv < ev
            return fv <= ev

        fv_s = _as_text(field_value)
        ev_s = _as_text(expected_value)
        if op == ComparisonOperator.REGEX_MATCH:
            compiled = _compiled_regex_match(ev_s, ignore_case=not self.case_sensitive)
            if compiled is None:
                logger.error(f"Invalid regex pattern: {ev_s}")
                return False
            return bool(compiled.search(fv_s))

        if not self.case_sensitive:
            fv_s = fv_s.casefold()
            ev_s = ev_s.casefold()
        if op == ComparisonOperator.CONTAINS:
            return ev_s in fv_s
        if op == ComparisonOperator.NOT_CONTAINS:
            return ev_s not in fv_s
        if op == ComparisonOperator.STARTS_WITH:
            # Exact match on the first whitespace-delimited token
            tokens = fv_s.split(None, 1)
            return bool(tokens) and tokens[0] == ev_s
        if op == ComparisonOperator.BEGINS_WITH:
            return fv_s.startswith(ev_s)
        if op == ComparisonOperator.ENDS_WITH:
            return fv_s.endswith(ev_s)
        return False

    def _values_equal(self, field_value: Any, expected_value: Any) -> bool:
        if isinstance(field_value, bool):
            expected_bool = _as_bool(expected_value)
            if expected_bool is not None:
                return field_value == expected_bool
        elif isinstance(expected_value, bool) and isinstance(field_value, str):
            token = field_value.strip().lower()
            if token in ("true", "false"):
                return (token == "true") == expected_value
        fv = _as_number(field_value)
        ev = _as_number(expected_value)
        if fv is not None and ev is not None:
            return fv == ev
        fv_s = _as_text(field_value)
        ev_s = _as_text(expected_value)
        if not self.case_sensitive:
            return fv_s.casefold() == ev_s.casefold()
        return fv_s == ev_s


def _as_number(value: Any) -> Optional[float]:
    """Return a float for numbers and numeric text; None otherwise (bools excluded)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    return None


def _as_bool(value: Any) -> Optional[bool]:
    """Interpret a condition value as a bool for comparison with a bool event field."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        return None
    token = str(value).strip().lower()
    if token in ("true", "yes", "on", "1"):
        return True
    if token in ("false", "no", "off", "0"):
        return False
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def evaluate_condition_groups(
    conditions: List["TriggerCondition"], event_data: Dict[str, Any]
) -> bool:
    """AND/OR evaluation: split at each condition whose ``join`` is "or".

    AND binds tighter than OR, so ``A and B or C`` is ``(A and B) or C``. The
    first condition's ``join`` is ignored. No conditions means True.
    """
    if not conditions:
        return True
    groups: List[List[TriggerCondition]] = [[]]
    for index, condition in enumerate(conditions):
        join = str(getattr(condition, "join", "and") or "and").strip().lower()
        if index > 0 and join == "or":
            groups.append([])
        groups[-1].append(condition)
    return any(
        all(condition.evaluate(event_data) for condition in group)
        for group in groups
        if group
    )


def message_after_trigger_conditions(
    raw_message: str, conditions: List[TriggerCondition]
) -> str:
    """Remove matched condition literals from ``message`` for use in action placeholders.

    Mirrors case-sensitivity rules in :meth:`TriggerCondition._compare_values` for
    ``contains`` / ``starts_with`` / ``begins_with`` / ``ends_with`` on field
    ``message`` only.
    """
    msg = "" if raw_message is None else str(raw_message)
    for cond in conditions:
        if cond.field != "message":
            continue
        op = cond.operator
        if op not in (
            ComparisonOperator.CONTAINS,
            ComparisonOperator.STARTS_WITH,
            ComparisonOperator.BEGINS_WITH,
            ComparisonOperator.ENDS_WITH,
        ):
            continue
        expected = cond.value
        if expected is None:
            continue
        exp = str(expected)
        if not exp:
            continue
        case_sensitive = cond.case_sensitive
        if op == ComparisonOperator.CONTAINS:
            if case_sensitive:
                if exp in msg:
                    msg = msg.replace(exp, "", 1)
            else:
                low, sub = msg.lower(), exp.lower()
                i = low.find(sub)
                if i >= 0:
                    msg = (msg[:i] + msg[i + len(exp) :]).strip()
        elif op == ComparisonOperator.STARTS_WITH:
            # First-word match: strip the first token when it equals the expected value
            parts = msg.split(None, 1)
            if parts:
                check_tok = parts[0] if case_sensitive else parts[0].lower()
                check_e = exp if case_sensitive else exp.lower()
                if check_tok == check_e:
                    msg = parts[1] if len(parts) > 1 else ""
        elif op == ComparisonOperator.BEGINS_WITH:
            check_m = msg if case_sensitive else msg.lower()
            check_e = exp if case_sensitive else exp.lower()
            if check_m.startswith(check_e):
                msg = msg[len(exp) :]
        elif op == ComparisonOperator.ENDS_WITH:
            check_m = msg if case_sensitive else msg.lower()
            check_e = exp if case_sensitive else exp.lower()
            if check_m.endswith(check_e):
                msg = msg[: len(msg) - len(exp)]
        msg = msg.strip()
    return msg


@dataclass
class BaseTrigger(ABC):
    """Base class for all trigger types"""

    trigger_id: str
    trigger_type: TriggerType
    name: str
    description: str = ""
    enabled: bool = True
    conditions: List[TriggerCondition] = field(default_factory=list)
    cooldown_seconds: int = 0  # Minimum time between trigger activations
    last_triggered: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_trigger(self) -> bool:
        """Check if trigger is enabled and not on cooldown"""
        if not self.enabled:
            return False

        if self.cooldown_seconds > 0:
            time_since_last = time.time() - self.last_triggered
            if time_since_last < self.cooldown_seconds:
                return False

        return True

    def evaluate_conditions(self, event_data: Dict[str, Any]) -> bool:
        """Evaluate all conditions for this trigger"""
        # AND/OR groups; no conditions means always trigger
        return evaluate_condition_groups(self.conditions, event_data)

    @abstractmethod
    def should_trigger(self, event_data: Dict[str, Any]) -> bool:
        """Determine if this trigger should fire based on event data"""
        pass

    def trigger(self, event_data: Dict[str, Any]) -> bool:
        """Attempt to trigger this trigger"""
        if not self.can_trigger():
            return False

        if not self.should_trigger(event_data):
            return False

        self.last_triggered = time.time()
        logger.info(f"Trigger '{self.name}' ({self.trigger_id}) fired")
        return True


@dataclass
class BaseAction(ABC):
    """Base class for all action types"""

    action_id: str
    action_type: ActionType
    name: str
    description: str = ""
    enabled: bool = True
    delay_seconds: float = 0
    parameters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @abstractmethod
    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute this action"""
        pass

    def validate_parameters(self) -> bool:
        """Validate that required parameters are present and valid"""
        return True


@dataclass
class Connector:
    """A connector that links a trigger to one or more actions"""

    connector_id: str
    name: str
    description: str = ""
    enabled: bool = True
    trigger: BaseTrigger = None
    actions: List[BaseAction] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_triggered: float = 0
    trigger_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def _game_hook_prerequisites_unmet(self) -> bool:
        """If any enabled game-hook action's hook is off or not attached, skip the whole connector."""
        game_ids: set = set()
        for action in self.actions:
            if not action.enabled:
                continue
            if action.action_type != ActionType.GAME_HOOK:
                continue
            gid = str(getattr(action, "game_id", "ff7") or "ff7").strip() or "ff7"
            game_ids.add(gid)
        if not game_ids:
            return False
        from .game_hooks_service import game_hooks_service

        for gid in sorted(game_ids):
            if not game_hooks_service.is_game_hook_ready(gid):
                logger.debug(
                    "Connector '%s' skipped: game hook %r not ready",
                    self.name,
                    gid,
                )
                return True
        return False

    async def process_event(self, event_data: Dict[str, Any], *, report_failures: bool = True) -> bool:
        """Process an event and potentially trigger actions.

        Actions that fail are collected in ``self.last_action_failures`` and, when
        *report_failures* is True (live events), each one is shown as a notification
        (deduplicated per connector and action). Tests pass False and show the list.
        """
        self.last_action_failures = []
        if not self.enabled or not self.trigger:
            return False

        if self._game_hook_prerequisites_unmet():
            return False

        # Check if trigger should fire
        trigger_result = self.trigger.trigger(event_data)
        if not trigger_result:
            return False

        # Update connector stats
        self.last_triggered = time.time()
        self.trigger_count += 1

        # Execute all actions; action_results (1-based slot keys) is filled by actions
        # that produce outputs, e.g. game hook query_inventory, for {actionN.field} placeholders.
        success_count = 0
        msg_after = message_after_trigger_conditions(
            str(event_data.get("message", "")), self.trigger.conditions
        )
        action_results: Dict[str, Dict[str, Any]] = {}
        enabled_slots = [
            (idx + 1, act)
            for idx, act in enumerate(self.actions)
            if act.enabled
        ]
        for _seq_index, (action_index, action) in enumerate(enabled_slots):
            delay = float(getattr(action, "delay_seconds", 0) or 0)
            if delay > 0:
                await asyncio.sleep(delay)
            slot: Dict[str, Any] = {"connector_id": self.connector_id, "connector_name": self.name}
            token = _action_problem.set(slot)
            success = False
            try:
                success = await action.execute(
                    trigger_data={
                        "trigger_id": self.trigger.trigger_id,
                        "trigger_type": self.trigger.trigger_type.value,
                        "connector_id": self.connector_id,
                        "connector_name": self.name,
                        # Counted after the increment above: 1 on the first run.
                        "trigger_count": self.trigger_count,
                        "triggered_at": self.last_triggered,
                        "message_after_conditions": msg_after,
                        "action_index": action_index,
                        "action_results": action_results,
                    },
                    event_data=event_data,
                )
                if success:
                    success_count += 1
            except Exception as e:
                logger.error(
                    f"Error executing action '{action.name}' in connector '{self.name}': {e}",
                    exc_info=True,
                )
                slot.setdefault("reason", "it stopped with an error")
                slot.setdefault("details", f"{type(e).__name__}: {e}")
            finally:
                _action_problem.reset(token)
            if not success and not slot.get("skipped"):
                details = slot.get("details") or "\n".join(slot.get("log") or [])
                failure = {
                    "index": action_index,
                    "label": action_label(action),
                    "reason": slot.get("reason", ""),
                    "hint": slot.get("hint", ""),
                    "details": details,
                }
                self.last_action_failures.append(failure)
                if report_failures:
                    _notify_action_failure(self, failure)

        logger.info(
            f"Connector '{self.name}' executed {success_count}/{len(self.actions)} actions"
        )
        return success_count > 0


def normalize_sub_tier(raw: Any) -> int:
    """Map a Twitch sub tier to 1, 2 or 3.

    Accepts plan codes ``"1000"``/``"2000"``/``"3000"`` (str or int), ``1``/``2``/``3``
    and ``"prime"`` (Prime Gaming subs count as tier 1). Anything else is 1.
    """
    if isinstance(raw, bool) or raw is None:
        return 1
    text = str(getattr(raw, "value", raw)).strip().lower()
    if text == "prime":
        return 1
    try:
        value = int(float(text))
    except ValueError:
        return 1
    if value in (1000, 2000, 3000):
        return value // 1000
    if value in (1, 2, 3):
        return value
    return 1


class EventData:
    """Standardized event data structure for the connector system"""

    @staticmethod
    def from_twitch_bits(
        bits_amount: int, username: str, message: str = "", **kwargs
    ) -> Dict[str, Any]:
        """Create event data from Twitch bits event"""
        return {
            "event_type": "twitch_bits",
            "amount": bits_amount,
            "username": username,
            "message": message,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_sub(
        tier: int, username: str, message: str = "", months: int = 1, **kwargs
    ) -> Dict[str, Any]:
        """Create event data from Twitch subscription event"""
        return {
            "event_type": "twitch_sub",
            "tier": normalize_sub_tier(tier),
            "username": username,
            "message": message,
            "months": months,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_resub(
        tier: Any,
        username: str,
        message: str = "",
        months: int = 1,
        streak_months: int = 0,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create event data from a Twitch resubscription (``months`` = cumulative)."""
        return {
            "event_type": "twitch_resub",
            "tier": normalize_sub_tier(tier),
            "username": username,
            "message": message or "",
            "months": months,
            "streak_months": streak_months or 0,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_giftsub(
        tier: Any,
        username: str,
        gift_count: int = 1,
        is_anonymous: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create event data from a Twitch gift-sub event (``username`` = the gifter)."""
        return {
            "event_type": "twitch_giftsub",
            "tier": normalize_sub_tier(tier),
            "username": username,
            "gift_count": gift_count,
            "is_anonymous": bool(is_anonymous),
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_follow(username: str, **kwargs) -> Dict[str, Any]:
        """Create event data from Twitch follow event"""
        return {
            "event_type": "twitch_follow",
            "username": username,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_raid(username: str, viewer_count: int, **kwargs) -> Dict[str, Any]:
        """Create event data from Twitch raid event"""
        return {
            "event_type": "twitch_raid",
            "username": username,
            "viewer_count": viewer_count,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_twitch_chat(
        username: str,
        message: str,
        is_command: bool = False,
        command: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """Create event data from Twitch chat message"""
        return {
            "event_type": "twitch_chat_message",
            "username": username,
            "message": message,
            "is_command": is_command,
            "command": command,
            "timestamp": time.time(),
            "source": "twitch",
            **kwargs,
        }

    @staticmethod
    def from_donation(
        amount: float, username: str, message: str = "", currency: str = "USD", **kwargs
    ) -> Dict[str, Any]:
        """Create event data from donation event"""
        return {
            "event_type": "donation",
            "amount": amount,
            "username": username,
            "message": message,
            "currency": currency,
            "timestamp": time.time(),
            "source": kwargs.get("source", "donation"),
            **kwargs,
        }

    @staticmethod
    def from_youtube_chat(
        username: str, message: str, user_id: str = "", **kwargs
    ) -> Dict[str, Any]:
        return {
            "event_type": "youtube_chat_message",
            "username": username,
            "message": message,
            "user_id": user_id,
            "timestamp": time.time(),
            "source": "youtube",
            **kwargs,
        }

    @staticmethod
    def from_youtube_member(
        username: str, member_level: str = "", message: str = "", **kwargs
    ) -> Dict[str, Any]:
        return {
            "event_type": "youtube_member",
            "username": username,
            "member_level": member_level,
            "message": message,
            "timestamp": time.time(),
            "source": "youtube",
            **kwargs,
        }

    @staticmethod
    def from_youtube_member_milestone(
        username: str,
        months: int = 1,
        member_level: str = "",
        message: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        return {
            "event_type": "youtube_member_milestone",
            "username": username,
            "months": months,
            "member_level": member_level,
            "message": message,
            "timestamp": time.time(),
            "source": "youtube",
            **kwargs,
        }

    @staticmethod
    def from_youtube_gift_membership(
        username: str,
        gift_count: int = 1,
        member_level: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        return {
            "event_type": "youtube_gift_membership",
            "username": username,
            "gift_count": gift_count,
            "quantity": gift_count,
            "member_level": member_level,
            "timestamp": time.time(),
            "source": "youtube",
            **kwargs,
        }

    @staticmethod
    def from_youtube_superchat(
        username: str,
        amount: float,
        currency: str = "USD",
        message: str = "",
        display_amount: str = "",
        event_type: str = "youtube_superchat",
        **kwargs,
    ) -> Dict[str, Any]:
        return {
            "event_type": event_type,
            "username": username,
            "amount": amount,
            "currency": currency,
            "message": message,
            "display_amount": display_amount,
            "timestamp": time.time(),
            "source": "youtube",
            **kwargs,
        }

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch Predictions for Chatbot → Predictions and the Prediction overlay widget.

Each overlay that holds a Prediction widget keeps its own setup (question, outcomes, betting
time, how the result is announced). Twitch runs one prediction per channel, so there is at most
one live prediction; it belongs to the overlay that started it. A prediction started on the
Twitch dashboard is shown on the overlay used last (or the first one).

Twitch is the source of truth: Helix starts, locks, resolves and cancels, and EventSub updates
the numbers. Only the setup is saved; a running prediction is read back from Twitch.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import twitch_predictions as tp

logger = logging.getLogger(__name__)

DATA_PATH = "BotData/Predictions"
DEFAULT_RESULT_MESSAGE = "{winner} won! {points} points go to {winners} predictors."
DEFAULT_REFUND_MESSAGE = "The prediction “{title}” was canceled and everyone got their points back."
LIVE_STATUSES = ("active", "locked")
ENDED_STATUSES = ("resolved", "canceled")

DEFAULT_CONFIG: Dict[str, Any] = {
    "title": "",
    "outcomes": [],
    "window_seconds": 120,
    "announce_banner": False,
    "announce_chat": True,
    "result_message": DEFAULT_RESULT_MESSAGE,
    "refund_message": DEFAULT_REFUND_MESSAGE,
    "banner_duration": 10,
    "last_result": None,
}


def _outcome(name: str = "") -> Dict[str, Any]:
    return {"id": uuid.uuid4().hex[:12], "name": tp.clamp_outcome_name(name)}


def default_outcomes() -> List[Dict[str, Any]]:
    return [_outcome("Yes"), _outcome("No")]


# ---------------------------------------------------------------------------
#  Overlays with a Prediction widget
# ---------------------------------------------------------------------------


def _walk(elements: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for el in elements if isinstance(elements, list) else []:
        if isinstance(el, dict):
            found.append(el)
            found.extend(_walk(el.get("children")))
    return found


def has_prediction_widget(model: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(model, dict):
        return False
    return any(str(el.get("type") or "").lower() == "prediction" for el in _walk(model.get("elements")))


def list_prediction_template_names() -> List[str]:
    """Template names whose Spore sidecar contains a Prediction widget."""
    try:
        from .path_utils import get_template_path
        from .spore_studio.template_parser_back import load_sidecar
    except Exception as exc:
        logger.debug("prediction template scan import failed: %s", exc)
        return []
    folder = get_template_path("_spore")
    try:
        entries = os.listdir(folder) if os.path.isdir(folder) else []
    except OSError as exc:
        logger.debug("prediction template folder unreadable: %s", exc)
        return []
    names = []
    for filename in entries:
        stem = filename[: -len(".spore.json")] if filename.endswith(".spore.json") else ""
        if stem and not stem.startswith("_") and has_prediction_widget(load_sidecar(stem)):
            names.append(stem)
    return sorted(names)


def format_message(text: str, snapshot: Dict[str, Any]) -> str:
    """Fill {title}, {winner}, {points}, {winners} (predictors who won), {users}, {top} (biggest winner)."""
    winner = next((o for o in snapshot.get("outcomes") or [] if o.get("winner")), None)
    top = ""
    if winner and winner.get("top_predictors"):
        best = max(winner["top_predictors"], key=lambda p: p.get("points_won") or p.get("points_used") or 0)
        top = best.get("name") or ""
    values = {
        "title": snapshot.get("title") or "",
        "winner": (winner or {}).get("name") or "",
        "points": f"{int(snapshot.get('total_points') or 0):,}",
        "winners": f"{int((winner or {}).get('users') or 0):,}",
        "users": f"{int(snapshot.get('total_users') or 0):,}",
        "top": top,
    }
    out = str(text or "")
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out.strip()


_manager: Optional["PredictionManager"] = None
_manager_lock = threading.Lock()


def get_prediction_manager() -> "PredictionManager":
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PredictionManager()
        return _manager


class PredictionManager:
    """Thread-safe prediction setups (per overlay) and the one live prediction."""

    def __init__(self, *, persist: bool = True):
        self._lock = threading.RLock()
        self._persist_enabled = persist
        self._configs: Dict[str, Dict[str, Any]] = {}
        self._last_template: str = ""
        self._live: Optional[Dict[str, Any]] = None  # normalised prediction + "template"
        self._last_error: Dict[str, str] = {}
        self._load_failed = False
        if persist:
            self._load()

    # ----- persistence -----

    def _load(self) -> None:
        raw, ok = self._read()
        if not ok:
            self._load_failed = True
            logger.warning("%s could not be read; it will be re-read before it is saved", DATA_PATH)
        with self._lock:
            self._apply_stored(raw)

    @staticmethod
    def _read() -> Tuple[Any, bool]:
        try:
            from .database_manager import get_data_checked

            return get_data_checked(DATA_PATH)
        except Exception as exc:
            logger.warning("Prediction setup load failed: %s", exc)
            return None, False

    def _apply_stored(self, raw: Any) -> None:
        templates = raw.get("templates") if isinstance(raw, dict) else None
        for name, entry in (templates or {}).items() if isinstance(templates, dict) else []:
            name = str(name or "").strip()
            if name and isinstance(entry, dict) and name not in self._configs:
                self._configs[name] = self._normalize_config(entry.get("config"))
        if isinstance(raw, dict) and not self._last_template:
            self._last_template = str(raw.get("last_template") or "")

    def _persist(self) -> None:
        """Save the setups. Caller holds ``_lock``. Never writes over data that couldn't be read."""
        if not self._persist_enabled:
            return
        if self._load_failed:
            raw, ok = self._read()
            if not ok:
                return  # still unreadable: keep the change in memory; the next change retries
            self._apply_stored(raw)
            self._load_failed = False
        payload = {
            "templates": {name: {"config": copy.deepcopy(cfg)} for name, cfg in self._configs.items()},
            "last_template": self._last_template,
        }
        try:
            from .database_manager import set_data

            set_data(DATA_PATH, payload)
        except Exception as exc:
            logger.error("Saving predictions failed: %s", exc, exc_info=True)

    @staticmethod
    def _normalize_config(raw: Any) -> Dict[str, Any]:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        if isinstance(raw, dict):
            for key in DEFAULT_CONFIG:
                if key in raw:
                    cfg[key] = copy.deepcopy(raw[key])
        cfg["title"] = tp.clamp_title(cfg.get("title"))
        outcomes = []
        for o in cfg.get("outcomes") or []:
            if isinstance(o, dict):
                outcomes.append({"id": str(o.get("id") or uuid.uuid4().hex[:12]), "name": tp.clamp_outcome_name(o.get("name"))})
        cfg["outcomes"] = outcomes[: tp.OUTCOMES_MAX] or default_outcomes()
        cfg["window_seconds"] = tp.clamp_window(cfg.get("window_seconds"))
        try:
            cfg["banner_duration"] = max(1, min(120, int(cfg.get("banner_duration") or 10)))
        except (TypeError, ValueError):
            cfg["banner_duration"] = 10
        for key in ("announce_banner", "announce_chat"):
            cfg[key] = bool(cfg.get(key))
        for key in ("result_message", "refund_message"):
            cfg[key] = str(cfg.get(key) or "")
        return cfg

    # ----- setup (per overlay) -----

    def ensure_template(self, template: str) -> Dict[str, Any]:
        template = str(template or "").strip()
        with self._lock:
            if template not in self._configs:
                self._configs[template] = self._normalize_config(None)
            return copy.deepcopy(self._configs[template])

    def get_config(self, template: str) -> Dict[str, Any]:
        return self.ensure_template(template)

    def update_config(self, template: str, **changes: Any) -> Dict[str, Any]:
        """Change setup fields (unknown keys are ignored) and save."""
        with self._lock:
            cfg = copy.deepcopy(self._configs.get(template) or self._normalize_config(None))
            for key, value in changes.items():
                if key in DEFAULT_CONFIG and key != "last_result":
                    cfg[key] = value
            self._configs[template] = self._normalize_config(cfg)
            self._persist()
            return copy.deepcopy(self._configs[template])

    def add_outcome(self, template: str, name: str = "") -> Tuple[bool, str]:
        cfg = self.get_config(template)
        if len(cfg["outcomes"]) >= tp.OUTCOMES_MAX:
            return False, f"Twitch predictions have at most {tp.OUTCOMES_MAX} outcomes."
        cfg["outcomes"].append(_outcome(name or f"Outcome {len(cfg['outcomes']) + 1}"))
        self.update_config(template, outcomes=cfg["outcomes"])
        return True, ""

    def remove_outcome(self, template: str, outcome_id: str) -> Tuple[bool, str]:
        cfg = self.get_config(template)
        if len(cfg["outcomes"]) <= tp.OUTCOMES_MIN:
            return False, f"A prediction needs at least {tp.OUTCOMES_MIN} outcomes."
        self.update_config(template, outcomes=[o for o in cfg["outcomes"] if o["id"] != outcome_id])
        return True, ""

    def rename_outcome(self, template: str, outcome_id: str, name: str) -> None:
        cfg = self.get_config(template)
        for o in cfg["outcomes"]:
            if o["id"] == outcome_id:
                o["name"] = tp.clamp_outcome_name(name)
        self.update_config(template, outcomes=cfg["outcomes"])

    def get_last_error(self, template: str) -> str:
        with self._lock:
            return self._last_error.get(template, "")

    # ----- the live prediction -----

    def live(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._live)

    def live_template(self) -> str:
        with self._lock:
            live = self._live
            return live["template"] if live and live.get("status") in LIVE_STATUSES else ""

    def _fail(self, template: str, message: str) -> Tuple[bool, str]:
        with self._lock:
            self._last_error[template] = message
        return False, message

    def start_prediction(self, template: str) -> Tuple[bool, str]:
        """Start this overlay's prediction on Twitch. (ok, message)."""
        config = self.get_config(template)
        running = self.live_template()
        if running:
            return self._fail(template, f"A prediction is already running (“{running}”). End it first.")
        problems = tp.config_problems(config)
        if problems:
            return self._fail(template, problems[0])
        ready = tp.readiness()
        if ready != "ok":
            return self._fail(template, tp.readiness_message(ready))
        ok, result = tp.create_prediction(config["title"], [o["name"] for o in config["outcomes"]], config["window_seconds"])
        if not ok:
            return self._fail(template, str(result))
        snapshot = None
        with self._lock:
            self._last_error.pop(template, None)
            self._last_template = template
            live = tp.normalize(result)
            live["status"] = "active"
            live["template"] = template
            self._live = live
            self._persist()
            snapshot = self._snapshot_locked(template)
        self._emit("prediction_state", snapshot)
        self._dispatch("begin", snapshot)
        return True, "Prediction started."

    def _live_id(self, template: Optional[str] = None) -> Tuple[Optional[str], str]:
        with self._lock:
            live = self._live
            if not live or live.get("status") not in LIVE_STATUSES:
                return None, "No prediction is running."
            if template and live.get("template") != template:
                return None, "This overlay isn't running the prediction."
            return live["id"], ""

    def lock_prediction(self, template: Optional[str] = None) -> Tuple[bool, str]:
        """Stop taking bets now."""
        prediction_id, problem = self._live_id(template)
        if not prediction_id:
            return False, problem
        if (self._live or {}).get("status") == "locked":
            return True, "Bets are already locked."
        ok, message = tp.end_prediction(prediction_id, "LOCKED")
        if ok:
            self._set_status("locked")
        return ok, message or "Bets locked."

    def resolve_prediction(self, outcome: str, template: Optional[str] = None) -> Tuple[bool, str]:
        """Pay out to ``outcome`` (an outcome id, name or 1-based number)."""
        prediction_id, problem = self._live_id(template)
        if not prediction_id:
            return False, problem
        winner = self._find_outcome(outcome)
        if winner is None:
            return False, f"No outcome matches “{outcome}”."
        ok, message = tp.end_prediction(prediction_id, "RESOLVED", winner["id"])
        return ok, message or f"“{winner['name']}” won."

    def cancel_prediction(self, template: Optional[str] = None) -> Tuple[bool, str]:
        """Cancel and refund every bet."""
        prediction_id, problem = self._live_id(template)
        if not prediction_id:
            return False, problem
        ok, message = tp.end_prediction(prediction_id, "CANCELED")
        return ok, message or "Prediction canceled; points refunded."

    def _find_outcome(self, needle: str) -> Optional[Dict[str, Any]]:
        text = str(needle or "").strip()
        with self._lock:
            outcomes = list((self._live or {}).get("outcomes") or [])
        for o in outcomes:
            if o["id"] == text:
                return o
        for o in outcomes:
            if o["name"].strip().lower() == text.lower():
                return o
        if text.isdigit() and 1 <= int(text) <= len(outcomes):
            return outcomes[int(text) - 1]
        return None

    def _set_status(self, status: str) -> None:
        with self._lock:
            if not self._live:
                return
            self._live["status"] = status
            if status == "locked" and not self._live.get("locked_at"):
                self._live["locked_at"] = time.time()
            snapshot = self._snapshot_locked(self._live["template"])
        self._emit("prediction_state", snapshot)

    def _default_template_locked(self) -> str:
        names = list_prediction_template_names()
        if self._last_template and (not names or self._last_template in names):
            return self._last_template
        return names[0] if names else (self._last_template or "")

    def apply_twitch_update(self, update: Dict[str, Any], *, kind: str = "") -> None:
        """EventSub (or a Twitch read-back) changed the prediction."""
        prediction_id = update.get("id")
        if not prediction_id:
            return
        with self._lock:
            live = self._live
            if live and live.get("id") == prediction_id:
                template = live["template"]
            else:
                template = self._default_template_locked()
            previous_status = (live or {}).get("status") if live and live.get("id") == prediction_id else None
            merged = dict(update)
            if live and live.get("id") == prediction_id:
                # Keep what an event leaves out (the lock event has no locked_at; begin has no top predictors).
                for key in ("started_at", "locks_at", "locked_at", "title"):
                    if not merged.get(key) and live.get(key):
                        merged[key] = live[key]
            merged["template"] = template
            self._live = merged
            ended = merged["status"] in ENDED_STATUSES and previous_status not in ENDED_STATUSES
            snapshot = self._snapshot_locked(template)
            if ended and template:
                cfg = self._configs.get(template) or self._normalize_config(None)
                winner = next((o for o in snapshot["outcomes"] if o["winner"]), None)
                cfg["last_result"] = {
                    "title": snapshot["title"],
                    "status": merged["status"],
                    "winner": (winner or {}).get("name"),
                    "points": snapshot["total_points"],
                    "users": snapshot["total_users"],
                    "ended_at": merged.get("ended_at") or time.time(),
                }
                self._configs[template] = cfg
                self._persist()
        self._emit("prediction_state", snapshot)
        if ended:
            self._emit("prediction_ended", snapshot)
            self._announce(template, snapshot)
            self._record_stats(snapshot)
        self._dispatch(kind or merged["status"], snapshot)

    def sync_from_twitch(self) -> bool:
        """Pick up a prediction that is running on Twitch (after a restart or a dashboard start)."""
        row = tp.get_current_prediction()
        if not row:
            return False
        update = tp.normalize(row)
        if update["status"] not in LIVE_STATUSES:
            return False
        self.apply_twitch_update(update, kind="sync")
        return True

    # ----- overlay -----

    def get_overlay_state(self, template: str) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(str(template or ""))

    def _snapshot_locked(self, template: str) -> Dict[str, Any]:
        now = time.time()
        live = self._live if self._live and self._live.get("template") == template else None
        if live is None:
            cfg = self._configs.get(template) or self._normalize_config(None)
            outcomes = [
                {"id": o["id"], "name": o["name"], "color": tp.OUTCOME_COLORS[min(i, 1)], "users": 0, "points": 0,
                 "percent": 0.0, "user_percent": 0.0, "ratio": None, "winner": False, "top_predictors": []}
                for i, o in enumerate(cfg["outcomes"])
            ]
            return {"template": template, "status": "idle", "title": cfg["title"], "prediction_id": "",
                    "outcomes": outcomes, "total_points": 0, "total_users": 0, "locks_at": None, "locked_at": None,
                    "ended_at": None, "server_now": now, "remaining_seconds": None, "winning_outcome_id": None}
        total_points = sum(o["points"] for o in live["outcomes"])
        total_users = sum(o["users"] for o in live["outcomes"])
        winning = live.get("winning_outcome_id") if live["status"] == "resolved" else None
        outcomes = []
        for o in live["outcomes"]:
            outcomes.append({
                **copy.deepcopy(o),
                "percent": round(o["points"] * 100 / total_points, 1) if total_points else 0.0,
                "user_percent": round(o["users"] * 100 / total_users, 1) if total_users else 0.0,
                "ratio": round(total_points / o["points"], 2) if o["points"] else None,
                "winner": bool(winning and o["id"] == winning),
            })
        locks_at = live.get("locks_at")
        remaining = max(0.0, locks_at - now) if live["status"] == "active" and locks_at else None
        return {"template": template, "status": live["status"], "title": live.get("title") or "",
                "prediction_id": live["id"], "outcomes": outcomes, "total_points": total_points,
                "total_users": total_users, "locks_at": locks_at, "locked_at": live.get("locked_at"),
                "ended_at": live.get("ended_at"), "server_now": now, "remaining_seconds": remaining,
                "winning_outcome_id": winning}

    # ----- side effects -----

    def _announce(self, template: str, snapshot: Dict[str, Any]) -> None:
        cfg = self.get_config(template) if template else self._normalize_config(None)
        text = cfg["result_message"] if snapshot["status"] == "resolved" else cfg["refund_message"]
        message = format_message(text, snapshot)
        if not message:
            return
        if cfg["announce_banner"]:
            try:
                from .chat_banner import emit_chat_banner

                emit_chat_banner(message, duration=float(cfg["banner_duration"]), source="prediction")
            except Exception as exc:
                logger.error("Prediction result banner failed: %s", exc, exc_info=True)
        if cfg["announce_chat"]:
            try:
                from .chatbot import dispatch_chatbot_response

                dispatch_chatbot_response(message, ["twitch"])
            except Exception as exc:
                logger.error("Prediction result chat message failed: %s", exc, exc_info=True)

    def _record_stats(self, snapshot: Dict[str, Any]) -> None:
        try:
            from .statistics_manager import get_statistics_manager

            recorder = getattr(get_statistics_manager(), "record_prediction_finished", None)
            if recorder is not None:
                recorder(snapshot)
        except Exception as exc:
            logger.debug("prediction stats: %s", exc)

    def _dispatch(self, kind: str, snapshot: Dict[str, Any]) -> None:
        """Connector triggers for prediction begin / lock / end."""
        try:
            from . import connector_integration

            handler = getattr(connector_integration, "handle_twitch_prediction", None)
            if handler is not None:
                handler(kind, snapshot)
        except Exception as exc:
            logger.debug("prediction connector dispatch: %s", exc)

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            from . import web_engine

            if web_engine.web_engine_instance:
                web_engine.web_engine_instance.safe_emit(event, data)
        except Exception as exc:
            logger.debug("prediction emit %s: %s", event, exc)

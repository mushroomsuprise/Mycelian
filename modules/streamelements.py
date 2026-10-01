# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""StreamElements tips over the Astro WebSocket.

The old realtime.streamelements.com gateway is Socket.IO v2 and drops modern
clients before any tip arrives. Astro is a plain WebSocket. Only tip topics
are subscribed, so follows and subs stay on the Twitch connection.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import threading
import time
import uuid
from typing import Any, Optional
from urllib.parse import quote

import certifi

import requests
import websocket

from .donation_settings import clear_secret, get_settings, set_status, update_settings

logger = logging.getLogger(__name__)

ASTRO_URL = "wss://astro.streamelements.com/"
REALTIME_URL = "wss://realtime.streamelements.com/socket.io/?EIO=4&transport=websocket"
CHANNELS_ME = "https://api.streamelements.com/kappa/v2/channels/me"
TIP_TOPICS = ("channel.tips", "channel.tips.moderation")

_client: Optional["StreamElementsClient"] = None
_start_lock = threading.Lock()


_JWT_EXPIRED_NOTIFY_COOLDOWN_SEC = 45 * 60
_jwt_expired_last_notified = 0.0


def jwt_payload(token: str) -> dict:
    """Decode a JWT payload. The signature is not checked."""
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def jwt_is_expired(token: str) -> bool:
    """True when the JWT has an ``exp`` claim in the past."""
    exp = jwt_payload(token).get("exp")
    if exp is None:
        return False
    try:
        return time.time() >= float(exp)
    except (TypeError, ValueError):
        return False


def notify_streamelements_jwt_expired() -> None:
    """Tell the user once per cooldown that the StreamElements JWT needs replacing."""
    global _jwt_expired_last_notified
    now = time.time()
    if now - _jwt_expired_last_notified < _JWT_EXPIRED_NOTIFY_COOLDOWN_SEC:
        return
    _jwt_expired_last_notified = now
    try:
        from .notification_engine import nav_actions_settings, notify

        notify(
            "StreamElements JWT expired. Open Settings → StreamElements and paste a new token.",
            type="warning",
            timeout=10000,
            dedupe_key="streamelements:jwt_expired",
            dedupe_cooldown_sec=_JWT_EXPIRED_NOTIFY_COOLDOWN_SEC,
            actions=nav_actions_settings("StreamElements"),
        )
    except Exception:
        logger.debug("StreamElements JWT expiry notification failed", exc_info=True)


def channel_id_from_jwt(token: str) -> str:
    """Read the channel id from a StreamElements JWT payload. Signature is not checked."""
    data = jwt_payload(token)
    if not data:
        return ""
    for key in ("channel", "channelId", "channel_id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for inner in ("_id", "id"):
                inner_value = value.get(inner)
                if inner_value:
                    return str(inner_value).strip()
    return ""


def parse_socketio_message(packet: str) -> Optional[tuple]:
    """Return ``(event_name, payload)`` for a Socket.IO v2 event packet."""
    if not isinstance(packet, str) or not packet.startswith("42"):
        return None
    try:
        data = json.loads(packet[2:])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], str):
        return None
    payload = data[1] if len(data) > 1 else None
    return data[0], payload


def _tip_amount(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_realtime_tip(payload: Any) -> Optional[dict]:
    """Pull a cash tip out of a realtime ``event`` or overlay ``event:test`` packet.

    Overlay Emulate sends ``listener: tip-latest``. Real activity on the same
    socket uses ``type: tip``. Follows, subs, cheers, and other listeners are ignored.
    """
    if not isinstance(payload, dict):
        return None
    listener = str(payload.get("listener") or "")
    event_type = str(payload.get("type") or "")
    if listener == "tip-latest":
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        nested = event.get("data") if isinstance(event.get("data"), dict) else {}
        username = (
            event.get("name")
            or event.get("username")
            or event.get("displayName")
            or nested.get("username")
            or nested.get("displayName")
            or "Anonymous"
        )
        message = event.get("message")
        if message is None:
            message = nested.get("message") or ""
        currency = str(event.get("currency") or nested.get("currency") or "").strip().upper()
        tip_id = str(
            event.get("_id") or nested.get("_id") or payload.get("_id") or ""
        ).strip()
        return {
            "username": str(username).strip() or "Anonymous",
            "amount": _tip_amount(event.get("amount", nested.get("amount"))),
            "currency": currency,
            "message": str(message or ""),
            "event_id": tip_id,
        }
    if event_type != "tip":
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    username = data.get("username") or data.get("displayName") or payload.get("name") or "Anonymous"
    message = data.get("message") or ""
    currency = str(data.get("currency") or payload.get("currency") or "").strip().upper()
    tip_id = str(data.get("_id") or payload.get("_id") or payload.get("activityId") or "").strip()
    return {
        "username": str(username).strip() or "Anonymous",
        "amount": _tip_amount(data.get("amount", payload.get("amount"))),
        "currency": currency,
        "message": str(message or ""),
        "event_id": tip_id,
    }


def classify_tip(data: Any) -> str:
    """Return ``alert``, ``hold``, or ``drop`` for a tip payload."""
    if not isinstance(data, dict):
        return "drop"
    approved = str(data.get("approved") or "").strip().lower()
    status = str(data.get("status") or "success").strip().lower()
    if approved == "rejected" or (status and status != "success"):
        return "drop"
    if approved == "pending":
        return "hold"
    return "alert"


def normalize_streamelements_tip(data: dict) -> dict:
    donation = data.get("donation") if isinstance(data.get("donation"), dict) else {}
    user = donation.get("user") if isinstance(donation.get("user"), dict) else {}
    username = str(user.get("username") or "").strip() or "Anonymous"
    message = donation.get("message") or ""
    if message is None:
        message = ""
    try:
        amount = float(donation.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    currency = str(donation.get("currency") or "USD").strip().upper() or "USD"
    tip_id = str(data.get("_id") or data.get("transactionId") or "").strip()
    return {
        "username": username,
        "amount": amount,
        "currency": currency,
        "message": str(message),
        "event_id": tip_id,
    }


class TipGate:
    """Alert a tip once, after moderation allows it."""

    def __init__(self) -> None:
        self._alerted: set[str] = set()
        self._pending: dict[str, dict] = {}

    def consider(self, data: Any) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        decision = classify_tip(data)
        tip_id = str(data.get("_id") or "").strip()
        if decision == "drop":
            if tip_id:
                self._pending.pop(tip_id, None)
            return None
        if decision == "hold":
            if tip_id:
                self._pending[tip_id] = data
            return None
        if tip_id and tip_id in self._alerted:
            return None
        if tip_id:
            self._alerted.add(tip_id)
            self._pending.pop(tip_id, None)
        return normalize_streamelements_tip(data)


class StreamElementsClient:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._realtime_thread: Optional[threading.Thread] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._realtime_ws: Optional[websocket.WebSocketApp] = None
        self._gate = TipGate()
        self._reconnect_token = ""
        self._realtime_authenticated = False
        self._lock = threading.Lock()

    def status(self) -> str:
        return get_settings().streamelements_connection_status or "Disconnected"

    def last_error(self) -> str:
        return get_settings().streamelements_last_error or ""

    def start(self) -> None:
        with self._lock:
            astro_alive = bool(self._thread and self._thread.is_alive())
            realtime_alive = bool(self._realtime_thread and self._realtime_thread.is_alive())
            if astro_alive and realtime_alive:
                return
            jwt = get_settings().streamelements_jwt.strip()
            if not jwt:
                set_status("streamelements", "Disconnected")
                return
            if jwt_is_expired(jwt):
                set_status(
                    "streamelements",
                    "Token expired",
                    "Paste a new JWT from the StreamElements dashboard.",
                )
                notify_streamelements_jwt_expired()
                return
            self._stop.clear()
            self._realtime_authenticated = False
            self._gate = TipGate()
            if not astro_alive:
                self._thread = threading.Thread(
                    target=self._run, name="StreamElementsAstro", daemon=True
                )
                self._thread.start()
            if not realtime_alive:
                self._realtime_thread = threading.Thread(
                    target=self._realtime_loop, name="StreamElementsRealtime", daemon=True
                )
                self._realtime_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for socket in (self._ws, self._realtime_ws):
            if socket is None:
                continue
            try:
                socket.close()
            except Exception:
                logger.debug("StreamElements socket close failed", exc_info=True)
        for worker in (self._thread, self._realtime_thread):
            if worker and worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=3)

    def _note_connected(self) -> None:
        set_status("streamelements", "Connected", "")

    def _note_failure(self, status: str, error: str) -> None:
        if self._realtime_authenticated and status not in {
            "Authentication failed",
            "Token expired",
        }:
            logger.warning("StreamElements %s: %s", status, error)
            return
        set_status("streamelements", status, error)

    def _reject_jwt(self, jwt: str) -> None:
        """Stop retrying and tell the user when the saved JWT is no longer valid."""
        self._realtime_authenticated = False
        if jwt_is_expired(jwt):
            self._note_failure(
                "Token expired",
                "Paste a new JWT from the StreamElements dashboard.",
            )
            notify_streamelements_jwt_expired()
            self._stop.set()
            return
        self._note_failure(
            "Authentication failed",
            "StreamElements rejected the JWT.",
        )

    def disconnect(self) -> None:
        self.stop()
        clear_secret("streamelements_jwt")
        update_settings(streamelements_channel_id="")
        set_status("streamelements", "Disconnected", "")

    def _resolve_channel_id(self, jwt: str) -> str:
        stored = get_settings().streamelements_channel_id.strip()
        decoded = channel_id_from_jwt(jwt)
        if decoded:
            if decoded != stored:
                update_settings(streamelements_channel_id=decoded)
            return decoded
        if stored:
            return stored
        try:
            response = requests.get(
                CHANNELS_ME,
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            logger.warning("StreamElements channel lookup failed: %s", exc)
            return ""
        if response.status_code in (401, 403):
            self._reject_jwt(jwt)
            return ""
        if response.status_code >= 400:
            return ""
        try:
            payload = response.json()
        except ValueError:
            return ""
        channel_id = ""
        if isinstance(payload, dict):
            channel_id = str(payload.get("_id") or payload.get("id") or "").strip()
        if channel_id:
            update_settings(streamelements_channel_id=channel_id)
        return channel_id

    def _run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            jwt = get_settings().streamelements_jwt.strip()
            if not jwt:
                set_status("streamelements", "Disconnected")
                return
            set_status("streamelements", "Connecting")
            channel_id = self._resolve_channel_id(jwt)
            if self._stop.is_set():
                return
            if get_settings().streamelements_connection_status in {
                "Authentication failed",
                "Token expired",
            }:
                return
            if not channel_id:
                self._note_failure(
                    "Error",
                    "Could not read the channel id from the JWT.",
                )
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, 60.0)
                continue
            self._connect(jwt, channel_id)
            if self._stop.is_set():
                return
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, 30.0)
            set_status("streamelements", "Connecting", "Reconnecting to StreamElements.")

    def _connect(self, jwt: str, channel_id: str) -> None:
        token = self._reconnect_token
        self._reconnect_token = ""
        url = ASTRO_URL
        if token:
            url = f"{ASTRO_URL}?reconnect_token={quote(token, safe='')}"
        subscribed = {"tips": False, "moderation": False}
        skip_subscribe = bool(token)

        def _send(payload: dict) -> None:
            ws = self._ws
            if ws is None:
                return
            try:
                ws.send(json.dumps(payload))
            except Exception:
                logger.debug("StreamElements send failed", exc_info=True)

        def _subscribe() -> None:
            for topic in TIP_TOPICS:
                _send(
                    {
                        "type": "subscribe",
                        "nonce": str(uuid.uuid4()),
                        "data": {
                            "topic": topic,
                            "room": channel_id,
                            "token": jwt,
                            "token_type": "jwt",
                        },
                    }
                )

        def on_message(ws, raw: str) -> None:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                return
            if not isinstance(message, dict):
                return
            kind = message.get("type")
            if kind == "welcome":
                if not skip_subscribe:
                    _subscribe()
                else:
                    self._note_connected()
            elif kind == "response":
                self._handle_response(message, subscribed)
            elif kind == "message":
                self._handle_tip_message(message)
            elif kind == "reconnect":
                data = message.get("data") if isinstance(message.get("data"), dict) else {}
                reconnect = str(data.get("reconnect_token") or "")
                if reconnect:
                    self._reconnect_token = reconnect
                try:
                    ws.close()
                except Exception:
                    logger.debug("StreamElements reconnect close failed", exc_info=True)

        def on_error(ws, error) -> None:
            logger.warning("StreamElements socket error: %s", error)

        def on_close(ws, status_code, message) -> None:
            logger.info(
                "StreamElements socket closed (%s)",
                status_code if status_code else "no code",
            )

        app = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws = app
        try:
            app.run_forever(ping_interval=20, ping_timeout=10)
        finally:
            self._ws = None

    def _handle_response(self, message: dict, subscribed: dict) -> None:
        error = message.get("error")
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        if error:
            detail = str(data.get("message") or error)
            if error == "err_unauthorized":
                self._reject_jwt(get_settings().streamelements_jwt)
            else:
                self._note_failure("Error", detail)
            logger.warning("StreamElements subscribe error: %s", error)
            return
        topic = str(data.get("topic") or "")
        if topic == "channel.tips":
            subscribed["tips"] = True
        elif topic == "channel.tips.moderation":
            subscribed["moderation"] = True
        if subscribed["tips"] and subscribed["moderation"]:
            self._note_connected()

    def _handle_tip_message(self, message: dict) -> None:
        topic = str(message.get("topic") or "")
        if topic not in TIP_TOPICS:
            return
        data = message.get("data")
        donation = self._gate.consider(data)
        if not donation:
            return
        self._emit_normalized(donation)

    def _emit_normalized(self, donation: dict) -> None:
        from .donation_alerts import emit_donation_alert

        currency = donation.get("currency") or get_settings().alert_currency or "USD"
        event_id = donation.get("event_id") or f"streamelements-{time.time_ns()}"
        emit_donation_alert(
            username=donation["username"],
            amount=donation["amount"],
            currency=currency,
            message=donation["message"],
            source="streamelements",
            event_id=event_id,
        )

    def _realtime_loop(self) -> None:
        """Overlay Emulate tips arrive here, not on the Astro tips topic."""
        delay = 2.0
        while not self._stop.is_set():
            jwt = get_settings().streamelements_jwt.strip()
            if not jwt:
                return
            self._connect_realtime(jwt)
            if self._stop.is_set():
                return
            if get_settings().streamelements_connection_status in {
                "Authentication failed",
                "Token expired",
            }:
                return
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, 30.0)

    def _connect_realtime(self, jwt: str) -> None:
        def _send(payload: str) -> None:
            socket = self._realtime_ws
            if socket is None:
                return
            try:
                socket.send(payload)
            except Exception:
                logger.debug("StreamElements realtime send failed", exc_info=True)

        authenticated_sent = {"done": False}

        def on_message(_ws, raw: str) -> None:
            if not isinstance(raw, str) or not raw:
                return
            if raw[0] == "0" and not raw.startswith("40"):
                # Engine.IO 4: the server sends pings. A client ping ("2")
                # closes this socket. Only answer their ping with "3".
                _send("40")
                return
            if raw.startswith("40") and not authenticated_sent["done"]:
                authenticated_sent["done"] = True
                _send(
                    "42"
                    + json.dumps(
                        ["authenticate", {"method": "jwt", "token": jwt}]
                    )
                )
                return
            if raw == "2":
                _send("3")
                return
            parsed = parse_socketio_message(raw)
            if not parsed:
                return
            name, payload = parsed
            if name == "authenticated":
                if isinstance(payload, dict):
                    channel_id = str(
                        payload.get("channelId") or payload.get("channel_id") or ""
                    ).strip()
                    if channel_id:
                        update_settings(streamelements_channel_id=channel_id)
                self._realtime_authenticated = True
                self._note_connected()
                logger.info("StreamElements realtime socket authenticated")
                return
            if name == "unauthorized":
                self._realtime_authenticated = False
                self._reject_jwt(jwt)
                try:
                    _ws.close()
                except Exception:
                    logger.debug("StreamElements unauthorized close failed", exc_info=True)
                return
            if name not in ("event", "event:test"):
                return
            donation = normalize_realtime_tip(payload)
            if not donation:
                return
            logger.info(
                "StreamElements tip from %s %.2f %s",
                donation["username"],
                donation["amount"],
                donation["currency"] or get_settings().alert_currency or "USD",
            )
            self._emit_normalized(donation)

        def on_error(_ws, error) -> None:
            text = str(error)
            if "opcode=8" in text:
                return
            logger.warning("StreamElements realtime socket error: %s", error)

        def on_close(_ws, status_code, _message) -> None:
            self._realtime_authenticated = False
            logger.info(
                "StreamElements realtime socket closed (%s)",
                status_code if status_code else "no code",
            )

        app = websocket.WebSocketApp(
            REALTIME_URL,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._realtime_ws = app
        sslopt = {
            "cert_reqs": ssl.CERT_REQUIRED,
            "ca_certs": certifi.where(),
        }
        try:
            app.run_forever(ping_interval=0, ping_timeout=None, sslopt=sslopt)
        finally:
            self._realtime_ws = None


def get_streamelements_client() -> StreamElementsClient:
    global _client
    with _start_lock:
        if _client is None:
            _client = StreamElementsClient()
        return _client


def get_streamelements_status() -> dict:
    client = get_streamelements_client()
    status = client.status()
    return {
        "status": status,
        "error": client.last_error(),
        "is_valid": status == "Connected",
    }


def streamelements_configured() -> bool:
    return bool(get_settings().streamelements_jwt.strip())


def start_streamelements_service() -> None:
    if streamelements_configured():
        get_streamelements_client().start()


def connect_streamelements(jwt: str) -> str:
    """Save a JWT and open the socket. Returns an error string, or empty."""
    token = (jwt or "").strip()
    if not token:
        return "Paste a StreamElements JWT first."
    update_settings(streamelements_jwt=token, streamelements_channel_id="")
    client = get_streamelements_client()
    client.stop()
    client.start()
    return ""


def disconnect_streamelements() -> None:
    if _client is not None:
        _client.disconnect()
    else:
        clear_secret("streamelements_jwt")
        update_settings(streamelements_channel_id="")
        set_status("streamelements", "Disconnected", "")

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Streamlabs OAuth, Socket.IO v2 donations, and a donations poll backup.

Streamlabs' socket speaks Engine.IO 3. Mycelian's overlay server uses
python-socketio 5 (Engine.IO 4), so this client speaks Engine.IO 3 itself
on the websocket-client dependency and leaves the overlay server alone.

Live tips set ``for`` to ``streamlabs``. Alert-box tests often omit ``for``.
Both are donations. Twitch bits and subs on the same socket are ignored.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
import websocket

from .donation_settings import clear_secret, get_settings, set_status, update_settings

logger = logging.getLogger(__name__)

STREAMLABS_OAUTH_REDIRECT_URI = "http://127.0.0.1:9975"
AUTHORIZE_URL = "https://streamlabs.com/api/v2.0/authorize"
TOKEN_URL = "https://streamlabs.com/api/v2.0/token"
SOCKET_TOKEN_URL = "https://streamlabs.com/api/v2.0/socket/token"
DONATIONS_URL = "https://streamlabs.com/api/v2.0/donations"
SOCKET_URL = "wss://sockets.streamlabs.com/socket.io/"
OAUTH_SCOPES = "donations.read socket.token"
POLL_SEC = 20.0
FINGERPRINT_SEC = 120.0

_client: Optional["StreamlabsClient"] = None
_start_lock = threading.Lock()


def is_streamlabs_donation_event(event: Any) -> bool:
    """True for Streamlabs cash tips, including alert-box tests."""
    if not isinstance(event, dict):
        return False
    if event.get("type") != "donation":
        return False
    source = event.get("for")
    return source in (None, "", "streamlabs")


def parse_engineio_event(packet: str) -> Optional[dict]:
    """Return the object from a Socket.IO v2 event packet, if it is one."""
    if not isinstance(packet, str) or not packet.startswith("42"):
        return None
    try:
        data = json.loads(packet[2:])
    except (TypeError, ValueError):
        return None
    if (
        isinstance(data, list)
        and len(data) >= 2
        and data[0] == "event"
        and isinstance(data[1], dict)
    ):
        return data[1]
    return None


def _as_amount(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _numeric_id(value: Any) -> int:
    text = str(value or "").strip()
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return 0
    return 0


def donation_fingerprint(name: str, amount: float, currency: str, message: str) -> tuple:
    return (
        (name or "").strip().lower(),
        round(float(amount), 2),
        (currency or "USD").strip().upper(),
        (message or "").strip()[:120],
    )


def normalize_streamlabs_donation(item: dict) -> dict:
    name = str(item.get("name") or item.get("from") or "").strip() or "Anonymous"
    message = item.get("message") or ""
    if message is None:
        message = ""
    currency = str(item.get("currency") or "USD").strip().upper() or "USD"
    amount = _as_amount(item.get("amount"))
    ids = []
    for key in ("donation_id", "id", "_id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            ids.append(str(value).strip())
    numeric = 0
    for key in ("donation_id", "id"):
        numeric = _numeric_id(item.get(key))
        if numeric:
            break
    created = _numeric_id(item.get("created_at"))
    return {
        "username": name,
        "amount": amount,
        "currency": currency,
        "message": str(message),
        "ids": ids,
        "event_id": ids[0] if ids else "",
        "numeric_id": numeric,
        "created_at": created,
        "fingerprint": donation_fingerprint(name, amount, currency, str(message)),
    }


def iter_streamlabs_donations(event: Any) -> list[dict]:
    if not is_streamlabs_donation_event(event):
        return []
    messages = event.get("message") or []
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return []
    event_id = str(event.get("event_id") or "")
    donations = []
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            continue
        donation = normalize_streamlabs_donation(item)
        if not donation["ids"] and event_id:
            fallback = f"{event_id}:{index}"
            donation["ids"] = [fallback]
            donation["event_id"] = fallback
        donations.append(donation)
    return donations


class DonationDeduper:
    """Drop repeated tips from the socket and the donations poll."""

    def __init__(self, window_sec: float = FINGERPRINT_SEC) -> None:
        self.window_sec = window_sec
        self._ids: set[str] = set()
        self._fingerprints: dict[tuple, float] = {}
        self._high_water = 0
        self._primed = False
        self._primed_at = 0.0
        self._lock = threading.Lock()

    def _seen(self, donation: dict, now: float) -> bool:
        if any(item and item in self._ids for item in donation.get("ids") or []):
            return True
        seen_at = self._fingerprints.get(donation.get("fingerprint"))
        return seen_at is not None and now - seen_at < self.window_sec

    def _remember(self, donation: dict, now: float) -> None:
        for item in donation.get("ids") or []:
            if item:
                self._ids.add(item)
        fingerprint = donation.get("fingerprint")
        if fingerprint:
            self._fingerprints[fingerprint] = now
        numeric = int(donation.get("numeric_id") or 0)
        if numeric > self._high_water:
            self._high_water = numeric

    def accept_live(self, donation: dict, now: Optional[float] = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            if self._seen(donation, moment):
                return False
            self._remember(donation, moment)
            return True

    def accept_poll_batch(self, donations: list[dict], now: Optional[float] = None) -> list[dict]:
        moment = time.monotonic() if now is None else now
        fresh = []
        with self._lock:
            if not self._primed:
                for donation in donations:
                    self._remember(donation, moment)
                self._primed = True
                self._primed_at = time.time()
                return []
            for donation in donations:
                if self._seen(donation, moment):
                    continue
                numeric = int(donation.get("numeric_id") or 0)
                created = int(donation.get("created_at") or 0)
                if numeric and self._high_water and numeric <= self._high_water:
                    self._remember(donation, moment)
                    continue
                if created and self._primed_at and created < self._primed_at - 15:
                    self._remember(donation, moment)
                    continue
                self._remember(donation, moment)
                fresh.append(donation)
        return fresh


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        server = self.server
        if "code" in params:
            server.auth_code = params["code"][0]
            server.auth_state = (params.get("state") or [""])[0]
            body = (
                "<html><body><p>Streamlabs is connected. "
                "You can close this window.</p></body></html>"
            )
            status = 200
        else:
            server.auth_error = (params.get("error") or ["authorization_failed"])[0]
            body = (
                "<html><body><p>Streamlabs authorization was not completed. "
                "Return to Mycelian and try again.</p></body></html>"
            )
            status = 400
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


class _CallbackServer(TCPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 9975), _OAuthHandler)
        self.auth_code: Optional[str] = None
        self.auth_state: str = ""
        self.auth_error: Optional[str] = None


class StreamlabsClient:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._deduper = DonationDeduper()
        self._lock = threading.Lock()
        self._oauth_lock = threading.Lock()

    def status(self) -> str:
        return get_settings().streamlabs_connection_status or "Disconnected"

    def last_error(self) -> str:
        return get_settings().streamlabs_last_error or ""

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            settings = get_settings()
            if not settings.streamlabs_access_token:
                set_status("streamlabs", "Disconnected")
                return
            self._stop.clear()
            self._deduper = DonationDeduper()
            self._thread = threading.Thread(
                target=self._socket_loop, name="StreamlabsSocket", daemon=True
            )
            self._thread.start()
            self._poll_thread = threading.Thread(
                target=self._poll_loop, name="StreamlabsPoll", daemon=True
            )
            self._poll_thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                logger.debug("Streamlabs socket close failed", exc_info=True)
        for worker in (self._thread, self._poll_thread):
            if worker and worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=3)
        set_status("streamlabs", "Disconnected")

    def disconnect(self) -> None:
        self.stop()
        clear_secret("streamlabs_access_token")
        clear_secret("streamlabs_refresh_token")
        set_status("streamlabs", "Disconnected", "")

    def begin_oauth(self, client_id: str, client_secret: str) -> str:
        """Open the browser. Returns an error string, or empty on success."""
        if not self._oauth_lock.acquire(blocking=False):
            return "Authorization is already in progress."
        try:
            return self._oauth(client_id, client_secret)
        finally:
            self._oauth_lock.release()

    def _oauth(self, client_id: str, client_secret: str) -> str:
        state = secrets.token_urlsafe(16)
        try:
            server = _CallbackServer()
        except OSError as exc:
            logger.error("Streamlabs OAuth port is in use: %s", exc)
            set_status("streamlabs", "Error", "OAuth callback port 9975 is in use.")
            return "OAuth callback port 9975 is in use."
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": STREAMLABS_OAUTH_REDIRECT_URI,
                "response_type": "code",
                "scope": OAUTH_SCOPES,
                "state": state,
            }
        )
        import webbrowser

        webbrowser.open(f"{AUTHORIZE_URL}?{query}")
        set_status("streamlabs", "Connecting")
        deadline = time.time() + 180
        try:
            while time.time() < deadline and not server.auth_code and not server.auth_error:
                time.sleep(0.2)
        finally:
            server.shutdown()
            server.server_close()
        if server.auth_error:
            set_status("streamlabs", "Authentication failed", server.auth_error)
            return server.auth_error
        if not server.auth_code or server.auth_state != state:
            set_status("streamlabs", "Authentication failed", "Authorization was not completed.")
            return "Authorization was not completed."
        token = self._exchange_code(client_id, client_secret, server.auth_code)
        if not token:
            return get_settings().streamlabs_last_error or "Token exchange failed."
        return ""

    def _exchange_code(self, client_id: str, client_secret: str, code: str) -> str:
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": STREAMLABS_OAUTH_REDIRECT_URI,
                    "code": code,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            set_status("streamlabs", "Error", "Could not reach Streamlabs.")
            logger.warning("Streamlabs token exchange failed: %s", exc)
            return ""
        if response.status_code >= 400:
            set_status("streamlabs", "Authentication failed", "Streamlabs rejected the authorization code.")
            logger.warning("Streamlabs token exchange HTTP %s", response.status_code)
            return ""
        payload = response.json()
        access = str(payload.get("access_token") or "")
        refresh = str(payload.get("refresh_token") or "")
        if not access:
            set_status("streamlabs", "Authentication failed", "Streamlabs did not return a token.")
            return ""
        update_settings(
            streamlabs_access_token=access,
            streamlabs_refresh_token=refresh,
        )
        self.stop()
        self.start()
        return access

    def _refresh_access_token(self) -> str:
        settings = get_settings()
        from .api_credentials_manager import get_streamlabs_credentials

        creds = get_streamlabs_credentials()
        refresh = settings.streamlabs_refresh_token
        if not refresh or not creds.get("client_id") or not creds.get("client_secret"):
            set_status("streamlabs", "Token expired", "Reconnect Streamlabs.")
            return ""
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                    "redirect_uri": STREAMLABS_OAUTH_REDIRECT_URI,
                    "refresh_token": refresh,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("Streamlabs token refresh failed: %s", exc)
            set_status("streamlabs", "Token expired", "Reconnect Streamlabs.")
            return ""
        if response.status_code >= 400:
            set_status("streamlabs", "Token expired", "Reconnect Streamlabs.")
            return ""
        payload = response.json()
        access = str(payload.get("access_token") or "")
        if not access:
            set_status("streamlabs", "Token expired", "Reconnect Streamlabs.")
            return ""
        new_refresh = str(payload.get("refresh_token") or refresh)
        update_settings(
            streamlabs_access_token=access,
            streamlabs_refresh_token=new_refresh,
        )
        return access

    def _api_get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        access = get_settings().streamlabs_access_token
        if not access:
            return None
        response = self._authorized_get(url, access, params)
        if response is not None and response.status_code == 401:
            access = self._refresh_access_token()
            if not access:
                return None
            response = self._authorized_get(url, access, params)
        if response is None:
            return None
        if response.status_code >= 400:
            logger.warning("Streamlabs GET %s HTTP %s", url, response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def _authorized_get(self, url: str, access: str, params: Optional[dict]) -> Optional[requests.Response]:
        try:
            return requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json",
                },
                params=params,
                timeout=20,
            )
        except requests.RequestException as exc:
            logger.warning("Streamlabs request failed: %s", exc)
            return None

    def _socket_token(self) -> str:
        payload = self._api_get(SOCKET_TOKEN_URL)
        if not payload:
            return ""
        return str(payload.get("socket_token") or "")

    def _socket_loop(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            set_status("streamlabs", "Connecting")
            token = self._socket_token()
            if self._stop.is_set():
                return
            if not token:
                if get_settings().streamlabs_connection_status not in {
                    "Token expired",
                    "Authentication failed",
                }:
                    set_status("streamlabs", "Error", "Could not get a Streamlabs socket token.")
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, 60.0)
                continue
            delay = 2.0
            self._connect_socket(token)
            if self._stop.is_set():
                return
            set_status("streamlabs", "Connecting", "Reconnecting to Streamlabs.")
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, 30.0)

    def _connect_socket(self, socket_token: str) -> None:
        url = (
            f"{SOCKET_URL}?EIO=3&transport=websocket&token={quote(socket_token, safe='')}"
        )
        ping_stop = threading.Event()
        opened = {"ok": False}

        def _send(payload: str) -> None:
            ws = self._ws
            if ws is None:
                return
            try:
                ws.send(payload)
            except Exception:
                logger.debug("Streamlabs socket send failed", exc_info=True)

        def _ping_loop(interval: float) -> None:
            while not ping_stop.wait(interval):
                _send("2")

        ping_thread: dict[str, Optional[threading.Thread]] = {"thread": None}

        def on_open(ws) -> None:
            self._ws = ws

        def on_message(ws, raw: str) -> None:
            if not isinstance(raw, str) or not raw:
                return
            kind = raw[0]
            if kind == "0":
                interval = 25.0
                try:
                    opened_payload = json.loads(raw[1:] or "{}")
                    interval = max(5.0, float(opened_payload.get("pingInterval") or 25000) / 1000.0)
                except (TypeError, ValueError):
                    interval = 25.0
                _send("40")
                if ping_thread["thread"] is None:
                    worker = threading.Thread(
                        target=_ping_loop, args=(interval,), daemon=True
                    )
                    ping_thread["thread"] = worker
                    worker.start()
                opened["ok"] = True
                set_status("streamlabs", "Connected", "")
            elif raw == "2":
                _send("3")
            elif raw.startswith("42"):
                event = parse_engineio_event(raw)
                if event:
                    self._handle_socket_event(event)

        def on_error(ws, error) -> None:
            logger.warning("Streamlabs socket error: %s", error)

        def on_close(ws, status_code, message) -> None:
            ping_stop.set()
            logger.info(
                "Streamlabs socket closed (%s)", status_code if status_code else "no code"
            )

        app = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws = app
        try:
            app.run_forever(ping_interval=0, ping_timeout=None)
        finally:
            ping_stop.set()
            self._ws = None

    def _handle_socket_event(self, event: dict) -> None:
        for donation in iter_streamlabs_donations(event):
            if self._deduper.accept_live(donation):
                self._emit(donation)

    def _emit(self, donation: dict) -> None:
        from .donation_alerts import emit_donation_alert

        emit_donation_alert(
            username=donation["username"],
            amount=donation["amount"],
            currency=donation["currency"],
            message=donation["message"],
            source="streamlabs",
            event_id=donation["event_id"],
        )

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            if self._stop.wait(POLL_SEC):
                return

    def _poll_once(self) -> None:
        payload = self._api_get(DONATIONS_URL, {"limit": 25})
        if not payload:
            return
        rows = payload.get("data") or []
        if not isinstance(rows, list):
            return
        donations = []
        for row in rows:
            if isinstance(row, dict):
                donations.append(normalize_streamlabs_donation(row))
        for donation in self._deduper.accept_poll_batch(donations):
            self._emit(donation)


def get_streamlabs_client() -> StreamlabsClient:
    global _client
    with _start_lock:
        if _client is None:
            _client = StreamlabsClient()
        return _client


def get_streamlabs_status() -> dict:
    client = get_streamlabs_client()
    status = client.status()
    return {
        "status": status,
        "error": client.last_error(),
        "is_valid": status == "Connected",
    }


def streamlabs_configured() -> bool:
    from .api_credentials_manager import get_streamlabs_credentials

    creds = get_streamlabs_credentials()
    if (creds.get("client_id") or "").strip():
        return True
    return bool(get_settings().streamlabs_access_token)


def start_streamlabs_service() -> None:
    if streamlabs_configured() and get_settings().streamlabs_access_token:
        get_streamlabs_client().start()


def stop_streamlabs_service() -> None:
    if _client is not None:
        _client.disconnect()


def disconnect_streamlabs() -> None:
    stop_streamlabs_service()

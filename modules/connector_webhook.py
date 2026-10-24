# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""HTTP entry point for connector webhook triggers.

``POST /api/connectors/webhook/<connector_id>`` (``GET`` also works for simple
tools). Authenticate with the connector's webhook key, either as the header
``X-Mycelian-Key: <key>`` or the query parameter ``?key=<key>``. A JSON body,
form fields or query parameters (``key`` excluded) become event fields; the raw
payload is also available as ``{payload}``. Responses are JSON:
``{"ok": true}`` (200), or ``{"ok": false, "error": ...}`` with 401 (missing or
wrong key / no key configured), 404 (no such connector), 400 (not a webhook
connector), 409 (disabled) or 503 (connector system not running).

The route lives on the existing local web server (it only accepts local-network
clients); see :func:`modules.connector_variables.webhook_url` for the URL.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

WEBHOOK_ROUTE = "/api/connectors/webhook/<connector_id>"
WEBHOOK_KEY_HEADER = "X-Mycelian-Key"


def _request_payload(request) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key in request.args:
        if key == "key":
            continue
        values = request.args.getlist(key)
        payload[key] = values[0] if len(values) == 1 else values
    if request.method == "POST":
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            payload.update(body)
        elif body is not None:
            payload["body"] = body
        elif request.form:
            for key in request.form:
                values = request.form.getlist(key)
                payload[key] = values[0] if len(values) == 1 else values
        else:
            raw = request.get_data(as_text=True) or ""
            if raw.strip():
                payload["body"] = raw
    return payload


def install_connector_webhook_route(app) -> None:
    """Register the webhook route on a Flask *app*."""
    from flask import request

    def connector_webhook(connector_id: str):
        from .connector_integration import handle_connector_webhook

        try:
            provided = request.headers.get(WEBHOOK_KEY_HEADER) or request.args.get("key")
            status, body = handle_connector_webhook(
                connector_id, provided, _request_payload(request)
            )
        except Exception as e:
            logger.error("Connector webhook failed: %s", e, exc_info=True)
            status, body = 500, {"ok": False, "error": "Webhook failed"}
        if status != 200:
            logger.info("Connector webhook %s rejected: %s", connector_id, body.get("error"))
        return body, status, {"Content-Type": "application/json"}

    app.add_url_rule(
        WEBHOOK_ROUTE,
        endpoint="connector_webhook",
        view_func=connector_webhook,
        methods=["GET", "POST"],
    )

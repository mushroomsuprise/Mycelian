# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_webhook: payload extraction from query / JSON / form / raw bodies and the Flask route."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from flask import Flask

from modules import connector_integration as ci, connector_webhook as cw


@pytest.fixture
def client(monkeypatch):
    calls = []
    result = {"value": (200, {"ok": True})}

    def handle(connector_id, key, payload):
        calls.append((connector_id, key, payload))
        if isinstance(result["value"], Exception):
            raise result["value"]
        return result["value"]

    monkeypatch.setattr(ci, "handle_connector_webhook", handle)
    app = Flask(__name__)
    cw.install_connector_webhook_route(app)
    return app.test_client(), calls, result


def test_route_registered():
    app = Flask(__name__)
    cw.install_connector_webhook_route(app)
    rule = next(r for r in app.url_map.iter_rules() if r.endpoint == "connector_webhook")
    assert rule.rule == cw.WEBHOOK_ROUTE and {"GET", "POST"} <= rule.methods


def test_get_with_query_key_and_multi_values(client):
    c, calls, _ = client
    resp = c.get("/api/connectors/webhook/abc?key=k1&username=Bob&tag=a&tag=b")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert resp.headers["Content-Type"] == "application/json"
    assert calls == [("abc", "k1", {"username": "Bob", "tag": ["a", "b"]})]


def test_post_json_header_key(client):
    c, calls, _ = client
    c.post("/api/connectors/webhook/abc?x=1", json={"amount": 5}, headers={cw.WEBHOOK_KEY_HEADER: "hk"})
    assert calls[-1] == ("abc", "hk", {"x": "1", "amount": 5})
    c.post("/api/connectors/webhook/abc", json=[1, 2])
    assert calls[-1] == ("abc", None, {"body": [1, 2]})


def test_post_form_and_raw(client):
    c, calls, _ = client
    c.post("/api/connectors/webhook/abc", data={"user": "Ann", "t": ["1", "2"]})
    assert calls[-1][2] == {"user": "Ann", "t": ["1", "2"]}
    c.post("/api/connectors/webhook/abc", data="plain text", content_type="text/plain")
    assert calls[-1][2] == {"body": "plain text"}
    c.post("/api/connectors/webhook/abc", data="   ", content_type="text/plain")
    assert calls[-1][2] == {}


def test_rejections_and_errors(client):
    c, calls, result = client
    result["value"] = (401, {"ok": False, "error": "Invalid webhook key"})
    resp = c.post("/api/connectors/webhook/abc")
    assert resp.status_code == 401 and resp.get_json()["error"] == "Invalid webhook key"
    result["value"] = RuntimeError("boom")
    resp = c.get("/api/connectors/webhook/abc")
    assert resp.status_code == 500 and resp.get_json() == {"ok": False, "error": "Webhook failed"}

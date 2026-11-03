# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker HTTP API (/api/game-tracker/...) through Flask's test client.

The tracker service runs on a temp store with no platform clients; the web engine's
background workers are faked as in tests/modules/test_web_engine__routes.py.
"""

from __future__ import annotations

import io
import time
from types import SimpleNamespace

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from PIL import Image

from modules import web_engine
from modules.game_tracker import covers
from modules.game_tracker import service as svc_mod
from modules.game_tracker.models import GameEvent, TrackedGame
from modules.game_tracker.service import GameTrackerService
from modules.game_tracker.store import GameTrackerStore
from modules.spore_studio import assets_watcher
from modules.spore_studio import containers as containers_mod
from modules.template_config_parser import TemplateConfigParser

WE = web_engine.WebEngine


@pytest.fixture
def client(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    adir = tmp_path / "assets"
    adir.mkdir()
    monkeypatch.setattr(web_engine, "get_template_path", lambda rel="": str(tdir / rel) if rel else str(tdir))
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": str(adir / rel) if rel else str(adir))
    for name in ("ALERT_PLAYING", "ALERTS_PAUSED", "ALERTS_MUTED", "web_engine_instance", "web_engine_running",
                 "EXPECTED_ALERT_COMPLETE_SEQ"):
        monkeypatch.setattr(web_engine, name, getattr(web_engine, name))
    for meth in ("ensure_web_engine_heartbeat", "ensure_template_control_emit_worker", "_refresh_cached_listener"):
        monkeypatch.setattr(WE, meth, lambda self: None)
    monkeypatch.setattr(assets_watcher, "ensure_background_poller", lambda sio: None)
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: [])
    eng = WE(template_dir=str(tdir), port=5994)
    eng.template_config_parser = TemplateConfigParser(config_dir=str(tdir / "template_configs"))
    monkeypatch.setattr(eng.socketio, "start_background_task", lambda fn, *a, **k: None)
    yield eng.app.test_client()
    eng._close_emit_wake_pipe()


@pytest.fixture
def tracker(tmp_path):
    store = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    now = time.time()
    store.upsert_games([
        TrackedGame("psn", "NPWR00001_00", title="Alpha", platform_label="PS5", earned={"bronze": 3}, defined={"bronze": 10},
                    progress=30, last_played=now - 10),
        TrackedGame("steam", "440", title="Bravo", platform_label="Steam", unlocked_ids=["a"], total_achievements=2,
                    progress=50, last_played=now),
        TrackedGame("steam", "441", title="Hidden", hidden=True, last_played=now + 5),
    ])
    store.record_events([GameEvent("unlock", "psn", "NPWR00001_00", "Alpha", unlock_name="First", unlock_grade="bronze")])
    resolved = SimpleNamespace(npwr="NPWR00002_00", calls=[])

    class FakePsn:
        def resolve_np_communication_id_from_np_title_id(self, tid):
            resolved.calls.append(tid)
            return resolved.npwr

    service = GameTrackerService(store=store, psn_client_fn=FakePsn, steam_client_fn=lambda: None)
    syncs = []
    service.sync_now = lambda platform=None: syncs.append(platform)
    old = svc_mod._service
    svc_mod.set_service(service)
    yield SimpleNamespace(store=store, service=service, syncs=syncs, resolved=resolved)
    svc_mod.set_service(old)


def test_list_games_filters_sorts_and_hides(client, tracker):
    games = client.get("/api/game-tracker/games").get_json()["games"]
    assert [g["title"] for g in games] == ["Bravo", "Alpha"]
    assert [g["title"] for g in client.get("/api/game-tracker/games?sort=name").get_json()["games"]] == ["Alpha", "Bravo"]
    assert [g["title"] for g in client.get("/api/game-tracker/games?sort=progress").get_json()["games"]] == ["Bravo", "Alpha"]
    assert [g["id"] for g in client.get("/api/game-tracker/games?platform=psn").get_json()["games"]] == ["NPWR00001_00"]
    assert len(client.get("/api/game-tracker/games?include_hidden=1").get_json()["games"]) == 3
    assert [g["title"] for g in client.get("/api/game-tracker/games?q=alp").get_json()["games"]] == ["Alpha"]
    assert client.get("/api/game-tracker/games?platform=xbox").status_code == 400


def test_summary_and_status(client, tracker):
    body = client.get("/api/game-tracker/summary").get_json()
    assert body["summary"]["games"] == 2
    assert set(body["status"]) == {"psn", "steam"}


def test_game_detail_and_unknowns(client, tracker):
    body = client.get("/api/game-tracker/games/psn/NPWR00001_00").get_json()
    assert body["game"]["title"] == "Alpha" and body["recent_unlocks"][0]["unlock_name"] == "First"
    assert client.get("/api/game-tracker/games/psn/NPWR09999_00").status_code == 404
    assert client.get("/api/game-tracker/games/xbox/1").status_code == 400


def test_patch_steam_title_and_hidden(client, tracker):
    res = client.patch("/api/game-tracker/games/steam/440", json={"title": "Bravo!", "hidden": True})
    assert res.status_code == 200 and res.get_json()["game"]["title"] == "Bravo!"
    game = tracker.store.get_game("steam", "440")
    assert game.title_override == "Bravo!" and game.hidden is True


def test_patch_bad_input(client, tracker):
    assert client.patch("/api/game-tracker/games/steam/440", data="nope", content_type="application/json").status_code == 400
    assert client.patch("/api/game-tracker/games/steam/999", json={"title": "x"}).status_code == 404
    res = client.patch("/api/game-tracker/games/psn/NPWR00001_00", json={"np_communication_id": "bad"})
    assert res.status_code == 400 and "NPWR" in res.get_json()["error"]


def test_patch_psn_rekeys(client, tracker, monkeypatch):
    from modules import psnapi

    monkeypatch.setattr(psnapi, "get_psn_game_cache_by_comm_id", lambda cid: None)
    monkeypatch.setattr(psnapi, "store_psn_game_cache_in_db", lambda doc: True)
    res = client.patch("/api/game-tracker/games/psn/NPWR00001_00", json={"np_communication_id": "npwr00005_00", "np_title_id": "ppsa00001_00"})
    assert res.status_code == 200 and res.get_json()["game"]["id"] == "NPWR00005_00"
    assert tracker.store.get_game("psn", "NPWR00001_00") is None
    assert tracker.store.get_game("psn", "NPWR00005_00").np_title_id == "PPSA00001_00"


def test_delete(client, tracker, monkeypatch):
    from modules import psnapi

    monkeypatch.setattr(psnapi, "delete_psn_game_cache_in_db", lambda cid: True)
    assert client.delete("/api/game-tracker/games/steam/440").get_json() == {"ok": True}
    assert client.delete("/api/game-tracker/games/steam/440").status_code == 404
    assert client.delete("/api/game-tracker/games/psn/NPWR00001_00").status_code == 200


def test_resolve(client, tracker, monkeypatch):
    from modules import psnapi

    monkeypatch.setattr(psnapi, "get_psn_game_cache_by_comm_id", lambda cid: None)
    monkeypatch.setattr(psnapi, "store_psn_game_cache_in_db", lambda doc: True)
    assert client.post("/api/game-tracker/games/psn/NPWR00001_00/resolve", json={}).status_code == 400
    assert client.post("/api/game-tracker/games/psn/NPWR09999_00/resolve", json={"np_title_id": "x"}).status_code == 404
    res = client.post("/api/game-tracker/games/psn/NPWR00001_00/resolve", json={"np_title_id": "ppsa04609_00"})
    assert res.status_code == 200 and res.get_json()["np_communication_id"] == "NPWR00002_00"
    assert tracker.resolved.calls == ["PPSA04609_00"]
    tracker.resolved.npwr = None
    assert client.post("/api/game-tracker/games/psn/NPWR00002_00/resolve", json={"np_title_id": "x"}).status_code == 404


def test_sync(client, tracker):
    assert client.post("/api/game-tracker/sync", json={"platform": "steam"}).status_code == 202
    assert client.post("/api/game-tracker/sync").status_code == 202
    assert client.post("/api/game-tracker/sync", json={"platform": "xbox"}).status_code == 400
    assert tracker.syncs == ["steam", None]


def test_cover(client, tracker, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "JPEG")
    monkeypatch.setattr(covers, "cover_bytes", lambda p, g: buf.getvalue() if (p, g) == ("steam", "440") else None)
    res = client.get("/api/game-tracker/cover/steam/440.jpg")
    assert res.status_code == 200 and res.headers["Content-Type"] == "image/jpeg"
    assert "max-age=86400" in res.headers["Cache-Control"]
    assert client.get("/api/game-tracker/cover/steam/1.jpg").status_code == 404
    assert client.get("/api/game-tracker/cover/xbox/440.jpg").status_code == 404


def test_export_png(client, tracker, monkeypatch):
    from modules.game_tracker import export

    seen = []
    real = export.render_games_image

    def fake(options, **kw):
        seen.append(options)
        return real(options, theme=None, channel_name="T", **{k: v for k, v in kw.items() if k != "theme"})

    monkeypatch.setattr(export, "render_games_image", fake)
    tracker.service.settings.export = {"layout": "square", "title": "Saved"}
    res = client.get("/api/game-tracker/export.png?title=Override&game=steam:440&template=game")
    assert res.status_code == 200 and res.headers["Content-Type"] == "image/png"
    assert Image.open(io.BytesIO(res.data)).size == (1080, 1080)
    assert (seen[-1].layout, seen[-1].title, seen[-1].game_key, seen[-1].template) == ("square", "Override", "steam:440", "game")
    assert client.get("/api/game-tracker/export.png?template=game&game=psn:NPWR09999_00").status_code == 404

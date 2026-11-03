# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker service: syncs on its own threads, callers never block, edits, summary, lifecycle."""

import threading
import time

import pytest

from modules.game_tracker import covers, dispatch as dispatch_mod, service as svc
from modules.game_tracker.models import PSN, STEAM, TrackedGame, TrackerSettings
from modules.game_tracker.store import GameTrackerStore


@pytest.fixture
def db(tmp_path):
    return GameTrackerStore(db_path=str(tmp_path / "gt.db"))


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(covers, "request_covers", lambda items: 0)
    sent = []
    monkeypatch.setattr(svc, "dispatch", lambda events, settings, store=None: sent.extend(events) or list(events))
    return sent


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_sync_runs_on_worker_threads_and_callers_never_block(db, monkeypatch):
    release = threading.Event()
    threads = []

    def slow_sync(client, store, settings, **kw):
        threads.append(threading.current_thread().name)
        release.wait(5)
        store.set_baseline(PSN, "acct")
        return []

    monkeypatch.setattr(svc.psn_sync, "sync_library", slow_sync)
    service = svc.GameTrackerService(db, psn_client_fn=lambda: object(), steam_client_fn=lambda: None, start_delay=0)
    service.start()
    try:
        assert wait_for(lambda: threads)
        assert threads == ["game-tracker-psn"]
        assert service.status()[PSN]["state"] == "syncing"
        t0 = time.monotonic()
        service.observe_psn_title({"np_communication_id": "NPWR00001_00"})
        service.observe_steam_game(10, "x", [{"api_name": "a"}])
        service.sync_now()
        service.summary()
        assert time.monotonic() - t0 < 0.5  # queued, not run
        release.set()
        assert wait_for(lambda: service.status()[PSN]["state"] == "idle")
        assert service.status()[PSN]["last_sync"] > 0
        assert service.status()[STEAM]["state"] == "waiting"  # no Steam client
    finally:
        release.set()
        service.stop(join_timeout=2)
    assert not service.is_running()


def test_events_are_dispatched_and_listeners_told(db, monkeypatch, quiet):
    from modules.game_tracker.models import GameEvent

    monkeypatch.setattr(svc.psn_sync, "sync_library",
                        lambda *a, **k: [GameEvent(kind="unlock", platform=PSN, game_id="N", game_title="G")])
    service = svc.GameTrackerService(db, psn_client_fn=lambda: object(), steam_client_fn=lambda: None, start_delay=0)
    told = []
    service.add_listener(lambda: told.append(1))
    assert service._sync(PSN, TrackerSettings())
    assert [e.kind for e in quiet] == ["unlock"] and told == [1]
    service.remove_listener(service._listeners[0])


def test_failed_sync_reports_waiting(db, monkeypatch):
    monkeypatch.setattr(svc.psn_sync, "sync_library", lambda *a, **k: None)
    service = svc.GameTrackerService(db, psn_client_fn=lambda: object(), steam_client_fn=lambda: None)
    assert service._sync(PSN, TrackerSettings()) is False
    assert service.status()[PSN]["state"] == "waiting"


def test_disabled_platform_is_off(db):
    db.save_settings(TrackerSettings(steam_enabled=False))
    service = svc.GameTrackerService(db, psn_client_fn=lambda: None, steam_client_fn=lambda: None, start_delay=0)
    service.start()
    try:
        assert wait_for(lambda: service.status()[STEAM]["state"] == "off")
        assert service.status()[STEAM]["enabled"] is False
    finally:
        service.stop(join_timeout=2)


def test_observe_is_ignored_before_the_first_import(db, monkeypatch):
    called = []
    monkeypatch.setattr(svc.psn_sync, "observe_title", lambda *a: called.append(1) or [])
    service = svc.GameTrackerService(db, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    service._observe(PSN, {"np_communication_id": "N"}, TrackerSettings())
    assert called == []
    db.set_baseline(PSN, "a")
    service._observe(PSN, {"np_communication_id": "N"}, TrackerSettings())
    assert called == [1]


def test_summary_counts_visible_games(db):
    db.upsert_games([
        TrackedGame(platform=PSN, game_id="A", title="A", earned={"bronze": 3, "platinum": 1}, defined={"bronze": 3, "platinum": 1},
                    progress=100, last_played=10),
        TrackedGame(platform=PSN, game_id="B", title="B", earned={"bronze": 1}, defined={"bronze": 9}, progress=20, last_played=50),
        TrackedGame(platform=STEAM, game_id="1", title="S", unlocked_ids=["a", "b"], total_achievements=2, progress=100, last_played=5),
        TrackedGame(platform=STEAM, game_id="2", title="Hidden", unlocked_ids=["a"], total_achievements=9, hidden=True, last_played=99),
    ])
    service = svc.GameTrackerService(db, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    s = service.summary()
    assert (s["games"], s["psn_games"], s["steam_games"]) == (3, 2, 1)
    assert (s["platinums"], s["perfect"], s["completed"]) == (1, 1, 2)
    assert s["trophies"] == 5 and s["trophies_by_grade"]["platinum"] == 1 and s["achievements"] == 2
    assert s["avg_completion"] == round((100 + 20 + 100) / 3)
    assert s["current"]["title"] == "B"
    assert svc.summarize([])["current"] is None


def test_edits_go_through_the_platform_rules(db, monkeypatch):
    db.upsert_games([TrackedGame(platform=STEAM, game_id="1", title="S")])
    service = svc.GameTrackerService(db, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    game, err = service.update_game(STEAM, "1", title=" Mine ", hidden=True)
    assert err == "" and game.display_title == "Mine" and db.get_game(STEAM, "1").hidden
    assert service.update_game(STEAM, "404", title="x")[1]
    monkeypatch.setattr(svc.psn_sync, "apply_edit", lambda store, gid, **kw: (None, f"psn {gid} {sorted(kw)}"))
    assert service.update_game(PSN, "N", title="t")[1] == "psn N ['title']"
    assert service.remove_game(STEAM, "1") and not service.remove_game(STEAM, "1")


def test_settings_save_and_reset_baseline(db):
    service = svc.GameTrackerService(db, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    saved = service.save_settings(TrackerSettings(sync_minutes=1, cover_ttl_days=3))
    assert saved.sync_minutes == 5 and service.settings.cover_ttl_days == 3
    assert covers._ttl_sec == 3 * 24 * 3600
    db.upsert_games([TrackedGame(platform=PSN, game_id="A")])
    service.reset_baseline()
    assert db.get_game(PSN, "A").baselined is False
    covers.set_ttl(7, 6)


def test_module_level_helpers_never_raise_without_a_service(monkeypatch):
    monkeypatch.setattr(svc, "_service", None)
    svc.observe_psn_title({"np_communication_id": "x"})
    svc.observe_steam_game(1, "x", [])
    svc.stop_game_tracker()


def test_module_level_helpers_queue_on_the_shared_service(db, monkeypatch):
    service = svc.GameTrackerService(db, psn_client_fn=lambda: None, steam_client_fn=lambda: None)
    svc.set_service(service)
    try:
        svc.observe_psn_title({"np_communication_id": "N"})
        svc.observe_psn_title({})  # nothing to track
        svc.observe_steam_game(10, "G", [{"api_name": "a"}])
        svc.sync_now(STEAM)
        assert service._queues[PSN].qsize() == 1 and service._queues[STEAM].qsize() == 2
        assert svc.get_service() is service
        assert svc.recent_unlocks() == []
    finally:
        svc.set_service(None)


def test_steam_client_holder_rebuilds_on_credential_change(monkeypatch):
    from modules import dataobjects

    creds = {"api_key": "k", "steam_profile": "p"}

    class S:
        def __getattr__(self, name):
            return creds[name]

    monkeypatch.setattr(dataobjects.state_manager, "get_steam_settings_data", lambda: S())
    holder = svc._SteamClientHolder()
    first = holder.get()
    assert first is not None and holder.get() is first
    creds["steam_profile"] = "q"
    assert holder.get() is not first
    creds["api_key"] = ""
    assert holder.get() is None

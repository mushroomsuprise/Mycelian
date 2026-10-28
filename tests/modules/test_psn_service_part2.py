# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/psn_service.py psn_data_update_loop and _resolve_game_via_bulk_fallback, driven
iteration by iteration with a fake client, a fake stop event and a fake clock."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules  # noqa: E402
from modules import psn_service as ps  # noqa: E402
from modules.psn_data import PSNData  # noqa: E402


class FakeState:
    def __init__(self):
        self.set_calls = []

    def set_live_psn_data(self, data):
        self.set_calls.append(data)


class StopAfter:
    """Stop event that reports set after ``n`` waits; each loop iteration waits exactly once."""

    def __init__(self, n, on_wait=None):
        self.n = n
        self.waits = []
        self.on_wait = on_wait

    def is_set(self):
        return len(self.waits) >= self.n

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait(len(self.waits))
        return self.is_set()

    def set(self):
        self.n = 0

    def clear(self):
        pass


def fresh_iso():
    return datetime.now().isoformat()


def stale_iso():
    return (datetime.now() - timedelta(days=3)).isoformat()


class LoopClient:
    """Fake PSNClient with scripted responses for each loop step; logs every call."""

    def __init__(self):
        self.psn_data = PSNData(npsso_code="tok")
        self.connected = True
        self.expired = False
        self.connect_result = True
        self.presence = {"basicPresence": {"gameTitleInfoList": [
            {"npTitleId": "PPSA1_00", "format": "PS5", "titleName": "Returnal"}]}}
        self.summary = object()
        self.by_title = {}
        self.by_name = {}
        self.title_result = None
        self.cache = {}
        self.read_ok = True
        self.update_from_result = "auto"
        self.groups_result = None
        self.all_games = None
        self.resolved = None
        self.calls = []

    def _log(self, *a):
        self.calls.append(a)

    def names(self):
        return [c[0] for c in self.calls]

    def is_connected(self):
        return self.connected

    def is_auth_expired(self):
        return self.expired

    def connect(self):
        self._log("connect")
        if self.connect_result:
            self.connected = True
        return self.connect_result

    def get_presence(self):
        self._log("get_presence")
        p = self.presence
        if callable(p):
            p = p()
        if p:
            self.psn_data.presence = p
        return p

    def get_overall_trophy_summary(self):
        self._log("summary")
        return self.summary

    def find_game_by_np_title_id(self, tid):
        self._log("by_title", tid)
        return self.by_title.get(tid)

    def find_game_by_name(self, name, platform=None):
        self._log("by_name", name, platform)
        return self.by_name.get(name)

    def get_title_trophy_progress(self, tid):
        self._log("title", tid)
        r = self.title_result
        return r() if callable(r) else r

    def get_cached_game_data_checked(self, cid):
        self._log("checked", cid)
        if not self.read_ok:
            return None, False
        return self.cache.get(cid), True

    def get_cached_game_data(self, cid):
        self._log("cached", cid)
        return self.cache.get(cid)

    def update_game_cache_entry_from(self, cid, updates, existing):
        self._log("update_from", cid, dict(updates))
        if self.update_from_result == "auto":
            doc = {**existing, **updates}
            self.cache[cid] = doc
            return doc
        return self.update_from_result

    def update_game_cache_entry(self, cid, updates):
        self._log("update", cid, dict(updates))
        self.cache[cid] = {**self.cache.get(cid, {}), **updates}
        return True

    def store_game_data(self, doc):
        self._log("store", dict(doc))
        self.cache[doc["np_communication_id"]] = doc
        return True

    def get_game_trophies(self, np_communication_id, platform):
        self._log("groups", np_communication_id, platform)
        return self.groups_result

    def get_all_games(self):
        self._log("all_games")
        return self.all_games

    def resolve_np_communication_id_from_np_title_id(self, tid):
        self._log("resolve", tid)
        return self.resolved


@pytest.fixture
def loop(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(ps, "state_manager", state)
    monkeypatch.setattr(ps, "_mismatch_notification_times", {})
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(ps, "time", SimpleNamespace(time=lambda: clock["t"]))
    broadcasts = []
    monkeypatch.setattr(ps, "_update_and_broadcast", lambda: broadcasts.append(1))
    from modules import connection_monitor as cm

    net = {"internet": True, "reach": True, "raise": False}

    def internet():
        if net["raise"]:
            raise RuntimeError("no monitor")
        return net["internet"]

    monkeypatch.setattr(cm, "is_internet_available", internet)
    monkeypatch.setattr(cm, "is_service_reachable", lambda k: net["reach"])
    client = LoopClient()
    monkeypatch.setattr(ps, "psn_client_instance", client)

    def run(iterations=1, on_wait=None):
        ev = StopAfter(iterations, on_wait)
        monkeypatch.setattr(ps, "stop_psn_thread_event", ev)
        ps.psn_data_update_loop()
        return ev

    return SimpleNamespace(client=client, state=state, clock=clock, broadcasts=broadcasts, net=net, run=run,
                           mp=monkeypatch)


def title_res(cid="NPWR1_00", **kw):
    return {"np_communication_id": cid, "title_name": "Returnal Trophies", "icon_url": "icon.png",
            "has_trophy_groups": True, **kw}


# --------------------------------------------------------------------------- guards / connection


def test_loop_exits_without_client(loop):
    loop.mp.setattr(ps, "psn_client_instance", None)
    ev = loop.run(1)
    assert ev.waits == []


def test_loop_no_npsso_pauses(loop):
    loop.client.psn_data.npsso_code = None
    ev = loop.run(2)
    assert ev.waits == [ps.SLEEP_PRESENCE, ps.SLEEP_PRESENCE]
    assert loop.state.set_calls == [loop.client.psn_data] * 2
    assert loop.client.calls == []


def test_loop_expired_token_skips_reconnect(loop):
    loop.client.connected = False
    loop.client.expired = True
    loop.run(1)
    assert loop.client.calls == [] and len(loop.state.set_calls) == 1


@pytest.mark.parametrize("internet,reach", [(False, True), (True, False)])
def test_loop_connectivity_failure_skips_connect(loop, internet, reach):
    loop.client.connected = False
    loop.net.update(internet=internet, reach=reach)
    loop.run(1)
    assert "connect" not in loop.client.names() and len(loop.state.set_calls) == 1


def test_loop_connectivity_check_error_still_connects_and_fails(loop):
    loop.client.connected = False
    loop.client.connect_result = False
    loop.net["raise"] = True
    loop.run(1)
    assert loop.client.names() == ["connect"] and len(loop.state.set_calls) == 1


def test_loop_reconnect_success_continues_to_presence(loop):
    loop.client.connected = False
    loop.client.presence = None
    loop.run(1)
    assert loop.client.names() == ["connect", "get_presence"]


# --------------------------------------------------------------------------- presence / summary


def test_loop_no_presence_clears_and_broadcasts(loop):
    loop.client.presence = None
    loop.client.psn_data.current_game_name = "Old"
    loop.run(1)
    assert loop.client.psn_data.current_game_name is None
    assert loop.broadcasts == [1]


def test_loop_no_game_in_presence(loop):
    loop.client.presence = {"basicPresence": {"availability": "available"}}
    loop.run(1)
    assert loop.client.names() == ["get_presence", "summary"]
    assert loop.broadcasts == [1]


def test_loop_root_level_presence_without_basic(loop):
    loop.client.presence = {"availability": "available", "gameTitleInfoList": []}
    loop.run(1)
    assert loop.broadcasts == [1]


def test_loop_presence_dict_empty_after_get(loop):
    """psn_data.presence falsy (e.g. API returned a truthy non-dict) -> treated as no game."""
    loop.client.presence = ["truthy-list"]
    loop.client.psn_data.presence = {}
    loop.client.get_presence = lambda: (loop.client.calls.append(("get_presence",)) or ["x"])
    loop.run(1)
    assert loop.broadcasts == [1]


def test_loop_summary_failure_with_expired_token_stops_iteration(loop):
    loop.client.summary = None
    loop.client.expired = True
    loop.run(1)
    assert loop.client.names() == ["get_presence", "summary"]
    assert loop.broadcasts == [] and len(loop.state.set_calls) == 1


def test_loop_summary_failure_not_expired_continues(loop):
    loop.client.summary = None
    loop.client.presence = {"basicPresence": {}}
    loop.run(1)
    assert loop.broadcasts == [1]


def test_loop_summary_cadence(loop):
    loop.client.presence = {"basicPresence": {}}

    def tick(n):
        loop.clock["t"] += 10  # 10 s per iteration

    loop.run(3, on_wait=tick)
    assert loop.client.names().count("summary") == 1
    loop.client.calls.clear()
    loop.clock["t"] += ps.OVERALL_SUMMARY_SEC
    loop.run(1)
    assert loop.client.names().count("summary") == 1


def test_loop_game_missing_ids(loop):
    loop.client.presence = {"basicPresence": {"gameTitleInfoList": [{"titleName": "App"}]}}
    loop.run(1)
    assert "by_title" not in loop.client.names() and loop.broadcasts == [1]


# --------------------------------------------------------------------------- title trophies + cache mapping


def test_loop_title_success_existing_seeded_doc_updates_without_group_flag(loop):
    c = loop.client
    doc = {"np_communication_id": "NPWR1_00", "np_title_id": "PPSA1_00", "trophy_groups": [{"g": 1}],
           "trophy_groups_updated_at": fresh_iso(), "cover_art_url": "old.png"}
    c.by_title["PPSA1_00"] = doc
    c.cache["NPWR1_00"] = doc
    c.title_result = title_res(icon_url=None)
    loop.run(1)
    upd = [x for x in c.calls if x[0] == "update_from"][0][2]
    assert "has_trophy_groups" not in upd
    assert upd["cover_art_url"] == "old.png" and upd["trophy_name"] == "Returnal Trophies"
    assert upd["presence_name"] == "Returnal" and upd["platform"] == "PS5"
    assert "groups" not in c.names()  # cache fresh
    assert c.psn_data.current_game_np_comm_id == "NPWR1_00"
    assert c.psn_data.current_game_mismatch is None
    assert loop.broadcasts == [1]


def test_loop_title_success_existing_unseeded_keeps_flag_and_written_none_falls_back(loop):
    c = loop.client
    existing = {"np_communication_id": "NPWR1_00", "trophy_groups": []}
    c.cache["NPWR1_00"] = existing
    c.by_name["Returnal"] = existing
    c.update_from_result = None
    c.title_result = title_res(title_name=None)
    c.groups_result = None
    loop.run(1)
    upd = [x for x in c.calls if x[0] == "update_from"][0][2]
    assert upd["has_trophy_groups"] is True and upd["trophy_name"] == "Returnal"
    assert ("cached", "NPWR1_00") in c.calls  # written None -> re-read
    assert "groups" in c.names()  # stale/missing groups -> refresh attempted


def test_loop_title_success_read_failure_skips_mapping(loop):
    c = loop.client
    c.read_ok = False
    c.title_result = title_res()
    c.groups_result = {"trophy_groups": [{"a": 1}]}
    loop.run(1)
    assert "update_from" not in c.names() and "store" not in c.names() and "update" not in c.names()


def test_loop_title_success_new_doc_stored_then_groups_stored(loop):
    c = loop.client
    c.title_result = title_res()
    c.groups_result = {"trophy_groups": [{"a": 1}, {"b": 2}]}
    original_checked = c.get_cached_game_data_checked
    seq = iter([(None, True), (None, True)])

    def checked(cid):
        c._log("checked", cid)
        return next(seq)

    c.get_cached_game_data_checked = checked
    loop.run(1)
    stores = [x[1] for x in c.calls if x[0] == "store"]
    assert stores[0]["trophy_groups"] == [] and stores[0]["cover_art_url"] == "icon.png"
    assert stores[1]["has_trophy_groups"] is True and stores[1]["trophy_groups"] == [{"a": 1}, {"b": 2}]
    assert original_checked is not None


def test_loop_groups_update_existing_doc(loop):
    c = loop.client
    c.cache["NPWR1_00"] = {"np_communication_id": "NPWR1_00", "trophy_groups": []}
    c.by_title["PPSA1_00"] = c.cache["NPWR1_00"]
    c.title_result = None
    c.groups_result = {"trophy_groups": [{"a": 1}]}
    loop.run(1)
    upd = [x for x in c.calls if x[0] == "update"][0][2]
    assert upd["has_trophy_groups"] is False and upd["trophy_groups"] == [{"a": 1}]
    assert "trophy_groups_updated_at" in upd


def test_loop_groups_result_unreadable_cache(loop):
    c = loop.client
    c.by_title["PPSA1_00"] = {"np_communication_id": "NPWR1_00"}
    c.read_ok = False
    c.title_result = None
    c.groups_result = {"trophy_groups": None}
    loop.run(1)
    assert "update" not in c.names() and "store" not in c.names()


def test_loop_groups_failure_with_expired_stops(loop):
    c = loop.client
    c.by_title["PPSA1_00"] = {"np_communication_id": "NPWR1_00"}
    c.title_result = None

    def groups(np_communication_id, platform):
        c._log("groups")
        c.expired = True
        return None

    c.get_game_trophies = groups
    loop.run(1)
    assert loop.broadcasts == [] and len(loop.state.set_calls) == 1


def test_loop_groups_failure_not_expired_broadcasts(loop):
    c = loop.client
    c.by_title["PPSA1_00"] = {"np_communication_id": "NPWR1_00"}
    c.title_result = None
    loop.run(1)
    assert loop.broadcasts == [1]


def test_loop_cached_doc_for_other_id_is_reloaded(loop):
    c = loop.client
    c.by_title["PPSA1_00"] = {"np_communication_id": "NPWR1_00"}
    c.title_result = title_res(cid="NPWR2_00")
    c.read_ok = False  # skip mapping write so cached_game stays the NPWR1 doc
    c.cache["NPWR2_00"] = {"np_communication_id": "NPWR2_00", "trophy_groups": [{"x": 1}],
                           "trophy_groups_updated_at": fresh_iso()}
    loop.run(1)
    assert ("cached", "NPWR2_00") in c.calls
    assert "groups" not in c.names()


# --------------------------------------------------------------------------- title failure / fallback / mismatch


def test_loop_title_failure_with_expired_stops(loop):
    c = loop.client
    c.title_result = None
    c.expired = True
    c.summary = object()
    loop.run(1)
    assert "all_games" not in c.names() and loop.broadcasts == []


def test_loop_title_failure_bulk_fallback_resolves(loop, monkeypatch):
    c = loop.client
    c.title_result = None
    calls = []

    def fallback(name, tid, platform):
        calls.append((name, tid, platform))
        return "NPWR7_00"

    monkeypatch.setattr(ps, "_resolve_game_via_bulk_fallback", fallback)
    c.cache["NPWR7_00"] = {"np_communication_id": "NPWR7_00", "trophy_groups": [{"a": 1}],
                           "trophy_groups_updated_at": fresh_iso()}
    loop.run(1)
    assert calls == [("Returnal", "PPSA1_00", "PS5")]
    assert ("cached", "NPWR7_00") in c.calls
    assert c.psn_data.current_game_mismatch is None


def test_loop_unresolved_game_creates_mismatch(loop, monkeypatch):
    c = loop.client
    c.title_result = None
    monkeypatch.setattr(ps, "_resolve_game_via_bulk_fallback", lambda *a: None)
    loop.run(1)
    m = c.psn_data.current_game_mismatch
    assert m.presence_name == "Returnal" and m.np_title_id == "PPSA1_00" and m.platform == "PS5"
    assert m.notified is False


def test_loop_same_game_skips_title_refresh_until_due(loop, monkeypatch):
    c = loop.client
    c.by_title["PPSA1_00"] = {"np_communication_id": "NPWR1_00", "trophy_groups": [{"a": 1}],
                              "trophy_groups_updated_at": fresh_iso()}
    c.cache["NPWR1_00"] = c.by_title["PPSA1_00"]
    c.title_result = title_res()

    def tick(n):
        loop.clock["t"] += 6

    loop.run(3, on_wait=tick)  # t=0, 6, 12 -> title only at first
    assert c.names().count("title") == 1
    loop.clock["t"] += ps.TROPHY_TITLE_REFRESH_SEC
    c.calls.clear()
    loop.run(1)
    assert c.names().count("title") == 1


def test_loop_no_mismatch_when_title_not_needed(loop, monkeypatch):
    """Second iteration with the same unresolved game and no due title refresh: no new mismatch record."""
    c = loop.client
    c.title_result = None
    monkeypatch.setattr(ps, "_resolve_game_via_bulk_fallback", lambda *a: None)

    def clear_mismatch(n):
        c.psn_data.current_game_mismatch = None

    loop.run(2, on_wait=clear_mismatch)
    assert c.names().count("title") == 1
    assert c.psn_data.current_game_mismatch is None


def test_loop_game_change_forces_title_refresh(loop):
    c = loop.client
    games = iter(["PPSA1_00", "PPSA2_00"])

    def presence():
        return {"basicPresence": {"gameTitleInfoList": [
            {"npTitleId": next(games), "format": "PS5", "titleName": "G"}]}}

    c.presence = presence
    c.title_result = title_res()
    c.read_ok = False
    c.groups_result = {"trophy_groups": []}
    loop.run(2)
    assert [x[1] for x in c.calls if x[0] == "title"] == ["PPSA1_00", "PPSA2_00"]


def test_loop_iteration_exception_recovers(loop):
    c = loop.client

    def boom():
        raise RuntimeError("presence broke")

    c.get_presence = boom
    ev = loop.run(2)
    assert ev.waits == [ps.SLEEP_PRESENCE, ps.SLEEP_PRESENCE]
    assert loop.state.set_calls == [c.psn_data, c.psn_data]


def test_loop_exception_after_client_cleared(loop):
    c = loop.client

    def boom():
        loop.mp.setattr(ps, "psn_client_instance", None)
        raise RuntimeError("gone")

    c.get_presence = boom
    loop.run(1)
    assert loop.state.set_calls == []


# --------------------------------------------------------------------------- bulk fallback


@pytest.fixture
def bulk(monkeypatch):
    c = LoopClient()
    monkeypatch.setattr(ps, "psn_client_instance", c)
    return c


def test_bulk_without_client(monkeypatch):
    monkeypatch.setattr(ps, "psn_client_instance", None)
    assert ps._resolve_game_via_bulk_fallback("G", "T", "PS5") is None


def test_bulk_exact_name_match_stores_entry(bulk):
    bulk.all_games = {
        "NPWR0": {"name": None},
        "NPWR1": {"name": "RETURNAL", "icon_url": "i.png", "platform": "PS5"},
    }
    assert ps._resolve_game_via_bulk_fallback("Returnal", "PPSA1", "PS4") == "NPWR1"
    stored = [x[1] for x in bulk.calls if x[0] == "store"][0]
    assert stored["trophy_name"] == "RETURNAL" and stored["cover_art_url"] == "i.png"
    assert stored["platform"] == "PS5" and stored["np_title_id"] == "PPSA1" and stored["trophy_groups"] == []
    assert bulk.psn_data.current_game_np_comm_id == "NPWR1"
    assert "resolve" not in bulk.names()


def test_bulk_normalized_name_match(bulk):
    bulk.all_games = {"NPWR1": {"name": "Returnal™ Trophies"}}
    assert ps._resolve_game_via_bulk_fallback("Returnal", "PPSA1", "PS5") == "NPWR1"


def test_bulk_fuzzy_match(bulk):
    bulk.all_games = {"NPWR2": {"name": "Horizon Forbidden West", "platform": "PS5"}}
    assert ps._resolve_game_via_bulk_fallback("Horizon Forbiden West", "PPSA2", "PS5") == "NPWR2"


def test_bulk_title_id_resolution_fallback(bulk):
    bulk.all_games = {"NPWR2": {"name": "Unrelated"}}
    bulk.resolved = "NPWR9"
    assert ps._resolve_game_via_bulk_fallback("Returnal", "PPSA1", "PS5") == "NPWR9"
    stored = [x[1] for x in bulk.calls if x[0] == "store"][0]
    assert stored["trophy_name"] == "Returnal" and stored["platform"] == "PS5" and stored["cover_art_url"] is None


def test_bulk_fetch_failed_and_unresolved(bulk):
    bulk.all_games = None
    bulk.resolved = None
    assert ps._resolve_game_via_bulk_fallback("Returnal", "PPSA1", "PS5") is None
    assert "store" not in bulk.names()


def test_bulk_no_title_id_skips_resolve(bulk):
    bulk.all_games = {}
    assert ps._resolve_game_via_bulk_fallback("Returnal", "", "PS5") is None
    assert "resolve" not in bulk.names()


def test_bulk_empty_games_with_resolution(bulk):
    bulk.all_games = {}
    bulk.resolved = "NPWR3"
    assert ps._resolve_game_via_bulk_fallback("Returnal", "PPSA1", "PS5") == "NPWR3"

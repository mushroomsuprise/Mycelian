"""RT-10 / PSN-01 / DBX-03: memoized fuzzy match and fewer PSN cache reads, same results."""

from __future__ import annotations

import copy
import json
import random

import pytest

from modules import psnapi


NAMES = [
    "Astro Bot",
    "ASTRO's PLAYROOM",
    "Gran Turismo 7",
    "Horizon Forbidden West",
    "Horizon Zero Dawn",
    "Ghost of Tsushima",
    "Ratchet & Clank: Rift Apart",
    "Returnal",
    "Spider-Man 2",
    "Spider-Man: Miles Morales",
]
QUERIES = ["Netflix", "YouTube", "Horizon Forbiden West", "Spiderman 2", "Astro", "Returnal"]


def test_memoized_fuzzy_matches_direct_across_mutations(monkeypatch):
    monkeypatch.setattr(psnapi, "_fuzzy_name_memo", {})
    rng = random.Random(7)
    docs = [
        {"np_communication_id": f"NPWR{i}", "trophy_name": n, "platform": rng.choice(["PS4", "PS5"])}
        for i, n in enumerate(NAMES)
    ]
    calls = {"n": 0}
    real = psnapi.find_best_fuzzy_game_name_match

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(psnapi, "find_best_fuzzy_game_name_match", counting)
    for step in range(300):
        roll = rng.random()
        if roll < 0.05:
            docs.append({"np_communication_id": f"X{step}", "presence_name": rng.choice(NAMES) + " Demo"})
        elif roll < 0.08 and docs:
            docs.pop(rng.randrange(len(docs)))
        elif roll < 0.12 and docs:
            rng.choice(docs)["presence_name"] = rng.choice(NAMES + QUERIES)
        elif roll < 0.14 and docs:
            i = rng.randrange(len(docs))
            docs[i] = dict(docs[i])  # replaced by a new document object
        query = rng.choice(QUERIES)
        platform = rng.choice([None, "PS4", "PS5"])
        snapshot = list(docs)
        got = psnapi._memoized_fuzzy_game_name_match(query, snapshot, platform)
        want = real(query, list(docs), platform=platform)
        assert got is want or (got is None and want is None)
    # Unchanged cache: each (query, platform) is scored once, then reused.
    calls["n"] = 0
    for _ in range(100):
        for query in QUERIES:
            psnapi._memoized_fuzzy_game_name_match(query, list(docs), "PS5")
    assert calls["n"] <= len(QUERIES)


class _FakeDB:
    def __init__(self, docs):
        self.store = {k: json.dumps(v) for k, v in docs.items()}
        self.gets = 0
        self.sets = []

    def get_data(self, path):
        self.gets += 1
        raw = self.store.get(path)
        return json.loads(raw) if raw is not None else None

    def set_data(self, path, value):
        self.sets.append((path, json.dumps(value, sort_keys=True)))
        self.store[path] = json.dumps(value)
        return True


def _doc():
    return {
        "np_communication_id": "NPWR1_00",
        "np_title_id": "PPSA1_00",
        "presence_name": "Returnal",
        "trophy_name": "Returnal",
        "platform": "PS5",
        "trophy_groups": [{"trophy_group_id": "default"}],
        "trophy_groups_updated_at": "2026-01-01T00:00:00",
    }


@pytest.fixture()
def frozen_now(monkeypatch):
    class FixedDatetime(psnapi.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 22, 12, 0, 0)

    monkeypatch.setattr(psnapi, "datetime", FixedDatetime)


def test_update_from_existing_writes_same_document(monkeypatch, frozen_now):
    path = f"{psnapi.PSN_GAME_DATA_PATH}/NPWR1_00"
    updates = {"presence_name": "Returnal", "cover_art_url": None, "platform": "PS5"}

    old_db = _FakeDB({path: _doc()})
    monkeypatch.setattr(psnapi, "database_manager", old_db)
    existing = psnapi.get_psn_game_cache_by_comm_id("NPWR1_00")
    assert psnapi.update_psn_game_cache_in_db("NPWR1_00", updates)
    old_reread = psnapi.get_psn_game_cache_by_comm_id("NPWR1_00")

    new_db = _FakeDB({path: _doc()})
    monkeypatch.setattr(psnapi, "database_manager", new_db)
    existing2 = psnapi.get_psn_game_cache_by_comm_id("NPWR1_00")
    before = copy.deepcopy(existing2)
    written = psnapi.update_psn_game_cache_from_existing("NPWR1_00", updates, existing2)

    assert new_db.sets == old_db.sets  # identical PUT
    assert written == old_reread  # what the extra re-read used to return
    assert existing2 == before  # caller's copy untouched
    assert existing == before
    assert (old_db.gets, new_db.gets) == (3, 1)


def test_update_from_existing_failure_returns_none(monkeypatch):
    db = _FakeDB({})
    db.set_data = lambda path, value: False
    monkeypatch.setattr(psnapi, "database_manager", db)
    assert psnapi.update_psn_game_cache_from_existing("NPWR1_00", {"a": 1}, _doc()) is None

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/psnapi.py module-level helpers: name matching, cache index, DB cache helpers,
error classification, platform conversion and the forbidden notice. PSNClient lives in _part2."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psnawp_api.core.psnawp_exceptions import (  # noqa: E402
    PSNAWPAuthenticationError,
    PSNAWPForbiddenError,
    PSNAWPServerError,
    PSNAWPUnauthorizedError,
)
from psnawp_api.models.trophies import PlatformType  # noqa: E402
from requests.exceptions import ConnectionError as RequestsConnectionError  # noqa: E402

from modules import psnapi  # noqa: E402

PREFIX = psnapi.PSN_GAME_DATA_PATH


class FakeDB:
    """Stands in for database_manager: dict-backed paths, configurable backend type and failures."""

    def __init__(self, store=None, db_type="sql", fail=None):
        self.store = dict(store or {})
        self.db_type = db_type
        self.fail = set(fail or ())
        self.sets = []
        self.deletes = []

    def get_config(self):
        return SimpleNamespace(database_type=self.db_type)

    def get_data(self, path):
        if "get" in self.fail:
            raise RuntimeError("db down")
        if path == PREFIX:
            sub = {k[len(PREFIX) + 1:]: v for k, v in self.store.items() if k.startswith(PREFIX + "/")}
            return self.store.get(PREFIX, sub or None)
        return self.store.get(path)

    def get_data_checked(self, path):
        if "checked_raise" in self.fail:
            raise RuntimeError("boom")
        if "checked" in self.fail:
            return None, False
        return self.store.get(path), True

    def get_all_paths(self):
        if "paths" in self.fail:
            raise RuntimeError("paths")
        return list(self.store)

    def set_data(self, path, value):
        if "set_raise" in self.fail:
            raise RuntimeError("set")
        self.sets.append((path, dict(value)))
        if "set" in self.fail:
            return False
        self.store[path] = value
        return True

    def delete_data(self, path):
        if "delete_raise" in self.fail:
            raise RuntimeError("del")
        self.deletes.append(path)
        if "delete" in self.fail:
            return False
        self.store.pop(path, None)
        return True


@pytest.fixture(autouse=True)
def fresh_index(monkeypatch):
    monkeypatch.setattr(psnapi, "_game_cache_by_title_id", {})
    monkeypatch.setattr(psnapi, "_game_cache_by_name", {})
    monkeypatch.setattr(psnapi, "_game_cache_by_comm_id", {})
    monkeypatch.setattr(psnapi, "_game_cache_index_loaded", False)
    monkeypatch.setattr(psnapi, "_fuzzy_name_memo", {})


def install_db(monkeypatch, **kw):
    db = FakeDB(**kw)
    monkeypatch.setattr(psnapi, "database_manager", db)
    return db


def doc(cid="NPWR1_00", title="CUSA1_00", presence="Game One", trophy="Game One Trophies", platform="PS5"):
    return {"np_communication_id": cid, "np_title_id": title, "presence_name": presence,
            "trophy_name": trophy, "platform": platform}


# --------------------------------------------------------------------------- name normalization


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("Game™ Name® (c)©", "game name c"),
        ("Tony Hawk‘s", "tony hawk's"),
        ("A—B–C−D", "a b c d"),
        ("Fancy Game Trophy Set", "fancy game"),
        ("Fancy Game Trophy Collection for PS5", "fancy game"),
        ("Ratchet & Clank: Rift-Apart!", "ratchet & clank rift apart"),
        ("Disney+ App", "disney+ app"),
        ("ＡＢＣ", "abc"),  # fullwidth NFKC fold
        (123, "123"),
    ],
)
def test_normalize_game_name_key(raw, expected):
    assert psnapi._normalize_game_name_key(raw) == expected
    assert psnapi.normalize_game_name_key is psnapi._normalize_game_name_key


@pytest.mark.parametrize(
    "raw,expected", [(None, ""), (" ps 5 ", "PS5"), ("ps_vita", "PSVITA"), (4, "4")]
)
def test_normalize_platform_token(raw, expected):
    assert psnapi._normalize_platform_token(raw) == expected


@pytest.mark.parametrize(
    "cand,wanted,ok",
    [
        ("PS4", None, True),
        ("PS4", "", True),
        (None, "PS5", True),
        ("PS4", "  ", True),
        ("PS5", "ps5", True),
        ("PS4,PS5", "PS5", True),
        ("PS5", "PS4,PS5", True),
        ("PS4", "PS5", False),
    ],
)
def test_platforms_compatible(cand, wanted, ok):
    assert psnapi._platforms_compatible(cand, wanted) is ok


# --------------------------------------------------------------------------- fuzzy match


def test_fuzzy_short_query_returns_none():
    assert psnapi.find_best_fuzzy_game_name_match("abc", [{"name": "abc"}]) is None


def test_fuzzy_skips_non_dicts_and_empty_fields():
    cands = ["x", None, {"name": ""}, {"presence_name": "!!!"}]
    assert psnapi.find_best_fuzzy_game_name_match("Returnal", cands) is None


def test_fuzzy_exact_normalized_wins():
    a = {"trophy_name": "Returnal™ Trophies"}
    b = {"name": "Something Else Entirely"}
    assert psnapi.find_best_fuzzy_game_name_match("Returnal", [b, a]) is a


def test_fuzzy_near_match_above_threshold():
    a = {"presence_name": "Horizon Forbidden West"}
    assert psnapi.find_best_fuzzy_game_name_match("Horizon Forbiden West", [a]) is a


def test_fuzzy_below_threshold_returns_none():
    assert psnapi.find_best_fuzzy_game_name_match("Horizon", [{"name": "Gran Turismo"}]) is None


def test_fuzzy_ambiguous_returns_none():
    a = {"name": "Spider-Man 2"}
    b = {"name": "Spider-Man 2"}
    assert psnapi.find_best_fuzzy_game_name_match("Spider Man 2", [a, b]) is None


def test_fuzzy_platform_pool_breaks_tie():
    a = {"name": "Spider-Man 2", "platform": "PS4"}
    b = {"name": "Spider-Man 2", "platform": "PS5"}
    assert psnapi.find_best_fuzzy_game_name_match("Spider Man 2", [a, b], platform="PS5") is b


def test_fuzzy_platform_pool_empty_falls_back_to_all():
    a = {"name": "Returnal", "platform": "PS4"}
    assert psnapi.find_best_fuzzy_game_name_match("Returnal", [a], platform="PS5") is a


def test_fuzzy_custom_name_fields_and_label_fallback(caplog):
    a = {"title": "Returnal"}
    assert psnapi.find_best_fuzzy_game_name_match("Returnal", [a], name_fields=("title",)) is a


def test_fuzzy_candidate_keeps_best_ratio_across_fields():
    a = {"presence_name": "Zzzz Qqqq", "trophy_name": "Horizon Forbiden West"}
    assert psnapi.find_best_fuzzy_game_name_match("Horizon Forbidden West", [a]) is a


# --------------------------------------------------------------------------- fuzzy memo


def test_fuzzy_candidate_fields_shape():
    docs = [{"presence_name": "a", "trophy_name": "b", "name": "c", "platform": "PS5"}, "nope"]
    assert psnapi._fuzzy_candidate_fields(docs) == (("a", "b", "c", "PS5"), None)


def test_memo_reuses_and_invalidates(monkeypatch):
    calls = []
    real = psnapi.find_best_fuzzy_game_name_match

    def spy(q, d, platform=None):
        calls.append(q)
        return real(q, d, platform=platform)

    monkeypatch.setattr(psnapi, "find_best_fuzzy_game_name_match", spy)
    d = {"name": "Returnal"}
    docs = [d]
    assert psnapi._memoized_fuzzy_game_name_match("Returnal", docs, None) is d
    assert psnapi._memoized_fuzzy_game_name_match("Returnal", [d], None) is d
    assert len(calls) == 1
    d["name"] = "Other"  # field changed -> rescore
    assert psnapi._memoized_fuzzy_game_name_match("Returnal", [d], None) is None
    assert len(calls) == 2
    # different object with same fields -> rescore
    psnapi._memoized_fuzzy_game_name_match("Returnal", [dict(d)], None)
    assert len(calls) == 3
    # different length -> rescore
    psnapi._memoized_fuzzy_game_name_match("Returnal", [d, d], None)
    assert len(calls) == 4


def test_memo_evicts_oldest_when_full(monkeypatch):
    monkeypatch.setattr(psnapi, "_FUZZY_NAME_MEMO_MAX", 2)
    psnapi._memoized_fuzzy_game_name_match("aaaa", [], None)
    psnapi._memoized_fuzzy_game_name_match("bbbb", [], None)
    psnapi._memoized_fuzzy_game_name_match("cccc", [], None)
    assert list(psnapi._fuzzy_name_memo) == [("bbbb", None), ("cccc", None)]
    # existing key does not evict
    psnapi._memoized_fuzzy_game_name_match("cccc", [{"name": "x"}], None)
    assert len(psnapi._fuzzy_name_memo) == 2


# --------------------------------------------------------------------------- cache index


def test_index_game_cache_doc_all_keys():
    d = doc(presence="Game™ One", trophy="Game One Trophies")
    psnapi._index_game_cache_doc(d)
    assert psnapi._game_cache_by_comm_id == {"NPWR1_00": d}
    assert psnapi._game_cache_by_title_id == {"CUSA1_00": d}
    assert set(psnapi._game_cache_by_name) == {"game one", "game™ one", "game one trophies"}


def test_index_game_cache_doc_ignores_non_dict_and_missing_ids():
    psnapi._index_game_cache_doc("nope")
    psnapi._index_game_cache_doc({"presence_name": None})
    assert psnapi._game_cache_by_comm_id == {} and psnapi._game_cache_by_name == {}


def test_ensure_index_loads_once(monkeypatch):
    loads = []
    monkeypatch.setattr(psnapi, "load_all_psn_game_cache_docs_from_db",
                        lambda: loads.append(1) or [doc(), doc("NPWR2_00", "CUSA2_00", "Two", "Two")])
    psnapi._game_cache_by_title_id["stale"] = {}
    psnapi._ensure_game_cache_index()
    psnapi._ensure_game_cache_index()
    assert loads == [1]
    assert psnapi._game_cache_index_loaded is True
    assert set(psnapi._game_cache_by_title_id) == {"CUSA1_00", "CUSA2_00"}


def test_invalidate_clears_everything():
    psnapi._index_game_cache_doc(doc())
    psnapi._game_cache_index_loaded = True
    psnapi._invalidate_game_cache_index()
    assert not psnapi._game_cache_by_title_id and not psnapi._game_cache_by_name
    assert not psnapi._game_cache_by_comm_id and psnapi._game_cache_index_loaded is False


# --------------------------------------------------------------------------- trophy helpers


def test_trophy_set_to_counts_variants():
    assert psnapi._trophy_set_to_counts(None) == {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0, "total": 0}
    assert psnapi._trophy_set_to_counts({"bronze": 3, "silver": None, "gold": "2", "platinum": 1}) == {
        "bronze": 3, "silver": 0, "gold": 2, "platinum": 1, "total": 6}
    obj = SimpleNamespace(bronze=1, silver=2, gold=3, platinum=None)
    assert psnapi._trophy_set_to_counts(obj)["total"] == 6


def test_serialize_trophy_groups_empty():
    assert psnapi._serialize_trophy_groups_for_cache(None) == []
    assert psnapi._serialize_trophy_groups_for_cache([]) == []


def test_serialize_trophy_groups_dicts_and_objects():
    groups = [
        {"trophyGroupId": "default", "trophyGroupName": "Base", "definedTrophies": {"bronze": 2, "platinum": 1}},
        {"trophy_group_id": "001", "trophy_group_name": "DLC", "defined_trophies": {"gold": 1}},
        {},
        SimpleNamespace(trophy_group_id="002", trophy_group_name=None,
                        defined_trophies=SimpleNamespace(bronze=1, silver=1, gold=0, platinum=0)),
        SimpleNamespace(),
    ]
    out = psnapi._serialize_trophy_groups_for_cache(groups)
    assert out[0] == {"trophy_group_id": "default", "group_name": "Base", "is_base_game": True,
                      "defined_trophies": {"bronze": 2, "silver": 0, "gold": 0, "platinum": 1}}
    assert out[1]["trophy_group_id"] == "001" and out[1]["group_name"] == "DLC"
    assert out[1]["defined_trophies"]["gold"] == 1 and out[1]["is_base_game"] is False
    assert out[2] == {"trophy_group_id": "", "group_name": "Unknown", "is_base_game": False,
                      "defined_trophies": {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}}
    assert out[3]["group_name"] == "Unknown" and out[3]["defined_trophies"]["silver"] == 1
    assert out[4]["trophy_group_id"] == "" and out[4]["group_name"] == "Unknown"


# --------------------------------------------------------------------------- error classification


@pytest.mark.parametrize(
    "exc,expired",
    [
        (PSNAWPAuthenticationError("x"), True),
        (PSNAWPUnauthorizedError("x"), True),
        (RuntimeError("Expired Token"), True),
        (RuntimeError("expired access token here"), True),
        (RuntimeError("other"), False),
    ],
)
def test_is_psn_auth_expired_error(exc, expired):
    assert psnapi._is_psn_auth_expired_error(exc) is expired


@pytest.mark.parametrize(
    "exc,conn",
    [
        (RequestsConnectionError("x"), True),
        (OSError("x"), True),
        (TimeoutError("x"), True),
        (PSNAWPServerError("x"), True),
        (RuntimeError("503 Service Unavailable"), True),
        (RuntimeError("other"), False),
    ],
)
def test_is_psn_connection_error(exc, conn):
    assert psnapi._is_psn_connection_error(exc) is conn


@pytest.mark.parametrize(
    "fmt,expected",
    [
        (None, PlatformType.UNKNOWN),
        ("  ", PlatformType.UNKNOWN),
        ("ps5", PlatformType.PS5),
        ("PS 4", PlatformType.PS4),
        ("PS_VITA", PlatformType.PS_VITA),
        ("psvita", PlatformType.PS_VITA),
        ("Vita", PlatformType.PS_VITA),
        ("pc", PlatformType.PSPC),
        ("PSPC", PlatformType.PSPC),
        ("PS3", PlatformType.PS3),
    ],
)
def test_presence_format_to_platform_type(fmt, expected):
    assert psnapi.presence_format_to_platform_type(fmt) is expected


def test_presence_format_unknown_value_maps_to_unknown():
    assert psnapi.presence_format_to_platform_type("XBOX") is PlatformType.UNKNOWN


def test_fallback_exception_classes_when_psnawp_lacks_them(monkeypatch):
    """Older psnawp has no PSNAWPServerError/PSNAWPUnauthorizedError: the module defines stand-ins."""
    import importlib.util
    import types

    import psnawp_api.core.psnawp_exceptions as real

    fake = types.ModuleType("psnawp_api.core.psnawp_exceptions")
    for name in ("PSNAWPAuthenticationError", "PSNAWPBadRequestError", "PSNAWPForbiddenError",
                 "PSNAWPNotFoundError"):
        setattr(fake, name, getattr(real, name))
    monkeypatch.setitem(sys.modules, "psnawp_api.core.psnawp_exceptions", fake)
    name = "modules._psnapi_fallback_copy"
    spec = importlib.util.spec_from_file_location(name, ROOT / "modules" / "psnapi.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    assert mod.PSNAWPServerError is not real.PSNAWPServerError
    assert issubclass(mod.PSNAWPServerError, Exception)
    assert mod._is_psn_connection_error(mod.PSNAWPServerError("x")) is True
    assert mod._is_psn_auth_expired_error(mod.PSNAWPUnauthorizedError("x")) is True


# --------------------------------------------------------------------------- load all docs


def test_load_all_sql_merges_parent_and_paths(monkeypatch):
    store = {
        f"{PREFIX}/NPWR1_00": doc("NPWR1_00", trophy="Zeta"),
        f"/{PREFIX}/NPWR2_00": {"trophy_name": "alpha"},  # leading slash, id from path
        f"{PREFIX}/NPWR3_00/sub": {"x": 1},  # nested -> skipped
        f"{PREFIX}/BAD": "not a dict",
        "Other/path": {"np_communication_id": "X"},
        PREFIX: {"NPWR9_00": {"trophy_name": "Mid"}, "skip": "str", "": {"trophy_name": "noid"}},
    }
    db = install_db(monkeypatch, store=store)

    def get_data(path):
        if path.startswith("/"):
            path = path[1:]
        if path == f"{PREFIX}/NPWR2_00":
            return store[f"/{PREFIX}/NPWR2_00"]
        return store.get(path)

    db.get_data = get_data
    out = psnapi.load_all_psn_game_cache_docs_from_db()
    assert [d["np_communication_id"] for d in out] == ["NPWR2_00", "NPWR9_00", "NPWR1_00"]
    assert out[0]["trophy_name"] == "alpha"


def test_load_all_path_row_wins_over_parent(monkeypatch):
    store = {
        PREFIX: {"NPWR1_00": {"np_communication_id": "NPWR1_00", "trophy_name": "Parent"}},
        f"{PREFIX}/NPWR1_00": {"np_communication_id": "NPWR1_00", "trophy_name": "Path"},
    }
    install_db(monkeypatch, store=store)
    out = psnapi.load_all_psn_game_cache_docs_from_db()
    assert len(out) == 1 and out[0]["trophy_name"] == "Path"


def test_load_all_skips_exact_prefix_and_empty_rel(monkeypatch):
    store = {f"{PREFIX}/": {"trophy_name": "empty rel"}}
    db = install_db(monkeypatch, store=store)
    db.get_data = lambda p: store.get(p) if p != PREFIX else None
    assert psnapi.load_all_psn_game_cache_docs_from_db() == []


def test_load_all_firebase_uses_parent_only(monkeypatch):
    db = install_db(monkeypatch, store={PREFIX: {"NPWR5_00": {"trophy_name": "B"},
                                                 "k": {"np_communication_id": "NPWR6_00", "trophy_name": "a"}}},
                    db_type="firebase")
    db.get_all_paths = lambda: pytest.fail("firebase must not list all paths")
    out = psnapi.load_all_psn_game_cache_docs_from_db()
    assert [d["np_communication_id"] for d in out] == ["NPWR6_00", "NPWR5_00"]


def test_load_all_error_returns_empty(monkeypatch):
    install_db(monkeypatch, fail={"get"})
    assert psnapi.load_all_psn_game_cache_docs_from_db() == []


def test_load_all_handles_missing_trophy_name_in_sort(monkeypatch):
    install_db(monkeypatch, store={f"{PREFIX}/A": {"np_communication_id": "A"},
                                   f"{PREFIX}/B": {"np_communication_id": "B", "trophy_name": "b"}})
    assert [d["np_communication_id"] for d in psnapi.load_all_psn_game_cache_docs_from_db()] == ["A", "B"]


# --------------------------------------------------------------------------- single doc get/update/delete


def test_get_by_comm_id_checked_paths(monkeypatch):
    assert psnapi.get_psn_game_cache_by_comm_id_checked("") == (None, True)
    db = install_db(monkeypatch, store={f"{PREFIX}/NPWR1_00": doc(), f"{PREFIX}/NOID": {"x": 1}})
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NPWR1_00") == (doc(), True)
    assert psnapi.get_psn_game_cache_by_comm_id("NPWR1_00") == doc()
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NOID") == (None, True)
    assert psnapi.get_psn_game_cache_by_comm_id_checked("MISSING") == (None, True)
    db.fail = {"checked"}
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NPWR1_00") == (None, False)
    db.fail = {"checked_raise"}
    assert psnapi.get_psn_game_cache_by_comm_id_checked("NPWR1_00") == (None, False)
    assert psnapi.get_psn_game_cache_by_comm_id("NPWR1_00") is None


def test_update_in_db_success_updates_index(monkeypatch):
    db = install_db(monkeypatch, store={f"{PREFIX}/NPWR1_00": doc()})
    psnapi._game_cache_index_loaded = True
    assert psnapi.update_psn_game_cache_in_db("NPWR1_00", {"presence_name": "Renamed"}) is True
    path, written = db.sets[-1]
    assert path == f"{PREFIX}/NPWR1_00"
    assert written["presence_name"] == "Renamed" and "last_updated" in written
    assert psnapi._game_cache_by_name["renamed"]["np_communication_id"] == "NPWR1_00"


def test_update_in_db_index_not_loaded_is_left_alone(monkeypatch):
    install_db(monkeypatch, store={f"{PREFIX}/NPWR1_00": doc()})
    assert psnapi.update_psn_game_cache_in_db("NPWR1_00", {"x": 1})
    assert psnapi._game_cache_by_comm_id == {}


@pytest.mark.parametrize("cid", ["", None])
def test_update_missing_id(monkeypatch, cid):
    db = install_db(monkeypatch)
    assert psnapi.update_psn_game_cache_in_db(cid, {"a": 1}) is False
    assert db.sets == []


def test_update_not_found(monkeypatch):
    db = install_db(monkeypatch)
    assert psnapi.update_psn_game_cache_in_db("NOPE", {"a": 1}) is False
    assert db.sets == []


def test_update_set_fails(monkeypatch):
    install_db(monkeypatch, store={f"{PREFIX}/NPWR1_00": doc()}, fail={"set"})
    assert psnapi.update_psn_game_cache_in_db("NPWR1_00", {"a": 1}) is False


def test_update_set_raises(monkeypatch):
    install_db(monkeypatch, store={f"{PREFIX}/NPWR1_00": doc()}, fail={"set_raise"})
    assert psnapi.update_psn_game_cache_in_db("NPWR1_00", {"a": 1}) is False


def test_update_from_existing_does_not_mutate_input(monkeypatch):
    db = install_db(monkeypatch)
    existing = doc()
    out = psnapi.update_psn_game_cache_from_existing("NPWR1_00", {"presence_name": "X"}, existing)
    assert out["presence_name"] == "X" and existing["presence_name"] == "Game One"
    assert db.sets[0][1]["presence_name"] == "X"


def test_delete_in_db(monkeypatch):
    assert psnapi.delete_psn_game_cache_in_db("") is False
    db = install_db(monkeypatch, store={f"{PREFIX}/NPWR1_00": doc()})
    psnapi._index_game_cache_doc(doc())
    psnapi._game_cache_index_loaded = True
    assert psnapi.delete_psn_game_cache_in_db("NPWR1_00") is True
    assert db.deletes == [f"{PREFIX}/NPWR1_00"]
    assert psnapi._game_cache_index_loaded is False and psnapi._game_cache_by_comm_id == {}
    db.fail = {"delete"}
    assert psnapi.delete_psn_game_cache_in_db("NPWR1_00") is False
    db.fail = {"delete_raise"}
    assert psnapi.delete_psn_game_cache_in_db("NPWR1_00") is False


# --------------------------------------------------------------------------- forbidden notice


def test_notify_forbidden_with_target(monkeypatch):
    from modules import notification_engine

    sent = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: sent.append((msg, kw)))
    exc = PSNAWPForbiddenError("nope")
    psnapi._notify_psn_forbidden("Friend", exc)
    msg, kw = sent[0]
    assert "Friend's trophy data" in msg
    assert kw["type"] == "warning" and kw["source"] == "psn"
    assert kw["dedupe_key"] == "psn:forbidden" and kw["dedupe_cooldown_sec"] == 6 * 3600.0
    assert kw["details"] == "PSNAWPForbiddenError: nope"
    assert kw["actions"] == notification_engine.nav_actions_settings("PSN")


def test_notify_forbidden_self_and_failure(monkeypatch):
    from modules import notification_engine

    sent = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: sent.append(msg))
    psnapi._notify_psn_forbidden("", RuntimeError("x"))
    assert "your trophy data" in sent[0]

    def boom(*a, **k):
        raise RuntimeError("notify broken")

    monkeypatch.setattr(notification_engine, "notify", boom)
    psnapi._notify_psn_forbidden("x", RuntimeError("y"))  # swallowed


def test_module_main_block_prints_placeholder_message(capsys):
    import runpy
    import logging

    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            runpy.run_module("modules.psnapi", run_name="__main__")
    finally:
        root.setLevel(saved[0])
        root.handlers[:] = saved[1]
    assert "YOUR_NPSSO_CODE_HERE" in capsys.readouterr().out

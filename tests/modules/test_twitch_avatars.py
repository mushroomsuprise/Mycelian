# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch profile picture lookups: login rules, cache expiry, saving, and the Helix call."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import twitch, twitch_avatars as ta  # noqa: E402

PIC = "https://static-cdn.jtvnw.net/jtv_user_pictures/abc-profile_image-300x300.png"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(ta, "_cache_path", lambda: str(tmp_path / "data" / "twitch_avatars.json"))
    ta._reset_for_tests()
    yield tmp_path / "data" / "twitch_avatars.json"
    ta._reset_for_tests()


class SyncThread:
    """Runs the lookup inline so tests don't wait on a real thread."""

    def __init__(self, target, args=(), name=None, daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(ta.threading, "Thread", SyncThread)


def fake_fetch(monkeypatch, pictures, calls=None):
    def fetch(batch):
        if calls is not None:
            calls.append(list(batch))
        return None if pictures is None else {k: v for k, v in pictures.items() if k in batch}

    monkeypatch.setattr(ta, "_fetch_batch", fetch)


@pytest.mark.parametrize("name, login", [
    ("MushroomSuprise", "mushroomsuprise"),
    ("  daniel_schell_86 ", "daniel_schell_86"),
    ("Some Name", None),
    ("名前", None),
    ("", None),
    ("a" * 26, None),
])
def test_login_for(name, login):
    assert ta.login_for(name) == login


def test_request_fetches_unknown_names_and_reports_new_pictures(inline, monkeypatch, fresh):
    calls, updates = [], []
    fake_fetch(monkeypatch, {"alice": PIC}, calls)
    assert ta.request_avatars(["Alice", "bob", "Not A Login"], on_update=lambda: updates.append(1)) is True
    assert calls == [["alice", "bob"]]
    assert updates == [1]
    assert ta.cached_avatars(["Alice", "bob"]) == {"Alice": PIC}
    # Both answers are remembered, so nothing is asked again.
    assert ta.request_avatars(["alice", "BOB"]) is False
    saved = json.loads(fresh.read_text())
    assert saved["alice"][0] == PIC and saved["bob"][0] == ""


def test_no_update_callback_when_nothing_new(inline, monkeypatch):
    updates = []
    fake_fetch(monkeypatch, {})
    ta.request_avatars(["ghost"], on_update=lambda: updates.append(1))
    assert updates == []


def test_twitch_unavailable_is_asked_again_later(inline, monkeypatch, fresh):
    fake_fetch(monkeypatch, None)
    ta.request_avatars(["alice"])
    assert ta.cached_avatars(["alice"]) == {}
    assert not fresh.exists()
    calls = []
    fake_fetch(monkeypatch, {"alice": PIC}, calls)
    assert ta.request_avatars(["alice"]) is True and calls == [["alice"]]


def test_pictures_expire_and_get_refreshed(inline, monkeypatch):
    fake_fetch(monkeypatch, {"alice": PIC})
    ta.request_avatars(["alice", "ghost"])
    later = time.time() + ta.MISSING_TTL_SEC + 1
    monkeypatch.setattr(ta.time, "time", lambda: later)
    assert ta.cached_avatars(["alice"]) == {"alice": PIC}  # pictures last longer
    calls = []
    fake_fetch(monkeypatch, {}, calls)
    ta.request_avatars(["alice", "ghost"])
    assert calls == [["ghost"]]
    much_later = time.time() + ta.FOUND_TTL_SEC + 1
    monkeypatch.setattr(ta.time, "time", lambda: much_later)
    assert ta.cached_avatars(["alice"]) == {}
    ta.request_avatars(["alice"])
    assert calls[-1] == ["alice"]


def test_names_already_being_fetched_are_skipped(monkeypatch):
    started = []
    monkeypatch.setattr(ta.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw["args"][0])))
    assert ta.request_avatars(["alice", "Alice", "bob"]) is True
    assert started == [["alice", "bob"]]
    assert ta.request_avatars(["alice"]) is False


def test_big_lists_are_split_into_helix_batches(inline, monkeypatch):
    calls = []
    fake_fetch(monkeypatch, {}, calls)
    ta.request_avatars([f"user{i}" for i in range(250)])
    assert [len(c) for c in calls] == [100, 100, 50]


def test_failed_batch_keeps_earlier_batches(inline, monkeypatch, fresh):
    answers = iter([{"user0": PIC}, None])
    monkeypatch.setattr(ta, "_fetch_batch", lambda batch: next(answers))
    ta.request_avatars([f"user{i}" for i in range(150)])
    assert ta.cached_avatars(["user0"]) == {"user0": PIC}
    assert "user0" in json.loads(fresh.read_text())
    assert ta.request_avatars(["user120"]) is True  # never stored, so asked again


def test_saved_cache_is_read_back(fresh):
    fresh.parent.mkdir(parents=True)
    now = time.time()
    fresh.write_text(json.dumps({
        "alice": [PIC, now], "bob": ["", now], "Bad Name": [PIC, now], "eve": ["http://insecure", now],
        "carl": "broken",
    }))
    assert ta.cached_avatars(["alice", "bob", "eve", "carl"]) == {"alice": PIC}
    assert ta.request_avatars(["bob"]) is False  # remembered as unknown


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_unreadable_cache_is_ignored(fresh, content):
    fresh.parent.mkdir(parents=True)
    fresh.write_text(content)
    assert ta.cached_avatars(["alice"]) == {}


def test_saved_cache_keeps_the_newest(inline, monkeypatch, fresh):
    monkeypatch.setattr(ta, "MAX_SAVED", 2)
    clock = iter([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])
    monkeypatch.setattr(ta.time, "time", lambda: next(clock, 700.0))
    fake_fetch(monkeypatch, {"a": PIC, "b": PIC, "c": PIC})
    for login in ("a", "b", "c"):
        ta.request_avatars([login])
    assert sorted(json.loads(fresh.read_text())) == ["b", "c"]


def test_save_failure_is_logged_not_raised(inline, monkeypatch):
    monkeypatch.setattr(ta.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    fake_fetch(monkeypatch, {"alice": PIC})
    ta.request_avatars(["alice"])
    assert ta.cached_avatars(["alice"]) == {"alice": PIC}


def test_update_callback_errors_are_contained(inline, monkeypatch):
    fake_fetch(monkeypatch, {"alice": PIC})

    def boom():
        raise RuntimeError("ui gone")

    ta.request_avatars(["alice"], on_update=boom)
    assert ta.cached_avatars(["alice"]) == {"alice": PIC}


def test_unexpected_fetch_error_frees_the_names(inline, monkeypatch):
    monkeypatch.setattr(ta, "_fetch_batch", lambda batch: 1 / 0)
    ta.request_avatars(["alice"])
    assert ta._pending == set()


# --------------------------------------------------------------------------- Helix call


class FakeApi:
    def __init__(self, response=None, error=None, connected=True):
        self.is_connected = connected
        self.response, self.error, self.urls = response, error, []

    async def generic_api_call(self, url, method="GET", **kwargs):
        self.urls.append((url, method))
        if self.error:
            raise self.error
        return self.response


def test_fetch_batch_reads_helix_users(monkeypatch):
    api = FakeApi({"data": [
        {"login": "Alice", "profile_image_url": PIC},
        {"login": "bob", "profile_image_url": "http://not-https"},
        {"login": "", "profile_image_url": PIC},
        "junk",
    ]})
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: api)
    assert ta._fetch_batch(["alice", "bob"]) == {"alice": PIC}
    assert api.urls == [("https://api.twitch.tv/helix/users?login=alice&login=bob", "GET")]


@pytest.mark.parametrize("api", [None, FakeApi(connected=False), FakeApi(error=RuntimeError("401"))])
def test_fetch_batch_returns_none_when_twitch_is_unavailable(monkeypatch, api):
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: api)
    assert ta._fetch_batch(["alice"]) is None


def test_fetch_batch_handles_an_empty_answer(monkeypatch):
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: FakeApi(None))
    assert ta._fetch_batch(["alice"]) == {}


def test_default_cache_path_follows_the_data_dir(monkeypatch):
    monkeypatch.undo()  # drop the fixture's temp path to reach the real one
    from modules.path_utils import get_data_path

    assert ta._cache_path() == get_data_path(str(Path("data") / "twitch_avatars.json"))


def test_lookup_runs_on_a_background_thread(monkeypatch):
    done = threading.Event()
    fake_fetch(monkeypatch, {"alice": PIC})
    ta.request_avatars(["alice"], on_update=done.set)
    assert done.wait(2)

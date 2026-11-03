# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Cover art cache: background download, resize, TTL, failures, clearing."""

import io
import os
import time

import pytest
from PIL import Image

from modules.game_tracker import covers


def _png(w=1200, h=600):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(covers, "cover_dir", lambda: str(tmp_path / "covers"))
    monkeypatch.setattr(covers, "_loaded_from", None)
    monkeypatch.setattr(covers, "_index", {})
    monkeypatch.setattr(covers, "_pending", set())
    calls = []

    def fetch(url):
        calls.append(url)
        if "fail" in url:
            raise OSError("nope")
        return _png()

    monkeypatch.setattr(covers, "_fetcher", fetch)
    covers.set_ttl(7, 6)
    yield calls
    covers.wait_idle()


def test_download_resizes_and_caches(cache):
    assert covers.request_covers([("psn", "NPWR1_00", "https://x/a.png")]) == 1
    assert covers.wait_idle()
    path = covers.cached_cover("psn", "NPWR1_00")
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.width == covers.MAX_WIDTH and im.height == 300
    assert covers.cover_bytes("psn", "NPWR1_00")[:2] == b"\xff\xd8"
    info = covers.cover_info("psn", "NPWR1_00")
    assert info["cached"] and info["expires_at"] > time.time() + 6 * 24 * 3600
    assert covers.cover_count() == 1


def test_fresh_covers_are_not_downloaded_again(cache):
    covers.request_covers([("steam", "10", "https://x/a.png")])
    covers.wait_idle()
    assert covers.request_covers([("steam", "10", "https://x/a.png")]) == 0
    # A new source URL is fetched again.
    assert covers.request_covers([("steam", "10", "https://x/b.png")]) == 1
    covers.wait_idle()
    assert cache == ["https://x/a.png", "https://x/b.png"]


def test_expired_cover_is_refetched(cache, monkeypatch):
    covers.request_covers([("steam", "10", "https://x/a.png")])
    covers.wait_idle()
    key = covers.cover_key("steam", "10")
    url, _t, ok = covers._index[key]
    covers._index[key] = (url, time.time() - 8 * 24 * 3600, ok)
    assert covers.request_covers([("steam", "10", "https://x/a.png")]) == 1


def test_failures_are_remembered_for_the_missing_ttl(cache):
    covers.request_covers([("psn", "N", "https://x/fail.png")])
    covers.wait_idle()
    assert covers.cached_cover("psn", "N") is None
    assert covers.cover_info("psn", "N")["ok"] is False
    assert covers.request_covers([("psn", "N", "https://x/fail.png")]) == 0


def test_non_https_and_blank_urls_are_ignored(cache):
    assert covers.request_covers([("psn", "a", "http://x/a.png"), ("psn", "b", ""), ("psn", "c", None)]) == 0


def test_index_survives_a_restart(cache, monkeypatch):
    covers.request_covers([("psn", "A", "https://x/a.png")])
    covers.wait_idle()
    monkeypatch.setattr(covers, "_loaded_from", None)
    monkeypatch.setattr(covers, "_index", {})
    assert covers.request_covers([("psn", "A", "https://x/a.png")]) == 0


def test_listener_runs_after_download(cache):
    seen = []
    covers.add_listener(seen.append)
    try:
        covers.request_covers([("psn", "L", "https://x/a.png")])
        covers.wait_idle()
    finally:
        covers._listeners.remove(seen.append)
    assert seen == [covers.cover_key("psn", "L")]


def test_clear_removes_files_and_index(cache):
    covers.request_covers([("psn", "A", "https://x/a.png"), ("steam", "1", "https://x/b.png")])
    covers.wait_idle()
    assert covers.clear_covers() == 2
    assert covers.cover_count() == 0 and covers.cover_info("psn", "A")["cached"] is False
    assert not os.path.exists(os.path.join(covers.cover_dir(), covers.INDEX_FILE))


def test_keys_are_filesystem_safe():
    assert covers.cover_key("psn", "../../evil id") == "psn_.._.._evil_id"
    assert os.path.basename(covers.cover_path("psn", "a/b")) == "psn_a_b.jpg"


def test_to_jpeg_keeps_small_images():
    out = covers._to_jpeg(_png(300, 200))
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (300, 200)

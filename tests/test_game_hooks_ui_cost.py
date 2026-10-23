"""RT-02 / RT-06: Game Hooks tab refresh cost and Factorio PID detection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil
import pytest

from modules.game_hooks import factorio_hook as fh
from modules.game_hooks import factorio_mod_installer as inst


def _write_mod(tmp: str, version: str) -> None:
    dest = Path(tmp) / "mods" / f"mycelian-stats_{version}"
    dest.mkdir(parents=True)
    (dest / "info.json").write_text(
        json.dumps({"name": "mycelian-stats", "version": version}), encoding="utf-8"
    )


@pytest.mark.parametrize("installed", [None, "1.0.1", "1.0.4"])
def test_ui_actions_match_separate_lookups(monkeypatch, installed):
    with tempfile.TemporaryDirectory() as tmp:
        if installed:
            _write_mod(tmp, installed)
        scans = {"n": 0}

        def fake_mods_dir_for(data_dir=None):
            scans["n"] += 1
            return Path(tmp) / "mods"

        monkeypatch.setattr(inst, "mods_dir_for", fake_mods_dir_for)
        expected_state = inst.detect_state()
        expected_detail = inst.version_label()
        scans["n"] = 0

        actions = fh.FactorioGameHook.ui_actions()
        assert scans["n"] == 1  # one mods-folder resolve (process scan) per refresh
        assert len(actions) == 1
        action = actions[0]
        assert (action["detail"], action["detail_tone"]) == expected_detail
        assert action["enabled"] == (expected_state != "current")
        assert inst.install_status() == (expected_state, *expected_detail)


@pytest.fixture()
def no_cache(monkeypatch):
    monkeypatch.setattr(fh, "_cached_factorio_proc", None)


def test_running_pid_is_revalidated_without_rescans(monkeypatch, no_cache):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        name = psutil.Process(child.pid).name().lower()
        monkeypatch.setattr(fh.FactorioGameHook, "process_names", (name,))
        scans = {"n": 0}
        alive = {"v": True}

        def fake_find(names):
            scans["n"] += 1
            return child.pid if alive["v"] else None

        monkeypatch.setattr(fh, "find_process_pid", fake_find)
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(()))

        results = [fh.find_factorio_pid() for _ in range(10)]
        assert results == [child.pid] * 10
        assert scans["n"] == 1

        # Exit is seen on the very next call, as with a full scan.
        child.kill()
        child.wait()
        alive["v"] = False
        assert fh.find_factorio_pid() is None
        assert scans["n"] == 2
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_cached_pid_dropped_when_name_no_longer_matches(monkeypatch, no_cache):
    pid = os.getpid()
    name = psutil.Process(pid).name().lower()
    monkeypatch.setattr(fh.FactorioGameHook, "process_names", (name,))
    monkeypatch.setattr(fh, "find_process_pid", lambda names: pid)
    assert fh.find_factorio_pid() == pid
    monkeypatch.setattr(fh.FactorioGameHook, "process_names", ("factorio.exe",))
    monkeypatch.setattr(fh, "find_process_pid", lambda names: None)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(()))
    assert fh.find_factorio_pid() is None


def test_action_tooltip_created_once(monkeypatch):
    from modules.uiwindows.tabs import game_hooks_tab as ght

    created = []

    class FakeTip:
        def __init__(self, text):
            self.text = text
            self.props = {}
            self.sets = 0
            created.append(self)

        def set_text(self, text):
            self.text = text
            self.sets += 1

    class FakeBtn:
        html_id = "c42"

    monkeypatch.setattr(ght.ui, "tooltip", FakeTip)
    tab = ght.GameHooksTab()
    btn = FakeBtn()
    for _ in range(10):
        tab._set_action_tooltip("factorio", "install_mod", btn, "A")
    tab._set_action_tooltip("factorio", "install_mod", btn, "B")
    assert len(created) == 1
    assert created[0].props["target"] == "#c42"
    assert created[0].text == "B"
    assert created[0].sets == 1

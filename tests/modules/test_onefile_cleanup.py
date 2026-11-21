# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""modules/onefile_cleanup.py: only Mycelian's old _MEI folders are removed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import onefile_cleanup as oc  # noqa: E402

NOW = 1_000_000.0


def _mei(tmp, name, ours=True, age=7200.0):
    path = tmp / name
    path.mkdir()
    if ours:
        for marker in oc.MARKERS:
            (path / marker).mkdir(parents=True)
    os.utime(path, (NOW - age, NOW - age))
    return path


def test_only_old_mycelian_unpack_folders_are_removed(tmp_path):
    ours_old = _mei(tmp_path, "_MEI1234")
    ours_new = _mei(tmp_path, "_MEI5678", age=60)  # may be a running copy
    other_app = _mei(tmp_path, "_MEI9999", ours=False)
    not_mei = _mei(tmp_path, "something", age=99999)
    (tmp_path / "_MEIfile").write_text("x")
    assert oc.leftover_unpack_dirs(str(tmp_path), NOW) == [str(ours_old)]
    assert oc.remove_leftovers(str(tmp_path), NOW) == 1
    assert not ours_old.exists() and ours_new.exists() and other_app.exists() and not_mei.exists()
    assert oc.leftover_unpack_dirs(str(tmp_path / "missing"), NOW) == []


def test_runs_only_from_the_windows_folder_build(monkeypatch, tmp_path):
    install = tmp_path / "Mycelian"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "Mycelian.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "_MEIPASS", str(install / "runtime"), raising=False)
    assert oc.runs_from_folder_build() is True
    started = []
    monkeypatch.setattr(oc.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    assert oc.start_background_cleanup() is True and started[0]["name"] == "OneFileCleanup"
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "Temp" / "_MEI1"), raising=False)  # one-file build
    assert oc.runs_from_folder_build() is False and oc.start_background_cleanup() is False
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "_MEIPASS", str(install / "runtime"), raising=False)
    assert oc.runs_from_folder_build() is False

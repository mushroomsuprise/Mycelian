# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.build_info (git commit lookup and build number resolution)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import build_info


def test_project_root_is_repo_root():
    assert build_info._project_root() == ROOT


def test_get_git_commit_runs_git_in_given_root(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(stdout="  abc123\n")

    monkeypatch.setattr(build_info.subprocess, "run", fake_run)
    assert build_info.get_git_commit(tmp_path) == "abc123"
    cmd, kwargs = calls[0]
    assert cmd == ["git", "rev-parse", "HEAD"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["check"] is True and kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["timeout"] == 5


def test_get_git_commit_defaults_to_project_root(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(stdout="deadbeef")

    monkeypatch.setattr(build_info.subprocess, "run", fake_run)
    assert build_info.get_git_commit() == "deadbeef"
    assert seen["cwd"] == ROOT


def test_get_git_commit_empty_output_is_none(monkeypatch):
    monkeypatch.setattr(build_info.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="   \n"))
    assert build_info.get_git_commit() is None


@pytest.mark.parametrize(
    "exc",
    [
        OSError("no git"),
        FileNotFoundError("git"),
        subprocess.CalledProcessError(128, ["git"]),
        subprocess.TimeoutExpired(["git"], 5),
        ValueError("bad"),
    ],
)
def test_get_git_commit_errors_return_none(monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(build_info.subprocess, "run", boom)
    assert build_info.get_git_commit() is None


def test_resolve_build_number_prefers_stamped(monkeypatch):
    monkeypatch.setattr(build_info, "get_git_commit", lambda: pytest.fail("must not query git"))
    assert build_info.resolve_build_number("1a2b3c") == "1a2b3c"


@pytest.mark.parametrize("stamped", ["dev", "", None])
def test_resolve_build_number_uses_git_when_not_stamped(monkeypatch, stamped):
    monkeypatch.setattr(build_info, "get_git_commit", lambda: "livesha")
    assert build_info.resolve_build_number(stamped) == "livesha"


def test_resolve_build_number_default_arg(monkeypatch):
    monkeypatch.setattr(build_info, "get_git_commit", lambda: None)
    assert build_info.resolve_build_number() == "dev"


@pytest.mark.parametrize("stamped, expected", [("dev", "dev"), ("", "dev"), (None, "dev")])
def test_resolve_build_number_falls_back_without_git(monkeypatch, stamped, expected):
    monkeypatch.setattr(build_info, "get_git_commit", lambda: None)
    assert build_info.resolve_build_number(stamped) == expected

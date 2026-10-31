"""Runs the real macOS/Linux apply helper against a throwaway install folder."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from modules import update_sync as us

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash helper only")


def _run_helper(staging: Path, *, copies, deletes=(), links=(), chmods=(), launch: Path, result: Path):
    script = staging / "apply.sh"
    script.write_text(us._unix_helper_script(), encoding="utf-8", newline="\n")
    us._write_apply_plan(staging, list(copies), list(deletes), list(chmods), "linux", launch,
                         links=list(links), result_path=result)
    # PID 0: nothing to wait for.
    plan = staging / "apply_plan.txt"
    plan.write_text(plan.read_text().replace(f"PID\t{os.getpid()}", "PID\t0"), encoding="utf-8")
    subprocess.run(["/bin/bash", str(script)], check=True, timeout=30)


def _wait_for(path: Path, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def layout(tmp_path):
    app = tmp_path / "app"
    staging = tmp_path / "staging"
    (staging / "files").mkdir(parents=True)
    (app / "Frameworks" / "lib").mkdir(parents=True)
    (app / "Frameworks" / "lib" / "x.so").write_text("lib")
    (app / "Resources" / "lib").mkdir(parents=True)
    (app / "Resources" / "lib" / "stale.txt").write_text("stale copy")
    (app / "keep.txt").write_text("old keep")
    (app / "gone.txt").write_text("obsolete")
    marker = tmp_path / "launched"
    launch = app / "Mycelian"
    launch.write_text(f"#!/bin/bash\ntouch '{marker}'\n")
    launch.chmod(0o755)
    return app, staging, marker


def test_helper_applies_copies_links_deletes(layout, tmp_path):
    app, staging, marker = layout
    new_keep = staging / "files" / "keep.txt"
    new_keep.write_text("new keep")
    new_file = staging / "files" / "added" / "new.txt"
    new_file.parent.mkdir()
    new_file.write_text("brand new")
    new_bin = staging / "files" / "tool"
    new_bin.write_text("#!/bin/bash\n")
    result = tmp_path / "data" / "update_result.txt"

    _run_helper(
        staging,
        copies=[(new_keep, app / "keep.txt"), (new_file, app / "added" / "new.txt"), (new_bin, app / "tool")],
        deletes=[app / "gone.txt"],
        links=[("../Frameworks/lib", app / "Resources" / "lib")],
        chmods=[app / "tool"],
        launch=app / "Mycelian",
        result=result,
    )

    assert (app / "keep.txt").read_text() == "new keep"
    assert (app / "added" / "new.txt").read_text() == "brand new"
    assert not (app / "gone.txt").exists()
    link = app / "Resources" / "lib"
    assert link.is_symlink() and os.readlink(link) == "../Frameworks/lib"
    assert (link / "x.so").read_text() == "lib"
    assert os.access(app / "tool", os.X_OK)
    assert not list(app.rglob("*.mycelian-old")) and not list(app.rglob("*.mycelian-new"))
    assert result.read_text().strip() == "ok"
    assert not staging.exists()
    assert _wait_for(marker)


def test_helper_rolls_back_a_failed_apply(layout, tmp_path):
    app, staging, marker = layout
    new_keep = staging / "files" / "keep.txt"
    new_keep.write_text("new keep")
    new_file = staging / "files" / "new.txt"
    new_file.write_text("brand new")
    result = tmp_path / "data" / "update_result.txt"

    _run_helper(
        staging,
        copies=[
            (new_keep, app / "keep.txt"),
            (new_file, app / "new.txt"),
            (staging / "files" / "missing.bin", app / "Mycelian"),  # fails: source is gone
        ],
        deletes=[app / "gone.txt"],
        links=[("../Frameworks/lib", app / "Resources" / "lib")],
        launch=app / "Mycelian",
        result=result,
    )

    assert (app / "keep.txt").read_text() == "old keep"
    assert (app / "gone.txt").read_text() == "obsolete"
    assert not (app / "new.txt").exists()
    assert not (app / "Resources" / "lib").is_symlink()
    assert (app / "Resources" / "lib" / "stale.txt").read_text() == "stale copy"
    assert not list(app.rglob("*.mycelian-old"))
    assert result.read_text().startswith("failed\t")
    assert staging.exists()  # kept for diagnosis
    assert _wait_for(marker)  # the previous version is started again


def test_take_update_result(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert us.take_update_result() is None
    path = us.update_result_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbffailed\tcould not copy\r\n")  # PowerShell writes a BOM
    assert us.take_update_result() == (False, "could not copy")
    assert not path.exists()
    path.write_text("ok\n")
    assert us.take_update_result() == (True, "")

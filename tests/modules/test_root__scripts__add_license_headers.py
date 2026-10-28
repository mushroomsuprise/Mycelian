# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for scripts/add_license_headers.py, run only against files under tmp_path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import add_license_headers as alh  # noqa: E402

FULL_MIT = '"""\nMIT License\n\nCopyright (c) 2023 Mycelian\n\nPermission is hereby granted...\n"""\nimport os\n'


def _file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path



def _run_as_script(script: str, fake_file: Path, argv=None):
    """Execute the real script source as ``__main__`` with ``__file__`` pointing under tmp_path.

    The fake ``__file__`` has a different basename so coverage keeps the real file name.
    """
    real = ROOT / "scripts" / script
    code = compile(real.read_text(encoding="utf-8"), str(real), "exec")
    old_argv = sys.argv
    sys.argv = [str(fake_file), *(argv or [])]
    try:
        exec(code, {"__name__": "__main__", "__file__": str(fake_file), "__builtins__": __builtins__})
    finally:
        sys.argv = old_argv

def test_constants():
    assert alh.COMPACT_HEADER == "# Copyright (c) 2024-2026 Mycelian\n# SPDX-License-Identifier: MIT\n"
    assert alh.COPYRIGHT_IN_DOCSTRING.search("Copyright (c) 2024–2025 Mycelian")
    assert alh.COPYRIGHT_IN_DOCSTRING.search("Copyright (c) 2024 Mycelian")
    assert not alh.COPYRIGHT_IN_DOCSTRING.search("Copyright (c) 1999 Mycelian")


def test_project_root_is_repo_root():
    assert alh.project_root() == ROOT


def test_iter_python_files(tmp_path):
    _file(tmp_path, "main.py", "")
    _file(tmp_path, "build.py", "")
    # pause_alerts.py missing, merge_template_configs.py is a directory -> skipped
    (tmp_path / "merge_template_configs.py").mkdir()
    _file(tmp_path, "other_root.py", "")  # not an entry point
    _file(tmp_path, "modules/a.py", "")
    _file(tmp_path, "modules/sub/b.py", "")
    _file(tmp_path, "scripts/c.py", "")
    _file(tmp_path, "tests/d/e.py", "")
    _file(tmp_path, "modules/readme.md", "")
    found = [p.relative_to(tmp_path).as_posix() for p in alh.iter_python_files(tmp_path)]
    assert found[:2] == ["main.py", "build.py"]
    assert sorted(found[2:]) == ["modules/a.py", "modules/sub/b.py", "scripts/c.py", "tests/d/e.py"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("# Copyright (c) 2024 X\n# SPDX-License-Identifier: MIT\n", True),
        ("# SPDX-License-Identifier: MIT\n", False),
        ("# Copyright (c) 2024 X\n", False),
        ("", False),
    ],
)
def test_has_compact_header(text, expected):
    assert alh.has_compact_header(text) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        (FULL_MIT, True),
        ("\n\n" + FULL_MIT, True),
        ("# comment\n" + FULL_MIT, False),
        ('"""Just a docstring."""\n', False),
        ('"""' + "x" * 900 + "MIT License", False),  # outside the first 800 chars
    ],
)
def test_has_full_mit_block(text, expected):
    assert alh.has_full_mit_block(text) is expected


@pytest.mark.parametrize(
    "text,expected,changed",
    [
        ("Copyright (c) 2023 Mycelian", "Copyright (c) 2024-2026 Mycelian", True),
        ("Copyright (c) 2023-2025 Mycelian", "Copyright (c) 2024-2026 Mycelian", True),
        ("Copyright (c) 2023–2025 Mycelian", "Copyright (c) 2024-2026 Mycelian", True),
        ("Copyright (c) 2024-2026 Mycelian", "Copyright (c) 2024-2026 Mycelian", True),
        ("no copyright", "no copyright", False),
    ],
)
def test_update_full_mit_copyright(text, expected, changed):
    assert alh.update_full_mit_copyright(text) == (expected, changed)


def test_update_full_mit_copyright_only_first_occurrence():
    text = "Copyright (c) 2020 Mycelian\nCopyright (c) 2021 Mycelian\n"
    updated, changed = alh.update_full_mit_copyright(text)
    assert changed
    assert updated == "Copyright (c) 2024-2026 Mycelian\nCopyright (c) 2021 Mycelian\n"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("import os\n", alh.COMPACT_HEADER + "import os\n"),
        ("", alh.COMPACT_HEADER),
        ("#!/usr/bin/env python3\nimport os\n", "#!/usr/bin/env python3\n" + alh.COMPACT_HEADER + "import os\n"),
        (
            "#!/usr/bin/env python3\n\nimport os\n",
            "#!/usr/bin/env python3\n\n" + alh.COMPACT_HEADER + "import os\n",
        ),
        ("#!/usr/bin/env python3\n", "#!/usr/bin/env python3\n" + alh.COMPACT_HEADER),
    ],
)
def test_insert_compact_header(text, expected):
    assert alh.insert_compact_header(text) == expected


def test_process_file_full_mit_updated(tmp_path):
    path = _file(tmp_path, "a.py", FULL_MIT)
    assert alh.process_file(path) == "updated_full_mit"
    assert "Copyright (c) 2024-2026 Mycelian" in path.read_text(encoding="utf-8")


def test_process_file_full_mit_already_current(tmp_path):
    text = FULL_MIT.replace("2023", "2024-2026")
    path = _file(tmp_path, "a.py", text)
    assert alh.process_file(path) is None
    assert path.read_text(encoding="utf-8") == text


def test_process_file_full_mit_without_mycelian_copyright(tmp_path):
    text = '"""\nMIT License\nCopyright (c) 2020 Someone Else\n"""\n'
    path = _file(tmp_path, "a.py", text)
    assert alh.process_file(path) is None
    assert path.read_text(encoding="utf-8") == text


def test_process_file_compact_updated(tmp_path):
    path = _file(tmp_path, "a.py", "# Copyright (c) 2024-2025 Mycelian\n# SPDX-License-Identifier: MIT\nx = 1\n")
    assert alh.process_file(path) == "updated_compact"
    assert path.read_text(encoding="utf-8").startswith(alh.COMPACT_HEADER)


def test_process_file_compact_current_untouched(tmp_path):
    text = alh.COMPACT_HEADER + "x = 1\n"
    path = _file(tmp_path, "a.py", text)
    mtime = path.stat().st_mtime_ns
    assert alh.process_file(path) is None
    assert path.stat().st_mtime_ns == mtime


def test_process_file_compact_other_owner(tmp_path):
    text = "# Copyright (c) 2020 Other\n# SPDX-License-Identifier: MIT\n"
    path = _file(tmp_path, "a.py", text)
    assert alh.process_file(path) is None
    assert path.read_text(encoding="utf-8") == text


def test_process_file_other_copyright_updated(tmp_path):
    path = _file(tmp_path, "a.py", '"""Module.\n\nCopyright (c) 2022 Mycelian\n"""\n')
    assert alh.process_file(path) == "updated_other"
    assert "Copyright (c) 2024-2026 Mycelian" in path.read_text(encoding="utf-8")


def test_process_file_other_copyright_not_matching_pattern(tmp_path):
    text = "# Copyright (c) Mycelian team\n"
    path = _file(tmp_path, "a.py", text)
    assert alh.process_file(path) is None
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.xfail(
    strict=True,
    reason="BUG: process_file reports an already-current 'other' copyright as updated_other and rewrites the file",
)
def test_process_file_other_copyright_already_current_is_not_reported(tmp_path):
    text = '"""Module.\r\n\r\nCopyright (c) 2024-2026 Mycelian\r\n"""\r\n'
    path = _file(tmp_path, "a.py", text)
    assert alh.process_file(path) is None
    assert path.read_bytes() == text.encode("utf-8")


def test_process_file_adds_header(tmp_path):
    path = _file(tmp_path, "a.py", "#!/usr/bin/env python3\r\nimport os\r\n")
    assert alh.process_file(path) == "added_compact"
    # Written with LF newlines
    assert path.read_bytes() == ("#!/usr/bin/env python3\n" + alh.COMPACT_HEADER + "import os\n").encode()


def test_process_file_returns_none_when_insert_changes_nothing(tmp_path, monkeypatch):
    path = _file(tmp_path, "a.py", "x = 1\n")
    monkeypatch.setattr(alh, "insert_compact_header", lambda text: text)
    assert alh.process_file(path) is None
    assert path.read_text(encoding="utf-8") == "x = 1\n"


def test_main_reports_and_skips_itself(tmp_path, monkeypatch, capsys):
    _file(tmp_path, "main.py", FULL_MIT)
    _file(tmp_path, "modules/new.py", "x = 1\n")
    _file(tmp_path, "modules/current.py", alh.COMPACT_HEADER)
    _file(tmp_path, "scripts/add_license_headers.py", "x = 1\n")  # the script itself is skipped
    monkeypatch.setattr(alh, "project_root", lambda: tmp_path)
    assert alh.main() == 0
    out = capsys.readouterr().out
    assert "added_compact (1):" in out and "modules/new.py" in out
    assert "updated_full_mit (1):" in out and "main.py" in out
    assert "Total files changed: 2" in out
    assert (tmp_path / "scripts/add_license_headers.py").read_text() == "x = 1\n"
    assert (tmp_path / "modules/new.py").read_text().startswith(alh.COMPACT_HEADER)


def test_main_nothing_to_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(alh, "project_root", lambda: tmp_path)
    assert alh.main() == 0
    assert capsys.readouterr().out.strip() == "Total files changed: 0"


def test_script_entry_point(tmp_path):
    # Real source run as __main__, with __file__ under tmp_path so project_root() resolves there.
    (tmp_path / "scripts").mkdir()
    target = _file(tmp_path, "modules/x.py", "x = 1\n")
    with pytest.raises(SystemExit) as exc:
        _run_as_script("add_license_headers.py", tmp_path / "scripts" / "script_under_test.py")
    assert exc.value.code == 0
    assert target.read_text().startswith(alh.COMPACT_HEADER)

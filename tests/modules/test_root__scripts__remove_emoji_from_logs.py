# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for scripts/remove_emoji_from_logs.py, run only against files under tmp_path.

main() resolves the project root from the module's ``__file__``; the tests point that at
tmp_path so the real source tree is never scanned or rewritten.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import remove_emoji_from_logs as rel  # noqa: E402

SMILE = "\U0001F600"
ROCKET = "\U0001F680"



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

@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """A fake project whose main() root is tmp_path."""
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(rel, "__file__", str(tmp_path / "scripts" / "remove_emoji_from_logs.py"))
    return tmp_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "line",
    [
        "print('x')",
        "print ('x')",
        "logging.info('x')",
        "logger.warning('x')",
        "logger . error ('x')",
        "self.logger.debug('x')",
        "log.exception('x')",
        "logger.warn('x')",
        "logger.critical('x')",
        "logger.log(10, 'x')",
    ],
)
def test_call_pattern_matches(line):
    assert rel.CALL_PATTERN.search(line)


@pytest.mark.parametrize("line", ["x = 'a'", "reprint('x')", "logger.handlers", "mylog.info('x')"])
def test_call_pattern_ignores(line):
    assert not rel.CALL_PATTERN.search(line)


def test_contains_html_like():
    assert rel.contains_html_like("<b>bold</b>")
    assert not rel.contains_html_like("a < b")


def test_remove_emojis():
    assert rel.remove_emojis(f"Hi {SMILE}{ROCKET} ✅ ❤️ 1⃣ a‍b") == "Hi    1 ab"
    assert rel.remove_emojis("plain") == "plain"


def test_clean_line_not_a_call():
    line = f"x = '{SMILE}'\n"
    assert rel._clean_line(line) == (line, False)


def test_clean_line_double_quoted():
    assert rel._clean_line(f'print("Done {SMILE}")\n') == ('print("Done ")\n', True)


def test_clean_line_prefixed_strings():
    assert rel._clean_line(f'logger.info(f"Value {{x}} {ROCKET}")') == ('logger.info(f"Value {x} ")', True)
    assert rel._clean_line(f"logger.info(rb'raw {SMILE}')") == ("logger.info(rb'raw ')", True)


def test_clean_line_single_quoted():
    assert rel._clean_line(f"print('ok {SMILE}', 'plain')") == ("print('ok ', 'plain')", True)


def test_clean_line_without_emoji_unchanged():
    line = "logger.info('nothing here', \"or here\")"
    assert rel._clean_line(line) == (line, False)


def test_clean_line_skips_html_strings():
    dq = f'print("<b>{SMILE}</b>")'
    sq = f"print('<i>{SMILE}</i>')"
    assert rel._clean_line(dq) == (dq, False)
    assert rel._clean_line(sq) == (sq, False)


def test_clean_line_escaped_quotes():
    line = f'print("say \\"hi\\" {SMILE}")'
    assert rel._clean_line(line) == ('print("say \\"hi\\" ")', True)


def test_clean_line_emoji_outside_string_untouched():
    line = f"print(x)  # {SMILE}"
    assert rel._clean_line(line) == (line, False)


def test_iter_target_files(tmp_path):
    _write(tmp_path / "b.py", "")
    _write(tmp_path / "a.py", "")
    _write(tmp_path / "notes.txt", "")
    _write(tmp_path / "modules" / "z.py", "")
    _write(tmp_path / "modules" / "sub" / "y.py", "")
    _write(tmp_path / "scripts" / "s.py", "")  # not scanned
    found = [p.relative_to(tmp_path).as_posix() for p in rel.iter_target_files(tmp_path)]
    assert found == ["a.py", "b.py", "modules/sub/y.py", "modules/z.py"]


def test_iter_target_files_without_modules(tmp_path):
    _write(tmp_path / "a.py", "")
    assert [p.name for p in rel.iter_target_files(tmp_path)] == ["a.py"]


def test_process_file_rewrites(tmp_path):
    path = _write(tmp_path / "a.py", f"x = '{SMILE}'\nprint('a {SMILE}')\nlogger.info(\"b {ROCKET}\")\n")
    assert rel.process_file(path) == (True, 2)
    assert path.read_text(encoding="utf-8") == f"x = '{SMILE}'\nprint('a ')\nlogger.info(\"b \")\n"


def test_process_file_unchanged_not_written(tmp_path):
    path = _write(tmp_path / "a.py", "print('fine')\n")
    mtime = path.stat().st_mtime_ns
    assert rel.process_file(path) == (False, 0)
    assert path.stat().st_mtime_ns == mtime


def test_process_file_non_utf8_falls_back(tmp_path):
    path = tmp_path / "a.py"
    path.write_bytes(b"x = '\xff'\nprint('ok')\n")
    assert rel.process_file(path) == (False, 0)
    assert path.read_bytes() == b"x = '\xff'\nprint('ok')\n"


def test_verify_file(tmp_path):
    path = _write(
        tmp_path / "a.py",
        f"print('a {SMILE}')\nx = '{SMILE}'\nprint('<b>{SMILE}</b>')\nlogger.info('clean')\n",
    )
    assert rel.verify_file(path) == [(1, f"print('a {SMILE}')")]


def test_verify_file_non_utf8(tmp_path):
    path = tmp_path / "a.py"
    path.write_bytes(b"\xff\nprint('a')\n")
    assert rel.verify_file(path) == []


# --------------------------------------------------------------------------- main


def test_main_rewrites_and_records_modified(fake_root, capsys):
    a = _write(fake_root / "a.py", f"print('hi {SMILE}')\n")
    b = _write(fake_root / "modules" / "b.py", "print('clean')\n")
    assert rel.main([]) == 0
    assert a.read_text(encoding="utf-8") == "print('hi ')\n"
    assert b.read_text(encoding="utf-8") == "print('clean')\n"
    record = fake_root / "logs" / "emoji_cleanup_modified.txt"
    assert record.read_text(encoding="utf-8") == str(a)
    assert "Processed 2 files. Modified 1 files. Line edits: 1" in capsys.readouterr().out


def test_main_survives_unwritable_logs(fake_root, capsys):
    (fake_root / "logs").write_text("a file, not a folder")
    assert rel.main([]) == 0
    assert "Processed 0 files" in capsys.readouterr().out


def test_main_verify_only_clean(fake_root, capsys):
    _write(fake_root / "a.py", "print('ok')\n")
    assert rel.main(["--verify-only"]) == 0
    assert "Outstanding lines: 0" in capsys.readouterr().out


def test_main_verify_only_reports_and_does_not_write(fake_root, capsys):
    path = _write(fake_root / "a.py", f"print('hi {SMILE}')\nlogger.info('{ROCKET}')\n")
    before = path.read_text(encoding="utf-8")
    assert rel.main(["--verify-only"]) == 1
    out = capsys.readouterr().out
    assert f"VERIFY: {path}" in out and "  L1: " in out and "  L2: " in out
    assert "Outstanding lines: 2" in out
    assert path.read_text(encoding="utf-8") == before
    assert not (fake_root / "logs").exists()


@pytest.mark.xfail(strict=True, reason="BUG: --dry-run is parsed but ignored; main() still rewrites the files")
def test_main_dry_run_does_not_write(fake_root):
    path = _write(fake_root / "a.py", f"print('hi {SMILE}')\n")
    rel.main(["--dry-run"])
    assert path.read_text(encoding="utf-8") == f"print('hi {SMILE}')\n"


def test_script_entry_point(tmp_path):
    (tmp_path / "scripts").mkdir()
    _write(tmp_path / "a.py", "print('ok')\n")
    with pytest.raises(SystemExit) as exc:
        _run_as_script("remove_emoji_from_logs.py", tmp_path / "scripts" / "script_under_test.py", ["--verify-only"])
    assert exc.value.code == 0

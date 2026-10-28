# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Every module and script has its own test file, so nothing new slips in untested.

Python: ``modules/a/b.py`` is tested in ``tests/modules/test_a__b.py`` (large modules may
be split into ``test_a__b_part2.py`` or ``test_a__b__routes.py``). Files outside
``modules/`` use ``test_root__<path>.py``. JavaScript: ``x.js`` is tested in
``tests/js/x.test.mjs``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_TESTS = ROOT / "tests" / "modules"
JS_TESTS = ROOT / "tests" / "js"

# Root files tested somewhere other than tests/modules.
ELSEWHERE = {"run_tests.py": ROOT / "tests" / "test_run_tests.py"}
ROOT_SCRIPTS = ["main.py", "build.py", "merge_template_configs.py", "pause_alerts.py", "run_tests.py"]
JS_SOURCES = [
    "assets/default_assets/*.js",
    "assets/default_assets/spore_studio/*.js",
    "templates/_boilerplates/*.js",
]


def _python_sources():
    for path in sorted((ROOT / "modules").rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path, path.relative_to(ROOT / "modules").with_suffix("").as_posix().replace("/", "__")
    for name in ROOT_SCRIPTS:
        yield ROOT / name, "root__" + Path(name).stem
    for path in sorted((ROOT / "scripts").glob("*.py")):
        yield path, "root__scripts__" + path.stem


def _has_test(stem: str) -> bool:
    pattern = re.compile(rf"^test_{re.escape(stem)}(_part\d+[a-z]?|__\w+)?\.py$")
    return any(pattern.match(p.name) for p in MODULE_TESTS.glob("test_*.py"))


def test_every_python_module_and_script_has_a_test_file():
    missing = []
    for path, stem in _python_sources():
        rel = path.relative_to(ROOT).as_posix()
        elsewhere = ELSEWHERE.get(rel)
        if elsewhere is not None:
            if not elsewhere.exists():
                missing.append(f"{rel} -> {elsewhere.relative_to(ROOT)}")
            continue
        if not _has_test(stem):
            missing.append(f"{rel} -> tests/modules/test_{stem}.py")
    assert not missing, "No test file for:\n  " + "\n  ".join(missing)


def test_every_javascript_file_has_a_test_file():
    missing = []
    for pattern in JS_SOURCES:
        for path in sorted(ROOT.glob(pattern)):
            if path.name.endswith(".min.js"):
                continue
            test = JS_TESTS / f"{path.stem}.test.mjs"
            if not test.exists():
                missing.append(f"{path.relative_to(ROOT).as_posix()} -> tests/js/{test.name}")
    assert not missing, "No test file for:\n  " + "\n  ".join(missing)


def test_the_naming_rule_matches_split_files():
    assert re.match(r"^test_web_engine(_part\d+[a-z]?|__\w+)?\.py$", "test_web_engine__routes_part2.py")
    assert re.match(r"^test_a(_part\d+[a-z]?|__\w+)?\.py$", "test_a_part2c.py")
    # A different module that merely shares a prefix doesn't count.
    assert not re.match(r"^test_twitch(_part\d+[a-z]?|__\w+)?\.py$", "test_twitch_polls.py")

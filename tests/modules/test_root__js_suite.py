# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Run the JavaScript unit tests (tests/js/*.test.mjs, node:test) from pytest.

``node --test tests/js/`` loads tests/js/index.js, which imports every *.test.mjs file. A failing
JS test fails this test and shows node's output.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

NODE = shutil.which("node")
JS_DIR = ROOT / "tests" / "js"


def test_js_suite_has_tests():
    files = sorted(JS_DIR.glob("*.test.mjs"))
    assert files, "no JavaScript test files in tests/js"
    assert (JS_DIR / "index.js").is_file()


@pytest.mark.skipif(NODE is None, reason="node is not installed; the JavaScript tests need it")
def test_js_suite_passes():
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    env["NO_COLOR"] = "1"
    proc = subprocess.run(
        [NODE, "--test", "--test-reporter=spec", "tests/js/"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        check=False,
    )
    output = proc.stdout + proc.stderr
    tail = output[-20000:]
    assert proc.returncode == 0, f"node --test tests/js/ failed (exit {proc.returncode}):\n{tail}"
    passed = re.search(r"^\S*\s*pass (\d+)", output, re.M)
    failed = re.search(r"^\S*\s*fail (\d+)", output, re.M)
    assert passed and int(passed.group(1)) > 0, tail
    assert failed and int(failed.group(1)) == 0, tail

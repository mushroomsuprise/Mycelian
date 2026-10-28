#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Run every Mycelian test and write a detailed log for triage.

    python run_tests.py              # whole suite (Python tests, plus the JS tests through pytest)
    python run_tests.py -k twitch    # extra arguments go straight to pytest

The console shows progress and, at the end, the tests that failed. Everything else goes
to ``logs/tests/test-run-<date>-<time>.log`` (``latest.log`` is a copy of the newest):
full tracebacks with the log output captured during each failing test, real-data guard
findings, known bugs (``xfail`` tests whose reason starts with ``BUG:``), skipped tests
with their reasons, the slowest tests and the complete pytest output.

``build.py`` calls :func:`run_all_tests` before compiling.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"
LOG_DIR = ROOT / "logs" / "tests"
KEEP_LOGS = 30
PER_TEST_TIMEOUT_SEC = 300
RUN_TIMEOUT_SEC = 60 * 60

_PROGRESS_RE = re.compile(r"^[.FEsxXp]+\s*(\[\s*\d+%\])?$")
_SUMMARY_RE = re.compile(r"^=+ .*\b(passed|failed|error|errors|skipped|deselected|no tests ran)\b.* =+$")
_GUARD_RE = re.compile(r"^\s+(changed|deleted|created): (.+)$")


@dataclass
class TestOutcome:
    nodeid: str
    kind: str  # failed | error | skipped | xfailed | xpassed
    message: str = ""
    details: str = ""
    duration: float = 0.0

    @property
    def is_known_bug(self) -> bool:
        return self.kind == "xfailed" and self.message.lstrip().startswith("BUG:")


@dataclass
class TestRunResult:
    exit_code: int
    duration: float
    log_path: Optional[Path] = None
    total: int = 0
    passed: int = 0
    outcomes: List[TestOutcome] = field(default_factory=list)
    guard_changes: List[str] = field(default_factory=list)
    slowest: List[str] = field(default_factory=list)
    setup_problem: str = ""  # pytest couldn't run at all (missing, crashed, timed out)
    command: List[str] = field(default_factory=list)

    def of_kind(self, kind: str) -> List[TestOutcome]:
        return [o for o in self.outcomes if o.kind == kind]

    @property
    def failed(self) -> List[TestOutcome]:
        return self.of_kind("failed")

    @property
    def errors(self) -> List[TestOutcome]:
        return self.of_kind("error")

    @property
    def problems(self) -> List[TestOutcome]:
        """Everything that should stop a build: failures, errors, unexpected passes."""
        return self.failed + self.errors + self.of_kind("xpassed")

    @property
    def known_bugs(self) -> List[TestOutcome]:
        return [o for o in self.outcomes if o.is_known_bug]

    @property
    def ok(self) -> bool:
        return (
            not self.setup_problem
            and not self.problems
            and not self.guard_changes
            and self.exit_code in (0, 5)
            and self.total > 0
        )

    def summary_line(self) -> str:
        if self.setup_problem:
            return f"Tests could not run: {self.setup_problem}"
        parts = [f"{self.passed} passed"]
        for label, items in (
            ("failed", self.failed),
            ("errors", self.errors),
            ("unexpectedly passed", self.of_kind("xpassed")),
            ("known bugs", self.known_bugs),
            ("skipped", self.of_kind("skipped")),
        ):
            if items:
                parts.append(f"{len(items)} {label}")
        if self.guard_changes:
            parts.append(f"{len(self.guard_changes)} real data file(s) changed")
        return f"{', '.join(parts)} in {self.duration:.1f}s"


# --------------------------------------------------------------------------- command


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def pytest_command(junit_path: Path, extra: Sequence[str] = ()) -> Optional[List[str]]:
    """The pytest command line, or None when pytest isn't available anywhere."""
    if _has_module("pytest"):
        base = [sys.executable, "-m", "pytest"]
        timeout = _has_module("pytest_timeout")
        parallel = _has_module("xdist")
    else:
        uv = shutil.which("uv")
        if uv is None:
            return None
        base = [uv, "run", "--with", "pytest", "--with", "pytest-timeout", "--with", "pytest-xdist",
                "python", "-m", "pytest"]
        timeout = parallel = True
    args = [
        str(TESTS_DIR),
        "-p", "no:cacheprovider",
        "-q",
        "--color=no",
        "-rfEX",
        "--tb=long",
        "--log-level=DEBUG",
        "--durations=25",
        "--junitxml", str(junit_path),
        "-o", "junit_family=xunit2",
        "-o", "junit_logging=all",
        "-o", "junit_log_passing_tests=false",
    ]
    if timeout:
        args += ["--timeout", str(PER_TEST_TIMEOUT_SEC)]
    if parallel and not any(a == "-n" or a.startswith("-n") or a.startswith("--numprocesses") for a in extra):
        # One worker per core; each worker has its own temp data folder and test database.
        args += ["-n", "auto"]
    return base + args + list(extra)


# --------------------------------------------------------------------------- results


def parse_junit(path: Path) -> tuple[int, List[TestOutcome]]:
    """(number of tests, every test that didn't simply pass) from a pytest junit XML file."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return 0, []
    total = 0
    outcomes: List[TestOutcome] = []
    for case in tree.iter("testcase"):
        total += 1
        classname = case.get("classname", "")
        name = case.get("name", "")
        nodeid = _nodeid(classname, name)
        duration = float(case.get("time") or 0)
        for child in case:
            tag = child.tag
            message = child.get("message", "") or ""
            text = (child.text or "").strip()
            if tag == "failure":
                kind = "xpassed" if "XPASS" in message or message.startswith("[XPASS") else "failed"
            elif tag == "error":
                kind = "error"
            elif tag == "skipped":
                kind = "xfailed" if child.get("type") == "pytest.xfail" else "skipped"
            else:
                continue
            if kind == "xfailed" and message.startswith("xfail-marked test passes unexpectedly"):
                kind = "xpassed"
            if kind == "skipped" and message.startswith("Skipped: "):
                message = message[len("Skipped: "):]
            extra = [
                (grand.text or "").strip()
                for grand in case
                if grand.tag in ("system-out", "system-err") and (grand.text or "").strip()
            ]
            details = "\n\n".join([text] + [f"--- captured output ---\n{e}" for e in extra]) if extra else text
            outcomes.append(TestOutcome(nodeid, kind, message, details, duration))
            break
    return total, outcomes


def _nodeid(classname: str, name: str) -> str:
    """Rebuild something close to the pytest node id from junit's classname."""
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ROOT.joinpath(*parts[:i]).with_suffix(".py")
        if candidate.is_file():
            rel = candidate.relative_to(ROOT).as_posix()
            rest = parts[i:]
            return "::".join([rel] + rest + [name])
    return f"{classname}::{name}" if classname else name


def parse_output(lines: Iterable[str]) -> tuple[List[str], List[str]]:
    """(real data guard findings, slowest-test lines) from the raw pytest output."""
    guard: List[str] = []
    slowest: List[str] = []
    in_guard = in_durations = False
    for line in lines:
        if "real data guard" in line:
            in_guard, in_durations = True, False
            continue
        if "slowest" in line and "durations" in line:
            in_durations, in_guard = True, False
            continue
        if line.startswith("=") or line.startswith("!"):
            in_guard = in_durations = False
            continue
        if in_guard:
            m = _GUARD_RE.match(line)
            if m:
                guard.append(f"{m.group(1)}: {m.group(2)}")
        elif in_durations and line.strip() and not line.startswith("("):
            slowest.append(line.rstrip())
    return guard, slowest


# --------------------------------------------------------------------------- log


def _git_state() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        return f"{head or 'unknown'}{' (uncommitted changes)' if dirty else ''}"
    except Exception:
        return "unknown"


def _node_version() -> str:
    node = shutil.which("node")
    if not node:
        return "not installed (JS tests skipped)"
    try:
        return subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def _section(title: str) -> str:
    return f"\n{'=' * 100}\n{title}\n{'=' * 100}\n"


def format_log(result: TestRunResult, raw_output: str, started: datetime) -> str:
    out: List[str] = []
    out.append("Mycelian test run")
    out.append(f"Started:   {started:%Y-%m-%d %H:%M:%S}")
    out.append(f"Duration:  {result.duration:.1f}s")
    out.append(f"Result:    {'PASSED' if result.ok else 'FAILED'} - {result.summary_line()}")
    out.append(f"Exit code: {result.exit_code}")
    out.append(f"Commit:    {_git_state()}")
    out.append(f"Python:    {sys.version.split()[0]} ({sys.executable})")
    out.append(f"Platform:  {platform.platform()}")
    out.append(f"Node:      {_node_version()}")
    out.append(f"Command:   {' '.join(result.command) if result.command else '(none)'}")
    out.append(f"Tests:     {result.total}")

    if result.setup_problem:
        out.append(_section("TESTS COULD NOT RUN"))
        out.append(result.setup_problem)

    def listing(title: str, items: List[TestOutcome], with_details: bool) -> None:
        if not items:
            return
        out.append(_section(f"{title} ({len(items)})"))
        for i, o in enumerate(items, 1):
            out.append(f"[{i}] {o.nodeid}  ({o.duration:.2f}s)")
            if o.message:
                out.append(f"    {o.message.strip().splitlines()[0][:500]}")
            if with_details and o.details:
                out.append("")
                out.append(o.details)
                out.append("-" * 100)

    listing("FAILED TESTS", result.failed, True)
    listing("ERRORS (setup, teardown or collection)", result.errors, True)
    listing("UNEXPECTED PASSES (an xfail test passed; remove its xfail mark)", result.of_kind("xpassed"), True)
    if result.guard_changes:
        out.append(_section(f"REAL DATA CHANGED DURING THE RUN ({len(result.guard_changes)})"))
        out.append("A test wrote to the real data files instead of a temp copy. If Mycelian itself was")
        out.append("running during the tests, it may have made these changes instead.")
        out.extend(f"  {c}" for c in result.guard_changes)
    listing("KNOWN BUGS (xfail tests marked BUG:)", result.known_bugs, False)
    other_xfail = [o for o in result.of_kind("xfailed") if not o.is_known_bug]
    listing("EXPECTED FAILURES (xfail, not marked as bugs)", other_xfail, False)
    listing("SKIPPED", result.of_kind("skipped"), False)
    if result.slowest:
        out.append(_section("SLOWEST TESTS"))
        out.extend(result.slowest)
    out.append(_section("FULL PYTEST OUTPUT"))
    out.append(raw_output)
    return "\n".join(out) + "\n"


def _write_log(text: str, started: datetime, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"test-run-{started:%Y%m%d-%H%M%S}.log"
    path.write_text(text, encoding="utf-8")
    try:
        shutil.copyfile(path, log_dir / "latest.log")
    except OSError:
        pass
    old = sorted(log_dir.glob("test-run-*.log"))
    for stale in old[:-KEEP_LOGS]:
        try:
            stale.unlink()
        except OSError:
            pass
    return path


# --------------------------------------------------------------------------- run


def run_all_tests(
    extra_args: Sequence[str] = (),
    *,
    echo: Callable[[str], None] = print,
    log_dir: Path = LOG_DIR,
    run_timeout: float = RUN_TIMEOUT_SEC,
) -> TestRunResult:
    """Run the whole suite, write the log, and return what happened."""
    started = datetime.now()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mycelian-tests-") as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = pytest_command(junit, extra_args)
        if cmd is None:
            result = TestRunResult(
                exit_code=-1, duration=0.0,
                setup_problem="pytest is not installed and uv was not found. Install the dev tools with: uv sync",
            )
            result.log_path = _write_log(format_log(result, "", started), started, log_dir)
            return result

        echo(f"Running the test suite ({TESTS_DIR.relative_to(ROOT)}) ...")
        raw: List[str] = []
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", env=env)
        except OSError as e:
            result = TestRunResult(exit_code=-1, duration=0.0, command=cmd,
                                   setup_problem=f"could not start pytest: {e}")
            result.log_path = _write_log(format_log(result, "", started), started, log_dir)
            return result

        timed_out = threading.Event()

        def _kill_after_timeout() -> None:
            if proc.poll() is None:
                timed_out.set()
                proc.kill()

        timer = threading.Timer(run_timeout, _kill_after_timeout)
        timer.daemon = True
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                raw.append(line)
                stripped = line.strip()
                if _PROGRESS_RE.match(stripped) or _SUMMARY_RE.match(stripped):
                    echo(line)
            exit_code = proc.wait()
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            raise
        finally:
            timer.cancel()

        total, outcomes = parse_junit(junit)
        guard, slowest = parse_output(raw)
        result = TestRunResult(
            exit_code=exit_code,
            duration=time.monotonic() - t0,
            total=total,
            outcomes=outcomes,
            guard_changes=guard,
            slowest=slowest,
            command=cmd,
        )
        result.passed = max(0, total - len(outcomes))
        if timed_out.is_set():
            result.setup_problem = f"the test run took longer than {run_timeout / 60:.0f} minutes and was stopped"
        elif total == 0 and exit_code not in (0, 5):
            result.setup_problem = f"pytest exited with code {exit_code} before running any tests (see the log)"
        result.log_path = _write_log(format_log(result, "\n".join(raw), started), started, log_dir)
        return result


def print_report(result: TestRunResult, echo: Callable[[str], None] = print, limit: int = 60) -> None:
    """The short console report: verdict, each failed test, and where the log is."""
    echo("")
    echo(("TESTS PASSED: " if result.ok else "TESTS FAILED: ") + result.summary_line())
    problems = result.problems
    if problems:
        echo("")
        echo("Failed tests:")
        for o in problems[:limit]:
            label = {"failed": "FAILED", "error": "ERROR", "xpassed": "XPASS"}[o.kind]
            first = o.message.strip().splitlines()[0][:160] if o.message.strip() else ""
            echo(f"  {label:6} {o.nodeid}" + (f"\n         {first}" if first else ""))
        if len(problems) > limit:
            echo(f"  ... and {len(problems) - limit} more (see the log)")
    if result.guard_changes:
        echo("")
        echo("Real data changed during the tests:")
        for c in result.guard_changes:
            echo(f"  {c}")
    if result.known_bugs:
        echo(f"\n{len(result.known_bugs)} known bug(s) are tracked as xfail tests (listed in the log).")
    if result.log_path:
        echo(f"\nFull details: {result.log_path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    result = run_all_tests(args)
    print_report(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())

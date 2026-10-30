# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The test runner (run_tests.py) and the test step at the start of build.py."""

from __future__ import annotations

import io
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build  # noqa: E402
import run_tests as rt  # noqa: E402

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="7">
  <testcase classname="tests.test_footer_badge_styles" name="test_ok" time="0.01"/>
  <testcase classname="tests.test_footer_badge_styles" name="test_bad" time="0.20">
    <failure message="AssertionError: assert 1 == 2">long traceback here</failure>
    <system-out>printed while failing</system-out>
    <system-err>DEBUG detail</system-err>
  </testcase>
  <testcase classname="tests.test_footer_badge_styles" name="test_setup" time="0">
    <error message="failed on setup with &quot;RuntimeError: boom&quot;">setup trace</error>
  </testcase>
  <testcase classname="tests.test_footer_badge_styles" name="test_bug" time="0">
    <skipped type="pytest.xfail" message="BUG: totals double count"/>
  </testcase>
  <testcase classname="tests.test_footer_badge_styles" name="test_expected" time="0">
    <skipped type="pytest.xfail" message="flaky on CI"/>
  </testcase>
  <testcase classname="tests.test_footer_badge_styles" name="test_xpass" time="0">
    <failure message="[XPASS(strict)] should fail">xpass</failure>
  </testcase>
  <testcase classname="tests.test_footer_badge_styles" name="test_skip" time="0">
    <skipped type="pytest.skip" message="Skipped: node is not installed">skip</skipped>
  </testcase>
</testsuite></testsuites>
"""


@pytest.fixture
def junit(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- parsing


def test_parse_junit_sorts_every_outcome(junit):
    total, outcomes = rt.parse_junit(junit)
    assert total == 7
    kinds = {o.nodeid.rsplit("::", 1)[1]: o.kind for o in outcomes}
    assert kinds == {"test_bad": "failed", "test_setup": "error", "test_bug": "xfailed",
                     "test_expected": "xfailed", "test_xpass": "xpassed", "test_skip": "skipped"}
    bad = next(o for o in outcomes if o.kind == "failed")
    assert bad.nodeid == "tests/test_footer_badge_styles.py::test_bad"
    assert bad.message == "AssertionError: assert 1 == 2"
    assert "long traceback here" in bad.details and "DEBUG detail" in bad.details
    assert bad.duration == pytest.approx(0.2)
    skip = next(o for o in outcomes if o.kind == "skipped")
    assert skip.message == "node is not installed"


def test_known_bugs_are_xfails_marked_bug(junit):
    _total, outcomes = rt.parse_junit(junit)
    result = rt.TestRunResult(exit_code=1, duration=1.0, total=7, passed=1, outcomes=outcomes)
    assert [o.message for o in result.known_bugs] == ["BUG: totals double count"]
    assert {o.kind for o in result.problems} == {"failed", "error", "xpassed"}
    assert not result.ok


def test_parse_junit_tolerates_a_missing_or_broken_file(tmp_path):
    assert rt.parse_junit(tmp_path / "nope.xml") == (0, [])
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testcase", encoding="utf-8")
    assert rt.parse_junit(broken) == (0, [])


def test_nodeid_includes_class_and_nested_paths():
    assert rt._nodeid("tests.test_run_tests.SomeTests", "test_x") == "tests/test_run_tests.py::SomeTests::test_x"
    assert rt._nodeid("not.a.file", "t") == "not.a.file::t"
    assert rt._nodeid("", "t") == "t"


def test_parse_output_finds_guard_findings_and_slowest_tests():
    lines = [
        "....",
        "!!!!!!!!!!!! real data guard !!!!!!!!!!!!",
        "REAL DATA CHANGED DURING THE TEST RUN (a test is not isolated):",
        "  changed: data/statistics.db",
        "  created: data/x.json",
        "  If Mycelian itself was running during the tests, it may have made these changes.",
        "============================= slowest 25 durations =============================",
        "5.02s call     tests/test_a.py::test_slow",
        "",
        "(3 durations < 0.005s hidden.  Use -vv to show these durations.)",
        "=========== 2 passed ===========",
    ]
    guard, slowest = rt.parse_output(lines)
    assert guard == ["changed: data/statistics.db", "created: data/x.json"]
    assert slowest == ["5.02s call     tests/test_a.py::test_slow"]


# --------------------------------------------------------------------------- result


def test_ok_needs_tests_and_a_clean_run():
    assert rt.TestRunResult(exit_code=0, duration=1, total=3, passed=3).ok
    assert not rt.TestRunResult(exit_code=0, duration=1, total=0).ok
    assert not rt.TestRunResult(exit_code=0, duration=1, total=3, guard_changes=["changed: x"]).ok
    assert not rt.TestRunResult(exit_code=0, duration=1, total=3, setup_problem="no pytest").ok
    assert not rt.TestRunResult(exit_code=2, duration=1, total=3).ok
    # Known bugs (xfail) don't block.
    bug = rt.TestOutcome("t", "xfailed", "BUG: x")
    assert rt.TestRunResult(exit_code=0, duration=1, total=3, passed=2, outcomes=[bug]).ok


def test_summary_line_counts_each_kind(junit):
    _total, outcomes = rt.parse_junit(junit)
    result = rt.TestRunResult(exit_code=1, duration=2.5, total=7, passed=1, outcomes=outcomes,
                              guard_changes=["changed: a"])
    line = result.summary_line()
    for part in ("1 passed", "1 failed", "1 errors", "1 unexpectedly passed", "1 known bugs", "1 skipped",
                 "1 real data file(s) changed", "2.5s"):
        assert part in line
    assert rt.TestRunResult(exit_code=-1, duration=0, setup_problem="no pytest").summary_line() == (
        "Tests could not run: no pytest")


# --------------------------------------------------------------------------- log


def test_log_has_every_section_in_triage_order(junit):
    _total, outcomes = rt.parse_junit(junit)
    result = rt.TestRunResult(exit_code=1, duration=2.0, total=7, passed=1, outcomes=outcomes,
                              guard_changes=["changed: data/statistics.db"], slowest=["5.0s call t"],
                              command=["python", "-m", "pytest"])
    from datetime import datetime
    text = rt.format_log(result, "RAW OUTPUT", datetime(2026, 1, 2, 3, 4, 5))
    order = ["Result:    FAILED", "FAILED TESTS (1)", "ERRORS", "UNEXPECTED PASSES", "REAL DATA CHANGED",
             "KNOWN BUGS", "EXPECTED FAILURES", "SKIPPED (1)", "SLOWEST TESTS", "FULL PYTEST OUTPUT", "RAW OUTPUT"]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "long traceback here" in text and "Command:   python -m pytest" in text
    assert "Started:   2026-01-02 03:04:05" in text


def test_log_reports_a_run_that_could_not_start():
    from datetime import datetime
    result = rt.TestRunResult(exit_code=-1, duration=0, setup_problem="pytest is not installed")
    text = rt.format_log(result, "", datetime.now())
    assert "TESTS COULD NOT RUN" in text and "pytest is not installed" in text


def test_logs_rotate_and_latest_is_a_copy(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    monkeypatch.setattr(rt, "KEEP_LOGS", 3)
    start = datetime(2026, 1, 1, 12, 0, 0)
    paths = [rt._write_log(f"run {i}", start + timedelta(seconds=i), tmp_path) for i in range(5)]
    kept = sorted(p.name for p in tmp_path.glob("test-run-*.log"))
    assert kept == [p.name for p in paths[-3:]]
    assert (tmp_path / "latest.log").read_text() == "run 4"


# --------------------------------------------------------------------------- command


def test_command_uses_this_python_when_pytest_is_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_has_module", lambda name: True)
    cmd = rt.pytest_command(tmp_path / "j.xml", ["-k", "x"])
    assert cmd[:3] == [sys.executable, "-m", "pytest"]
    assert "--junitxml" in cmd and str(tmp_path / "j.xml") in cmd
    assert "--timeout" in cmd and "-p" in cmd and "no:cacheprovider" in cmd
    assert cmd[-2:] == ["-k", "x"]


def test_command_skips_the_timeout_flag_without_the_plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_has_module", lambda name: name == "pytest")
    cmd = rt.pytest_command(tmp_path / "j.xml")
    assert "--timeout" not in cmd and "-n" not in cmd


def test_command_runs_in_parallel_unless_told_otherwise(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_has_module", lambda name: True)
    cmd = rt.pytest_command(tmp_path / "j.xml")
    assert cmd[cmd.index("-n") + 1] == "auto"
    cmd = rt.pytest_command(tmp_path / "j.xml", ["-n", "2"])
    assert cmd.count("-n") == 1 and cmd[-2:] == ["-n", "2"]
    assert "-n" not in rt.pytest_command(tmp_path / "j.xml", ["-n0"])[:-1]


def test_command_falls_back_to_uv_then_gives_up(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_has_module", lambda name: False)
    monkeypatch.setattr(rt.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    cmd = rt.pytest_command(tmp_path / "j.xml")
    assert cmd[:4] == ["/bin/uv", "run", "--with", "pytest"] and "--timeout" in cmd
    assert "pytest-xdist" in cmd and "-n" in cmd
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    assert rt.pytest_command(tmp_path / "j.xml") is None


# --------------------------------------------------------------------------- run


class FakeProc:
    def __init__(self, lines, code, junit_text=None, junit_path=None):
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.code = code
        self.killed = False
        if junit_text is not None:
            Path(junit_path).write_text(junit_text, encoding="utf-8")

    def poll(self):
        return None if not self.killed else self.code

    def wait(self):
        return self.code

    def kill(self):
        self.killed = True


def _fake_popen(monkeypatch, lines, code, junit_text=None, captured=None):
    def popen(cmd, **kwargs):
        junit_path = cmd[cmd.index("--junitxml") + 1]
        if captured is not None:
            captured.update(cmd=cmd, kwargs=kwargs)
        return FakeProc(lines, code, junit_text, junit_path)

    monkeypatch.setattr(rt, "_has_module", lambda name: True)
    monkeypatch.setattr(rt.subprocess, "Popen", popen)


def test_run_echoes_progress_only_and_writes_the_log(monkeypatch, tmp_path, junit):
    lines = ["....F.  [ 50%]", "E   AssertionError: detail", "....   [100%]", "=== 1 failed, 6 passed in 1s ==="]
    captured = {}
    _fake_popen(monkeypatch, lines, 1, junit.read_text(), captured)
    echoed = []
    result = rt.run_all_tests(echo=echoed.append, log_dir=tmp_path / "logs")
    assert echoed[0].startswith("Running the test suite")
    assert echoed[1:] == ["....F.  [ 50%]", "....   [100%]", "=== 1 failed, 6 passed in 1s ==="]
    assert result.total == 7 and result.passed == 1 and len(result.failed) == 1
    assert result.log_path.exists() and "E   AssertionError: detail" in result.log_path.read_text()
    assert captured["kwargs"]["cwd"] == rt.ROOT and captured["kwargs"]["env"]["PYTHONUNBUFFERED"] == "1"


def test_run_reports_missing_pytest(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "pytest_command", lambda *a, **k: None)
    result = rt.run_all_tests(echo=lambda s: None, log_dir=tmp_path)
    assert "pytest is not installed" in result.setup_problem and not result.ok
    assert result.log_path.exists()


def test_run_reports_pytest_that_cannot_start(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_has_module", lambda name: True)

    def popen(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr(rt.subprocess, "Popen", popen)
    result = rt.run_all_tests(echo=lambda s: None, log_dir=tmp_path)
    assert result.setup_problem == "could not start pytest: no such file"


def test_run_reports_a_crash_before_any_test(monkeypatch, tmp_path):
    _fake_popen(monkeypatch, ["ImportError while loading conftest"], 4)
    result = rt.run_all_tests(echo=lambda s: None, log_dir=tmp_path)
    assert "exited with code 4" in result.setup_problem


def test_run_stops_a_run_that_takes_too_long(monkeypatch, tmp_path):
    _fake_popen(monkeypatch, [], 1)

    class InstantTimer:
        def __init__(self, interval, fn):
            self.fn = fn
            self.daemon = False

        def start(self):
            self.fn()

        def cancel(self):
            pass

    monkeypatch.setattr(rt.threading, "Timer", InstantTimer)
    result = rt.run_all_tests(echo=lambda s: None, log_dir=tmp_path, run_timeout=120)
    assert result.setup_problem == "the test run took longer than 2 minutes and was stopped"


def test_print_report_lists_failures_and_the_log(junit):
    _total, outcomes = rt.parse_junit(junit)
    result = rt.TestRunResult(exit_code=1, duration=1, total=7, passed=1, outcomes=outcomes,
                              guard_changes=["changed: a"], log_path=PurePosixPath("/tmp/x.log"))
    lines = []
    rt.print_report(result, echo=lines.append, limit=2)
    text = "\n".join(lines)
    assert "TESTS FAILED" in text
    assert "FAILED tests/test_footer_badge_styles.py::test_bad" in text
    assert "ERROR  tests/test_footer_badge_styles.py::test_setup" in text
    assert "... and 1 more" in text
    assert "changed: a" in text and "1 known bug(s)" in text and "/tmp/x.log" in text


def test_main_exit_codes(monkeypatch, capsys):
    good = rt.TestRunResult(exit_code=0, duration=1, total=1, passed=1)
    monkeypatch.setattr(rt, "run_all_tests", lambda args: good)
    assert rt.main([]) == 0
    monkeypatch.setattr(rt, "run_all_tests", lambda args: rt.TestRunResult(exit_code=1, duration=1, total=1,
                                                                             outcomes=[rt.TestOutcome("t", "failed")]))
    assert rt.main(["-k", "x"]) == 1
    assert rt.main(["--help"]) == 0 and "run_tests.py" in capsys.readouterr().out


# --------------------------------------------------------------------------- build.py gate


def _args(**kw):
    return SimpleNamespace(skip_tests=kw.get("skip", False), ignore_test_failures=kw.get("ignore", False))


@pytest.fixture
def gate(monkeypatch):
    calls = {"runs": 0}
    state = {"result": rt.TestRunResult(exit_code=0, duration=1, total=1, passed=1)}

    def fake_run(**kwargs):
        calls["runs"] += 1
        return state["result"]

    monkeypatch.setattr(rt, "run_all_tests", fake_run)
    monkeypatch.setattr(rt, "print_report", lambda result: None)
    return calls, state


def _failing():
    return rt.TestRunResult(exit_code=1, duration=1, total=2, passed=1, outcomes=[rt.TestOutcome("t", "failed")])


def test_build_args_have_the_test_flags():
    args = build._parse_args(["--skip-tests", "--ignore-test-failures"])
    assert args.skip_tests and args.ignore_test_failures
    args = build._parse_args([])
    assert not args.skip_tests and not args.ignore_test_failures


def test_gate_can_be_skipped(gate):
    calls, _state = gate
    build.run_test_gate(_args(skip=True), ROOT)
    assert calls["runs"] == 0


def test_gate_passes_quietly(gate):
    calls, _state = gate
    build.run_test_gate(_args(), ROOT, ask=lambda q: pytest.fail("must not ask"))
    assert calls["runs"] == 1


def test_gate_continues_with_the_ignore_flag(gate):
    _calls, state = gate
    state["result"] = _failing()
    build.run_test_gate(_args(ignore=True), ROOT, ask=lambda q: pytest.fail("must not ask"))


def test_gate_stops_a_non_interactive_build(gate, monkeypatch):
    _calls, state = gate
    state["result"] = _failing()
    monkeypatch.setattr(build.sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exc:
        build.run_test_gate(_args(), ROOT, ask=lambda q: pytest.fail("must not ask"))
    assert exc.value.code == 1


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("answer, keeps_going", [("y", True), ("YES", True), ("n", False), ("", False)])
def test_gate_asks_whether_to_build_anyway(gate, monkeypatch, answer, keeps_going):
    _calls, state = gate
    state["result"] = _failing()
    monkeypatch.setattr(build.sys, "stdin", _Tty())
    questions = []

    def ask(q):
        questions.append(q)
        return answer

    if keeps_going:
        build.run_test_gate(_args(), ROOT, ask=ask)
    else:
        with pytest.raises(SystemExit):
            build.run_test_gate(_args(), ROOT, ask=ask)
    assert "Continue building anyway?" in questions[0]


def test_gate_treats_end_of_input_as_no(gate, monkeypatch):
    _calls, state = gate
    state["result"] = _failing()
    monkeypatch.setattr(build.sys, "stdin", _Tty())

    def ask(q):
        raise EOFError

    with pytest.raises(SystemExit):
        build.run_test_gate(_args(), ROOT, ask=ask)


def test_build_main_runs_the_gate_first(monkeypatch):
    order = []
    monkeypatch.setattr(build, "_parse_args", lambda: SimpleNamespace(
        skip_installer=True, skip_tests=False, ignore_test_failures=False))
    monkeypatch.setattr(build, "run_test_gate", lambda args, root: order.append("tests"))
    monkeypatch.setattr(build, "CURRENT_OS", "linux")

    def stop():
        order.append("dependencies")
        raise KeyboardInterrupt

    monkeypatch.setattr(build, "ensure_dependencies", stop)
    with pytest.raises(SystemExit):
        build.main()
    assert order == ["tests", "dependencies"]

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for main.py, the app entry point.

The app is never started. main.py's real source is compiled under its own filename (so
coverage maps to it) with ``__name__ == "__main__"`` rewritten to a private name, and run in
a fresh namespace whose ``__file__`` points at tmp_path. Every collaborator the entry point
touches (SSL probe, profiler, logging, signals, atexit, database, UI shell, services, tray,
shutdown, updater, notifications, dialogs) is a fake installed in ``sys.modules``. Real
``sys.modules["__main__"]`` is never touched.
"""

from __future__ import annotations

import asyncio
import atexit
import builtins
import io
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules  # noqa: E402
from modules import app_startup  # noqa: E402

MAIN_PATH = ROOT / "main.py"
RUN_AS_MAIN = "__mycelian_main_under_test__"
RUN_AS_IMPORT = "mycelian_main_imported_under_test"
_SOURCE = MAIN_PATH.read_text(encoding="utf-8")
assert _SOURCE.count('__name__ == "__main__"') == 2, "main.py's __main__ guards changed; update this harness"
_CODE = compile(_SOURCE.replace('__name__ == "__main__"', f'__name__ == "{RUN_AS_MAIN}"'), str(MAIN_PATH), "exec")

_TUNED_LOGGERS = (
    "twitchAPI", "websockets", "urllib3", "google.auth", "werkzeug", "asyncio",
    "engineio.server", "socketio.server", "pyrate_limiter", "modules.template_log",
)
_PID_ENV = "MYCELIAN_STARTUP_PROFILE_OWNER_PID"


# --------------------------------------------------------------------------- fakes


class Recorder:
    """Callable that records calls into a shared event list."""

    def __init__(self, events, name, result=None, raises=None):
        self.events, self.name, self.result, self.raises = events, name, result, raises
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.events.append(self.name)
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeStream(io.StringIO):
    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail
        self.reconfigured = []

    def reconfigure(self, **kwargs):
        if self.fail:
            raise ValueError("cannot reconfigure")
        self.reconfigured.append(kwargs)


class FakeThread:
    """Runs the NiceGUI preload inline; keeps the splash closer for the test to drive."""

    created: list = []

    def __init__(self, target=None, daemon=None, name=None, **kwargs):
        self.target, self.daemon, self.name = target, daemon, name
        self.started = self.joined = False
        FakeThread.created.append(self)

    def start(self):
        self.started = True
        if self.name == "NiceGUIImport":
            self.target()

    def join(self, timeout=None):
        self.joined = True


class FakeServiceManager:
    def __init__(self, events, progress_callback):
        self.events = events
        self.progress_callback = progress_callback
        self.registered: list = []
        self.deferred = None

    def register(self, name, fn, priority, background=False):
        self.events.append(f"register:{name}")
        self.registered.append((name, fn, priority, background))

    def start_deferred_init(self, delay_seconds):
        self.events.append("start_deferred_init")
        self.deferred = delay_seconds


class Env(types.SimpleNamespace):
    def mod(self, name):
        return self.modules[name]

    def run(self, run_name=RUN_AS_IMPORT):
        """Execute main.py's source. Returns the namespace (also kept on self.ns).

        ``__file__`` lives in tmp_path (so logs go there) and has a different basename than
        main.py: coverage would otherwise attribute the lines to the tmp file.
        sys.stdout/stderr are swapped only while the module body runs, so setup_logging's
        reconfigure() calls are recorded without touching pytest's capture streams.
        """
        self.ns = {"__name__": run_name, "__file__": str(self.tmp / "main_under_test.py"), "__builtins__": builtins}
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.stdout, self.stderr
        try:
            exec(_CODE, self.ns)
        finally:
            sys.stdout, sys.stderr = saved
        return self.ns

    def run_main(self):
        return self.run(RUN_AS_MAIN)


def _install(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, name, module)
    parent, _, attr = name.rpartition(".")
    if parent == "modules":
        monkeypatch.setattr(modules, attr, module, raising=False)


def _block(monkeypatch, name):
    """Make ``import <name>`` fail with ImportError."""
    monkeypatch.setitem(sys.modules, name, None)
    parent, _, attr = name.rpartition(".")
    if parent == "modules":
        monkeypatch.delattr(modules, attr, raising=False)


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fakes for everything main.py's top level touches."""
    events: list = []
    e = Env(tmp=tmp_path, events=events, modules={}, spawn_child=False)

    monkeypatch.setattr(app_startup, "is_spawn_child", lambda: e.spawn_child)

    def add(name, **attrs):
        mod = _module(name, **attrs)
        e.modules[name] = mod
        _install(monkeypatch, name, mod)
        return mod

    e.add = add

    class StartupTimer:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            events.append(f"timer:{self.name}")
            return self

        def __exit__(self, *exc):
            return False

    add("modules.ssl_certs", ensure_ssl_certificates=Recorder(events, "ensure_ssl_certificates"))
    add(
        "modules.startup_profiler",
        StartupTimer=StartupTimer,
        configure_startup_profiling=Recorder(events, "configure_startup_profiling"),
        get_elapsed_since_baseline=Recorder(events, "get_elapsed_since_baseline", 1.25),
        log_startup_summary=Recorder(events, "log_startup_summary"),
        mark_process_start=Recorder(events, "mark_process_start"),
        print_import_timing=Recorder(events, "print_import_timing"),
        print_startup_message=Recorder(events, "print_startup_message"),
        set_total_startup_time=Recorder(events, "set_total_startup_time"),
    )
    add("modules.log_filters", install_keepalive_missed_filter=Recorder(events, "install_keepalive_missed_filter"))
    add("modules.log_parser", get_log_file_path=lambda: tmp_path / "logs" / "mycelian.log")
    add("modules.system_notify", notify=Recorder(events, "notify"))
    add("modules.npsso_webview_capture", main=Recorder(events, "npsso_main", 3))

    e.signals = Recorder(events, "signal.signal")
    e.atexit = Recorder(events, "atexit.register")
    e.basic_config = Recorder(events, "logging.basicConfig")
    e.subprocess_run = Recorder(events, "subprocess.run")
    monkeypatch.setattr(signal, "signal", e.signals)
    monkeypatch.setattr(atexit, "register", e.atexit)
    monkeypatch.setattr(logging, "basicConfig", e.basic_config)
    monkeypatch.setattr(subprocess, "run", e.subprocess_run)
    e.stdout, e.stderr = FakeStream(), FakeStream()
    monkeypatch.setattr(sys, "argv", ["main.py"])

    levels = {name: logging.getLogger(name).level for name in _TUNED_LOGGERS}
    saved_pid = os.environ.pop(_PID_ENV, None)
    yield e
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)
    if saved_pid is None:
        os.environ.pop(_PID_ENV, None)
    else:
        os.environ[_PID_ENV] = saved_pid
    for args, kwargs in e.basic_config.calls:
        for handler in kwargs.get("handlers", []):
            handler.close()


@pytest.fixture
def startup(env, monkeypatch):
    """Additional fakes for the __main__ startup phases."""
    events = env.events
    FakeThread.created = []
    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(multiprocessing, "freeze_support", Recorder(events, "freeze_support"))
    env.reentry = False
    monkeypatch.setattr(app_startup, "critical_startup_done", lambda: env.reentry)
    monkeypatch.setattr(app_startup, "mark_critical_startup_done", Recorder(events, "mark_critical_startup_done"))
    monkeypatch.setitem(sys.modules, "nicegui", _module("nicegui"))

    env.add("modules.database_init", ensure_database_initialized=Recorder(events, "ensure_database_initialized", True), last_startup_error="db is locked")
    env.initial_data = {"app_settings": {}, "alerts": {}}

    async def load_all_initial_data():
        events.append("load_all_initial_data")
        return env.initial_data

    env.add("modules.database_manager", load_all_initial_data=load_all_initial_data)
    env.settings = types.SimpleNamespace(current_theme=" nord ")
    state_manager = types.SimpleNamespace(get_app_settings=lambda: env.settings)
    env.add("modules.dataobjects", initialize_with_data=Recorder(events, "dataobjects.initialize_with_data"), state_manager=state_manager)
    env.add("modules.alertutils", initialize_alert_state_with_data=Recorder(events, "alertutils.initialize_alert_state_with_data"))
    env.add(
        "modules.statistics_manager",
        initialize_statistics_with_data=Recorder(events, "statistics.initialize_statistics_with_data"),
        start_statistics_saving=Recorder(events, "statistics.start_statistics_saving"),
        shutdown_statistics=Recorder(events, "statistics.shutdown_statistics"),
    )
    env.add(
        "modules.mainuiwindow",
        initialize_ui_shell=Recorder(events, "initialize_ui_shell"),
        update_splash_progress=Recorder(events, "update_splash_progress"),
        close_splash_screen=Recorder(events, "close_splash_screen"),
        initialize_background_ui_services=Recorder(events, "initialize_background_ui_services"),
        start_ui=Recorder(events, "start_ui"),
        _is_benign_shutdown_websocket_error=Recorder(events, "is_benign", False),
    )
    env.theme_manager = types.SimpleNamespace(load_active_theme=Recorder(events, "load_active_theme"), _loaded_themes={"x": 1})
    env.add("modules.theme_manager", get_theme_manager=lambda: env.theme_manager)
    env.service_managers = []

    def get_service_manager(progress_callback=None):
        sm = FakeServiceManager(events, progress_callback)
        env.service_managers.append(sm)
        return sm

    env.add("modules.service_manager", get_service_manager=get_service_manager)
    env.add("modules.connection_monitor", start=Recorder(events, "connection_monitor.start"))
    env.add("modules.tray_controller", initialize=Recorder(events, "tray_controller.initialize"))
    env.in_progress = False
    env.add(
        "modules.shutdown",
        shutdown_application=Recorder(events, "shutdown_application"),
        is_shutdown_in_progress=lambda: env.in_progress,
        cleanup_shared_memory=Recorder(events, "cleanup_shared_memory"),
        mark_shutdown_in_progress=Recorder(events, "mark_shutdown_in_progress", True),
    )
    env.add("modules.updater", _force_application_exit=Recorder(events, "force_exit"))
    # Lazily imported service starters
    env.add("modules.twitch", initialize=Recorder(events, "twitch.initialize"))
    env.add("modules.chatbot", initialize=Recorder(events, "chatbot.initialize"))
    env.add("modules.connector_manager", initialize=Recorder(events, "connector_manager.initialize"))
    env.add("modules.connector_integration", initialize_integration=Recorder(events, "connector_integration.initialize_integration"))
    env.add("modules.spotify", start_spotify_service=Recorder(events, "spotify.start"))
    env.add("modules.psn_service", initialize_psn_module=Recorder(events, "psn.initialize"))
    env.add("modules.youtube", start_youtube_service=Recorder(events, "youtube.start"))
    env.add("modules.streamlabs", start_streamlabs_service=Recorder(events, "streamlabs.start"))
    env.add("modules.streamelements", start_streamelements_service=Recorder(events, "streamelements.start"))
    env.add("modules.discord_service", start_discord_service=Recorder(events, "discord.start"))
    env.add("modules.obs_service", start_obs_service=Recorder(events, "obs.start"))
    monkeypatch.setattr(sys, "platform", "linux")  # fatal errors use the (fake) notification path
    yield env
    asyncio.set_event_loop(None)


def _phase_events(events):
    return [e for e in events if not e.startswith(("timer:", "print_startup_message", "register:"))]


# --------------------------------------------------------------------------- module top level


def test_top_level_side_effects_in_app_process(env):
    ns = env.run()
    assert env.mod("modules.ssl_certs").ensure_ssl_certificates.calls == [((), {})]
    assert env.signals.calls == [((signal.SIGINT, ns["signal_handler"]), {}), ((signal.SIGTERM, ns["signal_handler"]), {})]
    assert env.atexit.calls == [((ns["emergency_stats_save"],), {})]
    assert os.environ[_PID_ENV] == str(os.getpid())
    prof = env.mod("modules.startup_profiler")
    assert prof.configure_startup_profiling.calls == [((False,), {})]
    assert prof.print_import_timing.calls == [(("process start",), {})]
    assert ns["ENABLE_STARTUP_PROFILING"] is False
    assert ns["_NPSSO_HELPER_FLAG"] == "--mycelian-npsso-capture"
    # Order: SSL probe, profiler setup, then logging (timed)
    assert env.events[:5] == ["ensure_ssl_certificates", "configure_startup_profiling", "mark_process_start", "print_import_timing", "timer:setup_logging"]
    assert ns["logger"].name == RUN_AS_IMPORT
    # Importing main.py does not run any startup phase
    assert "freeze_support" not in env.events and "start_ui" not in env.events


def test_top_level_in_spawn_child_skips_handlers_and_ssl(env):
    env.spawn_child = True
    ns = env.run()
    assert ns["_IS_SPAWN_CHILD"] is True
    assert env.mod("modules.ssl_certs").ensure_ssl_certificates.calls == []
    assert env.signals.calls == [] and env.atexit.calls == []


def test_profile_owner_pid_is_kept_when_already_set(env):
    os.environ[_PID_ENV] = "12345"
    env.run()
    assert os.environ[_PID_ENV] == "12345"


def test_npsso_helper_flag_runs_capture_and_exits(env):
    sys.argv[:] = ["Mycelian", "--mycelian-npsso-capture", "--port", "1"]
    with pytest.raises(SystemExit) as exc:
        env.run_main()
    assert exc.value.code == 3
    assert env.mod("modules.npsso_webview_capture").main.calls == [((), {})]
    # Nothing past the helper runs: no logging setup, no signal handlers, no startup
    assert env.basic_config.calls == [] and env.signals.calls == []
    assert env.mod("modules.startup_profiler").mark_process_start.calls == []


def test_npsso_helper_flag_ignored_when_imported(env):
    sys.argv[:] = ["Mycelian", "--mycelian-npsso-capture"]
    env.run()
    assert env.mod("modules.npsso_webview_capture").main.calls == []
    assert len(env.basic_config.calls) == 1


# --------------------------------------------------------------------------- path helpers


def test_get_resource_path_dev(env, monkeypatch):
    ns = env.run()
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert ns["get_resource_path"]("assets/x.png") == os.path.join(os.path.abspath("."), "assets/x.png")


def test_get_resource_path_frozen(env, monkeypatch):
    ns = env.run()
    monkeypatch.setattr(sys, "_MEIPASS", "/bundle", raising=False)
    assert ns["get_resource_path"]("assets/x.png") == os.path.join("/bundle", "assets/x.png")


def test_get_data_path_dev_uses_file_folder(env):
    ns = env.run()
    assert ns["get_data_path"]("logs") == os.path.join(str(env.tmp), "logs")


def test_get_data_path_frozen_uses_executable_folder(env, monkeypatch):
    ns = env.run()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(env.tmp / "app" / "Mycelian.exe"))
    assert ns["get_data_path"]("logs") == os.path.join(str(env.tmp / "app"), "logs")


# --------------------------------------------------------------------------- setup_logging


def test_setup_logging_configures_handlers(env):
    ns = env.run()
    assert (env.tmp / "logs").is_dir()
    (args, kwargs), = env.basic_config.calls
    assert kwargs["level"] == logging.WARNING
    assert kwargs["format"] == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_handler, stream_handler = kwargs["handlers"]
    assert isinstance(file_handler, ns["CappedFileHandler"])
    assert file_handler.baseFilename == str(env.tmp / "logs" / "mycelian.log")
    assert file_handler.max_bytes == ns["LOG_MAX_BYTES"] == 15 * 1024 * 1024
    assert file_handler.keep_bytes == ns["LOG_TRIM_KEEP_BYTES"] == 12 * 1024 * 1024
    assert file_handler.encoding == "utf-8"
    assert type(stream_handler) is logging.StreamHandler
    assert env.stdout.reconfigured == [{"encoding": "utf-8"}]
    assert env.stderr.reconfigured == [{"encoding": "utf-8"}]
    assert logging.getLogger("pyrate_limiter").level == logging.CRITICAL
    assert logging.getLogger("modules.template_log").level == logging.INFO
    assert logging.getLogger("werkzeug").level == logging.WARNING
    assert env.mod("modules.log_filters").install_keepalive_missed_filter.calls == [((), {})]


def test_setup_logging_reconfigure_failure_ignored(env):
    env.stdout = FakeStream(fail=True)
    env.run()
    assert len(env.basic_config.calls) == 1


def test_setup_logging_streams_without_reconfigure(env):
    env.stdout, env.stderr = io.BytesIO(), io.BytesIO()
    env.run()
    assert len(env.basic_config.calls) == 1


def test_setup_logging_existing_logs_dir(env):
    (env.tmp / "logs").mkdir()
    ns = env.run()
    ns["setup_logging"]()
    assert len(env.basic_config.calls) == 2


# --------------------------------------------------------------------------- CappedFileHandler


def _record(msg="hello"):
    return logging.LogRecord("t", logging.WARNING, __file__, 1, msg, None, None)


def test_capped_handler_trims_on_open(env):
    ns = env.run()
    path = env.tmp / "big.log"
    path.write_bytes(b"".join(b"line %04d\n" % i for i in range(100)))  # 1000 bytes
    handler = ns["CappedFileHandler"](path, max_bytes=500, keep_bytes=200)
    handler.close()
    data = path.read_bytes()
    assert 0 < len(data) <= 200 and data.endswith(b"line 0099\n")


def test_capped_handler_keep_bytes_capped_at_max(env):
    ns = env.run()
    handler = ns["CappedFileHandler"](env.tmp / "a.log", max_bytes=100, keep_bytes=1000)
    handler.close()
    assert handler.keep_bytes == 100


def test_capped_handler_emit_and_rollover(env):
    ns = env.run()
    path = env.tmp / "a.log"
    handler = ns["CappedFileHandler"](path, max_bytes=300, keep_bytes=100)
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        handler.emit(_record("first"))
        assert path.read_text() == "first\n"
        for i in range(40):
            handler.emit(_record(f"message number {i:03d}"))
            assert path.stat().st_size <= 300
        assert handler.stream is not None  # reopened after trimming
        assert path.read_text().endswith("message number 039\n")
    finally:
        handler.close()


def test_capped_handler_delay_reopens_lazily(env):
    ns = env.run()
    path = env.tmp / "a.log"
    handler = ns["CappedFileHandler"](path, max_bytes=50, keep_bytes=10, delay=True)
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        handler.emit(_record("x" * 60))
        assert handler.stream is None  # trimmed and left closed (delay)
        handler.emit(_record("y"))
        assert handler.stream is not None
        assert path.read_text().endswith("y\n")
    finally:
        handler.close()


def test_capped_handler_closed_handler_ignores_records(env):
    ns = env.run()
    handler = ns["CappedFileHandler"](env.tmp / "a.log")
    handler.close()
    handler.baseFilename = None
    handler.emit(_record())  # no error, nothing written


def test_capped_handler_format_error_goes_to_handle_error(env, monkeypatch):
    ns = env.run()
    handler = ns["CappedFileHandler"](env.tmp / "a.log")
    errors = []
    monkeypatch.setattr(handler, "handleError", lambda record: errors.append(record))
    monkeypatch.setattr(handler, "format", lambda record: (_ for _ in ()).throw(ValueError("bad format")))
    rec = _record()
    handler.emit(rec)
    handler.close()
    assert errors == [rec]


def test_capped_handler_recursion_error_propagates(env, monkeypatch):
    ns = env.run()
    handler = ns["CappedFileHandler"](env.tmp / "a.log")
    monkeypatch.setattr(handler, "format", lambda record: (_ for _ in ()).throw(RecursionError()))
    errors = []
    monkeypatch.setattr(handler, "handleError", lambda record: errors.append(record))
    try:
        handler.emit(_record())
    finally:
        handler.close()
    # The inner handler re-raises RecursionError; the outer guard reports it once.
    assert len(errors) == 1


def test_capped_handler_lock_failure_goes_to_handle_error(env, monkeypatch):
    ns = env.run()
    handler = ns["CappedFileHandler"](env.tmp / "a.log")
    errors = []
    monkeypatch.setattr(handler, "acquire", lambda: (_ for _ in ()).throw(RuntimeError("lock")))
    monkeypatch.setattr(handler, "handleError", lambda record: errors.append(record))
    handler.emit(_record())
    handler.close()
    assert len(errors) == 1


# --------------------------------------------------------------------------- signal_handler


@pytest.fixture
def loaded(startup):
    """main.py imported (not run as main) with the startup fakes in place."""
    startup.run()
    startup.events.clear()
    return startup


def test_signal_handler_shuts_down(loaded, capsys):
    loaded.ns["signal_handler"](15, None)
    assert loaded.events == ["shutdown_application", "cleanup_shared_memory", "force_exit"]
    assert loaded.mod("modules.shutdown").shutdown_application.calls == [((), {"reason": "signal_15", "force": False})]
    out = capsys.readouterr().out
    assert "Received signal 15, shutting down..." in out and "Cleanup completed, exiting..." in out


def test_signal_handler_ignored_during_shutdown(loaded, capsys):
    loaded.in_progress = True
    loaded.ns["signal_handler"](2, None)
    assert loaded.events == []
    assert capsys.readouterr().out == ""


def test_signal_handler_shutdown_module_importing(loaded, monkeypatch):
    _block(monkeypatch, "modules.shutdown")
    assert loaded.ns["signal_handler"](2, None) is None
    assert loaded.events == []


def test_signal_handler_cleanup_error_exits(loaded, capsys):
    loaded.mod("modules.shutdown").cleanup_shared_memory.raises = OSError("shm busy")
    with pytest.raises(SystemExit) as exc:
        loaded.ns["signal_handler"](2, None)
    assert exc.value.code == 1
    assert "Error during cleanup: shm busy" in capsys.readouterr().out
    assert "force_exit" not in loaded.events


# --------------------------------------------------------------------------- emergency_stats_save


def test_emergency_stats_save_runs(loaded, capsys):
    loaded.ns["emergency_stats_save"]()
    assert loaded.events == ["mark_shutdown_in_progress", "statistics.shutdown_statistics"]
    out = capsys.readouterr().out
    assert "Emergency statistics save (atexit fallback)..." in out
    assert "Emergency statistics save completed" in out


def test_emergency_stats_save_skipped_during_shutdown(loaded):
    loaded.in_progress = True
    loaded.ns["emergency_stats_save"]()
    assert loaded.events == []


def test_emergency_stats_save_skipped_when_mark_fails(loaded):
    loaded.mod("modules.shutdown").mark_shutdown_in_progress.result = False
    loaded.ns["emergency_stats_save"]()
    assert loaded.events == ["mark_shutdown_in_progress"]


def test_emergency_stats_save_import_error(loaded, monkeypatch):
    _block(monkeypatch, "modules.shutdown")
    assert loaded.ns["emergency_stats_save"]() is None
    assert loaded.events == []


def test_emergency_stats_save_failure_is_reported(loaded, capsys):
    loaded.mod("modules.statistics_manager").shutdown_statistics.raises = RuntimeError("db closed")
    loaded.ns["emergency_stats_save"]()
    assert "Emergency statistics save failed: db closed" in capsys.readouterr().out


# --------------------------------------------------------------------------- fatal startup error


def test_fatal_error_linux_notifies(loaded, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    loaded.ns["_show_fatal_startup_error"]("  Disk full  ")
    notify = loaded.mod("modules.system_notify").notify
    expected = f"Disk full\n\nDetails are in the log: {loaded.tmp / 'logs' / 'mycelian.log'}"
    assert notify.calls == [((expected,), {"title": "Mycelian couldn't start", "force": True})]
    assert loaded.subprocess_run.calls == []


def test_fatal_error_default_reason_and_log_hint_fallback(loaded, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    loaded.mod("modules.log_parser").get_log_file_path = lambda: (_ for _ in ()).throw(OSError("no path"))
    loaded.ns["_show_fatal_startup_error"](None)
    (args, kwargs), = loaded.mod("modules.system_notify").notify.calls
    assert args == ("Something went wrong while starting.\n\nDetails are in mycelian.log.",)


def test_fatal_error_windows_message_box(loaded, monkeypatch):
    import ctypes

    monkeypatch.setattr(sys, "platform", "win32")
    boxes = []
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=types.SimpleNamespace(MessageBoxW=lambda *a: boxes.append(a))), raising=False)
    loaded.ns["_show_fatal_startup_error"]("Boom")
    assert len(boxes) == 1
    hwnd, message, title, flags = boxes[0]
    assert (hwnd, title, flags) == (0, "Mycelian couldn't start", 0x10)
    assert message.startswith("Boom\n\nDetails are in the log:")
    assert loaded.mod("modules.system_notify").notify.calls == []


def test_fatal_error_macos_alert_escapes_text(loaded, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    loaded.mod("modules.log_parser").get_log_file_path = lambda: Path("/logs/m.log")
    loaded.ns["_show_fatal_startup_error"]('Bad "quote" and \\ slash')
    (args, kwargs), = loaded.subprocess_run.calls
    cmd = args[0]
    assert cmd[:2] == ["osascript", "-e"]
    assert cmd[2] == (
        'display alert "Mycelian couldn\'t start" message '
        '"Bad \\"quote\\" and \\\\ slash\n\nDetails are in the log: /logs/m.log" as critical'
    )
    assert kwargs == {"timeout": 600, "check": False}
    assert loaded.mod("modules.system_notify").notify.calls == []


def test_fatal_error_dialog_failure_falls_back_to_notification(loaded, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    loaded.subprocess_run.raises = FileNotFoundError("osascript")
    loaded.ns["_show_fatal_startup_error"]("Boom")
    assert len(loaded.mod("modules.system_notify").notify.calls) == 1


def test_fatal_error_notification_failure_never_raises(loaded, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    loaded.mod("modules.system_notify").notify.raises = RuntimeError("no dbus")
    loaded.ns["_show_fatal_startup_error"]("Boom")  # does not raise


def test_exit_startup_failed(loaded):
    with pytest.raises(SystemExit) as exc:
        loaded.ns["_exit_startup_failed"]("Nope")
    assert exc.value.code == 1
    (args, _), = loaded.mod("modules.system_notify").notify.calls
    assert args[0].startswith("Nope\n\n")


# --------------------------------------------------------------------------- startup phases


def test_startup_runs_phases_in_order(startup):
    startup.run_main()
    phases = _phase_events(startup.events)
    start = phases.index("freeze_support")
    assert phases[start:] == [
        "freeze_support",
        "ensure_database_initialized",
        "load_all_initial_data",
        "dataobjects.initialize_with_data",
        "alertutils.initialize_alert_state_with_data",
        "statistics.initialize_statistics_with_data",
        "initialize_ui_shell",
        "load_active_theme",
        "start_deferred_init",
        "set_total_startup_time",
        "get_elapsed_since_baseline",
        "log_startup_summary",
        "mark_critical_startup_done",
        "start_ui",
        "shutdown_application",
        "force_exit",
    ]
    # Phase 1 runs the NiceGUI import on its own daemon thread, joined before the UI shell
    preload = next(t for t in FakeThread.created if t.name == "NiceGUIImport")
    assert preload.daemon is True and preload.started and preload.joined
    assert "timer:import:nicegui" in startup.events
    # Startup data reaches every core module
    for name, func in (
        ("modules.dataobjects", "initialize_with_data"),
        ("modules.alertutils", "initialize_alert_state_with_data"),
        ("modules.statistics_manager", "initialize_statistics_with_data"),
    ):
        assert getattr(startup.mod(name), func).calls == [((startup.initial_data,), {})]
    assert startup.mod("modules.shutdown").shutdown_application.calls == [((), {"reason": "ui_run_returned", "force": False})]
    messages = [c[0][0] for c in startup.mod("modules.startup_profiler").print_startup_message.calls]
    assert messages[0].startswith("critical path (__main__ only) ready in ")
    assert messages[1] == "total since import baseline: 1.250s (includes module imports above)"
    assert messages[-2:] == ["starting UI server (ui.run will block until shutdown)...", "ui.run returned (application shutdown)"]


def test_startup_registers_services_by_priority(startup):
    startup.run_main()
    (sm,) = startup.service_managers
    registered = [(name, priority, background) for name, _, priority, background in sm.registered]
    assert registered == [
        ("statistics_saving", 1, False),
        ("twitch", 2, False),
        ("chatbot", 3, False),
        ("connectors", 4, False),
        ("spotify", 5, False),
        ("psn", 6, False),
        ("youtube", 7, False),
        ("streamlabs", 8, False),
        ("streamelements", 8, False),
        ("discord", 7, True),
        ("obs", 5, False),
        ("connection_monitor", 8, False),
        ("background_ui_services", 8, False),
        ("tray", 9, False),
    ]
    assert sm.deferred == 1.0
    fns = {name: fn for name, fn, _, _ in sm.registered}
    assert fns["statistics_saving"] is startup.mod("modules.statistics_manager").start_statistics_saving
    assert fns["connection_monitor"] is startup.mod("modules.connection_monitor").start
    assert fns["background_ui_services"] is startup.mod("modules.mainuiwindow").initialize_background_ui_services
    assert fns["tray"] is startup.mod("modules.tray_controller").initialize


def test_startup_service_starters_call_their_modules(startup):
    startup.run_main()
    (sm,) = startup.service_managers
    fns = {name: fn for name, fn, _, _ in sm.registered}
    startup.events.clear()
    for name in ("twitch", "chatbot", "connectors", "spotify", "psn", "youtube", "streamlabs", "streamelements", "discord", "obs"):
        fns[name]()
    assert startup.events == [
        "twitch.initialize",
        "chatbot.initialize",
        "connector_manager.initialize",
        "connector_integration.initialize_integration",
        "spotify.start",
        "psn.initialize",
        "youtube.start",
        "streamlabs.start",
        "streamelements.start",
        "discord.start",
        "obs.start",
    ]


def test_startup_progress_callback_updates_splash(startup):
    startup.run_main()
    startup.service_managers[0].progress_callback(40, "Loading Twitch")
    assert startup.mod("modules.mainuiwindow").update_splash_progress.calls == [((40, "Loading Twitch"), {})]


def test_startup_splash_closes_after_delay(startup, monkeypatch):
    startup.run_main()
    splash = [t for t in FakeThread.created if t.name != "NiceGUIImport"]
    assert len(splash) == 1 and splash[0].daemon is True and splash[0].started
    assert startup.mod("modules.mainuiwindow").close_splash_screen.calls == []
    sleeps = []
    monkeypatch.setattr(startup.ns["time"], "sleep", lambda s: sleeps.append(s))
    splash[0].target()
    assert sleeps == [2.0]
    assert startup.mod("modules.mainuiwindow").close_splash_screen.calls == [((), {})]


@pytest.mark.parametrize(
    "settings,expected",
    [
        (types.SimpleNamespace(current_theme=" nord "), "nord"),
        (types.SimpleNamespace(current_theme="   "), "dark"),
        (types.SimpleNamespace(current_theme=None), "dark"),
        (types.SimpleNamespace(), "dark"),
        (None, "dark"),
    ],
)
def test_startup_loads_saved_theme(startup, settings, expected):
    startup.settings = settings
    startup.run_main()
    assert startup.theme_manager.load_active_theme.calls == [((expected,), {})]


def test_startup_theme_settings_error_falls_back_to_dark(startup):
    startup.mod("modules.dataobjects").state_manager = types.SimpleNamespace(
        get_app_settings=lambda: (_ for _ in ()).throw(RuntimeError("no settings"))
    )
    startup.run_main()
    assert startup.theme_manager.load_active_theme.calls == [(("dark",), {})]


def test_startup_reentry_skips_everything(startup):
    startup.reentry = True
    startup.run_main()
    phases = _phase_events(startup.events)
    assert phases[phases.index("freeze_support"):] == ["freeze_support"]
    assert FakeThread.created == []


def test_startup_frozen_changes_working_directory(startup, monkeypatch):
    chdirs = []
    monkeypatch.setattr(os, "chdir", lambda path: chdirs.append(path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(startup.tmp / "dist" / "Mycelian"))
    (startup.tmp / "dist").mkdir()
    startup.run_main()
    assert chdirs == [str(startup.tmp / "dist")]
    # Frozen builds keep logs next to the executable
    assert (startup.tmp / "dist" / "logs").is_dir()


def _expect_startup_exit(startup, text):
    with pytest.raises(SystemExit) as exc:
        startup.run_main()
    assert exc.value.code == 1
    (args, kwargs), = startup.mod("modules.system_notify").notify.calls
    assert text in args[0]
    assert kwargs == {"title": "Mycelian couldn't start", "force": True}
    assert "start_ui" not in startup.events
    assert "mark_critical_startup_done" not in startup.events


def test_startup_database_not_initialized(startup):
    startup.mod("modules.database_init").ensure_database_initialized.result = False
    _expect_startup_exit(startup, "db is locked")
    assert "load_all_initial_data" not in startup.events


def test_startup_database_init_raises(startup):
    startup.mod("modules.database_init").ensure_database_initialized.raises = RuntimeError("corrupt")
    _expect_startup_exit(startup, "Mycelian's database system failed to start.\n\ncorrupt")


def test_startup_loading_data_fails(startup):
    async def broken():
        raise RuntimeError("read error")

    startup.mod("modules.database_manager").load_all_initial_data = broken
    _expect_startup_exit(startup, "Mycelian couldn't load your data from the database.\n\nread error")
    assert "dataobjects.initialize_with_data" not in startup.events


def test_startup_core_modules_fail(startup):
    startup.mod("modules.alertutils").initialize_alert_state_with_data.raises = KeyError("alerts")
    _expect_startup_exit(startup, "Mycelian couldn't set up your settings and alerts.")
    assert "initialize_ui_shell" not in startup.events


def test_startup_nicegui_import_fails(startup, monkeypatch):
    monkeypatch.setitem(sys.modules, "nicegui", None)
    _expect_startup_exit(startup, "Mycelian couldn't build its window.")
    assert "initialize_ui_shell" not in startup.events


def test_startup_ui_shell_fails(startup):
    startup.mod("modules.mainuiwindow").initialize_ui_shell.raises = RuntimeError("no display")
    _expect_startup_exit(startup, "Mycelian couldn't build its window.\n\nno display")


def test_startup_deferred_service_errors_are_not_fatal(startup):
    startup.mod("modules.service_manager").get_service_manager = lambda progress_callback=None: (_ for _ in ()).throw(RuntimeError("sm"))
    startup.run_main()
    assert "start_ui" in startup.events
    assert "mark_critical_startup_done" in startup.events
    assert startup.mod("modules.system_notify").notify.calls == []


@pytest.mark.parametrize("in_progress,benign", [(True, False), (False, True)])
def test_startup_ui_error_during_shutdown(startup, in_progress, benign):
    startup.mod("modules.mainuiwindow").start_ui.raises = ConnectionResetError("socket closed")
    startup.mod("modules.mainuiwindow")._is_benign_shutdown_websocket_error.result = benign
    startup.in_progress = in_progress
    with pytest.raises(SystemExit) as exc:
        startup.run_main()
    # The real _force_application_exit never returns; the fake does, so the error exit follows.
    assert exc.value.code == 1
    after = startup.events[startup.events.index("start_ui") + 1:]
    assert [e for e in after if e != "is_benign"] == ["shutdown_application", "force_exit"]
    assert startup.mod("modules.shutdown").shutdown_application.calls == [((), {"reason": "ui_run_returned", "force": False})]


def test_startup_ui_error_exits_with_failure(startup):
    startup.mod("modules.mainuiwindow").start_ui.raises = OSError("port in use")
    with pytest.raises(SystemExit) as exc:
        startup.run_main()
    assert exc.value.code == 1
    after = startup.events[startup.events.index("start_ui") + 1:]
    assert "shutdown_application" not in after and "force_exit" not in after
    assert startup.mod("modules.mainuiwindow")._is_benign_shutdown_websocket_error.calls[0][0][0].args == ("port in use",)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Module-level helpers of modules/web_engine.py (everything above ``class WebEngine``).

HTML injection, lazy imports, port/cmdline helpers, request-origin checks, the local-only
middleware, the safe_emit queue, the alert-queue state/skip helpers, health helpers and
evaluate_bottleneck.
"""

from __future__ import annotations

import errno
import queue
import sys
import types
from collections import deque
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine as we  # noqa: E402


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def flask_ready():
    """Flask/SocketIO globals loaded (idempotent, as WebEngine() does)."""
    we._ensure_server_deps()
    return we


@pytest.fixture
def alert_globals(monkeypatch):
    """Snapshot and restore every alert-queue global the helpers mutate."""
    for name in ("ALERT_PLAYING", "ALERTS_PAUSED", "ALERTS_MUTED", "EXPECTED_ALERT_COMPLETE_SEQ",
                 "_queue_state_revision", "_queue_state_last_sig", "_last_skip_at",
                 "_alert_queue_seq", "web_engine_instance"):
        monkeypatch.setattr(we, name, getattr(we, name))
    return we


class FakeEngine:
    def __init__(self, result=True, raises=None):
        self.emits = []
        self.result = result
        self.raises = raises

    def safe_emit(self, event, data=None, **kw):
        if self.raises:
            raise self.raises
        self.emits.append((event, data))
        return self.result


# --------------------------------------------------------------------------- HTML injection


def test_inject_served_overlay_scripts_chains_recovery_then_logger(monkeypatch):
    import modules.spore_studio.overlay_recovery_inject as ori

    monkeypatch.setattr(ori, "inject_overlay_recovery", lambda h: h + "<R>")
    monkeypatch.setattr(we, "inject_template_logger", lambda h: h + "<L>")
    assert we._inject_served_overlay_scripts("x") == "x<R><L>"


class _Theme:
    def __init__(self, theme_type="light"):
        self.theme_type = theme_type


def _theme_mgr(theme):
    return types.SimpleNamespace(get_theme=lambda: theme)


def test_inject_theme_vars_inserts_style_before_head_and_theme_type(monkeypatch):
    monkeypatch.setattr(we, "get_theme_manager", lambda: _theme_mgr(_Theme("light")))
    monkeypatch.setattr(we, "generate_css_variables", lambda t: ":root{--a:1}")
    out = we._inject_theme_vars("<html><head></head><body></body></html>")
    assert '<style id="mycelian-theme-vars">:root{--a:1}</style>\n</head>' in out
    assert '<html data-theme-type="light"' in out


def test_inject_theme_vars_without_head_prepends_and_defaults_dark(monkeypatch):
    monkeypatch.setattr(we, "get_theme_manager", lambda: _theme_mgr(_Theme(None)))
    monkeypatch.setattr(we, "generate_css_variables", lambda t: "CSS")
    out = we._inject_theme_vars("<html><body/></html>")
    assert out.startswith('<style id="mycelian-theme-vars">CSS</style>')
    assert 'data-theme-type="dark"' in out


def test_inject_theme_vars_keeps_existing_blocks(monkeypatch):
    monkeypatch.setattr(we, "get_theme_manager", lambda: _theme_mgr(_Theme("light")))
    monkeypatch.setattr(we, "generate_css_variables", lambda t: "CSS")
    html = '<html data-theme-type="dark"><head><style id="mycelian-theme-vars"></style></head></html>'
    assert we._inject_theme_vars(html) == html


def test_inject_theme_vars_no_theme_or_error_returns_input(monkeypatch):
    monkeypatch.setattr(we, "get_theme_manager", lambda: _theme_mgr(None))
    assert we._inject_theme_vars("<html>") == "<html>"

    def boom():
        raise RuntimeError("no theme")

    monkeypatch.setattr(we, "get_theme_manager", boom)
    assert we._inject_theme_vars("<html>") == "<html>"


def test_theme_dock_html_only_themes_dock_templates(monkeypatch):
    monkeypatch.setattr(we, "_inject_theme_vars", lambda h: "THEMED:" + h)
    assert we._theme_dock_html("source_controls", "a") == "THEMED:a"
    assert we._theme_dock_html("activity_feed", "a") == "THEMED:a"
    assert we._theme_dock_html("alerts", "a") == "a"
    assert we._theme_dock_html("activity_feed", None) is None


def test_finalize_served_overlay_html_outside_request_and_no_embed(monkeypatch, flask_ready):
    monkeypatch.setattr(we, "_inject_served_overlay_scripts", lambda h: h + "!")
    monkeypatch.setattr(we, "request", None)
    assert we._finalize_served_overlay_html("p") == "p!"


def test_finalize_served_overlay_html_embed_audio(monkeypatch, flask_ready):
    import flask
    import modules.spore_studio.containers as containers

    monkeypatch.setattr(we, "_inject_served_overlay_scripts", lambda h: h)
    calls = []
    monkeypatch.setattr(containers, "inject_embed_audio",
                        lambda html, volume, muted: calls.append((volume, muted)) or html + "+A")
    app = flask.Flask("t")
    with app.test_request_context("/?mycelian_embed=1&mycelian_muted=1&mycelian_volume=40"):
        assert we._finalize_served_overlay_html("p") == "p+A"
    with app.test_request_context("/?mycelian_embed=1&mycelian_volume=loud"):
        assert we._finalize_served_overlay_html("p") == "p+A"
    with app.test_request_context("/"):
        assert we._finalize_served_overlay_html("p") == "p"
    assert calls == [(40.0, True), (100.0, False)]


def test_finalize_served_overlay_html_embed_failure_is_swallowed(monkeypatch, flask_ready):
    import flask
    import modules.spore_studio.containers as containers

    monkeypatch.setattr(we, "_inject_served_overlay_scripts", lambda h: h)
    monkeypatch.setattr(containers, "inject_embed_audio", mock.Mock(side_effect=RuntimeError("x")))
    with flask.Flask("t").test_request_context("/?mycelian_embed=1"):
        assert we._finalize_served_overlay_html("p") == "p"


# --------------------------------------------------------------------------- lazy imports


def test_ensure_server_deps_loads_flask_globals_once():
    we._ensure_server_deps()
    import flask
    import flask_socketio

    assert we.Flask is flask.Flask
    assert we.SocketIO is flask_socketio.SocketIO
    assert we.request is flask.request
    sio = we.SocketIO
    we._ensure_server_deps()  # fast path
    assert we.SocketIO is sio


def test_ensure_server_deps_second_check_inside_lock(monkeypatch):
    """Another thread finished the import while this one waited for the lock."""
    we._ensure_server_deps()
    sentinel = we.SocketIO

    class _Lock:
        def __enter__(self):
            monkeypatch.setattr(we, "SocketIO", sentinel)
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(we, "SocketIO", None)
    monkeypatch.setattr(we, "_server_deps_lock", _Lock())
    we._ensure_server_deps()
    assert we.SocketIO is sentinel


def test_lazy_twitch_loads_module_on_first_attribute():
    lazy = we._LazyTwitch()
    assert object.__getattribute__(lazy, "_mod") is None
    fake = types.SimpleNamespace(answer=42)
    with mock.patch.dict(sys.modules, {"modules.twitch": fake}):
        import modules

        with mock.patch.object(modules, "twitch", fake, create=True):
            assert lazy.answer == 42
            lazy.answer = 7
            assert fake.answer == 7
            assert object.__getattribute__(lazy, "_mod") is fake
            assert lazy._load() is fake


# --------------------------------------------------------------------------- port / process helpers


@pytest.mark.parametrize("exc, expected", [
    (OSError(errno.EADDRINUSE, "x"), True),
    (OSError(10048, "x"), True),
    (OSError(1, "Only one usage 10048"), True),
    (RuntimeError("EADDRINUSE"), True),
    (RuntimeError("Address already in use"), True),
    (RuntimeError("[Errno 48] taken"), True),
    (RuntimeError("errno 48"), True),
    (OSError(errno.ENOENT, "missing"), False),
    (ValueError("nope"), False),
])
def test_is_addr_in_use(exc, expected):
    assert we._is_addr_in_use(exc) is expected


def test_is_addr_in_use_winerror_attribute():
    exc = OSError(5, "denied")
    exc.winerror = 10048
    assert we._is_addr_in_use(exc) is True


def _completed(stdout):
    return types.SimpleNamespace(stdout=stdout)


@pytest.mark.parametrize("platform, expected_cmd", [
    ("win32", ["tasklist", "/FI", "PID eq 12", "/FO", "CSV", "/NH"]),
    ("darwin", ["ps", "-p", "12", "-o", "command="]),
])
def test_process_cmdline_windows_and_mac(monkeypatch, platform, expected_cmd):
    run = mock.Mock(return_value=_completed("  python main.py \n"))
    monkeypatch.setattr(we.sys, "platform", platform)
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    assert we._process_cmdline(12) == "python main.py"
    assert run.call_args[0][0] == expected_cmd


def test_process_cmdline_linux_proc_file(monkeypatch, tmp_path):
    proc = tmp_path / "cmdline"
    proc.write_bytes(b"python\x00main.py\x00")
    monkeypatch.setattr(we.sys, "platform", "linux")
    real_path = we.Path
    monkeypatch.setattr(we, "Path", lambda p: proc if p == "/proc/12/cmdline" else real_path(p))
    run = mock.Mock()
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    assert we._process_cmdline(12) == "python main.py"
    run.assert_not_called()


def test_process_cmdline_linux_ps_fallback_and_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(we.sys, "platform", "linux")
    monkeypatch.setattr(we, "Path", lambda p: tmp_path / "missing")
    run = mock.Mock(return_value=_completed(None))
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=run))
    assert we._process_cmdline(9) == ""
    assert run.call_args[0][0] == ["ps", "-p", "9", "-o", "args="]
    monkeypatch.setattr(we, "subprocess", types.SimpleNamespace(run=mock.Mock(side_effect=OSError("no ps"))))
    assert we._process_cmdline(9) == ""


@pytest.mark.parametrize("cmdline, expected", [
    ("", False),
    ("/System/Library/CoreServices/ControlCenter.app", False),
    ("Control Center helper mycelian", False),
    ("AirPlayXPCHelper", False),
    ("/Applications/Mycelian.app/Contents/MacOS/Mycelian", True),
    ("python3 main.py", True),
    ("uv run main.py", True),
    ("C:\\uv.exe run main.py", True),
    ("node main.py", False),
])
def test_cmdline_looks_like_mycelian(cmdline, expected):
    assert we._cmdline_looks_like_mycelian(cmdline) is expected


@pytest.mark.parametrize("element, expected", [
    ({"target_counter_id": "c1", "step": 3},
     {"target_counter_id": "c1", "operation": "increment", "delta": {"kind": "fixed", "value": 3}}),
    ({"target_counter_id": "c1", "step": 0},
     {"target_counter_id": "c1", "operation": "increment", "delta": {"kind": "fixed", "value": 1}}),
    ({"target_counter_id": "c1", "step": "bad"},
     {"target_counter_id": "c1", "operation": "increment", "delta": {"kind": "fixed", "value": 1}}),
    ({"action": "increment"}, {"action": "increment"}),
    ({"action": "deaths_increment"}, {"action": "increment"}),
    ({"action": "counter_decrement"}, {"action": "decrement"}),
    ({"action": "x_decrement"}, {"action": "decrement"}),
    ({"action": "reset"}, {"action": "reset"}),
    ({"action": "wins_reset"}, {"action": "reset"}),
    ({"action": "toggle"}, {}),
    ({}, {}),
])
def test_dynamic_counter_control_default_data(element, expected):
    assert we._dynamic_counter_control_default_data(element) == expected


@pytest.mark.parametrize("name, mime", [
    ("a.WAV", "audio/wav"), ("x/y.mp3", "audio/mpeg"), ("b.flac", "audio/flac"),
    ("c.png", None), ("noext", None),
])
def test_mimetype_for_asset_filename(name, mime):
    assert we._mimetype_for_asset_filename(name) == mime


@pytest.mark.parametrize("addr, expected", [
    ("127.0.0.1", True), (" ::1 ", True), ("LOCALHOST", True), ("::ffff:127.0.0.1", True),
    ("10.0.0.2", False), (None, False), ("", False),
])
def test_is_localhost_remote_addr(addr, expected):
    assert we._is_localhost_remote_addr(addr) is expected


@pytest.mark.parametrize("value, default, expected", [
    ("12", 5, 12), (3.9, 5, 3), ("0", 5, 5), (-4, 5, 5), ("abc", 5, 5), (None, 7, 7),
    (float("inf"), 9, 9),
])
def test_int_or(value, default, expected):
    assert we._int_or(value, default) == expected


@pytest.mark.parametrize("origin, site, host, expected", [
    (None, None, "127.0.0.1:5000", False),
    (None, "none", "127.0.0.1:5000", False),
    (None, "cross-site", "127.0.0.1:5000", True),
    ("null", None, "127.0.0.1:5000", True),
    ("http://127.0.0.1:8080", "same-origin", "127.0.0.1:5000", True),
    ("http://127.0.0.1:5000", "same-origin", "127.0.0.1:5000", False),
])
def test_is_cross_site_request(origin, site, host, expected):
    assert we._is_cross_site_request(origin, site, host) is expected


def test_is_cross_site_request_unparseable_origin():
    assert we._is_cross_site_request("http://[::1", None, "127.0.0.1:5000") is True


def test_is_cross_site_request_same_host_case_insensitive():
    assert we._is_cross_site_request("HTTP://LocalHost:5000", "same-origin", "localhost:5000") is False


@pytest.mark.parametrize("addr, expected", [
    ("127.0.0.1", True), ("10.1.2.3", True), ("172.20.0.1", True), ("192.168.1.5", True),
    ("169.254.1.1", True), ("100.100.1.1", True), ("::1", True), ("[::1]", True),
    ("fe80::1%en0", True), ("fd00::5", True), ("::ffff:192.168.0.2", True),
    ("8.8.8.8", False), ("2001:4860::8888", False), ("", False), (None, False), ("nonsense", False),
])
def test_is_local_client_addr(addr, expected):
    assert we.is_local_client_addr(addr) is expected


def test_local_only_middleware_passes_local_and_rejects_public():
    inner = mock.Mock(return_value=[b"ok"])
    mw = we.LocalOnlyMiddleware(inner)
    start = mock.Mock()
    assert mw({"REMOTE_ADDR": "127.0.0.1"}, start) == [b"ok"]
    inner.assert_called_once()
    assert mw({"REMOTE_ADDR": "8.8.8.8", "PATH_INFO": "/x"}, start) == [b"Forbidden"]
    status, headers = start.call_args[0]
    assert status == "403 Forbidden"
    assert ("Content-Length", "9") in headers
    assert mw({}, start) == [b"Forbidden"]
    assert inner.call_count == 1


def test_reject_non_localhost_socket(monkeypatch):
    monkeypatch.setattr(we, "request", types.SimpleNamespace(remote_addr="127.0.0.1"))
    assert we._reject_non_localhost_socket() is False
    monkeypatch.setattr(we, "request", types.SimpleNamespace(remote_addr="10.0.0.9"))
    assert we._reject_non_localhost_socket() is True
    monkeypatch.setattr(we, "request", None)
    assert we._reject_non_localhost_socket() is True


def test_dev_debug_endpoints_enabled(monkeypatch):
    monkeypatch.delenv("MYCELIAN_DEV", raising=False)
    monkeypatch.setattr(we.sys, "frozen", True, raising=False)
    assert we._dev_debug_endpoints_enabled() is False
    monkeypatch.setenv("MYCELIAN_DEV", "1")
    assert we._dev_debug_endpoints_enabled() is True
    monkeypatch.delenv("MYCELIAN_DEV")
    monkeypatch.delattr(we.sys, "frozen")
    assert we._dev_debug_endpoints_enabled() is True


@pytest.mark.parametrize("url, expected", [
    ("https://api.twitch.tv/helix/users", True),
    (" HTTPS://API.TWITCH.TV/helix ", True),
    ("http://api.twitch.tv/helix", False),
    ("https://evil.example/api.twitch.tv", False),
    ("not a url", False),
])
def test_is_twitch_helix_url(url, expected):
    assert we._is_twitch_helix_url(url) is expected


def test_is_twitch_helix_url_parse_error(monkeypatch):
    monkeypatch.setattr(we, "urlparse", mock.Mock(side_effect=ValueError("bad")))
    assert we._is_twitch_helix_url("https://api.twitch.tv") is False


# --------------------------------------------------------------------------- assets resolve


@pytest.fixture
def assets_root(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "a.png").write_bytes(b"x")
    monkeypatch.setattr(we, "get_assets_path", lambda: str(root))
    monkeypatch.setattr(we, "_ASSETS_RESOLVE_CACHE", {"root": None, "real": None, "files": {}})
    return root


def test_resolved_assets_file_hits_cache_and_rejects_escape(assets_root, tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    real = str((assets_root / "a.png").resolve())
    assert we._resolved_assets_file("a.png") == real
    assert we._ASSETS_RESOLVE_CACHE["files"]["a.png"] == real
    assert we._resolved_assets_file("a.png") == real  # cached path
    assert we._resolved_assets_file("../secret.txt") is None
    assert we._resolved_assets_file("") is None
    assert we._resolved_assets_file("a\x00b") is None


def test_resolved_assets_file_missing_drops_stale_cache(assets_root):
    we._resolved_assets_file("a.png")
    (assets_root / "a.png").unlink()
    assert we._resolved_assets_file("a.png") is None
    assert "a.png" not in we._ASSETS_RESOLVE_CACHE["files"]


def test_resolved_assets_file_cache_clears_when_large(assets_root):
    we._ASSETS_RESOLVE_CACHE["root"] = str(assets_root)
    we._ASSETS_RESOLVE_CACHE["real"] = str(assets_root.resolve())
    we._ASSETS_RESOLVE_CACHE["files"] = {f"k{i}": "/nope" for i in range(600)}
    assert we._resolved_assets_file("a.png")
    assert list(we._ASSETS_RESOLVE_CACHE["files"]) == ["a.png"]


def test_resolved_assets_file_commonpath_error(assets_root, monkeypatch):
    monkeypatch.setattr(we.os.path, "commonpath", mock.Mock(side_effect=ValueError("drives")))
    assert we._resolved_assets_file("a.png") is None


@pytest.mark.parametrize("event, data, expected", [
    ("deaths_increment", {"text": "x"}, False),
    ("counter_set", {"value": 1}, False),
    ("set_text", {"action": "increment", "text": "x"}, False),
    ("set_text", {"text": "x"}, True),
    ("slider", {"value": 3}, True),
    ("button", {"action": "go"}, False),
    ("set_text", "raw", False),
    (None, None, False),
])
def test_template_control_should_coalesce(event, data, expected):
    assert we._template_control_should_coalesce(event, data) is expected


# --------------------------------------------------------------------------- _SafeEmitQueue


def test_safe_emit_queue_fifo_and_empty():
    q = we._SafeEmitQueue(3)
    q.put(("a",), on_full=lambda: "x")
    q.put(("b",), on_full=lambda: "x")
    assert q.qsize() == 2
    assert q.get_nowait() == ("a",)
    assert q.get_nowait() == ("b",)
    with pytest.raises(queue.Empty):
        q.get_nowait()


def test_safe_emit_queue_latest_wins_keeps_position():
    q = we._SafeEmitQueue(5)
    q.put(("hook", 1), key=("hook", None), on_full=lambda: "x")
    q.put(("other",), on_full=lambda: "x")
    assert q.put(("hook", 2), key=("hook", None), on_full=lambda: "x") is None
    assert q.qsize() == 2
    assert q.get_nowait() == ("hook", 2)
    assert q.get_nowait() == ("other",)
    # Key slot freed after pop -> a new one queues again
    q.put(("hook", 3), key=("hook", None), on_full=lambda: "x")
    assert q.qsize() == 1


def test_safe_emit_queue_overflow_and_drop_oldest():
    q = we._SafeEmitQueue(1)
    q.put((1,), on_full=lambda: we._EMIT_FULL_OVERFLOW)
    assert q.put((2,), on_full=lambda: we._EMIT_FULL_OVERFLOW) is None
    assert q.qsize() == 2
    dropped = q.put((3,), on_full=lambda: we._EMIT_FULL_DROP_OLDEST)
    assert dropped == (1,)
    assert [q.get_nowait(), q.get_nowait()] == [(2,), (3,)]


def test_safe_emit_queue_wait_then_room(monkeypatch):
    q = we._SafeEmitQueue(1)
    q.put((1,), on_full=lambda: "x")
    modes = iter([we._EMIT_FULL_WAIT, we._EMIT_FULL_OVERFLOW])
    waits = []

    def fake_wait(timeout):
        waits.append(timeout)

    monkeypatch.setattr(q._not_full, "wait", fake_wait)
    q.put((2,), on_full=lambda: next(modes))
    assert waits == [we._SAFE_EMIT_PUT_WAIT_SLICE_SEC]
    assert q.qsize() == 2


# --------------------------------------------------------------------------- alert playback flags


def test_notify_alert_processor_wake_and_playing(monkeypatch):
    import modules.alert_processor as ap

    wake, playing = mock.Mock(), mock.Mock()
    monkeypatch.setattr(ap, "wake_alert_queue", wake)
    monkeypatch.setattr(ap, "notify_alert_playing", playing)
    we._notify_alert_processor()
    wake.assert_called_once_with()
    we._notify_alert_processor(playing=True)
    playing.assert_called_once_with(True)
    monkeypatch.setattr(ap, "wake_alert_queue", mock.Mock(side_effect=RuntimeError))
    we._notify_alert_processor()  # swallowed


def test_set_alert_playing_updates_flag_and_notifies(monkeypatch, alert_globals):
    import modules.uiwindows.activity_feed as af

    notify = mock.Mock()
    breath = mock.Mock()
    bcast = mock.Mock()
    monkeypatch.setattr(we, "_notify_alert_processor", notify)
    monkeypatch.setattr(af, "sync_pause_breath", breath)
    monkeypatch.setattr(we, "broadcast_alert_queue_state", bcast)
    we.set_alert_playing(1)
    assert we.ALERT_PLAYING is True
    notify.assert_called_once_with(playing=True)
    breath.assert_called_once_with(True)
    bcast.assert_called_once()
    monkeypatch.setattr(af, "sync_pause_breath", mock.Mock(side_effect=RuntimeError))
    we.set_alert_playing(0)
    assert we.ALERT_PLAYING is False


def test_set_alerts_paused(monkeypatch, alert_globals):
    notify, sync, bcast = mock.Mock(), mock.Mock(), mock.Mock()
    monkeypatch.setattr(we, "_notify_alert_processor", notify)
    monkeypatch.setattr(we, "_sync_pause_button_state", sync)
    monkeypatch.setattr(we, "broadcast_alert_queue_state", bcast)
    we.set_alerts_paused("yes")
    assert we.ALERTS_PAUSED is True
    notify.assert_called_once_with()
    sync.assert_called_once()
    bcast.assert_called_once()


@pytest.mark.parametrize("fn_name, target", [
    ("_sync_pause_button_state", "sync_pause_button_state"),
    ("_sync_mute_button_state", "sync_mute_button_state"),
])
def test_sync_button_state_runs_on_ui_loop_and_falls_back(monkeypatch, fn_name, target):
    import modules.ui_timer as ui_timer
    import modules.uiwindows.activity_feed as af

    sync = mock.Mock()
    monkeypatch.setattr(af, target, sync)
    scheduled = []
    monkeypatch.setattr(ui_timer, "run_on_ui_loop", lambda fn: scheduled.append(fn))
    getattr(we, fn_name)()
    assert sync.call_count == 0 and len(scheduled) == 1
    scheduled[0]()
    assert sync.call_count == 1
    # UI loop unavailable -> runs inline
    monkeypatch.setattr(ui_timer, "run_on_ui_loop", mock.Mock(side_effect=RuntimeError("no loop")))
    getattr(we, fn_name)()
    assert sync.call_count == 2
    # Sync itself failing is logged, not raised
    monkeypatch.setattr(af, target, mock.Mock(side_effect=RuntimeError("gone")))
    getattr(we, fn_name)()


def test_assign_next_alert_queue_seq_is_monotonic(alert_globals):
    a = we.assign_next_alert_queue_seq()
    b = we.assign_next_alert_queue_seq()
    assert b == a + 1
    assert we.EXPECTED_ALERT_COMPLETE_SEQ == b


def test_begin_and_abort_alert_playback(monkeypatch, alert_globals):
    import modules.alert_playback_session as aps

    begin = mock.Mock(return_value="session")
    abort = mock.Mock()
    monkeypatch.setattr(aps, "begin_session", begin)
    monkeypatch.setattr(aps, "abort_session", abort)
    setp = mock.Mock()
    monkeypatch.setattr(we, "set_alert_playing", setp)
    assert we.begin_alert_playback_session("7", ["/alerts"], 5.0, 1.0) == "session"
    assert we.EXPECTED_ALERT_COMPLETE_SEQ == 7
    begin.assert_called_once_with("7", ["/alerts"], 5.0, 1.0)
    we.abort_alert_playback("stop")
    abort.assert_called_once_with("stop")
    assert we.EXPECTED_ALERT_COMPLETE_SEQ is None
    setp.assert_called_once_with(False)


def test_apply_playback_session_finished(monkeypatch, alert_globals):
    setp = mock.Mock()
    monkeypatch.setattr(we, "set_alert_playing", setp)
    we.EXPECTED_ALERT_COMPLETE_SEQ = 3
    we._apply_playback_session_finished(False)
    assert we.EXPECTED_ALERT_COMPLETE_SEQ == 3
    setp.assert_not_called()
    we._apply_playback_session_finished(True)
    assert we.EXPECTED_ALERT_COMPLETE_SEQ is None
    setp.assert_called_once_with(False)


# --------------------------------------------------------------------------- alert queue state


def test_queue_entry():
    a = types.SimpleNamespace(alert_id=5, alert_type="follow", username=None, is_replay=True)
    assert we._queue_entry(a) == {"alert_id": "5", "type": "follow", "username": "", "is_replay": True}
    assert we._queue_entry(object()) == {"alert_id": "", "type": "", "username": ""}


class _Session:
    def __init__(self, seq, finished=False, force_ok=True):
        self.seq = seq
        self.finished = finished
        self.force_ok = force_ok
        self.forced = []

    def force_finish(self, reason):
        self.forced.append(reason)
        return self.force_ok


@pytest.fixture
def playback(monkeypatch, alert_globals):
    """Fake alert_processor / alert_playback_session state."""
    import modules.alert_playback_session as aps
    import modules.alert_processor as ap

    state = types.SimpleNamespace(info=None, session=None, removed={}, snapshot=[])
    monkeypatch.setattr(ap, "current_alert_info", lambda: state.info)
    monkeypatch.setattr(aps, "get_session", lambda: state.session)
    monkeypatch.setattr(ap, "remove_queued_alert_by_id", lambda aid: state.removed.get(aid))
    monkeypatch.setattr(ap, "play_order_snapshot", lambda: state.snapshot)
    return state


def test_playing_entry_states(playback):
    we.ALERT_PLAYING = False
    assert we._playing_entry() is None
    we.ALERT_PLAYING = True
    assert we._playing_entry() is None  # no info
    playback.info = {"alert_id": "a1", "type": "sub", "username": "u", "started_at_ms": 1,
                     "duration_ms": 2, "queue_seq": 4, "is_replay": True}
    assert we._playing_entry() is None  # no session
    playback.session = _Session(3)
    assert we._playing_entry() is None  # seq mismatch
    playback.session = _Session(4, finished=True)
    assert we._playing_entry() is None
    playback.session = _Session(4)
    assert we._playing_entry() == {"alert_id": "a1", "type": "sub", "username": "u",
                                   "started_at_ms": 1, "duration_ms": 2, "queue_seq": 4,
                                   "is_replay": True}
    playback.info = {"queue_seq": 4}
    assert we._playing_entry() == {"alert_id": "", "type": "", "username": "", "started_at_ms": None,
                                   "duration_ms": None, "queue_seq": 4}


def test_playing_entry_import_failure(monkeypatch, alert_globals):
    we.ALERT_PLAYING = True
    with mock.patch.dict(sys.modules, {"modules.alert_processor": None}):
        assert we._playing_entry() is None


def test_alert_queue_state_body_and_get(playback):
    playback.snapshot = [types.SimpleNamespace(alert_id="q1", alert_type="raid", username="z")]
    we.ALERTS_PAUSED = True
    we.ALERTS_MUTED = False
    we.ALERT_PLAYING = False
    body = we._alert_queue_state_body()
    assert body == {"paused": True, "muted": False, "playing": None,
                    "queue": [{"alert_id": "q1", "type": "raid", "username": "z"}]}
    we._queue_state_revision = 9
    state = we.get_alert_queue_state()
    assert state["revision"] == 9 and isinstance(state["server_now_ms"], int)


def test_alert_queue_state_body_snapshot_error(monkeypatch, playback):
    import modules.alert_processor as ap

    monkeypatch.setattr(ap, "play_order_snapshot", mock.Mock(side_effect=RuntimeError))
    assert we._alert_queue_state_body()["queue"] == []


def test_broadcast_alert_queue_state_dedupes_and_forces(playback):
    eng = FakeEngine()
    we.web_engine_instance = eng
    we._queue_state_last_sig = None
    we._queue_state_revision = 0
    first = we.broadcast_alert_queue_state()
    assert first["revision"] == 1 and eng.emits[-1] == ("alert_queue_state", first)
    assert we.broadcast_alert_queue_state() is None  # unchanged
    forced = we.broadcast_alert_queue_state(force=True)
    assert forced["revision"] == 1  # same state keeps revision
    assert len(eng.emits) == 2
    we.ALERTS_PAUSED = not we.ALERTS_PAUSED
    assert we.broadcast_alert_queue_state()["revision"] == 2


def test_broadcast_alert_queue_state_no_engine_and_error(monkeypatch, playback):
    we.web_engine_instance = None
    we._queue_state_last_sig = None
    assert we.broadcast_alert_queue_state() is not None
    we.web_engine_instance = FakeEngine(raises=RuntimeError("down"))
    assert we.broadcast_alert_queue_state(force=True) is None


def test_emit_alert_skip(alert_globals):
    we.web_engine_instance = None
    assert we.emit_alert_skip(1) is False
    eng = FakeEngine()
    we.web_engine_instance = eng
    assert we.emit_alert_skip("3", alert_id=9, source="deck") is True
    assert eng.emits[-1] == ("alerts_skip_alert", {"source": "deck", "queue_seq": 3, "alert_id": "9"})
    assert we.emit_alert_skip(None) is True
    assert eng.emits[-1] == ("alerts_skip_alert", {"source": ""})
    we.web_engine_instance = FakeEngine(raises=RuntimeError("x"))
    assert we.emit_alert_skip(1) is False


def test_skip_current_alert_paths(monkeypatch, playback):
    emit = mock.Mock()
    finished = mock.Mock()
    monkeypatch.setattr(we, "emit_alert_skip", emit)
    monkeypatch.setattr(we, "_apply_playback_session_finished", finished)
    we._last_skip_at = 0.0

    # nothing playing
    assert we.skip_current_alert()["reason"] == "not_playing"
    # queued id removed
    playback.removed = {"q9": object()}
    assert we.skip_current_alert("q9", source="t") == {
        "ok": True, "skipped_alert_id": "q9", "reason": "removed_from_queue"}
    # stale id
    assert we.skip_current_alert("zz")["reason"] == "not_playing"

    playback.info = {"alert_id": "a1", "queue_seq": 5}
    session = _Session(5)
    playback.session = session
    monkeypatch.setattr(we, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    res = we.skip_current_alert("current", source="deck")
    assert res == {"ok": True, "skipped_alert_id": "a1", "reason": "skipped"}
    assert session.forced == ["skip"]
    emit.assert_called_once_with(5, "a1", "deck")
    finished.assert_called_once_with(True)
    # id-less press right after -> double press
    assert we.skip_current_alert()["reason"] == "double_press"
    # explicit id bypasses the guard
    assert we.skip_current_alert("a1")["reason"] == "skipped"
    # force_finish refused
    playback.session = _Session(5, force_ok=False)
    monkeypatch.setattr(we, "time", types.SimpleNamespace(monotonic=lambda: 200.0))
    assert we.skip_current_alert()["reason"] == "not_playing"


def test_skip_alert_from_payload(monkeypatch, playback):
    calls = []
    monkeypatch.setattr(we, "skip_current_alert",
                        lambda aid, source="": calls.append((aid, source)) or {"ok": True, "reason": "skipped"})
    assert we.skip_alert_from_payload({"alert_id": "x"}, "s")["success"] is True
    assert calls[-1] == ("x", "s")
    we.skip_alert_from_payload({"alert_id": "x", "type": "current"})
    assert calls[-1] == (None, "")
    # queue_seq resolving to the playing alert
    playback.info = {"alert_id": "p1", "queue_seq": 4}
    we.skip_alert_from_payload({"queue_seq": "4"})
    assert calls[-1] == ("p1", "")
    # queue_seq not matching
    n = len(calls)
    res = we.skip_alert_from_payload({"queue_seq": 99})
    assert res == {"ok": False, "skipped_alert_id": None, "reason": "not_playing"} and len(calls) == n
    res = we.skip_alert_from_payload({"queue_seq": "abc"})
    assert res["reason"] == "not_playing"
    playback.info = None
    assert we.skip_alert_from_payload({"queue_seq": 1})["reason"] == "not_playing"
    # non-dict payload
    we.skip_alert_from_payload("junk")
    assert calls[-1] == (None, "")


@pytest.mark.parametrize("tn, an, ev, expected", [
    ("alerts", "skip", "", True),
    ("/Alerts/", "Skip Alert", "", True),
    ("other", "skip_alert", "", True),
    ("other", "", "alerts_skip_alert", True),
    ("other", "", "SKIP_ALERT", True),
    ("other", "skip", "", False),
    ("alerts", "pause", "", False),
    (None, None, None, False),
])
def test_is_alert_skip_template_action(tn, an, ev, expected):
    assert we.is_alert_skip_template_action(tn, an, ev) is expected


# --------------------------------------------------------------------------- one-shot gates


def test_obs_refresh_and_dock_gates(monkeypatch):
    monkeypatch.setattr(we, "_obs_browser_refresh_done_this_process", False)
    monkeypatch.setattr(we, "_obs_dock_reload_done_this_process", False)
    assert we._obs_browser_refresh_already_done() is False
    assert we._claim_obs_browser_refresh_this_process() is True
    assert we._claim_obs_browser_refresh_this_process() is False
    assert we._obs_browser_refresh_already_done() is True
    we._reset_obs_browser_refresh_gate_for_tests()
    assert we._obs_browser_refresh_already_done() is False

    assert we._obs_dock_reload_already_done() is False
    assert we._claim_obs_dock_reload_this_process() is True
    assert we._claim_obs_dock_reload_this_process() is False
    assert we._obs_dock_reload_already_done() is True
    we._reset_obs_dock_reload_gate_for_tests()
    assert we._obs_dock_reload_already_done() is False


# --------------------------------------------------------------------------- health helpers


def test_bottleneck_debug_and_print(monkeypatch, capsys):
    monkeypatch.delenv("MYCELIAN_BOTTLENECK_DEBUG", raising=False)
    monkeypatch.setattr(we, "_BOTTLENECK_DEBUG_PRINT", False)
    assert we._bottleneck_debug_enabled() is False
    we._bottleneck_print("quiet")
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("MYCELIAN_BOTTLENECK_DEBUG", "Yes")
    assert we._bottleneck_debug_enabled() is True
    we._bottleneck_print("loud")
    assert capsys.readouterr().out == "[BOTTLENECK] loud\n"
    monkeypatch.setenv("MYCELIAN_BOTTLENECK_DEBUG", "0")
    monkeypatch.setattr(we, "_BOTTLENECK_DEBUG_PRINT", True)
    assert we._bottleneck_debug_enabled() is True


@pytest.mark.parametrize("samples, pct, expected", [
    ([], 0.95, 0.0),
    ([5], 0.95, 5.0),
    ([3, 1, 2, 4], 0.5, 2.0),
    ([3, 1, 2, 4], 1.0, 4.0),
    ([3, 1, 2, 4], -1, 1.0),
    ([3, 1, 2, 4], 5, 4.0),
])
def test_percentile_value(samples, pct, expected):
    assert we._percentile_value(samples, pct) == expected


def test_count_recent_timestamps_prunes_old(monkeypatch):
    monkeypatch.setattr(we, "time", types.SimpleNamespace(time=lambda: 100.0))
    bucket = deque([10.0, 50.0, 95.0, 99.0])
    assert we._count_recent_timestamps(bucket, 10.0) == 2
    assert list(bucket) == [95.0, 99.0]
    assert we._count_recent_timestamps(deque(), 5) == 0


def test_get_webengine_health_without_instance(monkeypatch):
    import modules.shutdown as shutdown

    monkeypatch.setattr(we, "web_engine_instance", None)
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: False)
    h = we.get_webengine_health()
    assert h["state"] == we.WEBENGINE_STATE_CRASHED and h["is_running"] is False
    assert h["bottleneck_reasons"] == [] and h["detail"] == ""
    monkeypatch.setattr(shutdown, "is_shutdown_in_progress", lambda: True)
    assert we.get_webengine_health()["state"] == we.WEBENGINE_STATE_STOPPED


def test_get_webengine_health_delegates_to_instance(monkeypatch):
    inst = types.SimpleNamespace(get_health=lambda: {"state": "Running"})
    monkeypatch.setattr(we, "web_engine_instance", inst)
    assert we.get_webengine_health() == {"state": "Running"}


def test_evaluate_bottleneck_healthy_and_empty():
    assert we.evaluate_bottleneck(None) == {
        "bottlenecked": False, "reasons": [], "hard_reasons": [], "soft_hits": []}


def test_evaluate_bottleneck_every_hard_reason():
    m = {
        "heartbeat_stale_sec": 50, "hub_tick_drift_ms": 4000, "http_p95_ms": 800,
        "slow_requests_recent": 5, "template_control_oldest_age_ms": 600,
        "template_control_queue_depth": 40, "twitch_api_oldest_age_ms": 16000,
        "twitch_api_queue_depth": 8, "template_control_drops_60s": 1, "twitch_api_drops_60s": 2,
        "template_control_worker_alive": False,
    }
    res = we.evaluate_bottleneck(m, is_running=True, template_control_emit_worker_started=True)
    assert res["bottlenecked"] is True
    assert res["hard_reasons"] == [
        "heartbeat_stale_sec=50", "hub_tick_drift_ms=4000", "http_p95_ms=800", "slow_requests=5/5",
        "template_control_oldest_age_ms=600", "template_control_queue_depth=40/40",
        "twitch_api_oldest_age_ms=16000/depth=8/8", "template_control_drops_60s=1",
        "twitch_api_drops_60s=2", "template_control_worker_alive=false",
    ]


def test_evaluate_bottleneck_twitch_age_alone_is_not_overload():
    res = we.evaluate_bottleneck({"twitch_api_oldest_age_ms": 99999, "twitch_api_queue_depth": 2})
    assert res["bottlenecked"] is False


def test_evaluate_bottleneck_soft_hits_need_two():
    one = we.evaluate_bottleneck({"net_connects_30s": 11})
    assert one["bottlenecked"] is False and one["soft_hits"] == ["net_connects_30s=11"]
    assert one["reasons"] == []
    two = we.evaluate_bottleneck({"net_connects_30s": 11, "socket_emit_errors_60s": 1,
                                  "template_control_queue_depth": 15, "http_p95_ms": 500})
    assert two["bottlenecked"] is True
    assert two["hard_reasons"] == []
    assert two["reasons"] == ["http_p95_ms=500", "template_control_queue_depth=15",
                              "net_connects_30s=11", "socket_emit_errors_60s=1"]

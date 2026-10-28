# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/psn_service.py: init, settings changes, status labels, broadcasting, thread control
and helpers. The update loop itself is in test_psn_service_part2.py."""

from __future__ import annotations

import sys
import threading
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules  # noqa: E402
from modules import psn_service as ps  # noqa: E402
from modules.psn_data import PSNData, PSNGameMismatch  # noqa: E402


# --------------------------------------------------------------------------- shared fakes


class FakeState:
    def __init__(self, settings=None, live=None):
        self.settings = settings
        self.live = live
        self.set_calls = []

    def get_psn_settings_data(self):
        return self.settings

    def get_live_psn_data(self):
        return self.live

    def set_live_psn_data(self, data):
        self.set_calls.append(data)


class FakeEmitter:
    def __init__(self, fail=False):
        self.emits = []
        self.fail = fail

    def safe_emit(self, event, data):
        if self.fail:
            raise RuntimeError("emit broke")
        self.emits.append((event, data))


class FakeClient:
    def __init__(self, npsso="tok", username=None, authenticated=True, connect_result=True):
        self.psn_data = PSNData(npsso_code=npsso)
        self.psn_username = username
        self.authenticated = authenticated
        self.connect_result = connect_result
        self.connects = 0
        self.cred_updates = []

    def connect(self):
        self.connects += 1
        if self.connect_result:
            self.authenticated = True
        return self.connect_result

    def is_connected(self):
        return self.authenticated

    def update_credentials(self, npsso, username):
        self.cred_updates.append((npsso, username))
        self.psn_data.npsso_code = npsso
        self.psn_username = username
        self.authenticated = False


@pytest.fixture
def env(monkeypatch):
    state = FakeState()
    monkeypatch.setattr(ps, "state_manager", state)
    monkeypatch.setattr(ps, "psn_client_instance", None)
    monkeypatch.setattr(ps, "psn_update_thread", None)
    monkeypatch.setattr(ps, "stop_psn_thread_event", threading.Event())
    monkeypatch.setattr(ps, "_mismatch_notification_times", {})
    monkeypatch.setattr(ps, "_npsso_expired_notified_for", None)
    emitter = FakeEmitter()
    web = types.ModuleType("modules.web_engine")
    web.web_engine_instance = emitter
    monkeypatch.setitem(sys.modules, "modules.web_engine", web)
    monkeypatch.setattr(modules, "web_engine", web, raising=False)
    started = []
    monkeypatch.setattr(ps, "start_psn_data_updater_thread", lambda: started.append(1))
    stopped = []
    monkeypatch.setattr(ps, "stop_psn_data_updater_thread", lambda **kw: stopped.append(1))
    return SimpleNamespace(state=state, emitter=emitter, web=web, started=started, stopped=stopped, mp=monkeypatch)


def settings(npsso="tok", username=None):
    return SimpleNamespace(npsso_code=npsso, psn_username=username)


# --------------------------------------------------------------------------- cache freshness


def _iso(delta_sec):
    return (datetime.now() - timedelta(seconds=delta_sec)).isoformat()


@pytest.mark.parametrize(
    "doc,fresh",
    [
        (None, False),
        ({}, False),
        ({"trophy_groups": [], "has_trophy_groups": True, "trophy_groups_updated_at": _iso(10)}, False),
        ({"trophy_groups": [], "trophy_groups_updated_at": _iso(10)}, False),
        ({"trophy_groups": [], "has_trophy_groups": False, "trophy_groups_updated_at": _iso(10)}, True),
        ({"trophy_groups": [{"id": 1}]}, False),
        ({"trophy_groups": [{"id": 1}], "trophy_groups_updated_at": "garbage"}, False),
        ({"trophy_groups": [{"id": 1}], "trophy_groups_updated_at": _iso(10)}, True),
        ({"trophy_groups": [{"id": 1}], "trophy_groups_updated_at": _iso(ps.TROPHY_GROUPS_CACHE_TTL_SEC + 60)}, False),
    ],
)
def test_trophy_groups_cache_is_fresh(doc, fresh):
    assert ps._trophy_groups_cache_is_fresh(doc) is fresh


def test_backcompat_sleep_aliases():
    assert ps.SLEEP_NO_GAME == ps.SLEEP_CACHE_HIT == ps.SLEEP_CACHE_MISS == ps.SLEEP_PRESENCE


# --------------------------------------------------------------------------- npsso expired notice


def test_npsso_expired_notice_once_per_code(env, monkeypatch):
    from modules import notification_engine

    sent = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: sent.append((msg, kw)))
    ps.notify_psn_npsso_expired("abc")
    ps.notify_psn_npsso_expired("abc")
    assert len(sent) == 1
    msg, kw = sent[0]
    assert "sign in again" in msg
    assert kw["dedupe_key"] == "psn:npsso_expired" and kw["source"] == "psn" and kw["type"] == "warning"
    ps.notify_psn_npsso_expired("other")
    assert len(sent) == 2
    ps.clear_psn_npsso_expired_notice()
    assert ps._npsso_expired_notified_for is None
    ps.notify_psn_npsso_expired("other")
    assert len(sent) == 3
    ps.clear_psn_npsso_expired_notice()
    ps.notify_psn_npsso_expired()
    assert ps._npsso_expired_notified_for == hash("")


def test_npsso_expired_notice_failure_swallowed(env, monkeypatch):
    from modules import notification_engine

    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ps.notify_psn_npsso_expired("abc")
    assert ps._npsso_expired_notified_for == hash("abc")


# --------------------------------------------------------------------------- init


def test_finalize_without_client_noop(env):
    ps._finalize_psn_client_state(None)
    assert env.state.set_calls == [] and env.started == []


@pytest.mark.parametrize("username", [None, "pal"])
def test_finalize_authenticated_starts_thread(env, username):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    ps._finalize_psn_client_state(username)
    assert env.state.set_calls == [c.psn_data] and env.started == [1]


def test_finalize_unauthenticated_does_not_start(env):
    c = FakeClient(authenticated=False)
    env.mp.setattr(ps, "psn_client_instance", c)
    ps._finalize_psn_client_state(None)
    assert env.state.set_calls == [c.psn_data] and env.started == []


def test_init_impl_state_manager_missing_method(env, monkeypatch):
    monkeypatch.setattr(ps, "state_manager", object())
    ps._initialize_psn_module_impl()
    assert ps.psn_client_instance is None


def test_init_impl_no_code_sets_not_connected(env):
    env.state.settings = settings(npsso="")
    ps._initialize_psn_module_impl()
    assert ps.psn_client_instance is None
    assert env.state.set_calls[0].connection_status == "Not Connected"


def test_init_impl_no_settings(env):
    env.state.settings = None
    ps._initialize_psn_module_impl()
    assert env.state.set_calls[0].connection_status == "Not Connected"


def test_init_impl_creates_and_connects(env, monkeypatch):
    created = []

    def factory(npsso_code, psn_username):
        c = FakeClient(npsso_code, psn_username)
        created.append(c)
        return c

    monkeypatch.setattr(ps, "PSNClient", factory)
    env.state.settings = settings("tok", "pal")
    ps._initialize_psn_module_impl()
    assert ps.psn_client_instance is created[0]
    assert created[0].connects == 1 and created[0].psn_username == "pal"
    assert env.started == [1]


def test_init_impl_existing_client_retries_connect(env):
    c = FakeClient(authenticated=False)
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("tok")
    ps._initialize_psn_module_impl()
    assert c.connects == 1 and env.started == [1]


def test_init_impl_existing_authenticated_client_no_reconnect(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("tok")
    ps._initialize_psn_module_impl()
    assert c.connects == 0 and env.started == [1]


def test_init_impl_existing_client_without_code_no_reconnect(env):
    c = FakeClient(authenticated=False)
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("")
    ps._initialize_psn_module_impl()
    assert c.connects == 0 and env.started == []


def test_init_impl_exception_swallowed(env, monkeypatch):
    def boom():
        raise RuntimeError("settings broken")

    env.state.get_psn_settings_data = boom
    ps._initialize_psn_module_impl()  # no raise
    assert ps.psn_client_instance is None


def test_initialize_psn_module_starts_daemon_thread(env, monkeypatch):
    made = []

    class T:
        def __init__(self, target, daemon, name):
            made.append((target, daemon, name))

        def start(self):
            made.append("started")

    monkeypatch.setattr(ps, "threading", SimpleNamespace(Thread=T))
    ps.initialize_psn_module()
    assert made == [(ps._initialize_psn_module_impl, True, "PSNInit"), "started"]


# --------------------------------------------------------------------------- clear / payload / broadcast


def test_clear_game_data_if_needed(env):
    ps._clear_game_data_if_needed()  # no client
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    c.psn_data.current_game_name = "G"
    c.psn_data.current_game_np_comm_id = "N"
    c.psn_data.current_game_trophies = {"a": 1}
    c.psn_data.current_game_trophies_all = {"a": 1}
    c.psn_data.current_game_progress = 5
    c.psn_data.current_game_mismatch = PSNGameMismatch("G", "T", "PS5", "now")
    ps._clear_game_data_if_needed()
    d = c.psn_data
    assert d.current_game_name is None and d.current_game_np_comm_id is None
    assert d.current_game_trophies == {} and d.current_game_trophies_all == {}
    assert d.current_game_progress is None and d.current_game_mismatch is None


def test_clear_game_data_only_art_url_triggers(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    c.psn_data.current_game_art_url = "x"
    c.psn_data.current_game_progress = 3
    ps._clear_game_data_if_needed()
    assert c.psn_data.current_game_art_url is None and c.psn_data.current_game_progress is None


def test_clear_game_data_nothing_to_clear(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    c.psn_data.current_game_progress = 3
    ps._clear_game_data_if_needed()
    assert c.psn_data.current_game_progress == 3


def test_public_payload_strips_npsso():
    p = ps._public_psn_payload(PSNData(npsso_code="secret", online_id="me"))
    assert "npsso_code" not in p and p["online_id"] == "me"


def test_update_and_broadcast_without_client(env):
    ps._update_and_broadcast()
    assert env.state.set_calls == [] and env.emitter.emits == []


def test_update_and_broadcast_emits_payload(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    ps._update_and_broadcast()
    assert env.state.set_calls == [c.psn_data]
    (event, data), = env.emitter.emits
    assert event == "psn_data_update" and "npsso_code" not in data


def test_update_and_broadcast_no_web_engine(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    env.web.web_engine_instance = None
    ps._update_and_broadcast()
    assert env.state.set_calls == [c.psn_data]


def test_update_and_broadcast_emit_error_swallowed(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    env.emitter.fail = True
    ps._update_and_broadcast()
    assert env.state.set_calls == [c.psn_data]


def test_mismatch_present_still_broadcasts_data_update(env):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    c.psn_data.current_game_mismatch = PSNGameMismatch("G", "T", "PS5", "now")
    ps._update_and_broadcast()
    assert env.emitter.emits[0][0] == "psn_data_update"
    assert env.state.set_calls == [c.psn_data]


def test_mismatch_notification_throttled_and_swept(env, monkeypatch):
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    now = {"t": 10_000.0}
    monkeypatch.setattr(ps, "time", SimpleNamespace(time=lambda: now["t"]))
    ps._mismatch_notification_times["Old Game"] = 1.0  # long expired -> swept
    m = PSNGameMismatch("G", "T", "PS5", "now")
    c.psn_data.current_game_mismatch = m
    ps._update_and_broadcast()
    events = [e for e, _ in env.emitter.emits]
    assert events == ["psn_data_update", "psn_game_mismatch"]
    assert env.emitter.emits[1][1]["presence_name"] == "G"
    assert m.notified is True
    assert ps._mismatch_notification_times == {"G": 10_000.0}
    # notified mismatch: no second notice
    ps._update_and_broadcast()
    assert [e for e, _ in env.emitter.emits].count("psn_game_mismatch") == 1
    # fresh mismatch object for same game inside cooldown -> skipped
    m2 = PSNGameMismatch("G", "T", "PS5", "now")
    c.psn_data.current_game_mismatch = m2
    now["t"] += 10
    ps._update_and_broadcast()
    assert m2.notified is False
    # after cooldown -> sent
    now["t"] += ps.MISMATCH_NOTIFICATION_COOLDOWN
    ps._update_and_broadcast()
    assert m2.notified is True
    assert [e for e, _ in env.emitter.emits].count("psn_game_mismatch") == 2


# --------------------------------------------------------------------------- thread control (real functions)


@pytest.fixture
def real_threads(monkeypatch):
    monkeypatch.setattr(ps, "psn_client_instance", None)
    monkeypatch.setattr(ps, "psn_update_thread", None)
    ev = threading.Event()
    monkeypatch.setattr(ps, "stop_psn_thread_event", ev)
    ran = []

    def fake_loop():
        ran.append(threading.current_thread().name)
        ev.wait(2)

    monkeypatch.setattr(ps, "psn_data_update_loop", fake_loop)
    yield SimpleNamespace(ev=ev, ran=ran)
    ev.set()
    t = ps.psn_update_thread
    if t is not None and hasattr(t, "join"):
        t.join(2)


def test_start_thread_guards(real_threads, monkeypatch):
    ps.start_psn_data_updater_thread()  # no client
    assert ps.psn_update_thread is None
    monkeypatch.setattr(ps, "psn_client_instance", FakeClient(authenticated=False))
    ps.start_psn_data_updater_thread()
    assert ps.psn_update_thread is None
    monkeypatch.setattr(ps, "psn_client_instance", FakeClient(npsso=None))
    ps.start_psn_data_updater_thread()
    assert ps.psn_update_thread is None


def test_start_and_stop_thread(real_threads, monkeypatch):
    monkeypatch.setattr(ps, "psn_client_instance", FakeClient())
    real_threads.ev.set()  # start() must clear it
    ps.start_psn_data_updater_thread()
    t = ps.psn_update_thread
    assert t is not None and t.name == "PSNUpdateThread" and t.daemon
    assert not real_threads.ev.is_set() or real_threads.ran
    ps.start_psn_data_updater_thread()  # already running
    assert ps.psn_update_thread is t
    ps.stop_psn_data_updater_thread(join_timeout=2)
    assert ps.psn_update_thread is None and not t.is_alive()
    assert real_threads.ran == ["PSNUpdateThread"]
    ps.stop_psn_data_updater_thread()  # not running


def test_stop_thread_timeout_keeps_reference(real_threads, monkeypatch):
    class Stuck:
        def __init__(self):
            self.joins = []

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.joins.append(timeout)

    s = Stuck()
    monkeypatch.setattr(ps, "psn_update_thread", s)
    ps.stop_psn_data_updater_thread(join_timeout=0.01)
    assert s.joins == [0.01] and ps.psn_update_thread is s and real_threads.ev.is_set()
    monkeypatch.setattr(ps, "psn_update_thread", None)


# --------------------------------------------------------------------------- settings change


def test_settings_change_nothing_changed_and_thread_running(env, monkeypatch):
    c = FakeClient(npsso="tok", username="pal")
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("tok", "pal")
    monkeypatch.setattr(ps, "psn_update_thread", SimpleNamespace(is_alive=lambda: True))
    ps.handle_psn_settings_change()
    assert env.started == [] and env.stopped == []


def test_settings_change_nothing_changed_restarts_dead_thread(env, monkeypatch):
    c = FakeClient(npsso="tok")
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("tok", None)
    monkeypatch.setattr(ps, "psn_update_thread", SimpleNamespace(is_alive=lambda: False))
    ps.handle_psn_settings_change()
    assert env.started == [1]


def test_settings_change_no_client_no_code(env):
    env.state.settings = settings("", None)
    ps.handle_psn_settings_change()
    assert env.started == [] and env.state.set_calls == []


def test_settings_change_no_settings_no_client(env):
    env.state.settings = None
    ps.handle_psn_settings_change()
    assert env.stopped == []


def test_settings_change_creates_client(env, monkeypatch):
    created = []

    def factory(npsso_code, psn_username):
        c = FakeClient(npsso_code, psn_username)
        created.append(c)
        return c

    monkeypatch.setattr(ps, "PSNClient", factory)
    env.state.settings = settings("new", "pal")
    ps.handle_psn_settings_change()
    c = created[0]
    assert ps.psn_client_instance is c and c.connects == 1
    assert env.stopped == [1] and env.started == [1]
    assert env.state.set_calls == [c.psn_data]
    assert env.emitter.emits[0][0] == "psn_data_update"


def test_settings_change_updates_existing_client_connect_fails(env):
    c = FakeClient(npsso="old", connect_result=False)
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("new", None)
    env.web.web_engine_instance = None
    ps.handle_psn_settings_change()
    assert c.cred_updates == [("new", "")]
    assert c.connects == 1 and env.started == [1]
    assert env.emitter.emits == []


def test_settings_change_username_only_change_broadcast_error(env):
    c = FakeClient(npsso="tok", username="a")
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("tok", "b")
    env.emitter.fail = True
    ps.handle_psn_settings_change()
    assert c.cred_updates == [("tok", "b")] and env.started == [1]


@pytest.mark.parametrize("engine", ["ok", "none", "fail"])
def test_settings_change_code_removed_with_client(env, engine):
    c = FakeClient(npsso="tok")
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("", None)
    if engine == "none":
        env.web.web_engine_instance = None
    elif engine == "fail":
        env.emitter.fail = True
    ps.handle_psn_settings_change()
    assert c.cred_updates == [("", "")]
    assert env.state.set_calls == [c.psn_data] and env.started == []
    if engine == "ok":
        assert env.emitter.emits[0][0] == "psn_data_update"


@pytest.mark.parametrize("engine", ["ok", "none", "fail"])
def test_settings_change_code_removed_without_client_branch(env, monkeypatch, engine):
    """No client but the change is 'required': only reachable when the comparison says changed; force it."""
    # Reach the else-branch with no client: client exists for the comparison, then is dropped by stop().
    c = FakeClient(npsso="tok")
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.settings = settings("", None)

    def stop(**kw):
        env.mp.setattr(ps, "psn_client_instance", None)

    monkeypatch.setattr(ps, "stop_psn_data_updater_thread", stop)
    if engine == "none":
        env.web.web_engine_instance = None
    elif engine == "fail":
        env.emitter.fail = True
    ps.handle_psn_settings_change()
    assert len(env.state.set_calls) == 1
    assert isinstance(env.state.set_calls[0], PSNData) and env.state.set_calls[0] is not c.psn_data
    if engine == "ok":
        assert env.emitter.emits[0][0] == "psn_data_update"


def test_settings_change_exception_swallowed(env):
    def boom():
        raise RuntimeError("x")

    env.state.get_psn_settings_data = boom
    ps.handle_psn_settings_change()


# --------------------------------------------------------------------------- simple accessors / status


def test_get_psn_client(env):
    assert ps.get_psn_client() is None
    c = FakeClient()
    env.mp.setattr(ps, "psn_client_instance", c)
    assert ps.get_psn_client() is c


@pytest.fixture
def tracker(monkeypatch):
    from modules import connection_status_tracker as cst

    cfg = {"configured": True, "overlay": None}
    monkeypatch.setattr(cst, "psn_configured", lambda: cfg["configured"])
    monkeypatch.setattr(cst, "get_connectivity_overlay", lambda key: cfg["overlay"])
    return cfg


def test_status_label_not_configured(env, tracker):
    tracker["configured"] = False
    assert ps.get_psn_status_label() == "Not Connected"
    assert ps.psn_configured_for_monitor() is False
    tracker["configured"] = True
    assert ps.psn_configured_for_monitor() is True


def test_status_label_from_client(env, tracker):
    c = FakeClient()
    c.psn_data.connection_status = "Token Expired"
    env.mp.setattr(ps, "psn_client_instance", c)
    assert ps.get_psn_status_label() == "Token Expired"


def test_status_label_from_live(env, tracker):
    c = FakeClient(npsso="  ")
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.live = PSNData(connection_status="Offline")
    assert ps.get_psn_status_label() == "Offline"
    env.state.live = PSNData(connection_status="Disconnected", is_online=True)
    assert ps.get_psn_status_label() == "Connected"
    env.state.live = PSNData(connection_status="", is_online=False)
    assert ps.get_psn_status_label() == "Offline"  # client is connected


def test_status_label_disconnected_client_reports_token_expired(env, tracker):
    c = FakeClient(authenticated=False)
    env.mp.setattr(ps, "psn_client_instance", c)
    env.state.live = None
    assert ps.get_psn_status_label() == "Token Expired"


def test_status_label_offline_without_client(env, tracker):
    env.state.live = None
    assert ps.get_psn_status_label() == "Offline"


def _display(env, tracker, label, live=None, client=None, username=None):
    env.mp.setattr(ps, "get_psn_status_label", lambda: label)
    env.state.live = live
    env.state.settings = settings("tok", username)
    if client is not None:
        env.mp.setattr(ps, "psn_client_instance", client)
    return ps.get_psn_status_display()


def test_status_display_overlay(env, tracker):
    tracker["overlay"] = "No Internet"
    assert ps.get_psn_status_display() == ("No Internet", "N/A", "text-theme-error")


@pytest.mark.parametrize("label", ["Not Connected", "Token Expired", "Authentication Failed"])
def test_status_display_errors(env, tracker, label):
    assert _display(env, tracker, label) == (label, "N/A", "text-theme-error")


def test_status_display_connected_variants(env, tracker):
    assert _display(env, tracker, "Connected", username="pal") == (
        "Connected - Tracking pal", "pal (target)", "text-theme-success")
    assert _display(env, tracker, "Connected", live=PSNData(online_id="me")) == (
        "Connected as me", "me (own account)", "text-theme-success")
    c = FakeClient()
    c.psn_data.online_id = "client-me"
    assert _display(env, tracker, "Connected", live=PSNData(), client=c) == (
        "Connected as client-me", "client-me (own account)", "text-theme-success")
    c.psn_data.online_id = None
    assert _display(env, tracker, "Connected", client=c) == ("Connected", "Unknown", "text-theme-success")


def test_status_display_offline_variants(env, tracker):
    assert _display(env, tracker, "Offline", username="pal") == (
        "Offline - Tracking pal", "pal (target)", "text-theme-warning")
    assert _display(env, tracker, "Offline", live=PSNData(online_id="me")) == (
        "Offline", "me (own account)", "text-theme-warning")
    assert _display(env, tracker, "Offline") == ("Offline", "Unknown", "text-theme-warning")


def test_status_display_other_and_no_settings(env, tracker):
    env.mp.setattr(ps, "get_psn_status_label", lambda: "Weird")
    env.state.settings = None
    assert ps.get_psn_status_display() == ("Weird", "N/A", "text-theme-warning")


def test_is_psn_api_disconnected(env):
    assert ps.is_psn_api_disconnected() is False
    c = FakeClient(npsso=" ")
    env.mp.setattr(ps, "psn_client_instance", c)
    assert ps.is_psn_api_disconnected() is False
    c.psn_data.npsso_code = "tok"
    assert ps.is_psn_api_disconnected() is False
    c.authenticated = False
    assert ps.is_psn_api_disconnected() is True


@pytest.fixture
def monitor(monkeypatch):
    from modules import connection_monitor as cm

    cfg = {"internet": True, "reach": True, "raise": False}

    def internet():
        if cfg["raise"]:
            raise RuntimeError("x")
        return cfg["internet"]

    monkeypatch.setattr(cm, "is_internet_available", internet)
    monkeypatch.setattr(cm, "is_service_reachable", lambda key: cfg["reach"])
    return cfg


def test_attempt_auto_reconnect(env, monitor):
    assert ps.attempt_auto_reconnect() is False
    c = FakeClient(npsso="", authenticated=False)
    env.mp.setattr(ps, "psn_client_instance", c)
    assert ps.attempt_auto_reconnect() is False
    c.psn_data.npsso_code = "tok"
    monitor["internet"] = False
    assert ps.attempt_auto_reconnect() is False
    monitor["internet"] = True
    monitor["reach"] = False
    assert ps.attempt_auto_reconnect() is False
    monitor["reach"] = True
    monitor["raise"] = True
    assert ps.attempt_auto_reconnect() is False
    monitor["raise"] = False
    assert c.connects == 0
    assert ps.attempt_auto_reconnect() is True and c.connects == 1
    c.connect_result = False
    assert ps.attempt_auto_reconnect() is False

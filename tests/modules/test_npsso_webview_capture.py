# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/npsso_webview_capture.py with a fake pywebview (no window is ever opened)."""

from __future__ import annotations

import json
import sys
import types
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import npsso_webview_capture as cap  # noqa: E402

TOKEN = "Z" * 40 + "a1_-" * 6  # 64 chars


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        (123, None),
        ("  " + TOKEN + "\n", TOKEN),
        (TOKEN[:-1], None),
        (TOKEN + "x", None),
        ("$" * 64, None),
    ],
)
def test_normalize_npsso_value(raw, expected):
    assert cap._normalize_npsso_value(raw) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", None),
        (f'{{"npsso": "{TOKEN}"}}', TOKEN),
        (f'garbage before "npsso" :  "{TOKEN}" after', TOKEN),
        ('{"npsso": "tooshort"}', None),
        ("not json at all", None),
        ("[1, 2, 3]", None),
        ('{"other": 1}', None),
        ('{"npsso": 5}', None),
        (f"   {json.dumps({'npsso': TOKEN})}   ", TOKEN),
    ],
)
def test_parse_npsso_from_text(text, expected):
    assert cap._parse_npsso_from_text(text) == expected


def test_parse_eval_result_none():
    assert cap._parse_npsso_from_eval_result(None) is None


def test_parse_eval_result_dict_direct():
    assert cap._parse_npsso_from_eval_result({"npsso": TOKEN}) == TOKEN


def test_parse_eval_result_dict_with_bad_value_falls_back_to_json_text():
    assert cap._parse_npsso_from_eval_result({"npsso": "bad"}) is None
    assert cap._parse_npsso_from_eval_result({"npsso": 7}) is None


def test_parse_eval_result_dict_not_serializable():
    assert cap._parse_npsso_from_eval_result({"npsso": None, "x": object()}) is None


def test_parse_eval_result_string_and_other():
    assert cap._parse_npsso_from_eval_result(f'{{"npsso":"{TOKEN}"}}') == TOKEN

    class Weird:
        def __str__(self):
            return f'"npsso": "{TOKEN}"'

    assert cap._parse_npsso_from_eval_result(Weird()) == TOKEN
    assert cap._parse_npsso_from_eval_result(42) is None


# --------------------------------------------------------------------------- cookies / HTTP / DOM


class FakeWindow:
    def __init__(self, cookies=None, cookies_exc=None, eval_result=None, eval_exc=None, urls=None,
                 url_exc=None, load_exc=None, destroy_exc=None):
        self._cookies = cookies
        self._cookies_exc = cookies_exc
        self._eval_result = eval_result
        self._eval_exc = eval_exc
        self._urls = list(urls or [])
        self._url_exc = url_exc
        self._load_exc = load_exc
        self._destroy_exc = destroy_exc
        self.loaded = []
        self.destroyed = 0
        self.evals = 0
        self.events = SimpleNamespace(loaded=_Event())

    def get_cookies(self):
        if self._cookies_exc:
            raise self._cookies_exc
        return self._cookies

    def evaluate_js(self, js):
        self.evals += 1
        if self._eval_exc:
            raise self._eval_exc
        r = self._eval_result
        return r(self.evals) if callable(r) else r

    def get_current_url(self):
        if self._url_exc:
            raise self._url_exc
        if len(self._urls) > 1:
            return self._urls.pop(0)
        return self._urls[0] if self._urls else None

    def load_url(self, url):
        if self._load_exc:
            raise self._load_exc
        self.loaded.append(url)

    def destroy(self):
        self.destroyed += 1
        if self._destroy_exc:
            raise self._destroy_exc


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        for h in list(self.handlers):
            h()


def _cookie(name, value):
    c = SimpleCookie()
    c[name] = value
    return c


def test_cookies_merged_from_all_jars():
    w = FakeWindow(cookies=[_cookie("a", "1"), _cookie("b", "2"), _cookie("a", "3")])
    assert cap._cookies_from_webview(w) == {"a": "3", "b": "2"}


def test_cookies_get_cookies_raises():
    assert cap._cookies_from_webview(FakeWindow(cookies_exc=RuntimeError("x"))) == {}


@pytest.mark.parametrize("raw", [None, []])
def test_cookies_empty(raw):
    assert cap._cookies_from_webview(FakeWindow(cookies=raw)) == {}


def test_cookies_skip_bad_entries():
    w = FakeWindow(cookies=[object(), {"x": SimpleNamespace(value="v")}, {"y": "no-value-attr"}])
    assert cap._cookies_from_webview(w) == {"x": "v"}


class _Get:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return self.resp


def test_http_npsso_without_cookies_does_not_request(monkeypatch):
    get = _Get()
    monkeypatch.setattr(cap.requests, "get", get)
    assert cap._try_http_npsso(FakeWindow(cookies=[])) is None
    assert get.calls == []


def test_http_npsso_success(monkeypatch):
    get = _Get(SimpleNamespace(status_code=200, text=json.dumps({"npsso": TOKEN})))
    monkeypatch.setattr(cap.requests, "get", get)
    assert cap._try_http_npsso(FakeWindow(cookies=[_cookie("sess", "abc")])) == TOKEN
    url, kw = get.calls[0]
    assert url == cap.NPSSO_URL
    assert kw["cookies"] == {"sess": "abc"}
    assert kw["headers"] is cap._REQUEST_HEADERS
    assert kw["timeout"] == 45


def test_http_npsso_non_200(monkeypatch):
    monkeypatch.setattr(cap.requests, "get", _Get(SimpleNamespace(status_code=401, text="{}")))
    assert cap._try_http_npsso(FakeWindow(cookies=[_cookie("s", "1")])) is None


def test_http_npsso_request_exception(monkeypatch):
    monkeypatch.setattr(cap.requests, "get", _Get(exc=requests.ConnectionError("down")))
    assert cap._try_http_npsso(FakeWindow(cookies=[_cookie("s", "1")])) is None


def test_dom_npsso_success_and_failure():
    assert cap._try_dom_npsso(FakeWindow(eval_result=f'{{"npsso":"{TOKEN}"}}')) == TOKEN
    assert cap._try_dom_npsso(FakeWindow(eval_exc=RuntimeError("js"))) is None
    assert cap._try_dom_npsso(FakeWindow(eval_result="")) is None


# --------------------------------------------------------------------------- main() with fake pywebview


class FakeWebview:
    """Installs fake ``webview`` and ``webview.menu`` modules; ``script`` plays the user's actions."""

    def __init__(self, window, script):
        self.window = window
        self.script = script
        self.created = None
        self.start_kwargs = None
        self.menu = None

    def install(self, monkeypatch):
        mod = types.ModuleType("webview")
        menu_mod = types.ModuleType("webview.menu")

        class Menu:
            def __init__(self, title, items):
                self.title, self.items = title, items

        class MenuAction:
            def __init__(self, title, function):
                self.title, self.function = title, function

        menu_mod.Menu = Menu
        menu_mod.MenuAction = MenuAction
        mod.menu = menu_mod
        mod.Window = object

        def create_window(title, url, **kw):
            self.created = (title, url, kw)
            return self.window

        def start(menu=None, **kw):
            self.menu = menu
            self.start_kwargs = kw
            self.script(self)

        mod.create_window = create_window
        mod.start = start
        monkeypatch.setitem(sys.modules, "webview", mod)
        monkeypatch.setitem(sys.modules, "webview.menu", menu_mod)

    def click_menu(self):
        self.menu[0].items[0].function()


class SyncThreading:
    """Runs threads synchronously; records timers so tests can fire them."""

    def __init__(self):
        self.timers = []
        self.threads = 0

    def Thread(self, target, daemon=False):  # noqa: N802
        outer = self

        class T:
            def start(self_inner):
                outer.threads += 1
                target()

        return T()

    def Timer(self, interval, fn):  # noqa: N802
        outer = self

        class Tm:
            def start(self_inner):
                outer.timers.append((interval, fn))

        return Tm()


@pytest.fixture
def sync(monkeypatch):
    st = SyncThreading()
    monkeypatch.setattr(cap, "threading", st)
    sleeps = []
    monkeypatch.setattr(cap.time, "sleep", lambda s: sleeps.append(s))
    st.sleeps = sleeps
    return st


def test_main_success_via_dom_after_menu_and_loaded(monkeypatch, sync, capsys):
    window = FakeWindow(urls=["https://ca.account.sony.com/api/v1/ssocookie"],
                        eval_result=f'{{"npsso":"{TOKEN}"}}')

    def script(fw):
        # loaded before fetching does nothing
        window.events.loaded.fire()
        assert sync.threads == 0
        fw.click_menu()
        assert window.loaded == [cap.NPSSO_URL]
        assert sync.timers and sync.timers[0][0] == 0.45
        window.events.loaded.fire()

    fw = FakeWebview(window, script)
    fw.install(monkeypatch)
    assert cap.main() == 0
    out = capsys.readouterr()
    assert out.out.strip() == TOKEN
    assert window.destroyed == 1
    assert fw.created[1] == cap.PSN_START_URL
    assert fw.created[2]["text_select"] is True
    assert fw.start_kwargs == {"debug": False, "private_mode": False}
    assert fw.menu[0].title == "NPSSO"
    assert sync.threads == 1


def test_main_menu_twice_and_timer_do_not_restart_after_token(monkeypatch, sync, capsys):
    window = FakeWindow(urls=["https://my.account.sony.com/x"], eval_result={"npsso": TOKEN})

    def script(fw):
        fw.click_menu()
        sync.timers[0][1]()  # timer kick -> resolves
        assert sync.threads == 1
        window.events.loaded.fire()  # token already set -> no new thread
        fw.click_menu()  # token set -> start_fetch returns early
        assert window.loaded == [cap.NPSSO_URL]

    fw = FakeWebview(window, script)
    fw.install(monkeypatch)
    assert cap.main() == 0
    assert sync.threads == 1
    assert capsys.readouterr().out.strip() == TOKEN


def test_main_falls_back_to_http_after_dom_attempts(monkeypatch, sync, capsys):
    window = FakeWindow(urls=["https://ca.account.sony.com/api/v1/ssocookie"], eval_result="",
                        cookies=[_cookie("s", "1")])
    monkeypatch.setattr(cap.requests, "get", _Get(SimpleNamespace(status_code=200, text=f'"npsso":"{TOKEN}"')))

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert window.evals == 6
    assert sync.sleeps.count(0.12) == 6
    assert capsys.readouterr().out.strip() == TOKEN


def test_main_not_ready_url_waits_then_uses_http_after_18_attempts(monkeypatch, sync, capsys):
    window = FakeWindow(urls=["https://www.playstation.com/"], cookies=[_cookie("s", "1")])
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return SimpleNamespace(status_code=200, text=json.dumps({"npsso": TOKEN}))

    monkeypatch.setattr(cap.requests, "get", fake_get)

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert window.evals == 0  # never DOM since URL not ready
    assert sync.sleeps.count(0.2) == 18
    assert len(calls) == 1


def test_main_url_exception_treated_as_not_ready(monkeypatch, sync, capsys):
    window = FakeWindow(url_exc=RuntimeError("gone"), cookies=[_cookie("s", "1")])
    monkeypatch.setattr(cap.requests, "get", _Get(SimpleNamespace(status_code=200, text=json.dumps({"npsso": TOKEN}))))

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0


def test_main_gives_up_after_80_attempts(monkeypatch, sync, capsys):
    window = FakeWindow(urls=["about:blank"], cookies=[], destroy_exc=RuntimeError("already closed"))

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()
        # resolving flag reset in finally, but fetching is cleared by finish_error -> no restart
        window.events.loaded.fire()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 1
    err = capsys.readouterr().err
    assert "NPSSO could not be retrieved" in err
    assert sync.sleeps.count(0.2) == 80
    assert window.destroyed == 1
    assert sync.threads == 1


def test_main_load_url_failure_reports_error(monkeypatch, sync, capsys):
    window = FakeWindow(load_exc=RuntimeError("nav blocked"))

    def script(fw):
        fw.click_menu()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 1
    assert "Could not load NPSSO URL: nav blocked" in capsys.readouterr().err
    assert sync.timers == []
    assert window.destroyed == 1


def test_main_window_closed_without_token(monkeypatch, sync, capsys):
    window = FakeWindow()
    FakeWebview(window, lambda fw: None).install(monkeypatch)
    assert cap.main() == 1
    err = capsys.readouterr().err
    assert "Window closed before NPSSO was retrieved" in err
    assert window.destroyed == 0


def test_main_resolve_not_restarted_while_resolving(monkeypatch, sync, capsys):
    """A loaded event fired from inside the worker (while resolving) must not start a second worker."""
    window = FakeWindow(urls=["https://ca.account.sony.com/x"])
    fired = []

    def eval_result(n):
        if n == 1:
            fired.append(True)
            window.events.loaded.fire()  # resolving=True -> ignored
            return ""
        return {"npsso": TOKEN}

    window._eval_result = eval_result

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert fired == [True]
    assert sync.threads == 1


def test_main_worker_stops_when_token_set_elsewhere(monkeypatch, sync, capsys):
    """If another path set the token mid-loop, the worker returns without destroying again."""
    window = FakeWindow(urls=["https://ca.account.sony.com/x"])
    state = {"n": 0}

    def eval_result(n):
        state["n"] = n
        if n == 1:
            # a second worker finishing first (simulate by kicking the timer which is ignored while resolving)
            return {"npsso": TOKEN}
        raise AssertionError("should not evaluate again")

    window._eval_result = eval_result

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()
        # error after success is ignored
        window.events.loaded.fire()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert window.destroyed == 1


def test_finish_paths_ignore_after_token(monkeypatch, sync, capsys):
    """finish_success twice and finish_error after success are no-ops (token printed once)."""
    window = FakeWindow(urls=["https://ca.account.sony.com/x"], eval_result={"npsso": TOKEN})
    calls = {"n": 0}

    real_dom = cap._try_dom_npsso

    def dom(w):
        calls["n"] += 1
        return real_dom(w)

    monkeypatch.setattr(cap, "_try_dom_npsso", dom)

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()
        sync.timers[0][1]()  # token already set -> ensure_resolve_started returns early

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert calls["n"] == 1
    assert capsys.readouterr().out.count(TOKEN) == 1


def test_module_entrypoint_raises_systemexit(monkeypatch, sync):
    import runpy

    window = FakeWindow()
    FakeWebview(window, lambda fw: None).install(monkeypatch)
    monkeypatch.setitem(sys.modules, "requests", requests)
    with pytest.raises(SystemExit) as ei:
        runpy.run_path(str(ROOT / "modules" / "npsso_webview_capture.py"), run_name="__main__")
    assert ei.value.code == 1


class ManualThreading(SyncThreading):
    """Records thread targets instead of running them so tests can interleave two workers."""

    def __init__(self):
        super().__init__()
        self.targets = []

    def Thread(self, target, daemon=False):  # noqa: N802
        outer = self

        class T:
            def start(self_inner):
                outer.threads += 1
                outer.targets.append(target)

        return T()


@pytest.fixture
def manual(monkeypatch):
    mt = ManualThreading()
    monkeypatch.setattr(cap, "threading", mt)
    mt.sleeps = []
    monkeypatch.setattr(cap.time, "sleep", lambda s: mt.sleeps.append(s))
    return mt


def test_concurrent_worker_success_wins_and_others_back_off(monkeypatch, manual, capsys):
    """Worker B finishes inside worker A's DOM read: A's own success is ignored and its loops stop."""
    window = FakeWindow(urls=["https://ca.account.sony.com/x"], destroy_exc=RuntimeError("closed"))

    def eval_result(n):
        if n == 1:
            manual.targets[0]()  # nested "other thread" run: evaluates n == 2 and succeeds
            return {"npsso": TOKEN}  # A also gets a token -> finish_success no-op
        return {"npsso": TOKEN}

    window._eval_result = eval_result

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()
        manual.targets[0]()  # worker A
        manual.targets[0]()  # late worker: token already set -> returns at loop top

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert window.destroyed == 1  # destroy raised but was swallowed; only one success
    assert capsys.readouterr().out.count(TOKEN) == 1


def test_inner_dom_loop_stops_when_token_arrives_during_sleep(monkeypatch, manual, capsys):
    window = FakeWindow(urls=["https://ca.account.sony.com/x"])
    evals = []

    def eval_result(n):
        evals.append(n)
        return {"npsso": TOKEN} if n == 2 else ""

    window._eval_result = eval_result

    def sleep(s):
        manual.sleeps.append(s)
        if len(manual.sleeps) == 1:
            manual.targets[0]()  # other worker runs during the 0.12s sleep and succeeds

    monkeypatch.setattr(cap.time, "sleep", sleep)

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()
        manual.targets[0]()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    assert evals == [1, 2]
    assert manual.sleeps == [0.12]


def test_timeout_error_ignored_when_other_worker_succeeded_on_last_attempt(monkeypatch, manual, capsys):
    window = FakeWindow(urls=["about:blank"], cookies=[])
    count = {"n": 0}

    def sleep(s):
        count["n"] += 1
        if count["n"] == 80:  # outer worker's final 0.2s sleep: another worker succeeds now
            window._urls = ["https://ca.account.sony.com/api/v1/ssocookie"]
            window._eval_result = {"npsso": TOKEN}
            manual.targets[0]()

    monkeypatch.setattr(cap.time, "sleep", sleep)

    def script(fw):
        fw.click_menu()
        window.events.loaded.fire()
        manual.targets[0]()

    FakeWebview(window, script).install(monkeypatch)
    assert cap.main() == 0
    out = capsys.readouterr()
    assert out.out.strip() == TOKEN
    assert "could not be retrieved" not in out.err

"""RT-15 / CN-03: OBS idle health probe spacing and dead-socket detection time."""

from __future__ import annotations

import threading
import time

from modules import obs_service as obs


class _FakeClient:
    def __init__(self, *, fail: bool = False, hang: float = 0.0) -> None:
        self.fail = fail
        self.hang = hang
        self.calls = []

    def get_stream_status(self):
        self.calls.append(("stream", time.monotonic()))
        if self.hang:
            time.sleep(self.hang)
        if self.fail:
            raise ConnectionError("socket closed")
        return {"outputActive": True, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}

    def get_record_status(self):
        self.calls.append(("record", time.monotonic()))
        if self.fail:
            raise ConnectionError("socket closed")
        return {"outputActive": False, "outputState": ""}


def _service(client):
    svc = obs.ObsServiceImpl()
    svc._req_client = client
    svc._ev_client = None
    svc._apply_ws_timeout = lambda cl, timeout: None
    svc._disconnect_clients = lambda: setattr(svc, "_req_client", None)
    return svc


def test_idle_probe_spacing_and_flags():
    client = _FakeClient()
    svc = _service(client)
    t = threading.Thread(target=svc._run_loop_iteration, daemon=True)
    t.start()
    time.sleep(2.6)
    svc._stop.set()
    t.join(2)
    starts = [ts for kind, ts in client.calls if kind == "stream"]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) >= 4
    assert all(g >= obs._IDLE_HEALTH_INTERVAL_SEC for g in gaps)
    assert all(g < 0.7 for g in gaps)
    # Both output flags are still refreshed by the idle probe.
    assert svc._snapshot["stream_output_active"] is True
    assert svc._snapshot["record_output_active"] is False


def test_dead_socket_detected_within_budget():
    # Fast-fail socket: 4 failures at 0.5 s spacing -> about 2.0 s (was 1.2 s).
    client = _FakeClient(fail=True)
    svc = _service(client)
    t0 = time.monotonic()
    svc._run_loop_iteration()
    elapsed = time.monotonic() - t0
    assert svc._req_client is None
    assert elapsed < 1.2 + 1.0


def test_reconnect_wakeup_checked_every_tick():
    svc = _service(None)
    t = threading.Thread(target=svc._run_loop_iteration, daemon=True)
    t.start()
    t0 = time.monotonic()
    svc._reconnect_wakeup.set()
    t.join(2)
    assert not t.is_alive()
    assert time.monotonic() - t0 <= 0.3 + 0.1

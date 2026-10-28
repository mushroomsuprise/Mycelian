# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/memory/windows.py with kernel32 and psutil faked (runs on any OS)."""

from __future__ import annotations

import ctypes
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks.memory import windows as win  # noqa: E402
from modules.game_hooks.memory.base import AttachedProcess  # noqa: E402


# --------------------------------------------------------------------------- fakes


class FakeKernel:
    """Python stand-ins for the kernel32 calls; memory is a dict addr -> byte."""

    def __init__(self):
        self.mem = {}
        self.calls = []
        self.open_result = 0x99
        self.read_ok = True
        self.short_read = False
        self.write_ok = True
        self.protect_ok = True
        self.alloc_result = 0x5000
        self.thread_result = 0x77
        self.wait_result = 0
        self.raise_in = None
        self.last_error = 5

    def _maybe_raise(self, name):
        if self.raise_in == name:
            raise OSError(name)

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("OpenProcess", access, inherit, pid))
        return self.open_result

    def ReadProcessMemory(self, h, addr, buf, size, pread):
        a = addr.value
        self.calls.append(("Read", h, a, size))
        if not self.read_ok:
            return 0
        data = bytes(self.mem.get(a + i, 0) for i in range(size))
        ctypes.memmove(buf, data, size)
        pread._obj.value = size - 1 if self.short_read else size
        return 1

    def WriteProcessMemory(self, h, addr, data, n, pwritten):
        a = addr.value
        self.calls.append(("Write", h, a, bytes(data)))
        if not self.write_ok:
            return 0
        for i, b in enumerate(bytes(data)):
            self.mem[a + i] = b
        pwritten._obj.value = n
        return 1

    def CloseHandle(self, h):
        self.calls.append(("Close", h))
        self._maybe_raise("Close")
        return 1

    def VirtualProtectEx(self, h, page, size, prot, pold):
        self.calls.append(("Protect", h, page.value, size.value, prot))
        if not self.protect_ok:
            return 0
        pold._obj.value = 0x04 if prot == 0x40 else 0x40
        return 1

    def FlushInstructionCache(self, h, page, size):
        self.calls.append(("Flush", h, page.value, size.value))
        return 1

    def VirtualAllocEx(self, h, addr, size, kind, prot):
        self.calls.append(("Alloc", h, size, kind, prot))
        self._maybe_raise("Alloc")
        return self.alloc_result

    def VirtualFreeEx(self, h, addr, size, kind):
        self.calls.append(("Free", h, addr, kind))
        self._maybe_raise("Free")
        return 1

    def CreateRemoteThread(self, h, attr, stack, start, param, flags, ptid):
        self.calls.append(("Thread", h, start.value))
        return self.thread_result

    def WaitForSingleObject(self, h, ms):
        self.calls.append(("Wait", h, ms))
        return self.wait_result

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def k(monkeypatch):
    fk = FakeKernel()
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "_kernel32", object())
    monkeypatch.setattr(win, "_PROCESS_ACCESS", 0x43A)
    for name in ("OpenProcess", "ReadProcessMemory", "WriteProcessMemory", "CloseHandle", "VirtualProtectEx",
                 "FlushInstructionCache", "VirtualAllocEx", "VirtualFreeEx", "CreateRemoteThread",
                 "WaitForSingleObject"):
        monkeypatch.setattr(win, "_" + name, getattr(fk, name))
    monkeypatch.setattr(win.ctypes, "get_last_error", lambda: fk.last_error, raising=False)
    return fk


def _attached(handle=0x99, base=0x400000, pid=10):
    m = win.WindowsProcessMemory()
    m._attached = AttachedProcess(pid=pid, handle=handle, module_base=base)
    return m


class _Map:
    def __init__(self, path, addr):
        self.path, self.addr = path, addr


def _psutil(maps=None, raise_exc=None, pid_exists=True):
    class Proc:
        def __init__(self, pid):
            if raise_exc:
                raise raise_exc
            self.pid = pid

        def memory_maps(self, grouped=True):
            assert grouped is False
            return maps or []

    return types.SimpleNamespace(Process=Proc, pid_exists=lambda pid: pid_exists)


# --------------------------------------------------------------------------- module import on Windows


def test_import_block_binds_kernel32_on_windows(monkeypatch):
    class FakeFn:
        argtypes = None
        restype = None

    class FakeDLL:
        def __init__(self, name, use_last_error=False):
            self.name, self.ule = name, use_last_error
            self.fns = {}

        def __getattr__(self, item):
            if item.startswith("_") or item in ("name", "ule", "fns"):
                raise AttributeError(item)
            return self.fns.setdefault(item, FakeFn())

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", FakeDLL, raising=False)
    spec = importlib.util.spec_from_file_location("modules.game_hooks.memory._windows_import_probe", win.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert isinstance(mod._kernel32, FakeDLL) and mod._kernel32.ule is True
    assert mod._PROCESS_ACCESS == 0x0010 | 0x0020 | 0x0008 | 0x0400 | 0x0002
    for name in ("OpenProcess", "ReadProcessMemory", "WriteProcessMemory", "CloseHandle", "VirtualProtectEx",
                 "FlushInstructionCache", "VirtualAllocEx", "VirtualFreeEx", "CreateRemoteThread",
                 "WaitForSingleObject"):
        fn = getattr(mod, "_" + name)
        assert fn is mod._kernel32.fns[name]
        assert fn.argtypes is not None and fn.restype is not None
    assert mod._ReadProcessMemory.restype is mod.wintypes.BOOL
    assert "modules.game_hooks.memory._windows_import_probe" not in sys.modules


def test_non_windows_import_leaves_kernel_unbound():
    if sys.platform == "win32":  # pragma: no cover
        pytest.skip("non-Windows check")
    assert win._kernel32 is None and win._OpenProcess is None and win._PROCESS_ACCESS == 0


# --------------------------------------------------------------------------- module base


def test_module_base_found(monkeypatch):
    maps = [_Map(None, "0x1-0x2"), _Map("C:\\Windows\\ntdll.dll", "7ff0-7ff1"),
            _Map("C:\\Games\\FF7\\FF7_EN.EXE", " 00400000-00500000")]
    monkeypatch.setitem(sys.modules, "psutil", _psutil(maps))
    assert win._module_base_for_pid(5, ("ff7_en.exe", "ff7.exe")) == 0x400000


def test_module_base_missing_or_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", _psutil([_Map("x/other.exe", "100-200")]))
    assert win._module_base_for_pid(5, ("ff7.exe",)) is None
    monkeypatch.setitem(sys.modules, "psutil", _psutil(raise_exc=RuntimeError("gone")))
    assert win._module_base_for_pid(5, ("ff7.exe",)) is None
    monkeypatch.setitem(sys.modules, "psutil", _psutil([_Map("x/ff7.exe", "zz-1")]))
    assert win._module_base_for_pid(5, ("ff7.exe",)) is None  # bad hex -> ValueError swallowed


# --------------------------------------------------------------------------- basic state


def test_properties_unattached_and_attached():
    m = win.WindowsProcessMemory()
    assert (m.pid, m.handle, m.module_base) == (None, None, None)
    assert not m.is_attached()
    m = _attached(handle=3, base=0x1000, pid=4)
    assert (m.pid, m.handle, m.module_base) == (4, 3, 0x1000)
    assert m.is_attached()


def test_is_available(monkeypatch, k):
    assert win.WindowsProcessMemory().is_available()
    monkeypatch.setattr(win, "_kernel32", None)
    assert not win.WindowsProcessMemory().is_available()
    monkeypatch.setattr(win, "_kernel32", object())
    monkeypatch.setattr(win.sys, "platform", "darwin")
    assert not win.WindowsProcessMemory().is_available()


def test_is_process_running(monkeypatch):
    monkeypatch.setattr(win, "find_process_pid", lambda n: 3 if n == ("a.exe",) else None)
    m = win.WindowsProcessMemory()
    assert m.is_process_running(("a.exe",)) and not m.is_process_running(("b.exe",))


def test_close_unavailable_just_detaches(monkeypatch):
    monkeypatch.setattr(win.sys, "platform", "darwin")
    m = _attached()
    m.close()
    assert not m.is_attached()


def test_close_closes_handle_and_swallows_errors(k):
    m = _attached(handle=0x55)
    m.close()
    assert ("Close", 0x55) in k.calls and not m.is_attached()
    k.raise_in = "Close"
    m = _attached(handle=0x56)
    m.close()
    assert not m.is_attached()
    m = _attached(handle=0)
    k.calls.clear()
    m.close()
    assert k.calls == []


# --------------------------------------------------------------------------- attach


def test_attach_unavailable(monkeypatch):
    monkeypatch.setattr(win.sys, "platform", "darwin")
    ok, err = win.WindowsProcessMemory().attach(("ff7.exe",))
    assert not ok and "only supported on Windows" in err


def test_attach_process_not_found(monkeypatch, k):
    monkeypatch.setattr(win, "find_process_pid", lambda n: None)
    ok, err = win.WindowsProcessMemory().attach(("a.exe", "b.exe"))
    assert (ok, err) == (False, "Process not found (a.exe, b.exe)")


def test_attach_no_module_base(monkeypatch, k):
    monkeypatch.setattr(win, "find_process_pid", lambda n: 7)
    monkeypatch.setattr(win, "_module_base_for_pid", lambda pid, n: None)
    ok, err = win.WindowsProcessMemory().attach(("a.exe",))
    assert not ok and "module base" in err


def test_attach_open_process_fails(monkeypatch, k):
    monkeypatch.setattr(win, "find_process_pid", lambda n: 7)
    monkeypatch.setattr(win, "_module_base_for_pid", lambda pid, n: 0x400000)
    k.open_result = 0
    ok, err = win.WindowsProcessMemory().attach(("a.exe",))
    assert not ok and err == "OpenProcess failed (pid=7, err=5)"


def test_attach_success_replaces_previous(monkeypatch, k):
    monkeypatch.setattr(win, "find_process_pid", lambda n: 7)
    monkeypatch.setattr(win, "_module_base_for_pid", lambda pid, n: 0x410000)
    m = _attached(handle=0x11)
    ok, err = m.attach(("a.exe",), canon_image_base=0x123)
    assert (ok, err) == (True, None)
    assert ("OpenProcess", 0x43A, False, 7) in k.calls
    assert ("Close", 0x11) in k.calls
    assert (m.pid, m.handle, m.module_base) == (7, 0x99, 0x410000)


def test_ensure_attached_paths(monkeypatch, k):
    m = _attached()
    monkeypatch.setitem(sys.modules, "psutil", _psutil(pid_exists=True))
    assert m.ensure_attached(("a.exe",)) == (True, None)
    assert m.is_attached()

    attach_calls = []
    monkeypatch.setattr(win.WindowsProcessMemory, "attach",
                        lambda self, names, canon_image_base=0: attach_calls.append(canon_image_base) or (False, "nope"))
    monkeypatch.setitem(sys.modules, "psutil", _psutil(pid_exists=False))
    assert m.ensure_attached(("a.exe",), canon_image_base=0x500) == (False, "nope")
    assert not m.is_attached() and attach_calls == [0x500]

    m = _attached()
    monkeypatch.setitem(sys.modules, "psutil", None)  # import error -> close + reattach
    assert m.ensure_attached(("a.exe",)) == (False, "nope")
    assert not m.is_attached()


# --------------------------------------------------------------------------- read / write


def test_read_guards(k):
    assert win.WindowsProcessMemory().read(0x100, 4) is None
    m = _attached()
    assert m.read(0, 4) is None
    assert m.read(0x100, 0) is None
    assert k.calls == []


def test_read_success_caches_buffer(k):
    k.mem.update({0x100: 1, 0x101: 2, 0x102: 3, 0x103: 4})
    m = _attached()
    assert m.read(0x100, 4) == b"\x01\x02\x03\x04"
    buf = m._rpm_bufs[4]
    assert m.read(0x101, 4) == b"\x02\x03\x04\x00"
    assert m._rpm_bufs[4] is buf


def test_read_failures(k):
    m = _attached()
    k.read_ok = False
    assert m.read(0x100, 2) is None
    k.read_ok = True
    k.short_read = True
    assert m.read(0x100, 2) is None


def test_write(k, monkeypatch):
    m = _attached()
    assert m.write(0x200, b"\xAA\xBB") is True
    assert k.mem[0x200] == 0xAA and k.mem[0x201] == 0xBB
    k.write_ok = False
    assert m.write(0x200, b"\x01") is False
    assert m.write(0, b"\x01") is False
    assert m.write(0x200, b"") is False
    assert win.WindowsProcessMemory().write(0x200, b"\x01") is False
    monkeypatch.setattr(win.sys, "platform", "darwin")
    assert m.write(0x200, b"\x01") is False


# --------------------------------------------------------------------------- protect_write


def test_protect_write_guards(k):
    assert win.WindowsProcessMemory().protect_write(0x1000, b"\x01") is False
    assert _attached().protect_write(0x1000, b"") is False


def test_protect_write_without_virtualprotect_falls_back(monkeypatch, k):
    monkeypatch.setattr(win, "_VirtualProtectEx", None)
    assert _attached().protect_write(0x1234, b"\x09") is True
    assert k.mem[0x1234] == 9
    assert "Flush" not in k.names()


def test_protect_write_success_spans_pages_and_flushes(k):
    m = _attached(handle=0x42)
    assert m.protect_write(0x1FFE, b"\x01\x02\x03\x04") is True
    protects = [c for c in k.calls if c[0] == "Protect"]
    assert protects[0] == ("Protect", 0x42, 0x1000, 0x2000, 0x40)
    assert protects[1] == ("Protect", 0x42, 0x1000, 0x2000, 0x04)  # old protection restored
    assert ("Flush", 0x42, 0x1000, 0x2000) in k.calls
    assert [k.mem[0x1FFE + i] for i in range(4)] == [1, 2, 3, 4]


def test_protect_write_no_flush_options(k, monkeypatch):
    m = _attached()
    assert m.protect_write(0x1000, b"\x01", flush_icache=False, page_protect=0x04) is True
    assert "Flush" not in k.names()
    monkeypatch.setattr(win, "_FlushInstructionCache", None)
    assert m.protect_write(0x1000, b"\x01") is True


def test_protect_write_protect_fails(k):
    k.protect_ok = False
    assert _attached().protect_write(0x1000, b"\x01") is False
    assert "Write" not in k.names()


def test_protect_write_write_fails_still_restores(k):
    k.write_ok = False
    assert _attached().protect_write(0x1000, b"\x01") is False
    assert k.names().count("Protect") == 2
    assert "Flush" not in k.names()


# --------------------------------------------------------------------------- run_remote_shellcode


def test_shellcode_guards(monkeypatch, k):
    assert win.WindowsProcessMemory().run_remote_shellcode(b"\xC3", "x") is False
    for name in ("_VirtualAllocEx", "_VirtualFreeEx", "_CreateRemoteThread", "_WaitForSingleObject"):
        with monkeypatch.context() as mp:
            mp.setattr(win, name, None)
            assert _attached().run_remote_shellcode(b"\xC3", "x") is False
    monkeypatch.setattr(win.sys, "platform", "linux")
    assert _attached().run_remote_shellcode(b"\xC3", "x") is False


def test_shellcode_success(k):
    m = _attached(handle=0x42)
    assert m.run_remote_shellcode(b"\x90\xC3", "lbl") is True
    assert ("Alloc", 0x42, 2, 0x3000, 0x40) in k.calls
    assert ("Thread", 0x42, 0x5000) in k.calls
    assert ("Wait", 0x77, 5000) in k.calls
    assert ("Close", 0x77) in k.calls
    assert ("Free", 0x42, 0x5000, 0x8000) in k.calls
    assert k.mem[0x5000] == 0x90 and k.mem[0x5001] == 0xC3


def test_shellcode_alloc_fails(k):
    k.alloc_result = 0
    assert _attached().run_remote_shellcode(b"\xC3", "x") is False
    assert "Free" not in k.names()


def test_shellcode_write_fails(k):
    k.write_ok = False
    assert _attached().run_remote_shellcode(b"\xC3", "x") is False
    assert "Thread" not in k.names() and "Free" in k.names()


def test_shellcode_thread_fails(k):
    k.thread_result = 0
    assert _attached().run_remote_shellcode(b"\xC3", "x") is False
    assert "Wait" not in k.names() and "Free" in k.names()


def test_shellcode_wait_timeout_closes_thread(k):
    k.wait_result = 0x102
    assert _attached().run_remote_shellcode(b"\xC3", "x") is False
    assert ("Close", 0x77) in k.calls and "Free" in k.names()


def test_shellcode_exception_and_free_error_swallowed(k):
    k.raise_in = "Alloc"
    assert _attached().run_remote_shellcode(b"\xC3", "x") is False
    k.raise_in = "Free"
    assert _attached().run_remote_shellcode(b"\xC3", "x") is True


# --------------------------------------------------------------------------- rebase


def test_rebase():
    assert win.WindowsProcessMemory().rebase(0x401000) == 0x401000
    m = _attached(base=0x1400000)
    assert m.rebase(0x401000) == 0x1401000
    assert m.rebase(0x1010, canon_image_base=0x1000) == 0x1400010

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Windows process memory read/write via kernel32."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional, Tuple

from .base import AttachedProcess, find_process_pid

logger = logging.getLogger(__name__)

_PAGE_READWRITE = 0x04
_PAGE_EXECUTE_READWRITE = 0x40

_PROCESS_ACCESS = 0
_kernel32 = None
_OpenProcess = None
_ReadProcessMemory = None
_WriteProcessMemory = None
_CloseHandle = None
_VirtualProtectEx = None
_FlushInstructionCache = None
_VirtualAllocEx = None
_VirtualFreeEx = None
_CreateRemoteThread = None
_WaitForSingleObject = None

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OpenProcess = _kernel32.OpenProcess
    _ReadProcessMemory = _kernel32.ReadProcessMemory
    _WriteProcessMemory = _kernel32.WriteProcessMemory
    _CloseHandle = _kernel32.CloseHandle
    _PROCESS_ACCESS = (
        0x0010  # PROCESS_VM_READ
        | 0x0020  # PROCESS_VM_WRITE
        | 0x0008  # PROCESS_VM_OPERATION
        | 0x0400  # PROCESS_QUERY_INFORMATION
        | 0x0002  # PROCESS_CREATE_THREAD
    )

    _VirtualProtectEx = _kernel32.VirtualProtectEx
    _VirtualProtectEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _VirtualProtectEx.restype = wintypes.BOOL

    _FlushInstructionCache = _kernel32.FlushInstructionCache
    _FlushInstructionCache.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
    ]
    _FlushInstructionCache.restype = wintypes.BOOL

    _VirtualAllocEx = _kernel32.VirtualAllocEx
    _VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _VirtualAllocEx.restype = wintypes.LPVOID

    _VirtualFreeEx = _kernel32.VirtualFreeEx
    _VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    _VirtualFreeEx.restype = wintypes.BOOL

    _CreateRemoteThread = _kernel32.CreateRemoteThread
    _CreateRemoteThread.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _CreateRemoteThread.restype = wintypes.HANDLE

    _WaitForSingleObject = _kernel32.WaitForSingleObject
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD


def _module_base_for_pid(pid: int, exe_names: Tuple[str, ...]) -> Optional[int]:
    try:
        import psutil

        p = psutil.Process(pid)
        for m in p.memory_maps(grouped=False):
            path = (m.path or "").replace("\\", "/").lower()
            for ex in exe_names:
                if path.endswith("/" + ex.lower()):
                    addr = (m.addr or "").split("-")[0].strip()
                    return int(addr, 16)
    except Exception as e:
        logger.debug("module base resolve: %s", e)
    return None


class WindowsProcessMemory:
    """Attach to a Windows process and read/write its memory."""

    def __init__(self) -> None:
        self._attached: Optional[AttachedProcess] = None

    @property
    def pid(self) -> Optional[int]:
        return self._attached.pid if self._attached else None

    @property
    def handle(self) -> Optional[int]:
        return self._attached.handle if self._attached else None

    @property
    def module_base(self) -> Optional[int]:
        return self._attached.module_base if self._attached else None

    def is_available(self) -> bool:
        return sys.platform == "win32" and _kernel32 is not None

    def is_attached(self) -> bool:
        return self._attached is not None

    def is_process_running(self, process_names: Tuple[str, ...]) -> bool:
        return find_process_pid(process_names) is not None

    def close(self) -> None:
        if not self.is_available():
            self._attached = None
            return
        if self._attached and self._attached.handle:
            try:
                _CloseHandle(self._attached.handle)
            except Exception:
                pass
        self._attached = None

    def attach(
        self,
        process_names: Tuple[str, ...],
        *,
        canon_image_base: int = 0x00400000,
    ) -> Tuple[bool, Optional[str]]:
        del canon_image_base  # used by callers via rebase()
        if not self.is_available():
            return False, "Process memory hook is only supported on Windows"
        pid = find_process_pid(process_names)
        if pid is None:
            return False, f"Process not found ({', '.join(process_names)})"
        base = _module_base_for_pid(pid, process_names)
        if base is None:
            return False, "Could not resolve process module base address"
        h = _OpenProcess(_PROCESS_ACCESS, False, pid)
        if not h:
            err = ctypes.get_last_error()
            return False, f"OpenProcess failed (pid={pid}, err={err})"
        self.close()
        self._attached = AttachedProcess(pid=pid, handle=int(h), module_base=base)
        return True, None

    def ensure_attached(
        self,
        process_names: Tuple[str, ...],
        *,
        canon_image_base: int = 0x00400000,
    ) -> Tuple[bool, Optional[str]]:
        if self._attached:
            try:
                import psutil

                if not psutil.pid_exists(self._attached.pid):
                    self.close()
            except Exception:
                self.close()
        if not self._attached:
            return self.attach(process_names, canon_image_base=canon_image_base)
        return True, None

    def read(self, addr: int, size: int) -> Optional[bytes]:
        if not self._attached or addr <= 0 or size <= 0:
            return None
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = _ReadProcessMemory(
            self._attached.handle,
            ctypes.c_void_p(addr),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok or read.value != size:
            return None
        return buf.raw

    def write(self, addr: int, data: bytes) -> bool:
        if not self.is_available() or not self._attached or addr <= 0 or not data:
            return False
        written = ctypes.c_size_t(0)
        ok = _WriteProcessMemory(
            self._attached.handle,
            ctypes.c_void_p(addr),
            data,
            len(data),
            ctypes.byref(written),
        )
        return bool(ok) and written.value == len(data)

    def protect_write(
        self,
        addr: int,
        data: bytes,
        *,
        flush_icache: bool = True,
        page_protect: int = _PAGE_EXECUTE_READWRITE,
    ) -> bool:
        if not self._attached or not data:
            return False
        h = self._attached.handle
        page = addr & ~0xFFF
        end = addr + len(data)
        page_end = (end + 0xFFF) & ~0xFFF
        size = page_end - page
        if _VirtualProtectEx is None:
            return self.write(addr, data)
        old = wintypes.DWORD(0)
        if not _VirtualProtectEx(
            h,
            ctypes.c_void_p(page),
            ctypes.c_size_t(size),
            page_protect,
            ctypes.byref(old),
        ):
            return False
        try:
            ok = self.write(addr, data)
        finally:
            junk = wintypes.DWORD(0)
            _VirtualProtectEx(
                h,
                ctypes.c_void_p(page),
                ctypes.c_size_t(size),
                old.value,
                ctypes.byref(junk),
            )
        if ok and flush_icache and _FlushInstructionCache:
            _FlushInstructionCache(h, ctypes.c_void_p(page), ctypes.c_size_t(size))
        return ok

    def run_remote_shellcode(self, sc: bytes, label: str) -> bool:
        if (
            not self.is_available()
            or not self._attached
            or _VirtualAllocEx is None
            or _VirtualFreeEx is None
            or _CreateRemoteThread is None
            or _WaitForSingleObject is None
        ):
            return False
        MEM_COMMIT = 0x1000
        MEM_RESERVE = 0x2000
        MEM_RELEASE = 0x8000
        remote = None
        try:
            remote = _VirtualAllocEx(
                self._attached.handle,
                None,
                len(sc),
                MEM_COMMIT | MEM_RESERVE,
                _PAGE_EXECUTE_READWRITE,
            )
            if not remote:
                logger.debug("%s: VirtualAllocEx failed", label)
                return False
            ra = int(ctypes.cast(remote, ctypes.c_void_p).value or 0)
            if not ra or not self.write(ra, sc):
                logger.debug("%s: shellcode write failed", label)
                return False
            tid = wintypes.DWORD(0)
            h_thread = _CreateRemoteThread(
                self._attached.handle,
                None,
                0,
                ctypes.c_void_p(ra),
                None,
                0,
                ctypes.byref(tid),
            )
            if not h_thread:
                logger.debug("%s: CreateRemoteThread failed", label)
                return False
            try:
                w = _WaitForSingleObject(h_thread, 5000)
                if w != 0:
                    logger.debug("%s: WaitForSingleObject=%s", label, w)
                    return False
            finally:
                _CloseHandle(h_thread)
            return True
        except Exception as e:
            logger.debug("%s: %s", label, e)
            return False
        finally:
            if remote:
                try:
                    _VirtualFreeEx(self._attached.handle, remote, 0, MEM_RELEASE)
                except Exception:
                    pass

    def rebase(self, canon_addr: int, *, canon_image_base: int = 0x00400000) -> int:
        if not self._attached:
            return canon_addr
        return self._attached.module_base + (canon_addr - canon_image_base)

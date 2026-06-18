# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Platform-agnostic process memory access protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple


@dataclass(frozen=True)
class AttachedProcess:
    pid: int
    handle: int
    module_base: int


class ProcessMemory(Protocol):
    """Read/write attached game process memory."""

    @property
    def pid(self) -> Optional[int]: ...

    @property
    def handle(self) -> Optional[int]: ...

    @property
    def module_base(self) -> Optional[int]: ...

    def is_available(self) -> bool:
        """True when this backend can be used on the current OS."""
        ...

    def is_attached(self) -> bool:
        ...

    def is_process_running(self, process_names: Tuple[str, ...]) -> bool:
        ...

    def attach(
        self,
        process_names: Tuple[str, ...],
        *,
        canon_image_base: int = 0x00400000,
    ) -> Tuple[bool, Optional[str]]:
        ...

    def ensure_attached(
        self,
        process_names: Tuple[str, ...],
        *,
        canon_image_base: int = 0x00400000,
    ) -> Tuple[bool, Optional[str]]:
        ...

    def close(self) -> None:
        ...

    def read(self, addr: int, size: int) -> Optional[bytes]:
        ...

    def write(self, addr: int, data: bytes) -> bool:
        ...

    def protect_write(
        self,
        addr: int,
        data: bytes,
        *,
        flush_icache: bool = True,
        page_protect: int = 0x40,
    ) -> bool:
        ...

    def run_remote_shellcode(self, sc: bytes, label: str) -> bool:
        ...

    def rebase(self, canon_addr: int, *, canon_image_base: int = 0x00400000) -> int:
        ...


def find_process_pid(process_names: Tuple[str, ...]) -> Optional[int]:
    """Return PID of the first running process matching ``process_names`` (lowercase)."""
    try:
        import psutil
    except Exception:
        return None
    names = {n.lower() for n in process_names}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in names:
                return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def is_process_running(process_names: Tuple[str, ...]) -> bool:
    return find_process_pid(process_names) is not None

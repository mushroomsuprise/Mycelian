#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

MANIFEST_REPO_PATH = "packaged/manifest.json"
LOCAL_MANIFEST_RELATIVE = os.path.join("data", "update_manifest.json")
_ACTIONS = frozenset({"replace", "merge", "if-missing"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class UpdateSyncError(Exception):
    """A release could not be staged. The running app is left in place."""


class MissingReleaseFile(UpdateSyncError):
    """The release manifest names a path GitHub does not have."""


class UpdateCancelled(UpdateSyncError):
    """The user cancelled before any installed file was changed."""


def can_apply_updates() -> bool:
    """File sync is for a frozen install. A source checkout is never modified."""
    return bool(getattr(sys, "frozen", False))


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def install_layout() -> tuple[Path, Optional[Path]]:
    """Return ``(data_root, bundle_root)``.

    Shared files land in ``data_root`` (the folder that contains the
    executable, or ``Contents/MacOS`` inside a macOS bundle). ``bundle_root``
    is the ``.app`` directory on macOS and ``None`` everywhere else.
    """
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        bundle = next((parent for parent in exe.parents if parent.suffix == ".app"), None)
        if bundle is None:
            raise UpdateSyncError("Could not find the Mycelian.app bundle.")
        return bundle / "Contents" / "MacOS", bundle
    return exe.parent, None


def local_manifest_path() -> Path:
    from .path_utils import get_data_path

    return Path(get_data_path(LOCAL_MANIFEST_RELATIVE))


def safe_relative(value: str) -> Path:
    """Reject absolute paths and any ``..`` segment."""
    if not isinstance(value, str) or not value.strip():
        raise UpdateSyncError("Update manifest path is empty.")
    text = value.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise UpdateSyncError(f"Refusing absolute update path: {value}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise UpdateSyncError(f"Refusing unsafe update path: {value}")
    return Path(*pure.parts)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def destination_for(
    entry: Dict[str, Any],
    data_root: Path,
    bundle_root: Optional[Path],
) -> Path:
    relative = safe_relative(str(entry.get("install", "")))
    if entry.get("bundle"):
        if bundle_root is None:
            raise UpdateSyncError(
                "The manifest contains a macOS bundle file, but this install is not an app bundle."
            )
        root = bundle_root
    else:
        root = data_root
    dest = (root / relative).resolve()
    if not _is_within(dest, root):
        raise UpdateSyncError(
            f"Refusing to write outside the install folder: {entry.get('install')}"
        )
    return dest


def _validate_entry(entry: Dict[str, Any]) -> None:
    if not isinstance(entry, dict):
        raise UpdateSyncError("Update manifest contains an entry that is not an object.")
    action = entry.get("action")
    if action not in _ACTIONS:
        raise UpdateSyncError(f"Update manifest has an unknown action: {action!r}")
    safe_relative(str(entry.get("path", "")))
    safe_relative(str(entry.get("install", "")))
    sha = entry.get("sha256")
    if not isinstance(sha, str) or _SHA256_RE.fullmatch(sha) is None:
        raise UpdateSyncError(
            f"Update manifest hash is invalid for {entry.get('path')!r}."
        )
    size = entry.get("size")
    if type(size) is not int or size < 0:
        raise UpdateSyncError(
            f"Update manifest size is invalid for {entry.get('path')!r}."
        )


def entries_for_platform(manifest: Dict[str, Any], platform: str) -> List[Dict[str, Any]]:
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise UpdateSyncError("Update manifest is missing a file list.")
    selected: List[Dict[str, Any]] = []
    for entry in files:
        _validate_entry(entry)
        entry_os = entry.get("os")
        if entry_os and entry_os != platform:
            continue
        selected.append(entry)
    if not any(entry.get("os") == platform for entry in selected):
        raise UpdateSyncError(
            f"This release has no packaged {platform} build yet."
        )
    return selected


def load_local_manifest(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"version": "", "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable update manifest %s: %s", path, exc)
        return {"version": "", "files": {}}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {"version": "", "files": {}}
    return {"version": str(data.get("version") or ""), "files": files}


def plan_changes(
    entries: List[Dict[str, Any]],
    local_manifest: Dict[str, Any],
    dest_exists: Callable[[Dict[str, Any]], bool],
    installed_unchanged: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """Decide downloads and deletions from manifest hashes.

    ``if-missing`` files are downloaded only when the destination is absent.
    A ``replace`` file is left alone when the installed copy already matches
    the release, even on the first update. ``merge`` files are compared by
    the last applied release hash, because the installed JSON contains user
    values. Only ``replace`` paths that disappeared from the release are deleted.
    """
    local_files = local_manifest.get("files") if isinstance(local_manifest, dict) else None
    if not isinstance(local_files, dict):
        local_files = {}

    downloads: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["action"] == "if-missing":
            if not dest_exists(entry):
                downloads.append(entry)
            continue
        previous = local_files.get(entry["install"])
        previous_sha = previous.get("sha256") if isinstance(previous, dict) else None
        if previous_sha == entry["sha256"]:
            continue
        if (
            entry["action"] == "replace"
            and installed_unchanged is not None
            and installed_unchanged(entry)
        ):
            continue
        downloads.append(entry)

    remote_installs = {entry["install"] for entry in entries}
    deletes: List[Dict[str, Any]] = []
    for install, meta in local_files.items():
        if not isinstance(meta, dict) or meta.get("action") != "replace":
            continue
        if install in remote_installs:
            continue
        deletes.append({"install": install, "bundle": bool(meta.get("bundle"))})

    record = {
        entry["install"]: {
            "sha256": entry["sha256"],
            "action": entry["action"],
            "bundle": bool(entry.get("bundle")),
        }
        for entry in entries
    }
    return {"downloads": downloads, "deletes": deletes, "files": record}


def release_file_url(owner: str, repo: str, tag: str, path: str) -> str:
    safe_tag = quote(tag, safe="")
    safe_path = quote(path.replace("\\", "/"), safe="/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{safe_tag}/{safe_path}"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_matches(data: bytes, expected: str) -> bool:
    """True when ``data`` is the release file, ignoring CR/LF differences in text.

    The manifest hash may be the Windows working copy while GitHub serves the
    LF blob stored in git. Both describe the same text file.
    """
    if _sha256_bytes(data) == expected:
        return True
    if b"\0" in data:
        return False
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if _sha256_bytes(normalized) == expected:
        return True
    return _sha256_bytes(normalized.replace(b"\n", b"\r\n")) == expected


def file_matches(path: Path, expected: str) -> bool:
    """True when an installed file already has the release contents."""
    if not path.is_file():
        return False
    if _sha256_file(path) == expected:
        return True
    try:
        size = path.stat().st_size
    except OSError:
        return False
    # Keep newline folding off large binaries. Text payload files are small.
    if size > 8 * 1024 * 1024:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return content_matches(data, expected)


def _directory_writable(path: Path) -> bool:
    if not path.is_dir():
        return False
    probe = path / f".mycelian_write_test_{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            pass
        return False


def _report(state: Dict[str, Any], phase: str, fraction: float, speed: float, label: str) -> None:
    state["phase"] = phase
    state["progress"] = max(0.0, min(1.0, float(fraction)))
    state["speed_bps"] = float(speed or 0.0)
    state["label"] = label


def _cancelled(cancel_event: Optional[threading.Event]) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def merge_staged_templates(
    staging: Path,
    downloads: List[Dict[str, Any]],
    data_root: Path,
    bundle_root: Optional[Path],
) -> None:
    """Fold existing user values into staged template JSON before it is installed."""
    from merge_template_configs import merge_config

    for entry in downloads:
        if entry.get("action") != "merge":
            continue
        staged = _staged_file(staging, entry)
        if not staged.is_file():
            raise UpdateSyncError(f"Staged template is missing: {entry.get('path')}")
        dest = destination_for(entry, data_root, bundle_root)
        if not dest.is_file():
            continue
        try:
            new_obj = json.loads(staged.read_text(encoding="utf-8"))
            old_obj = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateSyncError(
                f"Could not read template config {dest.name}: {exc}"
            ) from exc
        merged = merge_config(new_obj, old_obj)
        staged.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def _is_application_binary(entry: Dict[str, Any]) -> bool:
    install = str(entry.get("install") or "").replace("\\", "/")
    if entry.get("bundle"):
        return install == "Contents/MacOS/Mycelian"
    return install in {"Mycelian.exe", "Mycelian"}


def _staged_file(staging: Path, entry: Dict[str, Any]) -> Path:
    kind = "bundle" if entry.get("bundle") else "files"
    dest = staging / kind / safe_relative(str(entry["install"]))
    if not _is_within(dest, staging):
        raise UpdateSyncError(f"Refusing to stage outside the download folder: {entry['install']}")
    return dest


async def _download_file(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
    expected_sha: str,
    progress: Callable[[int], None],
    cancel_event: Optional[threading.Event],
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    try:
        async with session.get(url) as response:
            if response.status == 404:
                raise MissingReleaseFile(dest.name)
            if response.status != 200:
                body = await response.text()
                detail = body.strip().splitlines()[0][:200] if body.strip() else ""
                raise UpdateSyncError(
                    f"Download failed ({response.status}) for {dest.name}. {detail}".strip()
                )
            with open(partial, "wb") as handle:
                async for chunk in response.content.iter_chunked(64 * 1024):
                    if _cancelled(cancel_event):
                        raise UpdateCancelled("Update cancelled.")
                    handle.write(chunk)
                    progress(len(chunk))
        if not file_matches(partial, expected_sha):
            raise UpdateSyncError(
                f"Downloaded {dest.name} did not match the release manifest."
            )
        partial.replace(dest)
    finally:
        if partial.exists() and not dest.exists():
            try:
                partial.unlink()
            except OSError:
                pass


async def _fetch_manifest(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    tag: str,
) -> Dict[str, Any]:
    url = release_file_url(owner, repo, tag, MANIFEST_REPO_PATH)
    async with session.get(url) as response:
        if response.status != 200:
            raise UpdateSyncError(
                f"Could not read the release file list ({response.status})."
            )
        try:
            data = await response.json(content_type=None)
        except (json.JSONDecodeError, aiohttp.ContentTypeError) as exc:
            raise UpdateSyncError("The release file list was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise UpdateSyncError("The release file list was not valid JSON.")
    return data


def _plan_line(op: str, *args: str) -> str:
    for arg in args:
        if "\t" in arg or "\n" in arg or "\r" in arg:
            raise UpdateSyncError(f"Refusing an update path with unusual characters: {arg!r}")
    return "\t".join((op, *args))


def _relaunch_target(data_root: Path, bundle_root: Optional[Path]) -> tuple[str, Path]:
    if sys.platform == "win32":
        return "windows", data_root / "Mycelian.exe"
    if sys.platform == "darwin":
        if bundle_root is None:
            raise UpdateSyncError("Could not find the Mycelian.app bundle to restart.")
        return "macos", bundle_root
    return "linux", data_root / "Mycelian"


def _write_apply_plan(
    staging: Path,
    copies: List[tuple[Path, Path]],
    deletes: List[Path],
    chmod_paths: List[Path],
    launch_kind: str,
    launch_target: Path,
) -> None:
    lines = [
        _plan_line("PID", str(os.getpid())),
        _plan_line("STAGING", str(staging)),
        _plan_line("LAUNCH", launch_kind, str(launch_target)),
    ]
    for src, dest in copies:
        lines.append(_plan_line("COPY", str(src), str(dest)))
    for path in deletes:
        lines.append(_plan_line("DELETE", str(path)))
    for path in chmod_paths:
        lines.append(_plan_line("CHMOD", str(path)))
    text = "\n".join(lines) + "\n"
    plan_path = staging / "apply_plan.txt"
    if sys.platform == "win32":
        # PowerShell 5 reads UTF-8 reliably when the file has a BOM.
        plan_path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    else:
        plan_path.write_text(text, encoding="utf-8", newline="\n")


def _windows_helper_script() -> str:
    return r"""$ErrorActionPreference = "Stop"
$planPath = Join-Path $PSScriptRoot "apply_plan.txt"
$log = Join-Path $env:TEMP "mycelian_update_apply.log"
function Write-Log([string]$Message) {
    Add-Content -LiteralPath $log -Value ("{0} {1}" -f (Get-Date -Format o), $Message)
}
Set-Content -LiteralPath (Join-Path $PSScriptRoot "helper.pid") -Value $PID -Encoding ascii
Write-Log "Helper started pid=$PID"

$ops = New-Object System.Collections.Generic.List[string]
Get-Content -LiteralPath $planPath -Encoding UTF8 | ForEach-Object { $ops.Add($_) }

$parent = 0
$staging = ""
$launchTarget = ""
foreach ($line in $ops) {
    if (-not $line) { continue }
    $parts = $line -split "`t", 3
    if ($parts[0] -eq "PID" -and $parts.Length -ge 2) { $parent = [int]$parts[1] }
    elseif ($parts[0] -eq "STAGING" -and $parts.Length -ge 2) { $staging = $parts[1] }
    elseif ($parts[0] -eq "LAUNCH" -and $parts.Length -ge 3) { $launchTarget = $parts[2] }
}

$success = $false
try {
    if ($parent -gt 0) {
        while (Get-Process -Id $parent -ErrorAction SilentlyContinue) {
            Start-Sleep -Seconds 1
        }
    }
    Start-Sleep -Seconds 2
    foreach ($line in $ops) {
        if (-not $line) { continue }
        $parts = $line -split "`t", 3
        $op = $parts[0]
        if ($op -eq "COPY" -and $parts.Length -ge 3) {
            $src = $parts[1]
            $dest = $parts[2]
            $parentDir = Split-Path -Parent $dest
            if ($parentDir) { New-Item -ItemType Directory -Force -Path $parentDir | Out-Null }
            $tmp = "$dest.mycelian-new"
            Copy-Item -LiteralPath $src -Destination $tmp -Force
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
            Move-Item -LiteralPath $tmp -Destination $dest -Force
        }
        elseif ($op -eq "DELETE" -and $parts.Length -ge 2) {
            if (Test-Path -LiteralPath $parts[1]) { Remove-Item -LiteralPath $parts[1] -Force }
        }
    }
    if ($launchTarget) {
        $work = Split-Path -Parent $launchTarget
        Start-Process -FilePath $launchTarget -WorkingDirectory $work
    }
    $success = $true
    Write-Log "Update applied"
}
catch {
    Write-Log $_.Exception.Message
    $elevatedFlag = Join-Path $PSScriptRoot "elevated.flag"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin -and -not (Test-Path -LiteralPath $elevatedFlag)) {
        Set-Content -LiteralPath $elevatedFlag -Value "1" -Encoding ascii
        $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`""
        Start-Process -FilePath "powershell.exe" -ArgumentList $arg -Verb RunAs
        exit 0
    }
    exit 1
}

if ($success -and $staging -and (Test-Path -LiteralPath $staging)) {
    $escaped = $staging.Replace("'", "''")
    $cleanup = "Start-Sleep -Seconds 2; Remove-Item -LiteralPath '$escaped' -Recurse -Force"
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-Command", $cleanup) -WindowStyle Hidden | Out-Null
}
"""


def _unix_helper_script() -> str:
    return """#!/bin/bash
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
PLAN="$DIR/apply_plan.txt"
LOG="${TMPDIR:-/tmp}/mycelian_update_apply.log"
echo $$ > "$DIR/helper.pid"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"; }
log "Helper started pid=$$"

parent=0
staging=""
launch_kind=""
launch_target=""
while IFS=$'\\t' read -r op a b || [[ -n "${op:-}" ]]; do
    case "$op" in
        PID) parent="$a" ;;
        STAGING) staging="$a" ;;
        LAUNCH) launch_kind="$a"; launch_target="$b" ;;
    esac
done < "$PLAN"

if [[ "${parent:-0}" -gt 0 ]]; then
    while kill -0 "$parent" 2>/dev/null; do
        sleep 1
    done
fi
sleep 2

set -e
while IFS=$'\\t' read -r op a b || [[ -n "${op:-}" ]]; do
    case "$op" in
        COPY)
            mkdir -p "$(dirname "$b")"
            tmp="$b.mycelian-new"
            cp -f "$a" "$tmp"
            mv -f "$tmp" "$b"
            ;;
        DELETE)
            rm -f "$a"
            ;;
        CHMOD)
            chmod +x "$a"
            ;;
    esac
done < "$PLAN"

if [[ -n "$launch_target" ]]; then
    if [[ "$launch_kind" == "macos" ]]; then
        open "$launch_target"
    else
        cd "$(dirname "$launch_target")"
        nohup "$launch_target" >/dev/null 2>&1 &
    fi
fi
log "Update applied"
if [[ -n "$staging" ]]; then
    rm -rf "$staging"
fi
"""


def _sanitized_env() -> dict:
    env = os.environ.copy()
    for name in ("_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"):
        env.pop(name, None)
    return env


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _windows_creationflags(*, breakaway: bool) -> int:
    flags = _CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS | _CREATE_NO_WINDOW
    if breakaway:
        flags |= _CREATE_BREAKAWAY_FROM_JOB
    return flags


def _popen_hidden(args: list, log_path: Path) -> subprocess.Popen:
    log_handle = open(log_path, "a", encoding="utf-8")
    try:
        kwargs: Dict[str, Any] = dict(
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            env=_sanitized_env(),
            close_fds=True,
        )
        if sys.platform == "win32":
            try:
                return subprocess.Popen(
                    args,
                    creationflags=_windows_creationflags(breakaway=True),
                    **kwargs,
                )
            except OSError:
                logger.debug("CREATE_BREAKAWAY_FROM_JOB rejected; retrying update helper")
                return subprocess.Popen(
                    args,
                    creationflags=_windows_creationflags(breakaway=False),
                    **kwargs,
                )
        kwargs["start_new_session"] = True
        return subprocess.Popen(args, **kwargs)
    finally:
        log_handle.close()


def _write_helper_script(staging: Path) -> Path:
    if sys.platform == "win32":
        path = staging / "apply.ps1"
        path.write_bytes(b"\xef\xbb\xbf" + _windows_helper_script().encode("utf-8"))
        return path
    path = staging / "apply.sh"
    path.write_text(_unix_helper_script(), encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _wait_for_helper_pid(pid_path: Path, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_path.is_file():
            text = pid_path.read_text(encoding="ascii", errors="ignore").strip()
            try:
                pid = int(text)
            except ValueError:
                pid = 0
            if _pid_alive(pid):
                return pid
        time.sleep(0.2)
    return 0


def launch_apply_helper(staging: Path, *, elevate: bool) -> int:
    """Start the detached apply script and return its pid."""
    script = _write_helper_script(staging)
    log_path = staging / "launch.log"
    pid_path = staging / "helper.pid"
    if sys.platform == "win32":
        if elevate:
            vbs_path = staging / "elevate.vbs"
            script_arg = str(script).replace('"', '""')
            vbs_path.write_text(
                "\r\n".join(
                    [
                        'Set shell = CreateObject("Shell.Application")',
                        "shell.ShellExecute \"powershell.exe\", "
                        f"\"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
                        f'-File ""{script_arg}""\", "", "runas", 0',
                        "",
                    ]
                ),
                encoding="utf-8",
                newline="\r\n",
            )
            _popen_hidden(["wscript.exe", str(vbs_path)], log_path)
            timeout = 120.0
        else:
            _popen_hidden(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(script),
                ],
                log_path,
            )
            timeout = 15.0
    else:
        if elevate:
            raise UpdateSyncError(
                "Mycelian cannot write to its install folder. "
                "Run it with permission to update that folder and try again."
            )
        bash = "/bin/bash" if Path("/bin/bash").is_file() else "bash"
        _popen_hidden([bash, str(script)], log_path)
        timeout = 15.0

    pid = _wait_for_helper_pid(pid_path, timeout)
    if pid <= 0:
        detail = ""
        if log_path.is_file():
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-500:].strip()
        message = "The update helper did not start."
        if elevate:
            message = "Update permission was not granted, so Mycelian was not changed."
        if detail:
            message = f"{message} {detail}"
        raise UpdateSyncError(message)
    logger.info("Update helper started pid=%s elevate=%s", pid, elevate)
    return pid


async def sync_release(
    *,
    tag: str,
    owner: str,
    repo: str,
    state: Dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
) -> Optional[int]:
    """Download a release into a staging folder and start the apply helper.

    Returns the helper pid when Mycelian should exit so the helper can replace
    files and relaunch. Returns ``None`` when the installed files already match
    the release.
    """
    if not can_apply_updates():
        raise UpdateSyncError(
            "This checkout is running from source. Install a packaged build to apply updates."
        )
    if not tag:
        raise UpdateSyncError("The release tag is missing.")

    data_root, bundle_root = install_layout()
    writable = _directory_writable(data_root) and (
        bundle_root is None or _directory_writable(bundle_root)
    )
    elevate = False
    if not writable:
        if sys.platform != "win32":
            raise UpdateSyncError(
                "Mycelian cannot write to its install folder. "
                "Run it with permission to update that folder and try again."
            )
        elevate = True

    _report(state, "listing", 0.0, 0.0, "Reading the release file list...")
    if _cancelled(cancel_event):
        raise UpdateCancelled("Update cancelled.")

    timeout = aiohttp.ClientTimeout(connect=30, sock_read=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        manifest = await _fetch_manifest(session, owner, repo, tag)
        entries = entries_for_platform(manifest, current_platform())
        local = load_local_manifest(local_manifest_path())

        def dest_exists(entry: Dict[str, Any]) -> bool:
            return destination_for(entry, data_root, bundle_root).exists()

        def installed_unchanged(entry: Dict[str, Any]) -> bool:
            return file_matches(
                destination_for(entry, data_root, bundle_root),
                str(entry["sha256"]),
            )

        planned = plan_changes(entries, local, dest_exists, installed_unchanged)
        downloads: List[Dict[str, Any]] = planned["downloads"]
        if _cancelled(cancel_event):
            raise UpdateCancelled("Update cancelled.")

        if not downloads and not planned["deletes"]:
            _report(state, "current", 1.0, 0.0, "Installed files already match this release.")
            return None

        staging = Path(tempfile.mkdtemp(prefix="mycelian_update_"))
        try:
            total = sum(int(entry["size"]) for entry in downloads) or 1
            downloaded = 0
            window_start = time.perf_counter()
            window_bytes = 0
            speed = 0.0
            fetched: List[Dict[str, Any]] = []
            skipped_installs: set[str] = set()
            for index, entry in enumerate(downloads, start=1):
                if _cancelled(cancel_event):
                    raise UpdateCancelled("Update cancelled.")
                name = Path(str(entry["install"])).name
                label = f"Downloading {name} ({index} of {len(downloads)})"

                def on_chunk(nbytes: int, label: str = label) -> None:
                    nonlocal downloaded, window_start, window_bytes, speed
                    downloaded += nbytes
                    window_bytes += nbytes
                    now = time.perf_counter()
                    elapsed = now - window_start
                    if elapsed >= 0.5:
                        speed = window_bytes / elapsed
                        window_start = now
                        window_bytes = 0
                    elif downloaded > 0 and elapsed > 0:
                        speed = downloaded / max(now - (window_start - elapsed), 0.001)
                    _report(state, "downloading", downloaded / total, speed, label)

                _report(state, "downloading", downloaded / total, speed, label)
                dest = _staged_file(staging, entry)
                try:
                    await _download_file(
                        session,
                        release_file_url(owner, repo, tag, str(entry["path"])),
                        dest,
                        str(entry["sha256"]),
                        on_chunk,
                        cancel_event,
                    )
                except MissingReleaseFile:
                    if _is_application_binary(entry):
                        raise UpdateSyncError(
                            f"The release is missing the application ({entry['path']})."
                        ) from None
                    logger.warning(
                        "Release file is not in the repository; skipping %s",
                        entry["path"],
                    )
                    skipped_installs.add(str(entry["install"]))
                    downloaded += int(entry["size"])
                    continue
                fetched.append(entry)

            if not fetched and not planned["deletes"]:
                shutil.rmtree(staging, ignore_errors=True)
                _report(
                    state,
                    "current",
                    1.0,
                    0.0,
                    "Installed files already match this release.",
                )
                return None

            _report(state, "merging", 1.0, 0.0, "Merging template configurations...")
            if _cancelled(cancel_event):
                raise UpdateCancelled("Update cancelled.")
            await asyncio.to_thread(
                merge_staged_templates, staging, fetched, data_root, bundle_root
            )

            record = {
                "version": str(manifest.get("version") or ""),
                "files": {
                    install: meta
                    for install, meta in planned["files"].items()
                    if install not in skipped_installs
                },
            }
            staged_manifest = staging / "update_manifest.json"
            staged_manifest.write_text(
                json.dumps(record, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            copies = [
                (_staged_file(staging, entry), destination_for(entry, data_root, bundle_root))
                for entry in fetched
            ]
            copies.append((staged_manifest, local_manifest_path()))
            deletes: List[Path] = []
            copy_dests = {dest for _, dest in copies}
            for item in planned["deletes"]:
                path = destination_for(
                    {"install": item["install"], "bundle": item["bundle"]},
                    data_root,
                    bundle_root,
                )
                if path not in copy_dests:
                    deletes.append(path)
            launch_kind, launch_target = _relaunch_target(data_root, bundle_root)
            chmod_paths: List[Path] = []
            if sys.platform != "win32":
                if sys.platform == "darwin" and bundle_root is not None:
                    chmod_paths.append(bundle_root / "Contents" / "MacOS" / "Mycelian")
                else:
                    chmod_paths.append(launch_target)
            _write_apply_plan(
                staging,
                copies,
                deletes,
                chmod_paths,
                launch_kind,
                launch_target,
            )
            if elevate:
                _report(
                    state,
                    "restarting",
                    1.0,
                    0.0,
                    "Waiting for permission to replace installed files...",
                )
            else:
                _report(state, "restarting", 1.0, 0.0, "Restarting Mycelian...")
            pid = await asyncio.to_thread(launch_apply_helper, staging, elevate=elevate)
            return pid
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

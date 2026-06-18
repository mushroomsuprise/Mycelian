#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT

"""Add or update MIT license headers in first-party Python source files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

COPYRIGHT_LINE = "# Copyright (c) 2024-2026 Mycelian"
SPDX_LINE = "# SPDX-License-Identifier: MIT"
COMPACT_HEADER = f"{COPYRIGHT_LINE}\n{SPDX_LINE}\n"

COPYRIGHT_IN_DOCSTRING = re.compile(
    r"Copyright \(c\) 20\d{2}(?:[–-]20\d{2})? Mycelian"
)

ROOT_ENTRYPOINTS = ("main.py", "build.py", "pause_alerts.py", "merge_template_configs.py")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def iter_python_files(root: Path):
    for name in ROOT_ENTRYPOINTS:
        path = root / name
        if path.is_file():
            yield path
    for pattern in ("modules/**/*.py", "scripts/**/*.py", "tests/**/*.py"):
        yield from root.glob(pattern)


def has_compact_header(text: str) -> bool:
    return "SPDX-License-Identifier: MIT" in text and "Copyright (c)" in text


def has_full_mit_block(text: str) -> bool:
    return text.lstrip().startswith('"""') and "MIT License" in text[:800]


def update_full_mit_copyright(text: str) -> tuple[str, bool]:
    updated, count = re.subn(
        r"Copyright \(c\) 20\d{2}(?:[–-]20\d{2})? Mycelian",
        "Copyright (c) 2024-2026 Mycelian",
        text,
        count=1,
    )
    return updated, count > 0


def insert_compact_header(text: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
        if insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at = 2
    return "".join(lines[:insert_at]) + COMPACT_HEADER + "".join(lines[insert_at:])


def process_file(path: Path) -> str | None:
    original = path.read_text(encoding="utf-8")
    text = original

    if has_full_mit_block(text):
        text, changed = update_full_mit_copyright(text)
        if changed and text != original:
            path.write_text(text, encoding="utf-8", newline="\n")
            return "updated_full_mit"
        return None

    if has_compact_header(text):
        text, changed = update_full_mit_copyright(text)
        if changed and text != original:
            path.write_text(text, encoding="utf-8", newline="\n")
            return "updated_compact"
        return None

    if "Copyright (c)" in text and "Mycelian" in text:
        text, changed = update_full_mit_copyright(text)
        if changed:
            path.write_text(text, encoding="utf-8", newline="\n")
            return "updated_other"
        return None

    new_text = insert_compact_header(text)
    if new_text != original:
        path.write_text(new_text, encoding="utf-8", newline="\n")
        return "added_compact"
    return None


def main() -> int:
    root = project_root()
    results: dict[str, list[str]] = {}

    for path in sorted(iter_python_files(root)):
        if path.name == "add_license_headers.py":
            continue
        action = process_file(path)
        if action:
            results.setdefault(action, []).append(str(path.relative_to(root)))

    for action, files in sorted(results.items()):
        print(f"\n{action} ({len(files)}):")
        for f in files:
            print(f"  {f}")

    total = sum(len(v) for v in results.values())
    print(f"\nTotal files changed: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

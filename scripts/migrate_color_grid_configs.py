#!/usr/bin/env python3
"""One-off migration: select + color_grid -> type color (keeps options for presets)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "templates" / "template_configs"


def _is_transparent_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    raw = value.strip().lower()
    if raw == "transparent":
        return True
    if raw.startswith("rgba(") and raw.endswith(")"):
        parts = [p.strip() for p in raw[5:-1].split(",")]
        if len(parts) >= 4:
            try:
                return float(parts[3]) < 1.0
            except ValueError:
                pass
    return False


def migrate_element(element: dict) -> bool:
    if element.get("type") != "select":
        return False
    if element.get("display") != "color_grid":
        return False

    element["type"] = "color"
    element.pop("display", None)
    value = element.get("value")
    element["transparent"] = _is_transparent_value(value)
    return True


def migrate_file(path: Path) -> int:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    changed = 0
    for element in data.get("elements", []):
        if migrate_element(element):
            changed += 1

    if changed:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4)
            fh.write("\n")

    return changed


def main() -> int:
    total = 0
    files = 0
    for path in sorted(CONFIG_DIR.glob("*.json")):
        count = migrate_file(path)
        if count:
            print(f"{path.name}: migrated {count} element(s)")
            total += count
            files += 1
    print(f"Done — {total} element(s) across {files} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

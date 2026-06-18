# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

CALL_PATTERN = re.compile(
    r"\bprint\s*\(|\b(?:logging|logger|self\.logger|log)\s*\.\s*"
    r"(?:debug|info|warning|warn|error|exception|critical|log)\s*\(",
)

# Match single-line string literals with optional prefixes (f, r, u, b in any order up to 3 chars)
_DQ = r'(?P<prefix>[fFrRuUbB]{0,3})"(?P<body_d>(?:[^"\\]|\\.)*)"'
_SQ = r"(?P<prefix>[fFrRuUbB]{0,3})'(?:[^'\\]|\\.)*'"

# Separate patterns for replacement so we can access appropriate groups
DOUBLE_QUOTE_STR_RE = re.compile(r'(?P<prefix>[fFrRuUbB]{0,3})"(?P<body>(?:[^"\\]|\\.)*)"')
SINGLE_QUOTE_STR_RE = re.compile(r"(?P<prefix>[fFrRuUbB]{0,3})'(?:[^'\\]|\\.)*'")

# HTML-like content detection inside a literal
HTML_LIKE_RE = re.compile(r"<[^>]+>")

# Emoji/ZWJ/variation selector/keycap combining marks
EMOJI_RE = re.compile(
    "["  # character class start
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, supplemental symbols, etc.
    "\u2600-\u27BF"          # misc symbols + dingbats
    "\uFE0F"                 # variation selector-16
    "\u200D"                 # zero width joiner
    "\u20E3"                 # keycap combining
    "]",
    re.UNICODE,
)


def contains_html_like(s: str) -> bool:
    return bool(HTML_LIKE_RE.search(s))


def remove_emojis(text: str) -> str:
    return EMOJI_RE.sub("", text)


def _clean_line(line: str) -> Tuple[str, bool]:
    """Clean a single line if it looks like a print/logger call.

    Returns (new_line, changed?).
    """
    if not CALL_PATTERN.search(line):
        return line, False

    original = line

    def _replace_double(m: re.Match) -> str:
        prefix = m.group("prefix") or ""
        body = m.group("body")
        # Skip if looks like HTML
        if contains_html_like(body):
            return m.group(0)
        cleaned_body = remove_emojis(body)
        return f'{prefix}"{cleaned_body}"'

    # First handle double-quoted strings where we can access body
    line = DOUBLE_QUOTE_STR_RE.sub(_replace_double, line)

    # Then handle single-quoted strings (can't access body easily with named group)
    # Use a second-pass matcher that extracts body using indexes
    def _replace_single(m: re.Match) -> str:
        s = m.group(0)
        # Identify prefix length (0-3), first quote is at len(prefix)
        prefix_len = 0
        for ch in s[:3]:
            if ch in "fFrRuUbB":
                prefix_len += 1
            else:
                break
        # s looks like <prefix>'<body>'
        try:
            first_quote_idx = prefix_len
            last_quote_idx = len(s) - 1
            body = s[first_quote_idx + 1:last_quote_idx]
        except Exception:
            return s
        if contains_html_like(body):
            return s
        cleaned_body = remove_emojis(body)
        if cleaned_body == body:
            return s
        return s[:first_quote_idx + 1] + cleaned_body + s[last_quote_idx:]

    line = SINGLE_QUOTE_STR_RE.sub(_replace_single, line)

    return line, (line != original)


def iter_target_files(root: Path) -> Iterable[Path]:
    # Root-level .py files (non-recursive)
    for p in sorted(root.glob("*.py")):
        yield p
    # modules/**/*.py recursively
    modules_dir = root / "modules"
    if modules_dir.is_dir():
        for p in sorted(modules_dir.rglob("*.py")):
            yield p


def process_file(path: Path) -> Tuple[bool, int]:
    changed = False
    changes = 0
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fall back to system default if UTF-8 fails
        text = path.read_text(errors="ignore")

    lines = text.splitlines(keepends=True)
    new_lines: List[str] = []
    for line in lines:
        new_line, did_change = _clean_line(line)
        if did_change:
            changed = True
            changes += 1
        new_lines.append(new_line)

    if changed:
        new_text = "".join(new_lines)
        path.write_text(new_text, encoding="utf-8")
    return changed, changes


def verify_file(path: Path) -> List[Tuple[int, str]]:
    """Return list of (line_no, line) that still has emoji in print/logger."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(errors="ignore")

    results: List[Tuple[int, str]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if CALL_PATTERN.search(line) and EMOJI_RE.search(line):
            # If the emoji is inside a string that also contains HTML-like tags, skip reporting
            # Quick heuristic: if any string literal on the line has HTML-like content, don't flag
            if HTML_LIKE_RE.search(line):
                continue
            results.append((idx, line))
    return results


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove emojis from print/logger string literals.")
    parser.add_argument("--verify-only", action="store_true", help="Only verify and report remaining instances.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    targets = list(iter_target_files(root))

    modified: List[Path] = []
    total_changes = 0

    if args.verify_only:
        outstanding = 0
        for p in targets:
            issues = verify_file(p)
            if issues:
                print(f"VERIFY: {p}")
                for ln, content in issues:
                    print(f"  L{ln}: {content}")
                outstanding += len(issues)
        print(f"Verification complete. Outstanding lines: {outstanding}")
        return 0 if outstanding == 0 else 1

    for p in targets:
        changed, changes = process_file(p)
        total_changes += changes
        if changed:
            modified.append(p)

    # Persist modified file list for follow-up tooling
    logs_dir = root / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "emoji_cleanup_modified.txt").write_text(
            "\n".join(str(p) for p in modified), encoding="utf-8"
        )
    except Exception:
        pass

    print(f"Processed {len(targets)} files. Modified {len(modified)} files. Line edits: {total_changes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())



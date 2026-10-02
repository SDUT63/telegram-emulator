#!/usr/bin/env python3
"""Fail CI if obvious MAX/API credentials are committed to the source tree."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
EXAMPLE_SUFFIXES = {".md", ".rst", ".txt"}
PATTERNS = (
    re.compile(r"(?im)^\s*MAX_BOT_TOKEN\s*=\s*([^\s#][^\r\n]*)$") ,
    re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
)
PLACEHOLDERS = {"...", "…", "<token>", "<your-token>", "<your_token>", "changeme", "change-me", "example", "dummy", "test"}


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip('"\'').lower()
    if not normalized or normalized in PLACEHOLDERS:
        return True
    return normalized.startswith("<") and normalized.endswith(">")


def files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        result.append(path)
    return result


def main() -> int:
    hits: list[tuple[Path, int]] = []
    for path in files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        is_document = path.suffix.lower() in EXAMPLE_SUFFIXES
        for lineno, line in enumerate(text.splitlines(), 1):
            match = PATTERNS[0].search(line)
            if match and _is_placeholder(match.group(1)):
                continue
            if is_document and "example" in line.lower() and any(pattern.search(line) for pattern in PATTERNS):
                continue
            if any(pattern.search(line) for pattern in PATTERNS[1:]):
                hits.append((path.relative_to(ROOT), lineno))
                continue
            if match:
                hits.append((path.relative_to(ROOT), lineno))
    if hits:
        print("Potential committed secret(s) detected:")
        for path, lineno in hits:
            print(f"  {path}:{lineno}")
        print("Use MAX_BOT_TOKEN only via the environment or local ignored token.txt.")
        return 1
    print("[ok] no obvious committed MAX/API bearer secrets found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

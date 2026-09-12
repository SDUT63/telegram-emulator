#!/usr/bin/env python3
"""Fail-closed audit for MAX outbound delivery paths.

The production bot must not accidentally regain a direct MAX send path while
refactors are in progress. This is intentionally a static AST check rather
than a grep: comments and strings do not count, while aliases and calls made
through an object do.

The durable worker is deliberately excluded: it is the one component whose
job is to perform the actual external MAX delivery. The application and
launchers must instead create durable outbound intents.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRODUCTION_FILES = {
    ROOT / "max_bot.py",
    ROOT / "max_webhook.py",
    ROOT / "run_max_postgres.py",
    ROOT / "run_max_webhook.py",
    ROOT / "production_outbox.py",
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def audit(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "send_message":
                    problems.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: assignment to send_message"
                    )
        if isinstance(node, ast.Call) and _call_name(node) == "send_message":
            problems.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: direct send_message call"
            )
    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(PRODUCTION_FILES):
        if path.exists():
            problems.extend(audit(path))
    if problems:
        print("Outbound path audit: FAIL")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("Outbound path audit: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fail-closed audit for MAX outbound delivery paths."""
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
    ROOT / "durable_outbox_worker.py",
    ROOT / "max_outbound_transport.py",
}
EXPLICIT_TRANSPORT = ROOT / "max_outbound_transport.py"


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
            if path != EXPLICIT_TRANSPORT:
                problems.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: direct send_message call outside explicit transport"
                )
    return problems


def problems() -> list[str]:
    found: list[str] = []
    for path in sorted(PRODUCTION_FILES):
        if path.exists():
            found.extend(audit(path))

    transport_tree = ast.parse(
        EXPLICIT_TRANSPORT.read_text(encoding="utf-8"),
        filename=str(EXPLICIT_TRANSPORT),
    )
    transport_calls = [
        node for node in ast.walk(transport_tree)
        if isinstance(node, ast.Call) and _call_name(node) == "send_message"
    ]
    if len(transport_calls) != 1:
        found.append(
            f"{EXPLICIT_TRANSPORT.relative_to(ROOT)}: expected exactly one MAX send_message call, found {len(transport_calls)}"
        )
    return found


def require_clean() -> None:
    """Refuse a production launch while outbound paths bypass the transport."""
    found = problems()
    if found:
        details = "\n".join(f"- {item}" for item in found)
        raise SystemExit(
            "Production outbound architecture gate FAILED. "
            "All MAX message delivery must pass through max_outbound_transport.py.\n"
            + details
        )


def main() -> int:
    found = problems()
    if found:
        print("Outbound path audit: FAIL")
        for problem in found:
            print(f"- {problem}")
        return 1
    print("Outbound path audit: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

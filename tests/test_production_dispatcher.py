from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _calls(path: Path, name: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == name]


def test_production_dispatcher_has_no_direct_provider_send():
    assert not _calls(ROOT / "max_production_dispatcher.py", "send_message")


def test_production_launchers_use_production_dispatcher():
    for filename in ("run_max_postgres.py", "max_webhook.py"):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert "from max_production_dispatcher import build_dispatcher" in text


def test_only_explicit_transport_calls_send_message():
    production = {
        "max_webhook.py",
        "run_max_postgres.py",
        "run_max_webhook.py",
        "max_production_dispatcher.py",
        "production_outbox.py",
        "durable_outbox_worker.py",
        "max_outbound_transport.py",
    }
    offenders: list[str] = []
    for filename in production:
        calls = _calls(ROOT / filename, "send_message")
        if filename == "max_outbound_transport.py":
            assert len(calls) == 1
        elif calls:
            offenders.append(filename)
    assert not offenders

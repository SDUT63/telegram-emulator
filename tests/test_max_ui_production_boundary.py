from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_production_modules_do_not_import_legacy_max_bot():
    for name in ("max_production_dispatcher.py", "production_outbox.py"):
        assert "max_bot" not in _imports(ROOT / name)


def test_production_ui_has_no_maxapi_dependency_or_network_send():
    source = (ROOT / "max_ui.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "maxapi" not in _imports(ROOT / "max_ui.py")
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"send_message", "ack", "edit"}
        for node in ast.walk(tree)
    )


def test_questionnaire_keyboard_is_serialized_without_transport_objects():
    import max_ui

    class SurveyStub:
        state = {"u": {"history": []}}
        def reading(self, user_id): return False
        def stage(self, user_id): return "consent"
        def current(self, user_id): return None

    rows = max_ui.layout(SurveyStub(), "u")
    assert rows
    assert all(isinstance(cell, tuple) and len(cell) == 2 for row in rows for cell in row)
    assert rows[0][0][1] == "c:y"

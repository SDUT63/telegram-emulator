from __future__ import annotations

import importlib.util
import os
import stat


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location("sdut_launcher", os.path.join(ROOT, "launcher.py"))
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_token_file_is_not_written_with_group_or_other_permissions(tmp_path, monkeypatch):
    token_path = tmp_path / "token.txt"
    monkeypatch.setattr(launcher, "TOKEN", str(token_path))
    monkeypatch.setattr(launcher, "say", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher.getpass, "getpass", lambda *_args, **_kwargs: "test-token")

    assert launcher.ask_token() is True
    assert token_path.read_text(encoding="utf-8") == "test-token\n"

    if os.name != "nt":
        mode = stat.S_IMODE(token_path.stat().st_mode)
        assert mode & 0o077 == 0


def test_token_exists_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    token_path = tmp_path / "token.txt"
    token_path.write_text("# comment\n\nactual-token\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "TOKEN", str(token_path))
    monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)

    assert launcher.token_exists() is True

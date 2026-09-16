from pathlib import Path

import pytest

import max_config


def test_read_token_prefers_environment(monkeypatch, tmp_path: Path):
    (tmp_path / "token.txt").write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("MAX_BOT_TOKEN", "env-token")
    assert max_config.read_token(tmp_path) == "env-token"


def test_read_token_uses_first_non_comment_line(tmp_path: Path):
    (tmp_path / "token.txt").write_text("\n# comment\n file-token \nsecond\n", encoding="utf-8")
    assert max_config.read_token(tmp_path) == "file-token"


def test_read_token_fails_closed_without_credentials(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="Не найден токен бота"):
        max_config.read_token(tmp_path)


def test_command_menu_is_explicit_and_unique():
    names = [name for name, _ in max_config.COMMANDS]
    assert names == ["start", "ask", "answers", "help", "cancel"]
    assert len(names) == len(set(names))

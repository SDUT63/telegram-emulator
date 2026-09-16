from pathlib import Path

import scripts.check_secrets as check_secrets


def test_documented_max_token_placeholder_is_allowed(tmp_path, monkeypatch):
    example = tmp_path / "README.md"
    example.write_text("MAX_BOT_TOKEN=...\n", encoding="utf-8")
    monkeypatch.setattr(check_secrets, "ROOT", tmp_path)
    assert check_secrets.main() == 0


def test_real_max_token_assignment_is_rejected(tmp_path, monkeypatch):
    example = tmp_path / ".env"
    example.write_text("MAX_BOT_TOKEN=real-looking-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(check_secrets, "ROOT", tmp_path)
    assert check_secrets.main() == 1

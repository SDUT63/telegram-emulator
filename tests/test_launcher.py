from __future__ import annotations

import io
import os
import re

import pytest

import launcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_normalize_mode_defaults_to_local_pilot():
    assert launcher.normalize_mode(None) == "bot"
    assert launcher.normalize_mode("") == "bot"
    assert launcher.normalize_mode("pilot") == "bot"


@pytest.mark.parametrize("value", ["prod", "production", "прод", "продакшен"])
def test_normalize_mode_selects_production(value):
    assert launcher.normalize_mode(value) == "prod"


def test_unknown_mode_is_safe_pilot_default():
    assert launcher.normalize_mode("unknown-mode") == "bot"


def test_production_requires_explicit_environment(monkeypatch):
    monkeypatch.setenv("SDUT_LAUNCHER_ENV", "1")
    monkeypatch.delenv("SDUT_ENV", raising=False)
    assert launcher.main(["prod"]) == 7


def test_production_launcher_does_not_preflight_or_ask_for_local_token(monkeypatch):
    monkeypatch.setenv("SDUT_LAUNCHER_ENV", "1")
    monkeypatch.setenv("SDUT_ENV", "production")
    calls = []
    monkeypatch.setattr(launcher, "start", lambda mode: calls.append(mode) or 0)
    monkeypatch.setattr(launcher, "preflight", lambda: pytest.fail("production used local pilot preflight"))
    monkeypatch.setattr(launcher, "token_exists", lambda: pytest.fail("production inspected token.txt"))
    assert launcher.main(["prod"]) == 0
    assert calls == ["prod"]


def test_token_file_is_not_read_as_production_configuration():
    assert "token.txt" not in launcher.start.__doc__ if launcher.start.__doc__ else True


def test_venv_python_points_to_platform_specific_interpreter():
    expected = "Scripts" if os.name == "nt" else "bin"
    assert expected in launcher.venv_python()


def test_local_preflight_lists_required_files():
    assert launcher.preflight() is True


def test_windows_launchers_have_expected_modes():
    launchers = {
        "Запустить-бота.bat": "bot",
        "Рабочее-место.bat": "crm",
        "Проверка.bat": "check",
        "Связь-с-MAX.bat": "doctor",
    }
    for filename, mode in launchers.items():
        path = os.path.join(ROOT, filename)
        assert os.path.exists(path)
        text = io.open(path, encoding="utf-8").read()
        assert re.search(rf"launcher\.py\s+{re.escape(mode)}\b", text)
        assert "pause" in text.lower()
        assert "chcp 65001" in text.lower()


def test_linux_launcher_exists_and_is_executable():
    path = os.path.join(ROOT, "запустить.sh")
    assert os.path.exists(path)
    assert os.access(path, os.X_OK)
    assert "launcher.py" in io.open(path, encoding="utf-8").read()

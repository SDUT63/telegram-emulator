#!/usr/bin/env python3
"""One-click launcher for the SDUT MAX bot and operator tools.

The repository is a local test/deployment bundle for the MAX bot. The launcher
keeps the original useful modes (bot, CRM, check, doctor) while ensuring that
Python dependencies are isolated in .venv.
"""
from __future__ import annotations

import getpass
import os
import platform
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
TOKEN = os.path.join(HERE, "token.txt")
REQUIREMENTS = os.path.join(HERE, "requirements.txt")
ENV_FLAG = "SDUT_LAUNCHER_ENV"
MIN_PYTHON = (3, 10)


def venv_python() -> str:
    return os.path.join(VENV, "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")


def say(text: str = "") -> None:
    print(text, flush=True)


def run(args: list[str], *, check: bool = False) -> int:
    return subprocess.run(args, cwd=HERE, check=check).returncode


def ensure_environment() -> int:
    """Create .venv and install the complete requirements set once."""
    if sys.version_info < MIN_PYTHON:
        say(f"Нужен Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} или новее.")
        return 2

    if os.environ.get(ENV_FLAG) == "1":
        return 0

    py = venv_python()
    if not os.path.exists(py):
        say("Создаю .venv...")
        try:
            subprocess.run([sys.executable, "-m", "venv", VENV], cwd=HERE, check=True)
        except subprocess.CalledProcessError as exc:
            say(f"Не удалось создать .venv: {exc}")
            return 3

    say(f"Проверяю зависимости (Python {platform.python_version()})...")
    probe = subprocess.run(
        [py, "-c", "import maxapi, flask, openpyxl, psycopg"],
        cwd=HERE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode:
        say("Устанавливаю зависимости из requirements.txt...")
        try:
            subprocess.run(
                [py, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS],
                cwd=HERE,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            say(f"Не удалось установить зависимости: {exc}")
            return 4

    env = dict(os.environ, **{ENV_FLAG: "1"})
    args = [py, os.path.abspath(__file__), *sys.argv[1:]]
    return subprocess.run(args, cwd=HERE, env=env).returncode


def token_exists() -> bool:
    if (os.environ.get("MAX_BOT_TOKEN") or "").strip():
        return True
    try:
        with open(TOKEN, encoding="utf-8-sig") as fh:
            return any(line.strip() and not line.lstrip().startswith("#") for line in fh)
    except OSError:
        return False


def _warn_token_permissions() -> None:
    """Warn on POSIX when token.txt is readable by group/other users."""
    if os.name == "nt" or not os.path.exists(TOKEN):
        return
    try:
        mode = stat.S_IMODE(os.stat(TOKEN).st_mode)
    except OSError:
        return
    if mode & 0o077:
        say("[warn] token.txt доступен группе/другим пользователям; ограничиваю права до 600.")
        try:
            os.chmod(TOKEN, 0o600)
        except OSError as exc:
            say(f"[warn] Не удалось ограничить права token.txt: {exc}")


def ask_token() -> bool:
    say("Токен MAX не найден. Вставьте токен одной строкой.")
    try:
        # getpass не показывает токен на экране во время ввода.
        token = getpass.getpass("Токен: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not token:
        return False
    try:
        if os.name != "nt":
            # Создаём файл сразу с 0600, а не сначала с обычными правами.
            fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(token + "\n")
        else:
            with open(TOKEN, "w", encoding="utf-8") as fh:
                fh.write(token + "\n")
            try:
                os.chmod(TOKEN, 0o600)
            except OSError:
                pass
        _warn_token_permissions()
    except OSError as exc:
        say(f"Не удалось сохранить token.txt: {exc}")
        return False
    return True


def preflight() -> bool:
    """Check bot-owned files before starting a process that can receive users."""
    required = (
        "max_bot.py",
        "chatbot_survey.py",
        "survey_questions.py",
        "knowledge.py",
        "consent_forms.py",
        "fallback.py",
        "scale_731.py",
    )
    missing = [name for name in required if not os.path.exists(os.path.join(HERE, name))]
    if missing:
        say("Отсутствуют обязательные файлы: " + ", ".join(missing))
        return False

    sys.path.insert(0, HERE)
    checks: list[str] = []
    try:
        import survey_questions
        checks.append(f"анкета: {len(survey_questions.QUESTIONS)} вопросов")
    except Exception as exc:  # noqa: BLE001
        say(f"Ошибка анкеты: {exc}")
        return False

    try:
        import knowledge
        data = knowledge.загрузить()
        if len(data) < 50:
            say(f"База знаний подозрительно мала: {len(data)} статей")
            return False
        checks.append(f"база знаний: {len(data)} статей")
    except Exception as exc:  # noqa: BLE001
        say(f"Ошибка базы знаний: {exc}")
        return False

    try:
        import consent_forms
        import chatbot_survey
        if not chatbot_survey.CONSENT_SHORT or not chatbot_survey.CONSENT_FULL:
            raise RuntimeError("пустой текст согласия")
        if chatbot_survey.consent_forms is not consent_forms:
            raise RuntimeError("модуль согласия подключён не тот")
        checks.append(f"согласие: версия {chatbot_survey.CONSENT_VERSION}")
    except Exception as exc:  # noqa: BLE001
        say(f"Ошибка форм согласия: {exc}")
        return False

    try:
        import fallback
        checks.append(f"fallback: {fallback.ступеней(fallback.ВОПРОС)} ступеней")
    except Exception as exc:  # noqa: BLE001
        say(f"Ошибка fallback: {exc}")
        return False

    try:
        import scale_731
        coverage = scale_731.покрытие()
        checks.append(f"шкала 731: {coverage['закрыто']}/{coverage['всего']}")
    except Exception as exc:  # noqa: BLE001
        say(f"Ошибка шкалы 731: {exc}")
        return False

    for item in checks:
        say(f"[ok] {item}")
    return True


def start(mode: str) -> int:
    targets = {
        "bot": ("run_max.py", "MAX bot / SQLite laptop pilot"),
        "crm": ("crm_server.py", "CRM operator workspace"),
        "doctor": ("diagnose.py", "MAX connectivity diagnostics"),
    }
    script, title = targets[mode]
    say(f"\n=== {title} ===")
    return run([sys.executable, os.path.join(HERE, script)])


def normalize_mode(value: str | None) -> str:
    aliases = {
        None: "bot",
        "": "bot",
        "bot": "bot",
        "бот": "bot",
        "crm": "crm",
        "doctor": "doctor",
        "check": "check",
        "проверка": "check",
    }
    return aliases.get((value or "").strip().lower(), "bot")


def main() -> int:
    mode = normalize_mode(sys.argv[1] if len(sys.argv) > 1 else None)
    env_result = ensure_environment()
    if env_result != 0:
        return env_result

    if not preflight():
        return 5

    if mode == "check":
        say("Проверка завершена успешно.")
        return 0

    if mode in {"bot", "doctor"} and not token_exists():
        if not ask_token():
            say("Без токена запуск невозможен.")
            return 6

    if mode in {"bot", "doctor"}:
        _warn_token_permissions()

    return start(mode)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("\nОстановлено пользователем.")
        raise SystemExit(0)

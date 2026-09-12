#!/usr/bin/env python3
"""One-click launcher for the durable MAX bot on a laptop."""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
TOKEN = os.path.join(HERE, "token.txt")
REQUIREMENTS = os.path.join(HERE, "requirements.txt")


def venv_python() -> str:
    return os.path.join(
        VENV, "Scripts", "python.exe" if os.name == "nt" else "bin/python"
    )


def run(args: list[str]) -> int:
    return subprocess.run(args, cwd=HERE).returncode


def main() -> int:
    if sys.version_info < (3, 10):
        print("Нужен Python 3.10 или новее.")
        return 2

    py = venv_python()
    if os.environ.get("SDUT_LAUNCHER_ENV") != "1":
        if not os.path.exists(py):
            print("Создаю .venv...")
            subprocess.run([sys.executable, "-m", "venv", VENV], check=True, cwd=HERE)

        print("Проверяю зависимости...")
        check = subprocess.run(
            [py, "-c", "import maxapi, flask, openpyxl"],
            cwd=HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if check.returncode:
            print("Устанавливаю зависимости из requirements.txt...")
            subprocess.run(
                [py, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS],
                check=True,
                cwd=HERE,
            )

        env = dict(os.environ, SDUT_LAUNCHER_ENV="1")
        return subprocess.run(
            [py, os.path.abspath(__file__)], cwd=HERE, env=env
        ).returncode

    if not os.path.exists(TOKEN) and not os.environ.get("MAX_BOT_TOKEN", "").strip():
        print("Токен MAX не найден. Вставьте токен одной строкой:")
        token = input("Токен: ").strip()
        if not token:
            return 3
        with open(TOKEN, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")

    return run([sys.executable, os.path.join(HERE, "run_max.py")])


if __name__ == "__main__":
    raise SystemExit(main())

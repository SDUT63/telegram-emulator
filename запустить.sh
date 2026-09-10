#!/bin/sh
# Запуск чат-бота АНО «СДУТ» на Linux и macOS.
#
# То же, что «Запустить-бота.bat» на Windows: окружение, библиотеки,
# токен, проверка и старт. Вся работа — в launcher.py, здесь только
# поиск Python.
#
#   ./запустить.sh          бот
#   ./запустить.sh crm      рабочее место координатора
#   ./запустить.sh check    только проверка
#   ./запустить.sh doctor   разбор связи с MAX

cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo
    echo "  Python на этом компьютере не найден."
    echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv"
    echo "  macOS:          brew install python"
    echo
    exit 1
fi

exec "$PY" launcher.py "$@"

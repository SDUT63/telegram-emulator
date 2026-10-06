"""Ни одной фразы из архива переписки в репозитории (тест 13 мануала).

Архив переписки онкопациентов, переданный психологом, содержит сведения
о здоровье людей, которые не давали согласия на его использование
(Р-А8). Сам архив в репозиторий не кладётся: путь к выгрузке
`chat_messages.csv` передаётся переменной окружения `ARCHIVE_CSV`, без
неё проверка пропускается. Поэтому в CI она не идёт, а запускается
локально перед каждым PR по архиву:

    ARCHIVE_CSV=/путь/chat_messages.csv python -m pytest tests/test_no_archive_leak.py

Проверяется весь текст репозитория: и код, и база знаний, и документы.
Совпадение — пять слов подряд. Обороты, которые совпали с архивом ещё
до того, как он был получен (общие фразы вроде «по месту жительства
или по месту», «об основах охраны здоровья граждан»), утечкой быть
не могут. Их отпечатки лежат в `tests/fixtures/archive_leak_baseline.txt`,
собранные по коммиту `20089ec` — последнему до получения архива.
Перестраивать этот список по более позднему коммиту нельзя: так в
него попадёт именно утечка.

Совпадения не печатаются — только файлы и их число.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

АРХИВ = os.environ.get("ARCHIVE_CSV")
КОРЕНЬ = Path(__file__).resolve().parents[1]
БАЗА = Path(__file__).resolve().with_name("fixtures") / "archive_leak_baseline.txt"
ДО_АРХИВА = "20089ec"
N = 5
РАСШИРЕНИЯ = {".py", ".md", ".json", ".yaml", ".yml", ".txt", ".html", ".js", ".csv"}


def _слова(текст: str) -> list[str]:
    return re.findall(r"\w+", текст.lower().replace("ё", "е"))


def _граммы(текст: str) -> list[tuple[str, ...]]:
    слова = _слова(текст)
    return [tuple(слова[i:i + N]) for i in range(len(слова) - N + 1)]


def _отпечаток(грамма: tuple[str, ...]) -> str:
    return hashlib.sha256(" ".join(грамма).encode("utf-8")).hexdigest()[:16]


def _граммы_архива(путь: str) -> set[tuple[str, ...]]:
    csv.field_size_limit(10**9)
    граммы: set[tuple[str, ...]] = set()
    with open(путь, encoding="utf-8-sig") as f:
        for строка in csv.DictReader(f, delimiter=";"):
            граммы.update(_граммы(строка.get("text") or ""))
    return граммы


def _файлы_репозитория() -> list[Path]:
    """Всё, что попадёт в коммит: отслеживаемое и новое, без игнорируемого."""
    вывод = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=КОРЕНЬ, capture_output=True, text=True, check=True).stdout
    return [КОРЕНЬ / п for п in вывод.splitlines()
            if Path(п).suffix in РАСШИРЕНИЯ and (КОРЕНЬ / п).is_file()]


@pytest.mark.skipif(not АРХИВ, reason="архив недоступен: проверка только локально")
def test_в_репозитории_нет_фраз_из_архива():
    архив = _граммы_архива(АРХИВ)
    известные = set(БАЗА.read_text(encoding="utf-8").split())
    по_файлам: Counter[str] = Counter()
    for путь in _файлы_репозитория():
        if путь == БАЗА:
            continue
        текст = путь.read_text(encoding="utf-8", errors="ignore")
        for грамма in _граммы(текст):
            if грамма in архив and _отпечаток(грамма) not in известные:
                по_файлам[str(путь.relative_to(КОРЕНЬ))] += 1
    assert not по_файлам, (
        f"совпадений с архивом по {N} слов: {sum(по_файлам.values())} в файлах "
        f"{sorted(по_файлам)}")


def построить_базу(путь_к_архиву: str, коммит: str = ДО_АРХИВА) -> list[str]:
    """Отпечатки совпадений, которые были в репозитории до получения архива."""
    архив = _граммы_архива(путь_к_архиву)
    файлы = subprocess.run(["git", "ls-tree", "-r", "--name-only", коммит],
                           cwd=КОРЕНЬ, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    отпечатки = set()
    for п in файлы:
        if Path(п).suffix not in РАСШИРЕНИЯ:
            continue
        текст = subprocess.run(["git", "show", f"{коммит}:{п}"], cwd=КОРЕНЬ,
                               capture_output=True).stdout.decode("utf-8", "ignore")
        отпечатки.update(_отпечаток(г) for г in _граммы(текст) if г in архив)
    return sorted(отпечатки)


if __name__ == "__main__":
    # python tests/test_no_archive_leak.py /путь/chat_messages.csv
    print("\n".join(построить_базу(sys.argv[1])))

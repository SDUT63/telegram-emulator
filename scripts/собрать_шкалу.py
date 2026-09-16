#!/usr/bin/env python3
"""Выгрузить оценочную шкалу в JSON для мини-приложения.

Мини-приложение — статическая страница на Cloudflare: Python там
не исполняется, и шкалу оно считает само. Переписывать её в HTML
руками нельзя: две копии нормативных весов разойдутся, и страница
начнёт показывать не тот уровень, который посчитает служба.

Поэтому единственный источник — scale_731.py, а этот скрипт делает
из него данные. Расхождение ловит проверка tests/test_scale_export.py.

    python scripts/собрать_шкалу.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))

import scale_731 as шкала                                    # noqa: E402

ВЫХОД = КОРЕНЬ / "webapp" / "шкала.json"


def собрать() -> dict:
    return {
        "источник": "Приказ Минтруда России № 731 от 23.12.2025, "
                    "приложение № 3, бланк «Блок В», раздел 4",
        "оговорка": шкала.PRELIMINARY,
        "максимум": sum(и.full for и in шкала.SCALE),
        "позиции": [
            {
                "id": и.id,
                "вопрос": и.plain,
                "официально": и.official,
                "раздел": и.group,
                "частично": и.mid,
                "не_может": и.full,
            }
            for и in шкала.SCALE
        ],
        "уровни": [
            {
                "от": низ,
                "до": верх,
                "номер": номер,
                "название": имя,
                "часов_в_неделю": list(шкала.PACKAGE_HOURS.get(номер, (0, 0))),
            }
            for низ, верх, номер, имя in шкала.LEVELS
        ],
    }


def main() -> int:
    данные = собрать()
    ВЫХОД.write_text(
        json.dumps(данные, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{ВЫХОД.relative_to(КОРЕНЬ)}: позиций {len(данные['позиции'])}, "
          f"максимум {данные['максимум']} баллов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

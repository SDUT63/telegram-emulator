#!/usr/bin/env python3
"""
Собирает файл certs.pem, которому бот будет доверять.

Нужен, когда Python не принимает сертификат MAX. Кладёте рядом с этим файлом
скачанные сертификаты (.cer или .crt) и запускаете:

    python add_cert.py

Скрипт соберёт certs.pem из всех найденных сертификатов плюс тех, которым
Python доверяет сейчас, — так что ничего не потеряется. Бот подхватит файл
сам, ничего дополнительно настраивать не нужно.

Где взять сертификаты MAX: gosuslugi.ru/crt — оба файла для Windows.
"""

from __future__ import annotations

import glob
import os
import ssl
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "certs.pem")
PATTERNS = ("*.cer", "*.crt", "*.der")

BEGIN = "-----BEGIN CERTIFICATE-----"


def to_pem(raw: bytes) -> list[str]:
    """Привести содержимое файла к списку сертификатов в текстовом виде."""
    text = raw.decode("ascii", errors="ignore")
    if BEGIN in text:
        # Уже текстовый формат — файл может содержать несколько штук
        parts = text.split(BEGIN)[1:]
        return [BEGIN + p.split("-----END CERTIFICATE-----")[0] +
                "-----END CERTIFICATE-----\n" for p in parts]
    # Двоичный формат
    return [ssl.DER_cert_to_PEM_cert(raw)]


def main() -> int:
    found: list[str] = []
    for pattern in PATTERNS:
        found.extend(sorted(glob.glob(os.path.join(HERE, pattern))))

    print()
    print("=" * 62)
    print("  Сборка списка доверенных сертификатов")
    print("=" * 62)
    print()

    if not found:
        print("  Рядом с этим файлом нет ни одного сертификата.")
        print()
        print("  Что нужно сделать:")
        print("  1. Откройте gosuslugi.ru/crt")
        print("  2. Скачайте оба файла для Windows (.cer)")
        print("  3. Положите их в эту же папку — рядом с max_bot.py")
        print("  4. Запустите эту команду ещё раз")
        print()
        return 1

    blocks: list[str] = []

    for path in found:
        try:
            with open(path, "rb") as fh:
                pems = to_pem(fh.read())
            blocks.extend(pems)
            name = os.path.basename(path)
            print(f"  [+] {name} — сертификатов: {len(pems)}")
        except Exception as error:  # noqa: BLE001
            print(f"  [!] {os.path.basename(path)} — не разобрался: {error}")

    if not blocks:
        print()
        print("  Ни один файл прочитать не удалось.")
        print()
        return 1

    # Добавляем то, чему Python доверяет сейчас, чтобы не потерять
    # обычные сертификаты остального интернета
    try:
        current = ssl.create_default_context().get_ca_certs(binary_form=True)
        for der in current:
            blocks.append(ssl.DER_cert_to_PEM_cert(der))
        print(f"  [+] прежний список доверия — сертификатов: {len(current)}")
    except Exception as error:  # noqa: BLE001
        print(f"  [!] прежний список прочитать не вышло: {error}")

    # Убираем повторы, сохраняя порядок
    seen: set[str] = set()
    unique: list[str] = []
    for block in blocks:
        key = "".join(block.split())
        if key not in seen:
            seen.add(key)
            unique.append(block if block.endswith("\n") else block + "\n")

    with open(OUT, "w", encoding="ascii") as fh:
        fh.write("".join(unique))

    print()
    print(f"  Готово: certs.pem, сертификатов внутри — {len(unique)}")
    print()
    print("  Теперь запускайте бота как обычно:")
    print("      python max_bot.py")
    print()
    print("  Файл нужен только этому компьютеру и в репозиторий не попадёт.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

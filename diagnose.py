#!/usr/bin/env python3
"""
Проверка связи с MAX.

Запускается, когда бот не подключается. Проверяет по шагам, где именно
обрывается связь, и говорит понятным языком, что делать.

Запуск:
    python diagnose.py
"""

from __future__ import annotations

import socket
import ssl
import sys

HOSTS = ["platform-api.max.ru", "platform-api2.max.ru"]
PORT = 443
LINE = "=" * 62


def say(text: str = "") -> None:
    print(text)


def check_dns(host: str) -> str | None:
    try:
        ip = socket.gethostbyname(host)
        say(f"  [ок]      адрес найден: {ip}")
        return ip
    except OSError as error:
        say(f"  [ОШИБКА]  не удалось найти адрес: {error}")
        return None


def check_tcp(host: str) -> bool:
    try:
        with socket.create_connection((host, PORT), timeout=10):
            say("  [ок]      соединение установлено")
            return True
    except OSError as error:
        say(f"  [ОШИБКА]  соединение не устанавливается: {error}")
        return False


def peek_certificate(host: str) -> dict | None:
    """Забрать сертификат БЕЗ проверки, только чтобы посмотреть, кто его выдал."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, PORT), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError as error:
        say(f"  [ОШИБКА]  не удалось получить сертификат: {error}")
        return None

    # Разбираем сертификат без сторонних библиотек
    try:
        import tempfile, os
        pem = ssl.DER_cert_to_PEM_cert(der)
        fd, path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "w") as fh:
            fh.write(pem)
        info = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
        os.unlink(path)
        return info
    except Exception as error:  # noqa: BLE001
        say(f"  [?]       сертификат получен, но разобрать не вышло: {error}")
        return None


def issuer_name(info: dict) -> str:
    parts = []
    for rdn in info.get("issuer", ()):
        for key, value in rdn:
            if key in ("organizationName", "commonName"):
                parts.append(value)
    # Убираем повторы, сохраняя порядок
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return " / ".join(seen) if seen else "неизвестно"


def check_tls(host: str) -> tuple[bool, str]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, PORT), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                say("  [ок]      сертификат принят — здесь проблемы нет")
                return True, ""
    except ssl.SSLCertVerificationError as error:
        say(f"  [ОШИБКА]  сертификат не принят: {error.verify_message or error}")
        return False, str(error)
    except OSError as error:
        say(f"  [ОШИБКА]  {error}")
        return False, str(error)


def windows_roots() -> list[str]:
    """Список названий корневых удостоверяющих центров из хранилища Windows."""
    if sys.platform != "win32":
        return []
    names: list[str] = []
    try:
        for der, _enc, trust in ssl.enum_certificates("ROOT"):  # type: ignore[attr-defined]
            if trust is True or (isinstance(trust, set) and trust):
                try:
                    import tempfile, os
                    pem = ssl.DER_cert_to_PEM_cert(der)
                    fd, path = tempfile.mkstemp(suffix=".pem")
                    with os.fdopen(fd, "w") as fh:
                        fh.write(pem)
                    info = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
                    os.unlink(path)
                    for rdn in info.get("subject", ()):
                        for key, value in rdn:
                            if key == "commonName":
                                names.append(value)
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return names


def main() -> int:
    say()
    say(LINE)
    say("  Проверка связи с MAX")
    say(LINE)
    say()
    say(f"Python: {sys.version.split()[0]}   Система: {sys.platform}")

    verdicts: list[str] = []

    for host in HOSTS:
        say()
        say(f"--- {host} ---")

        if not check_dns(host):
            verdicts.append("dns")
            continue
        if not check_tcp(host):
            verdicts.append("tcp")
            continue

        info = peek_certificate(host)
        if info:
            who = issuer_name(info)
            say(f"  [инфо]    сертификат выдал: {who}")

        ok, _ = check_tls(host)
        if ok:
            verdicts.append("ok")
            continue

        verdicts.append("tls")

        if info:
            who = issuer_name(info)
            roots = windows_roots()
            if roots:
                short = who.split(" / ")[0]
                found = any(short.lower() in r.lower() or r.lower() in who.lower() for r in roots)
                if found:
                    say("  [инфо]    этот центр в хранилище Windows есть,")
                    say("            но Python его почему-то не видит")
                else:
                    say("  [инфо]    этого центра в хранилище Windows НЕТ —")
                    say("            вот поэтому сертификат и не принимается")

    say()
    say(LINE)
    say("  Что это значит")
    say(LINE)
    say()

    if "ok" in verdicts:
        say("  Связь с MAX в порядке. Если бот всё равно не запускается,")
        say("  дело не в сети, а в токене — проверьте token.txt.")
        say()
        return 0

    if "dns" in verdicts:
        say("  Компьютер не может найти адрес MAX.")
        say()
        say("  Скорее всего мешает VPN. Отключите его и запустите проверку")
        say("  заново: MAX — российский сервис, для него VPN не нужен.")
    elif "tcp" in verdicts:
        say("  Адрес находится, но соединение не устанавливается.")
        say()
        say("  Обычно это VPN, брандмауэр или антивирус. Отключите VPN")
        say("  и попробуйте снова.")
    else:
        say("  Соединение есть, но Python не доверяет сертификату MAX.")
        say()
        say("  Это самая частая причина: MAX подписан российским корневым")
        say("  сертификатом (НУЦ Минцифры). В браузере сайт открывается,")
        say("  потому что у браузеров свои списки доверия, а Python берёт")
        say("  список из Windows — и там этого сертификата нет.")
        say()
        say("  Что делать — по порядку:")
        say()
        say("  1. Отключите VPN и запустите проверку заново.")
        say("     Если VPN подменяет сертификаты, это всё объясняет.")
        say()
        say("  2. Установите сертификаты НУЦ Минцифры с gosuslugi.ru/crt")
        say("     Оба файла: корневой и выпускающий. При установке выбирайте")
        say("     хранилище «Доверенные корневые центры сертификации».")
        say("     После установки перезапустите проверку.")
        say()
        say("  3. Если стоит антивирус с проверкой защищённых соединений")
        say("     (Kaspersky, ESET, Dr.Web) — он подменяет сертификаты собой.")
        say("     Отключите эту проверку для platform-api.max.ru")
        say("     или экспортируйте сертификат антивируса в certs.pem")
        say("     рядом с ботом — бот подхватит его сам.")

    say()
    say("  Отключать проверку сертификатов НЕЛЬЗЯ: через это соединение")
    say("  идут токен бота и персональные данные обратившихся людей.")
    say()
    return 1


if __name__ == "__main__":
    sys.exit(main())

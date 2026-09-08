"""Сборка слайдов и короткого видео из `_кадры-исходник.html`.

Короткий мануал — то, что кладут в пост: десяток слайдов и минутный ролик
из них же. В отличие от полного видеомануала кадры здесь нарисованы
вручную: это не прогон анкеты, а рассказ о ней. Значит, после правки
вопросов исходник надо поправить руками — сам он не подтянется.

Что нужно: playwright с Chromium и ffmpeg (ставится с imageio-ffmpeg).
Запуск из корня проекта:

    python webapp/инструкция/сборка-слайдов.py

Результат — PNG-слайды и Инструкция-СДУТ-бот.mp4 рядом с этим файлом.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ЗДЕСЬ)

import шрифт                                            # noqa: E402

ИСХОДНИК = os.path.join(ЗДЕСЬ, "_кадры-исходник.html")

ШИРИНА, ВЫСОТА, КАДРОВ_В_СЕКУНДУ = 540, 960, 30
ПАУЗА = 4.3          # секунд на слайд: столько нужно, чтобы прочитать подпись
ПАУЗА_ЗАСТАВКИ = 5.0


def имя_файла(номер: int, заголовок: str) -> str:
    """Понятное имя слайда: по нему его находят, не открывая."""
    коротко = " ".join(заголовок.split()[:3])
    коротко = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "-", коротко).strip("-").lower()
    return "%02d-%s.png" % (номер, коротко or "слайд")


def собрать() -> None:
    from playwright.sync_api import sync_playwright

    # Старые слайды удаляем: иначе от прошлой сборки останутся лишние,
    # и в пост уедет экран, которого в боте больше нет.
    for старый in os.listdir(ЗДЕСЬ):
        if re.match(r"^\d\d-.*\.png$", старый):
            os.remove(os.path.join(ЗДЕСЬ, старый))

    # Рисуем не сам исходник, а его копию с вшитым шрифтом: иначе каждый
    # кадр ждёт сеть, а без сети выходит не тем шрифтом
    страница = os.path.join(tempfile.mkdtemp(), "кадры.html")
    open(страница, "w", encoding="utf-8").write(
        шрифт.вшить(open(ИСХОДНИК, encoding="utf-8").read()))

    слайды: list[str] = []
    with sync_playwright() as pw:
        браузер = pw.chromium.launch(executable_path=os.getenv(
            "CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"))
        лист = браузер.new_page(
            viewport={"width": ШИРИНА, "height": ВЫСОТА}, device_scale_factor=2)
        лист.goto("file://" + страница, wait_until="domcontentloaded")
        лист.evaluate("document.fonts.ready")
        всего = лист.evaluate("window.FRAME_COUNT")

        for i in range(всего):
            лист.goto(f"file://{страница}?{i}", wait_until="domcontentloaded")
            лист.evaluate("document.fonts.ready")
            лист.wait_for_timeout(140)
            # innerText, а не textContent: в заголовке заставки стоит <br>,
            # и textContent склеил бы слова без пробела
            заголовок = лист.evaluate(
                "(document.querySelector('.cap h2') ||"
                " document.querySelector('.title h1') || {}).innerText || ''"
            ).replace("\n", " ")
            путь = os.path.join(ЗДЕСЬ, имя_файла(i, заголовок))
            лист.screenshot(path=путь)
            слайды.append(путь)
            print("  ", os.path.basename(путь))
        браузер.close()

    склеить(слайды)


def склеить(слайды: list[str]) -> None:
    import imageio_ffmpeg

    список = os.path.join(ЗДЕСЬ, "_список.txt")
    with open(список, "w", encoding="utf-8") as fh:
        for i, путь in enumerate(слайды):
            пауза = ПАУЗА_ЗАСТАВКИ if i in (0, len(слайды) - 1) else ПАУЗА
            fh.write("file '%s'\nduration %.2f\n" % (путь, пауза))
        fh.write("file '%s'\n" % слайды[-1])

    видео = os.path.join(ЗДЕСЬ, "Инструкция-СДУТ-бот.mp4")
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
         "-i", список, "-vf", f"fps={КАДРОВ_В_СЕКУНДУ},format=yuv420p",
         # preset medium, а не slow: кадры статичные, разницы в размере почти
         # нет, а пересборка после правки вопросов должна быть дешёвой
         "-c:v", "libx264", "-preset", "medium", "-crf", "23",
         "-movflags", "+faststart", видео],
        check=True, capture_output=True)
    os.remove(список)

    секунд = ПАУЗА_ЗАСТАВКИ * 2 + ПАУЗА * (len(слайды) - 2)
    print("готово: %s (%d:%02d), слайдов %d"
          % (видео, секунд // 60, секунд % 60, len(слайды)))


if __name__ == "__main__":
    собрать()

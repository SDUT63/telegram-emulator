#!/usr/bin/env python3
"""
Схема маршрутизации: собирается из кода, а не рисуется руками.

Нарисованная схема живёт своей жизнью: правила меняются, картинка
остаётся, и через полгода по ней учат новых сотрудников неправильно.
Поэтому здесь всё берётся из работающих модулей — правил маршрутизации,
сценария анкеты, карты базы знаний и оценочной шкалы. Поправили правило —
пересобрали схему, и она снова верна.

Запуск:
    python webapp/схема/сборка-схемы.py

Результат: webapp/Схема-маршрутизации.html — две страницы A4,
годятся и на экран, и на печать.
"""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
КОРЕНЬ = os.path.dirname(os.path.dirname(ЗДЕСЬ))
sys.path.insert(0, КОРЕНЬ)

import fallback                                              # noqa: E402
import knowledge                                             # noqa: E402
import routing_rules                                         # noqa: E402
import scale_731                                             # noqa: E402
import survey_questions as вопросы                           # noqa: E402

ВЫХОД = os.path.join(КОРЕНЬ, "webapp", "Схема-маршрутизации.html")

МЕСЯЦЫ = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def э(текст: object) -> str:
    return html.escape(str(текст))


# ------------------------------------------------------------ живые данные

def собрать() -> dict:
    """Всё, что схема показывает, — из работающих модулей."""
    статьи = knowledge.загрузить()
    все = вопросы.QUESTIONS
    до_развилки = [в["id"] for в in все]
    развилка = до_развилки.index(вопросы.CHECKPOINT_ID) + 1
    разделы = []
    for в in все:
        имя = в.get("section", "")
        if имя and имя not in разделы:
            разделы.append(имя)

    шкала = scale_731.покрытие()
    return {
        "вопросов": len(все),
        "коротких": развилка,
        "подробных": len(все) - развилка,
        "разделов": len(разделы),
        "стоп": list(routing_rules.URGENT_FLAGS),
        "самообслуживание": list(routing_rules.LOSS),
        "медпризнаки": list(routing_rules.MED),
        "противоречий": len(routing_rules.CONTRADICTIONS),
        "случаев": len(routing_rules.CASES),
        "маршруты": dict(routing_rules.NAMES),
        "ветви": knowledge.карта(),
        "статей": len(статьи),
        "ступеней": fallback.ступеней(fallback.ВОПРОС),
        "шкала": шкала,
    }


# -------------------------------------------------------------- разметка

ПОДПИСИ_УТРАТЫ = {
    "mobility": "передвижение", "turning": "повороты в кровати",
    "eating": "еда", "drinking": "питьё", "hygiene": "мытьё",
    "toilet": "туалет", "dressing": "одевание",
    "alone": "можно ли оставить одного",
}
ПОДПИСИ_МЕД = {
    "status": "паллиативный статус", "pain": "постоянная боль",
    "devices": "трубки, стомы, катетер", "skin": "открытая рана",
    "meds": "пропуски лекарств",
}

# Условия маршрутов — пересказ правил из routing_rules.route() человеческим
# языком. Порядок тот же, в каком правила проверяются в коде.
УСЛОВИЯ = {
    "СМП": ("Проверяется раньше всего",
            "В ответе на «что беспокоит прямо сейчас» отмечено хотя бы одно: "
            "резкое ухудшение, тяжёлое дыхание, спутанность. "
            "Анкета останавливается, маршрут не назначается."),
    "М1": ("Первое правило",
           "Есть паллиативный статус, или постоянная боль, или трахеостома, "
           "или открытая рана. Медицинский признак перевешивает всё остальное."),
    "М5": ("Второе правило",
           "Потребность охватывает три сферы и больше — или две сферы "
           "при утрате трёх параметров самообслуживания."),
    "М4": ("Третье правило — и последнее",
           "Ответы противоречат друг другу; либо анкета остановлена на развилке; "
           "либо запрос не сформулирован; либо обращение от специалиста; "
           "либо признаков нет вовсе. Считать уровень по такой анкете нельзя."),
    "М3": ("Четвёртое правило",
           "Утрачено три параметра самообслуживания и больше."),
    "М2": ("Пятое правило",
           "Утрачен один-два параметра, уход в семье обеспечен, "
           "медицинских признаков нет."),
}
ПОРЯДОК = ("СМП", "М1", "М5", "М4", "М3", "М2")

ЦВЕТА = {"СМП": "critical", "М1": "accent", "М5": "sand",
         "М4": "muted", "М3": "accent", "М2": "accent"}


def шаг(номер: str, имя: str, строки: list[str], вид: str = "") -> str:
    пункты = "".join(f"<li>{s}</li>" for s in строки)
    return (f'<div class="step {вид}"><div class="step__n">{э(номер)}</div>'
            f'<div class="step__body"><h3>{э(имя)}</h3>'
            f'<ul>{пункты}</ul></div></div>')


def собрать_html(д: dict) -> str:
    сегодня = datetime.now()
    дата = f"{сегодня.day} {МЕСЯЦЫ[сегодня.month - 1]} {сегодня.year} года"

    утрата = ", ".join(ПОДПИСИ_УТРАТЫ.get(к, к) for к in д["самообслуживание"])
    медпризнаки = ", ".join(ПОДПИСИ_МЕД.get(к, к) for к in д["медпризнаки"])
    стопы = ", ".join(f"«{s.lower()}»" for s in д["стоп"])

    маршруты = ""
    for код in ПОРЯДОК:
        когда, текст = УСЛОВИЯ[код]
        название = д["маршруты"].get(код, "")
        куда = название.split("—", 1)
        маршруты += (
            f'<tr class="r r--{ЦВЕТА[код]}">'
            f'<td class="r__code">{э(код)}</td>'
            f'<td class="r__name"><b>{э(куда[0].strip())}</b>'
            + (f'<span>{э(куда[1].strip())}</span>' if len(куда) > 1 else "")
            + f'</td><td class="r__when"><i>{э(когда)}</i>{э(текст)}</td></tr>')

    ветви = "".join(
        f'<li><b>{э(в["название"])}</b><span>{в["сколько"]}</span></li>'
        for в in д["ветви"])

    шаги = "".join((
        шаг("Вход", "Обращение", [
            "Чат-бот в MAX · телефон · форма на сайте · личный приём",
            "Обратиться может и не сам человек: родственник, сосед, участковый",
            "Условий нет: ни статуса, ни прописки, ни документов",
        ]),
        шаг("1", "Стоп-сигналы — проверяются раньше анкеты", [
            f"Признаки: {э(стопы)}",
            "<b>Анкета останавливается.</b> Бот называет 103 и 112 "
            "и передаёт обращение координатору немедленно",
            "Бот не оценивает состояние и не решает, вызывать ли скорую",
        ], "step--stop"),
        шаг("2", "Согласие на обработку данных", [
            "Без него анкета не ведётся: вопросы о здоровье — "
            "специальная категория сведений",
            "Четвёртая кнопка — «Просто почитать»: база знаний "
            "открывается без согласия и без записи",
            "«Удалить» стирает всё и подтверждает — в любой момент",
        ]),
        шаг("3", f"Короткая часть — {д['коротких']} вопросов", [
            "Кто обращается, телефон, адрес, что беспокоит сейчас, что нужно",
            "Ответы кнопками; «назад», «пропустить», «ответы» работают всегда",
            "Две-три минуты",
        ]),
        шаг("4", "Развилка", [
            "«Продолжить, я отвечу» — дальше подробная часть",
            f"«{э(вопросы.STOP_OPTION)}» — анкета закрывается, "
            "координатор дособирает по телефону",
            "Незавершённая анкета сохраняется и не теряется",
        ], "step--fork"),
        шаг("5", f"Подробная часть — ещё {д['подробных']} вопросов", [
            f"Самообслуживание: {э(утрата)}",
            f"Медицинские признаки: {э(медпризнаки)}",
            "Оборудование дома, оформленные документы, силы семьи",
        ]),
        шаг("6", "Правила относят обращение к маршруту", [
            f"Считаются утраченные параметры ({len(д['самообслуживание'])}), "
            f"медицинские признаки ({len(д['медпризнаки'])}) и затронутые сферы",
            f"{д['противоречий']} правил на противоречия: «ходит сам, но его моют» — "
            "считать уровень по такой анкете нельзя",
            "<b>Маршрут предлагается, решение принимает координатор</b>",
        ], "step--rules"),
        шаг("7", "Координатор", [
            "Карточка в CRM: ответы, переписка, вложения, история звонков",
            "Звонок в удобное время, которое человек отметил сам",
            "Передача в профильную организацию по маршруту",
        ]),
    ))

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Схема маршрутизации — АНО «СДУТ»</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700;800&display=swap">
<style>
:root {{
  --ground:#F6F1E8; --surface:#FFFCF7; --surface-warm:#EFE6D8;
  --ink:#16384A; --ink-soft:#33525E; --muted:#6E7C81;
  --line:#E4DACA; --line-soft:#EFE7DA;
  --accent:#2E6E63; --accent-deep:#1F5257; --accent-soft:#DFEAE6;
  --sand:#C09A6B; --sand-soft:#EDE0CC;
  --critical:#A93E32; --critical-soft:#F8E7E3;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);
  font-family:"Golos Text",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:11.5px;line-height:1.36;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.page{{width:210mm;height:297mm;overflow:hidden;margin:0 auto 8mm;padding:9mm 11mm;
  background:var(--surface);box-shadow:0 1px 3px rgba(22,56,74,.12)}}
h1{{font-size:20px;margin:0 0 2px;letter-spacing:-.3px}}
h2{{font-size:12px;margin:0 0 6px;text-transform:uppercase;letter-spacing:.8px;
  color:var(--accent-deep)}}
h3{{font-size:12.5px;margin:0 0 2px}}
.sub{{color:var(--muted);margin:0 0 9px;font-size:11px}}
.flow{{display:flex;flex-direction:column;gap:0}}
.step{{display:flex;gap:10px;background:var(--surface-warm);border-radius:10px;
  padding:6px 11px;border:1px solid var(--line)}}
.step--stop{{background:var(--critical-soft);border-color:#E8C9C2}}
.step--fork{{background:var(--sand-soft);border-color:#E0CDAE}}
.step--rules{{background:var(--accent-soft);border-color:#C6DCD5}}
.step__n{{flex:0 0 46px;font-weight:800;font-size:13px;color:var(--accent-deep);
  padding-top:1px}}
.step--stop .step__n{{color:var(--critical)}}
.step ul{{margin:0;padding-left:15px}}
.step li{{margin:1px 0}}
.arrow{{text-align:center;color:var(--muted);font-size:13px;line-height:1;
  padding:1px 0}}
.arrow span{{display:inline-block;transform:translateY(-1px)}}
table{{width:100%;border-collapse:collapse;margin-top:2px}}
td{{vertical-align:top;padding:5px 7px;border-top:1px solid var(--line-soft)}}
.r__code{{width:34px;font-weight:800;font-size:15px;color:var(--accent-deep)}}
.r--critical .r__code{{color:var(--critical)}}
.r--sand .r__code{{color:var(--sand)}}
.r--muted .r__code{{color:var(--muted)}}
.r__name{{width:33%}}
.r__name b{{display:block}}
.r__name span{{display:block;color:var(--muted);font-size:12px}}
.r__when i{{display:block;color:var(--accent);font-style:normal;font-weight:600;
  font-size:11px;text-transform:uppercase;letter-spacing:.4px}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px}}
.card{{background:var(--surface-warm);border:1px solid var(--line);
  border-radius:10px;padding:8px 11px}}
.card h3{{margin-bottom:5px}}
.card ul{{margin:0;padding-left:15px}}
.map{{list-style:none;margin:0;padding:0;columns:2;column-gap:14px}}
.map li{{display:flex;justify-content:space-between;gap:8px;
  border-bottom:1px dotted var(--line);padding:3px 0;break-inside:avoid}}
.map span{{color:var(--muted);font-variant-numeric:tabular-nums}}
.note{{background:var(--accent-soft);border-left:3px solid var(--accent);
  border-radius:0 8px 8px 0;padding:7px 11px;margin-top:8px}}
.foot{{margin-top:10px;padding-top:6px;border-top:1px solid var(--line);
  color:var(--muted);font-size:11px;display:flex;justify-content:space-between;gap:12px}}
@media print{{
  body{{background:#fff}}
  .page{{box-shadow:none;margin:0;width:auto;height:auto;padding:9mm 11mm;page-break-after:always}}
  @page{{size:A4;margin:0}}
}}
@media (max-width:820px){{
  .page{{width:auto;height:auto;overflow:visible;padding:16px}}
  .cols{{grid-template-columns:1fr}}
  .map{{columns:1}}
  .r__name{{width:auto}}
}}
</style>
</head>
<body>

<section class="page">
  <h1>Как обращение проходит через службу</h1>
  <p class="sub">АНО «Система долговременного ухода», Тольятти · проект «Точка входа» ·
  схема собрана из работающего кода {э(дата)}</p>

  <div class="flow">
    {шаги.replace('</div></div><div class="step', '</div></div>@@<div class="step').replace('@@', '<div class="arrow"><span>↓</span></div>')}
  </div>

  <div class="note">
    <b>Что бот не делает.</b> Не ставит диагнозов, не оценивает тяжесть состояния,
    не решает, вызывать ли скорую, не назначает лечение и дозировки,
    не признаёт нуждающимся и не устанавливает уровень нуждаемости.
    Всё это — врач, эксперты по оценке и уполномоченный орган.
  </div>

  <div class="foot">
    <span>Страница 1 из 2 · скелет</span>
    <span>Правила проверены на {д['случаев']} разобранных случаях: <code>python routing_rules.py</code></span>
  </div>
</section>

<section class="page">
  <h1>Пять маршрутов и нулевая ступень</h1>
  <p class="sub">Правила проверяются сверху вниз, срабатывает первое подходящее.
  Маршрут предлагается программой — решение принимает координатор.</p>

  <table>{маршруты}</table>

  <div class="cols">
    <div class="card">
      <h3>Если бот не понял</h3>
      <ul>
        <li>{д['ступеней']} разных ответов подряд, каждый с другим выходом:
            кнопка, «пропустить», свои слова, телефон, координатор, 103</li>
        <li>Ниже последней ступени бот не опускается — молчания нет
            ни при каком вводе</li>
        <li>Счётчик обнуляется, как только человек попал в понятное действие</li>
      </ul>
    </div>
    <div class="card">
      <h3>Оценочная шкала Приказа № 731</h3>
      <ul>
        <li>{д['шкала']['всего']} позиция, максимум {э(str(д['шкала']['максимум_по_шкале']).replace('.', ','))} баллов</li>
        <li>Анкета закрывает {д['шкала']['закрыто']} позиций —
            предел {э(str(д['шкала']['максимум_по_анкете']).replace('.', ','))} баллов</li>
        <li>Выше {э(д['шкала']['достижимый_уровень'].split()[0])} уровня
            по действующей анкете не подняться</li>
        <li>Уровень устанавливают два эксперта очно. Всё, что считает
            бот, — предварительно и со слов</li>
      </ul>
    </div>
  </div>

  <h2 style="margin-top:14px">База знаний: {д['статей']} статей в {len(д['ветви'])} ветвях</h2>
  <ul class="map">{ветви}</ul>

  <div class="note">
    <b>Куда идти, если ответа нет.</b> Человек пишет вопрос своими словами —
    бот ищет по всем материалам; не нашлось — предлагает темы кнопками;
    не подошло и это — сообщение попадает в карточку, и отвечает координатор.
    Тупика нет ни на одном шаге.
  </div>

  <div class="foot">
    <span>Страница 2 из 2 · маршруты и база</span>
    <span>Схема пересобирается: <code>python webapp/схема/сборка-схемы.py</code></span>
  </div>
</section>

</body>
</html>
"""


if __name__ == "__main__":
    данные = собрать()
    with open(ВЫХОД, "w", encoding="utf-8") as файл:
        файл.write(собрать_html(данные))
    print(f"Схема собрана: {ВЫХОД}")
    print(f"  вопросов {данные['вопросов']} ({данные['коротких']} + {данные['подробных']}), "
          f"маршрутов {len(данные['маршруты'])}, "
          f"статей {данные['статей']} в {len(данные['ветви'])} ветвях")

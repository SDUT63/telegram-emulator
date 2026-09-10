"""Модуль языковой модели: границы, деньги и молчание.

Сети здесь нет и не будет: проверяется всё, что должно работать до
первого сетевого вызова и вместо него. Самое важное — что бот остаётся
рабочим, когда модель выключена, недоступна или исчерпала бюджет.
"""
import pytest

from ai import assistant, budget, deident, provider


@pytest.fixture
def бюджет(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "FILE", str(tmp_path / "ai_budget.json"))
    monkeypatch.setattr(budget, "LIMIT", 100.0)
    return budget


@pytest.fixture(autouse=True)
def без_ключей(monkeypatch):
    """Ни один тест не должен случайно уйти в сеть с чужим ключом."""
    for имя in ("AI_PROVIDER", "YANDEX_API_KEY", "YANDEX_FOLDER_ID",
                "GIGACHAT_AUTH_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(имя, raising=False)


# ------------------------------------------------------- выключено по умолчанию

def test_по_умолчанию_модель_выключена():
    p = provider.get()
    assert p.name == "off"
    assert p.available() is False
    assert assistant.on() is False


def test_без_ключа_ничего_не_отправляется(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "yandex-lite")
    p = provider.get()
    готов, почему = p.ready()
    assert готов is False
    assert "YANDEX_API_KEY" in почему


def test_яндексу_нужен_и_каталог(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "yandex-lite")
    monkeypatch.setenv("YANDEX_API_KEY", "ключ")
    готов, почему = provider.get().ready()
    assert готов is False and "FOLDER" in почему


def test_ответ_без_ключа_это_none():
    assert assistant.reference("что такое долговременный уход") is None


def test_незнакомый_провайдер_это_выключено(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "чат-жпт-из-подвала")
    assert provider.get().name == "off"


# -------------------------------------------------------------- деньги

def test_цена_считается_по_токенам(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "yandex-lite")
    p = provider.get()
    цена = (1000 * p.price["in"] + 1000 * p.price["out"]) / 1000
    assert цена == pytest.approx(p.price["in"] + p.price["out"])


def test_свою_цену_можно_задать(monkeypatch):
    """Прайс меняется чаще кода, а расход идёт в отчёт по гранту."""
    monkeypatch.setenv("AI_PROVIDER", "yandex-lite")
    monkeypatch.setenv("AI_PRICE_IN", "0,55")
    assert provider.get().price["in"] == 0.55


def test_бюджет_копится_и_переживает_перезапуск(бюджет):
    бюджет.add(3.5, 100, 50)
    бюджет.add(1.5, 20, 10)
    assert бюджет.spent() == pytest.approx(5.0)
    assert бюджет.calls() == 2
    assert бюджет.left() == pytest.approx(95.0)


def test_исчерпанный_бюджет_останавливает_вызовы(бюджет):
    бюджет.add(бюджет.LIMIT + 1)
    assert бюджет.allowed() is False
    assert assistant.on() is False
    assert assistant.reference("уровни ухода") is None


# ------------------------------------------------------- деперсонализация

@pytest.mark.parametrize("текст, чего_быть_не_должно", [
    ("Звоните 8 917 123-45-67", "917"),
    ("Телефон +7(917)1234567", "1234567"),
    ("Пишите на mail@example.ru", "example"),
    ("Родилась 12.05.1948", "1948"),
    ("ул. Ворошилова, д. 19, кв. 5", "Ворошилова"),
    ("Полис 1234567890123456", "1234567890123456"),
])
def test_из_текста_уходит_всё_личное(текст, чего_быть_не_должно):
    assert чего_быть_не_должно not in deident.clean(текст)


def test_имя_вырезается_по_значению():
    """Имя не поймать выражением — оно берётся из анкеты и режется точно."""
    вышло = deident.clean("Анна Петровна почти не встаёт", "Анна Петровна")
    assert "Анна" not in вышло and "[имя]" in вышло


def test_короткое_имя_не_режется_по_подстроке():
    """«Ия» встречается внутри других слов — вырезать её опасно."""
    assert "операция" in deident.clean("операция была весной", "Ия")


def test_имя_и_телефон_не_уходят_никогда():
    ответы = {"name": "Мария", "phone": "89171234567", "mobility": "Не встаёт"}
    безопасно = deident.safe_answers(ответы)
    assert "name" not in безопасно and "phone" not in безопасно
    assert безопасно["mobility"] == "Не встаёт"


def test_проверка_падает_а_не_отправляет():
    """Последний рубеж: если личное осталось, вызов не должен состояться."""
    with pytest.raises(ValueError):
        deident.assert_clean("перезвоните на 8 917 123-45-67")


def test_ошибка_говорит_что_это_ошибка_кода():
    try:
        deident.assert_clean("почта тут: some@one.ru")
    except ValueError as беда:
        assert "ошибка в коде" in str(беда)


# ---------------------------------------------------------------- границы

def test_подсказка_запрещает_врачебные_решения():
    for нельзя in ("диагноз", "скорой", "маршрут"):
        assert нельзя in assistant.GUARD.lower()
    assert "103" in assistant.GUARD


def test_зарубежный_провайдер_не_видит_сведений_о_здоровье(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ключ")
    assert assistant.draft_card({"mobility": "Не встаёт"}) is None
    assert assistant.read_free_text("мама лежит, есть пролежень") is None


def test_справка_отвечает_только_по_базе():
    """Нет статей — нет ответа. Придумывать модели не разрешено."""
    assert assistant.reference("как починить стиральную машину") is None


def test_состояние_читается_человеком():
    строка = assistant.status()
    assert "Провайдер" in строка and "Статей в базе" in строка

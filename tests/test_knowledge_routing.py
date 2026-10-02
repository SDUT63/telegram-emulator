from knowledge import ответ_без_модели, по_заголовку


def test_palliative_and_sdu_are_not_mixed() -> None:
    question = "Хотел бы узнать как мне встать на учёт в паллиативной помощи"
    answer = ответ_без_модели(question)

    assert answer is not None
    assert "не смешивать" in answer.lower()
    assert "Анкета СДУ" in answer


def test_palliative_article_is_in_the_knowledge_base() -> None:
    article = по_заголовку("Как получить паллиативную помощь и при чём здесь анкета СДУ")
    assert article is not None
    # Статья обязана назвать паллиативную медицинскую помощь своим именем —
    # в тексте она стоит в косвенном падеже, как и положено живой фразе.
    assert "паллиативной медицинской помощи" in article.текст.lower()
    assert "движение" in article.текст.lower()


def test_palliative_article_is_reachable_from_the_map() -> None:
    """Статья, до которой нельзя дойти кнопками, для человека не существует."""
    import knowledge

    assert knowledge.вне_карты() == []
    article = по_заголовку("Как получить паллиативную помощь и при чём здесь анкета СДУ")
    ветвь = next(в for в in knowledge.КАРТА if в.название == "Паллиативная помощь")
    assert article.раздел in ветвь.разделы

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
    assert "Паллиативная медицинская помощь" in article.текст
    assert "движение" in article.текст.lower()

from max_ui import _article_callback


def test_article_callback_is_short_and_deterministic() -> None:
    title = "Очень длинный заголовок статьи, в котором первые шестьдесят символов совпадают с другой статьёй и раньше приводили к коллизии"
    callback = _article_callback(title)

    assert callback.startswith("k:@")
    assert len(callback) == 19
    assert title[:60] not in callback
    assert _article_callback(title) == callback
    assert _article_callback(title + "!") != callback

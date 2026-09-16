from metrics import Metrics


def test_metrics_render_escapes_labels_and_exposes_values():
    metrics = Metrics()
    metrics.inc("sdut_events_total", {"kind": 'message"x'})
    metrics.set("sdut_storage_up", 1)
    text = metrics.render()
    assert "sdut_events_total{kind=\"message\\\"x\"} 1" in text
    assert "sdut_storage_up 1" in text
    assert "sdut_process_uptime_seconds" in text


def test_тип_метрики_объявлен_один_раз_на_имя():
    """Prometheus отвергает весь ответ при второй строке TYPE для имени.

    Раньше TYPE печаталась на каждый ряд, и мониторинг молча ломался,
    как только у метрики появлялась метка: ошибка «second TYPE line for
    metric name» видна в Prometheus, а не в боте.
    """
    from metrics import Metrics

    m = Metrics()
    m.set("sdut_funnel_people", 10, {"stage": "Дали согласие"})
    m.set("sdut_funnel_people", 20, {"stage": "Написали боту"})
    m.inc("sdut_outbox_deliveries_total")
    m.inc("sdut_outbox_delivery_failures_total", {"error_class": "TimeoutError"})
    m.inc("sdut_outbox_delivery_failures_total", {"error_class": "OSError"})

    имена = [
        строка.split()[2]
        for строка in m.render().splitlines()
        if строка.startswith("# TYPE")
    ]
    повторы = [и for и in set(имена) if имена.count(и) > 1]
    assert повторы == [], f"TYPE объявлен дважды: {повторы}"


def test_все_ряды_метрики_идут_под_своим_типом():
    """Ряд без предшествующей TYPE своего имени — тоже сломанный ответ."""
    from metrics import Metrics

    m = Metrics()
    m.set("sdut_funnel_people", 1, {"stage": "Оставили телефон"})
    m.inc("sdut_outbox_deliveries_total", value=3)

    текущий = None
    for строка in m.render().splitlines():
        if строка.startswith("# TYPE"):
            текущий = строка.split()[2]
        elif строка.startswith("#") or not строка:
            continue
        else:
            assert текущий and строка.startswith(текущий), строка

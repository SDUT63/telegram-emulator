from metrics import Metrics


def test_metrics_render_escapes_labels_and_exposes_values():
    metrics = Metrics()
    metrics.inc("sdut_events_total", {"kind": 'message"x'})
    metrics.set("sdut_storage_up", 1)
    text = metrics.render()
    assert "sdut_events_total{kind=\"message\\\"x\"} 1" in text
    assert "sdut_storage_up 1" in text
    assert "sdut_process_uptime_seconds" in text

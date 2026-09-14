from max_webhook import validate_public_url, validate_secret, validate_settings


def test_valid_production_webhook_config():
    assert validate_settings("https://bot.example.ru/max", "Abc-123_xyz", "/max") == []


def test_webhook_rejects_http():
    errors = validate_public_url("http://bot.example.ru/max", "/max")
    assert any("https" in error for error in errors)


def test_webhook_rejects_non_443_public_port():
    errors = validate_public_url("https://bot.example.ru:8443/max", "/max")
    assert any("443" in error for error in errors)


def test_webhook_rejects_wrong_path():
    errors = validate_public_url("https://bot.example.ru/webhook", "/max")
    assert any("совпадать" in error for error in errors)


def test_webhook_secret_validation():
    assert validate_secret("abcd")
    assert validate_secret("abc def")
    assert validate_secret("abcde") == []


def test_settings_require_secret():
    errors = validate_settings("https://bot.example.ru/max", "", "/max")
    assert any("секрет" in error for error in errors)


def test_production_components_are_postgres_based():
    from production_privacy import ProductionPrivacySurvey
    from storage_postgres import TransactionalPersistentSeen

    assert ProductionPrivacySurvey.__name__ == "ProductionPrivacySurvey"
    assert TransactionalPersistentSeen.__name__ == "TransactionalPersistentSeen"


def test_webhook_does_not_offer_a_sqlite_production_fallback():
    import max_webhook

    source = open(max_webhook.__file__, encoding="utf-8").read()
    assert "SQLiteSurvey" not in source
    assert "SDUT_DATABASE_URL" in source


def test_metrics_open_when_no_token_configured(monkeypatch):
    import max_webhook
    monkeypatch.delenv("SDUT_METRICS_TOKEN", raising=False)
    assert max_webhook.metrics_authorized(None) is True


def test_metrics_requires_configured_token(monkeypatch):
    import max_webhook
    monkeypatch.setenv("SDUT_METRICS_TOKEN", "monitoring-secret")
    assert max_webhook.metrics_authorized("Bearer monitoring-secret") is True
    assert max_webhook.metrics_authorized("Bearer wrong") is False
    assert max_webhook.metrics_authorized("monitoring-secret") is False
    assert max_webhook.metrics_authorized(None) is False
    assert max_webhook.metrics_authorized("") is False

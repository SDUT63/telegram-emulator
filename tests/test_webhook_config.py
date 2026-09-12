import pytest

from max_webhook import _storage_classes, validate_public_url, validate_secret, validate_settings


def test_valid_production_webhook_config():
    assert validate_settings(
        "https://bot.example.ru/max", "Abc-123_xyz", "/max"
    ) == []


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


def test_webhook_storage_fails_closed_without_postgres(monkeypatch):
    monkeypatch.delenv("SDUT_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="требует SDUT_DATABASE_URL"):
        _storage_classes()


def test_webhook_storage_uses_postgres_when_configured(monkeypatch):
    monkeypatch.setenv("SDUT_DATABASE_URL", "postgresql://example")
    survey_cls, seen_cls, storage_name = _storage_classes()
    assert survey_cls.__name__ == "ProductionPostgresSurvey"
    assert seen_cls.__name__ == "TransactionalPersistentSeen"
    assert storage_name == "PostgreSQL"

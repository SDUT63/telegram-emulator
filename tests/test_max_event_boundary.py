import asyncio

import max_production_dispatcher as dispatcher
from outbox_postgres import safe_error_code
from storage_postgres import _TX_EVENT


def test_provider_event_id_normalization_rejects_missing_values():
    assert dispatcher._event_id(None) is None
    assert dispatcher._event_id("") is None
    assert dispatcher._event_id("   ") is None
    assert dispatcher._event_id(" mid-123 ") == "mid-123"
    assert dispatcher._event_id(123) == "123"


def test_with_event_id_binds_provider_id_for_transaction_boundary():
    seen = []

    async def mutation():
        seen.append(_TX_EVENT.get())

    asyncio.run(dispatcher._with_event_id("provider-evt-1", mutation))

    assert seen == ["provider-evt-1"]
    assert _TX_EVENT.get() is None


def test_with_event_id_restores_outer_event_id_after_nested_dispatch():
    seen = []

    async def nested():
        seen.append(("nested", _TX_EVENT.get()))

    async def outer():
        seen.append(("outer-before", _TX_EVENT.get()))
        await dispatcher._with_event_id("inner", nested)
        seen.append(("outer-after", _TX_EVENT.get()))

    asyncio.run(dispatcher._with_event_id("outer", outer))

    assert seen == [
        ("outer-before", "outer"),
        ("nested", "inner"),
        ("outer-after", "outer"),
    ]
    assert _TX_EVENT.get() is None


def test_safe_error_code_does_not_store_raw_provider_error_text():
    secret = "patient=Ivanov phone=+79991234567 body=very-sensitive-message"
    code = safe_error_code(secret)

    assert code.startswith("provider_error:ProviderError:")
    assert secret not in code
    assert "Ivanov" not in code
    assert "+79991234567" not in code
    assert "very-sensitive-message" not in code
    assert len(code.rsplit(":", 1)[-1]) == 16


def test_safe_error_code_uses_exception_class_without_exception_text():
    error = ValueError("user_id=42 payload=secret")
    code = safe_error_code(error)

    assert code.startswith("provider_error:ValueError:")
    assert "user_id=42" not in code
    assert "payload=secret" not in code

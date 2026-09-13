from production_privacy import ProductionPrivacySurvey


def test_production_privacy_disables_plaintext_csv_export():
    survey = object.__new__(ProductionPrivacySurvey)
    try:
        survey.export_csv()
    except RuntimeError as exc:
        assert "plaintext CSV export is disabled" in str(exc)
    else:
        raise AssertionError("production CSV export must be disabled")


def test_deletion_boundary_contains_common_variants():
    from max_production_dispatcher import DELETION_WORDS
    expected = {"удалить", "удалите", "сотри", "сотрите", "забудь меня", "забудьте меня", "/delete"}
    assert expected <= DELETION_WORDS

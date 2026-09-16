from production_privacy import ProductionPrivacySurvey


def test_production_privacy_disables_plaintext_csv_export():
    survey = object.__new__(ProductionPrivacySurvey)
    assert survey.export_csv() is None


def test_deletion_boundary_contains_common_variants():
    from max_production_dispatcher import DELETION_WORDS
    expected={"удалить","удалите","сотри","сотрите","забудь меня","забудьте меня","/delete"}
    assert expected <= DELETION_WORDS

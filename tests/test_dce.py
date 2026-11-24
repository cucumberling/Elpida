from datetime import date

from elpida.skills import dce


def test_option_expiry_is_12th_weekday_of_previous_month():
    assert dce.option_expiry("LH2605") == date(2026, 4, 16)
    assert dce.option_expiry("LH2701") == date(2026, 12, 16)


def test_individual_exit_deadline():
    assert dce.last_month_for_individuals("LH2605") == date(2026, 4, 30)
    assert dce.last_month_for_individuals("LH2701") == date(2026, 12, 31)

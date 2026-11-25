"""Small report boundary checks without database setup."""

from datetime import date
from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError

from .owner_statistics import OwnerStatistics, parse_statistics_filters, totals_payload, unit_name


def test_default_range_is_unbounded_and_default_grain_is_day():
    filters = parse_statistics_filters({})
    assert filters["grain"] == "day"
    assert "date_from" not in filters and "date_to" not in filters
    with pytest.raises(ValidationError):
        parse_statistics_filters({"date_from": "2026-11-20", "date_to": "2026-11-19"})


def test_custom_range_clips_natural_year_without_expanding_dates():
    report = OwnerStatistics.__new__(OwnerStatistics)
    report.filters = {"grain": "year", "date_from": date(2026, 2, 3), "date_to": date(2026, 8, 9)}
    row = {"group_key": "2026-01-01"}
    report.period_bounds(row)
    assert row["period_end"] == date(2026, 12, 31)
    assert row["range_start"] == date(2026, 2, 3)
    assert row["range_end"] == date(2026, 8, 9)
    assert row["partial_period"]


def test_units_require_chinese_names_instead_of_falling_back_to_codes():
    assert unit_name("瓶") == "瓶"
    assert unit_name("EA") == unit_name(None) == "单位待核对"


def test_unit_quantities_stay_separate_while_money_adds_exactly():
    payload = totals_payload(
        [
            dict(
                category="inbound",
                base_unit=code,
                base_unit_name=name,
                currency="CNY",
                qty=Decimal(qty),
                amount=Decimal(amount),
                priced_count=1,
                unpriced_count=pending,
            )
            for code, name, qty, amount, pending in [
                ("EA", "瓶", "100", "9007199254740993.01", 0),
                ("BOX", "箱", "8", "0.02", 1),
            ]
        ]
    )
    assert [row["qty"] for row in payload["totals"]] == ["100", "8"]
    assert payload["amounts"][0]["amount"] == "9007199254740993.03"
    assert payload["amounts"][0]["amount_status"] == "PARTIAL"

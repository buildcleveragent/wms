"""Packaging validation must reach duplicate detection without unresolved names."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from allapp.core.utils.uom import validate_packages_against_base_uom


def product_with_packages(*, base_factor=1, duplicate=None):
    product = SimpleNamespace(base_uom_id=1, code="TEST-P", packages=Mock())
    base_query = Mock()
    base_query.only.return_value.first.return_value = SimpleNamespace(
        qty_in_base=Decimal(base_factor)
    )
    duplicate_query = Mock()
    grouped = duplicate_query.values.return_value.order_by.return_value.annotate.return_value
    grouped.filter.return_value.first.return_value = duplicate
    product.packages.filter.side_effect = [base_query, duplicate_query]
    return product


def test_valid_packaging_passes_duplicate_check():
    validate_packages_against_base_uom(product_with_packages())


def test_duplicate_uom_reports_the_conflicting_unit():
    with pytest.raises(ValueError, match="重复的 UOM.*9"):
        validate_packages_against_base_uom(product_with_packages(duplicate={"uom_id": 9}))


def test_non_unit_base_package_is_rejected():
    with pytest.raises(ValueError, match="必须 1:1"):
        validate_packages_against_base_uom(product_with_packages(base_factor=2))

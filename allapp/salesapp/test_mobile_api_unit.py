from decimal import Decimal
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from . import mobile_api
from .mobile_api import (
    _catalog_product_payload,
    _check_stock_requirements,
    _error_message,
)


class FakePackages:
    def all(self):
        return []


def test_check_stock_requirements_rejects_aggregate_over_stock():
    product = SimpleNamespace(code="P001")

    with pytest.raises(ValidationError) as exc_info:
        _check_stock_requirements(
            {
                1: {
                    "product": product,
                    "required_base_qty": Decimal("11.000"),
                }
            },
            {1: Decimal("10.000")},
        )

    assert "P001 可用库存不足" in str(exc_info.value.detail["stock"])


def test_error_message_extracts_nested_validation_error_text():
    error = ValidationError({"order_uom": ["订货单位必须为 CTN"]})

    assert _error_message(error) == "订货单位必须为 CTN"


def test_check_stock_requirements_allows_backorder():
    product = SimpleNamespace(code="P001")

    _check_stock_requirements(
        {
            1: {
                "product": product,
                "required_base_qty": Decimal("11.000"),
            }
        },
        {1: Decimal("10.000")},
        allow_backorder=True,
    )


def test_catalog_payload_marks_invalid_policy_uom_unorderable(monkeypatch):
    product = SimpleNamespace(
        id=1,
        code="P001",
        sku="P001",
        name="测试商品",
        spec="",
        base_uom=SimpleNamespace(code="EA", name="件"),
        packages=FakePackages(),
    )
    monkeypatch.setattr(
        mobile_api, "compute_price_for_line", lambda *args, **kwargs: Decimal("12.5000")
    )
    monkeypatch.setattr(
        mobile_api,
        "_policy_for",
        lambda *args, **kwargs: {
            "source": "customer",
            "order_uom": "CTN",
            "min_order_qty": Decimal("0"),
            "multiple_qty": Decimal("0"),
        },
    )

    payload = _catalog_product_payload(
        SimpleNamespace(),
        owner=SimpleNamespace(),
        customer=None,
        product=product,
        available_qty=Decimal("10.000"),
        order_date=None,
    )

    assert payload["orderable"] is False
    assert payload["orderable_reason"] == "订货单位 CTN 未配置换算"

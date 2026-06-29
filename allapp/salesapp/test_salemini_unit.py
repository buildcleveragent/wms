from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from allapp.salesapp.management.commands.validate_sale_mini_data_accuracy import (
    IssueCollector,
    cents,
    money,
)
from allapp.salesapp.salemini_api import (
    _display_status,
    _display_status_q,
    _effective_rules,
    _fulfillment_for_order_payload,
    _fulfillment_preview_payload,
    _is_multiple,
)


def test_display_status_maps_outbound_state_to_buyer_words():
    order = SimpleNamespace(
        approval_status="OWNER_APPROVED",
        submit_status="SUBMITTED",
        is_closed=False,
    )
    mapping = SimpleNamespace(payment_status="OFFLINE")

    assert _display_status(order, mapping) == ("WAIT_SHIP", "待发货")

    mapping.payment_status = "UNPAID"
    assert _display_status(order, mapping) == ("WAIT_PAY", "待付款")
    mapping.payment_status = "OFFLINE"

    order.approval_status = "WHS_APPROVED"
    assert _display_status(order, mapping) == ("WAIT_SHIP", "待发货")

    order.approval_status = "CANCELLED"
    assert _display_status(order, mapping) == ("CANCELLED", "已取消")


def test_display_status_q_uses_database_filter_conditions():
    assert _display_status_q("PENDING_REVIEW") == Q(
        outbound_order__is_closed=False,
        outbound_order__submit_status="SUBMITTED",
        outbound_order__approval_status="OWNER_PENDING",
    )
    assert _display_status_q("WAIT_WAREHOUSE") == Q(
        outbound_order__is_closed=False,
        outbound_order__submit_status="SUBMITTED",
        outbound_order__approval_status__in=["OWNER_APPROVED", "WHS_PENDING"],
    )
    assert _display_status_q("WAIT_PICK") == Q(
        outbound_order__is_closed=False,
        outbound_order__submit_status="SUBMITTED",
        outbound_order__approval_status="WHS_APPROVED",
    )


def test_effective_rules_merge_config_policy_and_pick_multiple():
    product = SimpleNamespace(min_pick_multiple=6)
    config = SimpleNamespace(min_order_qty=Decimal("2"), multiple_qty=Decimal("1"))
    policy = {
        "min_order_qty": Decimal("3"),
        "multiple_qty": Decimal("0"),
    }

    min_qty, multiple_qty = _effective_rules(
        product,
        config,
        policy,
        Decimal("2"),
    )

    assert min_qty == Decimal("3")
    assert multiple_qty == Decimal("3.000")


def test_is_multiple_accepts_exact_decimal_multiples():
    assert _is_multiple(Decimal("6.000"), Decimal("3.000"))
    assert not _is_multiple(Decimal("5.000"), Decimal("3.000"))


def test_pickup_fulfillment_only_requires_contact_and_phone():
    owner = SimpleNamespace(name="测试商家")
    data = {
        "delivery_method": "PICKUP",
        "contact": "张三",
        "contact_phone": "13800000000",
        "ship_to": "",
    }

    contact, phone, ship_to, address = _fulfillment_for_order_payload(owner, None, data)
    preview = _fulfillment_preview_payload(owner, None, data)

    assert contact == "张三"
    assert phone == "13800000000"
    assert ship_to == "客户自提 - 测试商家"
    assert address is None
    assert preview["full_address"] == "客户自提 - 测试商家"


def test_delivery_fulfillment_still_requires_shipping_address():
    owner = SimpleNamespace(name="测试商家")
    data = {
        "delivery_method": "OWN_TRUCK",
        "contact": "张三",
        "contact_phone": "13800000000",
        "ship_to": "",
    }

    try:
        _fulfillment_for_order_payload(owner, None, data)
    except ValidationError as exc:
        assert "完整收货联系人" in str(exc.detail)
    else:
        raise AssertionError("delivery fulfillment must require a ship_to address")


def test_sale_mini_accuracy_money_and_cents_rounding():
    assert money("1.005") == Decimal("1.01")
    assert money(None) == Decimal("0.00")
    assert cents("1.005") == 101
    assert cents(Decimal("0.004")) == 0


def test_sale_mini_accuracy_issue_collector_counts_all_and_limits_samples():
    issues = IssueCollector(limit=2)

    issues.add("alpha", "first")
    issues.add("alpha", "second")
    issues.add("beta", "third")

    assert issues.total == 3
    assert issues.by_code == {"alpha": 2, "beta": 1}
    assert len(issues.items) == 2

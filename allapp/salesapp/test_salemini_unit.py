from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from PIL import Image
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
    _stock_payload,
    _uom_name,
)
from allapp.salesapp.salemini_review_api import (
    SaleMiniReviewDraftSerializer,
    _validate_uploaded_image,
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
    config = SimpleNamespace(
        enable_qty_rules=True,
        min_order_qty=Decimal("2"),
        multiple_qty=Decimal("1"),
    )
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


def test_effective_rules_default_to_unrestricted_when_switch_is_disabled():
    product = SimpleNamespace(min_pick_multiple=1000)
    config = SimpleNamespace(
        enable_qty_rules=False,
        min_order_qty=Decimal("1000"),
        multiple_qty=Decimal("1000"),
    )
    policy = {
        "min_order_qty": Decimal("500"),
        "multiple_qty": Decimal("500"),
    }

    min_qty, multiple_qty = _effective_rules(
        product,
        config,
        policy,
        Decimal("1"),
    )

    assert min_qty == Decimal("1")
    assert multiple_qty == Decimal("1")


def test_is_multiple_accepts_exact_decimal_multiples():
    assert _is_multiple(Decimal("6.000"), Decimal("3.000"))
    assert not _is_multiple(Decimal("5.000"), Decimal("3.000"))
    assert _is_multiple(Decimal("2.000"), Decimal("3.000"), Decimal("2.000"))
    assert _is_multiple(Decimal("5.000"), Decimal("3.000"), Decimal("2.000"))
    assert not _is_multiple(Decimal("3.000"), Decimal("3.000"), Decimal("2.000"))


def test_buyer_payload_uses_chinese_uom_name_without_changing_code():
    class EmptyPackages:
        def all(self):
            return []

    product = SimpleNamespace(
        base_uom=SimpleNamespace(code="PING", name="瓶"),
        packages=EmptyPackages(),
        min_pick_multiple=1,
    )
    config = SimpleNamespace(stock_display="EXACT")

    stock = _stock_payload(config, product, Decimal("552"))

    assert _uom_name(product, "PING") == "瓶"
    assert stock["display"] == "552.000 瓶"
    assert stock["base_uom"] == "PING"
    assert stock["base_uom_name"] == "瓶"


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
    assert ship_to == "客户自提"
    assert owner.name not in ship_to
    assert address is None
    assert preview["full_address"] == "客户自提"
    assert owner.name not in preview["full_address"]


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


def test_review_scores_and_content_are_strictly_validated():
    valid = SaleMiniReviewDraftSerializer(
        data={
            "order_line_id": 1,
            "quality_score": 5,
            "delivery_score": 4,
            "overall_score": 3,
            "content": "真实购买评价",
            "is_anonymous": True,
        }
    )
    invalid = SaleMiniReviewDraftSerializer(
        data={
            "order_line_id": 1,
            "quality_score": 0,
            "delivery_score": 6,
            "overall_score": 5,
            "content": "评" * 1001,
        }
    )

    assert valid.is_valid(), valid.errors
    assert not invalid.is_valid()
    assert set(invalid.errors) == {"quality_score", "delivery_score", "content"}


def test_review_image_validation_reads_real_image_content():
    content = BytesIO()
    Image.new("RGB", (32, 24), color=(22, 119, 255)).save(content, format="PNG")
    uploaded = SimpleUploadedFile(
        "claimed.jpg", content.getvalue(), content_type="image/jpeg"
    )

    width, height = _validate_uploaded_image(uploaded)

    assert (width, height) == (32, 24)
    assert uploaded.name == "review.png"


def test_review_image_validation_rejects_fake_image():
    uploaded = SimpleUploadedFile(
        "fake.jpg", b"not-an-image", content_type="image/jpeg"
    )

    try:
        _validate_uploaded_image(uploaded)
    except ValidationError as exc:
        assert "损坏" in str(exc.detail)
    else:
        raise AssertionError("fake review image must be rejected")

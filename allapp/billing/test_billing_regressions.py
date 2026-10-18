"""Financial invariants for repricing and receipt mutations (synthetic data only)."""

import datetime as dt
import queue
import threading
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections, transaction
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.billing.admin import PaymentAllocationInline
from allapp.billing.enums import (
    BillDocumentStatus,
    BillPaymentStatus,
    BillStatus,
    CalcMethod,
    CapMode,
    ChargeType,
    MetricType,
    PaymentReceiptStatus,
    PeriodStatus,
    PricingStatus,
)
from allapp.billing.models import (
    Bill,
    BillingAccrual,
    BillingEvent,
    BillingMetricDaily,
    BillingPeriod,
    BillingRule,
    PaymentAllocation,
    PaymentReceipt,
)
from allapp.billing.services.accrual import accrue_metrics_for_date
from allapp.billing.services.integrity import reprice_unpriced_events
from allapp.billing.services.receivables import post_receipt, reverse_receipt
from allapp.locations.models import Warehouse


@pytest.fixture
def scope(db):
    owner = Owner.objects.create(code="REG-OWNER", name="Synthetic regression owner")
    warehouse = Warehouse.objects.create(code="REG-WH", name="Synthetic regression warehouse")
    period = BillingPeriod.objects.create(
        owner=owner,
        warehouse=warehouse,
        label="REG-2026-07",
        start_date=dt.date(2026, 7, 1),
        end_date=dt.date(2026, 7, 31),
        status=PeriodStatus.INVOICED,
        currency="CNY",
    )
    bill = Bill.objects.create(
        owner=owner,
        warehouse=warehouse,
        period=period,
        invoice_no="REG-INVOICE",
        issue_date=dt.date(2026, 8, 1),
        currency="CNY",
        subtotal=D("100"),
        tax_total=D("0"),
        total=D("100"),
        status=BillStatus.ISSUED,
        document_status=BillDocumentStatus.ISSUED,
    )
    return SimpleNamespace(owner=owner, warehouse=warehouse, bill=bill)


def make_receipt(scope, *, allocate=True, number="REG-RECEIPT"):
    receipt = PaymentReceipt.objects.create(
        owner=scope.owner,
        warehouse=scope.warehouse,
        currency="CNY",
        receipt_no=number,
        receipt_date=dt.date(2026, 8, 10),
        amount=D("100"),
    )
    if allocate:
        PaymentAllocation.objects.create(receipt=receipt, bill=scope.bill, amount=D("100"))
    return receipt


def client_for(scope, role="owner"):
    user = get_user_model().objects.create_user(username=f"reg-{role}")
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="billing"))
    if role == "superuser":
        user.is_superuser = True
        user.save()
    elif role == "legacy":
        user.owner = scope.owner
        user.save()
    else:
        UserRoleScope.objects.create(
            user=user,
            role=(
                UserRoleScope.Role.OWNER_MANAGER
                if role == "owner"
                else UserRoleScope.Role.WAREHOUSE_MANAGER
            ),
            **({"owner": scope.owner} if role == "owner" else {"warehouse": scope.warehouse}),
        )
    client = APIClient()
    client.force_authenticate(user)
    return client, user


@pytest.mark.parametrize(
    "date,expected",
    [
        (dt.date(2026, 2, 28), "11.07"),
        (dt.date(2024, 2, 29), "10.69"),
        (dt.date(2026, 4, 30), "10.33"),
        (dt.date(2026, 7, 31), "10.00"),
    ],
)
@pytest.mark.parametrize("limited", [False, True])
def test_monthly_repricing_matches_daily_accrual(scope, date, expected, limited):
    other = Owner.objects.create(code="REG-NORMAL", name="Normal pricing owner")
    for owner in (scope.owner, other):
        BillingMetricDaily.objects.create(
            owner=owner,
            warehouse=scope.warehouse,
            service_date=date,
            metric_type=MetricType.AREA_M2,
            value=D("100"),
            source="MANUAL",
        )
    args = dict(owner_id=scope.owner.pk, warehouse_id=scope.warehouse.pk, service_date=date)
    accrue_metrics_for_date(**args)
    event = BillingEvent.objects.get(owner=scope.owner)
    assert event.pricing_status == PricingStatus.UNPRICED
    BillingRule.objects.create(
        warehouse=scope.warehouse,
        charge_type=ChargeType.STORAGE,
        calc_method=CalcMethod.PER_AREA_MONTH,
        unit_price=D("3.10"),
        taxable=True,
        tax_rate=D("0.06"),
        **(
            dict(min_charge=D("12"), cap_mode=CapMode.PER_DAY, cap_amount=D("11"))
            if limited
            else {}
        ),
    )
    reprice_args = dict(
        owner_id=scope.owner.pk, warehouse_id=scope.warehouse.pk, date_from=date, date_to=date
    )
    before = BillingEvent.objects.filter(pk=event.pk).values().get()
    reprice_unpriced_events(**reprice_args, dry_run=True)
    assert not BillingAccrual.objects.filter(owner=scope.owner).exists()
    assert BillingEvent.objects.filter(pk=event.pk).values().get() == before
    reprice_unpriced_events(**reprice_args, dry_run=False)
    accrue_metrics_for_date(**{**args, "owner_id": other.pk})
    normal = BillingAccrual.objects.get(owner=other)
    repriced = BillingAccrual.objects.get(event=event)
    assert repriced.amount == normal.amount == D("11" if limited else expected)
    assert (
        repriced.tax_amount == normal.tax_amount == (repriced.amount * D(".06")).quantize(D(".01"))
    )
    assert repriced.unit_price == normal.unit_price
    reprice_unpriced_events(**reprice_args, dry_run=False)
    assert accrue_metrics_for_date(**args) == (0, 0)
    assert BillingAccrual.objects.filter(event=event).count() == 1


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize(
    "role,fields,allowed",
    [
        ("owner", (), True),
        ("owner", ("owner",), False),
        ("owner", ("warehouse",), True),
        ("owner", ("owner", "warehouse"), False),
        ("warehouse", (), True),
        ("warehouse", ("owner",), True),
        ("warehouse", ("warehouse",), False),
        ("warehouse", ("owner", "warehouse"), False),
        ("superuser", ("owner", "warehouse"), True),
        ("legacy", (), False),
    ],
)
def test_receipt_update_validates_final_scope(scope, method, role, fields, allowed, settings):
    # Even when legacy read access is enabled, it must never grant write access.
    settings.WMS_ACCESS_SCOPE_LEGACY_FALLBACK = True
    receipt = make_receipt(scope, allocate=False)
    client, _ = client_for(scope, role)
    other_owner = Owner.objects.create(code="REG-OTHER", name="Other owner")
    other_warehouse = Warehouse.objects.create(code="REG-OTHER", name="Other warehouse")
    before = PaymentReceipt.objects.filter(pk=receipt.pk).values().get()
    payload = {"memo": "changed memo"}
    if method == "put":
        payload.update(
            receipt_no=receipt.receipt_no,
            receipt_date="2026-08-10",
            amount="100.00",
            currency="CNY",
            owner=scope.owner.pk,
            warehouse=scope.warehouse.pk,
        )
    for field in fields:
        payload[field] = other_owner.pk if field == "owner" else other_warehouse.pk
    response = getattr(client, method)(
        f"/api/billing/payment-receipts/{receipt.pk}/", payload, format="json"
    )
    assert response.status_code == (200 if allowed else 403), response.data
    after = PaymentReceipt.objects.filter(pk=receipt.pk).values().get()
    if allowed:
        assert after["owner_id"] == (other_owner.pk if "owner" in fields else scope.owner.pk)
        assert after["warehouse_id"] == (
            other_warehouse.pk if "warehouse" in fields else scope.warehouse.pk
        )
        assert after["memo"] == "changed memo"
    else:
        assert after == before


@pytest.mark.parametrize("value", ["2026-02-30", "2026-02-29", "2026-13-01", "not-a-date"])
def test_invalid_reversal_date_is_atomic_400(scope, value):
    receipt = make_receipt(scope)
    post_receipt(receipt.pk)
    client, _ = client_for(scope)
    before = PaymentReceipt.objects.filter(pk=receipt.pk).values().get()
    response = client.post(
        f"/api/billing/payment-receipts/{receipt.pk}/reverse/",
        {"receipt_no": "REG-REV", "receipt_date": value},
        format="json",
    )
    assert response.status_code == 400
    assert PaymentReceipt.objects.filter(pk=receipt.pk).values().get() == before
    assert PaymentReceipt.objects.count() == PaymentAllocation.objects.count() == 1
    scope.bill.refresh_from_db()
    assert scope.bill.outstanding_amount == 0
    assert scope.bill.payment_status == BillPaymentStatus.PAID


@pytest.mark.parametrize("value", [None, "2024-02-29"])
def test_valid_reversal_dates_and_duplicate_rejection(scope, value):
    receipt = make_receipt(scope)
    post_receipt(receipt.pk)
    client, _ = client_for(scope)
    payload = {"receipt_no": "REG-REV"}
    if value:
        payload["receipt_date"] = value
    now = timezone.now()
    today = timezone.localtime(now).date() if timezone.is_aware(now) else now.date()
    url = f"/api/billing/payment-receipts/{receipt.pk}/reverse/"
    response = client.post(url, payload, format="json")
    assert response.status_code == 201, response.data
    assert response.data["receipt_date"] == (value or today.isoformat())
    assert client.post(url, {"receipt_no": "REG-REV-2"}, format="json").status_code == 409
    assert PaymentReceipt.objects.count() == PaymentAllocation.objects.count() == 2
    scope.bill.refresh_from_db()
    assert scope.bill.outstanding_amount == D("100")
    assert scope.bill.payment_status == BillPaymentStatus.UNPAID


@pytest.mark.parametrize("status", ["POSTED", "REVERSED"])
@pytest.mark.parametrize("operation", ["append", "append_reversal", "edit", "move_in", "move_out"])
def test_non_draft_allocations_reject_cached_state(scope, status, operation):
    receipt = make_receipt(scope)
    original = receipt.allocations.get()
    original.receipt = receipt  # Intentionally keep the DRAFT related-object cache.
    post_receipt(receipt.pk)
    if status == "REVERSED":
        reverse_receipt(receipt.pk, receipt_no="REG-REV")
    before = list(PaymentAllocation.objects.order_by("pk").values())
    with pytest.raises(ValidationError) as rejected:
        if operation in ("append", "append_reversal"):
            PaymentAllocation.objects.create(
                receipt=receipt,
                bill=scope.bill,
                amount=D("1"),
                **(
                    dict(is_reversal=True, reversal_of=original)
                    if operation == "append_reversal"
                    else {}
                ),
            )
        elif operation == "edit":
            original.amount = D("1")
            original.save()
        else:
            draft = make_receipt(scope, allocate=False, number="REG-DRAFT")
            if operation == "move_out":
                original.receipt = draft
                original.save()
            else:
                row = PaymentAllocation.objects.create(
                    receipt=draft, bill=scope.bill, amount=D("1")
                )
                before = list(PaymentAllocation.objects.order_by("pk").values())
                row.receipt = receipt
                row.save()
    assert "receipt" in rejected.value.message_dict
    assert list(PaymentAllocation.objects.order_by("pk").values()) == before
    scope.bill.refresh_from_db()
    assert scope.bill.outstanding_amount == (D("0") if status == "POSTED" else D("100"))


def test_admin_inline_allows_only_draft_additions(scope):
    _, user = client_for(scope, "superuser")
    request = RequestFactory().get("/admin/")
    request.user = user
    inline = PaymentAllocationInline(PaymentReceipt, admin.site)
    receipt = make_receipt(scope)
    assert inline.has_add_permission(request, receipt)
    for status in (PaymentReceiptStatus.POSTED, PaymentReceiptStatus.REVERSED):
        receipt.status = status
        assert not inline.has_add_permission(request, receipt)


@pytest.mark.parametrize("action", ["post", "reverse"])
def test_receipt_failures_roll_back_money_and_status(scope, action):
    receipt = make_receipt(scope)
    if action == "reverse":
        post_receipt(receipt.pk)
    before = list(PaymentReceipt.objects.values()), list(PaymentAllocation.objects.values())
    with patch(
        "allapp.billing.services.receivables._sync_bill_payment_status",
        side_effect=RuntimeError("injected failure"),
    ):
        with pytest.raises(RuntimeError):
            if action == "post":
                post_receipt(receipt.pk)
            else:
                reverse_receipt(receipt.pk, receipt_no="REG-REV")
    assert (
        list(PaymentReceipt.objects.values()),
        list(PaymentAllocation.objects.values()),
    ) == before
    scope.bill.refresh_from_db()
    assert scope.bill.outstanding_amount == (D("100") if action == "post" else D("0"))


@pytest.mark.django_db(transaction=True)
def test_append_waits_for_concurrent_post_and_rechecks_status(scope):
    receipt = make_receipt(scope)
    original = receipt.allocations.get()
    started, finished = threading.Event(), threading.Event()
    results = queue.Queue()

    def append():
        close_old_connections()
        try:
            started.set()
            PaymentAllocation.objects.create(
                receipt=receipt,
                bill=scope.bill,
                amount=D("1"),
                is_reversal=True,
                reversal_of=original,
            )
            results.put("saved")
        except Exception as exc:
            results.put(type(exc))
        finally:
            connections.close_all()
            finished.set()

    thread = threading.Thread(target=append)
    try:
        with transaction.atomic():
            PaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
            thread.start()
            assert started.wait(5)
            assert not finished.wait(0.3)
            post_receipt(receipt.pk)
    finally:
        thread.join(10)
    assert not thread.is_alive()
    assert results.get_nowait() is ValidationError
    assert receipt.allocations.count() == 1
    scope.bill.refresh_from_db()
    assert scope.bill.outstanding_amount == 0
    assert scope.bill.payment_status == BillPaymentStatus.PAID


@pytest.mark.django_db(transaction=True)
def test_post_waits_for_draft_allocation_edit(scope):
    receipt = make_receipt(scope)
    allocation = receipt.allocations.get()
    started, finished = threading.Event(), threading.Event()
    results = queue.Queue()

    def post():
        close_old_connections()
        try:
            started.set()
            post_receipt(receipt.pk)
            results.put("posted")
        except Exception as exc:
            results.put(type(exc))
        finally:
            connections.close_all()
            finished.set()

    thread = threading.Thread(target=post)
    try:
        with transaction.atomic():
            allocation.amount = D("40")
            allocation.save(update_fields=["amount"])
            thread.start()
            assert started.wait(5)
            assert not finished.wait(0.3)
    finally:
        thread.join(10)
    assert not thread.is_alive()
    assert results.get_nowait() == "posted"
    scope.bill.refresh_from_db()
    assert scope.bill.paid_amount == D("40")
    assert scope.bill.outstanding_amount == D("60")
    assert scope.bill.payment_status == BillPaymentStatus.PARTIAL

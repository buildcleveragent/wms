from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from allapp.baseinfo.models import Owner
from allapp.billing.models import BillingPeriod
from allapp.billing.services.integrity import ensure_close_readiness
from allapp.inventory.models import InventoryDailyClose
from allapp.locations.models import Warehouse


@pytest.mark.django_db
def test_pending_inventory_close_is_advisory_even_with_zero_accruals(client):
    day = date(2026, 10, 10)
    owner = Owner.objects.create(code="CLOSE-W", name="日结货主")
    warehouse = Warehouse.objects.create(code="CLOSE-W", name="日结仓")
    other = Warehouse.objects.create(code="CLOSE-X", name="其他仓")
    other_owner = Owner.objects.create(code="CLOSE-X", name="其他货主")
    period = BillingPeriod.objects.create(
        owner=owner,
        warehouse=warehouse,
        label="close-test",
        start_date=day,
        end_date=day,
    )
    user = get_user_model().objects.create_superuser(username="advisory-tester")
    client.force_login(user)
    api = APIClient()
    api.force_authenticate(user=user)
    for o, w, d in (
        (other_owner, warehouse, day),
        (owner, other, day),
        (owner, warehouse, day - timedelta(days=1)),
    ):
        InventoryDailyClose.objects.create(
            owner=o,
            warehouse=w,
            business_date=d,
            review_state="FINANCE",
        )
    close = InventoryDailyClose.objects.create(
        owner=owner,
        warehouse=warehouse,
        business_date=day,
        review_state="WAREHOUSE",
        issues=[{"issue": "onhand_difference", "message": "日终在库数量不一致"}],
    )
    scope = dict(owner_id=owner.pk, warehouse_id=warehouse.pk, start_date=day, end_date=day)
    readiness = ensure_close_readiness(**scope)
    assert readiness["ready"] and readiness["blocker_count"] == 0
    detail = reverse("console:inventory_daily_close_detail", args=[close.pk])
    warnings = readiness["inventory_close_warnings"]
    assert [item["detail_url"] for item in warnings] == [detail]
    assert warnings[0]["reasons"] == [
        {"code": "onhand_difference", "message": "日终在库数量不一致"}
    ]
    for endpoint, request in (("close-readiness", api.get), ("preview-lock", api.post)):
        response = request(reverse(f"billing-period-{endpoint}", args=[period.pk]))
        assert response.status_code == 200
        assert response.json()["inventory_close_warnings"] == warnings
        if endpoint == "preview-lock":
            assert response.json()["accrual_count"] == 0
    page = client.get(reverse("console:billing_overview"), {"period": period.pk})
    assert page.status_code == 200 and detail in page.content.decode()
    close.review_state = "CLOSED"
    close.save(update_fields=["review_state"])
    assert ensure_close_readiness(**scope)["inventory_close_warnings"] == []


@pytest.mark.django_db
def test_due_days_without_a_worker_are_advisory_and_do_not_create_records(monkeypatch):
    from allapp.baseinfo.models import OwnerWarehouseBinding
    from allapp.inventory.daily_close_advisory import inventory_close_warnings
    from allapp.inventory.models import InventoryClosePilot, InventoryCloseScope

    first = date(2026, 1, 7)
    monkeypatch.setattr(
        "allapp.inventory.daily_close_advisory.latest_due_date", lambda: first + timedelta(days=3)
    )
    owner = Owner.objects.create(code="GAP", name="日结货主")
    wh = Warehouse.objects.create(code="GAP", name="日结仓")
    OwnerWarehouseBinding.objects.create(owner=owner, warehouse=wh)
    pilot = InventoryClosePilot.objects.create(warehouse=wh, start_date=first, enabled=True)
    InventoryDailyClose.objects.create(
        owner=owner, warehouse=wh, business_date=first + timedelta(days=2)
    )
    rows = inventory_close_warnings(
        owner.pk, wh.pk, first - timedelta(days=7), first + timedelta(days=7)
    )
    assert [(r["business_date"], r["through_date"]) for r in rows] == [
        ("2026-01-07", "2026-01-08"),
        ("2026-01-10", None),
    ]
    assert not InventoryCloseScope.objects.exists()
    assert InventoryDailyClose.objects.count() == 1
    pilot.enabled = False
    pilot.save()
    assert not inventory_close_warnings(owner.pk, wh.pk, first, first + timedelta(days=3))

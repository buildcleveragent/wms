"""Read-only assessments of transactions produced by guarded synthetic fixtures."""

import datetime
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.models import Sum
from django.utils import timezone

from allapp import test_business_flows as flows
from allapp.billing.enums import ChargeType
from allapp.billing.models import Bill, BillingAccrual, BillingEvent, BillingMetricDaily
from allapp.core import tests as core_fixtures
from allapp.inbound import test_customer_returns as return_fixtures
from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.locations.models import Warehouse
from allapp.pos import test_cross_flow_acceptance as cross_flows
from allapp.pos import test_sale_zone_scope as pos_zones
from allapp.pos import tests as pos_fixtures
from allapp.pos.models import PosSale
from allapp.tasking import counting


def save_observations(path, evidence):
    assert connection.settings_dict["NAME"] == "wms_db_test"
    Path(__file__).with_name(path).write_text(json.dumps(evidence, indent=2, default=str) + "\n")


def run_accuracy(*options):
    out = StringIO()
    call_command("reconcile_data_accuracy", "--json", *options, stdout=out)
    return json.loads(out.getvalue())


@pytest.mark.django_db
def test_operational_ledger_reconciles(settings):
    assert connection.settings_dict["NAME"] == "wms_db_test"
    settings.TASKING_BARCODE_RESOLVER = "allapp.test_business_flows.business_flow_barcode_resolver"
    case = flows.BusinessFlowTests()
    case.setUp()
    case.test_flow_12_operational_inventory_to_billing_invoice_chain()
    scope = {"owner": case.owner, "warehouse": case.warehouse}
    report = run_accuracy("--owner", str(case.owner.pk))
    by_type = list(InventoryTransaction.objects.filter(**scope).order_by("id").values_list("tx_type", "qty_delta"))
    details = InventoryDetail.objects.filter(**scope)
    onhand = details.aggregate(q=Sum("onhand_qty"))["q"]
    summary = InventorySummary.objects.filter(owner=case.owner).aggregate(q=Sum("onhand_qty"))["q"]
    invoice = Bill.objects.get(**scope)
    result = {
        "fixture": "5 units formally received then put away; zero charges for receive/putaway; storage price CNY 2.00 per CBM",
        "posted_transactions": by_type,
        "detail_locations": list(details.order_by("location__code").values_list("location__code", "onhand_qty", "allocated_qty")),
        "owner_total": summary,
        "metric_CBM": BillingMetricDaily.objects.get(**scope, metric_type="CBM").value,
        "accrual": list(BillingAccrual.objects.filter(**scope).values_list("amount", "status")),
        "invoice_total": invoice.total,
        "invoice_line_count": invoice.lines.count(),
        "inventory_checks": report["inventory"]["checks"],
        "billing_checks": report["billing"]["checks"],
        "issue_count": report["issue_count"],
    }
    save_observations("synthetic-ledger.json", result)
    assert by_type and sum(row[1] for row in by_type) == onhand == summary == Decimal(5)
    assert invoice.total == Decimal("2.50")
    assert report["ok"], report


@pytest.mark.django_db
def test_one_period_excludes_other_warehouse_pricing_events():
    assert connection.settings_dict["NAME"] == "wms_db_test"
    case = core_fixtures.DataAccuracyCommandTests()
    case.setUp()
    case.create_consistent_billing()
    other_warehouse = Warehouse.objects.create(code="SCOPE-WH2", name="Synthetic second warehouse")
    BillingEvent.objects.create(
        owner=case.owner,
        warehouse=other_warehouse,
        charge_type=ChargeType.RECEIVE,
        service_date=datetime.date(2026, 4, 2),
        quantity=Decimal(1),
        event_fp="assessment-synthetic-unpriced-second-warehouse",
    )
    args = ["--billing-only", "--owner", str(case.owner.id), "--period", str(case.period.id)]
    documented = run_accuracy(*args)
    control = run_accuracy(*args, "--warehouse", str(case.warehouse.id))
    evidence = {
        "documented_filter": "--owner owner_A --billing-only --period period_A",
        "period_warehouse": case.period.warehouse_id,
        "other_warehouse_with_pending_event": other_warehouse.id,
        "documented_issue_count": documented["issue_count"],
        "documented_failed_checks": [row for row in documented["billing"]["checks"] if not row["ok"]],
        "control_with_explicit_period_warehouse_issue_count": control["issue_count"],
    }
    save_observations("period-scope.json", evidence)
    assert control["ok"]
    assert documented["ok"], evidence


class ReturnTraceabilityProbe(return_fixtures.CustomerReturnTests):
    def test_expiry_controlled_customer_return_requires_traceability(self):
        self.assertEqual(connection.settings_dict["NAME"], "wms_db_test")
        self.product.expiry_control = True
        self.product.expiry_basis = "MFG"
        self.product.shelf_life_days = 100
        self.product.expiry_warning_days = 5
        self.product.save()
        order = self.ready("1")
        response = self.receipt(order, "1")
        details = list(
            InventoryDetail.objects.filter(product=self.product, batch_no="CR-BATCH")
            .values("available_qty", "production_date", "expiry_date")
        )
        report = run_accuracy("--owner", str(self.owner.pk), "--inventory-only")
        expiry_check = next(c for c in report["inventory"]["checks"] if c["name"] == "inventory_expiry_tracking_integrity")
        result = {
            "expiry_policy": "MFG",
            "shelf_life_days": 100,
            "return_response_status": response.status_code,
            "return_workflow_state": response.data.get("status"),
            "inventory_detail": details,
            "expiry_audit": expiry_check,
        }
        save_observations("customer-return-expiry.json", result)
        self.assertFalse(any(row["available_qty"] > 0 for row in details), result)

    def test_customer_return_waits_while_count_is_frozen(self):
        self.assertEqual(connection.settings_dict["NAME"], "wms_db_test")
        self.grant(self.wh, "taskconfirm_as_wh_manager")
        order = self.ready("1")
        detail = InventoryDetail.objects.create(
            product=self.product, owner=self.owner, warehouse=self.warehouse, location=self.location,
            batch_no="CR-BATCH", onhand_qty=Decimal(5)
        )
        task, _, _ = counting.create_count_task(
            created_by=self.wh, owner_id=self.owner.pk, warehouse_id=self.warehouse.pk,
            scope="LOC", location_id=self.location.pk
        )
        task = counting.release_count_task(task.pk, by_user=self.wh)
        self.assertTrue(task.count_scope_locks.filter(released_at__isnull=True).exists())
        self.assertEqual(task.lines.get().countlineextra.qty_book, Decimal(5))
        response = self.receipt(order, "1")
        detail.refresh_from_db()
        active_after = task.count_scope_locks.filter(released_at__isnull=True).exists()
        result = {
            "frozen_count_status": task.status,
            "lock_active_before_and_after_receipt": active_after,
            "count_book": task.lines.get().countlineextra.qty_book,
            "onhand_after": detail.onhand_qty,
            "return_http_status": response.status_code,
            "return_status": response.data.get("status"),
            "posted_return_transactions": InventoryTransaction.objects.filter(memo="销售退货入库").count(),
        }
        save_observations("customer-return-count-freeze.json", result)
        self.assertTrue(active_after, result)
        self.assertEqual(detail.onhand_qty, Decimal(5), result)


@pytest.mark.django_db
def test_expired_stock_is_excluded_from_regular_cash_checkout():
    assert connection.settings_dict["NAME"] == "wms_db_test"
    case = pos_zones.PosSaleZoneScopeTests()
    case.setUp()
    case.product.expiry_control = True
    case.product.expiry_basis = "MFG"
    case.product.shelf_life_days = 100
    case.product.expiry_warning_days = 5
    case.product.save()
    day = timezone.now().date()
    for detail, delta_days in ((case.pick, -1), (case.storage, 50)):
        detail.expiry_date = day + datetime.timedelta(days=delta_days)
        detail.production_date = detail.expiry_date - datetime.timedelta(days=100)
        detail.save()
    response = case._checkout(1)
    ledger = list(InventoryTransaction.objects.filter(product=case.product, tx_type="ISSUE").values(
        "batch_no", "expiry_date", "qty_delta"
    ))
    result = {
        "sale_day": day,
        "configured_expiry_basis": case.product.expiry_basis,
        "expired_units_before": "4",
        "valid_units_before": "6",
        "checkout_status": response.status_code,
        "issued": ledger,
        "balances_after": dict(InventoryDetail.objects.filter(product=case.product).values_list("batch_no", "onhand_qty")),
    }
    save_observations("pos-expired-sale.json", result)
    assert response.status_code == 201 and len(ledger) == 1, result
    assert ledger[0]["expiry_date"] >= day, result


@pytest.mark.django_db
def test_cash_checkout_avoids_a_frozen_location():
    assert connection.settings_dict["NAME"] == "wms_db_test"
    case = pos_zones.PosSaleZoneScopeTests()
    case.setUp()
    location = case.pick.location
    location.is_frozen = True
    location.save(update_fields=["is_frozen"])
    response = case._checkout(1)
    issues = list(InventoryTransaction.objects.filter(product=case.product, tx_type="ISSUE").values(
        "location_id", "batch_no", "qty_delta"
    ))
    result = {
        "frozen_location_id": location.id,
        "frozen_units_before": "4",
        "other_valid_units_before": "6",
        "checkout_status": response.status_code,
        "issued": issues,
    }
    save_observations("pos-frozen-location.json", result)
    assert response.status_code == 201 and len(issues) == 1, result
    assert issues[0]["location_id"] != location.id, result


@pytest.mark.django_db
def test_standard_outbound_reserves_valid_units_before_expired_stock():
    assert connection.settings_dict["NAME"] == "wms_db_test"
    case = cross_flows.ReviewInventorySummaryTests()
    case.setUp()
    case.product.expiry_control = True
    case.product.expiry_basis = "MFG"
    case.product.shelf_life_days = 100
    case.product.expiry_warning_days = 5
    case.product.save()
    day = timezone.now().date()
    earlier, _, order = case._stock_and_order()
    earlier.expiry_date = day - datetime.timedelta(days=1)
    earlier.production_date = earlier.expiry_date - datetime.timedelta(days=100)
    earlier.save()
    later = InventoryDetail.objects.create(owner=case.owner, warehouse=case.warehouse, product=case.product,
        location=case.location, onhand_qty=10, production_date=day - datetime.timedelta(days=50),
        expiry_date=day + datetime.timedelta(days=50), base_unit=case.base_uom.code)
    order.owner_approve(by_user=case.user, allow_backorder=False)
    earlier.refresh_from_db()
    later.refresh_from_db()
    result = {
        "sale_day": day,
        "order_qty": "3",
        "expired_date": earlier.expiry_date,
        "valid_date": later.expiry_date,
        "expired_allocated": earlier.allocated_qty,
        "valid_allocated": later.allocated_qty,
    }
    save_observations("standard-expired-allocation.json", result)
    assert later.allocated_qty == Decimal(3) and earlier.allocated_qty == 0, result


@pytest.mark.django_db
def test_unpriced_catalog_item_is_not_silently_sold_as_a_gift():
    assert connection.settings_dict["NAME"] == "wms_db_test"
    case = pos_fixtures.PosApiTests()
    case.setUp()
    case.product.price = None
    case.product.min_price = None
    case.product.max_discount = None
    case.product.save()
    listing = case.client.get("/api/pos/products/", {"barcode": case.product.unit_barcode})
    assert listing.status_code == 200 and listing.data["count"] == 1, listing.data
    item = listing.data["results"][0]
    price = item["price"]
    before = InventoryDetail.objects.get(product=case.product).available_qty
    if price is None:
        return
    payload = {"payment": case.payment(str(price)), "items": [
        {"product_id": item["id"], "qty": "1.000", "price": str(price), "amount": str(price)}
    ]}
    response = case.client.post("/api/pos/checkout/", payload, format="json")
    after = InventoryDetail.objects.get(product=case.product).available_qty
    result = {
        "configured_product_price": case.product.price,
        "catalog_unit_price": price,
        "checkout_status": response.status_code,
        "sale_amounts": list(PosSale.objects.all().values_list("total_amount", flat=True)),
        "available_before": before,
        "available_after": after,
        "gift_override_submitted": False,
    }
    save_observations("pos-missing-price.json", result)
    assert after == before, result

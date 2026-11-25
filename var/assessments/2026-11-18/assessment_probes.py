"""Targeted assessment scenarios. Run explicitly with the repository's guarded pytest."""

import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from allapp.billing import tests as billing_fixtures
from allapp.billing.models import BillingMetricDaily
from allapp.core.choices import ZoneType
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location
from allapp.pos import tests as pos_fixtures
from allapp.pos.models import PosRefund, PosSale


@pytest.mark.django_db
def test_full_scheduler_after_single_metric_matches_forced_control():
    fixture = billing_fixtures.BillingMetricGenerationTests()
    fixture.setUp()
    day = datetime.date(2026, 4, 8)
    fixture._create_inventory(location=fixture.location1, qty="4.0000")
    fixture._create_inventory_transaction(day, qty_delta="4.0000", location=fixture.location1)
    fixture._create_outbound_order(day, qty="2.000", price="15.0000")
    rows = BillingMetricDaily.objects.filter(
        owner=fixture.owner, warehouse=fixture.warehouse, service_date=day
    )

    def run(*options):
        output = StringIO()
        call_command(
            "billing_run_scheduler",
            "--once",
            "--date",
            day.isoformat(),
            "--owner",
            str(fixture.owner.pk),
            "--warehouse",
            str(fixture.warehouse.pk),
            *options,
            stdout=output,
        )
        values = dict(rows.order_by("metric_type").values_list("metric_type", "value"))
        print(options or ("full",), output.getvalue().strip(), values)
        return values

    partial = run("--metric-type", "ORDER_AMT")
    ordinary_full = run()
    forced_full = run("--force")
    assert set(partial) == {"ORDER_AMT"}
    assert {"ORDER_AMT", "CBM", "PALLET"} <= forced_full.keys()
    assert ordinary_full == forced_full, "Full-day metrics still missing after a normal full run"


@pytest.mark.django_db
def test_same_sku_has_explainable_picking_and_whole_warehouse_quantities():
    fixture = pos_fixtures.PosApiTests()
    fixture.setUp()
    storage = InventoryDetail.objects.get(owner=fixture.owner, product=fixture.product)
    assert storage.zone_type == ZoneType.STORAGE
    storage.onhand_qty = Decimal("6")
    storage.save()
    location = Location.objects.create(
        warehouse=fixture.warehouse, code="SWPOS-02-01-01", zone_type=ZoneType.PICK
    )
    InventoryDetail.objects.create(
        owner=fixture.owner,
        product=fixture.product,
        warehouse=fixture.warehouse,
        location=location,
        zone_type=ZoneType.PICK,
        onhand_qty=Decimal("4"),
    )
    for params, expected in (({"zone_type": ZoneType.PICK}, 4), ({}, 10)):
        result = fixture.client.get(
            "/api/pos/products/", {"barcode": fixture.product.unit_barcode, **params}
        )
        assert result.status_code == 200
        assert result.data["count"] == 1
        row = result.data["results"][0]
        assert row["id"] == fixture.product.pk
        assert Decimal(str(row["available_qty"])) == expected
        print("picking" if params else "warehouse", row["available_qty"])


@pytest.mark.django_db
def test_pda_partial_return_retry_records_only_one_cash_refund():
    fixture = pos_fixtures.PosApiTests()
    fixture.setUp()
    checkout = fixture.client.post(
        "/api/pos/checkout/",
        {
            "src_bill_no": "ASSESS-RETURN-RETRY",
            "payment": fixture.payment("18.00"),
            "items": [{"product_id": fixture.product.pk, "qty": "2.000", "price": "9.0000"}],
        },
        format="json",
    )
    assert checkout.status_code == 201
    sale = PosSale.objects.get(pk=checkout.data["sale"]["id"])
    payload = {
        "sale_id": sale.pk,
        "reason": "one item returned",
        "lines": [{"sale_line_id": sale.lines.get().pk, "qty": "1.000"}],
        "refunds": [{"method": "CASH", "amount": "9.00", "reference_no": ""}],
    }
    # The current PDA derives its request key from each confirmation time.
    for timestamp in (100, 101):
        result = fixture.client.post(
            "/api/pos/returns/",
            {**payload, "idempotency_key": f"{sale.pk}-{timestamp}"},
            format="json",
        )
        assert result.status_code == 201
    onhand = InventoryDetail.objects.get(owner=fixture.owner, product=fixture.product).onhand_qty
    refunds = list(
        PosRefund.objects.filter(return_order__sale=sale).values_list("amount", flat=True)
    )
    observed = (sale.returns.count(), sum(refunds), onhand)
    print("return records, recorded cash refunds, on-hand", observed)
    assert observed == (1, Decimal("9.00"), Decimal("9.0000"))

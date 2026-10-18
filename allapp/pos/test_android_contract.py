"""Mobile catalog/after-sales regressions against the real MySQL test schema."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone

from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.pos.models import PosRefund, PosReturn, PosSale
from allapp.products.identifier_services import add_product_barcode
from allapp.products.models import Product


@pytest.fixture
def pos(db):
    from allapp.pos.tests import PosApiTests

    fixture = PosApiTests("test_checkout_splits_products_by_owner")
    fixture.setUp()
    for owner in (fixture.owner, fixture.other_owner):
        OwnerWarehouseBinding.objects.get_or_create(owner=owner, warehouse=fixture.warehouse)
    return fixture


def test_mobile_catalog_includes_all_authorized_owners_and_zero_layers(pos):
    empty_owner = Owner.objects.create(code="MOB-EMPTY", name="缺货货主")
    forbidden_owner = Owner.objects.create(code="MOB-DENY", name="未授权货主")
    OwnerWarehouseBinding.objects.create(owner=empty_owner, warehouse=pos.warehouse)
    products = [pos.product, pos.other_product]
    for owner in (empty_owner, forbidden_owner):
        products.append(
            Product.objects.create(
                owner=owner,
                code=f"MOB-{owner.pk}",
                name=owner.name,
                base_uom=pos.uom,
                price=10,
            )
        )
    for product in products:
        add_product_barcode(product=product, barcode="MOB-SHARED", barcode_type="UNIT")
    stock = InventoryDetail.objects.get(product=pos.other_product)
    stock.onhand_qty = 0
    stock.save()
    response = pos.client.get(
        "/api/pos/products/",
        {
            "barcode": "MOB-SHARED",
            "include_out_of_stock": "1",
            "page_size": 100,
        },
    )
    assert response.status_code == 200
    rows = response.data["results"]
    assert {row["owner_id"] for row in rows} == {pos.owner.pk, pos.other_owner.pk, empty_owner.pk}
    assert all(row["owner_name"] and row["matched_barcode"]["label"] for row in rows)
    assert sum(Decimal(row["available_qty"]) == 0 for row in rows) == 2
    # The PC catalog remains stock-layer based when the opt-in is absent.
    legacy = pos.client.get("/api/pos/products/", {"barcode": "MOB-SHARED"})
    assert legacy.data["count"] == 2
    assert PosSale.objects.count() == 0
    assert InventoryTransaction.objects.count() == 0


def test_mobile_catalog_has_complete_stable_pagination(pos):
    for index in range(101):
        owner = Owner.objects.create(code=f"MOB-P-{index}", name=f"分页货主{index}")
        OwnerWarehouseBinding.objects.create(owner=owner, warehouse=pos.warehouse)
        product = Product.objects.create(
            owner=owner, code="MOB-PAGE", name="分页商品", base_uom=pos.uom, price=1
        )
        add_product_barcode(product=product, barcode="MOB-PAGE-ALIAS", barcode_type="UNIT")
    params = {"barcode": "MOB-PAGE-ALIAS", "include_out_of_stock": "1", "page_size": 100}
    first = pos.client.get("/api/pos/products/", params)
    second = pos.client.get("/api/pos/products/", {**params, "page": 2})
    assert first.status_code == second.status_code == 200
    assert first.data["count"] == 101 and first.data["next"]
    assert second.data["next"] is None
    rows = first.data["results"] + second.data["results"]
    assert len({row["id"] for row in rows}) == 101


def test_effective_package_alias_carries_authoritative_conversion(pos):
    package = pos.product.packages.first()
    alias = add_product_barcode(
        product=pos.product, barcode="MOB-OLD-BOX", barcode_type="PACKAGE", package=package
    )
    package.qty_in_base = 24
    package.save(update_fields=["qty_in_base"])
    response = pos.client.get(
        "/api/pos/products/", {"barcode": alias.barcode, "include_out_of_stock": 1}
    )
    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["owner_name"] == pos.owner.name
    assert row["matched_barcode"] == {
        "package_id": package.pk,
        "label": "箱",
        "multiplier": Decimal("12"),
        "code_type": "PACKAGE",
    }
    assert any(Decimal(unit["multiplier"]) == 24 for unit in row["unit_options"])


@pytest.mark.parametrize("state", ["inactive", "future", "expired"])
def test_unusable_aliases_do_not_become_sale_candidates(pos, state):
    options = {"is_active": state != "inactive"}
    if state == "future":
        options["valid_from"] = timezone.now() + timedelta(days=1)
    elif state == "expired":
        options["valid_to"] = timezone.now() - timedelta(days=1)
    add_product_barcode(product=pos.product, barcode="MOB-INVALID", barcode_type="UNIT", **options)
    response = pos.client.get(
        "/api/pos/products/", {"barcode": "MOB-INVALID", "include_out_of_stock": 1}
    )
    assert response.status_code == 200
    assert response.data["count"] == 0


def test_stock_is_aggregated_across_lines_and_failure_is_atomic(pos):
    stock = InventoryDetail.objects.get(product=pos.product)
    stock.onhand_qty = 12
    stock.save()
    response = pos.client.post(
        "/api/pos/checkout/",
        {
            "idempotency_key": str(uuid4()),
            "items": [
                {"product_id": pos.product.pk, "qty": qty, "price": "10"}
                for qty in ("12.000", "1.000")
            ],
            "payment": {"method": "CASH", "amount_received": "130.00"},
        },
        format="json",
    )
    assert response.status_code == 400
    stock.refresh_from_db()
    assert stock.onhand_qty == 12
    assert PosSale.objects.count() == InventoryTransaction.objects.count() == 0


def test_return_replay_restores_stock_and_refunds_exactly_once(pos):
    sale_response = pos.client.post(
        "/api/pos/checkout/",
        {
            "idempotency_key": str(uuid4()),
            "items": [{"product_id": pos.product.pk, "qty": "3.000", "price": "10"}],
            "payment": {"method": "CASH", "amount_received": "30.00"},
        },
        format="json",
    )
    assert sale_response.status_code == 201, sale_response.data
    sale = sale_response.data["sale"]
    payload = {
        "sale_id": sale["id"],
        "reason": "一次退货响应丢失",
        "idempotency_key": str(uuid4()),
        "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": "1.000"}],
        "refunds": [{"method": "CASH", "amount": "10.00"}],
    }
    first = pos.client.post("/api/pos/returns/", payload, format="json")
    assert first.status_code == 201, first.data
    before = list(InventoryTransaction.objects.values_list("id", "qty_delta"))
    second = pos.client.post("/api/pos/returns/", payload, format="json")
    assert second.status_code == 201, second.data
    assert second.data["return"]["id"] == first.data["return"]["id"]
    assert PosReturn.objects.count() == PosRefund.objects.count() == 1
    assert InventoryDetail.objects.get(product=pos.product).onhand_qty == 8
    assert list(InventoryTransaction.objects.values_list("id", "qty_delta")) == before
    detail = pos.client.get(f"/api/pos/sales/{sale['id']}/").data
    assert detail["warehouse_name"] == pos.warehouse.name
    assert detail["cashier_name"] == pos.user.username
    assert detail["lines"][0]["owner_name"] == pos.owner.name
    assert detail["lines"][0]["base_unit"] == "件"
    assert detail["lines"][0]["product_spec"] == pos.product.spec


def test_zero_price_return_uses_no_refund_rows(pos):
    pos.product.price = pos.product.min_price = Decimal("0")
    pos.product.save(update_fields=["price", "min_price"])
    response = pos.client.post(
        "/api/pos/checkout/",
        {
            "idempotency_key": str(uuid4()),
            "items": [{"product_id": pos.product.pk, "qty": "1.000", "price": "0"}],
            "payment": {"method": "CASH", "amount_received": "0"},
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    sale = response.data["sale"]
    payload = {
        "sale_id": sale["id"],
        "reason": "赠品退回",
        "idempotency_key": str(uuid4()),
        "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": "1.000"}],
        "refunds": [],
    }
    for _ in range(2):
        response = pos.client.post("/api/pos/returns/", payload, format="json")
        assert response.status_code == 201, response.data
    assert PosReturn.objects.count() == 1
    assert PosRefund.objects.count() == 0
    assert InventoryDetail.objects.get(product=pos.product).onhand_qty == 10

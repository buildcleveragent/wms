"""Opt-in regression with the built PDA, a real Django server and isolated MySQL."""

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from allapp.core.choices import InvTxType
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.pos import tests as pos_fixtures
from allapp.pos.models import PosRefund, PosReturn, PosReturnLine, PosSaleLine
from wmsmaster.test_database import validate_test_process, verify_test_database

ROOT = Path(__file__).resolve().parents[1]


def assert_return_ledger(sale_id, stocks_by_line, refund_amount, expected_ids):
    orders = PosReturn.objects.filter(sale_id=sale_id).order_by("pk")
    assert list(orders.values_list("pk", "total_amount")) == [
        (order_id, refund_amount) for order_id in expected_ids
    ]
    refunds = PosRefund.objects.filter(return_order__sale_id=sale_id).order_by("pk")
    assert list(refunds.values_list("return_order_id", "method", "amount")) == [
        (order_id, "CASH", refund_amount) for order_id in expected_ids if refund_amount > 0
    ]
    lines = PosReturnLine.objects.filter(return_order__sale_id=sale_id).order_by(
        "return_order_id", "sale_line_id"
    )
    assert list(lines.values_list("return_order_id", "sale_line_id", "qty")) == [
        (order_id, line_id, 1) for order_id in expected_ids for line_id in sorted(stocks_by_line)
    ]
    for line_id, (stock, after_sale) in stocks_by_line.items():
        entries = InventoryTransaction.objects.filter(
            src_model="PosReturnLine", warehouse_id=stock.warehouse_id, product_id=stock.product_id
        ).order_by("pk")
        assert list(entries.values_list("src_id", "tx_type", "src_line_id", "qty_delta")) == [
            (pk, InvTxType.RECEIVE, stock.pk, 1)
            for pk in lines.filter(sale_line_id=line_id).values_list("pk", flat=True)
        ]
        stock.refresh_from_db()
        assert stock.onhand_qty == stock.available_qty == after_sale + len(expected_ids)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("basket", ["paid", "gift", "mixed"])
def test_pda_recovers_committed_partial_return_after_restart(live_server, settings, basket):
    validate_test_process(settings.DATABASES["default"])
    identity = verify_test_database(settings.DATABASES["default"])
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    artifacts = Path(os.environ["WMS_BROWSER_ARTIFACTS"]).resolve() / "pos-return" / basket
    assert not artifacts.is_relative_to(ROOT)
    artifacts.mkdir(parents=True, exist_ok=True)

    fixture = pos_fixtures.PosApiTests()
    fixture.setUp()
    items = []
    after_sale = {}
    if basket in ("paid", "mixed"):
        items.append({"product_id": fixture.product.pk, "qty": "2.000", "price": "9.0000"})
        after_sale[fixture.product.pk] = 8  # fixture starts with 10 pieces
    if basket in ("gift", "mixed"):
        items.append({"product_id": fixture.other_product.pk, "qty": "2.000", "price": "0.0000"})
        after_sale[fixture.other_product.pk] = 6  # fixture starts with 8 pieces
    refund_amount = Decimal("0.00") if basket == "gift" else Decimal("9.00")
    checkout = fixture.client.post(
        "/api/pos/checkout/",
        {
            "src_bill_no": "PDA-PARTIAL-RETURN-E2E",
            "payment": fixture.payment(refund_amount * 2),
            "items": items,
        },
        format="json",
    )
    assert checkout.status_code == 201, checkout.data
    sale_id = checkout.data["sale"]["id"]
    stocks_by_line = {
        line.pk: (
            InventoryDetail.objects.get(product_id=line.product_id, warehouse=fixture.warehouse),
            after_sale[line.product_id],
        )
        for line in PosSaleLine.objects.filter(sale_id=sale_id)
    }
    assert len(stocks_by_line) == len(items)
    assert_return_ledger(sale_id, stocks_by_line, refund_amount, [])

    with TemporaryDirectory(prefix="pda-return-browser-") as profile:
        config = {
            **identity,
            "backend": live_server.url,
            "artifacts": str(artifacts),
            "profile": profile,
            "username": fixture.user.username,
            "sale_id": sale_id,
            "refund_amount": str(refund_amount),
            "line_count": len(items),
        }
        attempts = []
        expected_ids = []
        phases = ["lose-receipt", "retry"]
        if basket == "paid":
            phases.append("new-return")
        for phase in phases:
            result = subprocess.run(
                ["node", str(ROOT / "scripts/pos-return-browser-acceptance.mjs")],
                input=json.dumps({**config, "phase": phase}),
                text=True,
                capture_output=True,
                timeout=120,
            )
            assert result.returncode == 0, result.stderr
            attempt = json.loads(result.stdout)
            attempts.append(attempt)
            if phase == "retry":
                assert attempt == attempts[0]
            else:
                assert attempt["id"] not in expected_ids
                expected_ids.append(attempt["id"])
            assert_return_ledger(sale_id, stocks_by_line, refund_amount, expected_ids)

        original = attempts[0]["payload"]
        assert original["idempotency_key"]
        assert original["refunds"] == (
            [{"method": "CASH", "amount": str(refund_amount), "reference_no": ""}]
            if refund_amount
            else []
        )
        if basket == "paid":
            second_item = attempts[-1]["payload"]
            assert second_item["idempotency_key"] != original["idempotency_key"]
            assert {**original, "idempotency_key": second_item["idempotency_key"]} == second_item

import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from allapp.baseinfo.models import Owner, Supplier
from allapp.billing.models import BillingPeriod
from allapp.inbound.models import InboundOrder
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from scripts.wms_capacity_dataset import capture, count_dataset
from scripts.wms_capacity_reconciliation import readonly_snapshot


def snapshot_target():
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), @@server_uuid")
        name, server_uuid = cursor.fetchone()
    assert name == "wms_db_test"
    return name, server_uuid


@pytest.mark.django_db(transaction=True)
def test_baseline_counts_business_rows_and_distinct_billing_months(tmp_path):
    database, server_uuid = snapshot_target()
    before = count_dataset()
    warehouse = Warehouse.objects.create(name="Dataset warehouse", code="CBWH")
    owner = Owner.objects.create(name="Dataset owner", code="CBOWN")
    other = Owner.objects.create(name="Dataset second owner", code="CBOWN2")
    Owner.objects.create(name="Disabled dataset owner", code="CBOFF", is_active=False)
    supplier = Supplier.objects.create(owner=owner, name="Dataset supplier", code="CBDATA")
    InboundOrder.objects.bulk_create(
        [
            InboundOrder(owner=owner, warehouse=warehouse, supplier=supplier, order_no="CBIN"),
            InboundOrder(
                owner=owner,
                warehouse=warehouse,
                supplier=supplier,
                order_no="CBOLD",
                is_deleted=True,
            ),
        ]
    )
    OutboundOrder.objects.create(
        owner=owner,
        warehouse=warehouse,
        supplier=supplier,
        outbound_type="SUPPLIER_RETURN",
        order_no="CBOUT",
    )
    for tenant, month in ((owner, 1), (other, 1), (other, 2)):
        BillingPeriod.objects.create(
            owner=tenant,
            warehouse=warehouse,
            label=f"capacity-{month}",
            start_date=date(2094, month, 2),
            end_date=date(2094, month, 25),
        )
    args = SimpleNamespace(
        output=tmp_path / "baseline.json",
        snapshot_id="fixture",
        deployment_identity="test-candidate",
        snapshot_db=database,
        snapshot_server_uuid=server_uuid,
    )
    with pytest.raises(ValueError, match="below acceptance minimum"):
        capture(args)
    result = json.loads(args.output.read_text())
    assert result["database"] == database and result["server_uuid"] == server_uuid
    assert result["order_counts"] == {k: n + 1 for k, n in before["order_counts"].items()}
    expected_months = sorted(set(before["accounting_month_starts"]) | {"2094-01-01", "2094-02-01"})
    assert result["accounting_month_starts"] == expected_months
    assert result["dataset"] == {
        **before["dataset"],
        "owners": before["dataset"]["owners"] + 2,
        "warehouses": before["dataset"]["warehouses"] + 1,
        "orders": before["dataset"]["orders"] + 2,
        "accounting_months": len(expected_months),
    }


@pytest.mark.django_db(transaction=True)
def test_snapshot_requires_matching_instance_and_rejects_writes():
    database, server_uuid = snapshot_target()
    with pytest.raises(ValueError, match="server UUID mismatch"):
        with readonly_snapshot(database, str(uuid4()), "fixture"):
            pytest.fail("unexpected snapshot accepted")
    with pytest.raises(DatabaseError) as error:
        with readonly_snapshot(database, server_uuid, "fixture"):
            Owner.objects.filter(pk=0).update(name="read-only check")
    assert error.value.args[0] == 1792  # MySQL ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION.

"""Measure the synthetic WMS baseline on a read-only restored MySQL snapshot."""

import argparse
from pathlib import Path

from .wms_capacity_reconciliation import readonly_snapshot


def count_dataset():
    from allapp.baseinfo.models import Owner
    from allapp.billing.models import BillingPeriod
    from allapp.inbound.models import InboundOrder
    from allapp.inventory.models import InventoryDetail, InventoryTransaction
    from allapp.locations.models import Warehouse
    from allapp.outbound.models import OutboundOrder
    from allapp.products.models import Product

    order_counts = {
        "inbound": InboundOrder.objects.count(),
        "outbound": OutboundOrder.objects.count(),
    }
    months = [month.isoformat() for month in BillingPeriod.objects.dates("start_date", "month")]
    dataset = {
        "warehouses": Warehouse.objects.filter(is_active=True).count(),
        "owners": Owner.objects.filter(is_active=True).count(),
        "products": Product.objects.filter(is_active=True).count(),
        "inventory_details": InventoryDetail.objects.filter(is_active=True).count(),
        "inventory_transactions": InventoryTransaction.objects.count(),
        "orders": sum(order_counts.values()),
        "accounting_months": len(months),
    }
    return {"dataset": dataset, "order_counts": order_counts, "accounting_month_starts": months}


def capture(args):
    from .wms_capacity import PROFILE, load, save, utc

    if not args.deployment_identity.strip():
        raise ValueError("candidate deployment identity required")
    with readonly_snapshot(
        args.snapshot_db, args.snapshot_server_uuid, args.snapshot_id
    ) as identity:
        counts = count_dataset()
        result = {
            "source": "wms_capacity_dataset_v1",
            "captured_at": utc(),
            "deployment_identity": args.deployment_identity,
            **identity,
            **counts,
        }
    save(args.output, result)
    below = [
        key for key, minimum in load(PROFILE)["dataset"].items() if counts["dataset"][key] < minimum
    ]
    if below:
        raise ValueError("dataset below acceptance minimum: " + ", ".join(below))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("snapshot-db", "snapshot-server-uuid", "snapshot-id", "deployment-identity"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        capture(args)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    main()

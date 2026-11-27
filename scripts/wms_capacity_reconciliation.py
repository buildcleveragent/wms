"""Capture WMS reconciliation on a restored, isolated capacity snapshot. Run as a module."""

import argparse
import os
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

CHECKS = {
    "inventory": {
        "task_line_product_owner_consistency",
        "inventory_detail_product_owner_consistency",
        "inventory_summary_product_owner_consistency",
        "inventory_transaction_product_owner_consistency",
        "inventory_detail_available_identity",
        "inventory_summary_available_identity",
        "inventory_summary_vs_detail",
        "inventory_transaction_replay_onhand",
        "inventory_batch_tracking_integrity",
        "inventory_expiry_tracking_integrity",
        "inventory_serial_tracking_integrity",
    },
    "billing": {
        "billing_metric_non_negative",
        "billing_accrual_consistency",
        "billing_event_pricing_completeness",
        "billing_locked_period_linkage",
        "billing_invoiced_accrual_linkage",
        "bill_header_totals",
        "bill_line_matches_accrual",
    },
}
POS_CHECKS = {
    "sale_amount",
    "sale_payment",
    "sale_outbound",
    "inventory_flow",
    "inactive_restore_layer",
    "return_refund",
}


def window_dates(row):
    tz = ZoneInfo("Asia/Shanghai")
    return [
        datetime.fromisoformat(row[k]).astimezone(tz).date().isoformat()
        for k in ("started_at", "finished_at")
    ]


def verify(checkpoint, row, run_id, deployment, writes):
    """Match observed sales and scans to this window and check all native reconciliation results."""
    if (
        not checkpoint["snapshot_id"]
        or checkpoint["as_of"] != row["finished_at"]
        or checkpoint["run_id"] != run_id
        or checkpoint["deployment_identity"] != deployment
        or checkpoint["users"] != row["users"]
    ):
        raise ValueError("snapshot does not match the measured stage")
    actual = Counter(
        (d["role"], d["warehouse_id"], d["request_id"]) for d in checkpoint["committed"]
    )
    successes = {key for key, outcome in writes.items() if outcome == "ok"}
    if (
        not successes <= actual.keys()
        or not actual.keys() <= writes.keys()
        or any(n != 1 for n in actual.values())
    ):
        raise ValueError("committed business keys do not reconcile with client writes")
    # Include uncertain responses whose writes were committed when checking POS coverage.
    sales = Counter(wh for role, wh, _ in actual if role == "pos")
    if not sales or not any(role == "pda" for role, _, _ in successes):
        raise ValueError("both POS and PDA must have recorded business writes")
    recon = checkpoint["reconciliation"]
    if recon["scope"] != dict(owner_id=None, warehouse_id=None, service_date=None, period_id=None):
        raise ValueError("global inventory and billing reconciliation required")
    counts = {}
    for section, names in CHECKS.items():
        checks = recon[section]["checks"]
        if (
            not names <= {c["name"] for c in checks}
            or len(checks) != len({c["name"] for c in checks})
            or any(
                c["skipped"] is not False
                or type(c["issue_count"]) is not int
                or c["issue_count"] < 0
                or c["ok"] != (c["issue_count"] == 0)
                for c in checks
            )
        ):
            raise ValueError(f"incomplete {section} reconciliation")
        counts[section] = sum(c["issue_count"] for c in checks)
        if counts[section] != recon[section]["issue_count"]:
            raise ValueError(f"inconsistent {section} reconciliation")
    if Counter(r["scope"]["warehouse_id"] for r in checkpoint["pos"]) != Counter(sales.keys()):
        raise ValueError("POS reconciliation missing for measured warehouses")
    start_date, end_date = window_dates(row)
    for r in checkpoint["pos"]:
        s = r["summary"]
        checked = datetime.fromisoformat(r["checked_at"])
        # Native POS reports use Shanghai local time when Django USE_TZ is False.
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        if (
            checked < datetime.fromisoformat(row["finished_at"])
            or r["period"] != {"start_date": start_date, "end_date": end_date}
            or not POS_CHECKS <= {c["code"] for c in r["checks"]}
            or s["check_count"] != len(r["checks"])
            or s["sale_count"] < sales[r["scope"]["warehouse_id"]]
            or type(s["error_count"]) is not int
            or s["error_count"] < 0
            or s["error_count"] != sum(c["error_count"] for c in r["checks"])
        ):
            raise ValueError("POS reconciliation does not cover this phase and its sales")
        counts["billing"] += s["error_count"]
    return counts


@contextmanager
def readonly_snapshot(database, server_uuid, snapshot_id):
    """Read a consistent snapshot only from the explicitly identified MySQL instance."""
    import django
    from django.conf import settings
    from django.db import connection, transaction

    if not database.strip() or not snapshot_id.strip():
        raise ValueError("snapshot database and snapshot ID required")
    expected_uuid = str(UUID(server_uuid))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    if settings.DATABASES["default"]["NAME"] != database:
        raise ValueError("configured database differs from the supplied snapshot database")
    django.setup()
    if connection.vendor != "mysql" or not connection.get_autocommit():
        raise ValueError("a new MySQL connection to the restored snapshot is required")
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), @@server_uuid")
        db_name, actual_uuid = cursor.fetchone()
        if db_name != database or str(actual_uuid).lower() != expected_uuid:
            raise ValueError("snapshot database or server UUID mismatch")
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    with transaction.atomic(durable=True):
        yield {"database": db_name, "server_uuid": expected_uuid, "snapshot_id": snapshot_id}


def capture(args):
    from .wms_capacity import load, save

    client = load(args.client)
    row = next((r for r in client["runs"] if r["users"] == args.users), None)
    if not row or args.as_of != row["finished_at"]:
        raise ValueError("recovery point must match the client phase finish time")
    start_date, end_date = window_dates(row)
    prefix = f"cap-{client['run_id']}-{args.users}-"
    with readonly_snapshot(
        args.snapshot_db, args.snapshot_server_uuid, args.snapshot_id
    ) as identity:
        from allapp.accounts.models import AuditEvent
        from allapp.core.data_accuracy import reconcile_data_accuracy
        from allapp.pos.accuracy import reconcile_pos_accuracy
        from allapp.pos.models import PosSale

        recon = reconcile_data_accuracy()
        sales = PosSale.objects.filter(src_bill_no__startswith=prefix).values_list(
            "warehouse_id", "src_bill_no"
        )
        scans = AuditEvent.objects.filter(
            action="inbound.putaway.record", succeeded=True, metadata__request_id__startswith=prefix
        ).values_list("warehouse_id", "metadata__request_id")
        committed = [
            dict(role=role, warehouse_id=wh, request_id=key)
            for role, rows in (("pos", sales), ("pda", scans))
            for wh, key in rows
        ]
        pos = [
            reconcile_pos_accuracy(
                user=SimpleNamespace(warehouse_id=wh),
                params=dict(start_date=start_date, end_date=end_date),
            )
            for wh in sorted({r["warehouse_id"] for r in committed if r["role"] == "pos"})
        ]
    save(
        args.output,
        dict(
            **identity,
            as_of=args.as_of,
            users=args.users,
            run_id=client["run_id"],
            deployment_identity=client["deployment_identity"],
            committed=committed,
            reconciliation=recon,
            pos=pos,
        ),
    )


def main():
    cmd = argparse.ArgumentParser(description=__doc__)
    cmd.add_argument("--client", type=Path, required=True)
    cmd.add_argument("--users", type=int, choices=(50, 100), required=True)
    for flag in ("snapshot-db", "snapshot-server-uuid", "snapshot-id", "as-of"):
        cmd.add_argument("--" + flag, required=True)
    cmd.add_argument("--output", type=Path, required=True)
    args = cmd.parse_args()
    try:
        capture(args)
    except (ValueError, OSError, KeyError) as exc:
        cmd.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    main()

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional

from django.db.models import DecimalField, ExpressionWrapper, F

from allapp.billing.enums import AccrualStatus
from allapp.billing.models import (
    Bill,
    BillLine,
    BillingAccrual,
    BillingMetricDaily,
    qmoney,
)
from allapp.inventory.models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
)


def _q4(value: Any) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"))


def _normalize_tracking_text(value: Any) -> str:
    return str(value or "").strip().upper()


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def _serialize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _serialize_value(value) for key, value in issue.items()}


@dataclass
class CheckResult:
    name: str
    ok: bool
    issue_count: int
    samples: list[dict]
    note: str = ""
    skipped: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "issue_count": self.issue_count,
            "samples": self.samples,
            "note": self.note,
            "skipped": self.skipped,
        }


def _build_check(
    name: str, issues: Iterable[Dict[str, Any]], *, limit: int, note: str = ""
) -> CheckResult:
    issue_list = list(issues)
    return CheckResult(
        name=name,
        ok=not issue_list,
        issue_count=len(issue_list),
        samples=[_serialize_issue(issue) for issue in issue_list[:limit]],
        note=note,
    )


def _build_skipped_check(name: str, note: str) -> CheckResult:
    return CheckResult(
        name=name, ok=True, issue_count=0, samples=[], note=note, skipped=True
    )


def _inventory_detail_queryset(*, owner_id=None, warehouse_id=None):
    queryset = InventoryDetail.objects.filter(is_active=True)
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)
    return queryset


def _inventory_summary_queryset(*, owner_id=None):
    queryset = InventorySummary.objects.filter(is_active=True)
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    return queryset


def _inventory_transaction_queryset(*, owner_id=None, warehouse_id=None):
    queryset = InventoryTransaction.objects.filter(
        is_active=True, posted_at__isnull=False
    )
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)
    return queryset


def _inventory_replay_key(
    *,
    owner_id,
    warehouse_id,
    location_id,
    product_id,
    batch_no="",
    production_date=None,
    expiry_date=None,
    serial_no="",
):
    return (
        owner_id,
        warehouse_id,
        location_id,
        product_id,
        _normalize_tracking_text(batch_no),
        production_date,
        expiry_date,
        _normalize_tracking_text(serial_no),
    )


def _inventory_replay_issue_payload(
    key, *, issue, detail_qty, replay_qty, detail_rows=0, tx_rows=0
):
    return {
        "owner_id": key[0],
        "warehouse_id": key[1],
        "location_id": key[2],
        "product_id": key[3],
        "batch_no": key[4],
        "production_date": key[5],
        "expiry_date": key[6],
        "serial_no": key[7],
        "issue": issue,
        "detail_onhand_qty": _q4(detail_qty),
        "replayed_onhand_qty": _q4(replay_qty),
        "detail_rows": detail_rows,
        "tx_rows": tx_rows,
    }


def _tracking_issue_payload(*, source, issue_id, row, problems):
    return {
        "source": source,
        "id": issue_id,
        "owner_id": row.owner_id,
        "warehouse_id": row.warehouse_id,
        "location_id": row.location_id,
        "product_id": row.product_id,
        "batch_no": _normalize_tracking_text(getattr(row, "batch_no", "")),
        "production_date": getattr(row, "production_date", None),
        "expiry_date": getattr(row, "expiry_date", None),
        "serial_no": _normalize_tracking_text(
            getattr(row, "serial_no_norm", None) or getattr(row, "serial_no", "")
        ),
        "problems": ",".join(problems),
    }


def reconcile_inventory_accuracy(
    *, owner_id=None, warehouse_id=None, limit: int = 20
) -> Dict[str, Any]:
    checks = []

    detail_expected_available = ExpressionWrapper(
        F("onhand_qty") - F("allocated_qty") - F("locked_qty") - F("damaged_qty"),
        output_field=DecimalField(max_digits=18, decimal_places=4),
    )
    detail_identity_issues = list(
        _inventory_detail_queryset(owner_id=owner_id, warehouse_id=warehouse_id)
        .annotate(expected_available=detail_expected_available)
        .exclude(available_qty=F("expected_available"))
        .values(
            "id",
            "owner_id",
            "warehouse_id",
            "product_id",
            "location_id",
            "onhand_qty",
            "allocated_qty",
            "locked_qty",
            "damaged_qty",
            "available_qty",
            "expected_available",
        )
    )
    checks.append(
        _build_check(
            "inventory_detail_available_identity", detail_identity_issues, limit=limit
        )
    )

    summary_expected_available = ExpressionWrapper(
        F("onhand_qty") - F("allocated_qty") - F("locked_qty") - F("damaged_qty"),
        output_field=DecimalField(max_digits=18, decimal_places=4),
    )
    summary_identity_issues = list(
        _inventory_summary_queryset(owner_id=owner_id)
        .annotate(expected_available=summary_expected_available)
        .exclude(available_qty=F("expected_available"))
        .values(
            "id",
            "owner_id",
            "product_id",
            "onhand_qty",
            "allocated_qty",
            "locked_qty",
            "damaged_qty",
            "available_qty",
            "expected_available",
        )
    )
    checks.append(
        _build_check(
            "inventory_summary_available_identity", summary_identity_issues, limit=limit
        )
    )

    if warehouse_id:
        checks.append(
            _build_skipped_check(
                "inventory_summary_vs_detail",
                (
                    "InventorySummary is owner+product scoped. "
                    "This check is skipped when --warehouse is provided."
                ),
            )
        )
    else:
        detail_totals = {}
        for row in _inventory_detail_queryset(owner_id=owner_id).values(
            "owner_id",
            "product_id",
            "onhand_qty",
            "allocated_qty",
            "locked_qty",
            "damaged_qty",
        ):
            key = (row["owner_id"], row["product_id"])
            bucket = detail_totals.setdefault(
                key,
                {
                    "onhand_qty": Decimal("0.0000"),
                    "allocated_qty": Decimal("0.0000"),
                    "locked_qty": Decimal("0.0000"),
                    "damaged_qty": Decimal("0.0000"),
                },
            )
            bucket["onhand_qty"] += Decimal(row["onhand_qty"] or 0)
            bucket["allocated_qty"] += Decimal(row["allocated_qty"] or 0)
            bucket["locked_qty"] += Decimal(row["locked_qty"] or 0)
            bucket["damaged_qty"] += Decimal(row["damaged_qty"] or 0)

        summary_rows = {
            (row.owner_id, row.product_id): row
            for row in _inventory_summary_queryset(owner_id=owner_id)
        }

        parity_issues = []
        for key in sorted(set(detail_totals.keys()) | set(summary_rows.keys())):
            expected = detail_totals.get(
                key,
                {
                    "onhand_qty": Decimal("0.0000"),
                    "allocated_qty": Decimal("0.0000"),
                    "locked_qty": Decimal("0.0000"),
                    "damaged_qty": Decimal("0.0000"),
                },
            )
            expected_available = (
                Decimal(expected["onhand_qty"])
                - Decimal(expected["allocated_qty"])
                - Decimal(expected["locked_qty"])
                - Decimal(expected["damaged_qty"])
            )
            summary = summary_rows.get(key)
            if summary is None:
                if any(_q4(value) != Decimal("0.0000") for value in expected.values()):
                    parity_issues.append(
                        {
                            "owner_id": key[0],
                            "product_id": key[1],
                            "issue": "missing_summary",
                            "detail_onhand_qty": _q4(expected["onhand_qty"]),
                            "detail_allocated_qty": _q4(expected["allocated_qty"]),
                            "detail_locked_qty": _q4(expected["locked_qty"]),
                            "detail_damaged_qty": _q4(expected["damaged_qty"]),
                            "detail_available_qty": _q4(expected_available),
                        }
                    )
                continue

            comparisons = {
                "onhand_qty": (_q4(summary.onhand_qty), _q4(expected["onhand_qty"])),
                "allocated_qty": (
                    _q4(summary.allocated_qty),
                    _q4(expected["allocated_qty"]),
                ),
                "locked_qty": (_q4(summary.locked_qty), _q4(expected["locked_qty"])),
                "damaged_qty": (_q4(summary.damaged_qty), _q4(expected["damaged_qty"])),
                "available_qty": (_q4(summary.available_qty), _q4(expected_available)),
            }
            mismatched_fields = [
                field_name
                for field_name, (actual_value, expected_value) in comparisons.items()
                if actual_value != expected_value
            ]
            if mismatched_fields:
                parity_issues.append(
                    {
                        "summary_id": summary.id,
                        "owner_id": summary.owner_id,
                        "product_id": summary.product_id,
                        "issue": "summary_detail_mismatch",
                        "fields": ",".join(mismatched_fields),
                        "summary_onhand_qty": _q4(summary.onhand_qty),
                        "detail_onhand_qty": _q4(expected["onhand_qty"]),
                        "summary_allocated_qty": _q4(summary.allocated_qty),
                        "detail_allocated_qty": _q4(expected["allocated_qty"]),
                        "summary_locked_qty": _q4(summary.locked_qty),
                        "detail_locked_qty": _q4(expected["locked_qty"]),
                        "summary_damaged_qty": _q4(summary.damaged_qty),
                        "detail_damaged_qty": _q4(expected["damaged_qty"]),
                        "summary_available_qty": _q4(summary.available_qty),
                        "detail_available_qty": _q4(expected_available),
                    }
                )

        checks.append(
            _build_check("inventory_summary_vs_detail", parity_issues, limit=limit)
        )

    detail_replay_buckets = {}
    for row in _inventory_detail_queryset(
        owner_id=owner_id, warehouse_id=warehouse_id
    ).values(
        "owner_id",
        "warehouse_id",
        "location_id",
        "product_id",
        "batch_no",
        "production_date",
        "expiry_date",
        "serial_no",
        "serial_no_norm",
        "onhand_qty",
    ):
        key = _inventory_replay_key(
            owner_id=row["owner_id"],
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            product_id=row["product_id"],
            batch_no=row["batch_no"],
            production_date=row["production_date"],
            expiry_date=row["expiry_date"],
            serial_no=row["serial_no_norm"] or row["serial_no"],
        )
        bucket = detail_replay_buckets.setdefault(
            key,
            {"onhand_qty": Decimal("0.0000"), "detail_rows": 0},
        )
        bucket["onhand_qty"] += Decimal(row["onhand_qty"] or 0)
        bucket["detail_rows"] += 1

    tx_replay_buckets = {}
    for row in _inventory_transaction_queryset(
        owner_id=owner_id, warehouse_id=warehouse_id
    ).values(
        "owner_id",
        "warehouse_id",
        "location_id",
        "product_id",
        "batch_no",
        "production_date",
        "expiry_date",
        "serial_no",
        "qty_delta",
    ):
        key = _inventory_replay_key(
            owner_id=row["owner_id"],
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            product_id=row["product_id"],
            batch_no=row["batch_no"],
            production_date=row["production_date"],
            expiry_date=row["expiry_date"],
            serial_no=row["serial_no"],
        )
        bucket = tx_replay_buckets.setdefault(
            key,
            {"qty_delta": Decimal("0.0000"), "tx_rows": 0},
        )
        bucket["qty_delta"] += Decimal(row["qty_delta"] or 0)
        bucket["tx_rows"] += 1

    replay_issues = []
    replay_keys = sorted(
        set(detail_replay_buckets.keys()) | set(tx_replay_buckets.keys()),
        key=lambda key: tuple("" if value is None else str(value) for value in key),
    )
    for key in replay_keys:
        detail_bucket = detail_replay_buckets.get(
            key, {"onhand_qty": Decimal("0.0000"), "detail_rows": 0}
        )
        tx_bucket = tx_replay_buckets.get(
            key, {"qty_delta": Decimal("0.0000"), "tx_rows": 0}
        )
        detail_qty = _q4(detail_bucket["onhand_qty"])
        replay_qty = _q4(tx_bucket["qty_delta"])
        if detail_qty == replay_qty:
            continue
        if detail_bucket["detail_rows"] == 0 and replay_qty != Decimal("0.0000"):
            issue = "posted_replay_without_detail"
        elif tx_bucket["tx_rows"] == 0 and detail_qty != Decimal("0.0000"):
            issue = "detail_onhand_without_posted_replay"
        else:
            issue = "detail_onhand_replay_mismatch"
        replay_issues.append(
            _inventory_replay_issue_payload(
                key,
                issue=issue,
                detail_qty=detail_bucket["onhand_qty"],
                replay_qty=tx_bucket["qty_delta"],
                detail_rows=detail_bucket["detail_rows"],
                tx_rows=tx_bucket["tx_rows"],
            )
        )
    checks.append(
        _build_check(
            "inventory_transaction_replay_onhand",
            replay_issues,
            limit=limit,
            note=(
                "Replay uses posted InventoryTransaction.qty_delta only, "
                "so it validates InventoryDetail.onhand_qty but not "
                "allocated/locked/damaged history."
            ),
        )
    )

    tracking_details = list(
        _inventory_detail_queryset(
            owner_id=owner_id, warehouse_id=warehouse_id
        ).select_related("product")
    )
    tracking_transactions = list(
        _inventory_transaction_queryset(
            owner_id=owner_id, warehouse_id=warehouse_id
        ).select_related("product")
    )

    batch_issues = []
    expiry_issues = []
    serial_issues = []
    serial_detail_duplicates = {}

    for detail in tracking_details:
        batch_no = _normalize_tracking_text(detail.batch_no)
        serial_no = _normalize_tracking_text(detail.serial_no_norm or detail.serial_no)
        product = detail.product

        if getattr(product, "batch_control", False) and not batch_no:
            batch_issues.append(
                _tracking_issue_payload(
                    source="detail",
                    issue_id=detail.id,
                    row=detail,
                    problems=["missing_batch_no"],
                )
            )

        expiry_problems = []
        if getattr(product, "expiry_control", False):
            if not detail.expiry_date:
                expiry_problems.append("missing_expiry_date")
            if (
                getattr(product, "expiry_basis", None) == "MFG"
                and not detail.production_date
            ):
                expiry_problems.append("missing_production_date")
        if (
            detail.production_date
            and detail.expiry_date
            and detail.expiry_date < detail.production_date
        ):
            expiry_problems.append("expiry_before_production_date")
        if expiry_problems:
            expiry_issues.append(
                _tracking_issue_payload(
                    source="detail",
                    issue_id=detail.id,
                    row=detail,
                    problems=expiry_problems,
                )
            )

        serial_problems = []
        if getattr(product, "serial_control", False):
            if not serial_no:
                serial_problems.append("missing_serial_no")
            if _q4(detail.onhand_qty) > Decimal("1.0000"):
                serial_problems.append("onhand_qty_gt_one")
            if _q4(detail.allocated_qty) > Decimal("1.0000"):
                serial_problems.append("allocated_qty_gt_one")
            if _q4(detail.locked_qty) > Decimal("1.0000"):
                serial_problems.append("locked_qty_gt_one")
            if _q4(detail.damaged_qty) > Decimal("1.0000"):
                serial_problems.append("damaged_qty_gt_one")
            if _q4(detail.available_qty) > Decimal("1.0000"):
                serial_problems.append("available_qty_gt_one")
            if serial_no:
                duplicate_key = (detail.owner_id, detail.product_id, serial_no)
                serial_detail_duplicates.setdefault(duplicate_key, []).append(detail)
        if serial_problems:
            serial_issues.append(
                _tracking_issue_payload(
                    source="detail",
                    issue_id=detail.id,
                    row=detail,
                    problems=serial_problems,
                )
            )

    for tx in tracking_transactions:
        batch_no = _normalize_tracking_text(tx.batch_no)
        serial_no = _normalize_tracking_text(tx.serial_no)
        product = tx.product

        if getattr(product, "batch_control", False) and not batch_no:
            batch_issues.append(
                _tracking_issue_payload(
                    source="transaction",
                    issue_id=tx.id,
                    row=tx,
                    problems=["missing_batch_no"],
                )
            )

        expiry_problems = []
        if getattr(product, "expiry_control", False):
            if not tx.expiry_date:
                expiry_problems.append("missing_expiry_date")
            if (
                getattr(product, "expiry_basis", None) == "MFG"
                and not tx.production_date
            ):
                expiry_problems.append("missing_production_date")
        if (
            tx.production_date
            and tx.expiry_date
            and tx.expiry_date < tx.production_date
        ):
            expiry_problems.append("expiry_before_production_date")
        if expiry_problems:
            expiry_issues.append(
                _tracking_issue_payload(
                    source="transaction",
                    issue_id=tx.id,
                    row=tx,
                    problems=expiry_problems,
                )
            )

        serial_problems = []
        if getattr(product, "serial_control", False):
            if not serial_no:
                serial_problems.append("missing_serial_no")
            if _q4(abs(tx.qty_delta)) != Decimal("1.0000"):
                serial_problems.append("tx_abs_qty_not_one")
        if serial_problems:
            serial_issues.append(
                _tracking_issue_payload(
                    source="transaction",
                    issue_id=tx.id,
                    row=tx,
                    problems=serial_problems,
                )
            )

    for duplicate_details in serial_detail_duplicates.values():
        if len(duplicate_details) <= 1:
            continue
        for detail in duplicate_details:
            serial_issues.append(
                _tracking_issue_payload(
                    source="detail",
                    issue_id=detail.id,
                    row=detail,
                    problems=["duplicate_active_serial"],
                )
            )

    checks.append(
        _build_check("inventory_batch_tracking_integrity", batch_issues, limit=limit)
    )
    checks.append(
        _build_check("inventory_expiry_tracking_integrity", expiry_issues, limit=limit)
    )
    checks.append(
        _build_check("inventory_serial_tracking_integrity", serial_issues, limit=limit)
    )

    issue_count = sum(check.issue_count for check in checks)
    return {
        "ok": issue_count == 0,
        "issue_count": issue_count,
        "checks": [check.to_dict() for check in checks],
    }


def _billing_metric_queryset(*, owner_id=None, warehouse_id=None, service_date=None):
    queryset = BillingMetricDaily.objects.all()
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)
    if service_date:
        queryset = queryset.filter(service_date=service_date)
    return queryset


def _billing_accrual_queryset(
    *, owner_id=None, warehouse_id=None, service_date=None, period_id=None
):
    queryset = BillingAccrual.objects.select_related("rule", "period", "event")
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)
    if service_date:
        queryset = queryset.filter(service_date=service_date)
    if period_id:
        queryset = queryset.filter(period_id=period_id)
    return queryset


def _billing_line_queryset(
    *, owner_id=None, warehouse_id=None, service_date=None, period_id=None
):
    queryset = BillLine.objects.select_related("bill", "bill__period", "accrual")
    if owner_id:
        queryset = queryset.filter(bill__owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(bill__warehouse_id=warehouse_id)
    if service_date:
        queryset = queryset.filter(service_date=service_date)
    if period_id:
        queryset = queryset.filter(bill__period_id=period_id)
    return queryset


def _billing_bill_queryset(
    *, owner_id=None, warehouse_id=None, service_date=None, period_id=None
):
    queryset = Bill.objects.select_related("period").prefetch_related("lines")
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)
    if period_id:
        queryset = queryset.filter(period_id=period_id)
    elif service_date:
        bill_ids = (
            BillLine.objects.filter(service_date=service_date)
            .values_list("bill_id", flat=True)
            .distinct()
        )
        queryset = queryset.filter(id__in=bill_ids)
    return queryset


def reconcile_billing_accuracy(
    *,
    owner_id=None,
    warehouse_id=None,
    service_date: Optional[datetime.date] = None,
    period_id=None,
    limit: int = 20,
) -> Dict[str, Any]:
    checks = []

    metric_issues = list(
        _billing_metric_queryset(
            owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date
        )
        .filter(value__lt=0)
        .values(
            "id", "owner_id", "warehouse_id", "service_date", "metric_type", "value"
        )
    )
    checks.append(
        _build_check("billing_metric_non_negative", metric_issues, limit=limit)
    )

    accrual_issues = []
    accruals = list(
        _billing_accrual_queryset(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            period_id=period_id,
        )
    )
    for accrual in accruals:
        problems = []
        if accrual.amount < 0:
            problems.append("amount_negative")
        if accrual.quantity < 0:
            problems.append("quantity_negative")
        if accrual.unit_price < 0:
            problems.append("unit_price_negative")
        if accrual.tax_amount < 0:
            problems.append("tax_amount_negative")
        if (
            accrual.rule.owner_id is not None
            and accrual.rule.owner_id != accrual.owner_id
        ):
            problems.append("rule_owner_mismatch")
        if (
            accrual.rule.warehouse_id is not None
            and accrual.rule.warehouse_id != accrual.warehouse_id
        ):
            problems.append("rule_warehouse_mismatch")
        if accrual.rule.charge_type != accrual.charge_type:
            problems.append("rule_charge_type_mismatch")
        if (
            accrual.rule.currency
            and accrual.currency
            and accrual.rule.currency != accrual.currency
        ):
            problems.append("rule_currency_mismatch")
        if accrual.period_id:
            if (
                accrual.period.owner_id != accrual.owner_id
                or accrual.period.warehouse_id != accrual.warehouse_id
            ):
                problems.append("period_scope_mismatch")
            if not (
                accrual.period.start_date
                <= accrual.service_date
                <= accrual.period.end_date
            ):
                problems.append("service_date_outside_period")
            if (
                accrual.period.currency
                and accrual.currency
                and accrual.period.currency != accrual.currency
            ):
                problems.append("period_currency_mismatch")
        if accrual.event_id:
            if (
                accrual.event.owner_id != accrual.owner_id
                or accrual.event.warehouse_id != accrual.warehouse_id
            ):
                problems.append("event_scope_mismatch")
            if accrual.event.charge_type != accrual.charge_type:
                problems.append("event_charge_type_mismatch")
            if accrual.event.service_date != accrual.service_date:
                problems.append("event_service_date_mismatch")
        if problems:
            accrual_issues.append(
                {
                    "accrual_id": accrual.id,
                    "owner_id": accrual.owner_id,
                    "warehouse_id": accrual.warehouse_id,
                    "service_date": accrual.service_date,
                    "charge_type": accrual.charge_type,
                    "problems": ",".join(problems),
                }
            )
    checks.append(
        _build_check("billing_accrual_consistency", accrual_issues, limit=limit)
    )

    bill_issues = []
    for bill in _billing_bill_queryset(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date=service_date,
        period_id=period_id,
    ):
        problems = []
        line_subtotal = sum(
            (Decimal(line.amount or 0) for line in bill.lines.all()), Decimal("0.00")
        )
        line_tax_total = sum(
            (Decimal(line.tax_amount or 0) for line in bill.lines.all()),
            Decimal("0.00"),
        )
        expected_total = qmoney(
            Decimal(bill.subtotal or 0) + Decimal(bill.tax_total or 0)
        )
        if bill.subtotal < 0:
            problems.append("subtotal_negative")
        if bill.tax_total < 0:
            problems.append("tax_total_negative")
        if bill.total < 0:
            problems.append("total_negative")
        if (
            bill.period.owner_id != bill.owner_id
            or bill.period.warehouse_id != bill.warehouse_id
        ):
            problems.append("period_scope_mismatch")
        if (
            bill.currency
            and bill.period.currency
            and bill.currency != bill.period.currency
        ):
            problems.append("period_currency_mismatch")
        if bill.due_date and bill.issue_date and bill.due_date < bill.issue_date:
            problems.append("due_date_before_issue_date")
        if qmoney(bill.total) != expected_total:
            problems.append("bill_total_formula_mismatch")
        if qmoney(line_subtotal) != qmoney(bill.subtotal):
            problems.append("line_subtotal_mismatch")
        if qmoney(line_tax_total) != qmoney(bill.tax_total):
            problems.append("line_tax_total_mismatch")
        if problems:
            bill_issues.append(
                {
                    "bill_id": bill.id,
                    "invoice_no": bill.invoice_no,
                    "period_id": bill.period_id,
                    "problems": ",".join(problems),
                    "subtotal": qmoney(bill.subtotal),
                    "line_subtotal": qmoney(line_subtotal),
                    "tax_total": qmoney(bill.tax_total),
                    "line_tax_total": qmoney(line_tax_total),
                    "total": qmoney(bill.total),
                    "expected_total": expected_total,
                }
            )
    checks.append(_build_check("bill_header_totals", bill_issues, limit=limit))

    bill_line_issues = []
    for line in _billing_line_queryset(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date=service_date,
        period_id=period_id,
    ):
        problems = []
        if line.quantity < 0:
            problems.append("quantity_negative")
        if line.unit_price < 0:
            problems.append("unit_price_negative")
        if line.amount < 0:
            problems.append("amount_negative")
        if line.tax_amount < 0:
            problems.append("tax_amount_negative")
        if (
            line.bill.owner_id != line.accrual.owner_id
            or line.bill.warehouse_id != line.accrual.warehouse_id
        ):
            problems.append("bill_accrual_scope_mismatch")
        if line.bill.period_id != line.accrual.period_id:
            problems.append("bill_accrual_period_mismatch")
        if line.charge_type != line.accrual.charge_type:
            problems.append("charge_type_mismatch")
        if line.service_date != line.accrual.service_date:
            problems.append("service_date_mismatch")
        if line.quantity != line.accrual.quantity:
            problems.append("quantity_mismatch")
        if line.unit_price != line.accrual.unit_price:
            problems.append("unit_price_mismatch")
        if line.amount != line.accrual.amount:
            problems.append("amount_mismatch")
        if line.tax_amount != line.accrual.tax_amount:
            problems.append("tax_amount_mismatch")
        if problems:
            bill_line_issues.append(
                {
                    "bill_line_id": line.id,
                    "bill_id": line.bill_id,
                    "accrual_id": line.accrual_id,
                    "problems": ",".join(problems),
                }
            )
    checks.append(
        _build_check("bill_line_matches_accrual", bill_line_issues, limit=limit)
    )

    line_counts = {}
    line_count_queryset = BillLine.objects.all()
    if owner_id:
        line_count_queryset = line_count_queryset.filter(bill__owner_id=owner_id)
    if warehouse_id:
        line_count_queryset = line_count_queryset.filter(
            bill__warehouse_id=warehouse_id
        )
    if service_date:
        line_count_queryset = line_count_queryset.filter(service_date=service_date)
    if period_id:
        line_count_queryset = line_count_queryset.filter(bill__period_id=period_id)
    for accrual_id in line_count_queryset.values_list("accrual_id", flat=True):
        line_counts[accrual_id] = line_counts.get(accrual_id, 0) + 1

    invoicing_issues = []
    for accrual in accruals:
        line_count = line_counts.get(accrual.id, 0)
        if accrual.status == AccrualStatus.INVOICED and line_count != 1:
            invoicing_issues.append(
                {
                    "accrual_id": accrual.id,
                    "status": accrual.status,
                    "line_count": line_count,
                    "issue": "invoiced_accrual_requires_exactly_one_bill_line",
                }
            )
        elif accrual.status != AccrualStatus.INVOICED and line_count:
            invoicing_issues.append(
                {
                    "accrual_id": accrual.id,
                    "status": accrual.status,
                    "line_count": line_count,
                    "issue": "non_invoiced_accrual_should_not_have_bill_line",
                }
            )
    checks.append(
        _build_check("billing_invoiced_accrual_linkage", invoicing_issues, limit=limit)
    )

    issue_count = sum(check.issue_count for check in checks)
    return {
        "ok": issue_count == 0,
        "issue_count": issue_count,
        "checks": [check.to_dict() for check in checks],
    }


def reconcile_data_accuracy(
    *,
    owner_id=None,
    warehouse_id=None,
    service_date: Optional[datetime.date] = None,
    period_id=None,
    include_inventory: bool = True,
    include_billing: bool = True,
    limit: int = 20,
) -> Dict[str, Any]:
    if not include_inventory and not include_billing:
        raise ValueError(
            "At least one of include_inventory or include_billing must be True."
        )

    result = {
        "scope": {
            "owner_id": owner_id,
            "warehouse_id": warehouse_id,
            "service_date": service_date.isoformat() if service_date else None,
            "period_id": period_id,
        },
        "inventory": None,
        "billing": None,
    }

    total_issues = 0
    ok = True
    if include_inventory:
        result["inventory"] = reconcile_inventory_accuracy(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            limit=limit,
        )
        total_issues += result["inventory"]["issue_count"]
        ok = ok and result["inventory"]["ok"]

    if include_billing:
        result["billing"] = reconcile_billing_accuracy(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            period_id=period_id,
            limit=limit,
        )
        total_issues += result["billing"]["issue_count"]
        ok = ok and result["billing"]["ok"]

    result["ok"] = ok
    result["issue_count"] = total_issues
    return result

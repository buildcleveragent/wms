"""P1 boss cockpit read models.

All functions preserve original currencies and accept an already-authorized
scope.  They intentionally return coverage/warnings instead of inventing
historical values.
"""

import calendar
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.baseinfo.models import Owner
from allapp.billing.enums import (
    AccrualStatus,
    BillDocumentStatus,
    PricingStatus,
    SourceQuality,
)
from allapp.billing.models import (
    Bill,
    BillingAccrual,
    BillingEvent,
    BillingJobRun,
    BillingPeriod,
    PaymentAllocation,
)
from allapp.billing.services.integrity import build_close_readiness
from allapp.inventory.models import InventoryLayerPosition, InventorySnapshotDaily
from allapp.locations.models import Location
from allapp.tasking.models import WmsTask

from .boss_contract import build_meta, normalize_currency, warning
from .models import OperatingTarget

ZERO = Decimal("0.00")


def _today():
    current = timezone.now()
    return (
        timezone.localtime(current).date()
        if timezone.is_aware(current)
        else current.date()
    )


def _scoped(
    user,
    qs,
    owner_id=None,
    warehouse_id=None,
    *,
    owner_field="owner_id",
    warehouse_field="warehouse_id"
):
    qs = AccessScope.for_user(user).filter_queryset(
        qs, owner_field=owner_field, warehouse_field=warehouse_field
    )
    if owner_id:
        qs = qs.filter(**{owner_field: owner_id})
    if warehouse_id:
        qs = qs.filter(**{warehouse_field: warehouse_id})
    return qs


def _money_bucket(buckets, currency):
    code = normalize_currency(currency)
    return buckets.setdefault(
        code,
        {
            "currency": code,
            "record_count": 0,
            "subtotal": ZERO,
            "tax_total": ZERO,
            "total": ZERO,
        },
    )


def _amounts_by_currency(queryset):
    """Return non-void accrual impact without ever mixing currencies."""

    rows = []
    for row in (
        queryset.values("currency")
        .annotate(
            record_count=Count("id"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("currency")
    ):
        subtotal = row["subtotal"] or ZERO
        tax_total = row["tax_total"] or ZERO
        rows.append(
            {
                "currency": normalize_currency(row["currency"]),
                "record_count": row["record_count"],
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )
    return rows


def build_revenue_assurance(*, user, owner_id, warehouse_id, date_from, date_to, scope):
    events = _scoped(
        user,
        BillingEvent.objects.filter(service_date__range=(date_from, date_to)),
        owner_id,
        warehouse_id,
    )
    accruals = _scoped(
        user,
        BillingAccrual.objects.filter(service_date__range=(date_from, date_to)),
        owner_id,
        warehouse_id,
    )
    periods = _scoped(
        user,
        BillingPeriod.objects.filter(start_date__lte=date_to, end_date__gte=date_from),
        owner_id,
        warehouse_id,
    )
    unpriced = events.filter(
        pricing_status__in=[PricingStatus.PENDING, PricingStatus.UNPRICED]
    )
    missing_accrual = events.filter(pricing_status=PricingStatus.ACCRUED).exclude(
        billingaccrual__status__in=[
            AccrualStatus.OPEN,
            AccrualStatus.LOCKED,
            AccrualStatus.INVOICED,
        ]
    )
    approximate = accruals.filter(source_quality=SourceQuality.APPROXIMATE).exclude(
        status=AccrualStatus.VOID
    )
    failed_jobs = _scoped(
        user,
        BillingJobRun.objects.filter(
            service_date__range=(date_from, date_to), status__in=["FAILED", "WARNING"]
        ),
        owner_id,
        warehouse_id,
    )
    pending_close = periods.filter(status="OPEN", end_date__lt=date_to)
    late_events = events.none()
    for period in periods.exclude(closed_at__isnull=True):
        late_events = late_events | events.filter(
            owner_id=period.owner_id,
            warehouse_id=period.warehouse_id,
            service_date__range=(period.start_date, period.end_date),
            created_at__gt=period.closed_at,
        )
    late_open_accruals = accruals.filter(status=AccrualStatus.OPEN)
    # A correlated range condition is clearer and safer than trying to infer a
    # period from owner/warehouse alone when multiple periods are selected.
    late_open_ids = set()
    for period in periods.exclude(status="OPEN"):
        late_open_ids.update(
            late_open_accruals.filter(
                owner_id=period.owner_id,
                warehouse_id=period.warehouse_id,
                service_date__range=(period.start_date, period.end_date),
            ).values_list("id", flat=True)
        )
    late_open_accruals = accruals.filter(id__in=late_open_ids)

    variance_rows = []
    blockers = defaultdict(int)
    for period in periods.order_by("start_date", "id"):
        period_accruals = accruals.filter(period=period).exclude(
            status=AccrualStatus.VOID
        )
        acc = period_accruals.aggregate(subtotal=Sum("amount"), tax=Sum("tax_amount"))
        bill = (
            Bill.objects.filter(period=period)
            .exclude(document_status=BillDocumentStatus.VOID)
            .first()
        )
        line = (
            bill.lines.aggregate(subtotal=Sum("amount"), tax=Sum("tax_amount"))
            if bill
            else {}
        )
        currency = normalize_currency(period.currency)
        a_sub = acc["subtotal"] or ZERO
        a_tax = acc["tax"] or ZERO
        l_sub = line.get("subtotal") or ZERO
        l_tax = line.get("tax") or ZERO
        h_sub = bill.subtotal if bill else ZERO
        h_tax = bill.tax_total if bill else ZERO
        variance_rows.append(
            {
                "period_id": period.id,
                "label": period.label,
                "status": period.status,
                "currency": currency,
                "accrual": {
                    "subtotal": a_sub,
                    "tax_total": a_tax,
                    "total": a_sub + a_tax,
                },
                "bill_lines": {
                    "subtotal": l_sub,
                    "tax_total": l_tax,
                    "total": l_sub + l_tax,
                },
                "bill_header": {
                    "subtotal": h_sub,
                    "tax_total": h_tax,
                    "total": h_sub + h_tax,
                },
                "variance": {
                    "subtotal": a_sub - h_sub,
                    "tax_total": a_tax - h_tax,
                    "total": (a_sub + a_tax) - (h_sub + h_tax),
                },
            }
        )
        readiness = build_close_readiness(
            owner_id=period.owner_id,
            warehouse_id=period.warehouse_id,
            start_date=period.start_date,
            end_date=period.end_date,
            for_invoice=period.status == "CLOSED",
        )
        for code, count in readiness["by_code"].items():
            blockers[code] += count

    sections = {
        "unpriced_events": {
            "count": unpriced.count(),
            "samples": list(
                unpriced.values(
                    "id",
                    "owner_id",
                    "warehouse_id",
                    "service_date",
                    "charge_type",
                    "pricing_reason",
                )[:10]
            ),
        },
        "missing_accruals": {
            "count": missing_accrual.count()
            + blockers.get("BILLING_CONTRACT_EVENT_MISSING", 0)
            + blockers.get("BILLING_CONTRACT_METRIC_EVENT_MISSING", 0),
            "samples": list(
                missing_accrual.values("id", "service_date", "charge_type")[:10]
            ),
        },
        "late_arriving_charges": {
            "count": late_events.distinct().count() + late_open_accruals.count(),
            "impact_by_currency": _amounts_by_currency(late_open_accruals),
            "samples": list(
                late_events.distinct().values("id", "service_date", "created_at")[:10]
            )
            + list(
                late_open_accruals.values(
                    "id", "service_date", "created_at", "currency", "amount"
                )[:10]
            ),
        },
        "approximate_sources": {
            "count": approximate.count(),
            "impact_by_currency": _amounts_by_currency(approximate),
            "samples": list(
                approximate.values("id", "service_date", "charge_type", "source_note")[
                    :10
                ]
            ),
        },
        "billing_job_failures": {
            "count": failed_jobs.count(),
            "samples": list(
                failed_jobs.values(
                    "id", "service_date", "job_name", "status", "message"
                )[:10]
            ),
        },
        "periods_pending_close": {
            "count": pending_close.count(),
            "impact_by_currency": _amounts_by_currency(
                accruals.filter(period__in=pending_close).exclude(
                    status=AccrualStatus.VOID
                )
            ),
            "samples": list(
                pending_close.values("id", "label", "end_date", "currency")[:10]
            ),
        },
        "accrual_invoice_variance": {
            "count": len(variance_rows),
            "items": variance_rows,
        },
        "close_blockers": {"count": sum(blockers.values()), "by_code": dict(blockers)},
    }
    warnings = []
    unknown = accruals.filter(Q(currency="") | Q(currency__isnull=True)).count()
    if unknown:
        warnings.append(warning("UNKNOWN_CURRENCY", unknown))
    return {
        "scope": scope,
        "meta": build_meta(scope=scope, warnings=warnings),
        "sections": sections,
    }


def build_receivables(*, user, owner_id, warehouse_id, date_from, date_to, scope):
    bills = _scoped(
        user,
        Bill.objects.filter(
            issue_date__lte=date_to, document_status=BillDocumentStatus.ISSUED
        ),
        owner_id,
        warehouse_id,
    ).select_related("owner", "warehouse")
    allocations = PaymentAllocation.objects.filter(
        receipt__status__in=["POSTED", "REVERSED"]
    ).filter(
        Q(receipt__date_quality="VERIFIED", receipt__receipt_date__lte=date_to)
        | Q(receipt__date_quality="UNKNOWN", receipt__created_at__date__lte=date_to)
    )
    by_bill = defaultdict(Decimal)
    for row in allocations.filter(bill__in=bills).values(
        "bill_id", "amount", "is_reversal"
    ):
        by_bill[row["bill_id"]] += (
            -row["amount"] if row["is_reversal"] else row["amount"]
        )

    buckets = {}
    owner_overdue = defaultdict(lambda: defaultdict(Decimal))
    rows = []
    missing_due = 0
    for bill in bills.order_by("due_date", "id"):
        paid = max(ZERO, by_bill[bill.id])
        outstanding = max(ZERO, bill.total - paid)
        bucket = _money_bucket(buckets, bill.currency)
        bucket["record_count"] += 1
        bucket["total"] += outstanding
        if bill.total:
            ratio = outstanding / bill.total
            bucket["subtotal"] += bill.subtotal * ratio
            bucket["tax_total"] += bill.tax_total * ratio
        age_band = "NOT_DUE"
        if bill.due_date is None:
            missing_due += 1
            age_band = "UNKNOWN"
        elif outstanding and bill.due_date < date_to:
            days = (date_to - bill.due_date).days
            age_band = "1_30" if days <= 30 else "31_60" if days <= 60 else "61_PLUS"
            owner_overdue[normalize_currency(bill.currency)][
                bill.owner_id
            ] += outstanding
        elif bill.due_date and date_to < bill.due_date <= date_to + datetime.timedelta(
            days=7
        ):
            age_band = "DUE_SOON"
        rows.append(
            {
                "id": bill.id,
                "invoice_no": bill.invoice_no,
                "owner_id": bill.owner_id,
                "owner_name": bill.owner.name,
                "warehouse_id": bill.warehouse_id,
                "currency": normalize_currency(bill.currency),
                "issue_date": bill.issue_date,
                "due_date": bill.due_date,
                "total": bill.total,
                "paid_amount": paid,
                "outstanding_amount": outstanding,
                "payment_status": (
                    "PAID" if outstanding == 0 else "PARTIAL" if paid else "UNPAID"
                ),
                "aging_band": age_band,
            }
        )
    cohort = [row for row in rows if date_from <= row["issue_date"] <= date_to]
    collection = []
    for currency in sorted({row["currency"] for row in cohort}):
        subset = [row for row in cohort if row["currency"] == currency]
        invoiced = sum((row["total"] for row in subset), ZERO)
        paid = sum((row["paid_amount"] for row in subset), ZERO)
        collection.append(
            {
                "currency": currency,
                "numerator": paid,
                "denominator": invoiced,
                "rate": (paid / invoiced if invoiced else None),
            }
        )
    dso = []
    window_start = date_to - datetime.timedelta(days=89)
    for currency, bucket in sorted(buckets.items()):
        issued_90 = (
            bills.filter(
                currency=currency, issue_date__range=(window_start, date_to)
            ).aggregate(v=Sum("total"))["v"]
            or ZERO
        )
        dso.append(
            {
                "currency": currency,
                "outstanding": bucket["total"],
                "issued_90_days": issued_90,
                "days": (bucket["total"] / issued_90 * 90 if issued_90 else None),
            }
        )
    rankings = []
    for currency, owners in owner_overdue.items():
        rankings.append(
            {
                "currency": currency,
                "items": [
                    {"owner_id": oid, "overdue_amount": value}
                    for oid, value in sorted(
                        owners.items(), key=lambda item: (-item[1], item[0])
                    )
                ],
            }
        )
    warnings = [warning("BILLS_MISSING_DUE_DATE", missing_due)] if missing_due else []
    return {
        "scope": scope,
        "meta": build_meta(scope=scope, warnings=warnings),
        "outstanding_by_currency": [buckets[key] for key in sorted(buckets)],
        "collection_rate_by_currency": collection,
        "dso_by_currency": dso,
        "overdue_owner_ranking_by_currency": rankings,
        "bills": rows,
    }


def build_inventory_risk(*, user, owner_id, warehouse_id, date_from, date_to, scope):
    today = _today()
    if date_to != today:
        return {
            "scope": scope,
            "meta": build_meta(
                scope=scope,
                warnings=[warning("FIFO_HISTORY_NOT_AVAILABLE", 1)],
                unavailable=True,
            ),
            "age_bands": [],
            "expiry_bands": [],
            "value_by_currency": [],
        }
    positions = _scoped(
        user,
        InventoryLayerPosition.objects.filter(remaining_qty__gt=0).select_related(
            "layer", "layer__base_uom"
        ),
        owner_id,
        warehouse_id,
        owner_field="layer__owner_id",
        warehouse_field="layer__warehouse_id",
    )
    age = defaultdict(Decimal)
    expiry = defaultdict(Decimal)
    values = defaultdict(Decimal)
    missing_cost = 0
    missing_age = 0
    for pos in positions:
        layer = pos.layer
        uom = layer.base_uom
        uom_key = (uom.code, uom.name, uom.kind)
        if layer.received_date is None:
            age[("UNKNOWN", *uom_key)] += pos.remaining_qty
            missing_age += 1
        else:
            days = (date_to - layer.received_date).days
            band = (
                "0_7"
                if days <= 7
                else (
                    "8_30"
                    if days <= 30
                    else "31_60" if days <= 60 else "61_90" if days <= 90 else "90_PLUS"
                )
            )
            age[(band, *uom_key)] += pos.remaining_qty
        if layer.expiry_date is None:
            expiry[("NO_EXPIRY", *uom_key)] += pos.remaining_qty
        else:
            days = (layer.expiry_date - date_to).days
            band = (
                "EXPIRED"
                if days < 0
                else (
                    "0_7"
                    if days <= 7
                    else (
                        "8_30"
                        if days <= 30
                        else (
                            "31_60"
                            if days <= 60
                            else "61_90" if days <= 90 else "90_PLUS"
                        )
                    )
                )
            )
            expiry[(band, *uom_key)] += pos.remaining_qty
        if layer.unit_cost is None or not layer.cost_currency:
            missing_cost += 1
        else:
            values[normalize_currency(layer.cost_currency)] += (
                pos.remaining_qty * layer.unit_cost
            )
    warnings = []
    if missing_cost:
        warnings.append(warning("INVENTORY_COST_MISSING", missing_cost))
    if missing_age:
        warnings.append(warning("INVENTORY_AGE_UNKNOWN", missing_age))
    warnings.append(warning("FIFO_DAILY_VALUE_HISTORY_MISSING", 1))
    return {
        "scope": scope,
        "meta": build_meta(scope=scope, warnings=warnings),
        "age_bands": [
            {
                "band": key[0],
                "base_unit": {"code": key[1], "name": key[2], "kind": key[3]},
                "quantity": value,
            }
            for key, value in sorted(age.items())
        ],
        "expiry_bands": [
            {
                "band": key[0],
                "base_unit": {"code": key[1], "name": key[2], "kind": key[3]},
                "quantity": value,
            }
            for key, value in sorted(expiry.items())
        ],
        "value_by_currency": [
            {"currency": key, "total": value} for key, value in sorted(values.items())
        ],
        "cost_coverage": {
            "covered_positions": positions.count() - missing_cost,
            "total_positions": positions.count(),
        },
        "turnover_by_currency": [],
        "turnover_status": "UNAVAILABLE",
    }


def build_resource_yield(*, user, owner_id, warehouse_id, date_from, date_to, scope):
    accruals = _scoped(
        user,
        BillingAccrual.objects.filter(service_date__range=(date_from, date_to)).exclude(
            status=AccrualStatus.VOID
        ),
        owner_id,
        warehouse_id,
    )
    revenue = {
        (row["owner_id"], normalize_currency(row["currency"])): row["value"] or ZERO
        for row in accruals.values("owner_id", "currency").annotate(value=Sum("amount"))
    }
    snapshots = _scoped(
        user,
        InventorySnapshotDaily.objects.filter(
            snapshot_date__range=(date_from, date_to)
        ),
        owner_id,
        warehouse_id,
    )
    approximate = snapshots.filter(
        snapshot_source__in=[
            InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
            InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX,
        ]
    ).count()
    expected_days = (date_to - date_from).days + 1
    coverage = snapshots.values("snapshot_date").distinct().count()
    warnings = []
    if approximate:
        warnings.append(warning("RESOURCE_SNAPSHOT_APPROXIMATE", approximate))
    if coverage < expected_days:
        warnings.append(
            warning("RESOURCE_SNAPSHOT_DAYS_MISSING", expected_days - coverage)
        )
    unavailable = approximate > 0 or coverage < expected_days
    if unavailable:
        return {
            "scope": scope,
            "meta": build_meta(scope=scope, warnings=warnings, unavailable=True),
            "rankings_by_currency": [],
        }

    volume_expr = ExpressionWrapper(
        F("onhand_qty") * F("unit_volume_m3_snapshot"),
        output_field=DecimalField(max_digits=24, decimal_places=6),
    )
    resource = {}
    for row in snapshots.values("owner_id").annotate(
        occupied_volume_days=Sum(volume_expr),
        occupied_location_days=Count("location_id", distinct=False),
    ):
        resource[row["owner_id"]] = {
            "average_volume_m3": (row["occupied_volume_days"] or ZERO) / expected_days,
            "average_occupied_locations": Decimal(row["occupied_location_days"] or 0)
            / expected_days,
        }
    tasks = _scoped(
        user,
        WmsTask.objects.filter(
            status=WmsTask.Status.COMPLETED,
            finished_at__date__range=(date_from, date_to),
            task_type__in=[WmsTask.TaskType.RECEIVE, WmsTask.TaskType.DISPATCH],
        ),
        owner_id,
        warehouse_id,
    )
    order_counts = dict(
        tasks.values("owner_id")
        .annotate(value=Count("source_pk", distinct=True))
        .values_list("owner_id", "value")
    )
    by_currency = defaultdict(list)
    currency_totals = defaultdict(Decimal)
    total_volume = sum((item["average_volume_m3"] for item in resource.values()), ZERO)
    owner_names = dict(
        Owner.objects.filter(pk__in={oid for oid, _currency in revenue}).values_list(
            "id", "name"
        )
    )
    for (oid, currency), amount in revenue.items():
        currency_totals[currency] += amount
        row = resource.get(
            oid, {"average_volume_m3": ZERO, "average_occupied_locations": ZERO}
        )
        orders = order_counts.get(oid, 0)
        by_currency[currency].append(
            {
                "owner_id": oid,
                "owner_name": owner_names.get(oid, ""),
                "revenue_subtotal": amount,
                **row,
                "completed_orders": orders,
                "revenue_per_m3": (
                    amount / row["average_volume_m3"]
                    if row["average_volume_m3"]
                    else None
                ),
                "revenue_per_location": (
                    amount / row["average_occupied_locations"]
                    if row["average_occupied_locations"]
                    else None
                ),
                "revenue_per_order": (amount / orders if orders else None),
            }
        )
    groups = []
    for currency, items in sorted(by_currency.items()):
        for item in items:
            revenue_share = (
                item["revenue_subtotal"] / currency_totals[currency]
                if currency_totals[currency]
                else ZERO
            )
            capacity_share = (
                item["average_volume_m3"] / total_volume if total_volume else ZERO
            )
            item["revenue_share"] = revenue_share
            item["capacity_share"] = capacity_share
            item["contribution_gap"] = revenue_share - capacity_share
        groups.append(
            {
                "currency": currency,
                "items": sorted(
                    items, key=lambda item: (item["contribution_gap"], item["owner_id"])
                ),
            }
        )
    return {
        "scope": scope,
        "meta": build_meta(scope=scope),
        "rankings_by_currency": groups,
    }


def build_performance(*, user, owner_id, warehouse_id, date_from, date_to, scope):
    def revenue_range(start, end):
        qs = _scoped(
            user,
            BillingAccrual.objects.filter(service_date__range=(start, end)).exclude(
                status=AccrualStatus.VOID
            ),
            owner_id,
            warehouse_id,
        )
        return {
            normalize_currency(row["currency"]): row["value"] or ZERO
            for row in qs.values("currency").annotate(value=Sum("amount"))
        }

    def compare_ranges(current_range, prior_range):
        current_values = revenue_range(*current_range)
        prior_values = revenue_range(*prior_range)
        return [
            {
                "currency": currency,
                "current": current_values.get(currency, ZERO),
                "prior": prior_values.get(currency, ZERO),
                "change_rate": (
                    (
                        current_values.get(currency, ZERO)
                        - prior_values.get(currency, ZERO)
                    )
                    / prior_values[currency]
                    if prior_values.get(currency)
                    else None
                ),
            }
            for currency in sorted(set(current_values) | set(prior_values))
        ]

    accruals = _scoped(
        user,
        BillingAccrual.objects.filter(service_date__range=(date_from, date_to)).exclude(
            status=AccrualStatus.VOID
        ),
        owner_id,
        warehouse_id,
    )
    actual = [
        {
            "currency": normalize_currency(row["currency"]),
            "subtotal": row["subtotal"] or ZERO,
        }
        for row in accruals.values("currency")
        .annotate(subtotal=Sum("amount"))
        .order_by("currency")
    ]
    prior_to = date_from - datetime.timedelta(days=1)
    prior_from = prior_to - (date_to - date_from)
    prior_qs = _scoped(
        user,
        BillingAccrual.objects.filter(
            service_date__range=(prior_from, prior_to)
        ).exclude(status=AccrualStatus.VOID),
        owner_id,
        warehouse_id,
    )
    prior = {
        normalize_currency(row["currency"]): row["subtotal"] or ZERO
        for row in prior_qs.values("currency").annotate(subtotal=Sum("amount"))
    }
    comparisons = []
    for row in actual:
        before = prior.get(row["currency"], ZERO)
        comparisons.append(
            {
                **row,
                "prior_subtotal": before,
                "change_rate": (
                    (row["subtotal"] - before) / before if before else None
                ),
            }
        )

    week_start = date_to - datetime.timedelta(days=date_to.weekday())
    week_elapsed = (date_to - week_start).days
    previous_week_start = week_start - datetime.timedelta(days=7)
    previous_month_end = date_to.replace(day=1) - datetime.timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_month_to = min(
        previous_month_end,
        previous_month_start + datetime.timedelta(days=date_to.day - 1),
    )
    try:
        yoy_from = date_from.replace(year=date_from.year - 1)
    except ValueError:
        yoy_from = date_from.replace(year=date_from.year - 1, day=28)
    try:
        yoy_to = date_to.replace(year=date_to.year - 1)
    except ValueError:
        yoy_to = date_to.replace(year=date_to.year - 1, day=28)
    fixed_comparisons = {
        "today_vs_yesterday": compare_ranges(
            (date_to, date_to),
            (
                date_to - datetime.timedelta(days=1),
                date_to - datetime.timedelta(days=1),
            ),
        ),
        "week_to_date_vs_previous_week": compare_ranges(
            (week_start, date_to),
            (
                previous_week_start,
                previous_week_start + datetime.timedelta(days=week_elapsed),
            ),
        ),
        "month_to_date_vs_previous_month": compare_ranges(
            (date_to.replace(day=1), date_to),
            (previous_month_start, previous_month_to),
        ),
        "selected_vs_previous_equal_period": comparisons,
        "year_over_year": compare_ranges((date_from, date_to), (yoy_from, yoy_to)),
    }

    month_start = date_to.replace(day=1)
    month_end = date_to.replace(day=calendar.monthrange(date_to.year, date_to.month)[1])
    daily = (
        _scoped(
            user,
            BillingAccrual.objects.filter(
                service_date__range=(month_start, date_to)
            ).exclude(status=AccrualStatus.VOID),
            owner_id,
            warehouse_id,
        )
        .values("service_date", "currency")
        .annotate(value=Sum("amount"))
    )
    series = defaultdict(list)
    for row in daily:
        series[normalize_currency(row["currency"])].append(row)
    complete_end = date_to - datetime.timedelta(days=date_to.weekday() + 1)
    complete_start = complete_end - datetime.timedelta(days=55)
    training_qs = (
        _scoped(
            user,
            BillingAccrual.objects.filter(
                service_date__range=(complete_start, complete_end),
                source_quality=SourceQuality.VERIFIED,
            ).exclude(status=AccrualStatus.VOID),
            owner_id,
            warehouse_id,
        )
        .values("service_date", "currency")
        .annotate(value=Sum("amount"))
    )
    training = defaultdict(list)
    for row in training_qs:
        training[normalize_currency(row["currency"])].append(row)
    forecasts = []
    for currency, points in sorted(series.items()):
        total = sum((row["value"] or ZERO for row in points), ZERO)
        elapsed = (date_to - month_start).days + 1
        history = training.get(currency, [])
        valid_days = len({row["service_date"] for row in history})
        remaining_dates = [
            date_to + datetime.timedelta(days=offset)
            for offset in range(1, (month_end - date_to).days + 1)
        ]
        if valid_days >= 28:
            weekday_values = defaultdict(list)
            for row in history:
                weekday_values[row["service_date"].weekday()].append(
                    row["value"] or ZERO
                )
            remainder = sum(
                (
                    (
                        sum(weekday_values[day.weekday()], ZERO)
                        / len(weekday_values[day.weekday()])
                        if weekday_values[day.weekday()]
                        else ZERO
                    )
                    for day in remaining_dates
                ),
                ZERO,
            )
            forecast = total + remainder
            algorithm = "EIGHT_COMPLETE_WEEKS_WEEKDAY_RUN_RATE"
            sample_days = valid_days
        else:
            forecast = total + (
                total / elapsed * len(remaining_dates) if elapsed else ZERO
            )
            algorithm = "MTD_DAILY_RUN_RATE_FALLBACK"
            sample_days = len(points)
        forecasts.append(
            {
                "currency": currency,
                "algorithm": algorithm,
                "training_range": {
                    "date_from": complete_start,
                    "date_to": complete_end,
                },
                "sample_days": sample_days,
                "actual": total,
                "forecast": forecast,
                "trusted": len(points) >= 1,
            }
        )
    target_qs = OperatingTarget.objects.filter(month=month_start)
    target_qs = _scoped(user, target_qs, owner_id, warehouse_id)
    targets = list(
        target_qs.values(
            "warehouse_id", "owner_id", "metric", "currency", "target_value"
        )
    )
    revenue_targets = defaultdict(Decimal)
    for target in targets:
        if target["metric"] == OperatingTarget.Metric.ACCRUAL_REVENUE:
            revenue_targets[normalize_currency(target["currency"])] += target[
                "target_value"
            ]
    for forecast in forecasts:
        target = revenue_targets.get(forecast["currency"])
        forecast["target"] = target
        forecast["actual_achievement"] = forecast["actual"] / target if target else None
        forecast["forecast_achievement"] = (
            forecast["forecast"] / target if target else None
        )
    capacity_locations = AccessScope.for_user(user).filter_queryset(
        Location.objects.filter(max_volume_m3__gt=0),
        owner_field=None,
        warehouse_field="warehouse_id",
    )
    if warehouse_id:
        capacity_locations = capacity_locations.filter(warehouse_id=warehouse_id)
    capacity = capacity_locations.aggregate(value=Sum("max_volume_m3"))["value"] or ZERO
    capacity_start = date_to - datetime.timedelta(days=13)
    capacity_snapshots = _scoped(
        user,
        InventorySnapshotDaily.objects.filter(
            snapshot_date__range=(capacity_start, date_to),
            snapshot_source=InventorySnapshotDaily.Source.TX_ROLLFORWARD,
        ),
        owner_id,
        warehouse_id,
    )
    volume_expr = ExpressionWrapper(
        F("onhand_qty") * F("unit_volume_m3_snapshot"),
        output_field=DecimalField(max_digits=24, decimal_places=6),
    )
    capacity_points = list(
        capacity_snapshots.values("snapshot_date")
        .annotate(used=Sum(volume_expr))
        .order_by("snapshot_date")
    )
    capacity_forecast = {
        "algorithm": "FOURTEEN_DAY_LINEAR_TREND",
        "sample_days": len(capacity_points),
        "training_range": {"date_from": capacity_start, "date_to": date_to},
        "forecast_utilization": None,
        "slope_per_day": None,
        "trusted": False,
    }
    if capacity and len(capacity_points) >= 7:
        ys = [Decimal(row["used"] or ZERO) / capacity for row in capacity_points]
        xs = [Decimal(index) for index in range(len(ys))]
        x_mean = sum(xs, ZERO) / len(xs)
        y_mean = sum(ys, ZERO) / len(ys)
        denominator = sum(((x - x_mean) ** 2 for x in xs), ZERO)
        slope = (
            sum(((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)), ZERO)
            / denominator
            if denominator
            else ZERO
        )
        projected = ys[-1] + slope * (month_end - date_to).days
        capacity_forecast.update(
            {
                "forecast_utilization": max(ZERO, min(Decimal("1"), projected)),
                "slope_per_day": slope,
                "trusted": True,
            }
        )
    return {
        "scope": scope,
        "meta": build_meta(scope=scope),
        "selected_period_vs_prior": comparisons,
        "comparisons": fixed_comparisons,
        "forecasts": forecasts,
        "capacity_forecast": capacity_forecast,
        "targets": targets,
    }

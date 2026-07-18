"""Transactional refresh of the operational reporting aggregates."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from django.db.models import Count, Sum

from .etl_utils import ensure_datedim
from .models import (
    AggBillingDaily,
    AggOTIFDaily,
    AggThroughputDaily,
    FactBilling,
    FactInboundLine,
    FactOutboundLine,
)


def refresh_daily_aggregates(dates: Iterable[date]) -> int:
    """Rebuild each requested date from facts, including dates that became empty."""

    refreshed = 0
    for current_date in sorted({value for value in dates if value}):
        date_dim = ensure_datedim(current_date)

        AggThroughputDaily.objects.filter(date=date_dim).delete()
        throughput = defaultdict(
            lambda: {
                "inbound_lines": 0,
                "inbound_qty": 0,
                "outbound_lines": 0,
                "outbound_qty": 0,
            }
        )
        for row in (
            FactInboundLine.objects.filter(receive_date=date_dim)
            .values("owner_id", "warehouse_id")
            .annotate(lines=Count("line_id"), qty=Sum("qty_received"))
        ):
            bucket = throughput[(row["owner_id"], row["warehouse_id"])]
            bucket["inbound_lines"] = row["lines"] or 0
            bucket["inbound_qty"] = row["qty"] or 0
        for row in (
            FactOutboundLine.objects.filter(ship_date=date_dim)
            .values("owner_id", "warehouse_id")
            .annotate(lines=Count("line_id"), qty=Sum("qty_shipped"))
        ):
            bucket = throughput[(row["owner_id"], row["warehouse_id"])]
            bucket["outbound_lines"] = row["lines"] or 0
            bucket["outbound_qty"] = row["qty"] or 0
        for (owner_id, warehouse_id), values in throughput.items():
            AggThroughputDaily.objects.update_or_create(
                date=date_dim,
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                defaults=values,
            )

        AggOTIFDaily.objects.filter(date=date_dim).delete()
        order_rows = defaultdict(list)
        for row in FactOutboundLine.objects.filter(
            ship_date=date_dim, customer_id__isnull=False
        ).values("owner_id", "customer_id", "order_id", "on_time", "in_full"):
            order_rows[(row["owner_id"], row["customer_id"], row["order_id"])].append(row)
        otif = defaultdict(lambda: {"orders": 0, "orders_on_time": 0, "orders_in_full": 0})
        for (owner_id, customer_id, _order_id), lines in order_rows.items():
            bucket = otif[(owner_id, customer_id)]
            bucket["orders"] += 1
            in_full = all(line["in_full"] for line in lines)
            on_time = in_full and all(line["on_time"] for line in lines)
            bucket["orders_in_full"] += int(in_full)
            bucket["orders_on_time"] += int(on_time)
        for (owner_id, customer_id), values in otif.items():
            AggOTIFDaily.objects.update_or_create(
                date=date_dim,
                owner_id=owner_id,
                customer_id=customer_id,
                defaults=values,
            )

        AggBillingDaily.objects.filter(date=date_dim).delete()
        for row in (
            FactBilling.objects.filter(date=date_dim)
            .values("owner_id", "warehouse_id", "fee_type")
            .annotate(amount=Sum("amount"))
        ):
            AggBillingDaily.objects.update_or_create(
                date=date_dim,
                owner_id=row["owner_id"],
                warehouse_id=row["warehouse_id"],
                fee_type=row["fee_type"],
                defaults={"amount": row["amount"] or 0},
            )
        refreshed += 1
    return refreshed


def refresh_all_daily_aggregates() -> int:
    """Refresh all current and formerly populated dates after a fact ETL run."""

    dates = set()
    for model, field in (
        (FactInboundLine, "receive_date__date"),
        (FactOutboundLine, "ship_date__date"),
        (FactBilling, "date__date"),
        (AggThroughputDaily, "date__date"),
        (AggOTIFDaily, "date__date"),
        (AggBillingDaily, "date__date"),
    ):
        dates.update(model.objects.exclude(**{f"{field.split('__')[0]}__isnull": True}).values_list(field, flat=True))
    return refresh_daily_aggregates(dates)

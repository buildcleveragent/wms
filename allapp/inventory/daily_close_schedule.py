"""Independent daily-close work queue derived from the active owner/warehouse bindings."""

import logging
from datetime import time

from django.db.models import Min, Q
from django.utils import timezone

from allapp.inventory.daily_close import (
    DAY,
    SHANGHAI,
    _scope_for,
    effective_bindings,
    run_daily_close,
    shanghai_now,
)
from allapp.inventory.models import (
    InventoryDailyClose,
    InventoryOpeningVersion,
    InventoryTransaction,
)

logger = logging.getLogger(__name__)


def _earliest_change(scope, tenant, through):
    # Revisit earlier dates after approved openings, amended/deleted entries, or an undated posting.
    approvals = InventoryOpeningVersion.objects.filter(
        close__owner_id=tenant["owner_id"],
        close__warehouse_id=tenant["warehouse_id"],
        status=InventoryOpeningVersion.Status.APPROVED,
        close__business_date__lt=through,
        approved_at__gte=scope.scanned_at,
    )
    opened = approvals.aggregate(day=Min("close__business_date"))["day"]
    tx = InventoryTransaction.all_objects.filter(**tenant, updated_at__gte=scope.scanned_at).filter(
        Q(posted_at__date__lte=through) | Q(posted_at__isnull=True)
    )
    first_review = scope.start_date
    if InventoryDailyClose.objects.filter(**tenant, business_date=scope.start_date - DAY).exists():
        first_review -= DAY
    if tx.filter(posted_at__isnull=True).exists():
        return first_review
    posted = tx.aggregate(at=Min("posted_at"))["at"]
    if posted and timezone.is_aware(posted):
        posted = posted.astimezone(SHANGHAI)
    candidates = [
        day for day in (opened + DAY if opened else None, posted.date() if posted else None) if day
    ]
    return max(first_review, min(candidates)) if candidates else None


def latest_due_date():
    now = shanghai_now()
    return now.date() - DAY if now.time() >= time(0, 30) else now.date() - 2 * DAY


def process_due_closes():
    """Catch up through the last deadline, preserving each successful day's cursor on retries."""
    through = latest_due_date()
    completed = failed = 0
    bindings = (
        effective_bindings().order_by("owner_id", "warehouse_id").values("owner_id", "warehouse_id")
    )
    for tenant in bindings.iterator():
        try:
            scanned = timezone.now()
            scope = _scope_for(**tenant)
            start = max(
                scope.start_date,
                scope.through_date + DAY if scope.through_date else scope.start_date,
            )
            if scope.scanned_at and scope.through_date:
                changed = _earliest_change(scope, tenant, min(through, scope.through_date))
                if changed:
                    start = min(start, changed)
            # A transaction may commit after the scan with an earlier creation/update
            # timestamp. Rotate older seals through an independent manifest recheck.
            stale = (
                InventoryDailyClose.objects.filter(
                    **tenant,
                    business_date__lt=start,
                    business_date__lte=through,
                    checked_at__lt=scanned - DAY,
                )
                .order_by("checked_at", "pk")
                .values_list("business_date", flat=True)
                .first()
            )
            if stale is not None:
                run_daily_close(**tenant, business_date=stale)
                completed += 1
            day = start
            while day <= through:
                run_daily_close(**tenant, business_date=day)
                # Monotonic progress even with multiple independent workers.
                type(scope).objects.filter(pk=scope.pk).filter(
                    Q(through_date__lt=day) | Q(through_date__isnull=True)
                ).update(through_date=day)
                completed += 1
                day += DAY
            type(scope).objects.filter(pk=scope.pk).filter(
                Q(scanned_at__lt=scanned) | Q(scanned_at__isnull=True)
            ).update(scanned_at=scanned)
        except Exception:
            logger.exception(
                "库存日结范围处理失败 owner=%s warehouse=%s",
                tenant["owner_id"],
                tenant["warehouse_id"],
            )
            failed += 1
    return completed, failed

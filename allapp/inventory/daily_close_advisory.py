"""Inventory reconciliation advisories displayed with an owner/warehouse billing period."""

from datetime import timedelta
from urllib.parse import urlencode

from django.urls import reverse

from .daily_close import binding_start_date, effective_bindings
from .daily_close_schedule import latest_due_date
from .models import InventoryDailyClose


def inventory_close_warnings(owner_id, warehouse_id, start_date, end_date):
    """Include unprocessed dates even when the scheduler has never run for this scope."""
    tenant = dict(owner_id=owner_id, warehouse_id=warehouse_id)
    records = InventoryDailyClose.objects.filter(
        **tenant,
        business_date__range=(start_date, end_date),
    )
    pending = (
        records.filter(
            review_state__in=(
                InventoryDailyClose.ReviewState.WAREHOUSE,
                InventoryDailyClose.ReviewState.FINANCE,
            )
        )
        .select_related("case__assignee")
        .defer("details", "totals")
    )
    warnings = []
    for close in pending:
        assignee = close.case.assignee if close.case_id else None
        reasons = {item["issue"]: item.get("message") or item["issue"] for item in close.issues}
        warnings.append(
            {
                "business_date": close.business_date.isoformat(),
                "review_state": close.review_state,
                "state_label": close.get_review_state_display(),
                "reasons": [
                    {"code": code, "message": message} for code, message in reasons.items()
                ],
                "assignee": assignee.get_username() if assignee else None,
                "detail_url": reverse("console:inventory_daily_close_detail", args=[close.pk]),
            }
        )

    binding = (
        effective_bindings()
        .filter(**tenant)
        .select_related("warehouse__inventoryclosepilot")
        .first()
    )
    if binding:
        next_day = max(start_date, binding_start_date(binding))
        last_due = min(end_date, latest_due_date())
        if next_day <= last_due:
            one_day = timedelta(days=1)
            present = records.filter(business_date__range=(next_day, last_due)).order_by(
                "business_date"
            )
            # Report contiguous gaps in one row even after a long outage.
            for day in [*present.values_list("business_date", flat=True), last_due + one_day]:
                if next_day < day:
                    until = day - one_day
                    query = urlencode(
                        {
                            "owner": owner_id,
                            "warehouse": warehouse_id,
                            "start": next_day,
                            "end": until,
                        }
                    )
                    warnings.append(
                        {
                            "business_date": next_day.isoformat(),
                            "through_date": until.isoformat() if until != next_day else None,
                            "review_state": "NOT_RUN",
                            "state_label": "待补跑",
                            "reasons": [
                                {
                                    "code": "daily_close_missing",
                                    "message": "已到日结时间，尚无日结记录。",
                                }
                            ],
                            "assignee": None,
                            "detail_url": reverse("console:inventory_daily_close_list")
                            + "?"
                            + query,
                        }
                    )
                next_day = day + one_day
    return sorted(warnings, key=lambda row: row["business_date"])

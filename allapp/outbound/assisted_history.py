"""Read models for warehouse-assisted outbound history and statistics.

The assisted API is deliberately independent from the legacy outbound scope.
Every queryset in this module is anchored to the operator's bound warehouse and
to ``WAREHOUSE_ASSISTED`` orders.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    Case,
    BigIntegerField,
    CharField,
    Count,
    DecimalField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from allapp.tasking.models import WmsTask

from .models import OutboundOrder, OutboundOrderLine


BUSINESS_STATUS_LABELS = {
    "READY_TO_PICK": "等待拣货",
    "PICKING": "拣货中",
    "PENDING_REVIEW": "等待复核",
    "POSTING": "等待过账",
    "POSTING_FAILED": "过账失败",
    "NEED_RECOUNT": "需要复盘",
    "COMPLETED": "已完成出库",
    "CANCELLED": "已取消",
    "INCONSISTENT": "数据状态异常",
}

PENDING_STATUSES = {
    "READY_TO_PICK",
    "PICKING",
    "PENDING_REVIEW",
    "POSTING",
}
EXCEPTION_STATUSES = {"POSTING_FAILED", "NEED_RECOUNT", "INCONSISTENT"}
REPRINT_DISABLED_STATUSES = {"CANCELLED", "INCONSISTENT"}


def _integer_param(params, name, *, required=False, minimum=None, maximum=None):
    raw = params.get(name)
    if raw in (None, ""):
        if required:
            raise ValidationError({name: f"{name} 参数必填。"})
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: f"{name} 必须是整数。"})
    if minimum is not None and value < minimum:
        raise ValidationError({name: f"{name} 不能小于 {minimum}。"})
    if maximum is not None and value > maximum:
        raise ValidationError({name: f"{name} 不能大于 {maximum}。"})
    return value


def _date_param(params, name):
    raw = (params.get(name) or "").strip()
    if not raw:
        return None
    value = parse_date(raw)
    if value is None:
        raise ValidationError({name: f"{name} 必须使用 YYYY-MM-DD 格式。"})
    return value


def _aware_bound(value, *, end=False):
    if end:
        value += timedelta(days=1)
    result = datetime.combine(value, time.min)
    if timezone.is_aware(timezone.now()):
        result = timezone.make_aware(result, timezone.get_current_timezone())
    return result


def parse_period(params, *, default_today=False, max_days=None):
    start_date = _date_param(params, "start_date")
    end_date = _date_param(params, "end_date")
    if default_today and start_date is None and end_date is None:
        current = timezone.now()
        today = timezone.localtime(current).date() if timezone.is_aware(current) else current.date()
        start_date = end_date = today
    elif start_date is None and end_date is not None:
        start_date = end_date
    elif end_date is None and start_date is not None:
        end_date = start_date
    if start_date and end_date and end_date < start_date:
        raise ValidationError({"end_date": "end_date 必须大于或等于 start_date。"})
    if (
        max_days is not None
        and start_date
        and end_date
        and (end_date - start_date).days + 1 > max_days
    ):
        raise ValidationError(
            {"end_date": f"单次统计范围不能超过 {max_days} 天。"}
        )
    return start_date, end_date


def _line_total_subqueries():
    lines = OutboundOrderLine.objects.filter(
        order_id=OuterRef("pk"),
        is_deleted=False,
    ).values("order_id")
    line_count = lines.annotate(value=Count("id")).values("value")[:1]
    total_qty = lines.annotate(value=Sum("base_qty")).values("value")[:1]
    return line_count, total_qty


def _task_subquery():
    return (
        WmsTask.objects.filter(
            warehouse_id=OuterRef("warehouse_id"),
            task_type=WmsTask.TaskType.PICK,
            source_model__in=("outboundorder", "OutboundOrder"),
        )
        .annotate(_source_pk_int=Cast("source_pk", output_field=BigIntegerField()))
        .filter(_source_pk_int=OuterRef("pk"))
        .order_by("id")
    )


def assisted_history_queryset(user):
    """Return annotated assisted orders for one operator warehouse."""

    line_count, total_qty = _line_total_subqueries()
    tasks = _task_subquery()
    task_counts = tasks.order_by().values("source_pk").annotate(value=Count("id"))
    quantity_output = DecimalField(max_digits=24, decimal_places=3)

    qs = (
        OutboundOrder.objects.filter(
            warehouse_id=user.warehouse_id,
            processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
        )
        .select_related("owner", "customer", "assisted_by")
        .annotate(
            history_line_count=Coalesce(
                Subquery(line_count, output_field=IntegerField()), Value(0)
            ),
            history_total_qty=Coalesce(
                Subquery(total_qty, output_field=quantity_output),
                Value(Decimal("0.000"), output_field=quantity_output),
            ),
            history_task_count=Coalesce(
                Subquery(task_counts.values("value")[:1], output_field=IntegerField()),
                Value(0),
            ),
            history_task_id=Subquery(tasks.values("id")[:1]),
            history_task_no=Subquery(tasks.values("task_no")[:1]),
            history_task_owner_id=Subquery(tasks.values("owner_id")[:1]),
            history_task_status=Subquery(tasks.values("status")[:1]),
            history_review_status=Subquery(tasks.values("review_status")[:1]),
            history_posting_status=Subquery(tasks.values("posting_status")[:1]),
            history_posted_at=Subquery(tasks.values("posted_at")[:1]),
        )
    )

    valid_task = Q(
        history_task_count=1,
        history_task_owner_id=F("owner_id"),
    )
    qs = qs.annotate(
        business_status=Case(
            When(
                Q(approval_status="CANCELLED")
                | Q(history_task_status=WmsTask.Status.CANCELLED),
                then=Value("CANCELLED"),
            ),
            When(~valid_task, then=Value("INCONSISTENT")),
            When(
                valid_task
                & Q(is_closed=True)
                & Q(history_task_status=WmsTask.Status.COMPLETED)
                & Q(history_review_status=WmsTask.ReviewStatus.APPROVED)
                & Q(history_posting_status=WmsTask.PostingStatus.POSTED),
                then=Value("COMPLETED"),
            ),
            When(
                valid_task
                & Q(history_posting_status=WmsTask.PostingStatus.FAILED),
                then=Value("POSTING_FAILED"),
            ),
            When(
                valid_task
                & (
                    Q(history_review_status=WmsTask.ReviewStatus.NEED_RECOUNT)
                    | Q(history_posting_status=WmsTask.PostingStatus.NEED_RECOUNT)
                ),
                then=Value("NEED_RECOUNT"),
            ),
            When(
                valid_task
                & Q(history_task_status=WmsTask.Status.COMPLETED)
                & Q(history_review_status=WmsTask.ReviewStatus.APPROVED)
                & Q(history_posting_status=WmsTask.PostingStatus.PENDING)
                & Q(is_closed=False),
                then=Value("POSTING"),
            ),
            When(
                valid_task
                & Q(history_task_status=WmsTask.Status.COMPLETED)
                & Q(history_review_status=WmsTask.ReviewStatus.PENDING)
                & Q(history_posting_status=WmsTask.PostingStatus.NOT_READY)
                & Q(is_closed=False),
                then=Value("PENDING_REVIEW"),
            ),
            When(
                valid_task
                & Q(history_task_status=WmsTask.Status.IN_PROGRESS)
                & Q(is_closed=False),
                then=Value("PICKING"),
            ),
            When(
                valid_task
                & Q(history_task_status=WmsTask.Status.RELEASED)
                & Q(is_closed=False),
                then=Value("READY_TO_PICK"),
            ),
            default=Value("INCONSISTENT"),
            output_field=CharField(),
        )
    )
    return qs


def filter_history_queryset(qs, params, *, include_status=True):
    start_date, end_date = parse_period(params)
    if start_date:
        qs = qs.filter(assisted_at__gte=_aware_bound(start_date))
    if end_date:
        qs = qs.filter(assisted_at__lt=_aware_bound(end_date, end=True))

    owner_id = _integer_param(params, "owner_id", minimum=1)
    operator_id = _integer_param(params, "operator_id", minimum=1)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if operator_id:
        qs = qs.filter(assisted_by_id=operator_id)

    search = (params.get("search") or "").strip()
    if search:
        product_match = OutboundOrderLine.objects.filter(
            order_id=OuterRef("pk"), is_deleted=False
        ).filter(
            Q(product__name__icontains=search)
            | Q(product__code__icontains=search)
            | Q(product__sku__icontains=search)
            | Q(product__gtin__icontains=search)
            | Q(product__unit_barcode__icontains=search)
            | Q(product__carton_barcode__icontains=search)
            | Q(product__product_package__barcode__icontains=search)
        )
        qs = qs.annotate(_history_product_match=Exists(product_match)).filter(
            Q(order_no__icontains=search)
            | Q(src_bill_no__icontains=search)
            | Q(owner__name__icontains=search)
            | Q(owner__code__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(customer__code__icontains=search)
            | Q(contact__icontains=search)
            | Q(contact_phone__icontains=search)
            | Q(_history_product_match=True)
        )

    if include_status:
        status_value = (params.get("status") or "").strip().upper()
        if status_value:
            if status_value not in BUSINESS_STATUS_LABELS:
                raise ValidationError({"status": "不支持的代办出库状态。"})
            qs = qs.filter(business_status=status_value)
    return qs


def _display_name(user):
    if not user:
        return ""
    return getattr(user, "name", None) or getattr(user, "username", None) or ""


def _iso_datetime(value):
    if not value:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.isoformat()


def serialize_history_order(order):
    status_value = order.business_status
    valid_task = bool(
        order.history_task_count == 1
        and order.history_task_id
        and order.history_task_owner_id == order.owner_id
    )
    can_reprint = valid_task and status_value not in REPRINT_DISABLED_STATUSES
    if can_reprint:
        unavailable_reason = ""
    elif status_value == "CANCELLED":
        unavailable_reason = "已取消的出库单不能重新打印。"
    elif order.history_task_count == 0:
        unavailable_reason = "未找到关联拣货任务。"
    elif order.history_task_count > 1:
        unavailable_reason = "关联了多个拣货任务，请联系管理员。"
    else:
        unavailable_reason = "任务归属或状态异常，请联系管理员。"

    customer_name = getattr(order.customer, "name", "") if order.customer_id else ""
    receiver_name = (order.contact or "").strip() or customer_name
    task_id = order.history_task_id if valid_task else None
    return {
        "id": order.id,
        "order_no": order.order_no,
        "src_bill_no": order.src_bill_no or "",
        "assisted_at": _iso_datetime(order.assisted_at),
        "owner": {
            "id": order.owner_id,
            "code": order.owner.code,
            "name": order.owner.name,
        },
        "customer": {
            "id": order.customer_id,
            "code": getattr(order.customer, "code", "") or "",
            "name": customer_name,
        }
        if order.customer_id
        else None,
        "receiver_name": receiver_name,
        "contact_phone": order.contact_phone or "",
        "assisted_by": {
            "id": order.assisted_by_id,
            "name": _display_name(order.assisted_by),
        }
        if order.assisted_by_id
        else None,
        "line_count": int(order.history_line_count or 0),
        "total_base_qty": str(order.history_total_qty or Decimal("0")),
        "submit_status": order.submit_status,
        "approval_status": order.approval_status,
        "is_closed": order.is_closed,
        "task": {
            "id": task_id,
            "task_no": order.history_task_no or "",
            "status": order.history_task_status or "",
            "review_status": order.history_review_status or "",
            "posting_status": order.history_posting_status or "",
            "posted_at": _iso_datetime(order.history_posted_at),
        }
        if task_id
        else None,
        "business_status": status_value,
        "business_status_label": BUSINESS_STATUS_LABELS[status_value],
        "can_reprint": can_reprint,
        "reprint_unavailable_reason": unavailable_reason,
    }


def history_options(user):
    base = OutboundOrder.objects.filter(
        warehouse_id=user.warehouse_id,
        processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
    )
    owners = list(
        base.values("owner_id", "owner__code", "owner__name")
        .distinct()
        .order_by("owner__name", "owner_id")
    )
    operators = list(
        base.exclude(assisted_by_id__isnull=True)
        .values("assisted_by_id", "assisted_by__name", "assisted_by__username")
        .distinct()
        .order_by("assisted_by__username", "assisted_by_id")
    )
    return {
        "owners": [
            {"id": row["owner_id"], "code": row["owner__code"], "name": row["owner__name"]}
            for row in owners
        ],
        "operators": [
            {
                "id": row["assisted_by_id"],
                "name": row["assisted_by__name"] or row["assisted_by__username"] or "",
                "username": row["assisted_by__username"] or "",
            }
            for row in operators
        ],
        "statuses": [
            {"value": value, "label": label}
            for value, label in BUSINESS_STATUS_LABELS.items()
        ],
    }


def _new_stats_bucket(**identity):
    return {
        **identity,
        "order_count": 0,
        "completed_count": 0,
        "pending_count": 0,
        "exception_count": 0,
        "cancelled_count": 0,
        "line_count": 0,
        "total_base_qty": Decimal("0"),
    }


def _accumulate(bucket, row):
    status_value = row["business_status"]
    bucket["order_count"] += 1
    bucket["line_count"] += int(row["history_line_count"] or 0)
    bucket["total_base_qty"] += row["history_total_qty"] or Decimal("0")
    if status_value == "COMPLETED":
        bucket["completed_count"] += 1
    elif status_value == "CANCELLED":
        bucket["cancelled_count"] += 1
    elif status_value in EXCEPTION_STATUSES:
        bucket["exception_count"] += 1
    elif status_value in PENDING_STATUSES:
        bucket["pending_count"] += 1


def _json_bucket(bucket):
    return {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in bucket.items()
    }


def build_stats(user, params):
    start_date, end_date = parse_period(params, default_today=True, max_days=366)
    top_n = _integer_param(params, "top_n", minimum=1, maximum=50) or 10
    qs = assisted_history_queryset(user)
    # ``parse_period`` supplies today's range when the caller omits dates.  Feed
    # that normalized range back into the common filter; otherwise an empty
    # query string would accidentally aggregate the warehouse's full history.
    filter_params = params.copy()
    filter_params["start_date"] = start_date.isoformat()
    filter_params["end_date"] = end_date.isoformat()
    qs = filter_history_queryset(qs, filter_params, include_status=False)
    rows = list(
        qs.values(
            "id",
            "assisted_at",
            "owner_id",
            "owner__code",
            "owner__name",
            "assisted_by_id",
            "assisted_by__name",
            "assisted_by__username",
            "history_line_count",
            "history_total_qty",
            "business_status",
        )
    )

    summary = _new_stats_bucket()
    status_counts = defaultdict(int)
    daily = {}
    owners = {}
    operators = {}
    order_ids = []
    for row in rows:
        order_ids.append(row["id"])
        _accumulate(summary, row)
        status_counts[row["business_status"]] += 1

        assisted_at = row["assisted_at"]
        if assisted_at:
            if timezone.is_aware(assisted_at):
                assisted_at = timezone.localtime(assisted_at)
            date_key = assisted_at.date().isoformat()
            daily.setdefault(date_key, _new_stats_bucket(date=date_key))
            _accumulate(daily[date_key], row)

        owner_key = row["owner_id"]
        owners.setdefault(
            owner_key,
            _new_stats_bucket(
                owner_id=owner_key,
                owner_code=row["owner__code"] or "",
                owner_name=row["owner__name"] or "",
            ),
        )
        _accumulate(owners[owner_key], row)

        operator_key = row["assisted_by_id"]
        operators.setdefault(
            operator_key,
            _new_stats_bucket(
                operator_id=operator_key,
                operator_name=row["assisted_by__name"]
                or row["assisted_by__username"]
                or "未记录",
                operator_username=row["assisted_by__username"] or "",
            ),
        )
        _accumulate(operators[operator_key], row)

    products = []
    if order_ids:
        products = list(
            OutboundOrderLine.objects.filter(
                order_id__in=order_ids, is_deleted=False
            )
            .values(
                "product_id",
                "product__code",
                "product__sku",
                "product__name",
                "base_uom__code",
                "base_uom__name",
            )
            .annotate(
                order_count=Count("order_id", distinct=True),
                total_base_qty=Coalesce(
                    Sum("base_qty"),
                    Value(
                        Decimal("0.000"),
                        output_field=DecimalField(max_digits=24, decimal_places=3),
                    ),
                ),
            )
            .order_by("-total_base_qty", "product_id")[:top_n]
        )

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "summary": _json_bucket(summary),
        "status_rows": [
            {
                "status": value,
                "label": label,
                "order_count": status_counts[value],
            }
            for value, label in BUSINESS_STATUS_LABELS.items()
        ],
        "daily_rows": [
            _json_bucket(daily[key]) for key in sorted(daily.keys())
        ],
        "owner_rows": [
            _json_bucket(row)
            for row in sorted(
                owners.values(), key=lambda item: (-item["order_count"], item["owner_id"])
            )
        ],
        "operator_rows": [
            _json_bucket(row)
            for row in sorted(
                operators.values(),
                key=lambda item: (-item["order_count"], item["operator_id"] or 0),
            )
        ],
        "product_rows": [
            {
                "product_id": row["product_id"],
                "product_code": row["product__code"] or "",
                "product_sku": row["product__sku"] or "",
                "product_name": row["product__name"] or "",
                "base_uom_code": row["base_uom__code"] or "",
                "base_uom_name": row["base_uom__name"] or row["base_uom__code"] or "",
                "order_count": row["order_count"],
                "total_base_qty": str(row["total_base_qty"] or Decimal("0")),
            }
            for row in products
        ],
    }

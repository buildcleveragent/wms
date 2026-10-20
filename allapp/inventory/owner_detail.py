"""Read-only, tenant-scoped inventory facts for the owner portal."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.db.models import Max, Q, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from allapp.inbound.models import InboundOrder
from allapp.outbound.authz import strict_order_queryset
from allapp.outbound.models import OutboundOrder
from allapp.outbound.transfer_models import AssistedOwnershipTransfer
from allapp.tasking.models import WmsTask

from .models import InventoryDetail, InventoryTransaction

BUSINESS_TZ = ZoneInfo("Asia/Shanghai")
QTY_FIELDS = ("onhand_qty", "available_qty", "allocated_qty", "locked_qty", "damaged_qty")
LABELS = {
    "RECEIVE": "入库",
    "ISSUE": "出库（库存扣减）",
    "ADJ_GAIN": "调整增加",
    "ADJ_LOSS": "调整减少",
    "MOVE_IN": "移入",
    "MOVE_OUT": "移出",
    "OWNER_XFER_IN": "货权转入",
    "OWNER_XFER_OUT": "货权转出",
}
UNKNOWN = {"status": "unknown", "label": "关联单据待核对"}
RESTRICTED = {"status": "restricted", "label": "关联单据受权限限制"}


class InventoryFilters(serializers.Serializer):
    warehouse_id = serializers.IntegerField(min_value=1, required=False)
    batch = serializers.CharField(max_length=64, required=False, allow_blank=True)
    expiry = serializers.ChoiceField(choices=("expired", "valid", "unknown"), required=False)
    tx_type = serializers.ChoiceField(choices=tuple(LABELS), required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=50)
    cursor = serializers.CharField(max_length=2000, required=False)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to"):
            if attrs["date_from"] > attrs["date_to"]:
                raise serializers.ValidationError("开始日期不能晚于结束日期")
        return attrs


def iso(value):
    if value is None:
        return None
    if timezone.is_naive(value):
        value = value.replace(tzinfo=BUSINESS_TZ)
    return value.astimezone(BUSINESS_TZ).isoformat()


def _base(model, summary):
    return model.objects.filter(
        owner_id=summary.owner_id, product_id=summary.product_id, is_active=True
    )


def _filtered(qs, filters):
    if filters.get("warehouse_id"):
        qs = qs.filter(warehouse_id=filters["warehouse_id"])
    if filters.get("batch"):
        qs = qs.filter(batch_no__icontains=filters["batch"])
    today = timezone.now()
    today = (
        (today.replace(tzinfo=BUSINESS_TZ) if timezone.is_naive(today) else today)
        .astimezone(BUSINESS_TZ)
        .date()
    )
    if filters.get("expiry") == "expired":
        qs = qs.filter(expiry_date__lt=today)
    elif filters.get("expiry") == "valid":
        qs = qs.filter(expiry_date__gte=today)
    elif filters.get("expiry") == "unknown":
        qs = qs.filter(expiry_date__isnull=True)
    return qs


def _quantities(row):
    return {field: format(row[field] or Decimal(0), "f") for field in QTY_FIELDS}


def inventory_groups(qs):
    """Shared batch grouping, before pagination, for display and export."""
    sums = {field: Sum(field) for field in QTY_FIELDS}
    dimensions = (
        "warehouse_id",
        "warehouse__name",
        "batch_no",
        "production_date",
        "expiry_date",
        "base_unit",
    )
    groups = (
        qs.values(*dimensions)
        .order_by("warehouse_id", "batch_no", "production_date", "expiry_date", "base_unit")
        .annotate(**sums)
        .exclude(**{field: 0 for field in QTY_FIELDS})
    )
    return groups


def build_inventory_detail(summary, filters):
    """Aggregate only current facts; never refresh/rebuild stock snapshots here."""
    stocks = _base(InventoryDetail, summary)
    ledger = _base(InventoryTransaction, summary)
    warehouses = dict(stocks.values_list("warehouse_id", "warehouse__name").distinct())
    warehouses.update(dict(ledger.values_list("warehouse_id", "warehouse__name").distinct()))
    qs = _filtered(stocks, filters)
    sums = {field: Sum(field) for field in QTY_FIELDS}
    totals = [
        {"base_unit": row["base_unit"], **_quantities(row)}
        for row in qs.values("base_unit").order_by("base_unit").annotate(**sums)
    ]
    groups = inventory_groups(qs)
    count = groups.count()
    start = (filters["page"] - 1) * filters["page_size"]
    rows = []
    for row in groups[start : start + filters["page_size"]]:
        rows.append(
            {
                **{key: value for key, value in row.items() if key not in QTY_FIELDS},
                "warehouse_name": row["warehouse__name"],
                **_quantities(row),
            }
        )
        rows[-1].pop("warehouse__name")
    product = summary.product
    return {
        "summary_id": summary.pk,
        "as_of": iso(timezone.now()),
        "product": {
            "id": product.pk,
            "name": product.name,
            "code": product.code,
            "spec": product.spec,
            "base_unit": summary.base_unit,
        },
        "warehouses": [{"id": key, "name": warehouses[key]} for key in sorted(warehouses)],
        "totals": totals,
        "unit_mismatch": any(t["base_unit"] != summary.base_unit for t in totals),
        "count": count,
        "results": rows,
        "next_page": filters["page"] + 1 if start + len(rows) < count else None,
    }


def _source(model, pk, app=""):
    parts = (model or "").lower().split(".")
    expected = {"wmstask": "tasking", "outboundorder": "outbound", "inboundorder": "inbound"}
    name = parts[-1]
    namespace = parts[-2] if len(parts) == 2 else (app or "").lower()
    if (
        len(parts) > 2
        or name not in expected
        or (namespace and namespace != expected[name])
        or (app and app.lower() != expected[name])
    ):
        return None
    try:
        number = int(pk)
        return (name, number) if number > 0 else None
    except (TypeError, ValueError):
        return None


def _document(kind, number, order_id=None):
    result = {"status": "available", "type": kind, "number": number, "label": f"{kind} {number}"}
    if order_id:
        result["order_id"] = order_id
    return result


def _documents(rows, summary, user):
    """Resolve a page in batches, validating every graph edge before traversing it."""
    tasks = {}
    pending = {
        (s[1], row.warehouse_id)
        for row in rows
        if (s := _source(row.src_model, row.src_id)) and s[0] == "wmstask"
    }
    for _ in range(32):
        pending -= tasks.keys()
        if not pending:
            break
        targets = Q(pk__in=[])
        for task_id, warehouse_id in pending:
            targets |= Q(pk=task_id, warehouse_id=warehouse_id)
        found = {
            (t.pk, t.warehouse_id): t
            for t in WmsTask.objects.filter(targets, owner_id=summary.owner_id)
        }
        tasks.update({pk: found.get(pk) for pk in pending})
        pending = set()
        for task in found.values():
            source = _source(task.source_model, task.source_pk, task.source_app)
            if source and source[0] == "wmstask":
                pending.add((source[1], task.warehouse_id))

    roots = {}
    for row in rows:
        source = _source(row.src_model, row.src_id)
        seen = set()
        while source and source[0] == "wmstask":
            if source in seen:
                source = None
                break
            seen.add(source)
            task = tasks.get((source[1], row.warehouse_id))
            if not task or task.warehouse_id != row.warehouse_id:
                source = None
                break
            parent = _source(task.source_model, task.source_pk, task.source_app)
            if (
                not parent
                and not task.source_model
                and task.task_type
                in {
                    "ADJUST",
                    "COUNT",
                    "RELOC",
                    "REPLEN",
                    "RECEIVE",
                }
            ):
                break
            source = parent
        roots[row.pk] = source

    out_ids = {s[1] for s in roots.values() if s and s[0] == "outboundorder"}
    inbound_ids = {s[1] for s in roots.values() if s and s[0] == "inboundorder"}
    orders = {
        o.pk: o
        for o in strict_order_queryset(
            OutboundOrder.objects.filter(pk__in=out_ids, owner_id=summary.owner_id), user
        )
    }
    inbound = {
        o.pk: o for o in InboundOrder.objects.filter(pk__in=inbound_ids, owner_id=summary.owner_id)
    }
    # A transfer ledger entry can refer to the other owner's task. Resolve only the
    # scoped transfer number, never that task's sales order/customer.
    transfer_numbers = {r.src_no for r in rows if r.tx_type.startswith("OWNER_XFER_") and r.src_no}
    transfers = {
        (t.transfer_no, t.warehouse_id): t
        for t in AssistedOwnershipTransfer.objects.filter(
            Q(source_owner_id=summary.owner_id) | Q(target_owner_id=summary.owner_id),
            transfer_no__in=transfer_numbers,
        )
    }
    # Customer-return receipts are explicit links, independently authorized by original order.
    from allapp.inbound.customer_return_models import CustomerReturnReceiptLine

    return_ids = [r.src_id for r in rows if r.src_model == "inbound.CustomerReturnReceiptLine"]
    return_lines = {
        r.pk: r
        for r in CustomerReturnReceiptLine.objects.filter(
            pk__in=return_ids,
            line__order__owner_id=summary.owner_id,
            line__product_id=summary.product_id,
        ).select_related("line__order__original_order")
    }
    visible_return_orders = set(
        strict_order_queryset(
            OutboundOrder.objects.filter(
                pk__in=[r.line.order.original_order_id for r in return_lines.values()]
            ),
            user,
        ).values_list("pk", flat=True)
    )
    results = {}
    for row in rows:
        doc = UNKNOWN
        reason = "原因未记录"
        source = roots[row.pk]
        if row.src_model == "inbound.CustomerReturnReceiptLine":
            receipt_line = return_lines.get(row.src_id)
            if (
                receipt_line
                and receipt_line.line.order.warehouse_id == row.warehouse_id
                and receipt_line.line_id == row.src_line_id
            ):
                if receipt_line.line.order.original_order_id in visible_return_orders:
                    doc = _document("销售退货单", receipt_line.line.order.number)
                    doc["return_id"] = receipt_line.line.order_id
                    doc["original_order_id"] = receipt_line.line.order.original_order_id
                    reason = receipt_line.reason or receipt_line.line.reason or "原因未记录"
                else:
                    doc = RESTRICTED
        elif row.tx_type.startswith("OWNER_XFER_"):

            transfer = transfers.get((row.src_no, row.warehouse_id))
            side = "source_owner_id" if row.tx_type == "OWNER_XFER_OUT" else "target_owner_id"
            if transfer and getattr(transfer, side) == summary.owner_id:
                doc = _document("货权转移单", transfer.transfer_no)
        elif source and source[0] == "outboundorder":
            order = orders.get(source[1])
            doc = (
                _document("出库单", order.order_no, order.pk)
                if order and order.warehouse_id == row.warehouse_id
                else RESTRICTED
            )
        elif source and source[0] == "inboundorder":
            order = inbound.get(source[1])
            if order and order.warehouse_id == row.warehouse_id:
                doc = _document("入库单", order.order_no)
        elif source and source[0] == "wmstask":
            task = tasks[(source[1], row.warehouse_id)]
            doc = _document(
                {"ADJUST": "调整单", "COUNT": "盘点单", "RECEIVE": "收货单"}.get(
                    task.task_type, "库存移动单"
                ),
                task.task_no,
            )
            # Only the dedicated adjustment explanation is public; generic task
            # memos may contain customer details or technical errors.
            note = (task.remark or "").strip()
            if (
                task.task_type in {"ADJUST", "COUNT"}
                and note
                and not any(
                    marker in note.lower()
                    for marker in ("admin_", "traceback", "exception", "sql", "error", "password")
                )
            ):
                reason = note
        results[row.pk] = (doc, reason)
    return results


def build_inventory_transactions(summary, filters, user):
    """Stable ID cursor over recorded stock movements, without derived balances."""
    qs = _filtered(_base(InventoryTransaction, summary), filters)
    kind = filters.get("tx_type")
    if kind in {"MOVE_IN", "MOVE_OUT"}:
        legacy = "RECEIVE" if kind == "MOVE_IN" else "ISSUE"
        qs = qs.filter(Q(tx_type=kind) | Q(tx_type=legacy, pair_id__isnull=False))
    elif kind in {"RECEIVE", "ISSUE"}:
        qs = qs.filter(tx_type=kind, pair_id__isnull=True)
    elif kind:
        qs = qs.filter(tx_type=kind)
    for key, lookup, days in (("date_from", "posted_at__gte", 0), ("date_to", "posted_at__lt", 1)):
        if filters.get(key):
            boundary = datetime.combine(filters[key] + timedelta(days=days), time.min)
            if settings.USE_TZ:
                boundary = boundary.replace(tzinfo=BUSINESS_TZ)
            qs = qs.filter(**{lookup: boundary})
    signature = {key: str(value) for key, value in filters.items() if key not in {"cursor", "page"}}
    signature["summary_id"] = summary.pk
    ceiling = qs.aggregate(value=Max("id"))["value"] or 0
    before = ceiling + 1
    if filters.get("cursor"):
        try:
            cursor = signing.loads(filters["cursor"], salt="owner-inventory-ledger", max_age=86400)
            if cursor["filters"] != signature:
                raise ValueError
            ceiling, before = int(cursor["ceiling"]), int(cursor["before"])
        except (signing.BadSignature, KeyError, ValueError, TypeError):
            raise ValidationError({"cursor": "分页已失效，请刷新后重试"}) from None
    rows = list(
        qs.filter(id__lte=ceiling, id__lt=before)
        .select_related("warehouse")
        .order_by("-id")[: filters["page_size"] + 1]
    )
    more = len(rows) > filters["page_size"]
    rows = rows[: filters["page_size"]]
    documents = _documents(rows, summary, user)
    pairs = defaultdict(list)
    pair_ids = {
        row.pair_id for row in rows if row.pair_id and not row.tx_type.startswith("OWNER_XFER_")
    }
    for row in _base(InventoryTransaction, summary).filter(pair_id__in=pair_ids):
        pairs[row.pair_id].append(row)
    results = []
    for row in rows:
        pair = pairs[row.pair_id]
        balanced = (
            len(pair) == 2
            and sum(r.qty_delta for r in pair) == 0
            and all(
                getattr(pair[0], key) == getattr(pair[1], key)
                for key in (
                    "warehouse_id",
                    "batch_no",
                    "production_date",
                    "expiry_date",
                    "base_unit",
                )
            )
            and {r.tx_type for r in pair} in ({"RECEIVE", "ISSUE"}, {"MOVE_IN", "MOVE_OUT"})
        )
        label = LABELS.get(row.tx_type, "库存变动")
        if row.src_model == "inbound.CustomerReturnReceiptLine" and row.tx_type == "RECEIVE":
            label = "销售退货入库"
        if row.pair_id and row.tx_type in {"RECEIVE", "ISSUE"}:
            label = "移入" if row.qty_delta > 0 else "移出"
        doc, reason = documents[row.pk]
        results.append(
            {
                "id": row.pk,
                "tx_type": row.tx_type,
                "type_label": label,
                "qty_delta": format(row.qty_delta, "f"),
                "base_unit": row.base_unit,
                "unit_mismatch": row.base_unit != summary.base_unit,
                "warehouse_id": row.warehouse_id,
                "warehouse_name": row.warehouse.name,
                "batch_no": row.batch_no,
                "production_date": row.production_date,
                "expiry_date": row.expiry_date,
                "posted_at": iso(row.posted_at),
                "document": doc,
                "reason": reason,
                "movement_note": "同仓库存移动，仓库总量不变" if balanced else "",
            }
        )
    next_cursor = (
        signing.dumps(
            {"filters": signature, "ceiling": ceiling, "before": rows[-1].pk},
            salt="owner-inventory-ledger",
            compress=True,
        )
        if more
        else None
    )
    return {
        "summary_id": summary.pk,
        "as_of": iso(timezone.now()),
        "results": results,
        "next_cursor": next_cursor,
    }

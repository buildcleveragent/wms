"""Verify independently evidenced opening quantities at their business-day cutoff."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Sum

from allapp.locations.models import Location
from allapp.products.models import Product
from allapp.tasking.count_inventory import SNAPSHOT_KEY, count_quantity
from allapp.tasking.models import CountLineExtra, TaskScanLog, WmsTask

from .daily_close import HELD, opening_candidate
from .models import InventoryTransaction
from .snapshot_audit import DIMENSIONS


def _dimension(row):
    if not isinstance(row, dict) or not all(field in row for field in DIMENSIONS):
        raise ValidationError("每行必须完整标明商品、库位、批次、生产日期、效期及序列号。")
    key = []
    for field in DIMENSIONS:
        value = row[field]
        if field in ("location_id", "product_id"):
            if type(value) is not int or value < 1:
                raise ValidationError("商品和库位需要有效的数字主键。")
        elif field in ("production_date", "expiry_date"):
            if isinstance(value, date):
                value = value.isoformat()
            if value is not None and not isinstance(value, str):
                raise ValidationError("生产日期或效期格式无效。")
        elif not isinstance(value, str):
            raise ValidationError("批次及序列号需要文本值。")
        else:
            value = value.strip().upper()
        key.append(value)
    return tuple(key)


def _count_matches(task, close, onhand):
    # Recounts cover the selected differences only. A root all-stock count, without
    # a subwarehouse or other filter, provides an independently measured baseline.
    extra = task.counttaskextra
    filters = (
        "subwarehouse_id",
        "zone_type",
        "location_id",
        "location_prefix",
        "product_id",
        "batch_no",
    )
    if any(extra.scope_payload.get(f) not in (None, "") for f in filters):
        return False
    totals = defaultdict(Decimal)
    records = CountLineExtra.objects.filter(line__task=task).values(
        "line__plan_meta", "qty_counted", "count_status"
    )
    lines = task.lines.count()
    if not lines or records.count() != lines:
        return False
    # post_task stamps every accepted scan and its POSTED journal, including
    # zero-difference counts that produce no InventoryTransaction rows.
    marks = list(
        task.scan_logs.filter(
            status=TaskScanLog.ScanStatus.OK,
            owner_id=close.owner_id,
            warehouse_id=close.warehouse_id,
            posting_journal__src_model="WmsTask",
            posting_journal__src_id=task.pk,
            posting_journal__status="POSTED",
            posting_journal__tx_type="POST",
        )
        .exclude(review_status=TaskScanLog.ReviewStatus.REJECTED)
        .order_by()
        .values("posted_at")
        .annotate(lines=Count("task_line_id", distinct=True), scans=Count("pk"))[:2]
    )
    if marks and (len(marks) != 1 or marks[0]["lines"] != lines or marks[0]["scans"] != lines):
        return False
    posted_at = marks[0]["posted_at"] if marks else task.posted_at
    if not posted_at or posted_at.date() > close.business_date:
        return False
    for record in records.iterator():
        row = (record["line__plan_meta"] or {}).get(SNAPSHOT_KEY)
        if not row or record["count_status"] != "COUNTED":
            return False
        if row.get("owner_id") != close.owner_id or row.get("warehouse_id") != close.warehouse_id:
            return False
        totals[_dimension(row)] += record["qty_counted"]
    after_count = InventoryTransaction.objects.filter(
        owner_id=close.owner_id,
        warehouse_id=close.warehouse_id,
        posted_at__gte=posted_at,
        posted_at__date__lte=close.business_date,
    ).exclude(src_model="WmsTask", src_id=task.pk)
    # Independent postings with the same timestamp cannot be ordered against the count.
    if after_count.filter(posted_at=posted_at).exists():
        return False
    for movement in after_count.order_by().values(*DIMENSIONS).annotate(delta=Sum("qty_delta")):
        totals[_dimension(movement)] += movement["delta"]
    return {key: qty for key, qty in totals.items() if qty} == onhand


def checked_opening_rows(close, rows, refs, *, has_files):
    if not isinstance(rows, list):
        raise ValidationError("期初凭据需逐维度填写，确认空仓时提交空明细。")
    try:
        expected_rows = opening_candidate(close, allow_unknown_holds=True)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    inputs = {_dimension(row): row for row in rows}
    expected = {_dimension(row): row for row in expected_rows}
    if inputs.keys() != expected.keys() or len(inputs) != len(rows):
        raise ValidationError("期初明细重复、遗漏，或与日终倒推维度不一致。")
    products = {row["product_id"] for row in rows}
    locations = {row["location_id"] for row in rows}
    if Product.all_objects.filter(owner_id=close.owner_id, pk__in=products).count() != len(
        products
    ) or Location.all_objects.filter(
        warehouse_id=close.warehouse_id, pk__in=locations
    ).count() != len(
        locations
    ):
        raise ValidationError("期初明细含其他仓库或货主的商品或库位。")
    archive = has_files and any(ref["type"] == "external" for ref in refs)
    confirmed = []
    onhand = {}
    for key, candidate in expected.items():
        row = inputs[key]
        confirmed_row = candidate.copy()
        for field in ("onhand_qty", *HELD):
            qty = count_quantity(row.get(field), field=field)
            target = candidate[field]
            if target is None and not archive:
                raise ValidationError("历史占用余额缺失，请提供签署的日终占用明细原件与归档号。")
            if target is not None and qty != Decimal(str(target)):
                raise ValidationError("提交数量与日终独立核查不一致，请按原业务办理调整后复核。")
            confirmed_row[field] = str(qty)
        onhand[key] = Decimal(confirmed_row["onhand_qty"])
        available = onhand[key] - sum(Decimal(confirmed_row[f]) for f in HELD)
        if available < 0:
            raise ValidationError("分配、锁定和破损量之和超过日终实存。")
        confirmed_row["available_qty"] = str(available)
        confirmed.append(confirmed_row)
    if not archive:
        count_ids = [ref["id"] for ref in refs if ref["type"] == "count"]
        counts = WmsTask.objects.filter(
            pk__in=count_ids,
            owner_id=close.owner_id,
            warehouse_id=close.warehouse_id,
            status="COMPLETED",
            review_status="APPROVED",
            posting_status="POSTED",
            counttaskextra__scope="ALL",
            counttaskextra__parent_task__isnull=True,
        ).select_related("counttaskextra")
        if not any(_count_matches(task, close, onhand) for task in counts):
            raise ValidationError(
                "需能核对到日终的已过账全范围盘点单，或签署的完整结存原件及归档号。"
            )
    return confirmed

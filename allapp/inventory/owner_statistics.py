"""Owner-scoped calendar statistics with decimal money and Chinese units."""

import calendar
import re
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, localcontext

from django.conf import settings
from django.core import signing
from django.db import connection
from django.db.models import Max, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from allapp.core.owner_exports import MAX_EXPORT_ROWS, ExportTooLarge, xlsx_download
from allapp.products.models import ProductUom
from allapp.tasking.models import WmsTask

from .models import InventoryDetail, InventoryTransaction
from .owner_history import (
    CATEGORIES,
    HistoryFilters,
    OwnerHistory,
    serialize_row,
    stamp,
)
from .owner_permissions import owner_inventory_scope
from .owner_statistics_sql import statistics_sql

SNAPSHOT_SALT = "inventory.owner-statistics.v1"
AMOUNT_LABELS = {"PRICED": "已计价", "PARTIAL": "部分金额待核对", "UNPRICED": "金额待核对"}


def statistics_scope(user, *, exporting=False):
    """Use existing owner document roles; salesperson scopes remain personal."""
    scope = owner_inventory_scope(user)
    if not user.has_perm("reports.view_owner_operations") or (
        exporting and not user.has_perm("reports.export_operations")
    ):
        raise PermissionDenied("当前账号没有出入库统计访问权限。")
    return scope


class StatisticsFilters(HistoryFilters):
    base_unit = serializers.CharField(
        max_length=ProductUom._meta.get_field("code").max_length, required=False
    )
    missing_unit = serializers.BooleanField(required=False)
    currency = serializers.CharField(max_length=16, required=False)
    grain = serializers.ChoiceField(choices=("all", "day", "week", "month", "year"), default="day")
    snapshot = serializers.CharField(max_length=2048, required=False)
    view = serializers.ChoiceField(
        choices=("periods", "products", "documents", "events"), default="periods"
    )


class ChartFilters(StatisticsFilters):
    chart = serializers.ChoiceField(choices=("trend", "ranking"), default="trend")
    metric = serializers.ChoiceField(choices=("quantity", "amount"), default="quantity")
    dimension = serializers.CharField(
        max_length=len("base_unit:") + ProductUom._meta.get_field("code").max_length, required=False
    )
    direction = serializers.ChoiceField(choices=("both", "inbound", "outbound"), default="both")


def parse_statistics_filters(params, *, chart=False):
    schema = ChartFilters if chart else StatisticsFilters
    if set(params) - set(schema().fields) - {"format"}:
        raise ValidationError("包含不支持的筛选条件。")
    serializer = schema(data=params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def unit_name(value):
    return value if value and re.search("[一-龥]", value) else "单位待核对"


def amount_status(row):
    return (
        "PRICED" if not row["unpriced_count"] else "PARTIAL" if row["priced_count"] else "UNPRICED"
    )


def totals_payload(rows):
    """Combine already aggregated unit groups; never add unlike quantities."""
    totals, amounts = [], {}
    for source in rows:
        row = dict(source)
        row.pop("group_key", None)
        row["amount_status"] = amount_status(row)
        row["category_label"] = CATEGORIES[row["category"]]
        totals.append(serialize_row(row))
        key = (row["category"], row["currency"])
        amount = amounts.setdefault(
            key,
            dict(
                category=key[0],
                currency=key[1],
                amount=Decimal(0),
                priced_count=0,
                unpriced_count=0,
            ),
        )
        with localcontext() as context:
            context.prec = 80  # Preserve the full precision of MySQL DECIMAL aggregates.
            amount["amount"] += Decimal(row["amount"])
        for field in ("priced_count", "unpriced_count"):
            amount[field] += row[field]
    for amount in amounts.values():
        amount["amount_status"] = amount_status(amount)
        serialize_row(amount)
    return {"totals": totals, "amounts": list(amounts.values())}


AGGREGATES = """category, base_unit, MIN(base_unit_name) AS base_unit_name, currency,
    SUM(ABS(qty_delta)) AS qty, SUM(qty_delta) AS delta,
    COALESCE(SUM(amount), 0) AS amount,
    SUM(CASE WHEN amount IS NULL THEN ABS(qty_delta) ELSE 0 END) AS unpriced_qty,
    SUM(amount IS NULL) AS unpriced_count, SUM(amount IS NOT NULL) AS priced_count,
    SUM(posted_at IS NULL OR category = 'unknown' OR dimensions_unverified) AS unverified_count,
    SUM(posting_status <> 'POSTED') AS posting_pending_count"""


class OwnerStatistics(OwnerHistory):
    """Aggregate business facts in MySQL before paginating complete calendar groups."""

    def __init__(self, user, filters):
        self.user, self.filters = user, filters
        self.scope = statistics_scope(user)
        self.personal = "owner_salesperson" in self.scope.roles
        self.owner_id = self.scope.single_owner_id
        self.snapshot = filters.get("snapshot")
        if self.snapshot:
            try:
                state = signing.loads(self.snapshot, salt=SNAPSHOT_SALT)
                if state["owner"] != self.owner_id or state["user"] != user.pk:
                    raise ValueError
                self.cutoff = datetime.fromisoformat(state["at"])
                self.ledger_max, self.review_max = int(state["ledger"]), int(state["review"])
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                raise ValidationError("统计快照无效，请刷新后重试。") from None
        else:
            self.cutoff = timezone.now()
            self.ledger_max = (
                InventoryTransaction.objects.filter(owner_id=self.owner_id).aggregate(
                    value=Max("id")
                )["value"]
                or 0
            )
            self.review_max = (
                WmsTask.all_objects.filter(owner_id=self.owner_id, task_type="REVIEW").aggregate(
                    value=Max("id")
                )["value"]
                or 0
            )
            self.snapshot = signing.dumps(
                dict(
                    owner=self.owner_id,
                    user=user.pk,
                    at=self.cutoff.isoformat(),
                    ledger=self.ledger_max,
                    review=self.review_max,
                ),
                salt=SNAPSHOT_SALT,
                compress=True,
            )

    def query(self, sql, params=(), **kwargs):
        cutoff = self.cutoff
        if timezone.is_aware(cutoff):
            cutoff = cutoff.astimezone(dt_timezone.utc).replace(tzinfo=None)
        with connection.cursor() as cursor:
            cursor.execute(
                statistics_sql(personal=self.personal) + sql,
                [
                    self.owner_id,
                    self.ledger_max,
                    self.owner_id,
                    self.review_max,
                    cutoff,
                    *([self.user.pk] * 4 if self.personal else []),
                    *params,
                ],
            )
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def where(self):
        where, params = super().where()
        for column in ("base_unit", "currency"):
            if self.filters.get(column):
                where += f" AND {column} = %s"
                params.append(self.filters[column])
        if self.filters.get("missing_unit"):
            where += " AND base_unit = ''"
        return where, params

    def envelope(self):
        return {
            **super().envelope(),
            "as_of": stamp(self.cutoff),
            "access_scope": "personal" if self.personal else "owner",
            "access_scope_label": "本人经手" if self.personal else "本货主全部业务",
        }

    def summary(self):
        where, params = self.where()
        totals = self.query(
            f"SELECT {AGGREGATES} FROM statistics_facts WHERE {where} "
            "GROUP BY category, base_unit, currency ORDER BY category, base_unit, currency",
            params,
        )
        warehouses = self.query(
            "SELECT warehouse_id AS id, MIN(warehouse_name) AS name "
            "FROM statistics_facts GROUP BY warehouse_id ORDER BY warehouse_id"
        )
        return {**self.envelope(), **totals_payload(totals), "warehouses": warehouses}

    def group_expression(self, view):
        if view != "periods":
            return {
                "products": "CAST(product_id AS CHAR)",
                "documents": "document_key",
                "events": "event_id",
            }[view]
        day = (
            "DATE(CONVERT_TZ(posted_at, '+00:00', '+08:00'))"
            if settings.USE_TZ
            else "DATE(posted_at)"
        )
        grain = self.filters["grain"]
        if grain == "all":
            raise ValidationError("请选择按天、按周、按月或按年统计。")
        period = {
            "day": day,
            "week": f"DATE_SUB({day}, INTERVAL WEEKDAY({day}) DAY)",
            "month": f"DATE_SUB({day}, INTERVAL DAYOFMONTH({day}) - 1 DAY)",
            "year": f"MAKEDATE(YEAR({day}), 1)",
        }[grain]
        return f"CAST({period} AS CHAR)"

    def listing(self, view, *, export=False):
        where, params = self.where()
        grouping = self.group_expression(view)
        grouped = (
            f"SELECT {grouping} AS group_key FROM statistics_facts WHERE {where} GROUP BY group_key"
        )
        count = self.query(f"SELECT COUNT(*) AS n FROM ({grouped}) groups_count", params)[0]["n"]
        if export and count > MAX_EXPORT_ROWS:
            raise ExportTooLarge()
        page, size = self.filters["page"], self.filters["page_size"]
        limit, offset = (MAX_EXPORT_ROWS, 0) if export else (size, (page - 1) * size)
        order = self.filters["order"].upper()
        sort = (
            f"group_key IS NULL, group_key {order}"
            if view == "periods"
            else (f"last_at IS NULL, last_at {order}, group_key {order}")
        )
        rows = self.query(
            f"SELECT {grouping} AS group_key, MAX(posted_at) AS last_at, "
            "MIN(product_id) AS product_id, MIN(product_name) AS product_name, "
            "MIN(product_code) AS product_code, MIN(product_spec) AS product_spec, "
            "MIN(document_key) AS document_key, MIN(document_no) AS document_no, "
            "MIN(warehouse_name) AS warehouse_name, MIN(counterparty) AS counterparty, "
            "MIN(category) AS category, COUNT(DISTINCT product_id) AS product_count "
            f"FROM statistics_facts WHERE {where} GROUP BY group_key "
            f"ORDER BY {sort} LIMIT %s OFFSET %s",
            [*params, limit, offset],
        )
        if rows:
            chosen = " OR ".join(f"({grouping}) <=> %s" for _ in rows)
            totals = self.query(
                f"SELECT {grouping} AS group_key, {AGGREGATES} FROM statistics_facts "
                f"WHERE {where} AND ({chosen}) GROUP BY group_key, category, base_unit, currency "
                "ORDER BY category, base_unit, currency",
                [*params, *(r["group_key"] for r in rows)],
            )
            by_key = {}
            for total in totals:
                by_key.setdefault(total["group_key"], []).append(total)
            for row in rows:
                row.update(totals_payload(by_key[row["group_key"]]))
                if view == "periods":
                    self.period_bounds(row)
                elif view == "events":
                    row.update(event_id=row["group_key"], event_at=row["last_at"])
            if view == "products":
                self.product_stock(rows)
        return {
            **self.envelope(),
            "count": count,
            "page": page,
            "next_page": page + 1 if not export and offset + len(rows) < count else None,
            "results": [serialize_row(r) for r in rows],
        }

    def period_bounds(self, row):
        start = date.fromisoformat(row["group_key"]) if row["group_key"] else None
        end = None
        if start:
            grain = self.filters["grain"]
            if grain == "year":
                end = date(start.year, 12, 31)
            elif grain == "month":
                end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
            else:
                end = start + timedelta(days=6 if grain == "week" else 0)
        row.update(period_start=start, period_end=end)
        row["range_start"] = max(start, self.filters.get("date_from", start)) if start else None
        row["range_end"] = min(end, self.filters.get("date_to", end)) if end else None
        row["partial_period"] = row["range_start"] != start or row["range_end"] != end

    def product_stock(self, rows):
        stock = InventoryDetail.objects.filter(
            owner_id=self.owner_id, product_id__in=[r["product_id"] for r in rows], is_active=True
        )
        if self.filters.get("warehouse_id"):
            stock = stock.filter(warehouse_id=self.filters["warehouse_id"])
        if self.filters.get("batch"):
            stock = stock.filter(batch_no__icontains=self.filters["batch"])
        stocks = list(
            stock.values("product_id", "base_unit").annotate(
                current_onhand=Sum("onhand_qty"), current_available=Sum("available_qty")
            )
        )
        by_product = {}
        codes = {t["base_unit"] for r in rows for t in r["totals"]}
        for item in stocks:
            by_product.setdefault(item["product_id"], {})[item["base_unit"]] = item
            codes.add(item["base_unit"])
        names = dict(ProductUom.all_objects.filter(code__in=codes).values_list("code", "name"))
        for row in rows:
            current = by_product.get(row["product_id"], {})
            units = {t["base_unit"] for t in row["totals"]} | set(current)
            row["current_stock"] = [
                serialize_row(
                    dict(
                        base_unit=code,
                        base_unit_name=unit_name(names.get(code)),
                        current_onhand=current.get(code, {}).get("current_onhand", Decimal(0)),
                        current_available=current.get(code, {}).get(
                            "current_available", Decimal(0)
                        ),
                    )
                )
                for code in sorted(units)
            ]

    def event(self, event_id):
        where, params = self.where()
        where += " AND event_id = %s"
        params.append(event_id)
        count = self.query(f"SELECT COUNT(*) AS n FROM statistics_facts WHERE {where}", params)[0][
            "n"
        ]
        if not count:
            raise NotFound("当前货主和筛选条件下没有收发明细。")
        page, size = self.filters["page"], self.filters["page_size"]
        rows = self.query(
            f"SELECT * FROM statistics_facts WHERE {where} ORDER BY id LIMIT %s OFFSET %s",
            [*params, size, (page - 1) * size],
        )
        evidence = self.receipt_evidence({r["receipt_id"] for r in rows if r["receipt_id"]})
        names = dict(
            ProductUom.all_objects.filter(
                code__in={line["base_unit"] for receipt in evidence for line in receipt["lines"]}
            ).values_list("code", "name")
        )
        for receipt in evidence:
            for line in receipt["lines"]:
                line["base_unit_name"] = unit_name(names.get(line["base_unit"]))
        return {
            **self.envelope(),
            "event": {"event_id": event_id},
            "count": count,
            "next_page": page + 1 if page * size < count else None,
            "results": [serialize_row(r) for r in rows],
            "receipts": evidence,
        }

    def export(self, request):
        statistics_scope(request.user, exporting=True)
        view = self.filters["view"]
        if view == "events":
            where, params = self.where()
            count = self.query(f"SELECT COUNT(*) AS n FROM statistics_facts WHERE {where}", params)[
                0
            ]["n"]
            if count > MAX_EXPORT_ROWS:
                raise ExportTooLarge()
            order = self.filters["order"].upper()
            rows = self.query(
                f"SELECT * FROM statistics_facts WHERE {where} "
                f"ORDER BY posted_at IS NULL, posted_at {order}, id {order}",
                params,
            )
            headers = [
                "业务时间",
                "单据号",
                "商品编码",
                "商品名称",
                "仓库",
                "批次",
                "业务类型",
                "单位",
                "数量",
                "币种",
                "已知金额",
                "金额状态",
                "入账状态",
            ]
            values = [
                [
                    stamp(r["posted_at"]) or "时间未记录",
                    r["document_no"],
                    r["product_code"],
                    r["product_name"],
                    r["warehouse_name"],
                    r["batch_no"],
                    CATEGORIES[r["category"]],
                    r["base_unit_name"],
                    abs(r["qty_delta"]),
                    r["currency"],
                    r["amount"],
                    "金额待核对" if r["amount"] is None else "已计价",
                    "已入账" if r["posting_status"] == "POSTED" else "库存扣减异常／待入账",
                ]
                for r in rows
            ]
        else:
            rows = self.listing(view, export=True)["results"]
            identity = {
                "periods": (
                    ["周期开始", "周期结束", "实际开始", "实际结束"],
                    ["period_start", "period_end", "range_start", "range_end"],
                ),
                "products": (
                    ["商品编码", "商品名称", "规格"],
                    ["product_code", "product_name", "product_spec"],
                ),
                "documents": (
                    ["单据号", "仓库", "供应商／客户"],
                    ["document_no", "warehouse_name", "counterparty"],
                ),
            }[view]
            headers = identity[0] + [
                "业务类型",
                "单位",
                "数量",
                "币种",
                "已知金额",
                "金额状态",
                "未计价数量",
            ]
            values = [
                [
                    *(r[k] or ("时间未记录" if view == "periods" else "") for k in identity[1]),
                    CATEGORIES[t["category"]],
                    t["base_unit_name"],
                    Decimal(t["qty"]),
                    t["currency"],
                    Decimal(t["amount"]) if t["priced_count"] else None,
                    AMOUNT_LABELS[t["amount_status"]],
                    Decimal(t["unpriced_qty"]),
                ]
                for r in rows
                for t in r["totals"]
            ]
        if len(values) > MAX_EXPORT_ROWS:
            raise ExportTooLarge()
        return xlsx_download(
            request,
            scope=self.scope,
            kind="personal-stock-statistics" if self.personal else "stock-statistics",
            title={
                "periods": "周期收发汇总",
                "products": "商品收发汇总",
                "documents": "单据收发汇总",
                "events": "原始收发明细",
            }[view],
            headers=headers,
            rows=values,
            filters="；".join(
                f"{k}={v}"
                for k, v in self.filters.items()
                if k not in {"page", "page_size", "snapshot"}
            ),
            basis="出库按实际复核通过，入库按成功入账；北京时间；采购／销售金额；不同单位分别统计。",
        )

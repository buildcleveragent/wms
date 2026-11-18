"""Scoped, read-only stock history with common aggregation and export semantics."""

import calendar
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.db.models import Max, Prefetch, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from allapp.core.owner_exports import MAX_EXPORT_ROWS, ExportTooLarge, xlsx_download
from allapp.inbound.models import InboundReceipt, InboundReceiptLine
from allapp.products.identifier_lookup import product_search_q
from allapp.products.models import Product

from .models import InventoryDetail, InventoryTransaction
from .owner_detail import BUSINESS_TZ
from .owner_history_sql import NUMBERING_SQL, facts_sql
from .owner_permissions import owner_inventory_scope

CATEGORIES = {
    "inbound": "正常入库",
    "outbound": "正常出库",
    "customer_return": "销售退货入库",
    "supplier_return": "退货出库（退供／退货主）",
    "reversal": "撤销回补",
    "adjust_gain": "盘点／调整增加",
    "adjust_loss": "盘点／调整减少",
    "transfer_in": "货权转入",
    "transfer_out": "货权转出",
    "unknown": "待核对",
}


def history_scope(user, *, exporting=False):
    scope = owner_inventory_scope(user)
    if (
        "owner_manager" not in scope.roles
        or not user.has_perm("accounts.access_owner_management")
        or not user.has_perm("reports.view_owner_operations")
        or (exporting and not user.has_perm("reports.export_operations"))
    ):
        raise PermissionDenied("当前账号没有货主出入库历史访问权限。")
    return scope


class HistoryFilters(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    warehouse_id = serializers.IntegerField(min_value=1, required=False)
    product_id = serializers.IntegerField(min_value=1, required=False)
    search = serializers.CharField(max_length=100, required=False, allow_blank=True)
    document_no = serializers.CharField(max_length=100, required=False, allow_blank=True)
    document_key = serializers.RegexField(r"^[a-z_]+:[0-9]+$", required=False)
    batch = serializers.CharField(max_length=64, required=False, allow_blank=True)
    category = serializers.ChoiceField(choices=tuple(CATEGORIES), required=False)
    grain = serializers.ChoiceField(choices=("all", "month", "week", "day"), default="all")
    order = serializers.ChoiceField(choices=("asc", "desc"), default="desc")
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=30)
    snapshot = serializers.IntegerField(min_value=0, required=False)
    undated = serializers.BooleanField(default=False)
    view = serializers.ChoiceField(choices=("products", "documents", "events"), default="events")

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to"):
            if attrs["date_from"] > attrs["date_to"]:
                raise serializers.ValidationError("开始日期不能晚于结束日期。")
        if attrs.get("undated") and (attrs.get("date_from") or attrs.get("date_to")):
            raise serializers.ValidationError("未记录时间的流水不能同时按日期筛选。")
        return attrs


def parse_filters(params):
    unknown = set(params) - set(HistoryFilters().fields) - {"format"}
    if unknown:
        raise ValidationError("包含不支持的筛选条件。")
    serializer = HistoryFilters(data=params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def decimal_text(value):
    return format(Decimal(value or 0), "f")


def stamp(value):
    if value is None:
        return None
    if timezone.is_naive(value):
        value = value.replace(tzinfo=dt_timezone.utc if settings.USE_TZ else BUSINESS_TZ)
    return value.astimezone(BUSINESS_TZ).isoformat()


def serialize_row(row):
    for key, value in row.items():
        if isinstance(value, Decimal):
            row[key] = int(value) if key.endswith(("_count", "_sequence")) else decimal_text(value)
        elif isinstance(value, datetime):
            row[key] = stamp(value)
        elif isinstance(value, date):
            row[key] = value.isoformat()
    if "category" in row:
        row["category_label"] = CATEGORIES.get(row["category"], "待核对")
    return row


def quantity_columns():
    return ", ".join(
        f"SUM(CASE WHEN category = '{key}' THEN ABS(qty_delta) ELSE 0 END) AS {key}_qty, "
        f"COUNT(DISTINCT CASE WHEN category = '{key}' AND countable "
        f"THEN event_key END) AS {key}_count, "
        f"SUM(CASE WHEN category = '{key}' AND NOT countable "
        f"THEN 1 ELSE 0 END) AS {key}_unverified_count"
        for key in CATEGORIES
    )


class OwnerHistory:
    """Run aggregate queries against one owner and one immutable ledger ceiling."""

    def __init__(self, user, filters):
        self.user = user
        self.scope = history_scope(user)
        self.owner_id = self.scope.single_owner_id
        self.filters = filters
        self.snapshot = filters.get("snapshot")
        if self.snapshot is None:
            self.snapshot = (
                InventoryTransaction.objects.filter(
                    owner_id=self.owner_id, is_active=True
                ).aggregate(value=Max("id"))["value"]
                or 0
            )

    def query(self, sql, params=(), *, numbered=False):
        prefix = facts_sql() + (NUMBERING_SQL if numbered else "")
        with connection.cursor() as cursor:
            cursor.execute(prefix + sql, [self.owner_id, self.snapshot, *params])
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def where(self):
        conditions, params = ["category <> 'movement'"], []
        for key in ("warehouse_id", "product_id", "category", "document_key"):
            if self.filters.get(key):
                conditions.append(f"{key} = %s")
                params.append(self.filters[key])
        for key, column in (("document_no", "document_no"), ("batch", "batch_no")):
            if self.filters.get(key):
                conditions.append(f"{column} LIKE %s")
                params.append("%" + connection.ops.prep_for_like_query(self.filters[key]) + "%")
        if self.filters.get("search"):
            products = Product.all_objects.filter(owner_id=self.owner_id).filter(
                product_search_q(self.filters["search"], product_field="id")
            )
            sql, product_params = products.values("pk").query.sql_with_params()
            conditions.append(f"product_id IN ({sql})")
            params.extend(product_params)
        for key, comparison, days in (("date_from", ">=", 0), ("date_to", "<", 1)):
            if self.filters.get(key):
                try:
                    boundary = datetime.combine(self.filters[key] + timedelta(days=days), time.min)
                except OverflowError:
                    raise ValidationError("日期超出可查询范围。") from None
                if settings.USE_TZ:
                    boundary = boundary.replace(tzinfo=BUSINESS_TZ).astimezone(dt_timezone.utc)
                    boundary = boundary.replace(tzinfo=None)
                conditions.append(f"posted_at {comparison} %s")
                params.append(boundary)
        if self.filters.get("undated"):
            conditions.append("posted_at IS NULL")
        return " AND ".join(conditions), params

    def envelope(self):
        return {
            "as_of": stamp(timezone.now()),
            "snapshot": self.snapshot,
            "owner_id": self.owner_id,
            "scope_label": (
                "期间合计"
                if self.filters.get("date_from") or self.filters.get("date_to")
                else "历史累计"
            ),
        }

    def summary(self):
        where, params = self.where()
        rows = self.query(
            f"SELECT category, base_unit, SUM(ABS(qty_delta)) AS qty, "
            f"SUM(qty_delta) AS delta, "
            f"COUNT(DISTINCT CASE WHEN countable THEN event_key END) AS event_count, "
            f"SUM(CASE WHEN NOT countable THEN 1 ELSE 0 END) AS unverified_count "
            f"FROM facts WHERE {where} GROUP BY category, base_unit",
            params,
        )
        warehouses = list(
            InventoryTransaction.objects.filter(owner_id=self.owner_id, is_active=True)
            .values("warehouse_id", "warehouse__name")
            .order_by("warehouse_id")
            .distinct()
        )
        return {
            **self.envelope(),
            "totals": [serialize_row(r) for r in rows],
            "warehouses": [
                {"id": w["warehouse_id"], "name": w["warehouse__name"]} for w in warehouses
            ],
            "categories": [{"value": k, "label": v} for k, v in CATEGORIES.items()],
        }

    def grouped_sql(self, view):
        where, params = self.where()
        numbered = view == "events"
        source = "numbered" if numbered else "facts"
        common = quantity_columns()
        if view == "products":
            select = (
                "product_id, base_unit, MIN(product_name) AS product_name, "
                "MIN(product_code) AS product_code, MIN(product_sku) AS product_sku, "
                f"MIN(product_spec) AS product_spec, {common}, MAX(posted_at) AS last_at"
            )
            group = "product_id, base_unit"
            sort = "last_at IS NULL, last_at {order}, product_id {order}, base_unit"
        elif view == "documents":
            select = (
                "document_key, warehouse_id, base_unit, MIN(document_no) AS document_no, "
                "MIN(warehouse_name) AS warehouse_name, MIN(counterparty) AS counterparty, "
                f"{common}, MAX(posted_at) AS last_at"
            )
            group = "document_key, warehouse_id, base_unit"
            sort = "last_at IS NULL, last_at {order}, document_key {order}, warehouse_id, base_unit"
        elif view == "periods":
            grain = self.filters["grain"]
            if grain == "all":
                raise ValidationError("请先选择按月、按周或按天统计。")
            day = (
                "DATE(CONVERT_TZ(posted_at, '+00:00', '+08:00'))"
                if settings.USE_TZ
                else "DATE(posted_at)"
            )
            period = {
                "day": day,
                "week": f"DATE_SUB({day}, INTERVAL WEEKDAY({day}) DAY)",
                "month": f"DATE_SUB({day}, INTERVAL DAYOFMONTH({day}) - 1 DAY)",
            }[grain]
            select = f"{period} AS period_start, base_unit, {common}"
            group = "period_start, base_unit"
            sort = "period_start IS NULL, period_start {order}, base_unit"
        else:
            select = (
                "event_key, base_unit, MIN(event_id) AS event_id, MIN(event_at) AS event_at, "
                "MIN(document_key) AS document_key, MIN(document_no) AS document_no, "
                "MIN(warehouse_name) AS warehouse_name, MIN(category) AS category, "
                "MIN(document_sequence) AS document_sequence, MIN(countable) AS countable, "
                "SUM(ABS(qty_delta)) AS qty, SUM(qty_delta) AS delta, "
                "COUNT(DISTINCT product_id) AS product_count, MIN(product_name) AS product_name"
            )
            if self.filters.get("product_id"):
                select += ", MIN(product_sequence) AS product_sequence"
            group = "event_key, base_unit"
            sort = "event_at IS NULL, event_at {order}, event_id {order}, base_unit"
        return (
            f"SELECT {select} FROM {source} WHERE {where} GROUP BY {group}",
            params,
            numbered,
            sort.format(order=self.filters["order"].upper()),
        )

    def listing(self, view, *, export=False):
        sql, params, numbered, sort = self.grouped_sql(view)
        count = self.query(f"SELECT COUNT(*) AS n FROM ({sql}) grouped", params, numbered=numbered)[
            0
        ]["n"]
        if export and count > MAX_EXPORT_ROWS:
            raise ExportTooLarge()
        page, size = self.filters["page"], self.filters["page_size"]
        limit, offset = (MAX_EXPORT_ROWS, 0) if export else (size, (page - 1) * size)
        rows = self.query(
            sql + f" ORDER BY {sort} LIMIT %s OFFSET %s",
            [*params, limit, offset],
            numbered=numbered,
        )
        if view == "products":
            self.current_stock(rows)
        if view == "periods":
            for row in rows:
                start = row["period_start"]
                if start:
                    days = {
                        "day": 1,
                        "week": 7,
                        "month": calendar.monthrange(start.year, start.month)[1],
                    }[self.filters["grain"]]
                    row["period_end"] = start + timedelta(days=days - 1)
                else:
                    row["period_end"] = None
        return {
            **self.envelope(),
            "count": count,
            "page": page,
            "next_page": page + 1 if offset + len(rows) < count else None,
            "results": [serialize_row(r) for r in rows],
        }

    def current_stock(self, rows):
        qs = InventoryDetail.objects.filter(
            owner_id=self.owner_id, is_active=True, product_id__in=[r["product_id"] for r in rows]
        )
        if self.filters.get("warehouse_id"):
            qs = qs.filter(warehouse_id=self.filters["warehouse_id"])
        if self.filters.get("batch"):
            qs = qs.filter(batch_no__icontains=self.filters["batch"])
        values = qs.values("product_id", "base_unit").annotate(
            onhand=Sum("onhand_qty"), available=Sum("available_qty")
        )
        stocks = {(r["product_id"], r["base_unit"]): r for r in values}
        for row in rows:
            stock = stocks.get((row["product_id"], row["base_unit"]), {})
            row["current_onhand"] = decimal_text(stock.get("onhand"))
            row["current_available"] = decimal_text(stock.get("available"))

    def event(self, event_id):
        header = self.query(
            "SELECT * FROM document_numbers WHERE event_id = %s", [event_id], numbered=True
        )
        if not header:
            raise NotFound("收发记录不存在或不在当前货主范围。")
        where, params = self.where()
        sql = (
            "SELECT id, product_id, product_name, product_code, product_spec, base_unit, "
            "batch_no, production_date, expiry_date, qty_delta, posted_at, document_no, "
            "document_key, category, warehouse_name, receipt_id, inbound_id, outbound_id, "
            "customer_return_id, product_sequence, document_sequence "
            f"FROM numbered WHERE event_id = %s AND {where}"
        )
        count = self.query(
            f"SELECT COUNT(*) AS n FROM ({sql}) detail_rows", [event_id, *params], numbered=True
        )[0]["n"]
        if not count:
            raise NotFound("当前条件下没有收发明细。")
        page, size = self.filters["page"], self.filters["page_size"]
        rows = self.query(
            sql + " ORDER BY id LIMIT %s OFFSET %s",
            [event_id, *params, size, (page - 1) * size],
            numbered=True,
        )
        evidence = self.receipt_evidence({r["receipt_id"] for r in rows if r["receipt_id"]})
        return {
            **self.envelope(),
            "event": serialize_row(header[0]),
            "count": count,
            "next_page": page + 1 if page * size < count else None,
            "results": [serialize_row(r) for r in rows],
            "receipts": evidence,
        }

    def receipt_evidence(self, ids):
        receipts = InboundReceipt.all_objects.filter(
            pk__in=ids, owner_id=self.owner_id
        ).prefetch_related(
            Prefetch(
                "lines",
                queryset=InboundReceiptLine.all_objects.filter(
                    product__owner_id=self.owner_id
                ).prefetch_related("arrival_photos"),
            )
        )
        result = []
        for receipt in receipts:
            lines = []
            for line in receipt.lines.all():
                if self.filters.get("product_id") and line.product_id != self.filters["product_id"]:
                    continue
                if (
                    self.filters.get("batch")
                    and self.filters["batch"].lower() not in (line.lot_no or "").lower()
                ):
                    continue
                lines.append(
                    {
                        "id": line.pk,
                        "product_id": line.product_id,
                        "product_name": line.product_name,
                        "base_unit": line.base_uom,
                        "batch_no": line.lot_no,
                        "quality_recorded": receipt.stage != "LEGACY",
                        "qty_ok": decimal_text(line.qty_ok) if receipt.stage != "LEGACY" else None,
                        "qty_damage": (
                            decimal_text(line.qty_damage) if receipt.stage != "LEGACY" else None
                        ),
                        "qty_reject": (
                            decimal_text(line.qty_reject) if receipt.stage != "LEGACY" else None
                        ),
                        "damage_reason": line.damage_reason,
                        "reject_reason": line.reject_reason,
                        "photos": [
                            {
                                "id": p.pk,
                                "url": (
                                    f"/api/inbound/orders/{receipt.order_id}/"
                                    f"batches/{receipt.pk}/photos/{p.pk}/"
                                ),
                            }
                            for p in line.arrival_photos.all()
                            if receipt.order_id
                        ],
                    }
                )
            result.append({"id": receipt.pk, "number": receipt.receipt_no, "lines": lines})
        return result

    def export(self, request):
        history_scope(request.user, exporting=True)
        view = self.filters["view"]
        if view == "events":
            where, params = self.where()
            sql = f"""SELECT MIN(id) AS first_id, MIN(posted_at) AS event_at,
                MIN(document_no) AS document_no, MIN(category) AS category,
                MIN(warehouse_name) AS warehouse_name, MIN(product_code) AS product_code,
                MIN(product_name) AS product_name, base_unit, batch_no,
                SUM(qty_delta) AS qty, MIN(document_sequence) AS document_sequence,
                MIN(product_sequence) AS product_sequence
                FROM numbered WHERE {where}
                GROUP BY event_key, product_id, base_unit, batch_no, warehouse_id"""
            count = self.query(f"SELECT COUNT(*) AS n FROM ({sql}) grouped", params, numbered=True)[
                0
            ]["n"]
            if count > MAX_EXPORT_ROWS:
                raise ExportTooLarge()
            order = self.filters["order"].upper()
            rows = self.query(
                sql + f" ORDER BY event_at IS NULL, event_at {order}, first_id {order} LIMIT %s",
                [*params, MAX_EXPORT_ROWS],
                numbered=True,
            )
            headers = [
                "入账时间",
                "单据号",
                "业务类型",
                "仓库",
                "商品编码",
                "商品名称",
                "批次",
                "单位",
                "库存增减数量",
                "原单第几次",
                "商品第几次",
            ]
            values = [
                [
                    stamp(r["event_at"]) or "时间未记录",
                    r["document_no"] or "待核对",
                    CATEGORIES[r["category"]],
                    r["warehouse_name"],
                    r["product_code"],
                    r["product_name"],
                    r["batch_no"],
                    r["base_unit"],
                    r["qty"],
                    r["document_sequence"],
                    r["product_sequence"],
                ]
                for r in rows
            ]
        else:
            rows = self.listing(view, export=True)["results"]
            identity = (
                ["product_code", "product_name", "product_spec", "base_unit"]
                if view == "products"
                else ["document_no", "warehouse_name", "counterparty", "base_unit"]
            )
            headers = (
                ["商品编码", "商品名称", "规格", "单位"]
                if view == "products"
                else ["单据号", "仓库", "供应商／客户", "单位"]
            )
            for label in CATEGORIES.values():
                headers.extend([label + "数量", label + "次数"])
            values = []
            for row in rows:
                cells = [row[k] for k in identity]
                for key in CATEGORIES:
                    known = row[key + "_count"]
                    times = (
                        f"{known}（另有次数待核对）" if row[key + "_unverified_count"] else known
                    )
                    cells.extend([Decimal(row[key + "_qty"]), times])
                values.append(cells)
        return xlsx_download(
            request,
            scope=self.scope,
            kind="stock-history",
            title={
                "products": "商品收发汇总",
                "documents": "单据收发汇总",
                "events": "逐次收发明细",
            }[view],
            headers=headers,
            rows=values,
            filters="；".join(
                f"{k}={v}" for k, v in self.filters.items() if k not in {"page", "page_size"}
            ),
            basis=(
                "真实库存入账；正常收发、退货、回补、调整、转货分类展示；排除内部移动；"
                "不同单位不合计；无法确定的次数标为待核对，逐次序号留空。"
            ),
        )

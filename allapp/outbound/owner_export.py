"""Original order-line exports, scoped independently of legacy read compatibility."""

from django.db.models import FilteredRelation, Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from allapp.core.owner_exports import bounded_rows, export_params, owner_scope, xlsx_download

from .authz import strict_order_queryset
from .amounts import line_amount
from .models import OutboundOrder
from .order_filters import filter_orders

FILTER_LABELS = {
    "search": "搜索",
    "product": "商品",
    "sku": "SKU",
    "owner_id": "货主编号",
    "warehouse_id": "仓库编号",
    "submit_status": "提交状态",
    "approval_status": "审核状态",
    "outbound_type": "单据类型",
    "delivery_method": "交付方式",
    "src_bill_no": "来源单号",
    "task_no": "任务号",
    "task_status": "任务状态",
    "waybill_no": "运单号",
    "date_from": "业务日期从",
    "date_to": "业务日期到",
    "biz_date_from": "业务日期从",
    "biz_date_to": "业务日期到",
    "is_closed": "已关闭",
    "overdue": "列表超期条件",
}


def export_orders(request):
    scope = owner_scope(request.user)
    if not any(
        request.user.has_perm(p)
        for p in (
            "outbound.view_outboundorder",
            "outbound.view_all_outbound_orders",
            "outbound.submit_outbound_as_owner_buyers",
            "outbound.approve_outbound_as_owner_manager",
        )
    ):
        raise PermissionDenied("没有订单查看权限。")
    params = export_params(request.query_params, FILTER_LABELS)
    for key in ("owner_id", "warehouse_id"):
        if key in params:
            params[key] = serializers.IntegerField(min_value=1).run_validation(params[key])
    if params.get("owner_id", scope.single_owner_id) != scope.single_owner_id:
        raise PermissionDenied("不能导出其他货主的数据。")
    qs = filter_orders(strict_order_queryset(OutboundOrder.objects.all(), request.user), params)
    qs = qs.annotate(valid_line=FilteredRelation("lines", condition=Q(lines__is_deleted=False)))
    order_fields = [
        "id",
        "order_no",
        "biz_date",
        "customer__name",
        "warehouse__name",
        "processing_mode",
        "submit_status",
        "approval_status",
        "is_closed",
        "remark",
    ]
    line_fields = [
        "id",
        "line_no",
        "product__code",
        "product__sku",
        "product__name",
        "product__spec",
        "base_uom__name",
        "base_qty",
        "base_price",
        "is_priced",
        "aux_uom__uom__name",
        "aux_qty",
        "note",
    ]
    fields = order_fields + [f"valid_line__{f}" for f in line_fields]
    records = bounded_rows(
        qs.order_by("-biz_date", "-id", "valid_line__line_no", "valid_line__id")
        .values(*fields)
        .distinct()
    )
    choices = {
        key: dict(OutboundOrder._meta.get_field(key).choices)
        for key in ("processing_mode", "submit_status", "approval_status")
    }
    choices["approval_status"] = {
        code: OutboundOrder.approval_status_label(code)
        for code in choices["approval_status"]
    }
    rows = []
    for record in records:
        line = {k: record[f"valid_line__{k}"] for k in line_fields}
        amount = (
            line_amount(line["base_qty"], line["base_price"])
            if line["id"] is not None and line["is_priced"]
            else None
        )
        rows.append(
            [
                record["order_no"],
                record["biz_date"],
                record["customer__name"] or "未记录",
                record["warehouse__name"],
                *[choices[k].get(record[k], "状态待核对") for k in choices],
                "已关闭" if record["is_closed"] else "未关闭",
                line["line_no"],
                line["product__code"],
                line["product__sku"],
                line["product__name"] if line["id"] else "无有效商品行",
                line["product__spec"],
                line["base_uom__name"] or "未记录",
                line["base_qty"],
                line["base_price"] if line["is_priced"] else None,
                amount,
                line["aux_uom__uom__name"],
                line["aux_qty"],
                line["note"],
                record["remark"],
                ("已计价" if line["is_priced"] else "未计价") if line["id"] else None,
            ]
        )
    return xlsx_download(
        request,
        scope=scope,
        kind="orders",
        title="订单商品明细",
        headers=[
            "订单号",
            "业务日期",
            "客户",
            "仓库",
            "处理模式",
            "提交状态",
            "审核状态",
            "关闭状态",
            "原单行号",
            "商品编码",
            "SKU",
            "商品名称",
            "规格",
            "基本单位",
            "计划数量",
            "基本单价",
            "行金额",
            "包装单位",
            "包装数量",
            "行备注",
            "订单备注",
            "计价状态",
        ],
        rows=rows,
        filters="；".join(f"{FILTER_LABELS[k]}：{v}" for k, v in params.items()),
        basis="订单计划明细，数量及行金额与订单详情一致，不代表实际发运；相同商品按原单行分别保留。",
    )

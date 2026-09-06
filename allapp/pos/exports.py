from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .models import (
    PosCustomerRepayment,
    PosPayment,
    PosPaymentLine,
    PosRefund,
    PosReturn,
    PosReturnLine,
    PosSale,
    PosSaleLine,
)

SHEET_TITLES = {
    "shift": "班次汇总",
    "summary": "统计汇总",
    "payments": "支付汇总",
    "owners": "货主汇总",
    "products": "商品汇总",
    "cashiers": "收银员汇总",
    "sales": "销售单",
    "payment_lines": "支付明细",
    "sale_lines": "销售明细",
    "returns": "退货单",
    "return_lines": "退货明细",
    "refunds": "退款明细",
    "repayments": "客户还款",
}

STATS_SUMMARY_FIELDS = (
    ("sale_count", "销售单数"),
    ("completed_count", "完成单数"),
    ("voided_count", "作废单数"),
    ("return_count", "退货单数"),
    ("line_count", "销售明细行数"),
    ("return_line_count", "退货明细行数"),
    ("total_qty", "销售总数量"),
    ("completed_qty", "完成销售数量"),
    ("voided_qty", "作废数量"),
    ("return_qty", "退货数量"),
    ("net_qty", "净销售数量"),
    ("gross_amount", "销售总金额"),
    ("sales_amount", "销售金额"),
    ("voided_amount", "作废金额"),
    ("return_amount", "退货金额"),
    ("net_amount", "净销售额"),
    ("received_amount", "实收金额"),
    ("credit_amount", "赊账金额"),
    ("repayment_amount", "还款金额"),
)

SHIFT_SUMMARY_FIELDS = (
    ("sale_count", "销售单数"),
    ("completed_count", "完成单数"),
    ("voided_count", "作废单数"),
    ("return_count", "退货单数"),
    ("gross_sales_amount", "销售总额"),
    ("net_amount", "净销售额"),
    ("voided_amount", "作废金额"),
    ("return_amount", "退货金额"),
    ("opening_cash_amount", "备用金"),
    ("expected_cash_amount", "现金应点金额"),
    ("actual_cash_amount", "现金实点金额"),
    ("cash_difference", "现金差异"),
    ("credit_amount", "赊账金额"),
    ("repayment_amount", "还款金额"),
    ("received_amount", "实收金额"),
    ("line_count", "销售明细行数"),
    ("completed_line_count", "完成明细行数"),
    ("completed_qty", "完成销售数量"),
    ("completed_line_amount", "完成销售金额"),
    ("voided_qty", "作废数量"),
    ("voided_line_amount", "作废明细金额"),
    ("cash_sales_amount", "现金销售金额"),
)

PAYMENT_METHOD_LABELS = dict(PosPayment.Method.choices)
ALL_INFORMATION_SHEET_TITLE = "全部信息"
ALL_INFORMATION_SECTION_FILL = PatternFill(fill_type="solid", fgColor="1F4E78")
ALL_INFORMATION_HEADER_FILL = PatternFill(fill_type="solid", fgColor="D9EAF7")


def _text(value):
    if value is None:
        return ""
    return str(value)


def _dt(value):
    return value.isoformat() if value else ""


def _money(value):
    return Decimal(value or 0)


def _sale_customer_name(sale):
    customer = getattr(sale, "pos_customer", None) or getattr(sale, "selected_customer", None)
    return getattr(customer, "name", "") if customer else ""


def _repayment_customer_name(repayment):
    customer = getattr(repayment, "pos_customer", None) or getattr(repayment, "customer", None)
    return getattr(customer, "name", "") if customer else ""


def _append_stats_sheet(workbook, title, headers, rows):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    return sheet


def _append_summary_rows(sheet, data, fields):
    for key, label in fields:
        if key in data:
            sheet.append([label, data[key]])


def _payment_method_label(row):
    return row.get("method_label") or PAYMENT_METHOD_LABELS.get(row.get("method"), "其他")


def _prepend_all_information_sheet(workbook):
    """Copy every existing worksheet into one value-only sheet at index zero."""

    source_sheets = list(workbook.worksheets)
    combined = workbook.create_sheet(ALL_INFORMATION_SHEET_TITLE, 0)
    for index, source in enumerate(source_sheets):
        combined.append([source.title])
        section_row = combined.max_row
        for column in range(1, max(source.max_column, 1) + 1):
            cell = combined.cell(row=section_row, column=column)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = ALL_INFORMATION_SECTION_FILL

        header_row = combined.max_row + 1
        for values in source.iter_rows(values_only=True):
            combined.append(list(values))
        if combined.max_row >= header_row:
            for cell in combined[header_row]:
                cell.font = Font(bold=True)
                cell.fill = ALL_INFORMATION_HEADER_FILL

        if index < len(source_sheets) - 1:
            combined.append([])
    return combined


def build_sales_export_workbook(sales, *, include_all_sheet=False):
    sales = list(
        sales.select_related(
            "warehouse",
            "cashier",
            "shift",
            "pos_customer",
            "selected_customer",
            "payment",
        ).order_by("-created_at", "-id")
    )
    sale_ids = [sale.id for sale in sales]
    return_amounts = {
        row["sale_id"]: row["amount"] or Decimal("0.00")
        for row in PosReturn.objects.filter(
            sale_id__in=sale_ids,
            status=PosReturn.Status.COMPLETED,
        )
        .values("sale_id")
        .annotate(amount=Sum("total_amount"))
    }
    credit_amounts = {
        row["sale_id"]: row["amount"] or Decimal("0.00")
        for row in PosPaymentLine.objects.filter(
            sale_id__in=sale_ids,
            method=PosPayment.Method.CREDIT,
            status=PosPayment.Status.PAID,
        )
        .values("sale_id")
        .annotate(amount=Sum("amount"))
    }

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLES["sales"]
    sheet.append(
        [
            "销售单号",
            "小票号",
            "状态",
            "仓库",
            "班次号",
            "收银员",
            "客户",
            "销售时间",
            "应收金额",
            "退货金额",
            "净金额",
            "支付方式",
            "实收金额",
            "赊账金额",
            "找零",
            "支付参考号",
            "作废时间",
            "作废原因",
            "备注",
        ]
    )
    for sale in sales:
        payment = getattr(sale, "payment", None)
        return_amount = _money(return_amounts.get(sale.id, Decimal("0.00")))
        net_amount = (
            _money(sale.total_amount) - return_amount
            if sale.status == PosSale.Status.COMPLETED
            else Decimal("0.00")
        )
        sheet.append(
            [
                sale.sale_no,
                sale.src_bill_no,
                sale.get_status_display(),
                getattr(sale.warehouse, "name", ""),
                getattr(sale.shift, "shift_no", "") if sale.shift_id else "",
                getattr(sale.cashier, "username", "") if sale.cashier_id else "",
                _sale_customer_name(sale),
                _dt(sale.created_at),
                _money(sale.total_amount),
                return_amount,
                net_amount,
                payment.get_method_display() if payment else "",
                _money(payment.amount_received) if payment else Decimal("0.00"),
                _money(credit_amounts.get(sale.id, Decimal("0.00"))),
                _money(payment.change_amount) if payment else Decimal("0.00"),
                payment.reference_no if payment else "",
                _dt(sale.voided_at),
                sale.void_reason,
                sale.remark,
            ]
        )

    payment_sheet = workbook.create_sheet(SHEET_TITLES["payment_lines"])
    payment_sheet.append(
        [
            "销售单号",
            "小票号",
            "支付方式",
            "抵扣金额",
            "实收金额",
            "找零",
            "支付参考号",
            "状态",
            "收款时间",
        ]
    )
    payment_lines = (
        PosPaymentLine.objects.filter(sale_id__in=sale_ids)
        .select_related("sale")
        .order_by("-sale__created_at", "-sale_id", "id")
    )
    for line in payment_lines:
        payment_sheet.append(
            [
                line.sale.sale_no,
                line.sale.src_bill_no,
                line.get_method_display(),
                Decimal(line.amount),
                Decimal(line.amount_received),
                Decimal(line.change_amount),
                line.reference_no,
                line.get_status_display(),
                _dt(line.created_at),
            ]
        )

    line_sheet = workbook.create_sheet(SHEET_TITLES["sale_lines"])
    line_sheet.append(
        [
            "销售单号",
            "小票号",
            "状态",
            "班次号",
            "货主",
            "货主商品编码",
            "仓库SKU编码",
            "商品名称",
            "数量",
            "单价",
            "金额",
            "出库单号",
            "行号",
            "销售时间",
        ]
    )
    lines = (
        PosSaleLine.objects.filter(sale_id__in=sale_ids)
        .select_related(
            "sale",
            "sale__shift",
            "owner",
            "product",
            "outbound_order_line",
            "outbound_order_line__order",
        )
        .order_by("-sale__created_at", "-sale_id", "line_no")
    )
    for line in lines:
        order_line = line.outbound_order_line
        outbound_order = order_line.order if order_line else None
        line_sheet.append(
            [
                line.sale.sale_no,
                line.sale.src_bill_no,
                line.sale.get_status_display(),
                getattr(line.sale.shift, "shift_no", "") if line.sale.shift_id else "",
                getattr(line.owner, "name", ""),
                getattr(line.product, "code", ""),
                getattr(line.product, "sku", ""),
                getattr(line.product, "name", ""),
                Decimal(line.qty),
                Decimal(line.price),
                Decimal(line.amount),
                getattr(outbound_order, "order_no", "") if outbound_order else "",
                line.line_no,
                _dt(line.sale.created_at),
            ]
        )

    returns = (
        PosReturn.objects.filter(sale_id__in=sale_ids)
        .select_related("sale", "shift", "cashier")
        .order_by("-created_at", "-id")
    )
    return_ids = [return_order.id for return_order in returns]

    return_sheet = workbook.create_sheet(SHEET_TITLES["returns"])
    return_sheet.append(
        [
            "退货单号",
            "销售单号",
            "小票号",
            "状态",
            "班次号",
            "收银员",
            "退货金额",
            "退货原因",
            "退货时间",
        ]
    )
    for return_order in returns:
        return_sheet.append(
            [
                return_order.return_no,
                return_order.sale.sale_no,
                return_order.sale.src_bill_no,
                return_order.get_status_display(),
                (getattr(return_order.shift, "shift_no", "") if return_order.shift_id else ""),
                (getattr(return_order.cashier, "username", "") if return_order.cashier_id else ""),
                Decimal(return_order.total_amount),
                return_order.reason,
                _dt(return_order.created_at),
            ]
        )

    return_line_sheet = workbook.create_sheet(SHEET_TITLES["return_lines"])
    return_line_sheet.append(
        [
            "退货单号",
            "销售单号",
            "小票号",
            "状态",
            "货主",
            "货主商品编码",
            "仓库SKU编码",
            "商品名称",
            "数量",
            "单价",
            "金额",
            "原销售行号",
            "退货时间",
        ]
    )
    return_lines = (
        PosReturnLine.objects.filter(return_order_id__in=return_ids)
        .select_related(
            "return_order",
            "return_order__sale",
            "sale_line",
            "owner",
            "product",
        )
        .order_by("-return_order__created_at", "-return_order_id", "line_no")
    )
    for line in return_lines:
        return_line_sheet.append(
            [
                line.return_order.return_no,
                line.return_order.sale.sale_no,
                line.return_order.sale.src_bill_no,
                line.return_order.get_status_display(),
                getattr(line.owner, "name", ""),
                getattr(line.product, "code", ""),
                getattr(line.product, "sku", ""),
                getattr(line.product, "name", ""),
                Decimal(line.qty),
                Decimal(line.price),
                Decimal(line.amount),
                line.sale_line.line_no if line.sale_line_id else "",
                _dt(line.return_order.created_at),
            ]
        )

    refund_sheet = workbook.create_sheet(SHEET_TITLES["refunds"])
    refund_sheet.append(
        [
            "退货单号",
            "销售单号",
            "小票号",
            "退款方式",
            "退款金额",
            "退款参考号",
            "状态",
            "退款时间",
        ]
    )
    refunds = (
        PosRefund.objects.filter(return_order_id__in=return_ids)
        .select_related("return_order", "sale")
        .order_by("-return_order__created_at", "-return_order_id", "id")
    )
    for refund in refunds:
        refund_sheet.append(
            [
                refund.return_order.return_no,
                refund.sale.sale_no,
                refund.sale.src_bill_no,
                refund.get_method_display(),
                Decimal(refund.amount),
                refund.reference_no,
                refund.get_status_display(),
                _dt(refund.processed_at or refund.created_at),
            ]
        )

    if include_all_sheet:
        _prepend_all_information_sheet(workbook)
    return workbook


def build_pos_stats_export_workbook(payload, *, include_all_sheet=False):
    workbook = Workbook()
    summary = workbook.active
    summary.title = SHEET_TITLES["summary"]
    summary.append(["字段", "值"])
    _append_summary_rows(summary, payload.get("summary", {}), STATS_SUMMARY_FIELDS)
    summary.append(["开始日期", payload.get("period", {}).get("start_date", "")])
    summary.append(["结束日期", payload.get("period", {}).get("end_date", "")])

    _append_stats_sheet(
        workbook,
        SHEET_TITLES["payments"],
        [
            "支付方式",
            "销售单数",
            "退款单数",
            "还款单数",
            "销售金额",
            "退款金额",
            "还款金额",
            "净金额",
        ],
        [
            [
                _payment_method_label(row),
                row.get("sale_count", 0),
                row.get("refund_count", 0),
                row.get("repayment_count", 0),
                row.get("sale_amount", row.get("amount", "0.00")),
                row.get("refund_amount", "0.00"),
                row.get("repayment_amount", "0.00"),
                row.get("net_amount", row.get("amount", "0.00")),
            ]
            for row in payload.get("payments", [])
        ],
    )
    _append_stats_sheet(
        workbook,
        SHEET_TITLES["owners"],
        [
            "货主ID",
            "货主编码",
            "货主名称",
            "销售单数",
            "退货单数",
            "行数",
            "销售数量",
            "退货数量",
            "净数量",
            "销售金额",
            "退货金额",
            "净金额",
        ],
        [
            [
                row.get("owner_id", ""),
                row.get("owner_code", ""),
                row.get("owner_name", ""),
                row.get("sale_count", 0),
                row.get("return_count", 0),
                row.get("line_count", 0),
                row.get("sale_qty", row.get("qty", "0.000")),
                row.get("return_qty", "0.000"),
                row.get("net_qty", row.get("qty", "0.000")),
                row.get("sale_amount", row.get("amount", "0.00")),
                row.get("return_amount", "0.00"),
                row.get("net_amount", row.get("amount", "0.00")),
            ]
            for row in payload.get("owners", [])
        ],
    )
    _append_stats_sheet(
        workbook,
        SHEET_TITLES["products"],
        [
            "商品ID",
            "货主商品编码",
            "仓库SKU编码",
            "商品名称",
            "货主",
            "销售单数",
            "退货单数",
            "行数",
            "销售数量",
            "退货数量",
            "净数量",
            "销售金额",
            "退货金额",
            "净金额",
        ],
        [
            [
                row.get("product_id", ""),
                row.get("product_code", ""),
                row.get("product_sku", ""),
                row.get("product_name", ""),
                row.get("owner_name", ""),
                row.get("sale_count", 0),
                row.get("return_count", 0),
                row.get("line_count", 0),
                row.get("sale_qty", row.get("qty", "0.000")),
                row.get("return_qty", "0.000"),
                row.get("net_qty", row.get("qty", "0.000")),
                row.get("sale_amount", row.get("amount", "0.00")),
                row.get("return_amount", "0.00"),
                row.get("net_amount", row.get("amount", "0.00")),
            ]
            for row in payload.get("products", [])
        ],
    )
    _append_stats_sheet(
        workbook,
        SHEET_TITLES["cashiers"],
        [
            "收银员ID",
            "收银员",
            "销售单数",
            "完成单数",
            "作废单数",
            "退货单数",
            "完成金额",
            "作废金额",
            "退货金额",
            "净金额",
        ],
        [
            [
                row.get("cashier_id", ""),
                row.get("cashier_username", ""),
                row.get("sale_count", 0),
                row.get("completed_count", 0),
                row.get("voided_count", 0),
                row.get("return_count", 0),
                row.get("completed_amount", "0.00"),
                row.get("voided_amount", "0.00"),
                row.get("return_amount", "0.00"),
                row.get("net_amount", row.get("completed_amount", "0.00")),
            ]
            for row in payload.get("cashiers", [])
        ],
    )
    if include_all_sheet:
        _prepend_all_information_sheet(workbook)
    return workbook


def build_shift_export_workbook(shift, payload, *, include_all_sheet=False):
    workbook = Workbook()
    summary = workbook.active
    summary.title = SHEET_TITLES["shift"]
    summary.append(["字段", "值"])
    summary.append(["班次号", payload.get("shift_no", "")])
    summary.append(["状态", shift.get_status_display()])
    summary.append(["收银员", payload.get("cashier_username", "")])
    summary.append(["开班时间", payload.get("opened_at", "")])
    summary.append(["交班时间", payload.get("closed_at", "")])
    summary.append(["备注", payload.get("remark", "")])
    _append_summary_rows(summary, payload.get("summary", {}), SHIFT_SUMMARY_FIELDS)

    _append_stats_sheet(
        workbook,
        SHEET_TITLES["payments"],
        [
            "支付方式",
            "销售单数",
            "退款单数",
            "还款单数",
            "系统金额",
            "退款金额",
            "还款金额",
            "实点金额",
            "差异",
        ],
        [
            [
                _payment_method_label(row),
                row.get("sale_count", 0),
                row.get("refund_count", 0),
                row.get("repayment_count", 0),
                row.get("expected_amount", "0.00"),
                row.get("refund_amount", "0.00"),
                row.get("repayment_amount", "0.00"),
                row.get("actual_amount", "0.00"),
                row.get("difference", "0.00"),
            ]
            for row in payload.get("summary", {}).get("payments", [])
        ],
    )

    sales_workbook = build_sales_export_workbook(PosSale.objects.filter(shift=shift))
    for sheet in sales_workbook.worksheets:
        copied = workbook.create_sheet(sheet.title)
        for row in sheet.iter_rows(values_only=True):
            copied.append(list(row))
    repayments = (
        PosCustomerRepayment.objects.filter(shift=shift)
        .select_related("pos_customer", "customer", "cashier")
        .order_by("-created_at", "-id")
    )
    repayment_sheet = workbook.create_sheet(SHEET_TITLES["repayments"])
    repayment_sheet.append(
        [
            "还款单号",
            "客户",
            "收银员",
            "方式",
            "金额",
            "参考号",
            "状态",
            "备注",
            "时间",
        ]
    )
    for repayment in repayments:
        repayment_sheet.append(
            [
                repayment.repayment_no,
                _repayment_customer_name(repayment),
                (getattr(repayment.cashier, "username", "") if repayment.cashier_id else ""),
                repayment.get_method_display(),
                Decimal(repayment.amount),
                repayment.reference_no,
                repayment.get_status_display(),
                repayment.remark,
                _dt(repayment.created_at),
            ]
        )
    if include_all_sheet:
        _prepend_all_information_sheet(workbook)
    return workbook

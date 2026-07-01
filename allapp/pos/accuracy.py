from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from allapp.core.choices import InvTxType
from allapp.inventory.models import InventoryTransaction

from .models import (
    PosPayment,
    PosPaymentLine,
    PosRefund,
    PosReturn,
    PosReturnLine,
    PosSale,
    PosSaleLine,
    PosSaleOrder,
)

ZERO = Decimal("0")
MONEY_UNIT = Decimal("0.01")
QTY_UNIT = Decimal("0.0001")


def _money(value):
    return Decimal(value or ZERO).quantize(MONEY_UNIT)


def _qty(value):
    return Decimal(value or ZERO).quantize(QTY_UNIT)


def _parse_date(raw, name):
    try:
        return datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} 必须使用 YYYY-MM-DD 格式。")


def _current_date():
    current = timezone.now()
    if timezone.is_aware(current):
        return timezone.localtime(current).date()
    return current.date()


def _date_bounds(start_date, end_date):
    start_at = datetime.datetime.combine(start_date, datetime.time.min)
    end_at = datetime.datetime.combine(
        end_date + datetime.timedelta(days=1), datetime.time.min
    )
    if timezone.is_naive(start_at) and timezone.is_aware(timezone.now()):
        tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(start_at, tz)
        end_at = timezone.make_aware(end_at, tz)
    return start_at, end_at


def _parse_params(params):
    today = _current_date()
    start_raw = (params.get("start_date") or "").strip()
    end_raw = (params.get("end_date") or "").strip()
    start_date = _parse_date(start_raw, "start_date") if start_raw else today
    end_date = _parse_date(end_raw, "end_date") if end_raw else start_date
    if end_date < start_date:
        raise ValidationError("end_date 必须大于或等于 start_date。")
    return start_date, end_date


class PosAccuracyCollector:
    def __init__(self, *, issue_limit=200):
        self.issue_limit = issue_limit
        self.issues = []
        self.issue_count = 0
        self.checks = {}

    def register(self, code, label):
        self.checks.setdefault(code, {"code": code, "label": label, "issue_count": 0})

    def add_issue(
        self,
        *,
        code,
        label,
        object_type,
        object_id,
        object_no,
        message,
        expected="",
        actual="",
        severity="error",
    ):
        self.register(code, label)
        self.issue_count += 1
        self.checks[code]["issue_count"] += 1
        if len(self.issues) >= self.issue_limit:
            return
        self.issues.append(
            {
                "severity": severity,
                "code": code,
                "label": label,
                "object_type": object_type,
                "object_id": object_id,
                "object_no": object_no,
                "message": message,
                "expected": str(expected) if expected != "" else "",
                "actual": str(actual) if actual != "" else "",
            }
        )

    def rows(self):
        rows = []
        for row in self.checks.values():
            rows.append(
                {
                    **row,
                    "status": "failed" if row["issue_count"] else "passed",
                }
            )
        return rows


def _sum_money(queryset, field="amount"):
    return _money(queryset.aggregate(value=Sum(field))["value"])


def _sum_qty(queryset, field="qty"):
    return _qty(queryset.aggregate(value=Sum(field))["value"])


def _sale_no(sale):
    return sale.src_bill_no or sale.sale_no or str(sale.id)


def _return_no(return_order):
    return return_order.return_no or str(return_order.id)


def _check_sale_amounts(sales, collector):
    code = "sale_amount"
    label = "销售单金额"
    collector.register(code, label)
    for sale in sales:
        line_total = _sum_money(sale.lines.all())
        if _money(sale.total_amount) != line_total:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosSale",
                object_id=sale.id,
                object_no=_sale_no(sale),
                message="销售单应收金额与销售明细合计不一致。",
                expected=line_total,
                actual=_money(sale.total_amount),
            )


def _check_sale_payments(sales, collector):
    code = "sale_payment"
    label = "销售收款"
    collector.register(code, label)
    for sale in sales:
        payment = getattr(sale, "payment", None)
        sale_no = _sale_no(sale)
        if not payment:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosSale",
                object_id=sale.id,
                object_no=sale_no,
                message="销售单缺少 POS 收款记录。",
            )
            continue

        expected_status = (
            PosPayment.Status.VOIDED
            if sale.status == PosSale.Status.VOIDED
            else PosPayment.Status.PAID
        )
        if payment.status != expected_status:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosPayment",
                object_id=payment.id,
                object_no=sale_no,
                message="收款状态与销售单状态不一致。",
                expected=expected_status,
                actual=payment.status,
            )
        if _money(payment.amount_due) != _money(sale.total_amount):
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosPayment",
                object_id=payment.id,
                object_no=sale_no,
                message="收款应收金额与销售单金额不一致。",
                expected=_money(sale.total_amount),
                actual=_money(payment.amount_due),
            )

        payment_lines = sale.payment_lines.all()
        line_total = _sum_money(payment_lines)
        if _money(sale.total_amount) != line_total:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosSale",
                object_id=sale.id,
                object_no=sale_no,
                message="收款明细抵扣金额合计与销售单金额不一致。",
                expected=_money(sale.total_amount),
                actual=line_total,
            )
        for line in payment_lines:
            if line.status != expected_status:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="PosPaymentLine",
                    object_id=line.id,
                    object_no=sale_no,
                    message="收款明细状态与销售单状态不一致。",
                    expected=expected_status,
                    actual=line.status,
                )
            if line.method == PosPayment.Method.CREDIT:
                if _money(line.amount_received) != ZERO:
                    collector.add_issue(
                        code=code,
                        label=label,
                        object_type="PosPaymentLine",
                        object_id=line.id,
                        object_no=sale_no,
                        message="赊账明细实收金额应为 0。",
                        expected=ZERO,
                        actual=_money(line.amount_received),
                    )
                if _money(line.change_amount) != ZERO:
                    collector.add_issue(
                        code=code,
                        label=label,
                        object_type="PosPaymentLine",
                        object_id=line.id,
                        object_no=sale_no,
                        message="赊账明细找零金额应为 0。",
                        expected=ZERO,
                        actual=_money(line.change_amount),
                    )
            elif line.method == PosPayment.Method.CASH:
                expected_change = max(
                    _money(line.amount_received) - _money(line.amount), ZERO
                )
                if _money(line.change_amount) != expected_change:
                    collector.add_issue(
                        code=code,
                        label=label,
                        object_type="PosPaymentLine",
                        object_id=line.id,
                        object_no=sale_no,
                        message="现金找零金额不正确。",
                        expected=expected_change,
                        actual=_money(line.change_amount),
                    )
                if _money(line.amount_received) < _money(line.amount):
                    collector.add_issue(
                        code=code,
                        label=label,
                        object_type="PosPaymentLine",
                        object_id=line.id,
                        object_no=sale_no,
                        message="现金实收金额小于抵扣金额。",
                        expected=f">= {_money(line.amount)}",
                        actual=_money(line.amount_received),
                    )
            else:
                if _money(line.amount_received) != _money(line.amount):
                    collector.add_issue(
                        code=code,
                        label=label,
                        object_type="PosPaymentLine",
                        object_id=line.id,
                        object_no=sale_no,
                        message="非现金实收金额应等于抵扣金额。",
                        expected=_money(line.amount),
                        actual=_money(line.amount_received),
                    )


def _check_outbound_links(sales, collector):
    code = "sale_outbound"
    label = "销售出库"
    collector.register(code, label)
    for sale in sales:
        sale_no = _sale_no(sale)
        sale_lines = list(sale.lines.select_related("outbound_order_line", "product"))
        owner_amounts = defaultdict(lambda: ZERO)
        for line in sale_lines:
            owner_amounts[line.owner_id] += _money(line.amount)
            order_line = line.outbound_order_line
            if not order_line:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="PosSaleLine",
                    object_id=line.id,
                    object_no=sale_no,
                    message="销售明细缺少关联的出库明细。",
                )
                continue
            if order_line.product_id != line.product_id:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="OutboundOrderLine",
                    object_id=order_line.id,
                    object_no=sale_no,
                    message="出库明细商品与销售明细商品不一致。",
                    expected=line.product_id,
                    actual=order_line.product_id,
                )
            if _qty(order_line.base_qty) != _qty(line.qty):
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="OutboundOrderLine",
                    object_id=order_line.id,
                    object_no=sale_no,
                    message="出库明细数量与销售明细数量不一致。",
                    expected=_qty(line.qty),
                    actual=_qty(order_line.base_qty),
                )
            if _money(order_line.final_line_amount) != _money(line.amount):
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="OutboundOrderLine",
                    object_id=order_line.id,
                    object_no=sale_no,
                    message="出库明细金额与销售明细金额不一致。",
                    expected=_money(line.amount),
                    actual=_money(order_line.final_line_amount),
                )

        for link in sale.sale_orders.select_related("outbound_order"):
            expected_amount = _money(owner_amounts.get(link.owner_id, ZERO))
            order = link.outbound_order
            if _money(link.amount) != expected_amount:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="PosSaleOrder",
                    object_id=link.id,
                    object_no=sale_no,
                    message="POS 销售出库关联金额与该货主销售明细合计不一致。",
                    expected=expected_amount,
                    actual=_money(link.amount),
                )
            if _money(order.final_order_amount) != expected_amount:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="OutboundOrder",
                    object_id=order.id,
                    object_no=getattr(order, "order_no", sale_no),
                    message="出库单最终金额与 POS 货主销售金额不一致。",
                    expected=expected_amount,
                    actual=_money(order.final_order_amount),
                )
            if (order.src_bill_no or "") != sale.src_bill_no:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="OutboundOrder",
                    object_id=order.id,
                    object_no=getattr(order, "order_no", sale_no),
                    message="出库单源单号与 POS 小票号不一致。",
                    expected=sale.src_bill_no,
                    actual=order.src_bill_no or "",
                )
        link_owner_ids = set(sale.sale_orders.values_list("owner_id", flat=True))
        for owner_id in owner_amounts:
            if owner_id not in link_owner_ids:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="PosSale",
                    object_id=sale.id,
                    object_no=sale_no,
                    message="销售单缺少该货主的 POS 销售出库关联。",
                    expected=f"owner_id={owner_id}",
                    actual="",
                )
        for owner_id in link_owner_ids:
            if owner_id not in owner_amounts:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="PosSale",
                    object_id=sale.id,
                    object_no=sale_no,
                    message="销售单存在没有对应销售明细的 POS 销售出库关联。",
                    expected="",
                    actual=f"owner_id={owner_id}",
                )


def _tx_qty(*, src_model, src_id, tx_type):
    return _qty(
        InventoryTransaction.objects.filter(
            src_model=src_model, src_id=src_id, tx_type=tx_type
        ).aggregate(value=Sum("qty_delta"))["value"]
    )


def _check_inventory(sales, returns, collector):
    code = "inventory_flow"
    label = "库存流水"
    collector.register(code, label)
    for line in PosSaleLine.objects.filter(sale__in=sales).select_related("sale"):
        sale = line.sale
        sale_no = _sale_no(sale)
        issued_qty = _tx_qty(
            src_model="PosSaleLine", src_id=line.id, tx_type=InvTxType.ISSUE
        )
        if issued_qty != -_qty(line.qty):
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosSaleLine",
                object_id=line.id,
                object_no=sale_no,
                message="销售库存扣减数量与销售明细数量不一致。",
                expected=-_qty(line.qty),
                actual=issued_qty,
            )
        void_restore_qty = _tx_qty(
            src_model="PosSaleLine", src_id=line.id, tx_type=InvTxType.RECEIVE
        )
        expected_restore = (
            _qty(line.qty) if sale.status == PosSale.Status.VOIDED else ZERO
        )
        if void_restore_qty != expected_restore:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosSaleLine",
                object_id=line.id,
                object_no=sale_no,
                message="销售作废回补库存数量不正确。",
                expected=expected_restore,
                actual=void_restore_qty,
            )

    for line in PosReturnLine.objects.filter(return_order__in=returns).select_related(
        "return_order"
    ):
        return_no = _return_no(line.return_order)
        restored_qty = _tx_qty(
            src_model="PosReturnLine", src_id=line.id, tx_type=InvTxType.RECEIVE
        )
        if restored_qty != _qty(line.qty):
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosReturnLine",
                object_id=line.id,
                object_no=return_no,
                message="退货库存回补数量与退货明细数量不一致。",
                expected=_qty(line.qty),
                actual=restored_qty,
            )


def _check_returns(returns, collector):
    code = "return_refund"
    label = "退货退款"
    collector.register(code, label)
    returned_by_sale_line = defaultdict(lambda: ZERO)
    for return_order in returns:
        return_no = _return_no(return_order)
        line_total = _sum_money(return_order.lines.all())
        if _money(return_order.total_amount) != line_total:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosReturn",
                object_id=return_order.id,
                object_no=return_no,
                message="退货单金额与退货明细合计不一致。",
                expected=line_total,
                actual=_money(return_order.total_amount),
            )
        refund_total = _sum_money(
            return_order.refunds.filter(status=PosRefund.Status.REFUNDED)
        )
        if _money(return_order.total_amount) != refund_total:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosReturn",
                object_id=return_order.id,
                object_no=return_no,
                message="已退款金额合计与退货单金额不一致。",
                expected=_money(return_order.total_amount),
                actual=refund_total,
            )
        for line in return_order.lines.select_related("sale_line"):
            returned_by_sale_line[line.sale_line_id] += _qty(line.qty)
            if line.product_id != line.sale_line.product_id:
                collector.add_issue(
                    code=code,
                    label=label,
                    object_type="PosReturnLine",
                    object_id=line.id,
                    object_no=return_no,
                    message="退货商品与原销售明细商品不一致。",
                    expected=line.sale_line.product_id,
                    actual=line.product_id,
                )

    sale_line_ids = list(returned_by_sale_line.keys())
    cumulative_returns = (
        PosReturnLine.objects.filter(
            sale_line_id__in=sale_line_ids,
            return_order__status=PosReturn.Status.COMPLETED,
        )
        .values("sale_line_id")
        .annotate(qty=Sum("qty"))
    )
    returned_by_sale_line = {
        row["sale_line_id"]: _qty(row["qty"]) for row in cumulative_returns
    }
    sale_lines = PosSaleLine.objects.filter(id__in=sale_line_ids)
    sale_line_qty = {line.id: _qty(line.qty) for line in sale_lines}
    for sale_line_id, returned_qty in returned_by_sale_line.items():
        original_qty = sale_line_qty.get(sale_line_id, ZERO)
        if returned_qty > original_qty:
            collector.add_issue(
                code=code,
                label=label,
                object_type="PosSaleLine",
                object_id=sale_line_id,
                object_no=str(sale_line_id),
                message="累计退货数量超过原销售数量。",
                expected=f"<= {original_qty}",
                actual=returned_qty,
            )


def reconcile_pos_accuracy(*, user, params):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        raise ValidationError("当前用户未绑定仓库(warehouse)，无法执行 POS 数据校验。")

    start_date, end_date = _parse_params(params)
    start_at, end_at = _date_bounds(start_date, end_date)
    collector = PosAccuracyCollector(issue_limit=200)

    sales = (
        PosSale.objects.filter(
            warehouse_id=warehouse_id,
            created_at__gte=start_at,
            created_at__lt=end_at,
        )
        .select_related("payment", "warehouse", "shift")
        .prefetch_related("payment_lines", "lines", "sale_orders__outbound_order")
        .order_by("id")
    )
    returns = (
        PosReturn.objects.filter(
            warehouse_id=warehouse_id,
            created_at__gte=start_at,
            created_at__lt=end_at,
            status=PosReturn.Status.COMPLETED,
        )
        .select_related("sale", "shift")
        .prefetch_related("lines__sale_line", "refunds")
        .order_by("id")
    )
    sales = list(sales)
    returns = list(returns)

    _check_sale_amounts(sales, collector)
    _check_sale_payments(sales, collector)
    _check_outbound_links(sales, collector)
    _check_inventory(sales, returns, collector)
    _check_returns(returns, collector)

    status = "passed" if collector.issue_count == 0 else "failed"
    return {
        "status": status,
        "checked_at": timezone.now().isoformat(),
        "scope": {"warehouse_id": warehouse_id},
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "summary": {
            "sale_count": len(sales),
            "return_count": len(returns),
            "check_count": len(collector.checks),
            "issue_count": collector.issue_count,
            "shown_issue_count": len(collector.issues),
            "truncated": collector.issue_count > len(collector.issues),
        },
        "checks": collector.rows(),
        "issues": collector.issues,
    }

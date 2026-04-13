# allapp/billing/services/invoice.py
"""
开票模块 — 从已关账（CLOSED）的 BillingPeriod 生成 Bill（发票/结算单）。

流程概览:
    1. 验证 period 状态为 CLOSED，且尚未生成过发票
    2. [可选] 执行数据对账门控，确保计费数据一致
    3. 查询该 period 下所有 LOCKED 状态的 accrual
    4. 批量创建 BillLine（一条 accrual 对应一条 BillLine）
    5. 批量更新 accrual 状态为 INVOICED
    6. 汇总计算 Bill 的 subtotal / tax_total / total
    7. Bill 状态置为 ISSUED，Period 状态置为 INVOICED

性能优化:
    使用 bulk_create（BillLine）和 QuerySet.update（Accrual 状态），
    将原先 N 条 accrual → 2N 次 DB 写入 优化为 2 次批量操作。

调用方:
    - views.py: BillingPeriodViewSet.invoice action
    - admin.py: BillingPeriodAdmin.invoice_view
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from allapp.billing.enums import AccrualStatus, BillStatus, PeriodStatus
from allapp.billing.models import Bill, BillLine, BillingAccrual, BillingPeriod

from ._common import _q, logger
from ._reconciliation import _billing_accuracy_gate_enabled, _ensure_reconciliation_for_period


@transaction.atomic
def generate_invoice_for_period(period: BillingPeriod, invoice_no: str, issue_date=None, due_date=None) -> Bill:
    """
    从已关账的 period 生成发票/结算单。

    参数:
        period: 已关账（CLOSED）的 BillingPeriod 实例
        invoice_no: 发票号（如 "INV-2026-03-1-0001"），需全局唯一
        issue_date: 开票日期，默认今天
        due_date: 到期日期，可选

    返回:
        生成的 Bill 实例（status=ISSUED）

    异常:
        ValueError: period 不是 CLOSED / 已有发票 / 无可开票的 accrual
        BillingAccuracyGateError: 数据对账未通过

    注意:
        整个函数在 @transaction.atomic 中执行，任何异常都会回滚全部操作。
    """
    # select_for_update 锁定 period，防止并发开票
    # 保留 original_period 引用，函数末尾同步更新调用方持有的对象状态
    original_period = period
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)

    # ---- 前置校验 ----
    if period.status != PeriodStatus.CLOSED:
        raise ValueError("Only closed periods can be invoiced.")
    if Bill.objects.filter(period=period).exclude(status=BillStatus.VOID).exists():
        raise ValueError("Invoice already exists for this period.")

    # 数据对账门控（可通过 settings 关闭）
    if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_INVOICE_ENABLED"):
        _ensure_reconciliation_for_period(stage="开票", period=period)

    # ---- 查询待开票的 accrual ----
    accs = list(
        BillingAccrual.objects
        .filter(period=period, status=AccrualStatus.LOCKED)
        .select_related("rule")
        .order_by("service_date", "charge_type", "id")
    )
    if not accs:
        raise ValueError("No locked accruals to invoice.")

    # ---- 创建 Bill 主记录 ----
    bill = Bill.objects.create(
        owner=period.owner, warehouse=period.warehouse, period=period,
        invoice_no=invoice_no, issue_date=issue_date or timezone.now().date(),
        due_date=due_date, currency=period.currency
    )

    # ---- 批量创建 BillLine（1 次 INSERT 代替 N 次） ----
    bill_lines = [
        BillLine(
            bill=bill, accrual=a, charge_type=a.charge_type, service_date=a.service_date,
            quantity=a.quantity, unit_price=a.unit_price, amount=a.amount, tax_amount=a.tax_amount,
            description=f"{a.charge_type} {a.service_date}"
        )
        for a in accs
    ]
    BillLine.objects.bulk_create(bill_lines)

    # ---- 汇总金额 ----
    subtotal = sum(a.amount for a in accs)
    tax_total = sum(a.tax_amount for a in accs)

    # ---- 批量更新 accrual 状态为 INVOICED（1 次 UPDATE 代替 N 次） ----
    acc_ids = [a.id for a in accs]
    BillingAccrual.objects.filter(id__in=acc_ids).update(status=AccrualStatus.INVOICED)

    # ---- 更新 Bill 总计和状态 ----
    bill.subtotal = _q(subtotal, "0.01")
    bill.tax_total = _q(tax_total, "0.01")
    bill.total = _q(subtotal + tax_total, "0.01")
    bill.status = BillStatus.ISSUED
    bill.save(update_fields=["subtotal", "tax_total", "total", "status"])

    # ---- 更新 Period 状态 ----
    period.status = PeriodStatus.INVOICED
    period.save(update_fields=["status"])
    # 同步调用方持有的 period 对象（可能是不同的 Python 实例）
    if original_period is not period:
        original_period.status = period.status

    logger.info(
        "generate_invoice_for_period: invoice_no=%s lines=%d total=%s",
        invoice_no, len(bill_lines), bill.total,
    )
    return bill

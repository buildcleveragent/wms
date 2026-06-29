from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DateField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from allapp.core.choices import InvTxType, ZoneType
from allapp.inventory.models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
)
from allapp.outbound.enums import PricingStatus
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product

from .models import (
    PosAuditLog,
    PosPayment,
    PosPaymentLine,
    PosRefund,
    PosReturn,
    PosReturnLine,
    PosSale,
    PosSaleLine,
    PosSaleOrder,
    PosShift,
)
from .shift_services import current_shift_for_user

ZERO = Decimal("0")
QTY4 = Decimal("0.0001")
QTY3 = Decimal("0.001")
MONEY = Decimal("0.01")
PRICE = Decimal("0.0001")
POS_ORDER_MEMO_PREFIX = "[POS]"
POS_ORDER_CLOSE_REASON = "POS即时销售完成"


def _decimal(value, default=ZERO):
    if value in (None, ""):
        return default
    return Decimal(str(value))


def _q4(value):
    return _decimal(value).quantize(QTY4, rounding=ROUND_HALF_UP)


def _q3(value):
    return _decimal(value).quantize(QTY3, rounding=ROUND_HALF_UP)


def _money(value):
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _price(value):
    return _decimal(value).quantize(PRICE, rounding=ROUND_HALF_UP)


def _make_sale_no(now=None):
    now = now or timezone.now()
    for _ in range(8):
        sale_no = f"POS{now:%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
        if not PosSale.objects.filter(sale_no=sale_no).exists():
            return sale_no
    raise ValidationError("无法生成 POS 销售单号，请重试。")


def _make_return_no(now=None):
    now = now or timezone.now()
    for _ in range(8):
        return_no = f"PR{now:%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
        if not PosReturn.objects.filter(return_no=return_no).exists():
            return return_no
    raise ValidationError("无法生成 POS 退货单号，请重试。")


def _error(field, message):
    raise ValidationError({field: message})


def _normalize_stock_zone_type(value):
    if value in (None, ""):
        return None
    try:
        zone_type = int(value)
    except (TypeError, ValueError):
        _error("stock_zone_type", "库存范围参数无效。")
    valid_zone_types = {choice.value for choice in ZoneType}
    if zone_type not in valid_zone_types:
        _error("stock_zone_type", "库存范围参数无效。")
    return zone_type


def _pos_order_memo(remark):
    remark = (remark or "").strip()
    if not remark:
        return POS_ORDER_MEMO_PREFIX
    return f"{POS_ORDER_MEMO_PREFIX} {remark}"[:100]


def _idempotency_fingerprint(
    *,
    warehouse_id,
    customer_id,
    src_bill_no,
    remark,
    items,
    payment,
    payments,
    stock_zone_type,
):
    payment = payment or {}
    payments = payments or []
    canonical_items = [
        {
            "product_id": int(item["product_id"]),
            "qty": str(_q3(item["qty"])),
            "price": str(_price(item["price"])),
        }
        for item in items
    ]
    canonical = {
        "warehouse_id": warehouse_id,
        "customer_id": customer_id or None,
        "src_bill_no": (src_bill_no or "").strip(),
        "remark": (remark or "").strip(),
        "stock_zone_type": stock_zone_type,
        "payment": {
            "method": (payment.get("method") or "").strip().upper(),
            "amount_received": (
                str(_money(payment.get("amount_received")))
                if payment.get("amount_received") not in (None, "")
                else ""
            ),
            "reference_no": (payment.get("reference_no") or "").strip(),
        },
        "payments": [
            {
                "method": (item.get("method") or "").strip().upper(),
                "amount": str(_money(item.get("amount"))),
                "amount_received": (
                    str(_money(item.get("amount_received")))
                    if item.get("amount_received") not in (None, "")
                    else ""
                ),
                "reference_no": (item.get("reference_no") or "").strip(),
            }
            for item in payments
        ],
        "items": canonical_items,
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _return_fingerprint(*, sale_id, reason, lines, refunds):
    canonical = {
        "sale_id": int(sale_id),
        "reason": (reason or "").strip(),
        "lines": [
            {
                "sale_line_id": int(item["sale_line_id"]),
                "qty": str(_q3(item["qty"])),
            }
            for item in lines
        ],
        "refunds": [
            {
                "method": (item.get("method") or "").strip().upper(),
                "amount": str(_money(item.get("amount"))),
                "reference_no": (item.get("reference_no") or "").strip(),
                "status": (item.get("status") or PosRefund.Status.REFUNDED)
                .strip()
                .upper(),
            }
            for item in refunds
        ],
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _audit(
    *, action, user, sale=None, return_order=None, shift=None, reason="", metadata=None
):
    return PosAuditLog.objects.create(
        action=action,
        sale=sale,
        return_order=return_order,
        shift=shift,
        actor=user if user and user.is_authenticated else None,
        reason=(reason or "").strip(),
        metadata=metadata or {},
    )


def _cash_customer(owner_id, user):
    Customer = apps.get_model("baseinfo", "Customer")
    customer = (
        Customer.objects.filter(owner_id=owner_id, code="CASH").order_by("id").first()
    )
    if customer:
        return customer
    return Customer.objects.create(
        owner_id=owner_id, code="CASH", name="散客", salesperson=user
    )


def _load_products(items):
    product_ids = [item["product_id"] for item in items]
    products = {
        product.id: product
        for product in Product.objects.filter(
            id__in=product_ids, is_active=True
        ).select_related("owner", "base_uom")
    }
    missing = [product_id for product_id in product_ids if product_id not in products]
    if missing:
        _error("items", f"商品不存在或已停用：{missing}")
    return products


def _validate_prices_and_shape(items, products):
    normalized = []
    qty_by_product = defaultdict(lambda: ZERO)
    for raw in items:
        product = products[raw["product_id"]]
        qty = _q3(raw["qty"])
        price = _price(raw["price"])
        if qty <= 0:
            _error("items", "商品数量必须大于 0。")

        min_price = getattr(product, "min_price", None)
        if min_price is not None and price < Decimal(min_price):
            _error("price", f"{product.code} 成交价不能低于最低价 {min_price}。")

        base_price = getattr(product, "price", None)
        max_discount = getattr(product, "max_discount", None)
        if base_price is not None and max_discount is not None:
            discount_rate = (Decimal("100") - Decimal(max_discount)) / Decimal("100")
            lowest = (Decimal(base_price) * discount_rate).quantize(PRICE)
            if price < lowest:
                _error(
                    "price",
                    f"{product.code} 成交价超过最高折扣限制，最低可售 {lowest}。",
                )

        amount = _money(qty * price)
        qty_by_product[product.id] += qty
        normalized.append(
            {
                "product": product,
                "qty": qty,
                "price": price,
                "amount": amount,
            }
        )
    return normalized, qty_by_product


def _available_qty(owner_id, warehouse_id, product_id, *, zone_type=None):
    filters = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "is_active": True,
        "available_qty__gt": 0,
    }
    if zone_type is not None:
        filters["zone_type"] = zone_type
    result = InventoryDetail.objects.filter(**filters).aggregate(
        total=Sum("available_qty")
    )
    return result["total"] or ZERO


def _validate_stock(qty_by_product, products, warehouse_id, *, zone_type=None):
    for product_id, required_qty in qty_by_product.items():
        product = products[product_id]
        available = _available_qty(
            product.owner_id, warehouse_id, product_id, zone_type=zone_type
        )
        if available < required_qty:
            _error(
                "items",
                f"{product.code} 可售库存不足，可售 {available}，需要 {required_qty}。",
            )


def _normalize_payment(payment, total_amount):
    payment = payment or {}
    method = (payment.get("method") or "").strip().upper()
    valid_methods = {choice.value for choice in PosPayment.Method}
    if method not in valid_methods:
        _error("payment", "请选择有效的支付方式。")

    if method == PosPayment.Method.CASH:
        received = _money(payment.get("amount_received"))
        if received < total_amount:
            _error("payment", "现金实收金额不能小于应收金额。")
        change = _money(received - total_amount)
    else:
        received = _money(payment.get("amount_received", total_amount))
        if received != total_amount:
            _error("payment", "非现金支付实收金额必须等于应收金额。")
        change = ZERO.quantize(MONEY)

    return {
        "method": method,
        "amount_due": total_amount,
        "amount_received": received,
        "change_amount": change,
        "reference_no": (payment.get("reference_no") or "").strip(),
    }


def _normalize_payment_lines(payment, payments, total_amount):
    payments = payments or []
    raw_lines = list(payments)
    if not raw_lines:
        payment_data = _normalize_payment(payment, total_amount)
        raw_lines = [
            {
                "method": payment_data["method"],
                "amount": total_amount,
                "amount_received": payment_data["amount_received"],
                "reference_no": payment_data["reference_no"],
            }
        ]

    valid_methods = {choice.value for choice in PosPayment.Method}
    result = []
    paid_amount = ZERO.quantize(MONEY)
    for raw in raw_lines:
        method = (raw.get("method") or "").strip().upper()
        if method not in valid_methods:
            _error("payments", "请选择有效的支付方式。")
        amount = _money(raw.get("amount", raw.get("amount_received")))
        if amount <= 0:
            _error("payments", "支付金额必须大于 0。")
        if method == PosPayment.Method.CASH:
            received = _money(raw.get("amount_received", amount))
            if received < amount:
                _error("payments", "现金实收金额不能小于现金抵扣金额。")
            change = _money(received - amount)
        else:
            received = _money(raw.get("amount_received", amount))
            if received != amount:
                _error("payments", "非现金支付实收金额必须等于抵扣金额。")
            change = ZERO.quantize(MONEY)
        paid_amount = _money(paid_amount + amount)
        result.append(
            {
                "method": method,
                "amount": amount,
                "amount_received": received,
                "change_amount": change,
                "reference_no": (raw.get("reference_no") or "").strip(),
            }
        )

    if paid_amount != total_amount:
        _error(
            "payments", f"支付明细合计 {paid_amount} 必须等于应收金额 {total_amount}。"
        )
    return result


def _payment_summary_from_lines(payment_lines, total_amount):
    if len(payment_lines) == 1:
        method = payment_lines[0]["method"]
        reference_no = payment_lines[0]["reference_no"]
    else:
        method = PosPayment.Method.OTHER
        reference_no = "MULTI"
    return {
        "method": method,
        "amount_due": total_amount,
        "amount_received": _money(
            sum((line["amount_received"] for line in payment_lines), ZERO)
        ),
        "change_amount": _money(
            sum((line["change_amount"] for line in payment_lines), ZERO)
        ),
        "reference_no": reference_no,
    }


def _normalize_refunds(refunds, total_amount):
    refunds = refunds or []
    if not refunds:
        _error("refunds", "退货必须填写退款方式和退款金额。")
    valid_methods = {choice.value for choice in PosPayment.Method}
    valid_statuses = {choice.value for choice in PosRefund.Status}
    result = []
    refund_total = ZERO.quantize(MONEY)
    for raw in refunds:
        method = (raw.get("method") or "").strip().upper()
        if method not in valid_methods:
            _error("refunds", "请选择有效的退款方式。")
        status = (raw.get("status") or PosRefund.Status.REFUNDED).strip().upper()
        if status not in valid_statuses:
            _error("refunds", "请选择有效的退款状态。")
        amount = _money(raw.get("amount"))
        if amount <= 0:
            _error("refunds", "退款金额必须大于 0。")
        refund_total = _money(refund_total + amount)
        result.append(
            {
                "method": method,
                "amount": amount,
                "reference_no": (raw.get("reference_no") or "").strip(),
                "status": status,
            }
        )
    if refund_total != total_amount:
        _error(
            "refunds", f"退款明细合计 {refund_total} 必须等于退货金额 {total_amount}。"
        )
    return result


def _refresh_summaries(pairs):
    for owner_id, product_id in {pair for pair in pairs if pair and all(pair)}:
        aggregates = InventoryDetail.objects.filter(
            owner_id=owner_id,
            product_id=product_id,
            is_active=True,
        ).aggregate(
            onhand=Sum("onhand_qty"),
            allocated=Sum("allocated_qty"),
            locked=Sum("locked_qty"),
            damaged=Sum("damaged_qty"),
        )
        product = Product.objects.select_related("base_uom").get(pk=product_id)
        summary = (
            InventorySummary.objects.select_for_update()
            .filter(owner_id=owner_id, product_id=product_id, is_active=True)
            .first()
        )
        if summary is None:
            summary = InventorySummary(owner_id=owner_id, product_id=product_id)
        summary.base_unit = product.base_uom.code if product.base_uom_id else ""
        summary.onhand_qty = aggregates["onhand"] or ZERO
        summary.allocated_qty = aggregates["allocated"] or ZERO
        summary.locked_qty = aggregates["locked"] or ZERO
        summary.damaged_qty = aggregates["damaged"] or ZERO
        summary.available_qty = (
            summary.onhand_qty
            - summary.allocated_qty
            - summary.locked_qty
            - summary.damaged_qty
        )
        summary.save()


def _fefo_details(owner_id, warehouse_id, product_id, *, zone_type=None):
    filters = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "is_active": True,
        "available_qty__gt": 0,
    }
    if zone_type is not None:
        filters["zone_type"] = zone_type

    expiry_sort = Coalesce(
        "expiry_date",
        Value(date(9999, 12, 31), output_field=DateField()),
    )
    return (
        InventoryDetail.objects.select_for_update(skip_locked=True)
        .filter(**filters)
        .order_by(expiry_sort.asc(), "-onhand_qty", "id")
    )


def _insert_inventory_tx(*, tx_type, detail, qty_delta, sale_line, sale_no, memo, now):
    return InventoryTransaction.objects.create(
        tx_type=tx_type,
        owner_id=detail.owner_id,
        product_id=detail.product_id,
        warehouse_id=detail.warehouse_id,
        location_id=detail.location_id,
        subwarehouse_id=detail.subwarehouse_id,
        zone_type=detail.zone_type,
        batch_no=detail.batch_no or "",
        production_date=detail.production_date,
        expiry_date=detail.expiry_date,
        serial_no=detail.serial_no or "",
        qty_delta=_q4(qty_delta),
        src_model="PosSaleLine",
        src_id=sale_line.id,
        src_line_id=detail.id,
        src_no=sale_no,
        memo=memo,
        posted_at=now,
        posting_batch=sale_no[:40],
    )


def _deduct_stock_for_line(sale_line, warehouse_id, sale_no, now, *, zone_type=None):
    remaining = _q4(sale_line.qty)
    touched = set()
    for detail in _fefo_details(
        sale_line.owner_id, warehouse_id, sale_line.product_id, zone_type=zone_type
    ):
        if remaining <= 0:
            break
        available = _q4(detail.available_qty)
        if available <= 0:
            continue
        take = min(available, remaining)
        if _q4(detail.onhand_qty) < take:
            take = min(_q4(detail.onhand_qty), take)
        if take <= 0:
            continue

        detail.onhand_qty = _q4(detail.onhand_qty - take)
        detail.available_qty = _q4(
            detail.onhand_qty
            - detail.allocated_qty
            - detail.locked_qty
            - detail.damaged_qty
        )
        detail.save()
        _insert_inventory_tx(
            tx_type=InvTxType.ISSUE,
            detail=detail,
            qty_delta=-take,
            sale_line=sale_line,
            sale_no=sale_no,
            memo="POS_SALE",
            now=now,
        )
        touched.add((detail.owner_id, detail.product_id))
        remaining = _q4(remaining - take)

    if remaining > 0:
        product = sale_line.product
        _error("items", f"{product.code} 可售库存不足，缺口 {remaining}。")
    _refresh_summaries(touched)


def _restore_stock_from_sale(sale, user, reason):
    now = timezone.now()
    batch_no = f"{sale.sale_no}-VOID"
    line_ids = list(sale.lines.values_list("id", flat=True))
    issue_txs = (
        InventoryTransaction.objects.select_for_update()
        .filter(src_model="PosSaleLine", src_id__in=line_ids, tx_type=InvTxType.ISSUE)
        .order_by("id")
    )
    touched = set()
    line_map = {line.id: line for line in sale.lines.select_related("product")}
    for tx in issue_txs:
        detail = InventoryDetail.objects.select_for_update().get(pk=tx.src_line_id)
        qty = _q4(-tx.qty_delta)
        detail.onhand_qty = _q4(detail.onhand_qty + qty)
        detail.available_qty = _q4(
            detail.onhand_qty
            - detail.allocated_qty
            - detail.locked_qty
            - detail.damaged_qty
        )
        detail.save()
        sale_line = line_map[tx.src_id]
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner_id=detail.owner_id,
            product_id=detail.product_id,
            warehouse_id=detail.warehouse_id,
            location_id=detail.location_id,
            subwarehouse_id=detail.subwarehouse_id,
            zone_type=detail.zone_type,
            batch_no=detail.batch_no or "",
            production_date=detail.production_date,
            expiry_date=detail.expiry_date,
            serial_no=detail.serial_no or "",
            qty_delta=qty,
            src_model="PosSaleLine",
            src_id=sale_line.id,
            src_line_id=detail.id,
            src_no=sale.sale_no,
            memo=(reason or "POS_VOID")[:255],
            posted_at=now,
            posting_batch=batch_no[:40],
        )
        touched.add((detail.owner_id, detail.product_id))
    _refresh_summaries(touched)


def _returned_qty_by_sale_line(sale_line_ids):
    rows = (
        PosReturnLine.objects.filter(
            sale_line_id__in=sale_line_ids,
            return_order__status=PosReturn.Status.COMPLETED,
        )
        .values("sale_line_id")
        .annotate(qty=Sum("qty"))
    )
    return {row["sale_line_id"]: row["qty"] or ZERO for row in rows}


def _restored_qty_by_detail_for_sale_line(sale_line, *, exclude_return_line_id=None):
    prior_return_lines = PosReturnLine.objects.filter(
        sale_line=sale_line,
        return_order__status=PosReturn.Status.COMPLETED,
    )
    if exclude_return_line_id:
        prior_return_lines = prior_return_lines.exclude(pk=exclude_return_line_id)
    prior_ids = list(prior_return_lines.values_list("id", flat=True))
    if not prior_ids:
        return {}
    rows = (
        InventoryTransaction.objects.filter(
            src_model="PosReturnLine",
            src_id__in=prior_ids,
            tx_type=InvTxType.RECEIVE,
        )
        .values("src_line_id")
        .annotate(qty=Sum("qty_delta"))
    )
    return {row["src_line_id"]: row["qty"] or ZERO for row in rows}


def _restore_stock_for_return_line(return_line, return_no, now):
    sale_line = return_line.sale_line
    issue_txs = (
        InventoryTransaction.objects.select_for_update()
        .filter(src_model="PosSaleLine", src_id=sale_line.id, tx_type=InvTxType.ISSUE)
        .order_by("id")
    )
    restored_by_detail = _restored_qty_by_detail_for_sale_line(
        sale_line, exclude_return_line_id=return_line.id
    )
    remaining = _q4(return_line.qty)
    touched = set()
    for tx in issue_txs:
        if remaining <= 0:
            break
        issued_qty = _q4(-tx.qty_delta)
        already_restored = _q4(restored_by_detail.get(tx.src_line_id, ZERO))
        restorable = _q4(issued_qty - already_restored)
        if restorable <= 0:
            continue
        take = min(restorable, remaining)
        detail = InventoryDetail.objects.select_for_update().get(pk=tx.src_line_id)
        detail.onhand_qty = _q4(detail.onhand_qty + take)
        detail.available_qty = _q4(
            detail.onhand_qty
            - detail.allocated_qty
            - detail.locked_qty
            - detail.damaged_qty
        )
        detail.save()
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner_id=detail.owner_id,
            product_id=detail.product_id,
            warehouse_id=detail.warehouse_id,
            location_id=detail.location_id,
            subwarehouse_id=detail.subwarehouse_id,
            zone_type=detail.zone_type,
            batch_no=detail.batch_no or "",
            production_date=detail.production_date,
            expiry_date=detail.expiry_date,
            serial_no=detail.serial_no or "",
            qty_delta=take,
            src_model="PosReturnLine",
            src_id=return_line.id,
            src_line_id=detail.id,
            src_no=return_no,
            memo="POS_RETURN",
            posted_at=now,
            posting_batch=return_no[:40],
        )
        touched.add((detail.owner_id, detail.product_id))
        remaining = _q4(remaining - take)

    if remaining > 0:
        _error(
            "items", f"{sale_line.product.code} 原扣减库存不足，无法回补 {remaining}。"
        )
    _refresh_summaries(touched)


def _receipt_line(line):
    product = line.product
    return {
        "line_no": line.line_no,
        "owner_id": line.owner_id,
        "owner_name": getattr(line.owner, "name", ""),
        "product_id": product.id,
        "code": product.code,
        "product_code": product.code,
        "sku": product.sku,
        "name": product.name,
        "product_name": product.name,
        "qty": str(line.qty),
        "price": str(line.price),
        "amount": str(line.amount),
    }


def _receipt_customer(customer):
    if not customer:
        return None

    full_address = _full_address(customer)
    return {
        "id": customer.id,
        "code": customer.code or "",
        "name": customer.name or "",
        "phone": customer.phone or customer.mobile or "",
        "mobile": customer.mobile or "",
        "address": customer.address or "",
        "full_address": full_address,
        "bank_name": customer.bank_name or "",
        "bank_account": customer.bank_account or "",
    }


def _full_address(entity):
    address_parts = [
        getattr(entity, "province", "") or "",
        getattr(entity, "city", "") or "",
        getattr(entity, "district", "") or "",
        getattr(entity, "street", "") or "",
        getattr(entity, "address", "") or "",
    ]
    return "".join(part for part in address_parts if part)


def _customer_cumulative_debt(sale):
    if not sale.selected_customer_id:
        return ZERO
    totals = (
        PosSale.objects.filter(
            selected_customer_id=sale.selected_customer_id,
            warehouse_id=sale.warehouse_id,
        )
        .exclude(status=PosSale.Status.VOIDED)
        .aggregate(
            total_amount=Sum("total_amount"),
            amount_received=Sum("payment__amount_received"),
        )
    )
    return _money((totals["total_amount"] or ZERO) - (totals["amount_received"] or ZERO))


def build_receipt(sale):
    payment = getattr(sale, "payment", None)
    payment_lines = [
        {
            "method": line.method,
            "amount": str(line.amount),
            "amount_received": str(line.amount_received),
            "change_amount": str(line.change_amount),
            "reference_no": line.reference_no,
            "status": line.status,
        }
        for line in sale.payment_lines.order_by("id")
    ]
    sale_lines = list(sale.lines.select_related("product").order_by("line_no"))
    orders = [
        link.outbound_order
        for link in sale.sale_orders.select_related("outbound_order", "owner").order_by(
            "owner_id"
        )
    ]
    return {
        "sale_no": sale.sale_no,
        "src_bill_no": sale.src_bill_no,
        "status": sale.status,
        "warehouse_id": sale.warehouse_id,
        "shift": {
            "id": sale.shift_id,
            "shift_no": getattr(sale.shift, "shift_no", "") if sale.shift_id else "",
        },
        "cashier": getattr(sale.cashier, "username", "") if sale.cashier_id else "",
        "customer": _receipt_customer(getattr(sale, "selected_customer", None)),
        "total_amount": str(sale.total_amount),
        "cumulative_debt": str(_customer_cumulative_debt(sale)),
        "payment": {
            "method": payment.method if payment else "",
            "amount_due": str(payment.amount_due) if payment else "0.00",
            "amount_received": str(payment.amount_received) if payment else "0.00",
            "change_amount": str(payment.change_amount) if payment else "0.00",
            "reference_no": payment.reference_no if payment else "",
            "status": payment.status if payment else "",
        },
        "payment_lines": payment_lines,
        "lines": [
            _receipt_line(line)
            for line in sale_lines
        ],
        "orders": [
            {"id": order.id, "order_no": order.order_no, "owner_id": order.owner_id}
            for order in orders
        ],
        "created_at": sale.created_at.isoformat() if sale.created_at else "",
    }


def result_for_sale(sale):
    sale = (
        PosSale.objects.select_related(
            "payment", "cashier", "warehouse", "selected_customer", "shift"
        )
        .prefetch_related(
            "lines__product",
            "lines__return_lines",
            "payment_lines",
            "sale_orders__outbound_order",
        )
        .get(pk=sale.pk)
    )
    orders = [
        link.outbound_order
        for link in sale.sale_orders.select_related("outbound_order").order_by(
            "owner_id"
        )
    ]
    return {
        "sale": sale,
        "payment": sale.payment,
        "orders": orders,
        "receipt": build_receipt(sale),
    }


def result_for_return(return_order):
    return_order = (
        PosReturn.objects.select_related("sale", "warehouse", "shift", "cashier")
        .prefetch_related("lines__sale_line__product", "refunds")
        .get(pk=return_order.pk)
    )
    return {"return": return_order}


@transaction.atomic
def create_pos_sale(
    *,
    user,
    customer_id=None,
    src_bill_no="",
    remark="",
    items=None,
    payment=None,
    payments=None,
    idempotency_key="",
    stock_zone_type=None,
):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        raise ValidationError("当前用户未绑定仓库(warehouse)，无法收银。")
    items = items or []
    if not items:
        _error("items", "购物车不能为空。")

    stock_zone_type = _normalize_stock_zone_type(stock_zone_type)
    fingerprint = _idempotency_fingerprint(
        warehouse_id=warehouse_id,
        customer_id=customer_id,
        src_bill_no=src_bill_no,
        remark=remark,
        items=items,
        payment=payment,
        payments=payments,
        stock_zone_type=stock_zone_type,
    )
    idempotency_key = (idempotency_key or "").strip() or None
    if idempotency_key:
        existing = (
            PosSale.objects.select_for_update()
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing:
            if (
                existing.idempotency_fingerprint
                and existing.idempotency_fingerprint != fingerprint
            ):
                _error(
                    "idempotency_key", "同一幂等键对应的收银内容不一致，请刷新后重试。"
                )
            return result_for_sale(existing)

    shift = current_shift_for_user(user, for_update=True)
    if not shift:
        raise ValidationError("当前收银员没有进行中的 POS 班次，请先开班。")

    products = _load_products(items)
    normalized_items, qty_by_product = _validate_prices_and_shape(items, products)
    _validate_stock(qty_by_product, products, warehouse_id, zone_type=stock_zone_type)

    Customer = apps.get_model("baseinfo", "Customer")
    selected_customer = None
    if customer_id:
        selected_customer = Customer.objects.filter(id=customer_id).first()
        if not selected_customer:
            _error("customer_id", "客户不存在。")

    total_amount = _money(sum((item["amount"] for item in normalized_items), ZERO))
    payment_lines_data = _normalize_payment_lines(payment, payments, total_amount)
    payment_data = _payment_summary_from_lines(payment_lines_data, total_amount)
    sale_no = _make_sale_no()
    receipt_no = (src_bill_no or "").strip() or sale_no

    if PosSale.objects.filter(src_bill_no=receipt_no).exists():
        _error("src_bill_no", "POS 小票号/外部单号已存在。")

    owner_ids = sorted({item["product"].owner_id for item in normalized_items})
    duplicate_owner_ids = [
        owner_id
        for owner_id in owner_ids
        if OutboundOrder.objects.filter(
            owner_id=owner_id, src_bill_no=receipt_no
        ).exists()
    ]
    if duplicate_owner_ids:
        _error("src_bill_no", "POS 小票号/外部单号已存在。")

    customers_by_owner = {}
    for owner_id in owner_ids:
        if selected_customer and selected_customer.owner_id == owner_id:
            customers_by_owner[owner_id] = selected_customer
        else:
            customers_by_owner[owner_id] = _cash_customer(owner_id, user)

    sale = PosSale.objects.create(
        sale_no=sale_no,
        src_bill_no=receipt_no,
        idempotency_key=idempotency_key,
        idempotency_fingerprint=fingerprint if idempotency_key else "",
        warehouse_id=warehouse_id,
        shift=shift,
        cashier=user if user and user.is_authenticated else None,
        selected_customer=selected_customer,
        total_amount=total_amount,
        remark=(remark or "").strip(),
    )

    items_by_owner = defaultdict(list)
    for item in normalized_items:
        items_by_owner[item["product"].owner_id].append(item)

    orders = []
    owner_amounts = {
        owner_id: _money(sum((item["amount"] for item in owner_items), ZERO))
        for owner_id, owner_items in items_by_owner.items()
    }
    for owner_id in owner_ids:
        order = OutboundOrder.objects.create(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            customer=customers_by_owner[owner_id],
            outbound_type="SALES",
            delivery_method="PICKUP",
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
            src_bill_no=receipt_no,
            memo=_pos_order_memo(remark),
            is_closed=True,
            close_reason=POS_ORDER_CLOSE_REASON,
            pricing_status=PricingStatus.CONFIRMED,
            priced_at=timezone.now(),
            priced_by=user if user and user.is_authenticated else None,
            final_order_amount=owner_amounts[owner_id],
            created_by=user if user and user.is_authenticated else None,
        )
        PosSaleOrder.objects.create(
            sale=sale,
            owner_id=owner_id,
            outbound_order=order,
            amount=owner_amounts[owner_id],
        )
        orders.append(order)

    order_by_owner = {order.owner_id: order for order in orders}
    line_no = 10
    now = timezone.now()
    for item in normalized_items:
        product = item["product"]
        order = order_by_owner[product.owner_id]
        order_line = OutboundOrderLine.objects.create(
            order=order,
            product=product,
            base_qty=item["qty"],
            base_price=item["price"],
            final_line_amount=item["amount"],
        )
        sale_line = PosSaleLine.objects.create(
            sale=sale,
            owner_id=product.owner_id,
            product=product,
            outbound_order_line=order_line,
            line_no=line_no,
            qty=item["qty"],
            price=item["price"],
            amount=item["amount"],
        )
        _deduct_stock_for_line(
            sale_line, warehouse_id, sale_no, now, zone_type=stock_zone_type
        )
        line_no += 10

    PosPayment.objects.create(sale=sale, **payment_data)
    for line_data in payment_lines_data:
        PosPaymentLine.objects.create(sale=sale, **line_data)
    _audit(
        action=PosAuditLog.Action.CHECKOUT,
        user=user,
        sale=sale,
        shift=shift,
        metadata={
            "total_amount": str(total_amount),
            "payment_count": len(payment_lines_data),
            "line_count": len(normalized_items),
        },
    )
    return result_for_sale(sale)


@transaction.atomic
def create_pos_return(
    *,
    user,
    sale_id,
    lines,
    refunds,
    reason="",
    idempotency_key="",
):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        raise ValidationError("当前用户未绑定仓库(warehouse)，无法退货。")
    reason = (reason or "").strip()
    if not reason:
        _error("reason", "POS 退货必须填写原因。")
    lines = lines or []
    if not lines:
        _error("items", "退货明细不能为空。")

    fingerprint = _return_fingerprint(
        sale_id=sale_id, reason=reason, lines=lines, refunds=refunds or []
    )
    idempotency_key = (idempotency_key or "").strip() or None
    if idempotency_key:
        existing = (
            PosReturn.objects.select_for_update()
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing:
            if (
                existing.idempotency_fingerprint
                and existing.idempotency_fingerprint != fingerprint
            ):
                _error(
                    "idempotency_key", "同一幂等键对应的退货内容不一致，请刷新后重试。"
                )
            return result_for_return(existing)

    shift = current_shift_for_user(user, for_update=True)
    if not shift:
        raise ValidationError("当前收银员没有进行中的 POS 班次，请先开班。")

    sale = (
        PosSale.objects.select_for_update()
        .filter(pk=sale_id, warehouse_id=warehouse_id)
        .first()
    )
    if not sale:
        raise ValidationError("POS 销售单不存在或无权退货。")
    if sale.status != PosSale.Status.COMPLETED:
        raise ValidationError("只有已完成的 POS 销售单可以退货。")

    sale_lines = {
        line.id: line
        for line in sale.lines.select_related("product", "owner").select_for_update()
    }
    requested_ids = [int(item["sale_line_id"]) for item in lines]
    missing = [line_id for line_id in requested_ids if line_id not in sale_lines]
    if missing:
        _error("items", f"退货明细不存在或不属于该销售单：{missing}")

    returned_qty = _returned_qty_by_sale_line(requested_ids)
    normalized_lines = []
    qty_by_line = defaultdict(lambda: ZERO)
    for raw in lines:
        sale_line = sale_lines[int(raw["sale_line_id"])]
        qty = _q3(raw["qty"])
        if qty <= 0:
            _error("items", "退货数量必须大于 0。")
        qty_by_line[sale_line.id] += qty
        available = _q3(sale_line.qty - returned_qty.get(sale_line.id, ZERO))
        if qty_by_line[sale_line.id] > available:
            _error(
                "items",
                f"{sale_line.product.code} 可退数量不足，可退 {available}，本次退 {qty_by_line[sale_line.id]}。",
            )
        amount = _money(qty * sale_line.price)
        normalized_lines.append(
            {
                "sale_line": sale_line,
                "qty": qty,
                "price": sale_line.price,
                "amount": amount,
            }
        )

    total_amount = _money(sum((line["amount"] for line in normalized_lines), ZERO))
    refund_lines = _normalize_refunds(refunds, total_amount)
    now = timezone.now()
    return_order = PosReturn.objects.create(
        return_no=_make_return_no(now),
        sale=sale,
        warehouse_id=warehouse_id,
        shift=shift,
        cashier=user if user and user.is_authenticated else None,
        total_amount=total_amount,
        reason=reason,
        idempotency_key=idempotency_key,
        idempotency_fingerprint=fingerprint if idempotency_key else "",
    )
    line_no = 10
    for line in normalized_lines:
        sale_line = line["sale_line"]
        return_line = PosReturnLine.objects.create(
            return_order=return_order,
            sale_line=sale_line,
            owner_id=sale_line.owner_id,
            product_id=sale_line.product_id,
            line_no=line_no,
            qty=line["qty"],
            price=line["price"],
            amount=line["amount"],
        )
        _restore_stock_for_return_line(return_line, return_order.return_no, now)
        line_no += 10

    for refund in refund_lines:
        processed_at = now if refund["status"] == PosRefund.Status.REFUNDED else None
        PosRefund.objects.create(
            return_order=return_order,
            sale=sale,
            shift=shift,
            method=refund["method"],
            amount=refund["amount"],
            reference_no=refund["reference_no"],
            status=refund["status"],
            processed_by=user if user and user.is_authenticated else None,
            processed_at=processed_at,
        )

    _audit(
        action=PosAuditLog.Action.RETURN,
        user=user,
        sale=sale,
        return_order=return_order,
        shift=shift,
        reason=reason,
        metadata={
            "total_amount": str(total_amount),
            "line_count": len(normalized_lines),
            "refund_count": len(refund_lines),
        },
    )
    _audit(
        action=PosAuditLog.Action.REFUND,
        user=user,
        sale=sale,
        return_order=return_order,
        shift=shift,
        reason=reason,
        metadata={"refund_amount": str(total_amount)},
    )
    return result_for_return(return_order)


@transaction.atomic
def void_pos_sale(*, sale_id, user, reason=""):
    sale = PosSale.objects.select_for_update().get(pk=sale_id)
    if sale.status == PosSale.Status.VOIDED:
        raise ValidationError("POS 销售单已撤销。")
    if (
        sale.shift_id
        and PosShift.objects.filter(
            pk=sale.shift_id, status=PosShift.Status.CLOSED
        ).exists()
    ):
        raise ValidationError("该 POS 销售单所属班次已交班，不能作废。")
    if PosReturn.objects.filter(sale=sale, status=PosReturn.Status.COMPLETED).exists():
        raise ValidationError("该 POS 销售单已有退货记录，不能作废。")
    if not (reason or "").strip():
        _error("reason", "POS 作废必须填写原因。")

    _restore_stock_from_sale(sale, user, reason)
    payment = PosPayment.objects.select_for_update().get(sale=sale)
    payment.status = PosPayment.Status.VOIDED
    payment.save(update_fields=["status", "updated_at"])
    PosPaymentLine.objects.select_for_update().filter(sale=sale).update(
        status=PosPayment.Status.VOIDED,
        updated_at=timezone.now(),
    )

    for link in sale.sale_orders.select_related("outbound_order").select_for_update():
        order = link.outbound_order
        order.approval_status = "CANCELLED"
        order.is_closed = False
        order.close_reason = ""
        order.memo = ((order.memo or "") + " POS撤销").strip()[:100]
        order.save(
            update_fields=[
                "approval_status",
                "is_closed",
                "close_reason",
                "memo",
                "updated_at",
            ]
        )

    sale.status = PosSale.Status.VOIDED
    sale.voided_at = timezone.now()
    sale.voided_by = user if user and user.is_authenticated else None
    sale.void_reason = (reason or "").strip()
    sale.save(
        update_fields=["status", "voided_at", "voided_by", "void_reason", "updated_at"]
    )
    _audit(
        action=PosAuditLog.Action.VOID,
        user=user,
        sale=sale,
        shift=sale.shift,
        reason=reason,
        metadata={"total_amount": str(sale.total_amount)},
    )
    return result_for_sale(sale)

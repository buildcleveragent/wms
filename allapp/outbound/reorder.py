"""Owner-portal purchasing facts. Reads never reserve inventory or create orders."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope
from allapp.baseinfo.customer_access import active_bindings
from allapp.inventory.models import InventoryDetail
from allapp.outbound.authz import strict_order_queryset
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.warehouse_access import owner_can_use_warehouse
from allapp.pos.models import PosSaleLine, PosSaleOrder
from allapp.products.models import Product, ProductPackage
from allapp.products.pricing import InvalidSalePriceRule

from .amounts import line_amount
from .pricing import has_reference_price, match_saved_line, sale_minimum, saved_pricing_lines

SHANGHAI = ZoneInfo("Asia/Shanghai")
ZERO = Decimal("0")


class CheckoutConflict(APIException):
    status_code = 409
    default_detail = "下单条件发生变化，请核对后重试。"

    def __init__(self, detail=None):
        super().__init__(detail)
        if isinstance(detail, dict):
            # Preserve the validation contract's booleans and identifiers;
            # APIException otherwise coerces every scalar into an error string.
            self.detail = detail


def owner_scope(user, *, buyer=False):
    scope = AccessScope.for_user(user)
    if (
        not scope.is_valid
        or not scope.single_owner_id
        or not scope.roles & {"owner_manager", "owner_salesperson"}
    ):
        raise PermissionDenied("需要单一有效货主角色范围。")
    allowed = "owner_salesperson" in scope.roles and user.has_perm(
        "outbound.submit_outbound_as_owner_buyers"
    )
    if buyer and not allowed:
        raise PermissionDenied("需要货主业务员下单权限。")
    if not buyer and not (
        allowed
        or ("owner_manager" in scope.roles and user.has_perm("accounts.access_owner_management"))
    ):
        raise PermissionDenied("当前账号没有客户资料访问权限。")
    return scope


def scoped_binding(user, customer_id, *, buyer=False, lock=False):
    scope = owner_scope(user, buyer=buyer)
    qs = active_bindings(scope.single_owner_id)
    if buyer or "owner_manager" not in scope.roles:
        qs = qs.filter(Q(salesperson=user) | Q(code__iexact="CASH"))
    if lock:
        qs = qs.select_for_update()
    return get_object_or_404(qs.select_related("customer"), customer_id=customer_id)


def non_pos_orders(qs):
    # Both generations of POS evidence are authoritative; the legacy memo is
    # also excluded rather than treating an unlinked old POS order as ordinary.
    return (
        qs.alias(
            _reorder_pos_order=Exists(
                PosSaleOrder.objects.filter(outbound_order_id=OuterRef("pk"))
            ),
            _reorder_pos_line=Exists(
                PosSaleLine.objects.filter(outbound_order_line__order_id=OuterRef("pk"))
            ),
        )
        .filter(outbound_type="SALES", _reorder_pos_order=False, _reorder_pos_line=False)
        .exclude(memo__startswith="[POS]")
    )


def source_order(user, pk):
    scope = owner_scope(user, buyer=True)
    order = get_object_or_404(
        strict_order_queryset(OutboundOrder.objects.all(), user, scope=scope).select_related(
            "customer", "warehouse"
        ),
        pk=pk,
    )
    if not non_pos_orders(OutboundOrder.objects.filter(pk=pk)).exists():
        raise ValidationError("仅支持普通销售和仓库代办销售订单，POS 请使用收银入口。")
    return order


def decimal_text(value, scale=None):
    if value is None:
        return None
    number = Decimal(value)
    return format(number.quantize(Decimal(scale)) if scale else number, "f")


def product_facts(owner_id, warehouse_id, product_ids, *, request=None, lock=False):
    """Load current products, packages and stock in a fixed number of queries."""
    products = Product.objects.filter(owner_id=owner_id, pk__in=product_ids).select_related(
        "base_uom"
    )
    if lock:
        products = products.select_for_update().order_by("pk")
    products = {p.pk: p for p in products}
    packages = defaultdict(list)
    for package in (
        ProductPackage.objects.filter(
            product_id__in=products, is_active=True, uom__is_active=True, uom__is_deleted=False
        )
        .select_related("uom")
        .order_by("sort_order", "pk")
    ):
        packages[package.product_id].append(package)
    stocks = defaultdict(list)
    for row in (
        InventoryDetail.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            product_id__in=products,
            is_active=True,
        )
        .values("product_id", "base_unit")
        .annotate(available=Sum("available_qty"), onhand=Sum("onhand_qty"))
    ):
        stocks[row["product_id"]].append(row)
    result = {}
    for pk, p in products.items():
        issues = []
        if not p.is_active or not p.base_uom.is_active or p.base_uom.is_deleted:
            issues.append("商品或基本单位已停用，请移除后重新选品。")
        unit = p.base_uom.code
        stock_rows = stocks[pk]
        mismatch = any(
            row["base_unit"] != unit and (row["available"] or row["onhand"]) for row in stock_rows
        )
        available = sum(
            (row["available"] or ZERO for row in stock_rows if row["base_unit"] == unit), ZERO
        )
        if mismatch:
            issues.append("库存单位不一致，数据待核对。")
        lowest = None
        is_priced = True
        try:
            is_priced = has_reference_price(p)
            lowest = sale_minimum(p)
        except InvalidSalePriceRule:
            issues.append("商品价格规则异常，请联系货主管理员核对。")
        options = [
            {
                "key": "BASE",
                "kind": "base",
                "label": p.base_uom.name,
                "multiplier": "1",
                "package_id": None,
                "barcode": p.gtin,
            }
        ]
        for package in packages[pk]:
            if package.qty_in_base and package.qty_in_base > 0:
                options.append(
                    {
                        "key": f"PKG-{package.pk}",
                        "kind": "package",
                        "label": package.uom.name,
                        "multiplier": decimal_text(package.qty_in_base),
                        "package_id": package.pk,
                        "barcode": package.barcode,
                    }
                )
        rules = [
            pk,
            p.is_active,
            p.base_uom_id,
            p.base_uom.is_active,
            p.base_uom.is_deleted,
            decimal_text(p.price),
            decimal_text(p.min_price),
            decimal_text(p.max_discount),
            options,
        ]
        image = None
        if p.product_image and request:
            image = request.build_absolute_uri(p.product_image.url)
        result[pk] = {
            "id": pk,
            "product_id": pk,
            "code": p.code,
            "sku": p.sku or p.code,
            "name": p.name,
            "spec": p.spec or "",
            "base_uom_id": p.base_uom_id,
            "base_unit": unit,
            "base_unit_name": p.base_uom.name,
            "is_priced": is_priced,
            "price": decimal_text(p.price, "0.0001") if is_priced else None,
            "orig_price": decimal_text(p.price, "0.0001"),
            "minimum_sale_price": decimal_text(lowest, "0.0001"),
            "available": None if mismatch else decimal_text(available, "0.0000"),
            "catalog_version": hashlib.sha256(
                json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            "unitOptions": options,
            "selectedUnitIndex": 0,
            "product_image_url": image,
            "issues": issues,
            "can_add": not issues and available > 0,
        }
    return result


def check_items(owner_id, warehouse_id, items, *, request=None, lock=False, saved_lines=None):
    facts = product_facts(
        owner_id, warehouse_id, [it["product_id"] for it in items], request=request, lock=lock
    )
    totals = defaultdict(lambda: ZERO)
    unverified_products = set()
    for it in items:
        fact = facts.get(it["product_id"])
        if it.get("unit_unverified") or (
            fact and it.get("base_uom_id") and it["base_uom_id"] != fact["base_uom_id"]
        ):
            unverified_products.add(it["product_id"])
        totals[it["product_id"]] += Decimal(it["qty"])
    rows = []
    for index, it in enumerate(items):
        pk = it["product_id"]
        fact = facts.get(pk)
        saved = match_saved_line(it, saved_lines)
        if fact:
            fact = dict(fact)
            if saved is not None and (not saved.is_priced or saved.base_price > ZERO):
                fact["is_priced"] = saved.is_priced
                if not saved.is_priced:
                    fact["minimum_sale_price"] = None
                    fact["price"] = None
                    fact["issues"] = [issue for issue in fact["issues"] if "价格规则" not in issue]
        issues = list(fact["issues"]) if fact else ["商品不存在、已删除或不属于当前货主。"]
        if fact:
            if it.get("base_uom_id") and it["base_uom_id"] != fact["base_uom_id"]:
                issues.append("历史基本单位与当前单位不同，数据待核对，请重新选品。")
            if it.get("unit_unverified"):
                issues.append("历史基本单位未记录，数据待核对，请重新选品。")
            if it.get("catalog_version") and it["catalog_version"] != fact["catalog_version"]:
                issues.append("商品价格或单位规则已变化，请确认当前信息后重试。")
            price = it.get("price")
            if not fact["is_priced"]:
                pass
            elif price is None or Decimal(price) <= 0:
                if saved is not None and saved.base_price == ZERO:
                    issues.append(
                        f"该历史订单行尚未确认价格，当前参考价为 {fact['price']}，请确认成交价。"
                    )
                else:
                    issues.append("请填写大于零的成交价。")
            elif fact["minimum_sale_price"] is not None and Decimal(price) < Decimal(
                fact["minimum_sale_price"]
            ):
                issues.append(f"成交价不能低于 {fact['minimum_sale_price']}。")
            if (
                pk not in unverified_products
                and fact["available"] is not None
                and totals[pk] > Decimal(fact["available"])
            ):
                issues.append(
                    f"本商品合计需求 {decimal_text(totals[pk])}，"
                    f"可用 {fact['available']}，缺口 "
                    f"{decimal_text(totals[pk] - Decimal(fact['available']))} "
                    f"{fact['base_unit_name']}；请调整数量。"
                )
        rows.append(
            {
                **(fact or {"product_id": pk, "name": "商品不可用", "available": None}),
                "line_key": it.get("line_key") or str(index),
                "qty": decimal_text(it["qty"], "0.001"),
                "entered_price": decimal_text(it.get("price"), "0.0001"),
                "amount": (
                    line_amount(it["qty"], it.get("price")) if fact and fact["is_priced"] else None
                ),
                "issues": issues,
            }
        )
    unpriced = sum(row.get("is_priced") is False for row in rows)
    return {
        "amount_status": (
            "UNPRICED" if rows and unpriced == len(rows) else "PARTIAL" if unpriced else "PRICED"
        ),
        "priced_amount": sum((row["amount"] or ZERO for row in rows), ZERO),
        "unpriced_line_count": unpriced,
        "can_submit": bool(items) and not any(row["issues"] for row in rows),
        "checked_at": datetime.now(SHANGHAI).isoformat(),
        "items": rows,
        "notice": "库存为本次检查时的可用量，提交不锁定库存，审核时重新分配。",
    }


def validate_submission(
    user, data, *, request=None, lock=False, raise_error=True, saved_lines=None
):
    """Shared final owner sales validation, also used by draft resubmission."""
    scope = owner_scope(user, buyer=True)
    scoped_binding(user, data.get("customer_id"), buyer=True, lock=lock)
    if not owner_can_use_warehouse(scope.single_owner_id, data["warehouse_id"]):
        raise ValidationError({"warehouse_id": "仓库不可用或未关联当前货主。"})
    if data.get("reorder_source_id"):
        source_order(user, data["reorder_source_id"])
    if data.get("editing_order_id"):
        order = source_order(user, data["editing_order_id"])
        if (
            order.created_by_id != user.pk
            or order.processing_mode != "STANDARD"
            or order.submit_status != "DRAFT"
            or order.is_closed
            or order.approval_status not in {"OWNER_PENDING", "OWNER_REJECTED"}
        ):
            raise PermissionDenied("当前订单不允许修改。")
        saved_lines = saved_pricing_lines(order)
    report = check_items(
        scope.single_owner_id,
        data["warehouse_id"],
        data["items"],
        request=request,
        lock=lock,
        saved_lines=saved_lines,
    )
    if raise_error and not report["can_submit"]:
        raise CheckoutConflict(
            {
                "code": "checkout_changed",
                "detail": "请处理商品校验提示后重新提交。",
                "validation": report,
            }
        )
    return report


def reorder_context(user, pk, request, *, warehouse_id=None, customer_id=None):
    order = source_order(user, pk)
    warehouse_id = warehouse_id or order.warehouse_id
    customer_id = customer_id or order.customer_id
    issues = []
    try:
        binding = scoped_binding(user, customer_id, buyer=True)
    except (Http404, APIException):
        binding = None
        issues.append("原客户已停用或不在当前业务员范围内，请选择当前可用客户。")
    warehouse_allowed = owner_can_use_warehouse(order.owner_id, warehouse_id)
    if not warehouse_allowed:
        issues.append("原仓库不可用或授权已撤销，请选择当前可用仓库。")
        warehouse_id = order.warehouse_id
    from allapp.locations.models import Warehouse

    warehouse = Warehouse.objects.filter(pk=warehouse_id).first()
    lines = list(order.lines.select_related("product", "base_uom").order_by("line_no", "pk"))
    stock_warehouse_id = warehouse_id if warehouse_allowed else -1
    facts = product_facts(
        order.owner_id, stock_warehouse_id, [line.product_id for line in lines], request=request
    )
    items = [
        {
            "product_id": line.product_id,
            "qty": line.base_qty,
            "price": facts.get(line.product_id, {}).get("price"),
            "base_uom_id": line.base_uom_id,
            "unit_unverified": not line.base_uom_id,
            "line_key": f"source-{line.pk}",
        }
        for line in lines
    ]
    report = check_items(order.owner_id, stock_warehouse_id, items, request=request)
    for row, line in zip(report["items"], lines):
        historical_unit = line.base_uom.name if line.base_uom_id else "未记录"
        current_unit = row.get("base_unit_name")
        if line.product_id not in facts and line.product.owner_id == order.owner_id:
            row.update(
                name=f"{line.product.name}（已不可用）",
                code=line.product.code,
                sku=line.product.sku,
                spec=line.product.spec or "",
            )
        row.update(
            {
                "source_line_id": line.pk,
                "source_line_no": line.line_no,
                "historical_price": (
                    decimal_text(line.base_price, "0.0001") if line.is_priced else None
                ),
                "historical_is_priced": line.is_priced,
                "base_uom_id": line.base_uom_id,
                "unit_unverified": not line.base_uom_id,
                "unit_mismatch": bool(
                    line.base_uom_id
                    and facts.get(line.product_id)
                    and line.base_uom_id != facts[line.product_id]["base_uom_id"]
                ),
                "historical_unit_name": historical_unit,
                "current_base_unit_name": current_unit,
                "base_unit_name": historical_unit,
            }
        )
    delivery_methods = dict(OutboundOrder._meta.get_field("delivery_method").choices)
    return {
        "source_order_id": order.pk,
        "source_order_no": order.order_no,
        "source_status": order.get_approval_status_display(),
        "warehouse": {"id": warehouse_id, "name": warehouse.name if warehouse else ""},
        "customer": (
            {"id": binding.customer_id, "code": binding.code, "name": binding.customer.name}
            if binding
            else None
        ),
        "header": {
            "contact": (order.contact or "") if customer_id == order.customer_id else "",
            "contact_phone": (
                (order.contact_phone or "") if customer_id == order.customer_id else ""
            ),
            "ship_to": (order.ship_to or "") if customer_id == order.customer_id else "",
            "delivery_method": (
                order.delivery_method if order.delivery_method in delivery_methods else None
            ),
        },
        **report,
        "context_issues": issues,
        "can_copy": bool(lines) and not issues,
        "copy_notice": (
            "仅复制商品和原计划基本数量；使用当前参考价。原单号、平台单号、"
            "预计发货时间、备注、批次、效期及包装作业要求均不继承。"
        ),
    }


def frequent_products(
    user, customer_id, warehouse_id, *, search="", page=1, page_size=20, request=None
):
    binding = scoped_binding(user, customer_id, buyer=True)
    if not owner_can_use_warehouse(binding.owner_id, warehouse_id):
        raise ValidationError({"warehouse_id": "仓库不可用或未关联当前货主。"})
    today = datetime.now(SHANGHAI).date()
    orders = non_pos_orders(strict_order_queryset(OutboundOrder.objects.all(), user)).filter(
        customer_id=customer_id,
        submit_status="SUBMITTED",
        approval_status__in=["OWNER_APPROVED", "WHS_PENDING", "WHS_APPROVED"],
        biz_date__gte=today - timedelta(days=89),
        biz_date__lte=today,
    )
    lines = OutboundOrderLine.objects.filter(
        order_id__in=orders.values("pk"), product__owner_id=binding.owner_id
    )
    if search:
        lines = lines.filter(
            Q(product__name__icontains=search)
            | Q(product__code__icontains=search)
            | Q(product__sku__icontains=search)
        )
    grouped = (
        lines.values("product_id")
        .annotate(
            purchase_count=Count("order_id", distinct=True),
            last_order_date=Max("order__biz_date"),
            last_order_id=Max("order_id"),
        )
        .order_by("-purchase_count", "-last_order_date", "-last_order_id", "product_id")
    )
    count = grouped.count()
    selected = list(grouped[(page - 1) * page_size : page * page_size])
    facts = product_facts(
        binding.owner_id, warehouse_id, [r["product_id"] for r in selected], request=request
    )
    rows = []
    for row in selected:
        fact = facts.get(
            row["product_id"],
            {
                "id": row["product_id"],
                "product_id": row["product_id"],
                "name": "商品已删除",
                "can_add": False,
                "issues": ["商品已删除，不能加购。"],
            },
        )
        rows.append(
            {
                **fact,
                "purchase_count": row["purchase_count"],
                "last_order_date": row["last_order_date"].isoformat(),
            }
        )
    return {
        "count": count,
        "results": rows,
        "next": page + 1 if page * page_size < count else None,
        "page": page,
        "checked_at": datetime.now(SHANGHAI).isoformat(),
        "notice": "本人可见订单近90天已审核订购记录，不代表实际发货。",
    }

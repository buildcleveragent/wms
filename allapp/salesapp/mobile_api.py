from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.baseinfo.models import Customer
from allapp.inventory.models import InventorySummary
from allapp.products.models import Product

from .models import (
    ChannelProductPolicy,
    CustomerChannel,
    CustomerProductPolicy,
    SalesOrder,
    SalesOrderLine,
    Salesperson,
)
from .services_pricing import compute_price_for_line
from .services_validation import OrderRuleError, validate_order_line_rules

MONEY_QUANT = Decimal("0.01")
PRICE_QUANT = Decimal("0.0001")
QTY_QUANT = Decimal("0.001")


def _money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _price(value):
    return Decimal(value or 0).quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def _qty(value):
    return Decimal(value or 0).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def _str(value, quant):
    return str(Decimal(value or 0).quantize(quant, rounding=ROUND_HALF_UP))


def _today():
    now = timezone.now()
    if timezone.is_naive(now):
        return now.date()
    return timezone.localtime(now).date()


def _error_message(error):
    detail = getattr(error, "detail", error)
    if isinstance(detail, dict):
        for value in detail.values():
            return _error_message(value)
    if isinstance(detail, (list, tuple)):
        return _error_message(detail[0]) if detail else ""
    return str(detail)


def _owner_for_user(user):
    owner = getattr(user, "owner", None)
    if owner:
        return owner
    raise PermissionDenied("当前用户未绑定货主，不能使用销售小程序。")


def _salesperson_for_user(owner, user):
    return Salesperson.objects.filter(owner=owner, user=user, is_active=True).first()


def _customer_for_owner(owner, customer_id):
    return get_object_or_404(
        Customer.objects.filter(owner=owner, is_active=True),
        pk=customer_id,
    )


def _channel_for_customer(owner, customer):
    rel = (
        CustomerChannel.objects.filter(owner=owner, customer=customer, is_active=True)
        .select_related("channel")
        .first()
    )
    return rel.channel if rel else None


def _available_map(owner, product_ids):
    rows = (
        InventorySummary.objects.filter(
            owner=owner,
            product_id__in=product_ids,
            is_active=True,
        )
        .values("product_id")
        .annotate(available_qty=Sum("available_qty"))
    )
    return {row["product_id"]: Decimal(row["available_qty"] or 0) for row in rows}


def _image_url(request, product):
    image = getattr(product, "product_image", None)
    if not image:
        return ""
    try:
        return request.build_absolute_uri(image.url)
    except ValueError:
        return ""


def _uom_options(product, base_unit_price=None):
    options = []
    seen = set()

    def add(code, name, qty_in_base, source, is_default=False):
        key = (code or name or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        ratio = Decimal(qty_in_base or 1)
        option = {
            "code": code or name,
            "name": name or code,
            "qty_in_base": _str(ratio, QTY_QUANT),
            "source": source,
            "is_default": bool(is_default),
        }
        if base_unit_price is not None:
            option["unit_price"] = _str(
                _unit_price_from_base(base_unit_price, ratio), PRICE_QUANT
            )
        options.append(option)

    base_uom = getattr(product, "base_uom", None)
    add(
        getattr(base_uom, "code", "") or "EA",
        getattr(base_uom, "name", "") or getattr(base_uom, "code", "") or "基本单位",
        Decimal("1"),
        "base",
        True,
    )

    for package in product.packages.all():
        uom = getattr(package, "uom", None)
        add(
            getattr(uom, "code", ""),
            getattr(uom, "name", ""),
            package.qty_in_base,
            "package",
            package.is_sales_default,
        )

    return options


def _unit_price_from_base(base_unit_price, qty_in_base):
    return _price(Decimal(base_unit_price or 0) * Decimal(qty_in_base or 1))


def _unit_price_for_order_uom(
    owner, customer, product, order_date, order_uom, channel=None
):
    base_unit_price = compute_price_for_line(
        owner, customer, product, order_date, channel
    )
    qty_in_base = _qty_in_base_for_uom(product, order_uom)
    if qty_in_base is None:
        raise ValidationError(
            {"order_uom": f"商品 {product.code} 未配置订货单位 {order_uom}。"}
        )
    return _unit_price_from_base(base_unit_price, qty_in_base)


def _policy_for(owner, customer, product, channel):
    if customer:
        customer_policy = CustomerProductPolicy.objects.filter(
            owner=owner,
            customer=customer,
            product=product,
            is_active=True,
        ).first()
        if customer_policy:
            return {
                "source": "customer",
                "order_uom": customer_policy.order_uom,
                "min_order_qty": customer_policy.min_order_qty,
                "multiple_qty": customer_policy.multiple_qty,
            }

    if channel:
        channel_policy = ChannelProductPolicy.objects.filter(
            owner=owner,
            channel=channel,
            product=product,
            is_active=True,
        ).first()
        if channel_policy:
            return {
                "source": "channel",
                "order_uom": channel_policy.order_uom,
                "min_order_qty": channel_policy.min_order_qty,
                "multiple_qty": Decimal("0"),
            }

    return {
        "source": "",
        "order_uom": "",
        "min_order_qty": Decimal("0"),
        "multiple_qty": Decimal("0"),
    }


def _default_order_uom(product, policy):
    if policy.get("order_uom"):
        return policy["order_uom"]

    options = _uom_options(product)
    for option in options:
        if option["is_default"] and option["source"] == "package":
            return option["code"]
    return options[0]["code"] if options else "EA"


def _qty_in_base_for_uom(product, order_uom):
    order_uom = (order_uom or "").strip()
    base_uom = getattr(product, "base_uom", None)
    base_values = {
        (getattr(base_uom, "code", "") or "").strip(),
        (getattr(base_uom, "name", "") or "").strip(),
    }
    if order_uom in base_values:
        return Decimal("1")

    for package in product.packages.all():
        uom = getattr(package, "uom", None)
        package_values = {
            (getattr(uom, "code", "") or "").strip(),
            (getattr(uom, "name", "") or "").strip(),
        }
        if order_uom in package_values:
            return Decimal(package.qty_in_base or 1)
    return None


def _validate_order_uom(product, order_uom):
    valid_values = set()
    for option in _uom_options(product):
        valid_values.add((option.get("code") or "").strip())
        valid_values.add((option.get("name") or "").strip())
    valid_values.discard("")
    if order_uom not in valid_values:
        raise ValidationError(
            {"order_uom": f"商品 {product.code} 未配置订货单位 {order_uom}。"}
        )


def _catalog_product_payload(
    request, owner, customer, product, available_qty, order_date
):
    channel = _channel_for_customer(owner, customer) if customer else None
    policy = (
        _policy_for(owner, customer, product, channel)
        if customer
        else _policy_for(owner, None, product, None)
    )
    base_unit_price = _price(
        compute_price_for_line(owner, customer, product, order_date, channel)
    )
    uom_options = _uom_options(product, base_unit_price=base_unit_price)
    default_uom = _default_order_uom(product, policy)
    qty_in_base = _qty_in_base_for_uom(product, default_uom)
    orderable = qty_in_base is not None
    orderable_reason = "" if orderable else f"订货单位 {default_uom} 未配置换算"
    qty_in_base = qty_in_base or Decimal("1")
    unit_price = _unit_price_from_base(base_unit_price, qty_in_base)

    return {
        "id": product.id,
        "code": product.code,
        "sku": product.sku,
        "name": product.name,
        "spec": product.spec or "",
        "image_url": _image_url(request, product),
        "base_uom": {
            "code": getattr(product.base_uom, "code", ""),
            "name": getattr(product.base_uom, "name", ""),
        },
        "order_uom": default_uom,
        "qty_in_base": _str(qty_in_base, QTY_QUANT),
        "uom_options": uom_options,
        "base_unit_price": _str(base_unit_price, PRICE_QUANT),
        "unit_price": _str(unit_price, PRICE_QUANT),
        "available_qty": _str(available_qty, QTY_QUANT),
        "available_display": f"{_str(available_qty, QTY_QUANT)} {getattr(product.base_uom, 'code', '')}",
        "orderable": orderable,
        "orderable_reason": orderable_reason,
        "policy": {
            "source": policy["source"],
            "order_uom": policy["order_uom"],
            "min_order_qty": _str(policy["min_order_qty"], QTY_QUANT),
            "multiple_qty": _str(policy["multiple_qty"], QTY_QUANT),
        },
    }


def _line_payload(request, line):
    product = line.product
    qty_in_base = _qty_in_base_for_uom(product, line.order_uom) or Decimal("1")
    base_qty = Decimal(line.qty or 0) * qty_in_base
    return {
        "id": line.id,
        "product_id": product.id,
        "product_code": product.code,
        "product_name": product.name,
        "product_spec": product.spec or "",
        "product_image": _image_url(request, product),
        "order_uom": line.order_uom,
        "qty_in_base": _str(qty_in_base, QTY_QUANT),
        "base_qty": _str(base_qty, QTY_QUANT),
        "base_uom": {
            "code": getattr(product.base_uom, "code", ""),
            "name": getattr(product.base_uom, "name", ""),
        },
        "qty": _str(line.qty, QTY_QUANT),
        "unit_price": _str(line.unit_price, PRICE_QUANT),
        "discount_amount": _str(line.discount_amount, MONEY_QUANT),
        "line_amount": _str(line.line_amount, MONEY_QUANT),
    }


def _order_payload(request, order):
    lines = list(
        order.lines.select_related("product", "product__base_uom")
        .prefetch_related("product__packages", "product__packages__uom")
        .all()
    )
    return {
        "id": order.id,
        "order_no": f"SO-{order.id}",
        "owner_id": order.owner_id,
        "customer_id": order.customer_id,
        "customer_code": getattr(order.customer, "code", ""),
        "customer_name": getattr(order.customer, "name", ""),
        "salesperson_id": order.salesperson_id,
        "salesperson_name": getattr(
            getattr(order.salesperson, "user", None), "name", ""
        )
        or getattr(getattr(order.salesperson, "user", None), "username", ""),
        "order_type": order.order_type,
        "order_type_name": order.get_order_type_display(),
        "status": order.status,
        "status_name": order.get_status_display(),
        "order_date": order.order_date.isoformat(),
        "total_amount": _str(order.total_amount, MONEY_QUANT),
        "currency": order.currency,
        "source": order.source,
        "remark": order.remark or "",
        "line_count": len(lines),
        "lines": [_line_payload(request, line) for line in lines],
    }


def _check_stock(
    owner, product, order_uom, qty, available_qty, *, allow_backorder=False
):
    qty_in_base = _qty_in_base_for_uom(product, order_uom)
    if qty_in_base is None:
        raise ValidationError(
            {"order_uom": f"商品 {product.code} 未配置订货单位 {order_uom}。"}
        )

    required_base_qty = Decimal(qty) * qty_in_base
    if not allow_backorder and required_base_qty > available_qty:
        raise ValidationError(
            {
                "stock": (
                    f"{product.code} 可用库存不足：需要 "
                    f"{_str(required_base_qty, QTY_QUANT)}，当前 "
                    f"{_str(available_qty, QTY_QUANT)}。"
                )
            }
        )
    return required_base_qty


def _check_stock_requirement(
    product, required_base_qty, available_qty, *, allow_backorder=False
):
    if not allow_backorder and required_base_qty > available_qty:
        raise ValidationError(
            {
                "stock": (
                    f"{product.code} 可用库存不足：需要 "
                    f"{_str(required_base_qty, QTY_QUANT)}，当前 "
                    f"{_str(available_qty, QTY_QUANT)}。"
                )
            }
        )


def _check_stock_requirements(required_by_product, available, *, allow_backorder=False):
    for product_id, requirement in required_by_product.items():
        _check_stock_requirement(
            requirement["product"],
            requirement["required_base_qty"],
            available.get(product_id, Decimal("0")),
            allow_backorder=allow_backorder,
        )


def _refresh_order_totals(order, *, allow_backorder=False):
    customer = order.customer
    channel = _channel_for_customer(order.owner, customer)
    lines = list(
        order.lines.select_related("product", "product__base_uom")
        .prefetch_related("product__packages", "product__packages__uom")
        .all()
    )
    product_ids = [line.product_id for line in lines]
    available = _available_map(order.owner, product_ids)
    total = Decimal("0.00")
    required_by_product = {}

    for line in lines:
        _validate_order_uom(line.product, line.order_uom)
        try:
            validate_order_line_rules(
                order.owner, customer.id, line.product, line.order_uom, line.qty
            )
        except OrderRuleError as exc:
            raise ValidationError({"rules": str(exc)}) from exc

        required_base_qty = _check_stock(
            order.owner,
            line.product,
            line.order_uom,
            line.qty,
            available.get(line.product_id, Decimal("0")),
            allow_backorder=allow_backorder,
        )
        required_by_product.setdefault(
            line.product_id,
            {"product": line.product, "required_base_qty": Decimal("0")},
        )
        required_by_product[line.product_id]["required_base_qty"] += required_base_qty

        unit_price = _unit_price_for_order_uom(
            order.owner,
            customer,
            line.product,
            order.order_date,
            line.order_uom,
            channel,
        )
        line_amount = _money(
            unit_price * Decimal(line.qty) - Decimal(line.discount_amount or 0)
        )
        line.unit_price = unit_price
        line.line_amount = line_amount
        line.save(update_fields=["unit_price", "line_amount", "updated_at"])
        total += line_amount

    _check_stock_requirements(
        required_by_product,
        available,
        allow_backorder=allow_backorder,
    )

    order.total_amount = _money(total)
    order.save(update_fields=["total_amount", "updated_at"])


class SalesMobilePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class MobileOrderLineInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    qty = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=Decimal("0.001")
    )
    order_uom = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=True
    )


class MobileOrderCreateInputSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    order_type = serializers.ChoiceField(
        choices=SalesOrder.OrderType.choices,
        required=False,
        default=SalesOrder.OrderType.SALE,
    )
    order_date = serializers.DateField(required=False)
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    submit = serializers.BooleanField(required=False, default=False)
    allow_backorder = serializers.BooleanField(required=False, default=False)
    lines = MobileOrderLineInputSerializer(many=True)

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("至少需要一条商品明细。")
        product_ids = [line["product_id"] for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError("同一商品请合并后再提交。")
        return lines


class MobileOrderQuoteInputSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    order_date = serializers.DateField(required=False)
    allow_backorder = serializers.BooleanField(required=False, default=False)
    lines = MobileOrderLineInputSerializer(many=True)

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("至少需要一条商品明细。")
        product_ids = [line["product_id"] for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError("同一商品请合并后再提交。")
        return lines


def _quote_order_lines(
    owner, customer, lines_data, order_date, *, allow_backorder=False
):
    channel = _channel_for_customer(owner, customer)
    products = {
        product.id: product
        for product in Product.objects.filter(
            owner=owner,
            id__in=[line["product_id"] for line in lines_data],
            is_active=True,
        )
        .select_related("base_uom")
        .prefetch_related("packages", "packages__uom")
    }
    missing = [
        line["product_id"] for line in lines_data if line["product_id"] not in products
    ]
    if missing:
        raise ValidationError({"products": f"商品不存在或不属于当前货主：{missing}"})

    available = _available_map(owner, products.keys())
    quote_lines = []
    total = Decimal("0.00")
    ok = True

    for line_data in lines_data:
        product = products[line_data["product_id"]]
        policy = _policy_for(owner, customer, product, channel)
        order_uom = (
            line_data.get("order_uom") or _default_order_uom(product, policy)
        ).strip()
        qty = _qty(line_data["qty"])
        line_ok = True
        message = ""

        try:
            _validate_order_uom(product, order_uom)
            validate_order_line_rules(owner, customer.id, product, order_uom, qty)
        except (OrderRuleError, ValidationError) as exc:
            line_ok = False
            message = _error_message(exc)

        qty_in_base = _qty_in_base_for_uom(product, order_uom) or Decimal("1")
        required_base_qty = qty * qty_in_base
        available_qty = available.get(product.id, Decimal("0"))
        if not allow_backorder and required_base_qty > available_qty:
            line_ok = False
            message = (
                f"{product.code} 可用库存不足：需要 "
                f"{_str(required_base_qty, QTY_QUANT)}，当前 {_str(available_qty, QTY_QUANT)}。"
            )

        base_unit_price = compute_price_for_line(
            owner, customer, product, order_date, channel
        )
        unit_price = _unit_price_from_base(base_unit_price, qty_in_base)
        line_amount = _money(unit_price * qty)
        total += line_amount
        ok = ok and line_ok

        quote_lines.append(
            {
                "product_id": product.id,
                "product_code": product.code,
                "product_name": product.name,
                "product_spec": product.spec or "",
                "order_uom": order_uom,
                "qty": _str(qty, QTY_QUANT),
                "qty_in_base": _str(qty_in_base, QTY_QUANT),
                "base_qty": _str(required_base_qty, QTY_QUANT),
                "base_uom": {
                    "code": getattr(product.base_uom, "code", ""),
                    "name": getattr(product.base_uom, "name", ""),
                },
                "available_qty": _str(available_qty, QTY_QUANT),
                "unit_price": _str(unit_price, PRICE_QUANT),
                "line_amount": _str(line_amount, MONEY_QUANT),
                "ok": line_ok,
                "message": message,
            }
        )

    return {
        "ok": ok,
        "customer_id": customer.id,
        "order_date": order_date.isoformat(),
        "total_amount": _str(total, MONEY_QUANT),
        "line_count": len(quote_lines),
        "lines": quote_lines,
    }


class MobileHomeApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner = _owner_for_user(request.user)
        salesperson = _salesperson_for_user(owner, request.user)
        orders = SalesOrder.objects.filter(owner=owner)
        status_counts = {
            row["status"]: row["total"]
            for row in orders.values("status").annotate(total=Count("id"))
        }

        return Response(
            {
                "owner": {"id": owner.id, "code": owner.code, "name": owner.name},
                "salesperson": {
                    "id": salesperson.id if salesperson else None,
                    "name": getattr(request.user, "name", "") or request.user.username,
                    "phone": getattr(salesperson, "phone", "") if salesperson else "",
                },
                "metrics": {
                    "draft_orders": status_counts.get(SalesOrder.Status.DRAFT, 0),
                    "submitted_orders": status_counts.get(
                        SalesOrder.Status.SUBMITTED, 0
                    ),
                    "approved_orders": status_counts.get(SalesOrder.Status.APPROVED, 0),
                    "customers": Customer.objects.filter(
                        owner=owner, is_active=True
                    ).count(),
                    "active_products": Product.objects.filter(
                        owner=owner, is_active=True
                    ).count(),
                },
            }
        )


class MobileCustomerListApi(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = SalesMobilePagination

    def get(self, request):
        owner = _owner_for_user(request.user)
        search = (request.query_params.get("search") or "").strip()
        qs = Customer.objects.filter(owner=owner, is_active=True).order_by("code")
        if search:
            qs = qs.filter(
                Q(code__icontains=search)
                | Q(name__icontains=search)
                | Q(contact_person__icontains=search)
                | Q(phone__icontains=search)
                | Q(mobile__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = page if page is not None else qs
        data = [
            {
                "id": customer.id,
                "code": customer.code,
                "name": customer.name,
                "contact_person": customer.contact_person or "",
                "phone": customer.phone or customer.mobile or "",
                "area": customer.area or "",
                "address": customer.address or "",
                "delivery_route": customer.delivery_route or "",
                "delivery_seq": customer.delivery_seq,
            }
            for customer in rows
        ]
        if page is not None:
            return paginator.get_paginated_response(data)
        return Response(data)


class MobileCatalogApi(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = SalesMobilePagination

    def get(self, request):
        owner = _owner_for_user(request.user)
        customer_id = request.query_params.get("customer_id")
        customer = _customer_for_owner(owner, customer_id) if customer_id else None
        order_date = _today()
        search = (request.query_params.get("search") or "").strip()

        qs = (
            Product.objects.filter(owner=owner, is_active=True)
            .select_related("base_uom")
            .prefetch_related("packages", "packages__uom")
            .order_by("code")
        )
        if search:
            qs = qs.filter(
                Q(code__icontains=search)
                | Q(sku__icontains=search)
                | Q(name__icontains=search)
                | Q(unit_barcode__icontains=search)
                | Q(carton_barcode__icontains=search)
                | Q(gtin__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        products = page if page is not None else qs
        available = _available_map(owner, [product.id for product in products])
        data = [
            _catalog_product_payload(
                request,
                owner,
                customer,
                product,
                available.get(product.id, Decimal("0")),
                order_date,
            )
            for product in products
        ]
        if page is not None:
            return paginator.get_paginated_response(data)
        return Response(data)


class MobileOrderListCreateApi(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = SalesMobilePagination

    def get(self, request):
        owner = _owner_for_user(request.user)
        status_param = (request.query_params.get("status") or "").strip()
        search = (request.query_params.get("search") or "").strip()
        qs = (
            SalesOrder.objects.filter(owner=owner)
            .select_related("customer", "salesperson", "salesperson__user")
            .prefetch_related(
                "lines",
                "lines__product",
                "lines__product__base_uom",
                "lines__product__packages",
                "lines__product__packages__uom",
            )
            .order_by("-order_date", "-id")
        )
        if status_param:
            qs = qs.filter(status=status_param)
        if search:
            query = Q(customer__code__icontains=search) | Q(
                customer__name__icontains=search
            )
            if search.isdigit():
                query |= Q(id=int(search))
            qs = qs.filter(query)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        orders = page if page is not None else qs
        data = [_order_payload(request, order) for order in orders]
        if page is not None:
            return paginator.get_paginated_response(data)
        return Response(data)

    @transaction.atomic
    def post(self, request):
        owner = _owner_for_user(request.user)
        serializer = MobileOrderCreateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        customer = _customer_for_owner(owner, data["customer_id"])
        salesperson = _salesperson_for_user(owner, request.user)
        order_date = data.get("order_date") or _today()
        channel = _channel_for_customer(owner, customer)
        lines_data = data["lines"]
        products = {
            product.id: product
            for product in Product.objects.filter(
                owner=owner,
                id__in=[line["product_id"] for line in lines_data],
                is_active=True,
            )
            .select_related("base_uom")
            .prefetch_related("packages", "packages__uom")
        }
        missing = [
            line["product_id"]
            for line in lines_data
            if line["product_id"] not in products
        ]
        if missing:
            raise ValidationError(
                {"products": f"商品不存在或不属于当前货主：{missing}"}
            )

        available = _available_map(owner, products.keys())
        order = SalesOrder.objects.create(
            owner=owner,
            salesperson=salesperson,
            customer=customer,
            order_type=data["order_type"],
            status=SalesOrder.Status.DRAFT,
            order_date=order_date,
            total_amount=Decimal("0.00"),
            source="miniapp",
            remark=data.get("remark", ""),
            created_by=request.user,
            updated_by=request.user,
        )

        total = Decimal("0.00")
        for line_data in lines_data:
            product = products[line_data["product_id"]]
            policy = _policy_for(owner, customer, product, channel)
            order_uom = (
                line_data.get("order_uom") or _default_order_uom(product, policy)
            ).strip()
            _validate_order_uom(product, order_uom)

            try:
                validate_order_line_rules(
                    owner, customer.id, product, order_uom, line_data["qty"]
                )
            except OrderRuleError as exc:
                raise ValidationError({"rules": str(exc)}) from exc

            _check_stock(
                owner,
                product,
                order_uom,
                line_data["qty"],
                available.get(product.id, Decimal("0")),
                allow_backorder=data["allow_backorder"],
            )

            unit_price = _unit_price_for_order_uom(
                owner, customer, product, order_date, order_uom, channel
            )
            line_amount = _money(unit_price * Decimal(line_data["qty"]))
            SalesOrderLine.objects.create(
                owner=owner,
                order=order,
                product=product,
                order_uom=order_uom,
                qty=_qty(line_data["qty"]),
                unit_price=unit_price,
                discount_amount=Decimal("0.00"),
                line_amount=line_amount,
                created_by=request.user,
                updated_by=request.user,
            )
            total += line_amount

        order.total_amount = _money(total)
        if data["submit"]:
            order.status = SalesOrder.Status.SUBMITTED
        order.save(update_fields=["total_amount", "status", "updated_at"])

        return Response(_order_payload(request, order), status=status.HTTP_201_CREATED)


class MobileOrderQuoteApi(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        owner = _owner_for_user(request.user)
        serializer = MobileOrderQuoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = _customer_for_owner(owner, data["customer_id"])
        order_date = data.get("order_date") or _today()
        quote = _quote_order_lines(
            owner,
            customer,
            data["lines"],
            order_date,
            allow_backorder=data["allow_backorder"],
        )
        return Response(quote)


class MobileOrderDetailApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        owner = _owner_for_user(request.user)
        order = get_object_or_404(
            SalesOrder.objects.filter(owner=owner)
            .select_related("customer", "salesperson", "salesperson__user")
            .prefetch_related(
                "lines",
                "lines__product",
                "lines__product__base_uom",
                "lines__product__packages",
                "lines__product__packages__uom",
            ),
            pk=pk,
        )
        return Response(_order_payload(request, order))


class MobileOrderSubmitApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        owner = _owner_for_user(request.user)
        allow_backorder = bool(request.data.get("allow_backorder", False))
        order = get_object_or_404(
            SalesOrder.objects.select_for_update()
            .filter(owner=owner)
            .select_related("customer", "salesperson", "salesperson__user")
            .prefetch_related(
                "lines",
                "lines__product",
                "lines__product__base_uom",
                "lines__product__packages",
                "lines__product__packages__uom",
            ),
            pk=pk,
        )
        if order.status != SalesOrder.Status.DRAFT:
            raise ValidationError({"status": "仅草稿订单可以提交。"})

        _refresh_order_totals(order, allow_backorder=allow_backorder)
        order.status = SalesOrder.Status.SUBMITTED
        order.updated_by = request.user
        order.save(update_fields=["status", "updated_by", "updated_at"])

        return Response(_order_payload(request, order))

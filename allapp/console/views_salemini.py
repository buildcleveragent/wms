from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    DecimalField,
    Exists,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail
from allapp.products.models import Brand, Product, ProductCategory
from allapp.salesapp.models import SaleProductConfig
from allapp.salesapp.salemini_api import _saleable_inventory_detail_filter


@dataclass
class CatalogRow:
    product: Product
    config: SaleProductConfig | None
    available_qty: Decimal
    is_public_visible: bool
    warnings: list[str]

    @property
    def effective_price(self):
        if self.config and self.config.sale_price is not None:
            return self.config.sale_price
        return self.product.price


def _can_view_catalog(user):
    return (
        user.is_superuser
        or user.has_perm("salesapp.view_saleproductconfig")
        or user.has_perm("products.view_product")
    )


def _can_manage_catalog(user):
    return user.is_superuser or (
        user.has_perm("salesapp.add_saleproductconfig")
        and user.has_perm("salesapp.change_saleproductconfig")
    )


def _decimal_or_none(value, field_name):
    value = (value or "").strip()
    if value == "":
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field_name: "请输入有效数字。"}) from exc
    if parsed < 0:
        raise ValidationError({field_name: "不能小于 0。"})
    return parsed


def _positive_decimal(value, field_name):
    parsed = _decimal_or_none(value, field_name)
    if parsed is None or parsed <= 0:
        raise ValidationError({field_name: "必须大于 0。"})
    return parsed


def _available_qty_subquery():
    rows = (
        InventoryDetail.objects.filter(
            owner_id=OuterRef("owner_id"),
            product_id=OuterRef("pk"),
            is_active=True,
        )
        .filter(_saleable_inventory_detail_filter())
        .values("owner_id", "product_id")
        .annotate(qty=Sum("available_qty"))
        .values("qty")[:1]
    )
    return Coalesce(
        Subquery(rows, output_field=DecimalField(max_digits=18, decimal_places=4)),
        Value(Decimal("0.0000")),
        output_field=DecimalField(max_digits=18, decimal_places=4),
    )


def _product_queryset(params):
    matching_config = SaleProductConfig.objects.filter(
        owner_id=OuterRef("owner_id"),
        product_id=OuterRef("pk"),
    )
    mismatched_config = SaleProductConfig.objects.filter(product_id=OuterRef("pk")).exclude(
        owner_id=OuterRef("owner_id")
    )
    qs = (
        Product.objects.select_related("owner", "category", "brand", "base_uom")
        .annotate(
            available_qty_for_sale=_available_qty_subquery(),
            has_sale_mini_config=Exists(matching_config),
            has_sale_mini_price=Exists(
                matching_config.filter(sale_price__isnull=False)
            ),
            is_sale_mini_listed=Exists(
                matching_config.filter(is_active=True, is_listed=True)
            ),
            has_mismatched_config=Exists(mismatched_config),
            is_recommended_config=Exists(matching_config.filter(is_recommended=True)),
            is_hot_config=Exists(matching_config.filter(is_hot=True)),
            is_new_config=Exists(matching_config.filter(is_new=True)),
        )
        .order_by("owner__code", "code", "id")
    )

    owner_id = params.get("owner")
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    category_id = params.get("category")
    if category_id:
        qs = qs.filter(category_id=category_id)
    brand_id = params.get("brand")
    if brand_id:
        qs = qs.filter(brand_id=brand_id)

    keyword = (params.get("q") or "").strip()
    if keyword:
        qs = qs.filter(
            Q(code__icontains=keyword)
            | Q(sku__icontains=keyword)
            | Q(gtin__icontains=keyword)
            | Q(unit_barcode__icontains=keyword)
            | Q(carton_barcode__icontains=keyword)
            | Q(name__icontains=keyword)
            | Q(spec__icontains=keyword)
        )

    active_state = params.get("active") or "active"
    if active_state == "active":
        qs = qs.filter(is_active=True, owner__is_active=True)
    elif active_state == "inactive":
        qs = qs.filter(Q(is_active=False) | Q(owner__is_active=False))

    listing_state = params.get("listing") or ""
    if listing_state == "listed":
        qs = qs.filter(is_sale_mini_listed=True)
    elif listing_state == "unlisted":
        qs = qs.filter(has_sale_mini_config=True, is_sale_mini_listed=False)
    elif listing_state == "unconfigured":
        qs = qs.filter(has_sale_mini_config=False)
    elif listing_state == "configured":
        qs = qs.filter(has_sale_mini_config=True)

    stock_state = params.get("stock") or ""
    if stock_state == "in":
        qs = qs.filter(available_qty_for_sale__gt=0)
    elif stock_state == "out":
        qs = qs.filter(available_qty_for_sale__lte=0)

    price_state = params.get("price") or ""
    if price_state == "priced":
        qs = qs.filter(Q(price__isnull=False) | Q(has_sale_mini_price=True))
    elif price_state == "missing":
        qs = qs.filter(price__isnull=True, has_sale_mini_price=False)

    tag_state = params.get("tag") or ""
    if tag_state == "recommended":
        qs = qs.filter(is_recommended_config=True)
    elif tag_state == "hot":
        qs = qs.filter(is_hot_config=True)
    elif tag_state == "new":
        qs = qs.filter(is_new_config=True)

    return qs


def _config_map(products):
    product_ids = [product.id for product in products]
    owner_ids = [product.owner_id for product in products]
    configs = SaleProductConfig.objects.filter(
        owner_id__in=owner_ids,
        product_id__in=product_ids,
    ).select_related("owner", "product")
    return {(config.owner_id, config.product_id): config for config in configs}


def _row_for_product(product, config):
    warnings = []
    effective_price = config.sale_price if config and config.sale_price is not None else product.price
    if not product.owner.is_active:
        warnings.append("货主停用")
    if not product.is_active:
        warnings.append("商品停用")
    if getattr(product, "has_mismatched_config", False):
        warnings.append("存在货主不匹配配置")
    if not config:
        warnings.append("未创建商城配置")
    else:
        if config.owner_id != product.owner_id:
            warnings.append("配置货主不匹配")
        if not config.is_active:
            warnings.append("配置停用")
        if not config.is_listed:
            warnings.append("未上架")
    if effective_price is None:
        warnings.append("未设置价格")
    is_public_visible = (
        bool(config)
        and product.owner.is_active
        and product.is_active
        and config.owner_id == product.owner_id
        and config.is_active
        and config.is_listed
    )
    return CatalogRow(
        product=product,
        config=config,
        available_qty=getattr(product, "available_qty_for_sale", Decimal("0.0000")),
        is_public_visible=is_public_visible,
        warnings=warnings,
    )


def _ensure_config(product, *, created_by=None):
    config, created = SaleProductConfig.objects.get_or_create(
        owner=product.owner,
        product=product,
        defaults={
            "sale_price": product.price,
            "min_order_qty": Decimal("1.000"),
            "multiple_qty": Decimal("1.000"),
            "created_by": created_by,
            "updated_by": created_by,
        },
    )
    if not created and created_by:
        config.updated_by = created_by
    return config, created


def _validate_listable(product, config):
    if config.owner_id != product.owner_id:
        raise ValidationError("上架配置货主必须等于商品货主。")
    if not product.owner.is_active:
        raise ValidationError("货主已停用，不能上架。")
    if not product.is_active:
        raise ValidationError("商品已停用，不能上架。")
    if not config.is_active:
        raise ValidationError("商城配置已停用，不能上架。")
    if config.sale_price is None and product.price is None:
        raise ValidationError("未设置商品价格，不能上架。")


class SaleMiniProductListingView(LoginRequiredMixin, View):
    template_name = "console/sale_mini/product_listing.html"
    page_size = 50

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            if not _can_manage_catalog(request.user):
                raise PermissionDenied("没有商城上架管理权限。")
        elif not _can_view_catalog(request.user):
            raise PermissionDenied("没有查看商城上架管理页的权限。")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        selected = [int(item) for item in request.POST.getlist("product_ids") if item.isdigit()]
        action = request.POST.get("bulk_action") or ""
        if not selected:
            messages.warning(request, "请先勾选商品。")
            return redirect(self._redirect_url(request))
        try:
            result = self._apply_bulk_action(request, action, selected)
        except ValidationError as exc:
            messages.error(request, self._error_text(exc))
            return redirect(self._redirect_url(request))
        if result["errors"]:
            messages.warning(
                request,
                "已处理 {done} 个，跳过 {errors} 个：{sample}".format(
                    done=result["done"],
                    errors=len(result["errors"]),
                    sample="；".join(result["errors"][:5]),
                ),
            )
        else:
            messages.success(request, f"已处理 {result['done']} 个商品。")
        return redirect(self._redirect_url(request))

    def get_context(self, request):
        qs = _product_queryset(request.GET)
        paginator = Paginator(qs, self.page_size)
        page_obj = paginator.get_page(request.GET.get("page"))
        products = list(page_obj.object_list)
        configs = _config_map(products)
        rows = [
            _row_for_product(product, configs.get((product.owner_id, product.id)))
            for product in products
        ]
        summary_base = _product_queryset(request.GET.copy())
        return {
            "rows": rows,
            "page_obj": page_obj,
            "paginator": paginator,
            "owners": Owner.objects.order_by("code"),
            "categories": ProductCategory.objects.filter(is_active=True).order_by("code"),
            "brands": Brand.objects.filter(is_active=True).order_by("code"),
            "stock_display_choices": SaleProductConfig.StockDisplay.choices,
            "filters": request.GET,
            "query_without_page": self._query_without_page(request),
            "admin_config_changelist_url": reverse(
                "admin:salesapp_saleproductconfig_changelist"
            ),
            "summary": {
                "total": summary_base.count(),
                "listed": summary_base.filter(is_sale_mini_listed=True).count(),
                "unconfigured": summary_base.filter(has_sale_mini_config=False).count(),
                "in_stock": summary_base.filter(available_qty_for_sale__gt=0).count(),
                "missing_price": summary_base.filter(
                    price__isnull=True,
                    has_sale_mini_price=False,
                ).count(),
            },
        }

    def _apply_bulk_action(self, request, action, selected):
        products = list(
            Product.objects.select_related("owner").filter(id__in=selected).order_by("id")
        )
        selected_set = set(selected)
        missing_ids = selected_set - {product.id for product in products}
        errors = [f"商品 {pid} 不存在" for pid in sorted(missing_ids)]
        done = 0

        with transaction.atomic():
            for product in products:
                try:
                    changed = self._apply_action_to_product(request, action, product)
                except ValidationError as exc:
                    errors.append(f"{product.code}: {self._error_text(exc)}")
                    continue
                if changed:
                    done += 1

        return {"done": done, "errors": errors}

    def _apply_action_to_product(self, request, action, product):
        if action == "create_config":
            config, created = _ensure_config(product, created_by=request.user)
            if not created:
                return False
            config.full_clean()
            config.save()
            return True

        if action not in {
            "list",
            "unlist",
            "set_sale_price",
            "sync_sale_price",
            "set_market_price",
            "set_stock_display",
            "set_badges",
            "set_rules",
            "set_sort_order",
        }:
            raise ValidationError("未知批量操作。")

        config, _created = _ensure_config(product, created_by=request.user)
        changed = True
        if action == "list":
            _validate_listable(product, config)
            config.is_active = True
            config.is_listed = True
        elif action == "unlist":
            config.is_listed = False
        elif action == "set_sale_price":
            config.sale_price = _decimal_or_none(request.POST.get("sale_price"), "sale_price")
        elif action == "sync_sale_price":
            if product.price is None:
                raise ValidationError("商品基础价格为空，不能同步。")
            config.sale_price = product.price
        elif action == "set_market_price":
            config.market_price = _decimal_or_none(
                request.POST.get("market_price"), "market_price"
            )
        elif action == "set_stock_display":
            stock_display = request.POST.get("stock_display")
            valid = {key for key, _label in SaleProductConfig.StockDisplay.choices}
            if stock_display not in valid:
                raise ValidationError("库存展示方式无效。")
            config.stock_display = stock_display
        elif action == "set_badges":
            config.is_recommended = request.POST.get("is_recommended") == "1"
            config.is_hot = request.POST.get("is_hot") == "1"
            config.is_new = request.POST.get("is_new") == "1"
        elif action == "set_rules":
            min_order_qty = _positive_decimal(
                request.POST.get("min_order_qty"), "min_order_qty"
            )
            multiple_qty = _positive_decimal(
                request.POST.get("multiple_qty"), "multiple_qty"
            )
            max_order_qty = _decimal_or_none(
                request.POST.get("max_order_qty"), "max_order_qty"
            )
            if max_order_qty is not None and max_order_qty < min_order_qty:
                raise ValidationError("最大购买量不能小于起购数量。")
            config.min_order_qty = min_order_qty
            config.multiple_qty = multiple_qty
            config.max_order_qty = max_order_qty
        elif action == "set_sort_order":
            sort_order = request.POST.get("sort_order")
            if not str(sort_order).isdigit():
                raise ValidationError("排序必须是非负整数。")
            config.sort_order = int(sort_order)

        config.updated_by = request.user
        config.full_clean()
        config.save()
        return changed

    def _redirect_url(self, request):
        query = self._query_without_page(request)
        path = reverse("console:sale_mini_product_listing")
        return f"{path}?{query}" if query else path

    def _query_without_page(self, request):
        query = request.GET.copy()
        query.pop("page", None)
        return query.urlencode()

    def _error_text(self, exc):
        if hasattr(exc, "message_dict"):
            parts = []
            for field, messages_for_field in exc.message_dict.items():
                parts.append(f"{field}: {'; '.join(messages_for_field)}")
            return "；".join(parts)
        messages_list = getattr(exc, "messages", None)
        if messages_list:
            return "；".join(str(item) for item in messages_list)
        return str(exc)

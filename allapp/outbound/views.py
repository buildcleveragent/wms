# allapp/outbound/views.py  或  allapp/outbound/api_views.py
from django.core.exceptions import ValidationError as DjangoValidationError
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote
from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework.parsers import MultiPartParser, FormParser
from django.apps import apps
from datetime import datetime, timezone as datetime_timezone
from uuid import UUID
from django.db import IntegrityError
from django.db.models import (
    BigIntegerField,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce
import logging
from ..products.models import ProductPackage
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework import viewsets, mixins, status
from rest_framework.pagination import PageNumberPagination
from .models import OutboundOrder
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from allapp.tasking.models import WmsTask, WmsTaskLine
from allapp.tasking import services as task_services
from allapp.tasking.services import _run_posting_handler, adjust_pick_line_qty
from allapp.inventory.models import PostingJournal
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.decorators import action
from rest_framework.response import Response
from allapp.billing.enums import PeriodStatus
from allapp.billing.models import BillingPeriod
from allapp.outbound.enums import PricingStatus
from allapp.outbound.models import OutboundOrderLine
from allapp.outbound.serializers import (
    AssistedOutboundOrderCreateSerializer,
    ConfirmPricingSerializer,
    OutboundOrderCreateSerializer,
    OutboundOrderDraftUpdateSerializer,
    OutboundOrderReadSerializer,
)
from allapp.outbound import services as outbound_services
from allapp.outbound.assisted_history import (
    assisted_history_queryset,
    build_stats as build_assisted_outbound_stats,
    filter_history_queryset,
    history_options as assisted_history_options,
    serialize_history_order,
)
from allapp.outbound.authz import (
    apply_legacy_scope,
    assisted_task_queryset,
    can_self_review_assisted_task,
    can_review_task_actions,
    can_use_task_actions,
    get_assisted_order_for_task,
    is_assisted_operator,
    require_assisted_operator,
    require_legacy_action,
    strict_order_queryset,
    strict_pick_queryset,
)
from allapp.core.utils.log_context import build_log_payload
from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope
from allapp.outbound.warehouse_access import (
    owner_can_use_warehouse,
    owner_warehouse_ids,
    owner_warehouse_queryset,
)
from allapp.outbound.drop_ship_import import (
    DropShipImportFileError,
    import_drop_ship_workbook,
)

logger = logging.getLogger(__name__)


# 放到 OutboundOrderViewSet 类中
@action(detail=True, methods=["post"], url_path="confirm-pricing")
@transaction.atomic
def confirm_pricing(self, request, pk=None):
    order = self.get_object()

    locked_period_exists = BillingPeriod.objects.filter(
        owner_id=order.owner_id,
        warehouse_id=order.warehouse_id,
        start_date__lte=order.biz_date,
        end_date__gte=order.biz_date,
        status__in=[PeriodStatus.CLOSED, PeriodStatus.INVOICED],
    ).exists()
    if locked_period_exists:
        return Response(
            {"detail": "该订单所属账期已关账或已开票，禁止确认/修改价格。"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ConfirmPricingSerializer(
        data=request.data,
        context={"order": order, "request": request},
    )
    serializer.is_valid(raise_exception=True)

    lines_map = {
        line.id: line
        for line in OutboundOrderLine.objects.select_for_update().filter(
            order=order,
            is_deleted=False,
        )
    }

    total_amount = Decimal("0.00")
    for item in serializer.validated_data["lines"]:
        line = lines_map[item["line_id"]]
        line.base_price = item["base_price"]

        line_amount = (
            Decimal(line.base_qty or 0) * Decimal(line.base_price or 0)
        ).quantize(Decimal("0.01"))
        line.final_line_amount = line_amount
        line.save(update_fields=["base_price", "final_line_amount", "updated_at"])
        total_amount += line_amount

    order.final_order_amount = total_amount.quantize(Decimal("0.01"))
    order.pricing_status = PricingStatus.CONFIRMED
    order.priced_at = timezone.now()
    order.priced_by = request.user
    order.save(
        update_fields=[
            "final_order_amount",
            "pricing_status",
            "priced_at",
            "priced_by",
            "updated_at",
        ]
    )

    return Response(
        OutboundOrderReadSerializer(order, context={"request": request}).data,
        status=status.HTTP_200_OK,
    )



class DefaultPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class ReceiveProductPagination(PageNumberPagination):
    page_size = 300
    page_size_query_param = "page_size"
    max_page_size = 500

class ProductPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100


class AssistedHistoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "request_id 已用于不同的代办出库请求。"
    default_code = "idempotency_conflict"


class StandardOrderIdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "本次幂等键已用于不同的标准订单请求，请重新开单。"
    default_code = "idempotency_conflict"


class StaleOrderEdit(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "stale_order_edit"
    default_detail = "订单已被其他会话修改，请重新加载。"

    def __init__(self, current_updated_at):
        detail = {
            "code": self.default_code,
            "detail": self.default_detail,
            "current_updated_at": serializers.DateTimeField().to_representation(
                current_updated_at
            ),
        }
        super().__init__(detail=detail, code=self.default_code)


STANDARD_ORDER_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def _catalog_scope(request):
    scope = AccessScope.for_user(request.user)
    if not scope.is_valid:
        raise PermissionDenied("账号没有有效的角色数据范围。")
    return scope


def _warehouse_owner_ids(scope):
    """Resolve owners that have business facts in the authorized warehouses."""

    if not scope.is_valid or not scope.warehouse_ids:
        return frozenset()
    warehouse_ids = tuple(scope.warehouse_ids)
    InventoryDetail = apps.get_model("inventory", "InventoryDetail")
    InboundOrder = apps.get_model("inbound", "InboundOrder")
    owner_ids = set(
        InventoryDetail.objects.filter(warehouse_id__in=warehouse_ids)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    owner_ids.update(
        OutboundOrder.objects.filter(warehouse_id__in=warehouse_ids)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    owner_ids.update(
        InboundOrder.objects.filter(warehouse_id__in=warehouse_ids)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    owner_ids.update(
        WmsTask.objects.filter(warehouse_id__in=warehouse_ids)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    return frozenset(int(owner_id) for owner_id in owner_ids if owner_id)


def _catalog_owner_ids(scope):
    if scope.is_global:
        return None
    if scope.owner_ids:
        return scope.owner_ids
    return _warehouse_owner_ids(scope)


def _parse_catalog_id(value, field_name):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: "必须是有效整数。"})


def _resolve_product_owner_scope(request, *, param_name="owner", default_to_user_owner=True):
    scope = _catalog_scope(request)
    requested_owner = _parse_catalog_id(request.query_params.get(param_name), param_name)
    allowed_owner_ids = _catalog_owner_ids(scope)
    if scope.is_global:
        if requested_owner:
            return requested_owner
        return None
    if requested_owner is not None and requested_owner not in allowed_owner_ids:
        raise PermissionDenied("无权查看该货主档案或商品。")
    if requested_owner is not None:
        return requested_owner
    if scope.owner_ids:
        return next(iter(scope.owner_ids))
    return None


def _resolve_inventory_warehouse_scope(request):
    scope = _catalog_scope(request)
    requested_wh = _parse_catalog_id(
        request.query_params.get("warehouse_id"), "warehouse_id"
    )
    if scope.is_global:
        return frozenset({requested_wh}) if requested_wh else None
    if scope.warehouse_ids:
        if requested_wh is not None and requested_wh not in scope.warehouse_ids:
            raise PermissionDenied("无权查看该仓库库存。")
        return frozenset({requested_wh}) if requested_wh else scope.warehouse_ids
    # Owner roles may inspect only explicitly associated warehouses.  This is
    # independent from the user's role scope: the role remains owner-bound.
    if scope.owner_ids:
        allowed_warehouses = owner_warehouse_ids(scope.single_owner_id)
        if requested_wh is not None and requested_wh not in allowed_warehouses:
            raise PermissionDenied("无权查看该仓库库存。")
        return frozenset({requested_wh}) if requested_wh else allowed_warehouses
    return frozenset()


def _product_carton_info(product):
    """Return replenish UOM code and its conversion from prefetched packages."""

    replenish_uom = getattr(product, "replenish_uom", None)
    unit_code = getattr(replenish_uom, "code", None)
    if not unit_code or not product.replenish_uom_id:
        return unit_code, None

    package = next(
        (
            candidate
            for candidate in product.packages.all()
            if candidate.uom_id == product.replenish_uom_id
        ),
        None,
    )
    return unit_code, getattr(package, "qty_in_base", None)


class WarehouseViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Warehouses explicitly enabled for the authenticated owner role."""

    permission_classes = [IsAuthenticated]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        scope = _catalog_scope(request)
        owner_id = scope.single_owner_id
        if not owner_id:
            raise PermissionDenied("当前账号没有单一有效货主范围。")
        warehouses = owner_warehouse_queryset(owner_id)
        return Response(
            [
                {"id": warehouse.id, "code": warehouse.code, "name": warehouse.name}
                for warehouse in warehouses
            ]
        )


class ProductViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = ProductPagination

    def list(self, request, *args, **kwargs):
        Product   = apps.get_model("products", "Product")
        InvDetail = apps.get_model("inventory", "InventoryDetail")
        scope = _catalog_scope(request)
        allowed_owner_ids = _catalog_owner_ids(scope)

        owner_id = _resolve_product_owner_scope(
            request,
            param_name="owner",
            default_to_user_owner=False,
        )
        if not owner_id and allowed_owner_ids is not None and not allowed_owner_ids:
            ctx, ctx_text = build_log_payload(
                user=request.user,
                warehouse_id=request.query_params.get("warehouse_id"),
            )
            logger.warning("outbound.product_list.owner_missing %s", ctx_text, extra=ctx)
            return Response([])
        ctx, ctx_text = build_log_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=request.query_params.get("warehouse_id"),
        )

        warehouse_ids = _resolve_inventory_warehouse_scope(request)

        # Only fetch the fields consumed below.  Including is_sales_default is
        # important: a deferred read here would otherwise produce one query per
        # package even though the relationship itself was prefetched.
        pkg_qs = (ProductPackage.objects
                  .select_related("uom")
                  .only("id", "product_id", "uom_id", "qty_in_base", "barcode",
                        "length_cm", "width_cm", "height_cm",
                        "gross_weight_kg", "volume_m3",
                        "is_purchase_default", "is_sales_default", "sort_order",
                        "uom__name", "uom__code"))

        qs = Product.objects.all()
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        elif allowed_owner_ids is not None:
            qs = qs.filter(owner_id__in=allowed_owner_ids)
        qs = (qs.select_related("base_uom", "replenish_uom",)
              .prefetch_related(Prefetch("packages", queryset=pkg_qs))
              .only("id", "owner_id", "code", "name", "sku", "spec","product_image","gtin","price","min_price","max_discount",
                    "base_uom__code","base_uom__name", "replenish_uom__code", "replenish_uom__name","replenish_uom_id")
              .order_by("id"))

        q = request.query_params.get("search")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(code__icontains=q) |
                Q(sku__icontains=q) |
                Q(gtin__icontains=q) |
                Q(unit_barcode__icontains=q) |
                Q(carton_barcode__icontains=q)
            )

        # Availability is part of the database queryset, so DRF's count and
        # slice see exactly the same set of products as the response.  This
        # avoids empty pages when early product ids have no stock.
        inventory = InvDetail.objects.filter(product_id=OuterRef("pk"))
        if owner_id:
            inventory = inventory.filter(owner_id=owner_id)
        elif allowed_owner_ids is not None:
            inventory = inventory.filter(owner_id__in=allowed_owner_ids)
        if warehouse_ids is not None:
            inventory = inventory.filter(warehouse_id__in=warehouse_ids)
        inventory = (
            inventory.values("product_id")
            .annotate(total=Sum("available_qty"))
            .values("total")[:1]
        )
        qs = qs.annotate(
            available=Coalesce(
                Subquery(
                    inventory,
                    output_field=DecimalField(max_digits=18, decimal_places=4),
                ),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=18, decimal_places=4),
            )
        ).filter(available__gt=0)

        page = self.paginate_queryset(qs)
        if not page:
            return self.get_paginated_response([])

        def default_sales_uom(p):
            default_package = next(
                (pkg for pkg in p.packages.all() if pkg.is_sales_default),
                None,
            )
            if default_package:
                return default_package.uom.name,default_package.qty_in_base
            return None


        def product_packaging(p):
            """获取商品所有包装信息，使用 ProductPackage 来表示包装"""
            packaging = []
            for pkg in p.packages.all():  # 使用 p.packages 获取商品的所有包装信息
                packaging.append({
                    'id': pkg.id,  # 包装的 ID
                    'uom_type': pkg.uom.name,  # 获取包装单位名称
                    'quantity_in_base': pkg.qty_in_base,  # 获取换算数量
                    'barcode': pkg.barcode,  # 获取条码
                    'length_cm': pkg.length_cm,  # 获取包装尺寸
                    'width_cm': pkg.width_cm,
                    'height_cm': pkg.height_cm,
                    'gross_weight_kg': pkg.gross_weight_kg,  # 获取毛重
                    'volume_m3': pkg.volume_m3,  # 获取体积
                    'is_sales_default': pkg.is_sales_default,
                })
            return packaging

        # === 在服务端拼出 _unitOptions / _selectedUnitIndex（最小新增逻辑） ===
        def build_unit_options(p, packaging):
            base_name = (getattr(getattr(p, "base_uom", None), "name", None) or
                         getattr(getattr(p, "base_uom", None), "code", None))
            opts = []
            if base_name:
                opts.append({
                    "key": "BASE",
                    "kind": "base",
                    "label": base_name,
                    "multiplier": 1,
                    "package_id": None,
                    "barcode": None,
                })
            for row in packaging:
                # 跳过与基本单位 1:1 的冗余项
                if row["quantity_in_base"] == 1 and row["uom_type"] == base_name:
                    continue
                opts.append({
                    "key": row["id"],
                    "kind": "package",
                    "label": row["uom_type"],
                    "multiplier": row["quantity_in_base"],
                    "package_id": row["id"],
                    "barcode": row["barcode"],
                })
            return opts

        def default_selected_index(packaging, unit_opts):
            # 优先选择 is_sales_default 的包装；否则 0（通常为基本单位）
            sales_pkg_id = next((r["id"] for r in packaging if r.get("is_sales_default")), None)
            if sales_pkg_id is not None:
                for i, o in enumerate(unit_opts):
                    if o["package_id"] == sales_pkg_id:
                        return i
            return 0


        data = []
        for p in page:
            packaging = product_packaging(p)
            unit_opts = build_unit_options(p, packaging)  # ← 基本单位 + 包装
            sel_idx = default_selected_index(packaging, unit_opts)


            carton_unit, carton_conv = _product_carton_info(p)

            sales_uom = default_sales_uom(p)
            if sales_uom:
              aux_uom_name,aux_qty_in_base=sales_uom
            else:
              aux_uom_name=None
              aux_qty_in_base=None

            product_image_url = None
            if p.product_image:
                product_image_url = request.build_absolute_uri(p.product_image.url)
                # product_image_url = "http://192.168.1.6:8001"+p.product_image.url  # 获取图片的 URL 地址

            data.append({
                    "id": p.id,
                    "sku": p.sku or p.code or "",
                    "name": p.name or "",
                    "spec": p.spec,
                    "base_unit": getattr(getattr(p, "base_uom", None), "code", None),
                    "base_unit_name": getattr(getattr(p, "base_uom", None), "name", None),
                    "carton_unit": carton_unit,
                    "carton_conv": carton_conv,
                    "available": p.available,
                    "price": getattr(p, "price", None) or getattr(p, "sale_price", None) or 0,
                    "product_image_url":product_image_url,
                    "gtin":p.gtin,
                    "aux_uom_name":aux_uom_name,
                    "aux_qty_in_base":aux_qty_in_base,
                    "max_discount": p.max_discount ,
                    "product_min_price": p.min_price,
                    "unitOptions": unit_opts,
                    "selectedUnitIndex": sel_idx,
                    "base_quantity": 0,
                })
        logger.debug("outbound.product_list.response %s count=%s", ctx_text, len(data), extra=ctx)
        return self.get_paginated_response(data)

class CustomerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination     # 你项目里已有的分页类
    filter_backends = []
    queryset = apps.get_model("baseinfo", "Customer").objects.none()

    def get_queryset(self):
        Customer = apps.get_model("baseinfo", "Customer")
        user = self.request.user
        scope = _catalog_scope(self.request)
        qs = Customer.objects.all()
        if not scope.is_global:
            if scope.owner_ids:
                qs = qs.filter(owner_id__in=scope.owner_ids)
                if UserRoleScope.Role.OWNER_MANAGER not in scope.roles:
                    qs = qs.filter(salesperson=user)
            else:
                param_name = (
                    "owner_id"
                    if self.request.query_params.get("owner_id") is not None
                    else "owner"
                )
                owner_id = _resolve_product_owner_scope(
                    self.request,
                    param_name=param_name,
                    default_to_user_owner=False,
                )
                qs = qs.filter(owner_id=owner_id) if owner_id else qs.none()
        else:
            requested_owner = self.request.query_params.get("owner_id") or self.request.query_params.get("owner")
            if requested_owner:
                qs = qs.filter(
                    owner_id=_parse_catalog_id(requested_owner, "owner_id")
                )

        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

        return qs.order_by("id")

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        data = [{"id": c.id, "code": c.code, "name": c.name} for c in page]
        return self.get_paginated_response(data)

class OwnerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination     # 你项目里已有的分页类
    filter_backends = []
    queryset = apps.get_model("baseinfo", "Owner").objects.none()

    def get_queryset(self):
        Owner = apps.get_model("baseinfo", "Owner")
        scope = _catalog_scope(self.request)
        qs = Owner.objects.all()
        allowed_owner_ids = _catalog_owner_ids(scope)
        if allowed_owner_ids is not None:
            qs = qs.filter(id__in=allowed_owner_ids)

        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

        return qs.order_by("id")

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        data = [{"id": c.id, "code": c.code, "name": c.name} for c in page]
        return self.get_paginated_response(data)

class SupplierViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination     # 你项目里已有的分页类
    filter_backends = []
    queryset = apps.get_model("baseinfo", "Supplier").objects.none()

    def get_queryset(self):
        Supplier = apps.get_model("baseinfo", "Supplier")
        qs = Supplier.objects.all()

        q = self.request.query_params.get("search")
        o = _resolve_product_owner_scope(self.request, param_name="owner")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

        if o:
            qs = qs.filter(Q(owner_id=o))
        else:

            raise ValidationError({"detail": "owner 参数是必需的"})

        return qs.order_by("id")

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        data = [{"id": c.id, "code": c.code, "name": c.name} for c in page]
        return self.get_paginated_response(data)

class ReceiveProductViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = ReceiveProductPagination

    def list(self, request, *args, **kwargs):
        Product   = apps.get_model("products", "Product")
        ProductPackage = apps.get_model("products", "ProductPackage")

        owner_id = _resolve_product_owner_scope(request, param_name="owner")
        if not owner_id:
            ctx, ctx_text = build_log_payload(user=request.user)
            logger.warning("outbound.receive_product_list.owner_missing %s", ctx_text, extra=ctx)
            return Response([])
        ctx, ctx_text = build_log_payload(user=request.user, owner_id=owner_id)

        # 预取 packages + uom，减少 N+1
        # pkg_qs = (ProductPackage.objects
        #           .select_related("uom")
        #           .only("id","product_id","uom_id","qty_in_base","barcode",
        #                 "is_sales_default","sort_order",
        #                 "uom__code","uom__name"))

        # ✅ 只预取“当前查询命中的产品”的包装，并连带 uom，限制字段，避免 N+1
        pkg_qs = (ProductPackage.objects
                  .select_related("uom")
                  .only("id", "product_id", "uom_id", "qty_in_base", "barcode",
                        "length_cm", "width_cm", "height_cm",
                        "gross_weight_kg", "volume_m3",
                        "is_purchase_default", "sort_order",
                        "uom__name", "uom__code"))


        # qs = (Product.objects
        #       .filter(owner_id=owner_id)
        #       .select_related("base_uom", "replenish_uom",)
        #       .only("id", "owner_id", "code", "name", "sku", "spec","product_image","gtin","min_price","max_discount",
        #             "base_uom__code","base_uom__name", "replenish_uom__code", "replenish_uom__name","replenish_uom_id")
        #       .order_by("id"))

        qs = (Product.objects
              .filter(owner_id=owner_id)
              .select_related("base_uom", "replenish_uom")
              .prefetch_related(Prefetch("packages", queryset=pkg_qs))
              .only("id", "owner_id", "code", "name", "sku", "spec", "product_image", "gtin",
                    "price", "min_price", "max_discount", "base_uom__name", "base_uom__code",
                    "replenish_uom_id", "replenish_uom__name", "replenish_uom__code")
              .order_by("id"))


        q = request.query_params.get("search")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(code__icontains=q) |
                Q(sku__icontains=q) |
                Q(gtin__icontains=q) |
                Q(unit_barcode__icontains=q) |
                Q(carton_barcode__icontains=q)
            ).distinct()



        page = self.paginate_queryset(qs)
        if not page:
            return self.get_paginated_response([])

        def default_sales_uom(p):
            # 查找 product 的所有 package，返回 is_sales_default 为 True 的 UOM 的名称
            default_package = p.packages.filter(is_sales_default=True).first()
            if default_package:
                return default_package.uom.name,default_package.qty_in_base
            return None

        def product_packaging(p):
            """获取商品所有包装信息，使用 ProductPackage 来表示包装"""
            packaging = []
            for pkg in p.packages.all():  # 使用 p.packages 获取商品的所有包装信息
                packaging.append({
                    'id': pkg.id,  # 包装的 ID
                    'uom_type': pkg.uom.name,  # 获取包装单位名称
                    'quantity_in_base': pkg.qty_in_base,  # 获取换算数量
                    'barcode': pkg.barcode,  # 获取条码
                    'length_cm': pkg.length_cm,  # 获取包装尺寸
                    'width_cm': pkg.width_cm,
                    'height_cm': pkg.height_cm,
                    'gross_weight_kg': pkg.gross_weight_kg,  # 获取毛重
                    'volume_m3': pkg.volume_m3,  # 获取体积
                })
            return packaging

        # === 在服务端拼出 _unitOptions / _selectedUnitIndex（最小新增逻辑） ===
        def build_unit_options(p, packaging):
            base_name = (getattr(getattr(p, "base_uom", None), "name", None) or
                         getattr(getattr(p, "base_uom", None), "code", None))
            opts = []
            if base_name:
                opts.append({
                    "key": "BASE",
                    "kind": "base",
                    "label": base_name,
                    "multiplier": 1,
                    "package_id": None,
                    "barcode": None,
                })
            for row in packaging:
                # 跳过与基本单位 1:1 的冗余项
                if row["quantity_in_base"] == 1 and row["uom_type"] == base_name:
                    continue
                opts.append({
                    "key": row["id"],
                    "kind": "package",
                    "label": row["uom_type"],
                    "multiplier": row["quantity_in_base"],
                    "package_id": row["id"],
                    "barcode": row["barcode"],
                })
            return opts

        def default_selected_index(packaging, unit_opts):
            # 优先选择 is_sales_default 的包装；否则 0（通常为基本单位）
            sales_pkg_id = next((r["id"] for r in packaging if r.get("is_sales_default")), None)
            if sales_pkg_id is not None:
                for i, o in enumerate(unit_opts):
                    if o["package_id"] == sales_pkg_id:
                        return i
            return 0


        data = []
        for p in page:
            # product_packaging_info = product_packaging(p)
            packaging = product_packaging(p)
            unit_opts = build_unit_options(p, packaging)  # ← 基本单位 + 包装
            sel_idx = default_selected_index(packaging, unit_opts)

            carton_unit, carton_conv = _product_carton_info(p)

            if default_sales_uom(p):
              aux_uom_name,aux_qty_in_base=default_sales_uom(p)
            else:
              aux_uom_name=None
              aux_qty_in_base=None

            product_image_url = None
            if p.product_image:
                product_image_url = request.build_absolute_uri(p.product_image.url)
                # product_image_url = "http://192.168.1.6:8001"+p.product_image.url  # 获取图片的 URL 地址


            data.append({
                "id": p.id,
                "sku": p.sku or p.code or "",
                "name": p.name or "",
                "spec": p.spec,
                "base_unit": getattr(getattr(p, "base_uom", None), "code", None),
                "base_unit_name": getattr(getattr(p, "base_uom", None), "name", None),
                "carton_unit": carton_unit,
                "carton_conv": carton_conv,
                "price": getattr(p, "price", None) or getattr(p, "sale_price", None) or 0,
                "product_image_url":product_image_url,
                "gtin":p.gtin,
                "aux_uom_name":aux_uom_name,
                "aux_qty_in_base":aux_qty_in_base,
                "max_discount": p.max_discount ,
                "product_min_price": p.min_price,
                "packaging": packaging,  # 包装信息返回，包括 id

                # === 新增：用于前端单选/回填到购物车
                "unitOptions": unit_opts,
                "selectedUnitIndex": sel_idx,
                "base_quantity":0,
                # "selectedUnit": sel_unit,  # 若不想冗余，可不下发这个，前端用 index 取
            })
        logger.debug("outbound.receive_product_list.response %s count=%s", ctx_text, len(data), extra=ctx)
        return self.get_paginated_response(data)

class AssistedOutboundOrderViewSet(viewsets.GenericViewSet):
    """Strict warehouse-assisted outbound entry and owner-scoped catalogs."""

    permission_classes = [IsAuthenticated]
    serializer_class = AssistedOutboundOrderCreateSerializer
    pagination_class = AssistedHistoryPagination

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_assisted_operator(request.user)

    def _enabled_owner(self, owner_id):
        Owner = apps.get_model("baseinfo", "Owner")
        try:
            owner_id = int(owner_id)
        except (TypeError, ValueError):
            raise ValidationError({"owner_id": "owner_id 必须是有效整数。"})
        scope = _catalog_scope(self.request)
        allowed_owner_ids = _warehouse_owner_ids(scope)
        if owner_id not in allowed_owner_ids:
            raise PermissionDenied("该货主未关联当前授权仓库。")
        return get_object_or_404(
            Owner.objects.filter(
                is_active=True,
                allow_warehouse_assisted_outbound=True,
                pk__in=allowed_owner_ids,
            ),
            pk=owner_id,
        )

    @action(detail=False, methods=["get"])
    def owners(self, request):
        Owner = apps.get_model("baseinfo", "Owner")
        allowed_owner_ids = _warehouse_owner_ids(_catalog_scope(request))
        qs = Owner.objects.filter(
            is_active=True,
            allow_warehouse_assisted_outbound=True,
            pk__in=allowed_owner_ids,
        ).order_by("id")
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(code__icontains=search) | Q(name__icontains=search))
        return Response([{"id": owner.id, "code": owner.code, "name": owner.name} for owner in qs])

    @action(detail=False, methods=["get"])
    def customers(self, request):
        owner_id = request.query_params.get("owner_id")
        if not owner_id:
            raise ValidationError({"owner_id": "owner_id 参数必填。"})
        owner = self._enabled_owner(owner_id)
        Customer = apps.get_model("baseinfo", "Customer")
        qs = Customer.objects.filter(owner=owner, is_active=True).order_by("id")
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(code__icontains=search) | Q(name__icontains=search))
        return Response(
            [
                {"id": customer.id, "code": customer.code, "name": customer.name}
                for customer in qs
            ]
        )

    @action(detail=False, methods=["get"], url_path="history-options")
    def history_options(self, request):
        return Response(assisted_history_options(request.user))

    @action(detail=False, methods=["get"])
    def history(self, request):
        qs = assisted_history_queryset(request.user)
        qs = filter_history_queryset(qs, request.query_params)
        qs = qs.order_by(F("assisted_at").desc(nulls_last=True), "-id")
        page = self.paginate_queryset(qs)
        if page is None:
            return Response([serialize_history_order(order) for order in qs])
        return self.get_paginated_response(
            [serialize_history_order(order) for order in page]
        )

    @action(detail=False, methods=["get"])
    def stats(self, request):
        return Response(build_assisted_outbound_stats(request.user, request.query_params))

    @action(detail=False, methods=["get"])
    def products(self, request):
        owner_id = request.query_params.get("owner_id")
        if not owner_id:
            raise ValidationError({"owner_id": "owner_id 参数必填。"})
        owner = self._enabled_owner(owner_id)
        scope = _catalog_scope(request)
        search = (request.query_params.get("search") or "").strip()
        if not search:
            return Response([])
        Product = apps.get_model("products", "Product")
        InventoryDetail = apps.get_model("inventory", "InventoryDetail")
        package_qs = (
            ProductPackage.objects.filter(is_active=True, uom__is_active=True)
            .select_related("uom")
            .order_by("sort_order", "id")
        )
        qs = (
            Product.objects.filter(owner=owner, is_active=True)
            .select_related("base_uom")
            .prefetch_related(Prefetch("packages", queryset=package_qs))
        )
        qs = qs.filter(
            Q(code__icontains=search)
            | Q(sku__icontains=search)
            | Q(name__icontains=search)
            | Q(gtin__icontains=search)
            | Q(unit_barcode__icontains=search)
            | Q(carton_barcode__icontains=search)
            | Q(product_package__barcode__icontains=search)
        ).distinct()
        qs = qs.order_by("id")
        product_ids = list(qs.values_list("id", flat=True))
        available = {
            row["product_id"]: row["available"] or Decimal("0")
            for row in (
                InventoryDetail.objects.filter(
                    owner=owner,
                    warehouse_id__in=scope.warehouse_ids,
                    product_id__in=product_ids,
                    is_active=True,
                )
                .values("product_id")
                .annotate(available=Sum("available_qty"))
            )
        }
        data = []
        for product in qs:
            price = outbound_services.get_default_product_price(product)
            available_qty = available.get(product.id, Decimal("0"))
            base_label = (
                getattr(product.base_uom, "name", None)
                or getattr(product.base_uom, "code", None)
                or "基本单位"
            )
            unit_options = [
                {
                    "key": "BASE",
                    "kind": "base",
                    "label": base_label,
                    "multiplier": 1,
                    "package_id": None,
                    "barcode": getattr(product, "unit_barcode", None),
                }
            ]
            default_package_id = None
            for package in product.packages.all():
                package_label = package.uom.name or package.uom.code
                if package.qty_in_base == 1 and package_label == base_label:
                    continue
                unit_options.append(
                    {
                        "key": package.id,
                        "kind": "package",
                        "label": package_label,
                        "multiplier": package.qty_in_base,
                        "package_id": package.id,
                        "barcode": package.barcode,
                    }
                )
                if package.is_sales_default:
                    default_package_id = package.id
            selected_unit_index = next(
                (
                    index
                    for index, option in enumerate(unit_options)
                    if option["package_id"] == default_package_id
                ),
                0,
            )
            data.append(
                {
                    "id": product.id,
                    "code": product.code,
                    "sku": product.sku,
                    "gtin": product.gtin,
                    "name": product.name,
                    "spec": product.spec,
                    "base_unit": getattr(product.base_uom, "code", None),
                    "base_unit_name": getattr(product.base_uom, "name", None),
                    "default_price": price if price > 0 else None,
                    "price": price if price > 0 else None,
                    "available_qty": available_qty,
                    "available": available_qty,
                    "unitOptions": unit_options,
                    "selectedUnitIndex": selected_unit_index,
                }
            )
        return Response(data)

    def _response(self, order, task, *, idempotent, http_status):
        return Response(
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "task_id": task.id,
                "task_no": task.task_no,
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
                "task_status": task.status,
                "idempotent": idempotent,
                "replayed": idempotent,
            },
            status=http_status,
        )

    @staticmethod
    def _canonical_datetime(value):
        if value in (None, ""):
            return None
        parsed = value if isinstance(value, datetime) else parse_datetime(str(value).strip())
        if parsed is None:
            return str(value).strip()
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        parsed = parsed.astimezone(datetime_timezone.utc)
        return parsed.isoformat()

    @staticmethod
    def _canonical_qty(value):
        try:
            return format(Decimal(str(value)).quantize(Decimal("0.001")), "f")
        except (ArithmeticError, InvalidOperation, TypeError, ValueError):
            return "__invalid__"

    @classmethod
    def _canonical_optional_qty(cls, value):
        if value in (None, ""):
            return None
        return cls._canonical_qty(value)

    def _request_fingerprint(self, payload):
        raw_items = payload.get("items")
        if not isinstance(raw_items, (list, tuple)):
            items = "__invalid__"
        else:
            items = sorted(
                (
                    str(item.get("product_id")),
                    self._canonical_qty(item.get("qty")),
                    str(item.get("package_id"))
                    if item.get("package_id") not in (None, "")
                    else None,
                    self._canonical_optional_qty(item.get("package_qty")),
                )
                for item in raw_items
                if isinstance(item, dict)
            )
            if len(items) != len(raw_items):
                items = "__invalid__"
        return {
            "owner_id": str(payload.get("owner_id")),
            "customer_id": str(payload.get("customer_id")),
            "items": items,
            "src_bill_no": (payload.get("src_bill_no") or "").strip(),
            "delivery_method": payload.get("delivery_method") or None,
            "etd": self._canonical_datetime(payload.get("etd")),
            "contact": (payload.get("contact") or "").strip(),
            "contact_phone": (payload.get("contact_phone") or "").strip(),
            "ship_to": (payload.get("ship_to") or "").strip(),
            "remark": (payload.get("remark") or "").strip(),
            "assistance_reason": (payload.get("assistance_reason") or "").strip(),
        }

    def _persisted_fingerprint(self, order):
        return {
            "owner_id": str(order.owner_id),
            "customer_id": str(order.customer_id),
            "items": sorted(
                (
                    str(line.product_id),
                    self._canonical_qty(line.base_qty),
                    str(getattr(line, "aux_uom_id", None))
                    if getattr(line, "aux_uom_id", None) is not None
                    else None,
                    self._canonical_optional_qty(getattr(line, "aux_qty", None)),
                )
                for line in order.lines.filter(is_deleted=False)
            ),
            "src_bill_no": (order.src_bill_no or "").strip(),
            "delivery_method": order.delivery_method or None,
            "etd": self._canonical_datetime(order.etd),
            "contact": (order.contact or "").strip(),
            "contact_phone": (order.contact_phone or "").strip(),
            "ship_to": (order.ship_to or "").strip(),
            "remark": (order.memo or "").strip(),
            "assistance_reason": (order.assistance_reason or "").strip(),
        }

    @staticmethod
    def _supplied_prices_match(payload, order):
        """Compare prices only when the retry explicitly supplies one.

        An omitted price means "use the server default (or zero)", so the raw
        request cannot reliably reconstruct whether the original request also
        omitted it.  Explicit prices, however, must always match the persisted
        order line for the request id to be considered the same business call.
        """

        supplied_items = [
            item
            for item in (payload.get("items") or [])
            if isinstance(item, dict) and item.get("price") not in (None, "")
        ]
        if not supplied_items:
            return True

        lines = {
            line.product_id: Decimal(line.base_price or 0)
            for line in order.lines.filter(is_deleted=False)
        }
        for item in supplied_items:
            try:
                product_id = int(item.get("product_id"))
                supplied = Decimal(str(item["price"])).quantize(Decimal("0.0001"))
                persisted = lines[product_id].quantize(Decimal("0.0001"))
            except (ArithmeticError, InvalidOperation, KeyError, TypeError, ValueError):
                return False
            if supplied != persisted:
                return False
        return True

    def _idempotent_result(self, payload, user):
        request_id = payload.get("request_id")
        try:
            request_uuid = UUID(str(request_id))
        except (TypeError, ValueError, AttributeError):
            return None
        order = OutboundOrder.objects.filter(assistance_request_id=request_uuid).first()
        if order is None:
            return None
        scope = AccessScope.for_user(user)
        warehouse_id = (
            next(iter(scope.warehouse_ids))
            if scope.is_valid and len(scope.warehouse_ids) == 1
            else None
        )
        if (
            order.assisted_by_id != user.id
            or warehouse_id is None
            or order.warehouse_id != warehouse_id
            or self._request_fingerprint(payload) != self._persisted_fingerprint(order)
            or not self._supplied_prices_match(payload, order)
        ):
            raise IdempotencyConflict()
        task = (
            WmsTask.objects.filter(task_type=WmsTask.TaskType.PICK)
            .filter(outbound_services._task_source_q(order))
            .order_by("id")
            .first()
        )
        if task is None:
            raise ValidationError("幂等订单缺少关联拣货任务，请联系管理员。")
        return order, task

    def create(self, request, *args, **kwargs):
        existing = self._idempotent_result(request.data, request.user)
        if existing:
            return self._response(*existing, idempotent=True, http_status=status.HTTP_200_OK)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order, task = outbound_services.create_warehouse_assisted_order(
                validated_data=serializer.validated_data,
                by_user=request.user,
            )
        except IntegrityError:
            existing = self._idempotent_result(request.data, request.user)
            if existing:
                return self._response(
                    *existing,
                    idempotent=True,
                    http_status=status.HTTP_200_OK,
                )
            raise
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from exc
        record_audit_event(
            action="outbound.assisted.create_release",
            module="outbound",
            request=request,
            obj=order,
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
                "task_id": task.id,
                "task_status": task.status,
            },
        )
        return self._response(
            order,
            task,
            idempotent=False,
            http_status=status.HTTP_201_CREATED,
        )


class OutboundOrderViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = OutboundOrder.objects.all().order_by("-biz_date", "-id")
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination

    def _access_scope(self):
        if not hasattr(self, "_resolved_access_scope"):
            self._resolved_access_scope = AccessScope.for_user(self.request.user)
        return self._resolved_access_scope

    @staticmethod
    def _optimized_order_queryset():
        line_qs = OutboundOrderLine.objects.select_related(
            "product",
            "product__base_uom",
            "base_uom",
            "aux_uom",
        ).order_by("line_no", "id")
        return (
            OutboundOrder.objects.select_related(
                "owner",
                "warehouse",
                "customer",
                "supplier",
                "created_by",
                "priced_by",
                "assisted_by",
            )
            .prefetch_related(Prefetch("lines", queryset=line_qs))
            .annotate(
                catalog_total_qty=Coalesce(
                    Sum(
                        "lines__base_qty",
                        filter=Q(lines__is_deleted=False),
                    ),
                    Value(Decimal("0")),
                    output_field=DecimalField(max_digits=20, decimal_places=4),
                ),
                catalog_total_amount=Coalesce(
                    Sum(
                        ExpressionWrapper(
                            F("lines__base_qty") * F("lines__base_price"),
                            output_field=DecimalField(max_digits=28, decimal_places=4),
                        ),
                        filter=Q(lines__is_deleted=False),
                    ),
                    Value(Decimal("0")),
                    output_field=DecimalField(max_digits=28, decimal_places=4),
                ),
            )
        )

    def get_queryset(self):
        base = self._optimized_order_queryset().order_by("-biz_date", "-id")
        scope = self._access_scope()
        scoped = strict_order_queryset(base, self.request.user, scope=scope)
        # Assisted operators are newly introduced, tightly scoped identities;
        # never grant them historical fail-open visibility in shadow mode.
        if is_assisted_operator(self.request.user):
            return scoped
        # Compatibility applies only to historical STANDARD records.  Newly
        # introduced assisted data is always visible through the strict scope,
        # even while the broader legacy rollout remains in shadow mode.
        permitted_assisted = strict_order_queryset(
            base.filter(processing_mode="WAREHOUSE_ASSISTED"),
            self.request.user,
            scope=scope,
        )
        shadow_base = base.filter(
            Q(processing_mode="STANDARD") | Q(pk__in=permitted_assisted.values("pk"))
        )
        return apply_legacy_scope(
            base_qs=shadow_base,
            scoped_qs=scoped,
            user=self.request.user,
            endpoint=f"outbound.orders.{getattr(self, 'action', 'unknown')}",
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["access_scope"] = self._access_scope()
        return context

    def _require_owner_buyer(self, endpoint):
        user = self.request.user
        scope = AccessScope.for_user(user)
        allowed = bool(
            user.is_superuser
            or (
                user.has_perm("outbound.submit_outbound_as_owner_buyers")
                and scope.is_valid
                and scope.owner_ids
                and UserRoleScope.Role.OWNER_SALESPERSON in scope.roles
            )
        )
        require_legacy_action(
            user=user,
            allowed=allowed,
            endpoint=endpoint,
            reason="需要单一有效货主范围的货主业务员权限。",
        )

    def _require_owner_manager(self, order, endpoint):
        user = self.request.user
        scope = AccessScope.for_user(user)
        allowed = bool(
            user.is_superuser
            or (
                user.has_perm("outbound.approve_outbound_as_owner_manager")
                and scope.owner_ids
                and scope.allows(
                    owner_id=order.owner_id,
                    warehouse_id=order.warehouse_id,
                )
            )
        )
        require_legacy_action(
            user=user,
            allowed=allowed,
            endpoint=endpoint,
            reason="需要当前货主的出库审核权限。",
        )

    def _require_warehouse_manager(self, order, endpoint):
        user = self.request.user
        scope = AccessScope.for_user(user)
        allowed = bool(
            user.is_superuser
            or (
                user.has_perm("outbound.approve_outbound_as_wh_manager")
                and scope.is_valid
                and UserRoleScope.Role.WAREHOUSE_MANAGER in scope.roles
                and scope.allows(
                    owner_id=order.owner_id,
                    warehouse_id=order.warehouse_id,
                )
            )
        )
        require_legacy_action(
            user=user,
            allowed=allowed,
            endpoint=endpoint,
            reason="需要当前仓库的仓库管理员权限。",
        )

    def get_serializer_class(self):
        if self.action == "create":
            return OutboundOrderCreateSerializer
        if self.action == "update":
            return OutboundOrderDraftUpdateSerializer
        return OutboundOrderReadSerializer

    @staticmethod
    def _editable_order_error(order, user):
        if order.created_by_id != user.id:
            raise PermissionDenied("仅订单原创建业务员可以修改该订单。")
        if order.processing_mode != "STANDARD":
            raise ValidationError({"detail": "仓库代办订单不支持货主端修改。"})
        if order.is_closed:
            raise ValidationError({"detail": "已关闭订单不能修改。"})
        if order.submit_status != "DRAFT" or order.approval_status not in {
            "OWNER_PENDING",
            "OWNER_REJECTED",
        }:
            raise ValidationError({"detail": "当前订单状态不允许修改。"})

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """Atomically replace the business content of an editable owner draft."""

        current = self.get_object()
        self._require_owner_buyer("outbound.orders.update")
        self._editable_order_error(current, request.user)

        serializer = OutboundOrderDraftUpdateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        fingerprint = self._standard_order_fingerprint(serializer.validated_data)

        order = (
            OutboundOrder.objects.select_for_update()
            .select_related("owner", "warehouse", "customer")
            .get(pk=current.pk)
        )
        self._editable_order_error(order, request.user)
        expected_updated_at = serializer.validated_data["expected_updated_at"]
        if expected_updated_at != order.updated_at:
            raise StaleOrderEdit(order.updated_at)

        src_bill_no = serializer.validated_data.get("src_bill_no")
        duplicate = (
            OutboundOrder.all_objects.filter(
                owner_id=order.owner_id,
                src_bill_no=src_bill_no,
            )
            .exclude(pk=order.pk)
            .order_by("id")
            .first()
            if src_bill_no
            else None
        )
        if duplicate:
            raise self._duplicate_source_error(duplicate)

        if order.idempotency_fingerprint == fingerprint:
            data = dict(
                OutboundOrderReadSerializer(
                    order, context={"request": request}
                ).data
            )
            data["changed"] = False
            return Response(data)

        before = {
            "warehouse_id": order.warehouse_id,
            "customer_id": order.customer_id,
            "src_bill_no": order.src_bill_no,
            "active_line_ids": list(order.lines.values_list("id", flat=True)),
        }
        validated = serializer.validated_data
        order.warehouse_id = validated["warehouse_id__from_user"]
        order.customer_id = validated.get("customer_id")
        order.supplier_id = validated.get("supplier_id")
        order.outbound_type = validated.get("outbound_type", "SALES")
        order.delivery_method = validated.get("delivery_method")
        order.etd = validated.get("etd")
        order.memo = validated.get("remark", "")
        order.src_bill_no = src_bill_no
        order.contact = validated.get("contact", "")
        order.contact_phone = validated.get("contact_phone", "")
        order.ship_to = validated.get("ship_to", "")
        order.idempotency_fingerprint = fingerprint
        order.pricing_status = PricingStatus.PENDING
        order.priced_at = None
        order.priced_by = None
        order.final_order_amount = Decimal("0.00")
        order.updated_by = request.user
        order.save(
            update_fields=[
                "warehouse",
                "customer",
                "supplier",
                "outbound_type",
                "delivery_method",
                "etd",
                "memo",
                "src_bill_no",
                "contact",
                "contact_phone",
                "ship_to",
                "idempotency_fingerprint",
                "pricing_status",
                "priced_at",
                "priced_by",
                "final_order_amount",
                "updated_by",
                "updated_at",
            ]
        )

        order.lines.update(
            is_deleted=True,
            deleted_at=timezone.now(),
            deleted_by=request.user,
        )
        for index, item in enumerate(validated["items"]):
            try:
                OutboundOrderLine.objects.create(
                    order=order,
                    product_id=item["product_id"],
                    base_qty=item["qty"],
                    base_price=item.get("price") or Decimal("0.0000"),
                    created_by=request.user,
                )
            except DjangoValidationError as exc:
                errors = [{} for _ in validated["items"]]
                errors[index] = OutboundOrderCreateSerializer._line_model_validation_detail(exc)
                raise ValidationError({"items": errors}) from exc

        record_audit_event(
            action="outbound.order.update_draft",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={
                "warehouse_id": order.warehouse_id,
                "customer_id": order.customer_id,
                "src_bill_no": order.src_bill_no,
                "active_line_count": len(validated["items"]),
            },
        )
        order.refresh_from_db()
        data = dict(
            OutboundOrderReadSerializer(order, context={"request": request}).data
        )
        data["changed"] = True
        return Response(data)

    @action(detail=True, methods=["get"], url_path="edit-context")
    def edit_context(self, request, pk=None):
        order = self.get_object()
        self._require_owner_buyer("outbound.orders.edit_context")
        self._editable_order_error(order, request.user)

        lines = list(
            order.lines.select_related("product", "product__base_uom").order_by("line_no")
        )
        product_ids = [line.product_id for line in lines]
        available = {
            row["product_id"]: row["available"] or Decimal("0")
            for row in apps.get_model("inventory", "InventoryDetail")
            .objects.filter(
                owner_id=order.owner_id,
                warehouse_id=order.warehouse_id,
                product_id__in=product_ids,
            )
            .values("product_id")
            .annotate(available=Sum("available_qty"))
        }
        items = []
        for line in lines:
            product = line.product
            image_url = None
            if getattr(product, "product_image", None):
                image_url = request.build_absolute_uri(product.product_image.url)
            base_name = (
                getattr(product.base_uom, "name", None)
                or getattr(product.base_uom, "code", None)
                or "基本单位"
            )
            items.append(
                {
                    "id": product.id,
                    "product_id": product.id,
                    "sku": product.sku or product.code or "",
                    "name": product.name or "",
                    "spec": product.spec,
                    "price": line.base_price,
                    "orig_price": product.price,
                    "min_price": product.min_price,
                    "product_min_price": product.min_price,
                    "max_discount": product.max_discount,
                    "qty": line.base_qty,
                    "available": available.get(product.id, Decimal("0")),
                    "product_image_url": image_url,
                    "gtin": product.gtin,
                    "base_unit_name": base_name,
                    "unitOptions": [
                        {
                            "key": "BASE",
                            "kind": "base",
                            "label": base_name,
                            "multiplier": 1,
                            "package_id": None,
                            "barcode": None,
                        }
                    ],
                    "selectedUnitIndex": 0,
                }
            )
        return Response(
            {
                "id": order.id,
                "updated_at": order.updated_at,
                "owner_reject_reason": order.owner_reject_reason,
                "warehouse": {
                    "id": order.warehouse_id,
                    "code": order.warehouse.code,
                    "name": order.warehouse.name,
                },
                "customer": (
                    {
                        "id": order.customer_id,
                        "code": order.customer.code,
                        "name": order.customer.name,
                    }
                    if order.customer_id
                    else None
                ),
                "header": {
                    "outbound_type": order.outbound_type,
                    "delivery_method": order.delivery_method,
                    "etd": order.etd,
                    "remark": order.memo,
                    "src_bill_no": order.src_bill_no or "",
                    "contact": order.contact or "",
                    "contact_phone": order.contact_phone or "",
                    "ship_to": order.ship_to or "",
                },
                "items": items,
            }
        )

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        q = request.query_params.get("search")
        product_q = request.query_params.get("product") or request.query_params.get("sku")
        owner_id = request.query_params.get("owner_id")
        warehouse_id = request.query_params.get("warehouse_id")
        submit_status = request.query_params.get("submit_status")
        approval_status = request.query_params.get("approval_status")
        outbound_type = request.query_params.get("outbound_type")
        delivery_method = request.query_params.get("delivery_method")
        source_no = request.query_params.get("src_bill_no")
        task_no = request.query_params.get("task_no")
        task_status = request.query_params.get("task_status")
        waybill_no = request.query_params.get("waybill_no")

        if q:
            qs = qs.filter(
                Q(order_no__icontains=q) |
                Q(src_bill_no__icontains=q) |
                Q(contact__icontains=q) | Q(contact_phone__icontains=q) |
                Q(customer__name__icontains=q) | Q(customer__code__icontains=q) |
                Q(supplier__name__icontains=q) |
                Q(lines__product__name__icontains=q) |
                Q(lines__product__code__icontains=q) |
                Q(lines__product__sku__icontains=q) |
                Q(lines__product__gtin__icontains=q) |
                Q(lines__product__unit_barcode__icontains=q) |
                Q(lines__product__carton_barcode__icontains=q) |
                Q(lines__product__product_package__barcode__icontains=q)
            )
        if product_q:
            qs = qs.filter(
                Q(lines__product__name__icontains=product_q)
                | Q(lines__product__code__icontains=product_q)
                | Q(lines__product__sku__icontains=product_q)
                | Q(lines__product__gtin__icontains=product_q)
                | Q(lines__product__unit_barcode__icontains=product_q)
                | Q(lines__product__carton_barcode__icontains=product_q)
                | Q(lines__product__product_package__barcode__icontains=product_q)
            )
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if submit_status:
            qs = qs.filter(submit_status=submit_status)
        if approval_status:
            qs = qs.filter(approval_status=approval_status)
        if outbound_type:
            qs = qs.filter(outbound_type=outbound_type)
        if delivery_method:
            qs = qs.filter(delivery_method=delivery_method)
        if source_no:
            qs = qs.filter(src_bill_no__icontains=source_no)

        date_from = request.query_params.get("date_from") or request.query_params.get(
            "biz_date_from"
        )
        date_to = request.query_params.get("date_to") or request.query_params.get(
            "biz_date_to"
        )
        if date_from:
            parsed = parse_date(date_from)
            if parsed is None:
                raise ValidationError({"date_from": "日期格式必须为 YYYY-MM-DD。"})
            qs = qs.filter(biz_date__gte=parsed)
        if date_to:
            parsed = parse_date(date_to)
            if parsed is None:
                raise ValidationError({"date_to": "日期格式必须为 YYYY-MM-DD。"})
            qs = qs.filter(biz_date__lte=parsed)

        closed = request.query_params.get("is_closed")
        if closed is not None:
            if closed.lower() not in {"1", "0", "true", "false"}:
                raise ValidationError({"is_closed": "必须为 true/false 或 1/0。"})
            qs = qs.filter(is_closed=closed.lower() in {"1", "true"})
        overdue = request.query_params.get("overdue")
        if overdue is not None:
            if overdue.lower() not in {"1", "0", "true", "false"}:
                raise ValidationError({"overdue": "必须为 true/false 或 1/0。"})
            overdue_q = Q(etd__lt=timezone.now(), is_closed=False) & ~Q(
                approval_status="CANCELLED"
            )
            qs = qs.filter(overdue_q) if overdue.lower() in {"1", "true"} else qs.exclude(overdue_q)

        if task_no or task_status or waybill_no:
            task_qs = WmsTask.objects.filter(
                source_model__in=("outboundorder", "OutboundOrder"),
            )
            if task_no:
                task_qs = task_qs.filter(task_no__icontains=task_no)
            if task_status:
                task_qs = task_qs.filter(status=task_status)
            if waybill_no:
                task_qs = task_qs.filter(
                    lines__dispatchlineextra__waybill_no__icontains=waybill_no
                )
            task_order_ids = task_qs.annotate(
                _source_order_id=Cast(
                    "source_pk",
                    output_field=BigIntegerField(),
                )
            ).values("_source_order_id")
            qs = qs.filter(pk__in=task_order_ids)

        page = self.paginate_queryset(qs.distinct())
        ser = OutboundOrderReadSerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(ser.data)

    @staticmethod
    def _standard_order_idempotency_key(request):
        key = request.headers.get("Idempotency-Key") or ""
        if not key:
            raise ValidationError({"idempotency_key": "请提供 Idempotency-Key。"})
        if not STANDARD_ORDER_IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise ValidationError(
                {
                    "idempotency_key": (
                        "Idempotency-Key 仅允许字母、数字及 . _ : -，长度为 8-64 位。"
                    )
                }
            )
        return key

    @staticmethod
    def _standard_order_fingerprint(validated):
        def decimal_text(value, places):
            if value is None:
                return None
            return format(Decimal(value).quantize(Decimal(places)), "f")

        etd = validated.get("etd")
        canonical = {
            "owner_id": int(validated["owner_id__from_user"]),
            "warehouse_id": int(validated["warehouse_id__from_user"]),
            "customer_id": validated.get("customer_id"),
            "supplier_id": validated.get("supplier_id"),
            "outbound_type": validated.get("outbound_type", "SALES"),
            "delivery_method": validated.get("delivery_method") or None,
            "etd": etd.isoformat() if etd is not None else None,
            "remark": (validated.get("remark") or "").strip(),
            "src_bill_no": validated.get("src_bill_no") or None,
            "contact": (validated.get("contact") or "").strip(),
            "contact_phone": (validated.get("contact_phone") or "").strip(),
            "ship_to": (validated.get("ship_to") or "").strip(),
            "items": [
                {
                    "product_id": int(item["product_id"]),
                    "qty": decimal_text(item["qty"], "0.001"),
                    "price": decimal_text(item.get("price"), "0.0001"),
                }
                for item in validated["items"]
            ],
        }
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _duplicate_source_error(existing):
        return ValidationError(
            {
                "src_bill_no": f"平台单号重复，已存在订单 {existing.order_no}",
                "existing_order_id": str(existing.id),
                "existing_order_no": existing.order_no or "",
                "existing_approval_status": existing.approval_status or "",
                "existing_submit_status": existing.submit_status or "",
            }
        )

    @staticmethod
    def _standard_order_response(order, request, *, replayed, http_status):
        data = dict(
            OutboundOrderReadSerializer(order, context={"request": request}).data
        )
        data["idempotent"] = replayed
        data["replayed"] = replayed
        return Response(data, status=http_status)

    def _existing_idempotent_order(self, *, owner_id, user_id, key, fingerprint):
        existing = (
            OutboundOrder.all_objects.filter(
                owner_id=owner_id,
                created_by_id=user_id,
                idempotency_key=key,
            )
            .order_by("id")
            .first()
        )
        if existing and existing.idempotency_fingerprint != fingerprint:
            raise StandardOrderIdempotencyConflict()
        return existing

    @transaction.atomic
    def _persist_standard_order(self, *, serializer, request, key, fingerprint):
        owner_id = serializer.validated_data["owner_id__from_user"]
        src_bill_no = serializer.validated_data.get("src_bill_no")
        if src_bill_no:
            duplicate = (
                OutboundOrder.all_objects.filter(
                    owner_id=owner_id,
                    src_bill_no=src_bill_no,
                )
                .order_by("id")
                .first()
            )
            if duplicate:
                raise self._duplicate_source_error(duplicate)

        order = serializer.save(
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
        )
        record_audit_event(
            action="outbound.order.create",
            module="outbound",
            request=request,
            obj=order,
            after={"submit_status": order.submit_status, "approval_status": order.approval_status},
        )
        return order

    def create(self, request, *args, **kwargs):
        self._require_owner_buyer("outbound.orders.create")
        key = self._standard_order_idempotency_key(request)
        ser = OutboundOrderCreateSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        fingerprint = self._standard_order_fingerprint(ser.validated_data)
        owner_id = ser.validated_data["owner_id__from_user"]
        user_id = request.user.id

        existing = self._existing_idempotent_order(
            owner_id=owner_id,
            user_id=user_id,
            key=key,
            fingerprint=fingerprint,
        )
        if existing:
            return self._standard_order_response(
                existing,
                request,
                replayed=True,
                http_status=status.HTTP_200_OK,
            )

        try:
            order = self._persist_standard_order(
                serializer=ser,
                request=request,
                key=key,
                fingerprint=fingerprint,
            )
        except (IntegrityError, DjangoValidationError) as exc:
            existing = self._existing_idempotent_order(
                owner_id=owner_id,
                user_id=user_id,
                key=key,
                fingerprint=fingerprint,
            )
            if existing:
                return self._standard_order_response(
                    existing,
                    request,
                    replayed=True,
                    http_status=status.HTTP_200_OK,
                )
            src_bill_no = ser.validated_data.get("src_bill_no")
            duplicate = (
                OutboundOrder.all_objects.filter(
                    owner_id=owner_id,
                    src_bill_no=src_bill_no,
                )
                .order_by("id")
                .first()
                if src_bill_no
                else None
            )
            if duplicate:
                raise self._duplicate_source_error(duplicate)
            if isinstance(exc, DjangoValidationError):
                raise ValidationError(
                    exc.message_dict if hasattr(exc, "message_dict") else exc.messages
                ) from exc
            raise

        return self._standard_order_response(
            order,
            request,
            replayed=False,
            http_status=status.HTTP_201_CREATED,
        )

    # 提交：DRAFT -> SUBMITTED
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def submit(self, request, pk=None):
        order = self.get_object()
        self._require_owner_buyer("outbound.orders.submit")
        self._editable_order_error(order, request.user)
        before = {
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
        }
        try:
            order = outbound_services.submit_owner_draft(order, by_user=request.user)
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from exc
        record_audit_event(
            action="outbound.order.submit",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
            },
        )
        return Response(
            OutboundOrderReadSerializer(order, context={"request": request}).data
        )

    @action(detail=True, methods=["post"], url_path="owner-approve")
    @transaction.atomic
    def owner_approve(self, request, pk=None):
        order = self.get_object()
        self._require_owner_manager(order, "outbound.orders.owner_approve")

        before = {
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
        }
        try:
            order = outbound_services.approve_owner_order(
                order, by_user=request.user, allow_backorder=True
            )
        except DjangoValidationError as e:
            raise ValidationError(
                e.message_dict if hasattr(e, "message_dict") else e.messages
            ) from e
        record_audit_event(
            action="outbound.order.owner_approve",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={"approval_status": order.approval_status},
        )
        return Response(OutboundOrderReadSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="owner-reject")
    @transaction.atomic
    def owner_reject(self, request, pk=None):
        order = self.get_object()
        self._require_owner_manager(order, "outbound.orders.owner_reject")
        reason = str((request.data or {}).get("reason") or "").strip()
        before = {
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
        }
        try:
            order = outbound_services.reject_owner_order(
                order,
                by_user=request.user,
                reason=reason,
            )
        except DjangoValidationError as e:
            raise ValidationError(
                e.message_dict if hasattr(e, "message_dict") else e.messages
            ) from e

        record_audit_event(
            action="outbound.order.owner_reject",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
                "owner_reject_reason": order.owner_reject_reason,
            },
            metadata={"reason": reason},
        )
        return Response(
            OutboundOrderReadSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="warehouse-confirm")
    @transaction.atomic
    def warehouse_confirm(self, request, pk=None):
        """Confirm a fully allocated standard order and release its PICK task."""

        order = self.get_object()
        self._require_warehouse_manager(order, "outbound.orders.warehouse_confirm")
        before = {"approval_status": order.approval_status}
        try:
            order, task = outbound_services.confirm_warehouse_order(
                order,
                by_user=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from exc
        record_audit_event(
            action="outbound.order.warehouse_confirm",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={"approval_status": order.approval_status},
            metadata={"pick_task_id": task.pk},
        )
        return Response(
            OutboundOrderReadSerializer(order, context={"request": request}).data
        )

    @action(detail=True, methods=["post"], url_path="withdraw")
    @transaction.atomic
    def withdraw(self, request, pk=None):
        order = self.get_object()
        self._require_owner_buyer("outbound.orders.withdraw")
        if order.created_by_id != request.user.id:
            raise PermissionDenied("仅订单原创建业务员可以撤回该订单。")
        before = {
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
        }
        try:
            order = outbound_services.withdraw_order(
                order,
                by_user=request.user,
                reason=(request.data or {}).get("reason") or "货主业务员撤销提交",
            )
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from exc
        record_audit_event(
            action="outbound.order.withdraw",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
            },
        )
        return Response(
            OutboundOrderReadSerializer(order, context={"request": request}).data
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    @transaction.atomic
    def cancel(self, request, pk=None):
        order = self.get_object()
        self._require_owner_manager(order, "outbound.orders.cancel")
        before = {"approval_status": order.approval_status}
        try:
            order = outbound_services.cancel_order(
                order,
                by_user=request.user,
                reason=(request.data or {}).get("reason") or "货主管理员取消订单",
            )
        except DjangoValidationError as e:
            raise ValidationError(
                e.message_dict if hasattr(e, "message_dict") else e.messages
            ) from e
        record_audit_event(
            action="outbound.order.cancel",
            module="outbound",
            request=request,
            obj=order,
            before=before,
            after={"approval_status": order.approval_status},
        )
        return Response(
            OutboundOrderReadSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    def _excel_str(self, v):
        return "" if v is None else str(v).strip()

    def _excel_decimal(self, v):
        if v in (None, ""):
            raise ValueError("数量不能为空")
        try:
            d = Decimal(str(v).strip())
        except (InvalidOperation, ValueError):
            raise ValueError("数量格式不正确")
        if d <= 0:
            raise ValueError("数量必须大于 0")
        return d

    def _build_ship_to(self, row_dict):
        parts = [
            self._excel_str(row_dict.get("收件人省")),
            self._excel_str(row_dict.get("收件人市")),
            self._excel_str(row_dict.get("收件人区")),
            self._excel_str(row_dict.get("收件人详细地址")),
        ]
        return "".join([p for p in parts if p])

    def _build_remark(self, row_dict):
        parts = []

        remark = self._excel_str(row_dict.get("备注"))
        if remark:
            parts.append(f"备注:{remark}")

        express_no = self._excel_str(row_dict.get("物流单号"))
        if express_no:
            parts.append(f"物流单号:{express_no}")

        sale_attr = self._excel_str(row_dict.get("销售属性"))
        if sale_attr:
            parts.append(f"销售属性:{sale_attr}")

        goods_name = self._excel_str(row_dict.get("商品名称"))
        if goods_name:
            parts.append(f"商品名称:{goods_name}")

        sender_name = self._excel_str(row_dict.get("发货人姓名"))
        sender_phone = self._excel_str(row_dict.get("发货人手机/电话"))
        sender_addr = "".join([
            self._excel_str(row_dict.get("发货人省")),
            self._excel_str(row_dict.get("发货人市")),
            self._excel_str(row_dict.get("发货人区")),
            self._excel_str(row_dict.get("发货人详细地址")),
        ])
        if sender_name or sender_phone or sender_addr:
            parts.append(
                f"发货人:{sender_name} {sender_phone} {sender_addr}".strip()
            )

        return " | ".join(parts)

    def _get_default_price(self, product):
        """Compatibility wrapper for callers outside the importer."""
        return outbound_services.get_default_product_price(product)

    def _find_product_for_import(self, owner_id, row_dict):
        Product = apps.get_model("products", "Product")

        sku = self._excel_str(row_dict.get("商家编码"))
        goods_name = self._excel_str(row_dict.get("商品名称"))

        if sku:
            p = Product.objects.filter(owner_id=owner_id, sku=sku).order_by("id").first()
            if p:
                return p
            raise ValueError(f"商家编码[{sku}]匹配不到商品")

        if goods_name:
            qs = Product.objects.filter(owner_id=owner_id, name=goods_name).order_by("id")
            cnt = qs.count()
            if cnt == 1:
                return qs.first()
            if cnt > 1:
                raise ValueError(f"商品名称[{goods_name}]匹配到多个商品，请改填商家编码")
            raise ValueError(f"商品名称[{goods_name}]匹配不到商品")

        raise ValueError("商家编码和商品名称不能同时为空")

    @action(
        detail=False,
        methods=["post"],
        url_path="import-drop-ship-excel",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_drop_ship_excel(self, request):
        """
        上传一件代发 Excel，按行生成 OutboundOrder。
        每行 1 单、每单 1 条明细。
        """
        self._require_owner_buyer("outbound.orders.import_drop_ship_excel")
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response(
                {"code": "invalid_excel", "detail": "请上传 Excel 文件，字段名 file。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        scope = AccessScope.for_user(user)
        owner_id = scope.single_owner_id
        raw_warehouse_id = request.data.get("warehouse_id")
        try:
            warehouse_id = int(raw_warehouse_id) if raw_warehouse_id else None
        except (TypeError, ValueError):
            warehouse_id = None

        if not owner_id:
            return Response(
                {"detail": "当前用户没有单一有效货主角色范围"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not warehouse_id:
            return Response(
                {"warehouse_id": "请选择出库仓库。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        Customer = apps.get_model("baseinfo", "Customer")
        if not owner_can_use_warehouse(owner_id, warehouse_id):
            return Response(
                {"warehouse_id": "仓库不可用或未关联当前货主。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cash_customer = Customer.objects.filter(owner_id=owner_id, code="CASH").order_by("id").first()
        if not cash_customer:
            return Response(
                {"detail": f"当前货主[{owner_id}]下不存在 code=CASH 的散客客户，请先创建"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = import_drop_ship_workbook(
                uploaded_file=file_obj,
                request=request,
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                cash_customer=cash_customer,
            )
        except DropShipImportFileError as exc:
            return Response(
                {"code": "invalid_excel", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="import-drop-ship-template",
    )
    def import_drop_ship_template(self, request):
        template_path = Path(settings.BASE_DIR) / "allapp" / "outbound" / "resources" / "yi-jian-dai-fa-mo-ban.xlsx"

        if not template_path.exists():
            raise Http404("模板文件不存在，请联系管理员。")

        filename = "一件代发模板.xlsx"
        response = FileResponse(
            open(template_path, "rb"),
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return response

class PickTaskSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    is_warehouse_assisted = serializers.SerializerMethodField()
    can_self_review = serializers.SerializerMethodField()

    def get_is_warehouse_assisted(self, obj):
        return get_assisted_order_for_task(obj) is not None

    def get_can_self_review(self, obj):
        request = self.context.get("request")
        return bool(request and can_self_review_assisted_task(request.user, obj))

    class Meta:
        model = WmsTask
        fields = [
            "id",
            "task_no",
            "task_type",
            "status",
            "owner_id",
            "owner_name",
            "warehouse_id",
            "warehouse_name",
            "remark",
            "review_status",
            "posting_status",
            "picked_by_id",
            "is_warehouse_assisted",
            "can_self_review",
        ]

class PickTaskLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    from_loc_code = serializers.CharField(source="from_location.code", read_only=True)
    to_loc_code = serializers.CharField(source="to_location.code", read_only=True)

    class Meta:
        model = WmsTaskLine
        fields = [
            "id",
            "task_id",
            "product_id",
            "product_sku",
            "product_name",
            "from_location_id",
            "from_loc_code",
            "to_location_id",
            "to_loc_code",
            "qty_plan",
            "qty_done",
            "status",
        ]


class PickTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """
    PDA 拣货任务接口：
      - GET  /api/outbound/pda/pick-tasks/             列表
      - GET  /api/outbound/pda/pick-tasks/<id>/        任务头
      - GET  /api/outbound/pda/pick-tasks/<id>/lines/  行
      - POST /api/outbound/pda/pick-tasks/<id>/scan/   扫码拣货
      - POST /api/outbound/pda/pick-tasks/<id>/create-review-task/ complete拣货
      - POST /api/outbound/pda/pick-tasks/<id>/post/   完成并过账
    """
    permission_classes = [IsAuthenticated]
    serializer_class = PickTaskSerializer

    def _scope_pick_queryset(self, qs):
        user = self.request.user
        if not user or not user.is_authenticated:
            return qs.none()
        strict = strict_pick_queryset(qs, user)
        # The new assisted flow is never affected by the legacy compatibility
        # mode; otherwise preserve today's PDA scope while shadowing the new one.
        if is_assisted_operator(user):
            return strict
        access_scope = AccessScope.for_user(user)
        if access_scope.is_global:
            legacy = qs
        else:
            legacy = access_scope.filter_queryset(qs)
        warehouse_id = access_scope.single_warehouse_id
        if warehouse_id:
            assisted_task_ids = assisted_task_queryset(
                qs,
                warehouse_id=warehouse_id,
            ).values("pk")
            legacy = legacy.filter(
                ~Q(pk__in=assisted_task_ids) | Q(pk__in=strict.values("pk"))
            )
        return apply_legacy_scope(
            base_qs=legacy,
            scoped_qs=strict,
            user=user,
            endpoint=f"outbound.pick_tasks.{getattr(self, 'action', 'unknown')}",
        )

    def _require_task_action(self, endpoint, *, manager_allowed=False):
        require_legacy_action(
            user=self.request.user,
            allowed=(
                can_review_task_actions(self.request.user)
                if manager_allowed
                else can_use_task_actions(self.request.user)
            ),
            endpoint=endpoint,
            reason="需要仓库任务操作权限。",
        )

    def _get_pick_task_for_update(self, pk):
        qs = self._scope_pick_queryset(
            WmsTask.objects.select_for_update().filter(task_type=WmsTask.TaskType.PICK)
        )
        return get_object_or_404(qs, pk=pk)

    def get_queryset(self):
        qs = self._scope_pick_queryset(WmsTask.objects.filter(
            task_type=WmsTask.TaskType.PICK,
        )).order_by("-id")

        action = getattr(self, "action", None)

        for_review = self.request.query_params.get("for_review")
        if for_review in ("1", "true", "True"):
            qs = qs.filter(
                status=WmsTask.Status.COMPLETED,
                review_status=WmsTask.ReviewStatus.PENDING,
            ).exclude(picked_by=self.request.user)
            return qs

        # Normal lists retain their default in-progress status filter.  The
        # review queue above deliberately bypasses it.
        status_list = self.request.query_params.getlist("status")
        if status_list:
            qs = qs.filter(status__in=status_list)
        elif action == "list":
            qs = qs.filter(
                status__in=[
                    WmsTask.Status.RELEASED,
                    WmsTask.Status.IN_PROGRESS,
                    WmsTask.Status.RESERVED,
                ]
            )

        review_status_list = self.request.query_params.getlist("review_status")
        if review_status_list:
            qs = qs.filter(review_status__in=review_status_list)

        return qs

    @action(methods=["get"], detail=True)
    def lines(self, request, pk=None):
        task = self.get_object()
        ctx, ctx_text = build_log_payload(task_id=pk, user=request.user)
        logger.info("outbound.pick.lines.request %s", ctx_text, extra=ctx)
        lines = (
            WmsTaskLine.objects
            .filter(task_id=task.id)
            .select_related("product", "from_location", "to_location")
            .order_by("id")
        )
        data = PickTaskLineSerializer(lines, many=True).data
        logger.debug("outbound.pick.lines.response %s count=%s", ctx_text, len(data), extra=ctx)
        return Response(data)

    @action(methods=["post"], detail=True)
    def scan(self, request, pk=None):
        """
        请求体：
          { "barcode": "...", "qty": 1 }

        内部调用统一的 scan_task()，根据任务类型 = PICK 处理。
        """
        self._require_task_action("outbound.pick_tasks.scan")
        payload = request.data or {}
        barcode = (payload.get("barcode") or "").strip()
        qty = payload.get("qty") or 1
        location_id = payload.get("location_id") or None

        if not barcode:
            return Response({"detail": "缺少条码"}, status=400)

        task = self.get_object()
        res = task_services.scan_task(
            task_id=task.id,
            barcode=barcode,
            qty=qty,
            location_id=location_id,
            by_user=request.user,
            client_seq=payload.get("client_seq"),
        )

        # scan_task 返回里至少有 line_id / qty_done
        line_id = res.get("line_id")
        if line_id:
            line = (
                WmsTaskLine.objects
                .filter(id=line_id)
                .only("id", "qty_plan", "qty_done", "status")
                .first()
            )
            if line:
                res["line"] = {
                    "id": line.id,
                    "qty_plan": line.qty_plan,
                    "qty_done": line.qty_done,
                    "status": line.status,
                }

        return Response(res)

# === 新增：拣货完成 → 提交复核 =========================================
    @action(methods=["post"], detail=True, url_path="create-review-task")
    @transaction.atomic
    def create_review_task(self, request, pk=None):
        """Complete the PICK and idempotently create a real REVIEW task."""
        self._require_task_action("outbound.pick_tasks.submit_review")
        task = self._get_pick_task_for_update(pk)
        ctx, ctx_text = build_log_payload(task=task, user=request.user)
        logger.info("outbound.pick.create_review.begin %s", ctx_text, extra=ctx)
        try:
            review_task = outbound_services.create_review_task_for_pick(
                task,
                by_user=request.user,
            )
        except DjangoValidationError as exc:
            logger.warning("outbound.pick.create_review.rejected %s", ctx_text, extra=ctx)
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from exc
        task.refresh_from_db()
        record_audit_event(
            action="outbound.pick.submit_review",
            module="outbound",
            request=request,
            obj=task,
            before={"status": WmsTask.Status.RELEASED},
            after={"status": task.status, "review_status": task.review_status},
            metadata={"review_task_id": review_task.id},
        )
        logger.info(
            "outbound.pick.create_review.completed %s review_task_id=%s status=%s",
            ctx_text,
            review_task.id,
            task.status,
            extra=ctx,
        )
        return Response({
            "task_id": task.id,
            "review_task_id": review_task.id,
            "review_task_no": review_task.task_no,
            "review_task_status": review_task.status,
            "status": task.status,
            "review_status": task.review_status,
            "posting_status": task.posting_status,
            "message": "拣货已完成，已提交复核。",
        })

    # === 修改：复核通过 + 过账 ==============================================
    @action(methods=["post"], detail=True)
    def post(self, request, pk=None):
        """Approve the actual REVIEW, then journal-post its source PICK."""

        self._require_task_action(
            "outbound.pick_tasks.post",
            manager_allowed=True,
        )
        review_task = None
        with transaction.atomic():
            task = self._get_pick_task_for_update(pk)
            ctx, ctx_text = build_log_payload(task=task, user=request.user)
            logger.info("outbound.pick.post.begin %s", ctx_text, extra=ctx)

            if task.status != WmsTask.Status.COMPLETED:
                raise ValidationError("任务未处于已完成状态，不能过账。")

            journal, _ = PostingJournal.objects.get_or_create(
                src_model="WmsTask",
                src_id=task.id,
                tx_type="POST",
                defaults={"status": "PENDING", "message": "", "attempt_count": 0},
            )
            journal = PostingJournal.objects.select_for_update().get(pk=journal.pk)
            assisted_order = get_assisted_order_for_task(task, for_update=True)
            review_task = outbound_services.get_review_task_for_pick(
                task,
                for_update=True,
            )

            if journal.status == "POSTED":
                update_fields = []
                if task.review_status != WmsTask.ReviewStatus.APPROVED:
                    logger.error(
                        "outbound.pick.post.repair_review_status %s old_review_status=%s",
                        ctx_text,
                        task.review_status,
                        extra=ctx,
                    )
                    task.review_status = WmsTask.ReviewStatus.APPROVED
                    update_fields.append("review_status")
                if task.posting_status != WmsTask.PostingStatus.POSTED:
                    task.posting_status = WmsTask.PostingStatus.POSTED
                    update_fields.append("posting_status")
                if task.posted_at is None:
                    task.posted_at = timezone.now()
                    update_fields.append("posted_at")
                if update_fields:
                    task.save(update_fields=update_fields)
                if review_task is not None:
                    outbound_services.finalize_review_after_pick_post(
                        review_task,
                        by_user=request.user,
                    )
                if assisted_order is not None:
                    outbound_services.close_assisted_order_for_posted_task(task)
                return Response(
                    {
                        "task_id": task.id,
                        "posted": True,
                        "idempotent": True,
                        "affected_tx_count": 0,
                        "batch_no": journal.message or "",
                    }
                )

            picker = getattr(task, "picked_by", None)
            if picker and picker.id == request.user.id and not can_self_review_assisted_task(
                request.user, task
            ):
                logger.warning(
                    "outbound.pick.post.self_review_blocked %s", ctx_text, extra=ctx
                )
                raise ValidationError("拣货人不能作为本任务的复核人。")
            if review_task is None:
                raise ValidationError("缺少实际 REVIEW 任务，不能复核过账。")

            try:
                review_task = outbound_services.approve_review_task_for_pick(
                    task,
                    by_user=request.user,
                )
            except DjangoValidationError as exc:
                raise ValidationError(
                    exc.message_dict if hasattr(exc, "message_dict") else exc.messages
                ) from exc
            task.refresh_from_db()
            if not (
                task.review_status == WmsTask.ReviewStatus.APPROVED
                and task.posting_status
                in {WmsTask.PostingStatus.PENDING, WmsTask.PostingStatus.FAILED}
            ):
                raise ValidationError(
                    f"任务审核/过账状态组合不一致：{task.review_status}/{task.posting_status}。"
                )

        result = _run_posting_handler(
            task_id=int(pk),
            by_user=request.user,
            note="PDA拣货复核通过并过账",
        )
        with transaction.atomic():
            task = self._get_pick_task_for_update(pk)
            journal = PostingJournal.objects.select_for_update().get(
                src_model="WmsTask", src_id=task.id, tx_type="POST"
            )
            if (
                journal.status != "POSTED"
                or task.posting_status != WmsTask.PostingStatus.POSTED
            ):
                raise ValidationError("过账处理未将任务及日记账确认为 POSTED。")
            if review_task is None:
                raise ValidationError("复核任务不存在，不能完成出库链。")
            outbound_services.finalize_review_after_pick_post(
                review_task,
                by_user=request.user,
            )
            if get_assisted_order_for_task(task, for_update=True) is not None:
                outbound_services.close_assisted_order_for_posted_task(task)
        logger.info("outbound.pick.post.completed %s", ctx_text, extra=ctx)

        task.refresh_from_db()
        record_audit_event(
            action="outbound.review.approve_post",
            module="outbound",
            request=request,
            obj=task,
            after={
                "status": task.status,
                "review_status": task.review_status,
                "posting_status": task.posting_status,
            },
            metadata={"review_task_id": review_task.id if review_task else None},
        )

        return Response({
            "task_id": int(pk),
            "posted": True,
            "idempotent": bool((result or {}).get("tx_created") == 0),
            **(result or {}),
        })

    @action(methods=["post"], detail=True, url_path="adjust-line-qty")
    def adjust_line_qty(self, request, pk=None):
        """
        PDA 手工调整拣货行数量：
          - 请求体：{ line_id, final_qty_done, client_seq? }
          - 调用 tasking.services.adjust_pick_line_qty
        """
        self._require_task_action("outbound.pick_tasks.adjust_line_qty")
        ctx, ctx_text = build_log_payload(task_id=pk, user=request.user)
        logger.info("outbound.pick.adjust_line_qty.begin %s", ctx_text, extra=ctx)
        task = self.get_object()
        task_id = task.id
        line_id = request.data.get("line_id")
        final_qty_done = request.data.get("final_qty_done")
        client_seq = request.data.get("client_seq")

        if not line_id:
            return Response(
                {"detail": "缺少 line_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if final_qty_done is None:
            return Response(
                {"detail": "缺少 final_qty_done"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not WmsTaskLine.objects.filter(id=line_id, task_id=task.id).exists():
            return Response(
                {"detail": "line_id 不属于当前任务"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            res = adjust_pick_line_qty(
                task_id=task_id,
                line_id=int(line_id),
                final_qty=final_qty_done,
                by_user=request.user,
                client_seq=client_seq,
            )
        except ValidationError as e:
            # 和其它接口一样，抛 Django/DRF 的 ValidationError
            raise e

        return Response(res)

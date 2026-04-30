from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from .models import InventorySummary
from .serializers import OwnerInventorySummarySerializer

from django.db.models import Q, Sum
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import InventoryDetail
from .serializers import (
    CompanyInventoryWarehouseSummarySerializer,
    CompanyInventoryAllSummarySerializer,
)


class DefaultPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class OwnerInventorySummaryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    货主端实时库存（MVP 第一层）
    只提供 owner + product 粒度的汇总库存，不做 detail。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = OwnerInventorySummarySerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        user = self.request.user
        owner_id = getattr(user, "owner_id", None)

        # 未绑定货主，直接返回空
        if not owner_id:
            return InventorySummary.objects.none()

        qs = (
            InventorySummary.objects
            .filter(owner_id=owner_id, is_active=True)
            .select_related("product")
            .order_by("product_id")
        )

        q = (self.request.query_params.get("search") or "").strip()
        if q:
            qs = qs.filter(
                Q(product__name__icontains=q)
                | Q(product__code__icontains=q)
                | Q(product__sku__icontains=q)
                | Q(product__gtin__icontains=q)
            )

        return qs



# class DefaultPagination(PageNumberPagination):
#     page_size = 10
#     page_size_query_param = "page_size"
#     max_page_size = 100


class CompanyInventorySummaryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    公司级库存汇总：
    - mode=warehouse: 分仓库 + 分货主 + 分商品
    - mode=all: 所有仓库合并，只分货主 + 分商品
    """
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination

    def _check_company_level_permission(self, user):
        """
        这里是占位逻辑，请按你真实的用户体系改。
        例如：
        - user.is_superuser
        - user.groups.filter(name='warehouse_boss').exists()
        - user.role == 'COMPANY_ADMIN'
        """
        if user.is_superuser:
            return

        # 仓库操作员 / 仓库管理员：
        # 只要用户绑定了 warehouse，认为是仓库端用户，允许查看公司级库存汇总
        if getattr(user, "warehouse_id", None):
            return

        # 货主端用户通常只有 owner，没有 warehouse，不允许查看
        raise PermissionDenied("无权查看公司级库存汇总")

    def get_serializer_class(self):
        mode = (self.request.query_params.get("mode") or "warehouse").strip().lower()
        if mode == "all":
            return CompanyInventoryAllSummarySerializer
        return CompanyInventoryWarehouseSummarySerializer

    def get_queryset(self):
        user = self.request.user
        self._check_company_level_permission(user)

        mode = (self.request.query_params.get("mode") or "warehouse").strip().lower()
        if mode not in {"warehouse", "all"}:
            raise ValidationError({"mode": "mode 只能是 warehouse 或 all"})

        warehouse_id = (self.request.query_params.get("warehouse_id") or "").strip()
        owner_id = (self.request.query_params.get("owner_id") or "").strip()
        search = (self.request.query_params.get("search") or "").strip()

        qs = InventoryDetail.objects.select_related(
            "warehouse", "owner", "product"
        )

        # 可选过滤：按仓库
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)

        # 可选过滤：按货主
        if owner_id:
            qs = qs.filter(owner_id=owner_id)

        # 可选搜索：商品名 / 编码 / SKU / GTIN；也可顺手支持货主名、仓库名
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__code__icontains=search)
                | Q(product__sku__icontains=search)
                | Q(product__gtin__icontains=search)
                | Q(owner__name__icontains=search)
                | Q(warehouse__name__icontains=search)
            )

        if mode == "warehouse":
            qs = (
                qs.values(
                    "warehouse_id",
                    "warehouse__name",
                    "owner_id",
                    "owner__name",
                    "product_id",
                    "product__code",
                    "product__name",
                    "product__spec",
                    "product__sku",
                    "base_unit",
                )
                .annotate(
                    onhand_qty=Sum("onhand_qty"),
                    available_qty=Sum("available_qty"),
                    allocated_qty=Sum("allocated_qty"),
                    locked_qty=Sum("locked_qty"),
                    damaged_qty=Sum("damaged_qty"),
                )
                .order_by("warehouse__name", "owner__name", "product__name", "product_id")
            )
        else:
            qs = (
                qs.values(
                    "owner_id",
                    "owner__name",
                    "product_id",
                    "product__code",
                    "product__name",
                    "product__spec",
                    "product__sku",
                    "base_unit",
                )
                .annotate(
                    onhand_qty=Sum("onhand_qty"),
                    available_qty=Sum("available_qty"),
                    allocated_qty=Sum("allocated_qty"),
                    locked_qty=Sum("locked_qty"),
                    damaged_qty=Sum("damaged_qty"),
                )
                .order_by("owner__name", "product__name", "product_id")
            )

        return qs

    def list(self, request, *args, **kwargs):
        """
        把 values()/annotate() 的别名字段整理成前端更好用的名字。
        """
        queryset = self.filter_queryset(self.get_queryset())

        mode = (request.query_params.get("mode") or "warehouse").strip().lower()

        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset

        data = []
        if mode == "warehouse":
            for row in rows:
                data.append({
                    "warehouse_id": row["warehouse_id"],
                    "warehouse_name": row.get("warehouse__name") or "",
                    "owner_id": row["owner_id"],
                    "owner_name": row.get("owner__name") or "",
                    "product_id": row["product_id"],
                    "product_code": row.get("product__code") or "",
                    "product_name": row.get("product__name") or "",
                    "product_spec": row.get("product__spec") or "",
                    "product_sku": row.get("product__sku") or "",
                    "base_unit": row.get("base_unit") or "",
                    "onhand_qty": row["onhand_qty"],
                    "available_qty": row["available_qty"],
                    "allocated_qty": row["allocated_qty"],
                    "locked_qty": row["locked_qty"],
                    "damaged_qty": row["damaged_qty"],
                })
        else:
            for row in rows:
                data.append({
                    "owner_id": row["owner_id"],
                    "owner_name": row.get("owner__name") or "",
                    "product_id": row["product_id"],
                    "product_code": row.get("product__code") or "",
                    "product_name": row.get("product__name") or "",
                    "product_spec": row.get("product__spec") or "",
                    "product_sku": row.get("product__sku") or "",
                    "base_unit": row.get("base_unit") or "",
                    "onhand_qty": row["onhand_qty"],
                    "available_qty": row["available_qty"],
                    "allocated_qty": row["allocated_qty"],
                    "locked_qty": row["locked_qty"],
                    "damaged_qty": row["damaged_qty"],
                })

        serializer = self.get_serializer(data, many=True)

        if page is not None:
            return self.get_paginated_response(serializer.data)
        from rest_framework.response import Response
        return Response(serializer.data)
from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from .models import InventorySummary
from .serializers import OwnerInventorySummarySerializer

from django.db.models import Sum
from rest_framework.exceptions import PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event

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
        scope = AccessScope.for_user(user)
        qs = scope.filter_queryset(
            InventorySummary.objects.filter(is_active=True),
            owner_field="owner_id",
            warehouse_field=None,
        )
        if scope.is_global:
            owner_id = (self.request.query_params.get("owner_id") or "").strip()
            if not owner_id:
                return InventorySummary.objects.none()
            qs = qs.filter(owner_id=owner_id)

        qs = (
            qs
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

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        record_audit_event(
            action="QUERY",
            module="inventory.owner_summary",
            request=request,
            owner_id=next(iter(AccessScope.for_user(request.user).owner_ids), None),
            metadata={"search": request.query_params.get("search", "")},
        )
        return response



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
        if user.is_superuser:
            return AccessScope.for_user(user)
        scope = AccessScope.for_user(user)
        allowed = any(
            user.has_perm(code)
            for code in (
                "accounts.access_warehouse_operations",
                "accounts.access_warehouse_management",
                "reports.view_warehouse_operations",
                "reports.view_boss_dashboard",
            )
        )
        if not scope.is_valid or not scope.warehouse_ids or not allowed:
            raise PermissionDenied("无权查看仓库库存汇总")
        return scope

    def get_serializer_class(self):
        mode = (self.request.query_params.get("mode") or "warehouse").strip().lower()
        if mode == "all":
            return CompanyInventoryAllSummarySerializer
        return CompanyInventoryWarehouseSummarySerializer

    def get_queryset(self):
        user = self.request.user
        scope = self._check_company_level_permission(user)

        mode = (self.request.query_params.get("mode") or "warehouse").strip().lower()
        if mode not in {"warehouse", "all"}:
            raise ValidationError({"mode": "mode 只能是 warehouse 或 all"})

        warehouse_id = (self.request.query_params.get("warehouse_id") or "").strip()
        owner_id = (self.request.query_params.get("owner_id") or "").strip()
        search = (self.request.query_params.get("search") or "").strip()

        qs = scope.filter_queryset(
            InventoryDetail.objects.select_related("warehouse", "owner", "product"),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )

        # 可选过滤：按仓库
        if warehouse_id:
            if not scope.allows(warehouse_id=warehouse_id):
                raise PermissionDenied("无权查看所请求的仓库")
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
            response = self.get_paginated_response(serializer.data)
        else:
            from rest_framework.response import Response
            response = Response(serializer.data)
        record_audit_event(
            action="QUERY",
            module="inventory.company_summary",
            request=request,
            owner_id=int(request.query_params["owner_id"])
            if (request.query_params.get("owner_id") or "").isdigit()
            else None,
            warehouse_id=int(request.query_params["warehouse_id"])
            if (request.query_params.get("warehouse_id") or "").isdigit()
            else None,
            metadata={"mode": mode, "search": request.query_params.get("search", "")},
        )
        return response

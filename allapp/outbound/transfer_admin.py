"""Scoped, read-only transfer evidence; source owners never see the retail sale."""

from decimal import ROUND_HALF_UP, Decimal

from django.contrib import admin
from django.db.models import Q

from allapp.accounts.access import AccessScope

from .transfer_models import (
    AssistedOwnershipTransfer,
    AssistedOwnershipTransferLine,
    OwnerProductMapping,
)


class TransferLineInline(admin.TabularInline):
    model = AssistedOwnershipTransferLine
    fields = readonly_fields = (
        "source_product",
        "target_product",
        "quantity",
        "transfer_price",
        "transfer_amount",
        "source_reservations",
    )
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("outbound.view_assistedownershiptransfer") and (
            obj is None
            or AssistedOwnershipTransferAdmin(AssistedOwnershipTransfer, self.admin_site)
            .get_queryset(request)
            .filter(pk=obj.pk)
            .exists()
        )

    @admin.display(description="接收商品")
    def target_product(self, obj):
        return obj.sales_line.product

    @admin.display(description="转货金额")
    def transfer_amount(self, obj):
        return (obj.quantity * obj.transfer_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @admin.display(description="来源库存与拣货记录")
    def source_reservations(self, obj):
        return "；".join(
            f"库存 {row.source_inventory_id} / 任务行 {row.task_line_id} / "
            f"批次 {row.source_snapshot.get('batch_no') or '-'} / "
            f"序列号 {row.source_snapshot.get('serial_no') or '-'} / "
            f"{row.quantity} / {row.get_status_display()}"
            for row in obj.reservations.all()
        )


@admin.register(AssistedOwnershipTransfer)
class AssistedOwnershipTransferAdmin(admin.ModelAdmin):
    fields = readonly_fields = (
        "transfer_no",
        "source_owner",
        "target_owner",
        "warehouse",
        "status",
        "created_by",
        "created_at",
        "posted_at",
        "posting_batch",
    )
    list_display = (
        "transfer_no",
        "source_owner",
        "target_owner",
        "warehouse",
        "status",
        "posted_at",
    )
    search_fields = ("transfer_no",)
    inlines = (TransferLineInline,)

    def get_fields(self, request, obj=None):
        scope = AccessScope.for_user(request.user)
        if obj and (
            scope.is_global
            or obj.target_owner_id in scope.owner_ids
            or obj.warehouse_id in scope.warehouse_ids
        ):
            return (*self.fields, "sales_order")
        return self.fields

    def get_readonly_fields(self, request, obj=None):
        return (*self.readonly_fields, "sales_order")

    def get_queryset(self, request):
        qs = (
            super()
            .get_queryset(request)
            .select_related("source_owner", "target_owner", "warehouse")
        )
        scope = AccessScope.for_user(request.user)
        if not scope.is_valid:
            return qs.none()
        if scope.is_global:
            return qs
        if scope.owner_ids:
            return qs.filter(
                Q(source_owner_id__in=scope.owner_ids) | Q(target_owner_id__in=scope.owner_ids)
            )
        return qs.filter(warehouse_id__in=scope.warehouse_ids)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and (
            obj is None or self.get_queryset(request).filter(pk=obj.pk).exists()
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OwnerProductMapping)
class OwnerProductMappingAdmin(admin.ModelAdmin):
    fields = ("source_product", "target_owner", "target_product", "is_active")
    list_display = fields

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if AccessScope.for_user(request.user).is_global else qs.none()

    def has_module_permission(self, request):
        return (
            super().has_module_permission(request) and AccessScope.for_user(request.user).is_global
        )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and AccessScope.for_user(request.user).is_global

    def has_change_permission(self, request, obj=None):
        return (
            super().has_change_permission(request, obj)
            and AccessScope.for_user(request.user).is_global
        )

    def has_view_permission(self, request, obj=None):
        return (
            super().has_view_permission(request, obj)
            and AccessScope.for_user(request.user).is_global
        )

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin
from .models import (
    DateDim,
    OwnerDim,
    WarehouseDim,
    ProductDim,
    CustomerDim,
    SupplierDim,
    CarrierDim,
    ReasonDim,
    TempZoneDim,
    FactInventorySnapshotDaily,
    FactInventoryTxn,
    FactInboundLine,
    FactOutboundLine,
    FactBilling,
    AggThroughputDaily,
    AggOTIFDaily,
    AggInventoryAging,
    AggBillingDaily,
    EtlWatermark,
    EtlJobRun,
    DedupLedger,
    ReportSnapshot,
)
@admin.register(FactInventorySnapshotDaily)
class FactInventorySnapshotDailyAdmin(admin.ModelAdmin):
    list_display = (
        "snapshot_date",
        "owner",
        "warehouse",
        "location_id",
        "product",
        "lot_no",
        "qty_onhand",
        "qty_available",
    )
    search_fields = ("lot_no",)
    list_filter = ("owner", "warehouse")
    ordering = ("-snapshot_date",)




@admin.register(AggThroughputDaily)
class AggThroughputDailyAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "owner",
        "warehouse",
        "inbound_lines",
        "inbound_qty",
        "outbound_lines",
        "outbound_qty",
    )
    list_filter = ("owner", "warehouse")
    ordering = ("-date",)



@admin.register(AggInventoryAging)
class AggInventoryAgingAdmin(admin.ModelAdmin):
    list_display = ("date", "owner", "warehouse", "product", "band", "qty", "amount")
    list_filter = ("owner", "warehouse", "band")
    ordering = ("-date",)

@admin.register(AggBillingDaily)
class AggBillingDailyAdmin(admin.ModelAdmin):
    list_display = ("date", "owner", "warehouse", "fee_type", "amount")
    list_filter = ("owner", "warehouse", "fee_type")
    ordering = ("-date",)

@admin.register(ReportSnapshot)
class ReportSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "doc_type", "doc_no", "owner", "warehouse", "is_final", "created_at")
    list_filter = ("doc_type", "owner", "warehouse", "is_final")
    search_fields = ("doc_no", "src_model", "src_id")
    readonly_fields = ("created_at",)
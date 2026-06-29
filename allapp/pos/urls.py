from django.urls import path

from .views import (
    PosAccuracyApi,
    PosCheckoutApi,
    PosProductListApi,
    PosReturnDetailApi,
    PosReturnListCreateApi,
    PosSaleDetailApi,
    PosSaleExportApi,
    PosSaleListApi,
    PosSalePrintApi,
    PosSalePrintLogApi,
    PosSaleReceiptApi,
    PosSaleVoidApi,
    PosShiftCloseApi,
    PosShiftCurrentApi,
    PosShiftDetailApi,
    PosShiftExportApi,
    PosShiftListApi,
    PosShiftListExportApi,
    PosShiftOpenApi,
    PosShiftPrintApi,
    PosShiftReopenApi,
    PosStatsApi,
    PosStatsExportApi,
)

urlpatterns = [
    path("products/", PosProductListApi.as_view(), name="pos-products"),
    path("checkout/", PosCheckoutApi.as_view(), name="pos-checkout"),
    path("accuracy/", PosAccuracyApi.as_view(), name="pos-accuracy"),
    path("stats/", PosStatsApi.as_view(), name="pos-stats"),
    path("stats/export/", PosStatsExportApi.as_view(), name="pos-stats-export"),
    path("sales/", PosSaleListApi.as_view(), name="pos-sale-list"),
    path("sales/export/", PosSaleExportApi.as_view(), name="pos-sale-export"),
    path("sales/<int:sale_id>/", PosSaleDetailApi.as_view(), name="pos-sale-detail"),
    path(
        "sales/<int:sale_id>/receipt/",
        PosSaleReceiptApi.as_view(),
        name="pos-sale-receipt",
    ),
    path(
        "sales/<int:sale_id>/print/",
        PosSalePrintApi.as_view(),
        name="pos-sale-print",
    ),
    path(
        "sales/<int:sale_id>/print-log/",
        PosSalePrintLogApi.as_view(),
        name="pos-sale-print-log",
    ),
    path("sales/<int:sale_id>/void/", PosSaleVoidApi.as_view(), name="pos-sale-void"),
    path("returns/", PosReturnListCreateApi.as_view(), name="pos-return-list-create"),
    path(
        "returns/<int:return_id>/",
        PosReturnDetailApi.as_view(),
        name="pos-return-detail",
    ),
    path("shifts/", PosShiftListApi.as_view(), name="pos-shift-list"),
    path(
        "shifts/export/",
        PosShiftListExportApi.as_view(),
        name="pos-shift-list-export",
    ),
    path("shifts/current/", PosShiftCurrentApi.as_view(), name="pos-shift-current"),
    path("shifts/open/", PosShiftOpenApi.as_view(), name="pos-shift-open"),
    path(
        "shifts/<int:shift_id>/", PosShiftDetailApi.as_view(), name="pos-shift-detail"
    ),
    path(
        "shifts/<int:shift_id>/close/",
        PosShiftCloseApi.as_view(),
        name="pos-shift-close",
    ),
    path(
        "shifts/<int:shift_id>/print/",
        PosShiftPrintApi.as_view(),
        name="pos-shift-print",
    ),
    path(
        "shifts/<int:shift_id>/reopen/",
        PosShiftReopenApi.as_view(),
        name="pos-shift-reopen",
    ),
    path(
        "shifts/<int:shift_id>/export/",
        PosShiftExportApi.as_view(),
        name="pos-shift-export",
    ),
]

# allapp/inbound/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .excel_views import (
    NoOrderReceiveImportConfirmApi,
    NoOrderReceiveImportPreviewApi,
    NoOrderReceiveImportTemplateApi,
)
from .export_print import ReceiveTaskPrintView
from .export_views import export_receive_task_excel
from .gs1_views import Gs1LookupApi, Gs1OptionsApi, Gs1QuickCreateApi
from .views import InboundOrderViewSet, InboundTaskViewSet, ReceiveGoodsWithoutOrder

router = DefaultRouter()
router.register(r"orders", InboundOrderViewSet, basename="inbound-order")
router.register(r"pda/tasks", InboundTaskViewSet, basename="inbound-pda-task")

urlpatterns = [
    path("", include(router.urls)),
    path("gs1-products/lookup/", Gs1LookupApi.as_view(), name="gs1-product-lookup"),
    path("gs1-products/options/", Gs1OptionsApi.as_view(), name="gs1-product-options"),
    path(
        "gs1-products/quick-create/",
        Gs1QuickCreateApi.as_view(),
        name="gs1-product-quick-create",
    ),
    path(
        "receive_without_order/",
        ReceiveGoodsWithoutOrder.as_view(),
        name="receive-without-order",
    ),
    path(
        "receive_without_order/import_template/",
        NoOrderReceiveImportTemplateApi.as_view(),
        name="receive-without-order-import-template",
    ),
    path(
        "receive_without_order/import_preview/",
        NoOrderReceiveImportPreviewApi.as_view(),
        name="receive-without-order-import-preview",
    ),
    path(
        "receive_without_order/import_confirm/",
        NoOrderReceiveImportConfirmApi.as_view(),
        name="receive-without-order-import-confirm",
    ),
    path(
        "receive_task/<int:task_id>/export_excel/",
        export_receive_task_excel,
        name="receive_task_export_excel",
    ),
    path(
        "receive_task/<int:task_id>/print/",
        ReceiveTaskPrintView.as_view(),
        name="receive-task-print",
    ),
]

from django.urls import path

from .views_boss import BossAlertApi, BossHomeApi, BossInventoryApi
from .views_pda import PdaThroughputApi, PdaThroughputDetailApi
from .views_operations import (
    OperationsDetailsApi,
    OperationsExportApi,
    OperationsSummaryApi,
)

urlpatterns = [
    # The documented v2 contract is slashless. Keep slash aliases for existing
    # mini-program clients while they migrate; POST exports must not depend on
    # CommonMiddleware's unsafe body-dropping redirect.
    path("v2/operations/summary", OperationsSummaryApi.as_view()),
    path("v2/operations/details", OperationsDetailsApi.as_view()),
    path("v2/operations/exports", OperationsExportApi.as_view()),
    path("v2/operations/summary/", OperationsSummaryApi.as_view(), name="reports-v2-operations-summary"),
    path("v2/operations/details/", OperationsDetailsApi.as_view(), name="reports-v2-operations-details"),
    path("v2/operations/exports/", OperationsExportApi.as_view(), name="reports-v2-operations-exports"),
    path("boss/home/", BossHomeApi.as_view(), name="reports-boss-home"),
    path("boss/inventory/", BossInventoryApi.as_view(), name="reports-boss-inventory"),
    path("boss/alerts/", BossAlertApi.as_view(), name="reports-boss-alerts"),
    path("pda/throughput/", PdaThroughputApi.as_view(), name="reports-pda-throughput"),
    path(
        "pda/throughput/details/",
        PdaThroughputDetailApi.as_view(),
        name="reports-pda-throughput-details",
    ),
]

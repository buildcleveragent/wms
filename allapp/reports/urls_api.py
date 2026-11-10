from django.urls import path

from .views_boss import (
    BossAlertApi,
    BossAlertDetailApi,
    BossAlertSectionApi,
    BossContextApi,
    BossHomeApi,
    BossInventoryApi,
    BossInventoryDetailListApi,
)
from .views_boss_activity import (
    BossActivitiesApi,
    BossActivityDetailApi,
    BossTaskDetailApi,
    BossTasksApi,
)
from .views_boss_automation import BossAutomationSettingsApi
from .views_boss_focus import (
    BossAttentionApi,
    BossDigestApi,
    BossDigestDetailsApi,
    BossPeriodDetailsApi,
    BossPeriodsApi,
    BossPeriodSummaryApi,
    BossSlaDetailsApi,
    BossTodayApi,
    BossTodayDetailsApi,
)
from .views_boss_p1 import (
    AlertCaseActionApi,
    BossAlertCaseDetailApi,
    BossAlertCasesApi,
    BossCockpitExportApi,
    BossCollectionHistoryApi,
    BossInventoryRiskApi,
    BossInventoryRiskDetailsApi,
    BossOperationsApi,
    BossOperationsDetailsApi,
    BossPerformanceApi,
    BossReceivableBillsApi,
    BossReceivablesApi,
    BossResourceYieldApi,
    BossRevenueAssuranceApi,
    BossRevenueAssuranceSectionApi,
    BossReviewSnapshotCreateApi,
    BossReviewSnapshotDetailApi,
    BossReviewSnapshotExportApi,
    BossReviewSnapshotRevokeApi,
    OperatingTargetViewSet,
)
from .views_operations import (
    OperationsDetailsApi,
    OperationsExportApi,
    OperationsSummaryApi,
)
from .views_pda import PdaThroughputApi, PdaThroughputDetailApi

urlpatterns = [
    path("boss/automation-settings/", BossAutomationSettingsApi.as_view()),
    path("boss/today/", BossTodayApi.as_view()),
    path("boss/today/details/", BossTodayDetailsApi.as_view()),
    path("boss/sla/details/", BossSlaDetailsApi.as_view()),
    path("boss/attention/", BossAttentionApi.as_view()),
    path("boss/digest/", BossDigestApi.as_view()),
    path("boss/digest/details/", BossDigestDetailsApi.as_view()),
    path("boss/periods/", BossPeriodsApi.as_view()),
    path("boss/periods/summary/", BossPeriodSummaryApi.as_view()),
    path("boss/periods/details/", BossPeriodDetailsApi.as_view()),
    path("boss/activities/", BossActivitiesApi.as_view()),
    path("boss/activities/<str:kind>/<int:pk>/", BossActivityDetailApi.as_view()),
    path("boss/tasks/", BossTasksApi.as_view()),
    path("boss/tasks/<int:pk>/", BossTaskDetailApi.as_view()),
    # The documented v2 contract is slashless. Keep slash aliases for existing
    # mini-program clients while they migrate; POST exports must not depend on
    # CommonMiddleware's unsafe body-dropping redirect.
    path("v2/operations/summary", OperationsSummaryApi.as_view()),
    path("v2/operations/details", OperationsDetailsApi.as_view()),
    path("v2/operations/exports", OperationsExportApi.as_view()),
    path(
        "v2/operations/summary/",
        OperationsSummaryApi.as_view(),
        name="reports-v2-operations-summary",
    ),
    path(
        "v2/operations/details/",
        OperationsDetailsApi.as_view(),
        name="reports-v2-operations-details",
    ),
    path(
        "v2/operations/exports/",
        OperationsExportApi.as_view(),
        name="reports-v2-operations-exports",
    ),
    path("boss/home/", BossHomeApi.as_view(), name="reports-boss-home"),
    path("boss/context/", BossContextApi.as_view(), name="reports-boss-context"),
    path("boss/inventory/", BossInventoryApi.as_view(), name="reports-boss-inventory"),
    path(
        "boss/inventory/details/",
        BossInventoryDetailListApi.as_view(),
        name="reports-boss-inventory-details",
    ),
    path("boss/alerts/", BossAlertApi.as_view(), name="reports-boss-alerts"),
    path("boss/revenue-assurance/", BossRevenueAssuranceApi.as_view()),
    path(
        "boss/revenue-assurance/sections/<str:section>/",
        BossRevenueAssuranceSectionApi.as_view(),
    ),
    path("boss/receivables/", BossReceivablesApi.as_view()),
    path("boss/receivables/bills/", BossReceivableBillsApi.as_view()),
    path(
        "boss/receivables/bills/<int:pk>/collection-history/",
        BossCollectionHistoryApi.as_view(),
    ),
    path("boss/operations/", BossOperationsApi.as_view()),
    path("boss/operations/details/", BossOperationsDetailsApi.as_view()),
    path("boss/operations/export/", OperationsExportApi.as_view()),
    path("boss/exports/", BossCockpitExportApi.as_view()),
    path("boss/resource-yield/", BossResourceYieldApi.as_view()),
    path("boss/performance/", BossPerformanceApi.as_view()),
    path("boss/inventory-risk/", BossInventoryRiskApi.as_view()),
    path("boss/inventory-risk/details/", BossInventoryRiskDetailsApi.as_view()),
    path("boss/alert-cases/", BossAlertCasesApi.as_view()),
    path("boss/alert-cases/<int:pk>/", BossAlertCaseDetailApi.as_view()),
    path("boss/review-snapshots/", BossReviewSnapshotCreateApi.as_view()),
    path("boss/review-snapshots/<str:share_code>/", BossReviewSnapshotDetailApi.as_view()),
    path(
        "boss/review-snapshots/<str:share_code>/export/",
        BossReviewSnapshotExportApi.as_view(),
    ),
    path("boss/review-snapshots/<int:pk>/revoke/", BossReviewSnapshotRevokeApi.as_view()),
    path("alert-cases/<int:pk>/<str:action>/", AlertCaseActionApi.as_view()),
    path(
        "operating-targets/",
        OperatingTargetViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "operating-targets/<int:pk>/",
        OperatingTargetViewSet.as_view(
            {
                "get": "retrieve",
                "patch": "partial_update",
                "put": "update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "boss/alerts/sections/<str:section>/",
        BossAlertSectionApi.as_view(),
        name="reports-boss-alert-section",
    ),
    path(
        "boss/alerts/sections/<str:section>/<str:item_type>/<int:pk>/",
        BossAlertDetailApi.as_view(),
        name="reports-boss-alert-detail",
    ),
    path("pda/throughput/", PdaThroughputApi.as_view(), name="reports-pda-throughput"),
    path(
        "pda/throughput/details/",
        PdaThroughputDetailApi.as_view(),
        name="reports-pda-throughput-details",
    ),
]

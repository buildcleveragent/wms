from django.urls import path
from rest_framework.routers import DefaultRouter

from .owner_alert_views import (
    OwnerAlertItemsView,
    OwnerAlertProductSettingsView,
    OwnerAlertProductsView,
    OwnerAlertSettingsView,
    OwnerAlertsView,
)
from .owner_history_views import OwnerHistoryView
from .owner_statistics_views import OwnerStatisticsView
from .views import (
    CompanyInventorySummaryViewSet,
    OwnerInventorySummaryViewSet,
)

router = DefaultRouter()
router.register(r"inventory/summary", OwnerInventorySummaryViewSet, basename="inventory-summary")
router.register(
    r"inventory/company-summary",
    CompanyInventorySummaryViewSet,
    basename="inventory-company-summary",
)

urlpatterns = [
    *[
        path(
            f"inventory/owner-history/statistics/{section}/",
            OwnerStatisticsView.as_view(section=section),
        )
        for section in ("summary", "products", "documents", "events", "periods", "chart", "export")
    ],
    path(
        "inventory/owner-history/statistics/events/<str:event_id>/",
        OwnerStatisticsView.as_view(section="detail"),
    ),
    *[
        path(f"inventory/owner-history/{section}/", OwnerHistoryView.as_view(section=section))
        for section in ("summary", "products", "documents", "events", "periods", "export")
    ],
    path(
        "inventory/owner-history/events/<int:event_id>/", OwnerHistoryView.as_view(section="detail")
    ),
    path("inventory/owner-alerts/", OwnerAlertsView.as_view()),
    path("inventory/owner-alerts/items/", OwnerAlertItemsView.as_view()),
    path("inventory/owner-alerts/settings/", OwnerAlertSettingsView.as_view()),
    path("inventory/owner-alerts/products/", OwnerAlertProductsView.as_view()),
    path(
        "inventory/owner-alerts/products/<int:product_id>/settings/",
        OwnerAlertProductSettingsView.as_view(),
    ),
] + router.urls

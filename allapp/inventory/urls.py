from django.urls import path
from rest_framework.routers import DefaultRouter

from .owner_alert_views import (
    OwnerAlertItemsView,
    OwnerAlertProductSettingsView,
    OwnerAlertProductsView,
    OwnerAlertSettingsView,
    OwnerAlertsView,
)
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
    path("inventory/owner-alerts/", OwnerAlertsView.as_view()),
    path("inventory/owner-alerts/items/", OwnerAlertItemsView.as_view()),
    path("inventory/owner-alerts/settings/", OwnerAlertSettingsView.as_view()),
    path("inventory/owner-alerts/products/", OwnerAlertProductsView.as_view()),
    path(
        "inventory/owner-alerts/products/<int:product_id>/settings/",
        OwnerAlertProductSettingsView.as_view(),
    ),
] + router.urls

from rest_framework.routers import DefaultRouter

from .views import (
    OwnerInventorySummaryViewSet,
    CompanyInventorySummaryViewSet,
)

router = DefaultRouter()
router.register(r"inventory/summary", OwnerInventorySummaryViewSet, basename="inventory-summary")
router.register(r"inventory/company-summary", CompanyInventorySummaryViewSet, basename="inventory-company-summary")

urlpatterns = router.urls

urlpatterns = router.urls
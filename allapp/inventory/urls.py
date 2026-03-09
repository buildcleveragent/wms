from rest_framework.routers import DefaultRouter

from .views import OwnerInventorySummaryViewSet

router = DefaultRouter()
router.register(r"inventory/summary", OwnerInventorySummaryViewSet, basename="inventory-summary")

urlpatterns = router.urls
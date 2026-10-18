from rest_framework.routers import DefaultRouter

from .return_api import ReturnOrderViewSet

router = DefaultRouter()
router.register("return-orders", ReturnOrderViewSet, basename="pda-return-order")
urlpatterns = router.urls

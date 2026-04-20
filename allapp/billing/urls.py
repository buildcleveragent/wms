from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BillViewSet,
    BillingAccrualViewSet,
    BillingEventViewSet,
    BillingMetricDailyViewSet,
    BillingPeriodViewSet,
    BillingRuleTierViewSet,
    BillingRuleViewSet,
    BillingWarehouseOverviewApi,
)

router = DefaultRouter()
router.register(r"billing/rules", BillingRuleViewSet, basename="billing-rule")
router.register(r"billing/rule-tiers", BillingRuleTierViewSet, basename="billing-rule-tier")
router.register(r"billing/metrics", BillingMetricDailyViewSet, basename="billing-metric")
router.register(r"billing/events", BillingEventViewSet, basename="billing-event")
router.register(r"billing/accruals", BillingAccrualViewSet, basename="billing-accrual")
router.register(r"billing/periods", BillingPeriodViewSet, basename="billing-period")
router.register(r"billing/bills", BillViewSet, basename="billing-bill")

urlpatterns = [
    path("billing/dashboard/warehouse-overview/", BillingWarehouseOverviewApi.as_view(), name="billing-warehouse-overview"),
]
urlpatterns += router.urls

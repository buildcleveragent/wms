from django.urls import include, path
from rest_framework.routers import DefaultRouter

from allapp.tasking.views_replenishment import (
    ReplenishmentEvaluateView,
    ReplenishmentPolicyViewSet,
    ReplenishmentRequestViewSet,
    ReplenishmentTaskViewSet,
)


router = DefaultRouter()
router.register("policies", ReplenishmentPolicyViewSet, basename="replenishment-policy")
router.register(
    "requests", ReplenishmentRequestViewSet, basename="replenishment-request"
)
router.register(
    "pda/tasks", ReplenishmentTaskViewSet, basename="replenishment-pda-task"
)

urlpatterns = [
    path(
        "evaluate/", ReplenishmentEvaluateView.as_view(), name="replenishment-evaluate"
    ),
    path("", include(router.urls)),
]

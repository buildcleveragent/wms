from django.urls import include, path
from rest_framework.routers import DefaultRouter

from allapp.tasking.views_relocation import (
    RelocationDirectReleaseView,
    RelocationOptionsView,
    RelocationRequestViewSet,
    RelocationTaskViewSet,
)


router = DefaultRouter()
router.register("requests", RelocationRequestViewSet, basename="relocation-request")
router.register("pda/tasks", RelocationTaskViewSet, basename="relocation-pda-task")

urlpatterns = [
    path("options/", RelocationOptionsView.as_view(), name="relocation-options"),
    path("direct-release/", RelocationDirectReleaseView.as_view(), name="relocation-direct-release"),
    path("", include(router.urls)),
]

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from allapp.tasking.count_views import CountTaskViewSet
from allapp.tasking.home_todos import HomeTodosView

router = DefaultRouter()
router.register(r"pda/count-tasks", CountTaskViewSet, basename="pda-count-task")

urlpatterns = [
    path("pda/home-todos/", HomeTodosView.as_view(), name="pda-home-todos"),
    path("", include(router.urls)),
]

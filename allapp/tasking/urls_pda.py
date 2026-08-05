from django.urls import include, path
from rest_framework.routers import DefaultRouter

from allapp.tasking.count_views import CountTaskViewSet


router = DefaultRouter()
router.register(r"pda/count-tasks", CountTaskViewSet, basename="pda-count-task")

urlpatterns = [path("", include(router.urls))]

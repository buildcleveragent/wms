# allapp/tasking/urls.py
from rest_framework.routers import DefaultRouter
from .views import (
    WmsTaskViewSet, WmsTaskLineViewSet,
    TaskAssignmentViewSet, TaskStatusLogViewSet, TaskScanLogViewSet
)

router = DefaultRouter()
router.register(r"tasks", WmsTaskViewSet, basename="task")
router.register(r"task-lines", WmsTaskLineViewSet, basename="task-line")
router.register(r"assignments", TaskAssignmentViewSet, basename="task-assignment")
router.register(r"status-logs", TaskStatusLogViewSet, basename="task-status-log")
router.register(r"scan-logs", TaskScanLogViewSet, basename="task-scan-log")

urlpatterns = router.urls

# ===============================
# FILE: allapp/tasking/urls_console.py
# 说明：Tasking 的 CBV 路由（不影响原 Admin），用于仓内操作控制台/HTMX 端点
# ===============================
from django.urls import path
from allapp.tasking.views_console import (
    TaskListView,
    TaskLineWorkListView,
    TaskLineWorkView,
    ReceiveTaskListView,
    PutawayTaskListView,
    PickTaskBoardView,
    ReviewTaskListView,
    PackTaskListView,
    DispatchTaskListView,
    TaskDetailView,
    TaskPostView,
    TaskClaimView,
    TaskLineSaveSnapshotView,
    TaskLineFinalizeView,
)

app_name = "tasking_console"

urlpatterns = [
    # 列表 / 详情
    path("tasks/receive/", ReceiveTaskListView.as_view(), name="task_receive_list"),
    path("tasks/putaway/", PutawayTaskListView.as_view(), name="task_putaway_list"),
    path("tasks/pick/", PickTaskBoardView.as_view(), name="task_pick_list"),
    path("tasks/review/", ReviewTaskListView.as_view(), name="task_review_list"),
    path("tasks/pack/", PackTaskListView.as_view(), name="task_pack_list"),
    path("tasks/dispatch/", DispatchTaskListView.as_view(), name="task_dispatch_list"),
    path("tasks/", TaskListView.as_view(), name="task_list"),
    path("tasks/<int:pk>/", TaskDetailView.as_view(), name="task_detail"),
    path("task-lines/work/", TaskLineWorkListView.as_view(), name="task_line_work_list"),
    path("task-lines/<int:pk>/work/", TaskLineWorkView.as_view(), name="task_line_work"),

    # 任务动作（POST）
    path("tasks/<int:pk>/post/", TaskPostView.as_view(), name="task_post"),
    path("tasks/<int:pk>/claim/", TaskClaimView.as_view(), name="task_claim"),

    # 行级动作（POST）
    path("task-lines/<int:pk>/scan-snapshot/", TaskLineSaveSnapshotView.as_view(), name="line_scan_snapshot"),
    path("task-lines/<int:pk>/finalize/", TaskLineFinalizeView.as_view(), name="line_finalize"),
]

from django.urls import path
from allapp.console.views_op import (
    OpTaskListView, OpTaskDetailView,
    OpScanView, OpManualView, OpSaveSnapshotView, OpPostView, OpClearView, OpLineListView, OpLineDetailRedirectView,
    OpLineClaimView, OpLineUnclaimView, OpLineEditView
)

app_name = "op"

urlpatterns = [
    path("tasks/", OpTaskListView.as_view(), name="task_list"),
    path("tasks/<int:pk>/", OpTaskDetailView.as_view(), name="task_detail"),
    path("tasks/<int:pk>/scan/", OpScanView.as_view(), name="scan"),
    path("tasks/<int:pk>/manual/", OpManualView.as_view(), name="manual"),
    path("tasks/<int:pk>/save/", OpSaveSnapshotView.as_view(), name="save"),
    path("tasks/<int:pk>/post/", OpPostView.as_view(), name="post"),
    path("tasks/<int:pk>/clear/", OpClearView.as_view(), name="clear"),

    # ====== 新增：按行作业 CBV ======
    path("lines/", OpLineListView.as_view(), name="line_list"),
    # 进入行的数据录入界面：重定向到任务详情页，带上 ?line=<line_id>
    # path("lines/<int:line_id>/", OpLineDetailRedirectView.as_view(), name="line_detail"),
    path("lines/<int:line_id>/", OpLineEditView.as_view(), name="line_detail"),
    # 抢单/放回（按任务粒度）
    path("lines/<int:line_id>/claim/", OpLineClaimView.as_view(), name="line_claim"),
    path("lines/<int:line_id>/unclaim/", OpLineUnclaimView.as_view(), name="line_unclaim"),
]

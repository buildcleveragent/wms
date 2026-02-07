# ===============================
# FILE: allapp/console/urls.py
# 说明：控制台首页（仪表盘）及 API；并挂载 tasking 的 console
# ===============================
from django.urls import path, include
from .views_dashboard import DashboardHomeView, DashboardSummaryApi

app_name = "console"

urlpatterns = [
    # 首页仪表盘
    path("", DashboardHomeView.as_view(), name="dashboard_home"),
    path("dashboard/", DashboardHomeView.as_view(), name="dashboard_home_alias"),

    # 仪表盘数据（一个端点返回全部图表需要的数据）
    path("api/dashboard/summary/", DashboardSummaryApi.as_view(), name="dashboard_summary"),

    # 若你已在其它地方挂了 tasking console，可以保留一份直达
    path("tasking/", include("allapp.tasking.urls_console", namespace="tasking_console")),
    path("op/", include("allapp.console.urls_op")),
]

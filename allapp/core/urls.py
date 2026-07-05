from django.urls import path

from .views import DefaultPrintConfigApi, PrintConfigListApi, SystemSettingsApi

urlpatterns = [
    path("settings/", SystemSettingsApi.as_view(), name="system-settings"),
    path("print-configs/", PrintConfigListApi.as_view(), name="print-config-list"),
    path(
        "print-configs/default/",
        DefaultPrintConfigApi.as_view(),
        name="print-config-default",
    ),
]

from django.urls import path

from .views import SystemSettingsApi

urlpatterns = [
    path("settings/", SystemSettingsApi.as_view(), name="system-settings"),
]

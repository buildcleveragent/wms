from django.urls import path

from .views_boss import BossAlertApi, BossHomeApi, BossInventoryApi

urlpatterns = [
    path("boss/home/", BossHomeApi.as_view(), name="reports-boss-home"),
    path("boss/inventory/", BossInventoryApi.as_view(), name="reports-boss-inventory"),
    path("boss/alerts/", BossAlertApi.as_view(), name="reports-boss-alerts"),
]

# allapp/api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

# 统一在这里集中注册各 app 的 ViewSet
router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
]

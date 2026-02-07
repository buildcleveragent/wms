# -*- coding: utf-8 -*-
# accounts/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UserViewSet, SystemLogViewSet

app_name = "accounts"

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'system-logs', SystemLogViewSet, basename='systemlog')

urlpatterns = [
    # /accounts/users/..., /accounts/system-logs/...
    path('', include(router.urls)),
    # 可选：启用 DRF 浏览器登录/退出（不影响 API Token/JWT）
    path('auth/', include('rest_framework.urls', namespace='rest_framework')),
]

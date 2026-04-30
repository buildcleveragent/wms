# allapp/api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import download_page, download_bv2
router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
    path("download/", download_page, name="download_page"),
    path("bv2/<str:filename>.apk", download_bv2, name="download_bv2"),
]

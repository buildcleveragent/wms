# allapp/api/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import download_bv2, download_page

router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
    path("download/", download_page, name="download_page"),
    path("bv2/<str:filename>.apk", download_bv2, name="download_bv2"),
]

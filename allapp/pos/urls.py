from django.urls import path

from .views import PosCheckoutApi, PosProductListApi

urlpatterns = [
    path("products/", PosProductListApi.as_view(), name="pos-products"),
    path("checkout/", PosCheckoutApi.as_view(), name="pos-checkout"),
]

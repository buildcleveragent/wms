from django.urls import path

from .views import (
    PosCheckoutApi,
    PosProductListApi,
    PosSaleDetailApi,
    PosSaleReceiptApi,
    PosSaleVoidApi,
)

urlpatterns = [
    path("products/", PosProductListApi.as_view(), name="pos-products"),
    path("checkout/", PosCheckoutApi.as_view(), name="pos-checkout"),
    path("sales/<int:sale_id>/", PosSaleDetailApi.as_view(), name="pos-sale-detail"),
    path("sales/<int:sale_id>/receipt/", PosSaleReceiptApi.as_view(), name="pos-sale-receipt"),
    path("sales/<int:sale_id>/void/", PosSaleVoidApi.as_view(), name="pos-sale-void"),
]

# apps/products/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views
from .views import ProductViewSet
from allapp.products.autocomplete import ProductUomAutocomplete

router = DefaultRouter()
router.register(r"products", ProductViewSet, basename="product")

urlpatterns = [
    # 其他普通路由
    path("autocomplete/uom/", ProductUomAutocomplete.as_view(), name="uom-autocomplete"),
    path('get_product_details/<int:product_id>/', views.get_product_details, name='get_product_details'),

]

# 把 DRF 的路由追加进去（千万别重新赋值覆盖）
urlpatterns += router.urls

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, CustomerViewSet, OutboundOrderViewSet,OwnerViewSet,SupplierViewSet,ReceiveProductViewSet,PickTaskViewSet
from .export_print import pick_task_print

router = DefaultRouter()
router.register(r"catalog/products", ProductViewSet, basename="ob-product")
router.register(r"catalog/customers", CustomerViewSet, basename="ob-customer")
router.register(r"outbound/orders", OutboundOrderViewSet, basename="ob-order")

router.register(r"catalog/owners", OwnerViewSet, basename="ob-Owner")

router.register(r"catalog/suppliers", SupplierViewSet, basename="ob-Supplier")

router.register(r"catalog/receive_products", ReceiveProductViewSet, basename="ob-receive_products")
router.register(r"pda/pick-tasks", PickTaskViewSet, basename="pda-pick-task")
urlpatterns = [
    path("", include(router.urls)),
    path("pda/pick-tasks/<int:task_id>/print/", pick_task_print, name="pick-task-print"),
]

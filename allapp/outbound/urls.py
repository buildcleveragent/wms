from django.urls import include, path
from rest_framework.routers import DefaultRouter

from allapp.inbound.customer_return_api import ReturnEligibilityView

from .export_print import pick_task_print
from .views import (
    AssistedOutboundOrderViewSet,
    CustomerViewSet,
    OutboundOrderViewSet,
    OwnerViewSet,
    PickTaskViewSet,
    ProductViewSet,
    ReceiveProductViewSet,
    SupplierViewSet,
    WarehouseViewSet,
)
from .workbench import OwnerWorkbenchItemsView, OwnerWorkbenchView
from .reorder_api import (
    CheckoutValidationView,
    CustomerAddressView,
    CustomerAddressesView,
    FrequentProductsView,
)

router = DefaultRouter()
router.register(r"catalog/products", ProductViewSet, basename="ob-product")
router.register(r"catalog/customers", CustomerViewSet, basename="ob-customer")
router.register(r"catalog/warehouses", WarehouseViewSet, basename="ob-warehouse")
router.register(r"outbound/orders", OutboundOrderViewSet, basename="ob-order")
router.register(
    r"outbound/assisted-orders",
    AssistedOutboundOrderViewSet,
    basename="assisted-outbound-order",
)

router.register(r"catalog/owners", OwnerViewSet, basename="ob-Owner")

router.register(r"catalog/suppliers", SupplierViewSet, basename="ob-Supplier")

router.register(r"catalog/receive_products", ReceiveProductViewSet, basename="ob-receive_products")
router.register(r"pda/pick-tasks", PickTaskViewSet, basename="pda-pick-task")

urlpatterns = [
    path("outbound/orders/validate/", CheckoutValidationView.as_view(), name="owner-checkout"),
    path(
        "outbound/customers/<int:customer_id>/frequent-products/",
        FrequentProductsView.as_view(), name="owner-frequent-products",
    ),
    path(
        "outbound/customers/<int:customer_id>/addresses/",
        CustomerAddressesView.as_view(), name="owner-customer-addresses",
    ),
    path(
        "outbound/customers/<int:customer_id>/addresses/<int:address_id>/",
        CustomerAddressView.as_view(), name="owner-customer-address",
    ),
    path(
        "outbound/orders/<int:pk>/return-eligibility/",
        ReturnEligibilityView.as_view(),
        name="return-eligibility",
    ),
    path("outbound/owner-workbench/", OwnerWorkbenchView.as_view(), name="owner-workbench"),
    path(
        "outbound/owner-workbench/items/",
        OwnerWorkbenchItemsView.as_view(),
        name="owner-workbench-items",
    ),
    path("", include(router.urls)),
    path("pda/pick-tasks/<int:task_id>/print/", pick_task_print, name="pick-task-print"),
]

from rest_framework.routers import DefaultRouter

from .views import ProductBarcodeViewSet, ProductExternalIdentifierViewSet

router = DefaultRouter()
router.register("product-barcodes", ProductBarcodeViewSet, basename="product-barcode")
router.register(
    "product-external-identifiers",
    ProductExternalIdentifierViewSet,
    basename="product-external-identifier",
)

urlpatterns = router.urls

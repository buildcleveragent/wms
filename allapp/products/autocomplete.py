from dal import autocomplete
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Q

from allapp.baseinfo.models import Owner

from .category_backfill import scoped_products
from .models import Product, ProductPackage, ProductUom


class ProductUomAutocomplete(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        qs = ProductUom.objects.filter(is_active=True).only("id", "code", "name")
        if self.forwarded.get("only_count") in ("1", 1, True):
            qs = qs.filter(kind="COUNT")
        if self.q:
            qs = qs.filter(Q(code__icontains=self.q) | Q(name__icontains=self.q))
        return qs.order_by("code")

    def get_result_label(self, result):
        return f"{result.code} {result.name}"

    def get_selected_result_label(self, result):
        return self.get_result_label(result)


class ProductBarcodeAutocompleteMixin(PermissionRequiredMixin):
    """Restrict standalone barcode-admin lookups to authorized product scope."""

    permission_required = "products.add_productbarcode"
    raise_exception = True

    def scoped_product_queryset(self):
        return scoped_products(
            self.request.user,
            Product.objects.select_related("owner"),
        )


class ProductBarcodeOwnerAutocomplete(
    ProductBarcodeAutocompleteMixin,
    autocomplete.Select2QuerySetView,
):
    def get_queryset(self):
        owner_ids = self.scoped_product_queryset().values("owner_id")
        queryset = Owner.objects.filter(pk__in=owner_ids)
        if self.q:
            queryset = queryset.filter(
                Q(code__icontains=self.q) | Q(name__icontains=self.q)
            )
        return queryset.order_by("code")

    def get_result_label(self, result):
        return f"{result.code} - {result.name}"

    def get_selected_result_label(self, result):
        return self.get_result_label(result)


class ProductBarcodeProductAutocomplete(
    ProductBarcodeAutocompleteMixin,
    autocomplete.Select2QuerySetView,
):
    def get_queryset(self):
        owner_id = self.forwarded.get("owner")
        if not owner_id:
            return Product.objects.none()
        queryset = self.scoped_product_queryset().filter(owner_id=owner_id)
        if self.q:
            queryset = queryset.filter(
                Q(code__icontains=self.q)
                | Q(name__icontains=self.q)
                | Q(sku__icontains=self.q)
            )
        return queryset.order_by("code")

    def get_result_label(self, result):
        return f"{result.code} - {result.name}"

    def get_selected_result_label(self, result):
        return self.get_result_label(result)


class ProductBarcodePackageAutocomplete(
    ProductBarcodeAutocompleteMixin,
    autocomplete.Select2QuerySetView,
):
    def get_queryset(self):
        product_id = self.forwarded.get("product")
        if not product_id:
            return ProductPackage.objects.none()
        allowed_products = self.scoped_product_queryset().filter(pk=product_id)
        queryset = (
            ProductPackage.all_objects.filter(
                product__in=allowed_products,
                is_active=True,
                is_deleted=False,
            )
            .select_related("product", "uom")
            .order_by("sort_order", "uom__code")
        )
        if self.q:
            queryset = queryset.filter(
                Q(uom__code__icontains=self.q)
                | Q(uom__name__icontains=self.q)
                | Q(barcode__icontains=self.q)
            )
        return queryset

    def get_result_label(self, result):
        return f"{result.uom.code} - {result.uom.name}（1 = {result.qty_in_base} 基本单位）"

    def get_selected_result_label(self, result):
        return self.get_result_label(result)

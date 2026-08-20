"""Shared product search and exact identifier lookup helpers.

Business querysets keep responsibility for owner, warehouse, role and status
scope.  This module only supplies the product matching predicate.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from .models import (
    Product,
    ProductBarcode,
    ProductExternalIdentifier,
    ProductIdentifierRegistry,
    normalize_product_identifier,
)


def effective_product_barcodes(*, at=None) -> QuerySet:
    """Return barcode history records effective at ``at`` (inclusive)."""
    at = at or timezone.now()
    return ProductBarcode.all_objects.filter(
        is_active=True,
        is_deleted=False,
        product__is_active=True,
        product__is_deleted=False,
    ).filter(
        Q(package__isnull=True) | Q(package__is_active=True, package__is_deleted=False),
        Q(valid_from__isnull=True) | Q(valid_from__lte=at),
        Q(valid_to__isnull=True) | Q(valid_to__gte=at),
    )


def effective_external_identifiers(*, at=None) -> QuerySet:
    """Return external identifiers effective at ``at`` (inclusive)."""
    at = at or timezone.now()
    return ProductExternalIdentifier.all_objects.filter(
        is_active=True,
        is_deleted=False,
        product__is_active=True,
        product__is_deleted=False,
    ).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=at),
        Q(valid_to__isnull=True) | Q(valid_to__gte=at),
    )


def matching_product_ids(search, *, at=None) -> QuerySet:
    """Return a subquery of products matching one complete, trimmed phrase."""
    term = (search or "").strip()
    products = Product.objects.all()
    if not term:
        return products.values("pk")

    at = at or timezone.now()
    normalized_term = normalize_product_identifier(term)
    barcode_match = effective_product_barcodes(at=at).filter(
        product_id=OuterRef("pk"),
        normalized_value__icontains=normalized_term,
    )
    external_match = effective_external_identifiers(at=at).filter(
        product_id=OuterRef("pk"),
        normalized_value__icontains=normalized_term,
    )
    return (
        products.annotate(
            _identifier_barcode_match=Exists(barcode_match),
            _identifier_external_match=Exists(external_match),
        )
        .filter(
            Q(name__icontains=term)
            | Q(spec__icontains=term)
            | Q(code__icontains=term)
            | Q(sku__icontains=term)
            | Q(_identifier_barcode_match=True)
            | Q(_identifier_external_match=True)
        )
        .values("pk")
    )


def product_search_q(search, *, product_field="product_id", at=None) -> Q:
    """Build a Q object for a direct or related product foreign-key path."""
    return Q(**{f"{product_field}__in": Subquery(matching_product_ids(search, at=at))})


def filter_by_product_search(queryset, search, *, product_field="product_id", at=None):
    """Filter an arbitrary business queryset by the shared product rules."""
    term = (search or "").strip()
    if not term:
        return queryset
    return queryset.filter(product_search_q(term, product_field=product_field, at=at))


class UnifiedProductAdminSearchMixin:
    """Add current product identifiers to Django Admin's ordinary search."""

    product_search_paths = ("product_id",)
    _product_search_leaf_fields = {
        "code",
        "sku",
        "name",
        "spec",
        "gtin",
        "unit_barcode",
        "carton_barcode",
        "external_code",
        "barcode",
    }

    def get_search_fields(self, request):
        fields = super().get_search_fields(request)
        relation_paths = {
            path[:-3] if path.endswith("_id") else path for path in self.product_search_paths
        }
        return tuple(
            field
            for field in fields
            if not any(
                field.startswith(f"{relation}__")
                and field.rsplit("__", 1)[-1] in self._product_search_leaf_fields
                for relation in relation_paths
            )
        )

    def get_search_results(self, request, queryset, search_term):
        default_results, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        term = (search_term or "").strip()
        if not term:
            return default_results, may_have_duplicates
        product_match = Q()
        at = timezone.now()
        for path in self.product_search_paths:
            product_match |= product_search_q(term, product_field=path, at=at)
        return (
            default_results | queryset.filter(product_match),
            may_have_duplicates or any("__" in path for path in self.product_search_paths),
        )


def exact_matching_product_ids(value, *, owner_ids=None, at=None) -> QuerySet:
    """Return products whose current stable/history source exactly matches value."""
    normalized = normalize_product_identifier(value)
    at = at or timezone.now()
    registry = ProductIdentifierRegistry.objects.filter(normalized_value=normalized)
    if owner_ids is not None:
        registry = registry.filter(owner_id__in=owner_ids)

    active_barcode = effective_product_barcodes(at=at).filter(
        product_id=OuterRef("product_id"), normalized_value=normalized
    )
    active_external = effective_external_identifiers(at=at).filter(
        product_id=OuterRef("product_id"), normalized_value=normalized
    )
    return (
        registry.annotate(
            _active_barcode=Exists(active_barcode),
            _active_external=Exists(active_external),
        )
        .filter(
            Q(product__code__iexact=normalized)
            | Q(product__sku__iexact=normalized)
            | Q(_active_barcode=True)
            | Q(_active_external=True)
        )
        .values("product_id")
    )


@dataclass(frozen=True)
class ExactIdentifierSources:
    registry: ProductIdentifierRegistry
    stable_fields: tuple[tuple[str, str], ...]
    barcodes: tuple[ProductBarcode, ...]
    external_identifiers: tuple[ProductExternalIdentifier, ...]
    has_history: bool


def get_exact_identifier_sources(owner_id, value, *, at=None) -> ExactIdentifierSources:
    """Load all current sources for one owner-scoped registered identifier.

    ``ProductIdentifierRegistry.DoesNotExist`` and ``MultipleObjectsReturned``
    intentionally propagate so callers can preserve their public error behavior.
    """
    normalized = normalize_product_identifier(value)
    at = at or timezone.now()
    registry = ProductIdentifierRegistry.objects.select_related("product__base_uom").get(
        owner_id=owner_id, normalized_value=normalized
    )
    product = registry.product
    stable_fields = tuple(
        (field, code_type)
        for field, code_type in (("sku", "SKU"), ("code", "PRODUCT_CODE"))
        if normalize_product_identifier(getattr(product, field, None)) == normalized
    )
    barcodes = tuple(
        effective_product_barcodes(at=at)
        .filter(product=product, normalized_value=normalized)
        .select_related("package__uom")
    )
    external_identifiers = tuple(
        effective_external_identifiers(at=at).filter(product=product, normalized_value=normalized)
    )
    has_history = (
        ProductBarcode.all_objects.filter(product=product, normalized_value=normalized).exists()
        or ProductExternalIdentifier.all_objects.filter(
            product=product, normalized_value=normalized
        ).exists()
    )
    return ExactIdentifierSources(
        registry=registry,
        stable_fields=stable_fields,
        barcodes=barcodes,
        external_identifiers=external_identifiers,
        has_history=has_history,
    )

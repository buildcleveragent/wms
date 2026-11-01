"""Read-only product search and grouping shared by outbound histories."""

from django.db.models import Count, Q, Sum

from allapp.core.product_identity import combine_brand_model, identity_display_name
from allapp.products.identifier_lookup import product_search_q

from .models import OutboundOrderLine


def history_product_q(search):
    """Match current identifiers and immutable document descriptions."""
    return (
        product_search_q(search)
        | Q(product_snapshot__name__icontains=search)
        | Q(product_snapshot__brand_model__icontains=search)
        | Q(product_snapshot__spec__icontains=search)
    )


def product_ranking(orders, top_n=10):
    """Group by product identity and document unit, displaying current catalogue text."""
    rows = (
        OutboundOrderLine.objects.filter(order_id__in=orders.values("pk"), is_deleted=False)
        .values(
            "product_id",
            "base_uom_id",
            "product__code",
            "product__sku",
            "product__name",
            "product__brand__name",
            "product__model",
            "product__spec",
            "base_uom__code",
            "base_uom__name",
        )
        .annotate(order_count=Count("order_id", distinct=True), total_base_qty=Sum("base_qty"))
        .order_by("-total_base_qty", "product_id", "base_uom_id")[:top_n]
    )
    result = []
    for row in rows:
        brand_model = combine_brand_model(row["product__brand__name"], row["product__model"])
        result.append(
            {
                "product_id": row["product_id"],
                "product_code": row["product__code"] or "",
                "product_sku": row["product__sku"] or "",
                "product_name": row["product__name"] or "",
                "brand_model": brand_model,
                "product_spec": row["product__spec"] or "",
                "product_display_name": identity_display_name(
                    {
                        "name": row["product__name"],
                        "brand_model": brand_model,
                    }
                ),
                "base_uom_id": row["base_uom_id"],
                "base_uom_code": row["base_uom__code"] or "",
                "base_uom_name": row["base_uom__name"] or row["base_uom__code"] or "",
                "order_count": row["order_count"],
                "total_base_qty": str(row["total_base_qty"] or 0),
            }
        )
    return result

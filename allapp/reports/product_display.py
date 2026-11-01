"""Additive product descriptions for inventory reports (current catalogue)."""

from allapp.core.product_identity import identity_display_name, product_identity


def product_display_fields(product):
    """Keep catalogue descriptions distinct from document identity snapshots."""
    identity = product_identity(product)
    return {
        "product_name": identity["name"],
        "product_spec": identity["spec"],
        "brand_model": identity["brand_model"],
        "product_display_name": identity_display_name(identity),
    }

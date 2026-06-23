VIEW_ALL_OWNER_PRODUCTS_PERM = "products.view_all_owner_products"
MANAGE_ALL_OWNER_PRODUCTS_PERM = "products.manage_all_owner_products"


def can_view_all_owner_products(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return (
        getattr(user, "is_superuser", False)
        or user.has_perm(VIEW_ALL_OWNER_PRODUCTS_PERM)
        or user.has_perm(MANAGE_ALL_OWNER_PRODUCTS_PERM)
    )


def can_manage_all_owner_products(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "is_superuser", False) or user.has_perm(
        MANAGE_ALL_OWNER_PRODUCTS_PERM
    )

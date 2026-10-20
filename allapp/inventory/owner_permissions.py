"""One permission boundary for owner inventory views, downloads and alerts."""

from rest_framework.exceptions import PermissionDenied

from allapp.accounts.access import AccessScope


def inventory_capabilities(user, scope=None):
    scope = scope or AccessScope.for_user(user)
    owner_scope = bool(
        getattr(user, "is_active", False)
        and scope.is_valid
        and not scope.is_global
        and len(scope.owner_ids) == 1
        and not scope.warehouse_ids
        and scope.roles
        and scope.roles <= {"owner_manager", "owner_salesperson"}
    )
    manager = (
        owner_scope
        and "owner_manager" in scope.roles
        and user.has_perm("accounts.access_owner_management")
    )
    sales = (
        owner_scope
        and "owner_salesperson" in scope.roles
        and user.has_perm("accounts.access_owner_sales")
    )
    return {
        "can_view_owner_inventory": bool(manager or sales),
        "can_export_owner_inventory": bool(manager or sales),
        "can_manage_owner_inventory_alerts": bool(manager),
    }


def owner_inventory_scope(user):
    scope = AccessScope.for_user(user)
    if not inventory_capabilities(user, scope)["can_view_owner_inventory"]:
        raise PermissionDenied("当前账号没有货主库存访问权限。")
    return scope

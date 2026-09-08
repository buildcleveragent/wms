"""POS warehouse boundary, preserving legacy cashier bindings until migrated."""

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope


def pos_warehouse_allowed(user):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id or not getattr(user, "is_active", False):
        return False
    if user.is_superuser:
        return True
    if not UserRoleScope.objects.filter(user_id=user.pk).exists():
        return True
    scope = AccessScope.for_user(user)
    return scope.is_valid and warehouse_id in scope.warehouse_ids

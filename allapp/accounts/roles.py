"""Canonical WMS roles and their least-privilege group templates."""

from dataclasses import dataclass

from django.contrib.auth.models import Permission
from django.db.models import Q

from .models import UserRoleScope


@dataclass(frozen=True)
class RoleGroupTemplate:
    role: str
    group_name: str
    permissions: tuple[str, ...]


ROLE_GROUP_TEMPLATES = {
    UserRoleScope.Role.WAREHOUSE_OPERATOR: RoleGroupTemplate(
        role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
        group_name="WMS::仓库操作员",
        permissions=(
            "accounts.access_warehouse_operations",
            "accounts.receive_without_order",
            "reports.view_warehouse_operations",
            "tasking.view_wmstask",
            "tasking.claim_task_as_wh_operator",
            "outbound.process_warehouse_assisted_outbound",
        ),
    ),
    UserRoleScope.Role.WAREHOUSE_MANAGER: RoleGroupTemplate(
        role=UserRoleScope.Role.WAREHOUSE_MANAGER,
        group_name="WMS::仓库管理员",
        permissions=(
            "accounts.access_warehouse_management",
            "reports.view_warehouse_operations",
            "reports.export_operations",
            "inbound.view_inboundorder",
            "inbound.change_inboundorder",
            "inbound.approve_as_wh_manager",
            "outbound.view_outboundorder",
            "outbound.change_outboundorder",
            "outbound.approve_outbound_as_wh_manager",
            "tasking.view_wmstask",
            "tasking.change_wmstask",
            "tasking.taskconfirm_as_wh_manager",
            "inventory.view_inventorydetail",
            "inventory.view_inventorysummary",
            "inventory.view_inventorytransaction",
            "inventory.view_inventorysnapshotdaily",
        ),
    ),
    UserRoleScope.Role.WAREHOUSE_BOSS: RoleGroupTemplate(
        role=UserRoleScope.Role.WAREHOUSE_BOSS,
        group_name="WMS::仓库老板",
        permissions=(
            "reports.view_boss_dashboard",
            "reports.view_warehouse_operations",
            "reports.view_warehouse_finance",
            "reports.export_operations",
        ),
    ),
    UserRoleScope.Role.OWNER_MANAGER: RoleGroupTemplate(
        role=UserRoleScope.Role.OWNER_MANAGER,
        group_name="WMS::货主管理员",
        permissions=(
            "accounts.access_owner_management",
            "accounts.view_owner_financials",
            "reports.view_owner_operations",
            "reports.export_operations",
            "inbound.view_inboundorder",
            "inbound.change_inboundorder",
            "inbound.approve_as_owner_manager",
            "outbound.view_outboundorder",
            "outbound.change_outboundorder",
            "outbound.approve_outbound_as_owner_manager",
        ),
    ),
    UserRoleScope.Role.OWNER_SALESPERSON: RoleGroupTemplate(
        role=UserRoleScope.Role.OWNER_SALESPERSON,
        group_name="WMS::货主业务员",
        permissions=(
            "accounts.access_owner_sales",
            "reports.view_owner_operations",
            "inbound.view_inboundorder",
            "inbound.add_inboundorder",
            "inbound.change_inboundorder",
            "inbound.submit_as_owner_buyers",
            "outbound.view_outboundorder",
            "outbound.add_outboundorder",
            "outbound.change_outboundorder",
            "outbound.submit_outbound_as_owner_buyers",
        ),
    ),
}


ROLE_GROUP_ALIASES = {
    role: frozenset({template.group_name, str(UserRoleScope.Role(role).label), role})
    for role, template in ROLE_GROUP_TEMPLATES.items()
}


# Shared report permissions are intentionally absent: they cannot distinguish a
# manager from a boss.  These markers are unique role capabilities or legacy
# action permissions, and are used only to resolve the boundary for old users
# that do not yet have UserRoleScope rows.
ROLE_PERMISSION_MARKERS = {
    UserRoleScope.Role.WAREHOUSE_OPERATOR: frozenset(
        {
            "accounts.access_warehouse_operations",
            "accounts.receive_without_order",
            "tasking.claim_task_as_wh_operator",
            "outbound.process_warehouse_assisted_outbound",
        }
    ),
    UserRoleScope.Role.WAREHOUSE_MANAGER: frozenset(
        {
            "accounts.access_warehouse_management",
            "inbound.approve_as_wh_manager",
            "outbound.approve_outbound_as_wh_manager",
            "tasking.taskconfirm_as_wh_manager",
        }
    ),
    UserRoleScope.Role.WAREHOUSE_BOSS: frozenset(
        {
            "accounts.view_warehouse_boss_dashboard",
            "reports.view_boss_dashboard",
        }
    ),
    UserRoleScope.Role.OWNER_MANAGER: frozenset(
        {
            "accounts.access_owner_management",
            "inbound.approve_as_owner_manager",
            "outbound.approve_outbound_as_owner_manager",
        }
    ),
    UserRoleScope.Role.OWNER_SALESPERSON: frozenset(
        {
            "accounts.access_owner_sales",
            "inbound.submit_as_owner_buyers",
            "outbound.submit_outbound_as_owner_buyers",
        }
    ),
}


def infer_user_roles(user) -> frozenset[str]:
    """Infer legacy role intent from canonical groups and distinctive permissions."""

    if not user or not getattr(user, "is_authenticated", False) or not user.pk:
        return frozenset()

    group_names = set(user.groups.values_list("name", flat=True))
    # ``get_all_permissions`` caches its answer on the User instance.  Role
    # resolution is a security boundary and must immediately reflect a group or
    # direct-permission change within the current request/transaction.
    permissions = {
        f"{app_label}.{codename}"
        for app_label, codename in Permission.objects.filter(
            Q(user=user) | Q(group__user=user)
        )
        .values_list("content_type__app_label", "codename")
        .distinct()
    }
    roles = set()
    for role, aliases in ROLE_GROUP_ALIASES.items():
        if group_names.intersection(aliases):
            roles.add(role)
            continue
        if permissions.intersection(ROLE_PERMISSION_MARKERS[role]):
            roles.add(role)
    return frozenset(roles)


def role_group_name(role: str) -> str:
    """Return the canonical Group name for a role code."""

    return ROLE_GROUP_TEMPLATES[role].group_name

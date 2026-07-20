"""Synchronize canonical role-group membership from explicit user scopes."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import UserRoleScope
from .roles import ROLE_GROUP_ALIASES, role_group_name


ROLE_GROUP_NAMES = frozenset(
    name for aliases in ROLE_GROUP_ALIASES.values() for name in aliases
)


@dataclass(frozen=True)
class RoleMembershipChange:
    role: str | None
    desired_group: str | None
    added: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def explicit_role_for_user(user) -> str | None:
    """Return the single active explicit role, rejecting invalid combinations."""

    rows = list(
        UserRoleScope.objects.filter(user_id=user.pk, is_active=True).values_list(
            "role", flat=True
        )
    )
    roles = frozenset(rows)
    if len(roles) > 1:
        raise ValidationError("同一用户不能同时启用多个业务角色。")
    if rows and next(iter(roles)) != UserRoleScope.Role.WAREHOUSE_BOSS and len(rows) > 1:
        raise ValidationError("除仓库老板外，每个用户只能有一个活动范围。")
    return next(iter(roles), None)


def validate_role_group_configuration(user) -> str | None:
    """Validate the derived role and ensure its canonical group exists."""

    role = None if user.is_superuser else explicit_role_for_user(user)
    if role is None:
        return None
    group_name = role_group_name(role)
    if not Group.objects.filter(name=group_name).exists():
        raise ValidationError(
            f"缺少规范角色组“{group_name}”，请先执行 "
            "python manage.py sync_wms_role_groups。"
        )
    return role


def plan_user_role_membership(user) -> RoleMembershipChange:
    """Return the membership reconciliation required for one user."""

    role = validate_role_group_configuration(user)
    desired_name = role_group_name(role) if role else None
    current_names = set(
        user.groups.filter(name__in=ROLE_GROUP_NAMES).values_list("name", flat=True)
    )
    return RoleMembershipChange(
        role=role,
        desired_group=desired_name,
        added=(desired_name,) if desired_name and desired_name not in current_names else (),
        removed=tuple(sorted(current_names - ({desired_name} if desired_name else set()))),
    )


@transaction.atomic
def sync_user_role_membership(user) -> RoleMembershipChange:
    """Make recognized role groups a derived value of ``UserRoleScope``.

    Custom/auxiliary groups and direct permissions are deliberately untouched.
    Superusers need no canonical role group and therefore resolve to no role.
    """

    type(user).objects.select_for_update().get(pk=user.pk)
    change = plan_user_role_membership(user)
    role = change.role
    desired_name = change.desired_group
    desired_group = (
        Group.objects.get(name=desired_name) if desired_name is not None else None
    )

    removed_groups = list(user.groups.filter(name__in=change.removed).order_by("name"))
    if removed_groups:
        user.groups.remove(*removed_groups)

    if desired_group is not None and change.added:
        user.groups.add(desired_group)

    for cache_name in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_name):
            delattr(user, cache_name)

    return change

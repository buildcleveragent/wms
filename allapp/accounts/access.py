"""Fail-closed owner/warehouse scoping shared by APIs, admin and exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db.models import QuerySet

from .models import UserRoleScope
from .roles import infer_legacy_user_roles, infer_user_group_roles


@dataclass(frozen=True, slots=True)
class AccessScope:
    """Resolved tenant boundary for one authenticated user.

    Capabilities still come from Django Groups/permissions.  This value object
    only answers which tenant rows those capabilities may reach.
    """

    roles: frozenset[str] = field(default_factory=frozenset)
    owner_ids: frozenset[int] = field(default_factory=frozenset)
    warehouse_ids: frozenset[int] = field(default_factory=frozenset)
    is_global: bool = False
    is_valid: bool = False
    source: str = "none"
    denial_reason: str = ""

    @classmethod
    def for_user(cls, user) -> "AccessScope":
        """Resolve explicit role scopes, then a conservative legacy fallback."""

        if not user or not getattr(user, "is_authenticated", False):
            return cls._denied("unauthenticated")
        if not getattr(user, "is_active", False) or not getattr(user, "pk", None):
            return cls._denied("inactive_or_unsaved_user")
        if getattr(user, "is_superuser", False):
            return cls(
                roles=frozenset({"superuser"}),
                is_global=True,
                is_valid=True,
                source="superuser",
            )

        rows = list(
            UserRoleScope.objects.filter(user_id=user.pk, is_active=True).values(
                "role", "owner_id", "warehouse_id"
            )
        )
        if rows:
            return cls._from_explicit_rows(
                rows,
                group_roles=infer_user_group_roles(user),
            )

        inferred_roles = infer_legacy_user_roles(user)
        if not getattr(settings, "WMS_ACCESS_SCOPE_LEGACY_FALLBACK", False):
            if (
                inferred_roles
                or getattr(user, "owner_id", None)
                or getattr(user, "warehouse_id", None)
            ):
                return cls._denied(
                    "missing_explicit_role_scope",
                    roles=inferred_roles,
                )
            return cls._denied("unbound_user")
        return cls._from_legacy_user(user, inferred_roles=inferred_roles)

    @classmethod
    def _from_explicit_rows(
        cls,
        rows: list[dict[str, Any]],
        *,
        group_roles: frozenset[str],
    ) -> "AccessScope":
        roles = frozenset(row["role"] for row in rows)
        if len(roles) != 1:
            return cls._denied("conflicting_explicit_roles", roles=roles)
        if group_roles and group_roles != roles:
            return cls._denied(
                "role_scope_and_group_conflict",
                roles=roles | group_roles,
            )

        role = next(iter(roles))
        if role in UserRoleScope.WAREHOUSE_ROLES:
            if any(row["owner_id"] or not row["warehouse_id"] for row in rows):
                return cls._denied("invalid_warehouse_role_target", roles=roles)
            warehouse_ids = frozenset(int(row["warehouse_id"]) for row in rows)
            if role != UserRoleScope.Role.WAREHOUSE_BOSS and len(rows) != 1:
                return cls._denied("multiple_scopes_for_single_scope_role", roles=roles)
            return cls(
                roles=roles,
                warehouse_ids=warehouse_ids,
                is_valid=True,
                source="user_role_scope",
            )

        if role in UserRoleScope.OWNER_ROLES:
            if len(rows) != 1:
                return cls._denied("multiple_scopes_for_single_scope_role", roles=roles)
            row = rows[0]
            if row["warehouse_id"] or not row["owner_id"]:
                return cls._denied("invalid_owner_role_target", roles=roles)
            return cls(
                roles=roles,
                owner_ids=frozenset({int(row["owner_id"])}),
                is_valid=True,
                source="user_role_scope",
            )

        return cls._denied("unknown_explicit_role", roles=roles)

    @classmethod
    def _from_legacy_user(
        cls,
        user,
        *,
        inferred_roles: frozenset[str],
    ) -> "AccessScope":
        if len(inferred_roles) > 1:
            return cls._denied("conflicting_legacy_roles", roles=inferred_roles)

        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if inferred_roles:
            role = next(iter(inferred_roles))
            if role in UserRoleScope.OWNER_ROLES:
                if not owner_id:
                    return cls._denied("owner_role_without_owner", roles=inferred_roles)
                # A legacy owner user may also carry a warehouse binding for order
                # entry.  It must never turn an owner role into warehouse-wide access.
                return cls(
                    roles=inferred_roles,
                    owner_ids=frozenset({int(owner_id)}),
                    is_valid=True,
                    source="legacy_role_and_binding",
                )
            if role in UserRoleScope.WAREHOUSE_ROLES:
                if not warehouse_id:
                    return cls._denied(
                        "warehouse_role_without_warehouse",
                        roles=inferred_roles,
                    )
                return cls(
                    roles=inferred_roles,
                    warehouse_ids=frozenset({int(warehouse_id)}),
                    is_valid=True,
                    source="legacy_role_and_binding",
                )
            return cls._denied("unknown_legacy_role", roles=inferred_roles)

        if owner_id and not warehouse_id:
            return cls(
                owner_ids=frozenset({int(owner_id)}),
                is_valid=True,
                source="legacy_owner_binding",
            )
        if warehouse_id and not owner_id:
            return cls(
                warehouse_ids=frozenset({int(warehouse_id)}),
                is_valid=True,
                source="legacy_warehouse_binding",
            )
        if owner_id and warehouse_id:
            return cls._denied("ambiguous_legacy_owner_and_warehouse")
        return cls._denied("unbound_user")

    @classmethod
    def _denied(
        cls,
        reason: str,
        *,
        roles: frozenset[str] | None = None,
    ) -> "AccessScope":
        return cls(
            roles=roles or frozenset(),
            is_valid=False,
            source="denied",
            denial_reason=reason,
        )

    def filter_queryset(
        self,
        qs: QuerySet,
        owner_field: str | None = "owner_id",
        warehouse_field: str | None = "warehouse_id",
    ) -> QuerySet:
        """Apply this boundary, returning ``qs.none()`` when it cannot be proven."""

        if not self.is_valid:
            return qs.none()
        if self.is_global:
            return qs
        if self.owner_ids:
            if not owner_field:
                return qs.none()
            return qs.filter(**{f"{owner_field}__in": self.owner_ids})
        if self.warehouse_ids:
            if not warehouse_field:
                return qs.none()
            return qs.filter(**{f"{warehouse_field}__in": self.warehouse_ids})
        return qs.none()

    def allows(self, owner_id=None, warehouse_id=None) -> bool:
        """Return whether an object identified by its tenant keys is in scope."""

        if not self.is_valid:
            return False
        if self.is_global:
            return True
        if self.owner_ids:
            return self._id_in(owner_id, self.owner_ids)
        if self.warehouse_ids:
            return self._id_in(warehouse_id, self.warehouse_ids)
        return False

    def as_dict(self) -> dict[str, object]:
        """Return a stable, JSON-serializable representation for profile APIs."""

        return {
            "roles": sorted(self.roles),
            "owner_ids": sorted(self.owner_ids),
            "warehouse_ids": sorted(self.warehouse_ids),
            "is_global": self.is_global,
            "source": self.source,
        }

    @staticmethod
    def _id_in(value, allowed_ids: frozenset[int]) -> bool:
        if value is None:
            return False
        try:
            return int(value) in allowed_ids
        except (TypeError, ValueError):
            return False


def scope_queryset_for_user(
    qs: QuerySet,
    user,
    *,
    owner_field: str | None = "owner_id",
    warehouse_field: str | None = "warehouse_id",
) -> QuerySet:
    """Compatibility wrapper for callers that only need a scoped queryset."""

    return AccessScope.for_user(user).filter_queryset(
        qs,
        owner_field=owner_field,
        warehouse_field=warehouse_field,
    )

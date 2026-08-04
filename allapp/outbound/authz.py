"""Authorization and tenant-scoping helpers for outbound APIs.

Legacy endpoints can still be observed in ``shadow`` mode during an explicit
rollback, but production defaults to fail-closed enforcement.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import BigIntegerField, Exists, OuterRef, Q, QuerySet
from django.db.models.functions import Cast
from rest_framework.exceptions import PermissionDenied

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope

logger = logging.getLogger(__name__)

ASSISTED_PERMISSION = "outbound.process_warehouse_assisted_outbound"
VIEW_ALL_PERMISSION = "outbound.view_all_outbound_orders"
TASK_OPERATOR_PERMISSION = "tasking.claim_task_as_wh_operator"
TASK_MANAGER_PERMISSION = "tasking.taskconfirm_as_wh_manager"
ORDER_VIEW_PERMISSION = "outbound.view_outboundorder"
TASK_VIEW_PERMISSION = "tasking.view_wmstask"


def legacy_authz_mode() -> str:
    value = str(getattr(settings, "OUTBOUND_LEGACY_AUTHZ_MODE", "enforce") or "enforce")
    return (
        value.strip().lower()
        if value.strip().lower() in {"shadow", "enforce"}
        else "enforce"
    )


def is_assisted_operator(user) -> bool:
    """Return whether ``user`` satisfies the complete assisted-operator contract."""

    if not (
        user
        and getattr(user, "is_authenticated", False)
        and user.has_perm(ASSISTED_PERMISSION)
        and user.has_perm(TASK_OPERATOR_PERMISSION)
    ):
        return False
    scope = AccessScope.for_user(user)
    return bool(
        scope.is_valid
        and not scope.is_global
        and UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles
        and len(scope.warehouse_ids) == 1
    )


def require_assisted_operator(user) -> None:
    if not is_assisted_operator(user):
        raise PermissionDenied(
            "代办出库账号必须具有单一有效仓库操作员范围，"
            "并同时具有代办出库和仓库操作权限。"
        )


def assisted_task_queryset(qs: QuerySet, *, warehouse_id=None) -> QuerySet:
    """Return assisted outbound tasks without comparing text collations.

    ``WmsTask.source_pk`` is a generic text field.  Casting outbound integer
    primary keys to text makes MySQL use the connection collation, which may
    differ from the field collation and fail with error 1267.  Cast the task
    source key to an integer instead so the comparison remains collation-free.
    """

    from .models import OutboundOrder

    orders = OutboundOrder.objects.filter(processing_mode="WAREHOUSE_ASSISTED")
    if warehouse_id is not None:
        orders = orders.filter(warehouse_id=warehouse_id)
    return qs.annotate(
        _assisted_outbound_order_id=Cast(
            "source_pk",
            output_field=BigIntegerField(),
        )
    ).filter(
        source_model__in=("outboundorder", "OutboundOrder"),
        _assisted_outbound_order_id__in=orders.values("pk"),
    )


def strict_order_queryset(qs: QuerySet, user, *, scope=None) -> QuerySet:
    """Apply the planned fail-closed read scope to an order queryset."""

    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    scope = scope or AccessScope.for_user(user)
    if not scope.is_valid:
        return qs.none()
    if scope.is_global:
        return qs

    can_read_orders = any(
        user.has_perm(permission)
        for permission in (
            ORDER_VIEW_PERMISSION,
            VIEW_ALL_PERMISSION,
            "outbound.submit_outbound_as_owner_buyers",
            "outbound.approve_outbound_as_owner_manager",
            "outbound.approve_outbound_as_wh_manager",
        )
    )
    if can_read_orders:
        scoped = scope.filter_queryset(
            qs,
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
        if UserRoleScope.Role.OWNER_SALESPERSON in scope.roles:
            scoped = scoped.filter(created_by_id=user.pk)
        return scoped

    if is_assisted_operator(user):
        return scope.filter_queryset(
            qs.filter(processing_mode="WAREHOUSE_ASSISTED"),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
    return qs.none()


def strict_pick_queryset(qs: QuerySet, user) -> QuerySet:
    """Apply order-equivalent fail-closed scope to PICK task querysets."""

    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        return qs.none()
    if scope.is_global:
        return qs
    if not any(
        user.has_perm(permission)
        for permission in (
            TASK_VIEW_PERMISSION,
            TASK_OPERATOR_PERMISSION,
            TASK_MANAGER_PERMISSION,
        )
    ):
        return qs.none()
    scoped = scope.filter_queryset(
        qs,
        owner_field="owner_id",
        warehouse_field="warehouse_id",
    )
    if UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles:
        from allapp.tasking.models import TaskAssignment, WmsTask

        active_assignment = TaskAssignment.objects.filter(
            task_id=OuterRef("pk"), finished_at__isnull=True
        )
        scoped = scoped.annotate(
            _has_active_assignment=Exists(active_assignment)
        ).filter(
            Q(assignments__assignee_id=user.pk)
            | Q(created_by_id=user.pk)
            | Q(picked_by_id=user.pk)
            | Q(posted_by_id=user.pk)
            | Q(status=WmsTask.Status.RELEASED, _has_active_assignment=False)
        )
    return scoped.distinct()


def _shadow_has_denied_rows(base_qs: QuerySet, scoped_qs: QuerySet) -> bool:
    try:
        return base_qs.exclude(pk__in=scoped_qs.values("pk")).exists()
    except Exception:  # pragma: no cover - logging must never break a legacy request
        return True


def apply_legacy_scope(
    *, base_qs: QuerySet, scoped_qs: QuerySet, user, endpoint: str
) -> QuerySet:
    """Always enforce scope; ``shadow`` now only preserves rollout telemetry."""

    if legacy_authz_mode() == "shadow" and _shadow_has_denied_rows(base_qs, scoped_qs):
        logger.warning(
            "outbound.authz.legacy_shadow_enforced user_id=%s endpoint=%s reason=scope",
            getattr(user, "pk", None),
            endpoint,
        )
    return scoped_qs


def require_legacy_action(*, user, allowed: bool, endpoint: str, reason: str) -> None:
    """Gate a legacy write action; ``shadow`` is telemetry-only and never bypasses."""

    if allowed:
        return
    if legacy_authz_mode() == "shadow":
        logger.warning(
            "outbound.authz.legacy_shadow_action_denied user_id=%s endpoint=%s reason=%s",
            getattr(user, "pk", None),
            endpoint,
            reason,
        )
    raise PermissionDenied(reason)


def can_use_task_actions(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or user.has_perm(TASK_OPERATOR_PERMISSION)
        )
    )


def can_review_task_actions(user) -> bool:
    """Allow a warehouse manager to perform the independent REVIEW step."""

    if can_use_task_actions(user):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    scope = AccessScope.for_user(user)
    return bool(
        scope.is_valid
        and UserRoleScope.Role.WAREHOUSE_MANAGER in scope.roles
        and user.has_perm(TASK_MANAGER_PERMISSION)
    )


def is_assisted_source(task) -> bool:
    return (getattr(task, "source_model", "") or "") in {
        "outboundorder",
        "OutboundOrder",
    }


def get_assisted_order_for_task(task, *, for_update: bool = False):
    """Resolve a task's assisted order without accepting a client-provided flag."""

    cache_name = "_outbound_assisted_order_cache"
    if not for_update and hasattr(task, cache_name):
        return getattr(task, cache_name)
    if not is_assisted_source(task):
        if not for_update:
            setattr(task, cache_name, None)
        return None
    from .models import OutboundOrder

    qs = OutboundOrder.objects
    if for_update:
        qs = qs.select_for_update()
    try:
        order = qs.filter(
            pk=int(task.source_pk),
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            processing_mode="WAREHOUSE_ASSISTED",
        ).first()
        if not for_update:
            setattr(task, cache_name, order)
        return order
    except (TypeError, ValueError):
        return None


def can_self_review_assisted_task(user, task) -> bool:
    if not is_assisted_operator(user):
        return False
    scope = AccessScope.for_user(user)
    if not scope.allows(warehouse_id=getattr(task, "warehouse_id", None)):
        return False
    return get_assisted_order_for_task(task) is not None

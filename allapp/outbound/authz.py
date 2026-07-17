"""Authorization and tenant-scoping helpers for outbound APIs.

Legacy endpoints can be rolled out in ``shadow`` mode.  The assisted-outbound
API never uses that compatibility switch and is always fail closed.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import CharField, QuerySet
from django.db.models.functions import Cast
from rest_framework.exceptions import PermissionDenied

logger = logging.getLogger(__name__)

ASSISTED_PERMISSION = "outbound.process_warehouse_assisted_outbound"
VIEW_ALL_PERMISSION = "outbound.view_all_outbound_orders"
TASK_OPERATOR_PERMISSION = "tasking.claim_task_as_wh_operator"
ORDER_VIEW_PERMISSION = "outbound.view_outboundorder"
TASK_VIEW_PERMISSION = "tasking.view_wmstask"


def legacy_authz_mode() -> str:
    value = str(getattr(settings, "OUTBOUND_LEGACY_AUTHZ_MODE", "shadow") or "shadow")
    return value.strip().lower() if value.strip().lower() in {"shadow", "enforce"} else "shadow"


def is_assisted_operator(user) -> bool:
    """Return whether ``user`` satisfies the complete assisted-operator contract."""

    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "owner_id", None) is None
        and getattr(user, "warehouse_id", None)
        and user.has_perm(ASSISTED_PERMISSION)
        and user.has_perm(TASK_OPERATOR_PERMISSION)
    )


def require_assisted_operator(user) -> None:
    if not is_assisted_operator(user):
        raise PermissionDenied(
            "代办出库账号必须未绑定货主、绑定仓库，并同时具有代办出库和仓库操作权限。"
        )


def assisted_order_source_ids(*, warehouse_id=None):
    """A text-typed subquery suitable for matching ``WmsTask.source_pk``."""

    from .models import OutboundOrder

    orders = OutboundOrder.objects.filter(processing_mode="WAREHOUSE_ASSISTED")
    if warehouse_id is not None:
        orders = orders.filter(warehouse_id=warehouse_id)
    return (
        orders
        .annotate(source_pk_text=Cast("id", output_field=CharField()))
        .values("source_pk_text")
    )


def strict_order_queryset(qs: QuerySet, user) -> QuerySet:
    """Apply the planned fail-closed read scope to an order queryset."""

    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    if getattr(user, "is_superuser", False) or user.has_perm(VIEW_ALL_PERMISSION):
        return qs

    owner_id = getattr(user, "owner_id", None)
    warehouse_id = getattr(user, "warehouse_id", None)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
        return qs.filter(warehouse_id=warehouse_id) if warehouse_id else qs

    if warehouse_id and user.has_perm(ORDER_VIEW_PERMISSION):
        return qs.filter(warehouse_id=warehouse_id)

    if warehouse_id and is_assisted_operator(user):
        return qs.filter(
            warehouse_id=warehouse_id,
            processing_mode="WAREHOUSE_ASSISTED",
        )

    return qs.none()


def strict_pick_queryset(qs: QuerySet, user) -> QuerySet:
    """Apply order-equivalent fail-closed scope to PICK task querysets."""

    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    if getattr(user, "is_superuser", False):
        return qs

    owner_id = getattr(user, "owner_id", None)
    warehouse_id = getattr(user, "warehouse_id", None)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
        return qs.filter(warehouse_id=warehouse_id) if warehouse_id else qs

    if warehouse_id and user.has_perm(TASK_VIEW_PERMISSION):
        return qs.filter(warehouse_id=warehouse_id)

    if warehouse_id and is_assisted_operator(user):
        source_ids = assisted_order_source_ids(warehouse_id=warehouse_id)
        return qs.filter(
            warehouse_id=warehouse_id,
            source_model__in=("outboundorder", "OutboundOrder"),
            source_pk__in=source_ids,
        )

    return qs.none()


def _shadow_has_denied_rows(base_qs: QuerySet, scoped_qs: QuerySet) -> bool:
    try:
        return base_qs.exclude(pk__in=scoped_qs.values("pk")).exists()
    except Exception:  # pragma: no cover - logging must never break a legacy request
        return True


def apply_legacy_scope(*, base_qs: QuerySet, scoped_qs: QuerySet, user, endpoint: str) -> QuerySet:
    """Enforce a scope, or log the would-deny result in compatibility mode."""

    if legacy_authz_mode() == "enforce":
        return scoped_qs
    if _shadow_has_denied_rows(base_qs, scoped_qs):
        logger.warning(
            "outbound.authz.would_deny user_id=%s endpoint=%s reason=scope",
            getattr(user, "pk", None),
            endpoint,
        )
    return base_qs


def require_legacy_action(*, user, allowed: bool, endpoint: str, reason: str) -> None:
    """Gate a legacy write action while supporting a non-blocking shadow rollout."""

    if allowed:
        return
    if legacy_authz_mode() == "enforce":
        raise PermissionDenied(reason)
    logger.warning(
        "outbound.authz.would_deny user_id=%s endpoint=%s reason=%s",
        getattr(user, "pk", None),
        endpoint,
        reason,
    )


def can_use_task_actions(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (getattr(user, "is_superuser", False) or user.has_perm(TASK_OPERATOR_PERMISSION))
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
    if str(getattr(user, "warehouse_id", "")) != str(getattr(task, "warehouse_id", "")):
        return False
    return get_assisted_order_for_task(task) is not None

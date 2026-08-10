"""Authorization helpers shared by all no-order receiving entry points."""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope
from allapp.baseinfo.models import Owner
from allapp.baseinfo.owner_warehouse_access import owner_can_use_warehouse


def resolve_no_order_receive_scope(user, owner_id, warehouse_id=None):
    """Resolve and authorize one owner/warehouse pair for no-order receiving."""

    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"owner_id": "必须提供有效的货主 ID"}) from exc

    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        raise PermissionDenied("账号没有有效的数据范围。")

    resolved_warehouse_id = warehouse_id or scope.single_warehouse_id
    try:
        resolved_warehouse_id = int(resolved_warehouse_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "必须提供 warehouse_id；仅单一仓库范围账号可自动确定仓库"
        ) from exc

    if not scope.allows(
        owner_id=owner_id,
        warehouse_id=resolved_warehouse_id,
    ):
        raise PermissionDenied("无权处理指定货主或仓库。")

    owner = Owner.objects.filter(pk=owner_id, is_active=True).first()
    if owner is None:
        raise PermissionDenied("货主不存在或已停用。")
    if not owner_can_use_warehouse(owner_id, resolved_warehouse_id):
        raise PermissionDenied("该货主未授权当前仓库。")

    return owner, resolved_warehouse_id

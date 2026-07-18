from rest_framework.permissions import BasePermission
from django.db.models import Exists, OuterRef, Q

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.tasking.models import TaskAssignment, WmsTask


RECEIVE_TASK_VIEW_PERMISSIONS = (
    "tasking.view_wmstask",
    "tasking.claim_task_as_wh_operator",
    "inbound.view_pdanoorderreceive",
    "inbound.view_inboundorder",
    "accounts.receive_without_order",
)


def can_view_receive_tasks(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(user, "is_superuser", False)
        or any(
            user.has_perm(permission) for permission in RECEIVE_TASK_VIEW_PERMISSIONS
        )
    )


def scoped_receive_tasks(user, queryset=None):
    queryset = queryset if queryset is not None else WmsTask.objects.all()
    if not can_view_receive_tasks(user):
        return queryset.none()
    scope = AccessScope.for_user(user)
    scoped = scope.filter_queryset(
        queryset,
        owner_field="owner_id",
        warehouse_field="warehouse_id",
    )
    if UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles:
        active_assignment = TaskAssignment.objects.filter(
            task_id=OuterRef("pk"),
            finished_at__isnull=True,
        )
        scoped = (
            scoped.annotate(_has_active_assignment=Exists(active_assignment))
            .filter(
                Q(assignments__assignee_id=user.pk, assignments__finished_at__isnull=True)
                | Q(created_by_id=user.pk)
                | Q(posted_by_id=user.pk)
                | Q(
                    status=WmsTask.Status.RELEASED,
                    _has_active_assignment=False,
                )
            )
            .distinct()
        )
    return scoped


def can_operate_inbound_tasks(user):
    """Whether the account is a warehouse operator with task capability."""

    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    scope = AccessScope.for_user(user)
    return bool(
        scope.is_valid
        and UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles
        and user.has_perm("tasking.claim_task_as_wh_operator")
    )


class CanReceiveWithoutOrder(BasePermission):
    message = "没有无订单收货权限"

    def has_permission(self, request, view):
        user = request.user
        return bool(
            getattr(user, "is_authenticated", False)
            and (
                getattr(user, "is_superuser", False)
                or user.has_perm("accounts.receive_without_order")
            )
        )

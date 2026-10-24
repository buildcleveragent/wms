"""Read-only PDA reminders using the same queues as their destination pages."""

import hashlib
import json

from django.conf import settings
from django.http import QueryDict
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.inbound.views import inbound_task_queryset
from allapp.outbound.authz import strict_pick_queryset
from allapp.outbound.models import OutboundOrder
from allapp.outbound.physical_review import pending_reviews, review_pick_queryset
from allapp.outbound.views import filter_pick_queue
from allapp.outbound.warehouse_review import scoped_review_orders
from allapp.outbound.warehouse_review_api import review_stage_orders
from allapp.tasking.models import WmsTask
from allapp.tasking.views_replenishment import (
    _is_manager,
    _is_operator,
    replenishment_task_queryset,
)


class HomeTodosView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        scope = AccessScope.for_user(user)
        if not scope.is_valid or not (
            scope.is_global
            or scope.roles.intersection(
                {UserRoleScope.Role.WAREHOUSE_MANAGER, UserRoleScope.Role.WAREHOUSE_OPERATOR}
            )
        ):
            raise PermissionDenied("需要有效的仓库角色和仓库范围。")
        items = []

        def add(key, query):
            try:
                count = query().order_by().values("pk").distinct().count()
            except PermissionDenied:
                return
            items.append({"key": key, "count": count})

        add(
            "outbound_review",
            lambda: review_stage_orders(
                scoped_review_orders(OutboundOrder.objects.all(), user), "pending"
            ),
        )
        for kind in ("RECEIVE", "PUTAWAY"):
            add(
                kind.lower(),
                lambda kind=kind: inbound_task_queryset(user, QueryDict(f"task_type={kind}")),
            )
        if scope.is_global or any(
            user.has_perm(p)
            for p in (
                "tasking.view_wmstask",
                "tasking.claim_task_as_wh_operator",
                "tasking.taskconfirm_as_wh_manager",
            )
        ):
            add(
                "pick",
                lambda: filter_pick_queue(
                    strict_pick_queryset(WmsTask.objects.filter(task_type="PICK"), user),
                    QueryDict(),
                ),
            )
        add(
            "review",
            lambda: pending_reviews(review_pick_queryset(WmsTask.objects.all(), user), user),
        )
        if getattr(settings, "REPLENISHMENT_PDA_ENABLED", False) and (
            _is_manager(user, scope) or _is_operator(user, scope)
        ):
            add(
                "replenish",
                lambda: replenishment_task_queryset(user, QueryDict("pending=1")),
            )
        identity = [
            user.pk,
            scope.is_global,
            sorted(scope.roles),
            sorted(scope.owner_ids),
            sorted(scope.warehouse_ids),
            sorted(user.get_all_permissions()),
            [item["key"] for item in items],
        ]
        response = Response(
            {
                "scope": hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                "items": items,
            }
        )
        response["Cache-Control"] = "no-store"
        return response

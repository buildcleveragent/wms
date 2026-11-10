"""Boss-only read APIs for live activity and operational drilldowns."""

from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.tasking.models import WmsTask

from .boss_activity import ActivityScope, activity_page, date_bounds, local_today
from .boss_activity_detail import activity_detail, task_row
from .boss_contract import build_meta, parse_boss_date_range
from .views_boss import BossScopedApiMixin


def positive_int(params, name, default=None, maximum=None):
    raw = params.get(name, default)
    if raw in (None, ""):
        return default
    try:
        number = int(raw)
        if number <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError({name: "必须为正整数。"}) from None
    return min(number, maximum) if maximum else number


class ActivityApiMixin(BossScopedApiMixin):
    def context(self, request):
        owner = positive_int(request.query_params, "owner")
        warehouse = positive_int(request.query_params, "warehouse")
        self._validate_scope(request, owner_id=owner, warehouse_id=warehouse)
        today = str(local_today())
        try:
            start, end = parse_boss_date_range(
                request.query_params.get("date_from") or today,
                request.query_params.get("date_to") or today,
            )
        except ValueError as exc:
            raise ValidationError({"date": str(exc)}) from None
        scope = self._scope_payload(
            owner_id=owner, warehouse_id=warehouse, date_from=start, date_to=end
        )
        return ActivityScope(request.user, owner, warehouse), scope

    def respond(self, scope, data):
        meta = build_meta(scope=scope)
        meta.update(data_as_of=meta["generated_at"], source="LIVE_BUSINESS_RECORDS")
        return Response({"scope": scope, "meta": meta, **data})


class BossActivitiesApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        priority = request.query_params.get("priority", "all")
        if priority not in {"all", "important"}:
            raise ValidationError({"priority": "必须为 all 或 important。"})
        data = activity_page(
            access,
            date_from=scope["date_from"],
            date_to=scope["date_to"],
            category=request.query_params.get("category", ""),
            actor=request.query_params.get("actor", ""),
            search=request.query_params.get("search", "").strip(),
            important=priority == "important",
            limit=positive_int(request.query_params, "limit", 20, 100),
            cursor=request.query_params.get("cursor", ""),
        )
        return self.respond(scope, data)


class BossActivityDetailApi(ActivityApiMixin, APIView):
    def get(self, request, kind, pk):
        access, scope = self.context(request)
        return self.respond(scope, activity_detail(access, kind, pk, request.query_params))


class BossTasksApi(ActivityApiMixin, APIView):
    def get(self, request):
        access, scope = self.context(request)
        qs = access.apply(WmsTask.objects.select_related("warehouse", "owner"))
        mode = request.query_params.get("mode", "created")
        start, end = date_bounds(scope["date_from"], scope["date_to"])
        if mode == "created":
            qs = qs.filter(created_at__gte=start, created_at__lt=end)
        elif mode == "completed":
            qs = qs.filter(status="COMPLETED", finished_at__gte=start, finished_at__lt=end)
        elif mode == "backlog":
            qs = qs.exclude(status__in=["COMPLETED", "CANCELLED"])
        else:
            raise ValidationError({"mode": "未知任务查询方式。"})
        task_type = request.query_params.get("task_type", "")
        if task_type:
            if task_type not in WmsTask.TaskType.values:
                raise ValidationError({"task_type": "未知任务类型。"})
            qs = qs.filter(task_type=task_type)
        actor = positive_int(request.query_params, "actor")
        if actor:
            qs = qs.filter(assignments__assignee_id=actor).distinct()
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(Q(task_no__icontains=search) | Q(ref_no__icontains=search))
        page = positive_int(request.query_params, "page", 1)
        size = positive_int(request.query_params, "page_size", 20, 100)
        return self.respond(
            scope,
            {
                "count": qs.count(),
                "page": page,
                "page_size": size,
                "results": [
                    task_row(row)
                    for row in qs.order_by("-created_at", "-id")[(page - 1) * size : page * size]
                ],
                "date_semantics": "CURRENT_BACKLOG" if mode == "backlog" else mode.upper(),
            },
        )


class BossTaskDetailApi(BossActivityDetailApi):
    def get(self, request, pk):
        return super().get(request, "task_detail", pk)

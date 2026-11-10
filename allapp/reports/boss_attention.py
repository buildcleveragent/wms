"""Current attention queue and idempotent deadline checks (no push delivery)."""

from django.db import connection, transaction
from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTask, WmsTaskLine

from .boss_activity import actor_name
from .models import AlertCase, AlertCaseHistory, BossCheckState

RULES = ("DEADLINE_INBOUND", "DEADLINE_OUTBOUND", "DEADLINE_TASK", "BILLING_HEALTH")
CLOSED = ["RESOLVED", "CLOSED"]


def order_progress_lines(line_model):
    inbound = line_model is InboundOrderLine
    actual = WmsTaskLine.objects.filter(
        src_model__in=[line_model.__name__, f"{line_model._meta.app_label}.{line_model.__name__}"],
        src_id=OuterRef("pk"),
        task__owner_id=OuterRef("order__owner_id"),
        task__warehouse_id=OuterRef("order__warehouse_id"),
        task__task_type="RECEIVE" if inbound else "DISPATCH",
        task__status="COMPLETED",
    )
    if inbound:
        actual = actual.filter(task__posting_status="POSTED")
    actual = actual.order_by().values("src_id").annotate(total=Sum("qty_done")).values("total")[:1]
    return line_model.objects.filter(is_deleted=False).annotate(
        done=Coalesce(
            Subquery(actual), Value(0), output_field=line_model._meta.get_field("base_qty")
        ),
    )


def overdue_sources(rule, now):
    if rule == "DEADLINE_TASK":
        qs = WmsTask.objects.filter(
            is_deleted=False, planned_end__lt=now, status__in=["READY", "RELEASED", "IN_PROGRESS"]
        )
        for row in qs.iterator():
            yield row, row.planned_end, row.task_no, "任务超过计划完成时间", {
                "task_type": row.task_type
            }
        return
    inbound = rule == "DEADLINE_INBOUND"
    model, line_model = (
        (InboundOrder, InboundOrderLine) if inbound else (OutboundOrder, OutboundOrderLine)
    )
    deadline = "eta" if inbound else "etd"
    pending = order_progress_lines(line_model).filter(
        order_id=OuterRef("pk"), base_qty__gt=F("done")
    )
    qs = model.objects.filter(
        **{f"{deadline}__lt": now},
        submit_status="SUBMITTED",
        is_closed=False,
        is_deleted=False,
    ).exclude(approval_status__in=["CANCELLED", "OWNER_REJECTED", "WHS_REJECTED"])
    if not inbound:
        qs = qs.exclude(pos_sale_order__isnull=False)
    qs = qs.annotate(has_pending=Exists(pending)).filter(has_pending=True)
    for row in qs.iterator():
        yield row, getattr(row, deadline), row.order_no, (
            "入库超过预计到货时间" if inbound else "出库超过预计发货时间"
        ), {
            "progress": "存在尚未完成的商品行，按实际收货／发运记录核对",
        }


def _sync_rule(rule, now):
    # Roll back the whole rule if any source fails; never auto-resolve an incomplete scan.
    with transaction.atomic():
        seen = []
        for row, deadline, number, title, details in overdue_sources(rule, now):
            key = f"{rule}:{row.pk}"
            seen.append(key)
            defaults = dict(
                alert_type=rule,
                source_type=type(row).__name__,
                source_id=str(row.pk),
                owner_id=row.owner_id,
                warehouse_id=row.warehouse_id,
                severity="HIGH",
                title=title,
                detail={**details, "record_no": number, "business_due_at": deadline.isoformat()},
                first_seen_at=deadline,  # The known deadline is when this timeout began.
                last_seen_at=now,
            )
            case, created = AlertCase.objects.select_for_update().get_or_create(
                dedup_key=key, defaults=defaults
            )
            if created:
                AlertCaseHistory.objects.create(case=case, action="DISCOVER", to_status=case.status)
            else:
                before = case.status
                case.detail = defaults["detail"]
                case.owner_id = row.owner_id
                case.warehouse_id = row.warehouse_id
                case.last_seen_at = now
                if before in CLOSED:
                    case.status = "OPEN"
                    case.resolved_at = case.closed_at = None
                    AlertCaseHistory.objects.create(
                        case=case, action="REOPEN", from_status=before, to_status="OPEN"
                    )
                case.save(
                    update_fields=[
                        "detail",
                        "owner",
                        "warehouse",
                        "last_seen_at",
                        "status",
                        "resolved_at",
                        "closed_at",
                        "updated_at",
                    ]
                )
        for case in (
            AlertCase.objects.select_for_update()
            .filter(alert_type=rule)
            .exclude(dedup_key__in=seen)
            .exclude(status__in=CLOSED)
        ):
            before = case.status
            case.status, case.resolved_at = "RESOLVED", now
            case.save(update_fields=["status", "resolved_at", "updated_at"])
            AlertCaseHistory.objects.create(
                case=case, action="AUTO_RESOLVE", from_status=before, to_status="RESOLVED"
            )
        BossCheckState.objects.update_or_create(
            key=rule, defaults={"checked_at": now, "attempted_at": now, "failed": False}
        )
    return len(seen)


def check_deadlines(*, lock_held=False):
    """Use a connection-owned MySQL lock, including across different app servers."""
    lock = "boss-deadlines:" + connection.settings_dict["NAME"]
    acquired = lock_held
    if not lock_held:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", [lock])
            acquired = cursor.fetchone()[0] == 1
    if not acquired:
        return {"busy": True}
    try:
        result = {}
        for rule in RULES:
            now = timezone.now()
            try:
                if rule == "BILLING_HEALTH":
                    from .services_alert_cases import sync_alert_cases

                    with transaction.atomic():
                        result[rule] = sync_alert_cases()
                        BossCheckState.objects.update_or_create(
                            key=rule,
                            defaults={"checked_at": now, "attempted_at": now, "failed": False},
                        )
                else:
                    result[rule] = _sync_rule(rule, now)
            except Exception:
                BossCheckState.objects.update_or_create(
                    key=rule, defaults={"attempted_at": now, "failed": True}
                )
                raise
        return result
    finally:
        if not lock_held:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", [lock])


def check_health(automation=None):
    from .boss_automation import automation_status

    automation = automation or automation_status()
    states = {row.key: row for row in BossCheckState.objects.filter(key__in=RULES)}
    return [
        {
            "rule": key,
            "checked_at": getattr(states.get(key), "checked_at", None),
            "stale": automation["enabled"]
            and (
                key not in states
                or states[key].failed
                or not states[key].checked_at
                or automation["state"] in {"waiting", "failed", "delayed", "worker_unavailable"}
            ),
            "auto_enabled": automation["enabled"],
            "interval_minutes": automation["interval_minutes"],
            "state": automation["state"],
            "next_check_at": automation["next_check_at"],
        }
        for key in RULES
    ]


def case_payload(case):
    now = timezone.now()
    from django.utils.dateparse import parse_datetime

    business_due = parse_datetime(case.detail.get("business_due_at", ""))
    latest = case.history.order_by("-created_at", "-id").select_related("created_by").first()
    operators = []
    if case.source_type == "WmsTask":
        task = WmsTask.objects.filter(
            pk=case.source_id, warehouse_id=case.warehouse_id, owner_id=case.owner_id
        ).first()
        if task:
            operators = [
                {"id": a.assignee_id, "name": actor_name(a.assignee)}
                for a in task.assignments.filter(finished_at__isnull=True).select_related(
                    "assignee"
                )
            ]
    return {
        "id": case.pk,
        "source_target": (
            {
                "kind": {
                    "WmsTask": "task_detail",
                    "OutboundOrder": "outbound_document",
                    "InboundOrder": "inbound_document",
                }[case.source_type],
                "id": case.source_id,
            }
            if case.source_type in {"WmsTask", "OutboundOrder", "InboundOrder"}
            else None
        ),
        "title": case.title,
        "severity": case.severity,
        "status": case.status,
        "record_no": case.detail.get("record_no", case.source_id),
        "warehouse_name": case.warehouse.name,
        "owner_name": case.owner.name if case.owner else "",
        "first_seen_at": case.first_seen_at,
        "waiting_seconds": (
            max(0, int((now - case.first_seen_at).total_seconds())) if case.first_seen_at else None
        ),
        "business_due_at": business_due,
        "due_at": case.due_at,
        "overdue_seconds": (
            max(0, int((now - (business_due or case.due_at)).total_seconds()))
            if business_due or case.due_at
            else None
        ),
        "handling_overdue": bool(case.due_at and case.due_at < now and case.status not in CLOSED),
        "assignee": case.assignee_id,
        "assignee_name": actor_name(case.assignee) if case.assignee_id else "未分派",
        "operators": operators,
        "detail": case.detail,
        "latest_note": latest.note if latest else "",
        "latest_action": latest.action if latest else "",
        "latest_at": latest.created_at if latest else None,
    }


def attention_page(scope, page=1, size=20):
    now = timezone.now()
    qs = scope.apply(AlertCase.objects.select_related("owner", "warehouse", "assignee")).exclude(
        status__in=CLOSED
    )
    qs = (
        qs.filter(Q(severity__in=["HIGH", "CRITICAL"]) | Q(due_at__lt=now))
        .annotate(
            severity_order=Case(
                When(severity="CRITICAL", then=Value(0)),
                When(severity="HIGH", then=Value(1)),
                When(severity="WARNING", then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            ),
            overdue_order=Case(
                When(due_at__lt=now, then=Value(0)), default=Value(1), output_field=IntegerField()
            ),
        )
        .order_by("severity_order", "overdue_order", "first_seen_at", "id")
    )
    from .boss_automation import automation_status

    automation = automation_status()
    return {
        "count": qs.count(),
        "page": page,
        "page_size": size,
        "results": [case_payload(c) for c in qs[(page - 1) * size : page * size]],
        "checks": check_health(automation),
        "automation": automation,
    }

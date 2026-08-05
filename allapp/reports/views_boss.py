from __future__ import annotations

import datetime

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.billing.enums import AccrualStatus, BillStatus, PricingStatus, SourceQuality
from allapp.billing.models import (
    Bill,
    BillingAccrual,
    BillingEvent,
    BillingJobRun,
    BillingMetricDaily,
)
from allapp.inventory.models import (
    InventoryDetail,
    InventorySnapshotDaily,
    ReviewDifference,
)
from allapp.locations.models import Warehouse
from allapp.tasking.models import WmsTask

from .boss_contract import build_meta, warning
from .services_boss import (
    build_boss_alert_payload,
    build_boss_context_payload,
    build_boss_home_payload,
    build_boss_inventory_payload,
)


class CanViewBossDashboard(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        scope = AccessScope.for_user(user)
        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or (
                    scope.is_valid
                    and UserRoleScope.Role.WAREHOUSE_BOSS in scope.roles
                    and bool(scope.warehouse_ids)
                    and user.has_perm("reports.view_boss_dashboard")
                )
            )
        )


class BossScopedApiMixin:
    permission_classes = [CanViewBossDashboard]

    def _parse_int_param(self, request, name: str):
        raw = (request.query_params.get(name) or "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValueError(f"{name} must be an integer id.")
        return int(raw)

    def _validate_scope(self, request, *, owner_id=None, warehouse_id=None):
        scope = AccessScope.for_user(request.user)
        if scope.owner_ids and owner_id and not scope.allows(owner_id=owner_id):
            exc = PermissionDenied("No access to other owners in boss dashboard.")
            exc.detail = {
                "code": "SCOPE_FORBIDDEN",
                "detail": "No access to other owners in boss dashboard.",
            }
            raise exc
        if (
            scope.warehouse_ids
            and warehouse_id
            and not scope.allows(warehouse_id=warehouse_id)
        ):
            exc = PermissionDenied("No access to other warehouses in boss dashboard.")
            exc.detail = {
                "code": "SCOPE_FORBIDDEN",
                "detail": "No access to other warehouses in boss dashboard.",
            }
            raise exc

    def _parse_date_range(self, request):
        now = timezone.now()
        today = timezone.localtime(now).date() if timezone.is_aware(now) else now.date()
        raw_from = (request.query_params.get("date_from") or "").strip()
        raw_to = (request.query_params.get("date_to") or "").strip()
        date_to = parse_date(raw_to) if raw_to else today
        date_from = parse_date(raw_from) if raw_from else date_to.replace(day=1)
        if (raw_from and date_from is None) or (raw_to and date_to is None):
            raise ValueError("date_from and date_to must be YYYY-MM-DD.")
        if date_from > date_to:
            raise ValueError("date_from cannot be after date_to.")
        if date_to > today:
            raise ValueError("date_to cannot be after today.")
        if (date_to - date_from).days > 366:
            raise ValueError("date range cannot exceed 367 days.")
        return date_from, date_to

    def _date_cutoff(self, date_to):
        current = timezone.now()
        current_date = (
            timezone.localtime(current).date()
            if timezone.is_aware(current)
            else current.date()
        )
        if date_to == current_date:
            return current
        cutoff = datetime.datetime.combine(
            date_to + datetime.timedelta(days=1), datetime.time.min
        )
        if timezone.is_aware(current):
            cutoff = timezone.make_aware(cutoff, timezone.get_current_timezone())
        return cutoff

    def _scope_payload(self, *, owner_id, warehouse_id, date_from, date_to):
        return {
            "mode": "WAREHOUSE" if warehouse_id else "ALL_AUTHORIZED",
            "warehouse": warehouse_id,
            "warehouse_name": (
                Warehouse.objects.filter(pk=warehouse_id)
                .values_list("name", flat=True)
                .first()
                if warehouse_id
                else "全部授权仓库"
            ),
            "owner": owner_id,
            "owner_name": (
                Owner.objects.filter(pk=owner_id).values_list("name", flat=True).first()
                if owner_id
                else "全部货主"
            ),
            "date_from": date_from,
            "date_to": date_to,
        }


class BossContextApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            warehouse_id = self._parse_int_param(request, "warehouse")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        self._validate_scope(request, warehouse_id=warehouse_id)
        return Response(
            build_boss_context_payload(user=request.user, warehouse_id=warehouse_id)
        )


class BossHomeApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            date_from, date_to = self._parse_date_range(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_boss_home_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            date_from=date_from,
            date_to=date_to,
        )
        record_audit_event(
            action="QUERY",
            module="reports.boss.home",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(payload)


class BossAlertApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            item_limit = self._parse_int_param(request, "item_limit") or 8
            date_from, date_to = self._parse_date_range(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        item_limit = max(1, min(item_limit, 20))
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_boss_alert_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            item_limit=item_limit,
            date_from=date_from,
            date_to=date_to,
        )
        record_audit_event(
            action="QUERY",
            module="reports.boss.alerts",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(payload)


class BossInventoryApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            item_limit = self._parse_int_param(request, "item_limit") or 8
            date_from, date_to = self._parse_date_range(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        item_limit = max(1, min(item_limit, 20))
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_boss_inventory_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            item_limit=item_limit,
            date_from=date_from,
            date_to=date_to,
        )
        record_audit_event(
            action="QUERY",
            module="reports.boss.inventory",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(payload)


class BossInventoryDetailListApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            page = max(1, self._parse_int_param(request, "page") or 1)
            page_size = min(
                100, max(1, self._parse_int_param(request, "page_size") or 20)
            )
            date_from, date_to = self._parse_date_range(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        access = AccessScope.for_user(request.user)
        current = timezone.now()
        today = (
            timezone.localtime(current).date()
            if timezone.is_aware(current)
            else current.date()
        )
        historical = date_to != today
        if historical:
            qs = InventorySnapshotDaily.objects.select_related(
                "owner", "warehouse", "product", "location"
            ).filter(snapshot_date=date_to, onhand_qty__gt=0)
        else:
            qs = InventoryDetail.objects.select_related(
                "owner", "warehouse", "product", "location"
            ).filter(onhand_qty__gt=0)
        qs = access.filter_queryset(
            qs, owner_field="owner_id", warehouse_field="warehouse_id"
        )
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__code__icontains=search)
                | Q(product__sku__icontains=search)
                | Q(owner__name__icontains=search)
                | Q(location__code__icontains=search)
            )
        qs = qs.order_by("warehouse_id", "owner_id", "product_id", "location_id", "id")
        count = qs.count()
        start = (page - 1) * page_size
        objects = list(qs[start : start + page_size])
        snapshot_marker_qs = access.filter_queryset(
            InventorySnapshotDaily.objects.filter(snapshot_date=date_to),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
        if owner_id:
            snapshot_marker_qs = snapshot_marker_qs.filter(owner_id=owner_id)
        if warehouse_id:
            snapshot_marker_qs = snapshot_marker_qs.filter(warehouse_id=warehouse_id)
        snapshot_exists = snapshot_marker_qs.exists()
        unavailable = historical and not snapshot_exists
        warnings = []
        if historical and snapshot_exists:
            approximate = qs.filter(
                snapshot_source__in=[
                    InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
                    InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX,
                ]
            ).count()
            if approximate:
                warnings.append(
                    warning("HISTORICAL_INVENTORY_APPROXIMATE", approximate)
                )
        scope_payload = self._scope_payload(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            date_from=date_from,
            date_to=date_to,
        )
        results = [
            {
                "id": row.id,
                "owner_id": row.owner_id,
                "owner_name": row.owner.name,
                "warehouse_id": row.warehouse_id,
                "warehouse_name": row.warehouse.name,
                "product_id": row.product_id,
                "product_code": row.product.code,
                "product_sku": row.product.sku,
                "product_name": row.product.name,
                "location_id": row.location_id,
                "location_code": row.location.code,
                "onhand_qty": row.onhand_qty,
                "available_qty": row.available_qty,
                "allocated_qty": row.allocated_qty,
                "locked_qty": row.locked_qty,
                "damaged_qty": row.damaged_qty,
                "base_unit": getattr(row, "base_unit", "")
                or getattr(row, "base_unit_code", "")
                or "UNKNOWN",
                "base_unit_source": getattr(row, "base_unit_source", "VERIFIED"),
                "snapshot_date": getattr(row, "snapshot_date", None),
                "snapshot_source": getattr(row, "snapshot_source", None),
            }
            for row in objects
        ]
        record_audit_event(
            action="QUERY",
            module="reports.boss.inventory_details",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(
            {
                "scope": scope_payload,
                "meta": build_meta(
                    scope=scope_payload, warnings=warnings, unavailable=unavailable
                ),
                "count": count,
                "page": page,
                "page_size": page_size,
                "next_page": page + 1 if start + page_size < count else None,
                "results": results,
            }
        )


class BossAlertSectionApi(BossScopedApiMixin, APIView):
    def get(self, request, section):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            page = self._parse_int_param(request, "page") or 1
            page_size = self._parse_int_param(request, "page_size") or 20
            date_from, date_to = self._parse_date_range(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        page_size = max(1, min(page_size, 100))
        page = max(1, min(page, 10000))
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        end = page * page_size
        payload = build_boss_alert_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            item_limit=end,
            date_from=date_from,
            date_to=date_to,
        )
        section_payload = payload["sections"].get(section)
        if section_payload is None:
            return Response(
                {"code": "UNKNOWN_ALERT_SECTION", "detail": "Unknown alert section."},
                status=status.HTTP_404_NOT_FOUND,
            )
        start = (page - 1) * page_size
        return Response(
            {
                "scope": payload["scope"],
                "meta": payload["meta"],
                "section": section,
                "label": section_payload["label"],
                "date_semantics": section_payload["date_semantics"],
                "count": section_payload["count"],
                "page": page,
                "page_size": page_size,
                "next_page": page + 1 if end < section_payload["count"] else None,
                "previous_page": page - 1 if page > 1 else None,
                "results": section_payload["items"][start:end],
            }
        )


class BossAlertDetailApi(BossScopedApiMixin, APIView):
    def _scope(self, request, qs, *, owner_id, warehouse_id):
        scope = AccessScope.for_user(request.user)
        qs = scope.filter_queryset(
            qs, owner_field="owner_id", warehouse_field="warehouse_id"
        )
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        return qs

    def get(self, request, section, item_type, pk):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            date_from, date_to = self._parse_date_range(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)

        if section == "expiring_inventory":
            if item_type == "inventory_snapshot":
                queryset = InventorySnapshotDaily.objects.select_related(
                    "owner", "warehouse", "product", "location"
                )
            elif item_type == "inventory_detail":
                queryset = InventoryDetail.objects.select_related(
                    "owner", "warehouse", "product", "location"
                )
            else:
                queryset = InventoryDetail.objects.none()
            obj = (
                self._scope(
                    request,
                    queryset,
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(
                    pk=pk,
                    expiry_date__gte=date_to,
                    expiry_date__lte=date_to + datetime.timedelta(days=7),
                )
                .first()
            )
            if obj is not None and item_type == "inventory_snapshot":
                if obj.snapshot_date != date_to:
                    obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": item_type,
                    "snapshot_date": getattr(obj, "snapshot_date", None),
                    "owner": obj.owner_id,
                    "owner_name": obj.owner.name,
                    "warehouse": obj.warehouse_id,
                    "warehouse_name": obj.warehouse.name,
                    "product": obj.product_id,
                    "product_name": obj.product.name,
                    "location": obj.location_id,
                    "location_code": obj.location.code,
                    "expiry_date": obj.expiry_date,
                    "onhand_qty": obj.onhand_qty,
                    "available_qty": obj.available_qty,
                    "locked_qty": obj.locked_qty,
                    "damaged_qty": obj.damaged_qty,
                    "base_unit": getattr(obj, "base_unit", "")
                    or getattr(obj, "base_unit_code", ""),
                    "base_unit_source": getattr(obj, "base_unit_source", "VERIFIED"),
                    "snapshot_source": getattr(obj, "snapshot_source", None),
                }
            )
        elif section in {"overdue_tasks", "pending_review_tasks"}:
            obj = (
                self._scope(
                    request,
                    WmsTask.objects.prefetch_related("lines").select_related(
                        "owner", "warehouse"
                    ),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, created_at__date__lte=date_to)
                .first()
            )
            if obj is not None:
                closed = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
                if obj.status in closed:
                    obj = None
                elif (
                    section == "pending_review_tasks"
                    and obj.task_type != WmsTask.TaskType.REVIEW
                ):
                    obj = None
                elif section == "overdue_tasks" and (
                    not obj.planned_end or obj.planned_end >= self._date_cutoff(date_to)
                ):
                    obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "task",
                    "task_no": obj.task_no,
                    "task_type": obj.task_type,
                    "status": obj.status,
                    "owner": obj.owner_id,
                    "owner_name": obj.owner.name,
                    "warehouse": obj.warehouse_id,
                    "warehouse_name": obj.warehouse.name,
                    "planned_end": obj.planned_end,
                    "created_at": obj.created_at,
                    "line_count": obj.lines.count(),
                }
            )
        elif section in {"overdue_bills", "bills_missing_due_date"}:
            obj = (
                self._scope(
                    request,
                    Bill.objects.select_related("owner", "warehouse", "period"),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, issue_date__lte=date_to)
                .first()
            )
            if obj is not None:
                if section == "overdue_bills" and not (
                    obj.status == BillStatus.ISSUED
                    and obj.due_date
                    and obj.due_date < date_to
                ):
                    obj = None
                elif section == "bills_missing_due_date" and not (
                    obj.status in {BillStatus.ISSUED, BillStatus.PAID}
                    and obj.due_date is None
                ):
                    obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "bill",
                    "invoice_no": obj.invoice_no,
                    "status": obj.status,
                    "issue_date": obj.issue_date,
                    "due_date": obj.due_date,
                    "currency": obj.currency or "UNKNOWN",
                    "subtotal": obj.subtotal,
                    "tax_total": obj.tax_total,
                    "total": obj.total,
                    "owner": obj.owner_id,
                    "owner_name": obj.owner.name,
                    "warehouse": obj.warehouse_id,
                    "warehouse_name": obj.warehouse.name,
                }
            )
        elif section == "failed_billing_jobs":
            obj = (
                self._scope(
                    request,
                    BillingJobRun.objects.select_related("owner", "warehouse"),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, service_date__range=(date_from, date_to))
                .first()
            )
            if obj is not None and obj.status != BillingJobRun.Status.FAILED:
                obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "billing_job",
                    "job_name": obj.job_name,
                    "service_date": obj.service_date,
                    "status": obj.status,
                    "message": obj.message,
                    "summary": obj.summary,
                    "started_at": obj.started_at,
                    "finished_at": obj.finished_at,
                }
            )
        elif section == "review_differences":
            obj = (
                self._scope(
                    request,
                    ReviewDifference.objects.select_related(
                        "owner", "warehouse", "source_task"
                    ).prefetch_related("lines__product", "lines__location"),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, created_at__date__lte=date_to)
                .first()
            )
            if obj is not None and obj.status not in {
                ReviewDifference.Status.PENDING,
                ReviewDifference.Status.IN_PROGRESS,
            }:
                obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "review_difference",
                    "order_no": obj.order_no,
                    "status": obj.status,
                    "reason": obj.reason,
                    "note": obj.note,
                    "owner": obj.owner_id,
                    "owner_name": getattr(obj.owner, "name", ""),
                    "warehouse": obj.warehouse_id,
                    "warehouse_name": obj.warehouse.name,
                    "source_task": obj.source_task_id,
                    "legacy_owner_unknown": obj.owner_id is None,
                    "lines": [
                        {
                            "id": line.id,
                            "product": line.product_id,
                            "product_name": line.product.name,
                            "location": line.location_id,
                            "location_code": line.location.code,
                            "quantity_before": line.quantity_before,
                            "quantity_after": line.quantity_after,
                            "quantity_difference": line.quantity_difference,
                            "status": line.status,
                        }
                        for line in obj.lines.all()
                    ],
                }
            )
        elif section == "unpriced_billing_events":
            obj = (
                self._scope(
                    request,
                    BillingEvent.objects.select_related(
                        "owner", "warehouse", "pricing_rule"
                    ),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, service_date__range=(date_from, date_to))
                .first()
            )
            if obj is not None and obj.pricing_status not in {
                PricingStatus.PENDING,
                PricingStatus.UNPRICED,
            }:
                obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "billing_event",
                    "service_date": obj.service_date,
                    "charge_type": obj.charge_type,
                    "calc_method": obj.calc_method,
                    "pricing_status": obj.pricing_status,
                    "pricing_reason": obj.pricing_reason,
                    "pricing_detail": obj.pricing_detail,
                    "priced_at": obj.priced_at,
                }
            )
        elif section == "approximate_billing_data" and item_type == "metric":
            obj = (
                self._scope(
                    request,
                    BillingMetricDaily.objects.select_related("owner", "warehouse"),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, service_date__range=(date_from, date_to))
                .first()
            )
            if obj is not None and obj.source_quality != SourceQuality.APPROXIMATE:
                obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "metric",
                    "service_date": obj.service_date,
                    "metric_type": obj.metric_type,
                    "value": obj.value,
                    "source": obj.source,
                    "source_quality": obj.source_quality,
                }
            )
        elif section == "approximate_billing_data":
            obj = (
                self._scope(
                    request,
                    BillingAccrual.objects.select_related("owner", "warehouse", "rule"),
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                )
                .filter(pk=pk, service_date__range=(date_from, date_to))
                .first()
            )
            if obj is not None and (
                obj.source_quality != SourceQuality.APPROXIMATE
                or obj.status == AccrualStatus.VOID
            ):
                obj = None
            data = (
                None
                if obj is None
                else {
                    "id": obj.id,
                    "item_type": "accrual",
                    "service_date": obj.service_date,
                    "charge_type": obj.charge_type,
                    "currency": obj.currency or "UNKNOWN",
                    "amount": obj.amount,
                    "tax_amount": obj.tax_amount,
                    "source_quality": obj.source_quality,
                    "source_note": obj.source_note,
                }
            )
        else:
            data = None

        if data is None:
            return Response(
                {"detail": "Alert item not found."}, status=status.HTTP_404_NOT_FOUND
            )
        scope_payload = self._scope_payload(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            date_from=date_from,
            date_to=date_to,
        )
        warnings = []
        if data.get("currency") == "UNKNOWN":
            warnings.append(warning("UNKNOWN_CURRENCY", 1))
        if data.get("legacy_owner_unknown"):
            warnings.append(warning("LEGACY_OWNER_UNKNOWN", 1))
        record_audit_event(
            action="QUERY",
            module="reports.boss.alert_detail",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            metadata={"section": section, "item_type": item_type, "object_id": pk},
        )
        return Response(
            {
                "scope": scope_payload,
                "meta": build_meta(scope=scope_payload, warnings=warnings),
                "section": section,
                "detail": data,
            }
        )

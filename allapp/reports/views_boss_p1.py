from __future__ import annotations

import datetime
import hashlib
import json
import secrets

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from openpyxl import Workbook
from rest_framework import permissions, serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.billing.models import Bill
from allapp.inventory.models import InventoryLayerPosition
from allapp.locations.models import Warehouse
from allapp.tasking.models import WmsTask

from .boss_contract import build_meta, warning
from .models import (
    AlertCase,
    AlertCaseHistory,
    BusinessReviewSnapshot,
    FactInboundLine,
    FactOutboundLine,
    FactOutboundOrderSLA,
    OperatingTarget,
    TaskStateSnapshotDaily,
)
from .services_boss_p1 import (
    build_inventory_risk,
    build_performance,
    build_receivables,
    build_resource_yield,
    build_revenue_assurance,
)
from .services_operations import (
    OperationFilters,
    build_operations_summary,
    paginate_operations_details,
)
from .views_boss import BossScopedApiMixin


class BossP1Mixin(BossScopedApiMixin):
    def parse_scope(self, request):
        def raw(name):
            return request.query_params.get(name) or request.data.get(name) or ""

        def integer(name):
            value = str(raw(name)).strip()
            if not value:
                return None
            if not value.isdigit():
                raise ValueError(f"{name} must be an integer id.")
            return int(value)

        owner_id = integer("owner")
        warehouse_id = integer("warehouse")
        current = timezone.now()
        today = timezone.localtime(current).date() if timezone.is_aware(current) else current.date()
        date_to = parse_date(str(raw("date_to"))) if raw("date_to") else today
        date_from = (
            parse_date(str(raw("date_from"))) if raw("date_from") else date_to.replace(day=1)
        )
        if date_from is None or date_to is None:
            raise ValueError("date_from and date_to must be YYYY-MM-DD.")
        if date_from > date_to or date_to > today or (date_to - date_from).days > 366:
            raise ValueError("Invalid date range; maximum 367 days and date_to cannot be future.")
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        scope = self._scope_payload(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            date_from=date_from,
            date_to=date_to,
        )
        return owner_id, warehouse_id, date_from, date_to, scope

    def get_payload(self, request, builder):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
        except ValueError as exc:
            return None, Response({"detail": str(exc)}, status=400)
        return (
            builder(
                user=request.user,
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                date_from=date_from,
                date_to=date_to,
                scope=scope,
            ),
            None,
        )


def _cycle_stats(queryset, field):
    total = queryset.count()
    values = sorted(queryset.exclude(**{f"{field}__isnull": True}).values_list(field, flat=True))
    count = len(values)
    if not values:
        return {
            "sample_count": 0,
            "missing_count": total,
            "average_seconds": None,
            "p50_seconds": None,
            "p90_seconds": None,
        }
    return {
        "sample_count": count,
        "missing_count": total - count,
        "average_seconds": sum(values) / count,
        "p50_seconds": values[max(0, int((count - 1) * 0.50))],
        "p90_seconds": values[max(0, int((count - 1) * 0.90))],
    }


class OperatingTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperatingTarget
        fields = [
            "id",
            "month",
            "warehouse",
            "owner",
            "metric",
            "currency",
            "target_value",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = self.instance
        metric = attrs.get("metric", getattr(instance, "metric", None))
        currency = attrs.get("currency", getattr(instance, "currency", ""))
        month = attrs.get("month", getattr(instance, "month", None))
        warehouse = attrs.get("warehouse", getattr(instance, "warehouse", None))
        owner = attrs.get("owner", getattr(instance, "owner", None))
        if month and month.day != 1:
            raise serializers.ValidationError({"month": "目标月份必须使用当月第一天。"})
        if metric == OperatingTarget.Metric.ACCRUAL_REVENUE and not currency:
            raise serializers.ValidationError({"currency": "收入目标必须填写币种。"})
        if metric != OperatingTarget.Metric.ACCRUAL_REVENUE and currency:
            raise serializers.ValidationError({"currency": "百分比目标不能填写币种。"})
        if all([month, warehouse, metric]):
            duplicate = OperatingTarget.objects.filter(
                month=month,
                warehouse=warehouse,
                owner_scope_id=getattr(owner, "pk", 0) or 0,
                metric=metric,
                currency=currency or "",
            )
            if instance:
                duplicate = duplicate.exclude(pk=instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError("相同范围和指标的月度目标已存在。")
        return attrs


class OperatingTargetViewSet(viewsets.ModelViewSet):
    serializer_class = OperatingTargetSerializer

    def get_queryset(self):
        qs = OperatingTarget.objects.select_related("warehouse", "owner")
        return (
            AccessScope.for_user(self.request.user)
            .filter_queryset(qs, owner_field="owner_id", warehouse_field="warehouse_id")
            .order_by("-month", "warehouse_id", "owner_id", "metric", "currency", "id")
        )

    def get_permissions(self):
        return [permissions.IsAuthenticated()]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method in permissions.SAFE_METHODS and not (
            request.user.is_superuser
            or request.user.has_perm("reports.view_boss_dashboard")
            or request.user.has_perm("reports.view_operatingtarget")
        ):
            raise PermissionDenied("需要经营目标查看权限。")

    def _require_write(self):
        if not self.request.user.has_perm("reports.manage_operating_targets"):
            raise PermissionDenied("需要经营目标维护权限。")

    def perform_create(self, serializer):
        self._require_write()
        warehouse = serializer.validated_data["warehouse"]
        owner = serializer.validated_data.get("owner")
        if not AccessScope.for_user(self.request.user).allows(
            warehouse_id=warehouse.pk, owner_id=getattr(owner, "pk", None)
        ):
            raise PermissionDenied("经营目标超出授权范围。")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._require_write()
        warehouse = serializer.validated_data.get("warehouse", serializer.instance.warehouse)
        owner = serializer.validated_data.get("owner", serializer.instance.owner)
        if not AccessScope.for_user(self.request.user).allows(
            warehouse_id=warehouse.pk, owner_id=getattr(owner, "pk", None)
        ):
            raise PermissionDenied("经营目标超出授权范围。")
        serializer.save()

    def perform_destroy(self, instance):
        self._require_write()
        instance.delete()


class BossRevenueAssuranceApi(BossP1Mixin, APIView):
    def get(self, request):
        payload, error = self.get_payload(request, build_revenue_assurance)
        return error or Response(payload)


class BossRevenueAssuranceSectionApi(BossP1Mixin, APIView):
    def get(self, request, section):
        payload, error = self.get_payload(request, build_revenue_assurance)
        if error:
            return error
        value = payload["sections"].get(section)
        if value is None:
            return Response({"code": "UNKNOWN_SECTION"}, status=404)
        return Response(
            {
                "scope": payload["scope"],
                "meta": payload["meta"],
                "section": section,
                **value,
            }
        )


def _cockpit_workbook(*, report_type, scope, meta, payload):
    def safe(value):
        text = "" if value is None else str(value)
        return f"'{text}" if text[:1] in {"=", "+", "-", "@"} else text

    workbook = Workbook()
    info = workbook.active
    info.title = "报告信息"
    info.append(["字段", "值"])
    rows = [
        ("报告类型", report_type),
        (
            "仓库范围",
            scope.get("warehouse_name") or scope.get("warehouse") or "全部授权仓库",
        ),
        ("货主", scope.get("owner_name") or scope.get("owner") or "全部货主"),
        ("日期开始", scope.get("date_from")),
        ("日期结束", scope.get("date_to")),
        ("生成时间", meta.get("generated_at")),
        ("数据截至", meta.get("data_as_of") or meta.get("generated_at")),
        ("数据状态", meta.get("data_status")),
        (
            "Warnings",
            json.dumps(meta.get("warnings", []), ensure_ascii=False, cls=DjangoJSONEncoder),
        ),
    ]
    for row in rows:
        info.append([safe(row[0]), safe(row[1])])
    data = workbook.create_sheet("冻结数据" if report_type == "REVIEW_SNAPSHOT" else "报表数据")
    data.append(["数据块", "分片", "JSON（金额均携带原币种）"])
    for key, value in payload.items():
        encoded = json.dumps(value, ensure_ascii=False, cls=DjangoJSONEncoder)
        chunks = [encoded[index : index + 30000] for index in range(0, len(encoded), 30000)] or [""]
        for index, chunk in enumerate(chunks, start=1):
            data.append([safe(key), index, safe(chunk)])
    return workbook


def _workbook_response(workbook, filename):
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


class BossCockpitExportApi(BossP1Mixin, APIView):
    def post(self, request):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        report_type = str(request.data.get("report_type") or "").lower()
        builders = {
            "revenue_assurance": build_revenue_assurance,
            "receivables": build_receivables,
            "resource_yield": build_resource_yield,
            "inventory_risk": build_inventory_risk,
            "performance": build_performance,
        }
        builder = builders.get(report_type)
        if builder is None:
            return Response({"detail": "Unsupported report_type."}, status=400)
        payload = builder(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            date_from=date_from,
            date_to=date_to,
            scope=scope,
        )
        workbook = _cockpit_workbook(
            report_type=report_type,
            scope=scope,
            meta=payload.get("meta", {}),
            payload={key: value for key, value in payload.items() if key not in {"scope", "meta"}},
        )
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        return _workbook_response(
            workbook, f"boss-{report_type}-{date_from}-{date_to}-{stamp}.xlsx"
        )


class BossReceivablesApi(BossP1Mixin, APIView):
    def get(self, request):
        payload, error = self.get_payload(request, build_receivables)
        if error:
            return error
        payload = dict(payload)
        payload.pop("bills", None)
        return Response(payload)


class BossReceivableBillsApi(BossP1Mixin, APIView):
    def get(self, request):
        payload, error = self.get_payload(request, build_receivables)
        if error:
            return error
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid pagination."}, status=400)
        rows = payload.pop("bills")
        payment_status = (request.query_params.get("payment_status") or "").upper()
        aging_band = (request.query_params.get("aging_band") or "").upper()
        if payment_status:
            rows = [row for row in rows if row["payment_status"] == payment_status]
        if aging_band:
            rows = [row for row in rows if row["aging_band"] == aging_band]
        start = (page - 1) * page_size
        return Response(
            {
                "scope": payload["scope"],
                "meta": payload["meta"],
                "count": len(rows),
                "page": page,
                "page_size": page_size,
                "results": rows[start : start + page_size],
            }
        )


class BossCollectionHistoryApi(BossP1Mixin, APIView):
    def get(self, request, pk):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        bills = AccessScope.for_user(request.user).filter_queryset(
            Bill.objects.select_related("owner", "warehouse").prefetch_related(
                "payment_allocations__receipt", "collection_case__activities"
            ),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
        if owner_id:
            bills = bills.filter(owner_id=owner_id)
        if warehouse_id:
            bills = bills.filter(warehouse_id=warehouse_id)
        bill = bills.filter(pk=pk).first()
        if not bill:
            return Response(status=404)
        allocations = [
            {
                "id": row.id,
                "receipt_id": row.receipt_id,
                "receipt_no": row.receipt.receipt_no,
                "receipt_date": row.receipt.receipt_date,
                "amount": row.amount,
                "is_reversal": row.is_reversal,
            }
            for row in bill.payment_allocations.order_by("allocated_at", "id")
        ]
        case = getattr(bill, "collection_case", None)
        activities = (
            []
            if not case
            else [
                {
                    "id": row.id,
                    "contacted_at": row.contacted_at,
                    "channel": row.channel,
                    "result": row.result,
                    "note": row.note,
                    "next_follow_up_at": row.next_follow_up_at,
                }
                for row in case.activities.all()
            ]
        )
        return Response(
            {
                "scope": scope,
                "meta": build_meta(scope=scope),
                "bill_id": bill.id,
                "allocations": allocations,
                "collection_case": (
                    None
                    if not case
                    else {
                        "id": case.id,
                        "status": case.status,
                        "assignee": case.assignee_id,
                        "activities": activities,
                    }
                ),
            }
        )


class BossResourceYieldApi(BossP1Mixin, APIView):
    def get(self, request):
        payload, error = self.get_payload(request, build_resource_yield)
        return error or Response(payload)


class BossPerformanceApi(BossP1Mixin, APIView):
    def get(self, request):
        payload, error = self.get_payload(request, build_performance)
        return error or Response(payload)


class BossInventoryRiskApi(BossP1Mixin, APIView):
    def get(self, request):
        payload, error = self.get_payload(request, build_inventory_risk)
        return error or Response(payload)


class BossInventoryRiskDetailsApi(BossP1Mixin, APIView):
    def get(self, request):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=400)
        qs = AccessScope.for_user(request.user).filter_queryset(
            InventoryLayerPosition.objects.filter(remaining_qty__gt=0).select_related(
                "layer",
                "layer__product",
                "layer__base_uom",
                "location",
                "location__subwarehouse",
            ),
            owner_field="layer__owner_id",
            warehouse_field="layer__warehouse_id",
        )
        if owner_id:
            qs = qs.filter(layer__owner_id=owner_id)
        if warehouse_id:
            qs = qs.filter(layer__warehouse_id=warehouse_id)
        filters = {
            "layer__product_id": request.query_params.get("product"),
            "layer__batch_no": request.query_params.get("batch"),
            "location__subwarehouse_id": request.query_params.get("subwarehouse"),
            "location__zone_type": request.query_params.get("zone_type"),
            "location_id": request.query_params.get("location"),
        }
        for field, value in filters.items():
            if value not in (None, ""):
                qs = qs.filter(**{field: value})
        age_band = (request.query_params.get("age_band") or "").upper()
        age_ranges = {
            "0_7": (date_to - datetime.timedelta(days=7), date_to),
            "8_30": (
                date_to - datetime.timedelta(days=30),
                date_to - datetime.timedelta(days=8),
            ),
            "31_60": (
                date_to - datetime.timedelta(days=60),
                date_to - datetime.timedelta(days=31),
            ),
            "61_90": (
                date_to - datetime.timedelta(days=90),
                date_to - datetime.timedelta(days=61),
            ),
        }
        if age_band in age_ranges:
            qs = qs.filter(layer__received_date__range=age_ranges[age_band])
        elif age_band == "90_PLUS":
            qs = qs.filter(layer__received_date__lt=date_to - datetime.timedelta(days=90))
        elif age_band == "UNKNOWN":
            qs = qs.filter(layer__received_date__isnull=True)
        qs = qs.order_by("layer__received_date", "layer_id", "location_id", "id")
        count = qs.count()
        start = (page - 1) * page_size
        rows = []
        for position in qs[start : start + page_size]:
            layer = position.layer
            age_days = (date_to - layer.received_date).days if layer.received_date else None
            expiry_days = (layer.expiry_date - date_to).days if layer.expiry_date else None
            rows.append(
                {
                    "id": position.id,
                    "layer_id": layer.id,
                    "owner_id": layer.owner_id,
                    "warehouse_id": layer.warehouse_id,
                    "product_id": layer.product_id,
                    "product_name": layer.product.name,
                    "batch_no": layer.batch_no,
                    "serial_no": layer.serial_no,
                    "location_id": position.location_id,
                    "location_code": position.location.code,
                    "received_date": layer.received_date,
                    "age_days": age_days,
                    "expiry_date": layer.expiry_date,
                    "expiry_days": expiry_days,
                    "remaining_qty": position.remaining_qty,
                    "base_unit": {
                        "code": layer.base_uom.code,
                        "name": layer.base_uom.name,
                        "kind": layer.base_uom.kind,
                    },
                    "unit_cost": layer.unit_cost,
                    "cost_currency": layer.cost_currency or "UNKNOWN",
                    "cost_quality": layer.cost_quality,
                }
            )
        return Response(
            {
                "scope": scope,
                "meta": build_meta(scope=scope),
                "count": count,
                "page": page,
                "page_size": page_size,
                "results": rows,
            }
        )


class BossOperationsApi(BossP1Mixin, APIView):
    def get(self, request):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        operation_filters = OperationFilters(
            start_date=date_from,
            end_date=date_to,
            direction=(request.query_params.get("direction") or "all").lower(),
            metric_basis=(request.query_params.get("metric_basis") or "actual").lower(),
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            status=(request.query_params.get("status") or "").upper(),
            order_no="",
            source_no="",
            product="",
            lot_no="",
            task_no="",
            operator="",
            exception_type=(request.query_params.get("exception_type") or "").lower(),
        )
        operation_filters.validate()
        payload = build_operations_summary(user=request.user, filters=operation_filters)
        sla_qs = AccessScope.for_user(request.user).filter_queryset(
            FactOutboundOrderSLA.objects.filter(order_date__date__range=(date_from, date_to)),
            owner_field="owner__owner_id",
            warehouse_field="warehouse__warehouse_id",
        )
        if owner_id:
            sla_qs = sla_qs.filter(owner__owner_id=owner_id)
        if warehouse_id:
            sla_qs = sla_qs.filter(warehouse__warehouse_id=warehouse_id)
        sla = sla_qs.aggregate(
            total_orders=Count("id"),
            eligible_orders=Count("id", filter=Q(sla_eligible=True)),
            on_time_orders=Count("id", filter=Q(sla_eligible=True, on_time=True)),
            in_full_orders=Count("id", filter=Q(sla_eligible=True, in_full=True)),
            otif_orders=Count("id", filter=Q(sla_eligible=True, otif=True)),
            missing_etd_orders=Count("id", filter=Q(etd__isnull=True)),
        )
        denominator = sla["eligible_orders"] or 0
        sla.update(
            {
                "on_time_rate": (sla["on_time_orders"] / denominator if denominator else None),
                "in_full_rate": (sla["in_full_orders"] / denominator if denominator else None),
                "otif_rate": sla["otif_orders"] / denominator if denominator else None,
                "coverage": (denominator / sla["total_orders"] if sla["total_orders"] else None),
            }
        )
        inbound_cycles = AccessScope.for_user(request.user).filter_queryset(
            FactInboundLine.objects.filter(order_date__date__range=(date_from, date_to)),
            owner_field="owner__owner_id",
            warehouse_field="warehouse__warehouse_id",
        )
        outbound_cycles = AccessScope.for_user(request.user).filter_queryset(
            FactOutboundLine.objects.filter(order_date__date__range=(date_from, date_to)),
            owner_field="owner__owner_id",
            warehouse_field="warehouse__warehouse_id",
        )
        if owner_id:
            inbound_cycles = inbound_cycles.filter(owner__owner_id=owner_id)
            outbound_cycles = outbound_cycles.filter(owner__owner_id=owner_id)
        if warehouse_id:
            inbound_cycles = inbound_cycles.filter(warehouse__warehouse_id=warehouse_id)
            outbound_cycles = outbound_cycles.filter(warehouse__warehouse_id=warehouse_id)
        cycles = {
            "order_to_receive": _cycle_stats(inbound_cycles, "sec_to_receive"),
            "receive_to_putaway": _cycle_stats(inbound_cycles, "sec_to_putaway"),
            "order_to_allocate": _cycle_stats(outbound_cycles, "sec_alloc"),
            "allocate_to_pick": _cycle_stats(outbound_cycles, "sec_pick"),
            "pick_to_pack": _cycle_stats(outbound_cycles, "sec_pack"),
            "pack_to_ship": _cycle_stats(outbound_cycles, "sec_ship"),
        }
        current = timezone.now()
        today = timezone.localtime(current).date() if timezone.is_aware(current) else current.date()
        backlog_warnings = []
        backlog_rows = []
        if date_to != today:
            snapshots = AccessScope.for_user(request.user).filter_queryset(
                TaskStateSnapshotDaily.objects.filter(snapshot_date=date_to),
                owner_field="owner_id",
                warehouse_field="warehouse_id",
            )
            if owner_id:
                snapshots = snapshots.filter(owner_id=owner_id)
            if warehouse_id:
                snapshots = snapshots.filter(warehouse_id=warehouse_id)
            backlog_rows = list(snapshots.values("task_id", "status", "age_minutes"))
            if not backlog_rows:
                backlog_warnings.append(warning("LEGACY_BACKLOG_SNAPSHOT_MISSING", 1))
        if date_to == today or not backlog_rows:
            tasks = AccessScope.for_user(request.user).filter_queryset(
                WmsTask.objects.exclude(
                    status__in=[WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
                ).filter(created_at__date__lte=date_to),
                owner_field="owner_id",
                warehouse_field="warehouse_id",
            )
            if owner_id:
                tasks = tasks.filter(owner_id=owner_id)
            if warehouse_id:
                tasks = tasks.filter(warehouse_id=warehouse_id)
            for task in tasks:
                anchor = task.started_at or task.released_at or task.created_at
                age_minutes = max(0, int((current - anchor).total_seconds() // 60)) if anchor else 0
                backlog_rows.append(
                    {
                        "task_id": task.id,
                        "status": task.status,
                        "age_minutes": age_minutes,
                    }
                )
        age_bands = {"0_4H": 0, "4_24H": 0, "1_3D": 0, "3D_PLUS": 0}
        for row in backlog_rows:
            minutes = row["age_minutes"]
            key = (
                "0_4H"
                if minutes < 240
                else ("4_24H" if minutes < 1440 else "1_3D" if minutes < 4320 else "3D_PLUS")
            )
            age_bands[key] += 1
        backlog = {
            "count": len(backlog_rows),
            "oldest_age_minutes": max((row["age_minutes"] for row in backlog_rows), default=0),
            "age_bands": age_bands,
            "date_semantics": (
                "HISTORICAL_SNAPSHOT"
                if date_to != today and not backlog_warnings
                else "CURRENT_UNRESOLVED_CREATED_BEFORE_CUTOFF"
            ),
        }
        return Response(
            {
                "scope": scope,
                "meta": build_meta(scope=scope, warnings=backlog_warnings),
                "operations": payload,
                "sla": sla,
                "cycles": cycles,
                "backlog": backlog,
            }
        )


class BossOperationsDetailsApi(BossOperationsApi):
    def get(self, request):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=400)
        filters = OperationFilters(
            start_date=date_from,
            end_date=date_to,
            direction=(request.query_params.get("direction") or "all").lower(),
            metric_basis=(request.query_params.get("metric_basis") or "actual").lower(),
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            status=(request.query_params.get("status") or "").upper(),
            order_no=(request.query_params.get("order_no") or ""),
            source_no="",
            product="",
            lot_no="",
            task_no="",
            operator="",
            exception_type=(request.query_params.get("exception_type") or "").lower(),
        )
        filters.validate()
        count, rows = paginate_operations_details(
            user=request.user, filters=filters, page=page, page_size=page_size
        )
        return Response(
            {
                "scope": scope,
                "meta": build_meta(scope=scope),
                "count": count,
                "page": page,
                "page_size": page_size,
                "results": rows,
            }
        )


def _alert_payload(case):
    return {
        "id": case.id,
        "alert_type": case.alert_type,
        "source_type": case.source_type,
        "source_id": case.source_id,
        "owner_id": case.owner_id,
        "warehouse_id": case.warehouse_id,
        "severity": case.severity,
        "title": case.title,
        "detail": case.detail,
        "first_seen_at": case.first_seen_at,
        "last_seen_at": case.last_seen_at,
        "assignee": case.assignee_id,
        "due_at": case.due_at,
        "status": case.status,
    }


class BossAlertCasesApi(BossP1Mixin, APIView):
    def get(self, request):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=400)
        qs = AccessScope.for_user(request.user).filter_queryset(
            AlertCase.objects.select_related("owner", "warehouse", "assignee"),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
        if owner_id:
            qs = qs.filter(Q(owner_id=owner_id) | Q(owner__isnull=True))
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        qs = qs.filter(first_seen_at__date__lte=date_to).order_by(
            "-severity", "-last_seen_at", "id"
        )
        count = qs.count()
        start = (page - 1) * page_size
        return Response(
            {
                "scope": scope,
                "meta": build_meta(scope=scope),
                "count": count,
                "page": page,
                "page_size": page_size,
                "results": [_alert_payload(row) for row in qs[start : start + page_size]],
            }
        )


class BossAlertCaseDetailApi(BossAlertCasesApi):
    def get(self, request, pk):
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        qs = AccessScope.for_user(request.user).filter_queryset(
            AlertCase.objects.prefetch_related("history"),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
        case = qs.filter(pk=pk).first()
        if not case:
            return Response(status=404)
        data = _alert_payload(case)
        data["history"] = list(
            case.history.values(
                "id",
                "action",
                "from_status",
                "to_status",
                "note",
                "detail",
                "created_at",
                "created_by_id",
            )
        )
        return Response({"scope": scope, "meta": build_meta(scope=scope), "case": data})


class AlertCaseActionApi(APIView):
    def _case(self, request, pk):
        if not request.user.has_perm("reports.manage_alert_cases"):
            raise PermissionDenied("需要预警处置权限。")
        case = AlertCase.objects.select_for_update().get(pk=pk)
        if not AccessScope.for_user(request.user).allows(
            warehouse_id=case.warehouse_id, owner_id=case.owner_id
        ):
            raise PermissionDenied("预警超出授权范围。")
        return case

    @transaction.atomic
    def patch(self, request, pk, action):
        case = self._case(request, pk)
        before = case.status
        if action == "assignment":
            assignee_id = request.data.get("assignee")
            from django.contrib.auth import get_user_model

            assignee = get_user_model().objects.filter(pk=assignee_id).first()
            if not assignee:
                return Response({"detail": "责任人不存在。"}, status=400)
            if not AccessScope.for_user(assignee).allows(warehouse_id=case.warehouse_id):
                return Response({"detail": "责任人没有对应仓库范围。"}, status=409)
            case.assignee = assignee
            due_at = request.data.get("due_at")
            if due_at:
                from django.utils.dateparse import parse_datetime

                case.due_at = parse_datetime(str(due_at))
                if case.due_at is None:
                    return Response({"detail": "due_at 格式无效。"}, status=400)
            history_action = "ASSIGN"
        elif action == "acknowledge":
            if case.status not in [AlertCase.Status.OPEN, AlertCase.Status.IN_PROGRESS]:
                return Response({"detail": "当前状态不能确认。"}, status=409)
            case.status = AlertCase.Status.ACKNOWLEDGED
            history_action = "ACKNOWLEDGE"
        elif action == "note":
            history_action = "NOTE"
        elif action == "close":
            if case.status != AlertCase.Status.RESOLVED:
                return Response({"detail": "来源问题恢复后才能关闭。"}, status=409)
            case.status = AlertCase.Status.CLOSED
            case.closed_at = timezone.now()
            history_action = "CLOSE"
        else:
            return Response(status=404)
        case.save()
        AlertCaseHistory.objects.create(
            case=case,
            action=history_action,
            from_status=before,
            to_status=case.status,
            note=str(request.data.get("note") or ""),
            created_by=request.user,
        )
        return Response(_alert_payload(case))

    post = patch


class BossReviewSnapshotCreateApi(BossP1Mixin, APIView):
    @transaction.atomic
    def post(self, request):
        if not request.user.has_perm("reports.create_business_review_snapshot"):
            raise PermissionDenied("当前账号没有创建经营例会快照的权限。")
        try:
            owner_id, warehouse_id, date_from, date_to, scope = self.parse_scope(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        access = AccessScope.for_user(request.user)
        warehouse_ids = (
            [warehouse_id]
            if warehouse_id
            else (
                list(Warehouse.objects.order_by("id").values_list("id", flat=True))
                if access.is_global
                else sorted(access.warehouse_ids)
            )
        )
        if not warehouse_ids:
            raise PermissionDenied("当前账号没有可冻结的仓库范围。")
        sections = request.data.get("sections") or [
            "revenue_assurance",
            "receivables",
            "resource_yield",
            "performance",
            "inventory_risk",
        ]
        builders = {
            "revenue_assurance": build_revenue_assurance,
            "receivables": build_receivables,
            "resource_yield": build_resource_yield,
            "performance": build_performance,
            "inventory_risk": build_inventory_risk,
        }
        payload = {}
        for key in sections:
            if key not in builders:
                return Response({"detail": f"Unknown snapshot section: {key}"}, status=400)
            payload[key] = builders[key](
                user=request.user,
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                date_from=date_from,
                date_to=date_to,
                scope=scope,
            )
        frozen = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
        canonical = json.dumps(frozen, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        snapshot = BusinessReviewSnapshot.objects.create(
            share_code=secrets.token_urlsafe(24),
            created_by=request.user,
            warehouse_ids=warehouse_ids,
            owner_id=owner_id,
            date_from=date_from,
            date_to=date_to,
            data_as_of=timezone.now(),
            response_version="v1",
            payload=frozen,
            warnings=[
                item
                for value in frozen.values()
                for item in value.get("meta", {}).get("warnings", [])
            ],
            checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        record_audit_event(
            action="reports.business_review_snapshot.create",
            module="reports",
            request=request,
            obj=snapshot,
            warehouse_id=warehouse_ids[0] if len(warehouse_ids) == 1 else None,
            metadata={"warehouse_ids": warehouse_ids, "checksum": snapshot.checksum},
        )
        return Response(
            {
                "id": snapshot.id,
                "share_code": snapshot.share_code,
                "checksum": snapshot.checksum,
            },
            status=201,
        )


class BossReviewSnapshotDetailApi(BossScopedApiMixin, APIView):
    def get(self, request, share_code):
        snapshot = BusinessReviewSnapshot.objects.filter(
            share_code=share_code, revoked_at__isnull=True
        ).first()
        if not snapshot:
            return Response(status=404)
        access = AccessScope.for_user(request.user)
        if not all(
            access.allows(
                warehouse_id=warehouse_id,
                owner_id=snapshot.owner_id,
            )
            for warehouse_id in snapshot.warehouse_ids
        ):
            raise PermissionDenied("快照包含未授权范围。")
        return Response(
            {
                "id": snapshot.id,
                "created_at": snapshot.created_at,
                "data_as_of": snapshot.data_as_of,
                "checksum": snapshot.checksum,
                "warnings": snapshot.warnings,
                "payload": snapshot.payload,
            }
        )


class BossReviewSnapshotExportApi(BossReviewSnapshotDetailApi):
    def get(self, request, share_code):
        detail_response = super().get(request, share_code)
        if detail_response.status_code != 200:
            return detail_response
        value = detail_response.data
        snapshot = BusinessReviewSnapshot.objects.get(
            share_code=share_code, revoked_at__isnull=True
        )
        scope = {
            "warehouse_name": ",".join(str(item) for item in snapshot.warehouse_ids),
            "owner": snapshot.owner_id,
            "date_from": snapshot.date_from,
            "date_to": snapshot.date_to,
        }
        meta = {
            "generated_at": snapshot.created_at,
            "data_as_of": snapshot.data_as_of,
            "data_status": "WARNING" if snapshot.warnings else "COMPLETE",
            "warnings": snapshot.warnings,
        }
        workbook = _cockpit_workbook(
            report_type="REVIEW_SNAPSHOT",
            scope=scope,
            meta=meta,
            payload=value["payload"],
        )
        return _workbook_response(
            workbook,
            f"business-review-{snapshot.id}-{snapshot.date_from}-{snapshot.date_to}.xlsx",
        )


class BossReviewSnapshotRevokeApi(BossScopedApiMixin, APIView):
    @transaction.atomic
    def post(self, request, pk):
        snapshot = BusinessReviewSnapshot.objects.select_for_update().filter(pk=pk).first()
        if not snapshot:
            return Response(status=404)
        if snapshot.created_by_id != request.user.id and not request.user.is_superuser:
            raise PermissionDenied("只能撤销自己创建的经营快照。")
        snapshot.revoked_at = timezone.now()
        snapshot.revoked_by = request.user
        snapshot.save(update_fields=["revoked_at", "revoked_by"])
        record_audit_event(
            action="reports.business_review_snapshot.revoke",
            module="reports",
            request=request,
            obj=snapshot,
            warehouse_id=(snapshot.warehouse_ids[0] if len(snapshot.warehouse_ids) == 1 else None),
            metadata={"warehouse_ids": snapshot.warehouse_ids},
        )
        return Response({"id": snapshot.id, "revoked_at": snapshot.revoked_at})

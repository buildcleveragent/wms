from __future__ import annotations

import datetime
import io
from decimal import Decimal
from openpyxl import Workbook

from django.http import HttpResponse
from django.db import transaction
from django.db.models import Count, Q, QuerySet, Sum
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .enums import AccrualStatus, PeriodStatus
from .models import (
    Bill,
    BillingAccrual,
    BillingEvent,
    BillingMetricDaily,
    BillingPeriod,
    BillingRule,
    BillingRuleTier,
)
from .serializers import (
    BillDetailSerializer,
    BillListSerializer,
    BillingAccrualSerializer,
    BillingEventSerializer,
    BillingMetricGenerateSerializer,
    BillingMetricDailySerializer,
    BillingPeriodInvoiceSerializer,
    BillingPeriodSerializer,
    BillingRuleSerializer,
    BillingRuleTierSerializer,
)
from .services import (
    accrue_metrics_for_date,
    accrue_order_processing_from_posted,
    accrue_storage_for_date,
    generate_metrics_for_date,
    generate_metrics_for_range,
    generate_invoice_for_period,
    lock_period,
)


class OwnerWarehouseScopedQuerysetMixin:
    permission_classes = [permissions.IsAuthenticated]

    def scope_queryset(self, qs: QuerySet):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return qs.none()
        if getattr(user, "is_superuser", False):
            return qs

        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        model = qs.model
        if owner_id and hasattr(model, "owner_id"):
            qs = qs.filter(owner_id=owner_id)
        if warehouse_id and hasattr(model, "warehouse_id"):
            qs = qs.filter(warehouse_id=warehouse_id)
        return qs

    def get_queryset(self):  # type: ignore[override]
        qs = super().get_queryset()  # type: ignore[misc]
        return self.scope_queryset(qs)


class OwnerWarehouseSaveMixin:
    def _save_scope_kwargs(self, serializer):
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return {}

        extra = {}
        if "owner" in serializer.fields and getattr(user, "owner", None) is not None:
            extra["owner"] = user.owner
        if "warehouse" in serializer.fields and getattr(user, "warehouse", None) is not None:
            extra["warehouse"] = user.warehouse
        return extra

    def perform_create(self, serializer):
        serializer.save(**self._save_scope_kwargs(serializer))

    def perform_update(self, serializer):
        serializer.save(**self._save_scope_kwargs(serializer))


class BillingRuleViewSet(OwnerWarehouseScopedQuerysetMixin, OwnerWarehouseSaveMixin, viewsets.ModelViewSet):
    queryset = BillingRule.objects.select_related("owner", "warehouse").prefetch_related("tiers").all()
    serializer_class = BillingRuleSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "charge_type": ["exact", "in"],
        "calc_method": ["exact", "in"],
        "active": ["exact"],
        "bundle_key": ["exact", "icontains"],
        "effective_from": ["exact", "gte", "lte"],
        "effective_to": ["exact", "gte", "lte"],
    }
    search_fields = ["note", "bundle_key"]
    ordering_fields = ["id", "priority", "effective_from", "effective_to"]
    ordering = ["owner_id", "priority", "id"]

    def scope_queryset(self, qs: QuerySet):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return qs.none()
        if getattr(user, "is_superuser", False):
            return qs

        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if owner_id:
            qs = qs.filter(Q(owner_id=owner_id) | Q(owner__isnull=True))
        if warehouse_id:
            qs = qs.filter(Q(warehouse_id=warehouse_id) | Q(warehouse__isnull=True))
        return qs

    def _validate_rule_write_scope(self, rule: BillingRule):
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return

        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if owner_id and rule.owner_id != owner_id:
            raise PermissionDenied("无权修改通用规则或其他货主规则。")
        if warehouse_id and rule.warehouse_id != warehouse_id:
            raise PermissionDenied("无权修改通用规则或其他仓库规则。")

    def perform_update(self, serializer):
        self._validate_rule_write_scope(serializer.instance)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        self._validate_rule_write_scope(instance)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        rule = self.get_object()
        self._validate_rule_write_scope(rule)
        rule.active = True
        rule.save(update_fields=["active"])
        return Response(self.get_serializer(rule).data)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        rule = self.get_object()
        self._validate_rule_write_scope(rule)
        rule.active = False
        rule.save(update_fields=["active"])
        return Response(self.get_serializer(rule).data)


class BillingRuleTierViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = BillingRuleTier.objects.select_related("rule", "rule__owner", "rule__warehouse").all()
    serializer_class = BillingRuleTierSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = {
        "rule": ["exact"],
        "rule__owner": ["exact"],
        "rule__warehouse": ["exact"],
    }
    ordering_fields = ["id", "threshold_from", "threshold_to"]
    ordering = ["rule_id", "threshold_from", "id"]

    def scope_queryset(self, qs: QuerySet):
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return qs.none()
        if getattr(user, "is_superuser", False):
            return qs

        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if owner_id:
            qs = qs.filter(Q(rule__owner_id=owner_id) | Q(rule__owner__isnull=True))
        if warehouse_id:
            qs = qs.filter(Q(rule__warehouse_id=warehouse_id) | Q(rule__warehouse__isnull=True))
        return qs

    def _validate_rule_scope(self, rule):
        user = self.request.user
        if not user.is_superuser:
            owner_id = getattr(user, "owner_id", None)
            warehouse_id = getattr(user, "warehouse_id", None)
            if owner_id and rule.owner_id != owner_id:
                raise PermissionDenied("无权操作通用规则或其他货主规则的阶梯。")
            if warehouse_id and rule.warehouse_id != warehouse_id:
                raise PermissionDenied("无权操作通用规则或其他仓库规则的阶梯。")

    def perform_create(self, serializer):
        rule = serializer.validated_data["rule"]
        self._validate_rule_scope(rule)
        serializer.save()

    def perform_update(self, serializer):
        rule = serializer.validated_data.get("rule", serializer.instance.rule)
        self._validate_rule_scope(rule)
        serializer.save()

    def perform_destroy(self, instance):
        self._validate_rule_scope(instance.rule)
        instance.delete()


class BillingMetricDailyViewSet(OwnerWarehouseScopedQuerysetMixin, OwnerWarehouseSaveMixin, viewsets.ModelViewSet):
    queryset = BillingMetricDaily.objects.select_related("owner", "warehouse").all()
    serializer_class = BillingMetricDailySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "service_date": ["exact", "gte", "lte"],
        "metric_type": ["exact", "in"],
    }
    search_fields = ["source", "note"]
    ordering_fields = ["id", "service_date", "metric_type", "created_at"]
    ordering = ["-service_date", "metric_type", "-id"]

    @action(detail=False, methods=["post"], url_path="generate")
    @transaction.atomic
    def generate(self, request):
        payload = BillingMetricGenerateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        owner_id = data.get("owner") or getattr(request.user, "owner_id", None)
        warehouse_id = data.get("warehouse") or getattr(request.user, "warehouse_id", None)
        if not owner_id or not warehouse_id:
            return Response(
                {"detail": "必须提供 owner 和 warehouse，或让当前用户绑定 owner/warehouse。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not getattr(request.user, "is_superuser", False):
            if getattr(request.user, "owner_id", None) and owner_id != request.user.owner_id:
                raise PermissionDenied("无权为其他货主生成计费指标。")
            if getattr(request.user, "warehouse_id", None) and warehouse_id != request.user.warehouse_id:
                raise PermissionDenied("无权为其他仓库生成计费指标。")

        summary = generate_metrics_for_range(
            owner_id,
            warehouse_id,
            data["start_date"],
            data["end_date"],
            metric_types=data.get("metric_types"),
            overwrite=data.get("overwrite", False),
            allow_area_fallback=data.get("allow_area_fallback", False),
        )
        return Response(summary)


class BillingEventViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = (
        BillingEvent.objects.select_related("owner", "warehouse", "task", "task_line", "scan_log", "posting_journal")
        .all()
    )
    serializer_class = BillingEventSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "charge_type": ["exact", "in"],
        "service_date": ["exact", "gte", "lte"],
        "task": ["exact"],
        "task_line": ["exact"],
        "scan_log": ["exact"],
    }
    search_fields = ["event_fp"]
    ordering_fields = ["id", "service_date", "created_at"]
    ordering = ["-service_date", "-id"]


class BillingAccrualViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = (
        BillingAccrual.objects.select_related("owner", "warehouse", "period", "rule", "event", "created_by")
        .all()
    )
    serializer_class = BillingAccrualSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "period": ["exact", "isnull"],
        "charge_type": ["exact", "in"],
        "rule": ["exact"],
        "service_date": ["exact", "gte", "lte"],
        "status": ["exact", "in"],
        "bundle_key": ["exact", "icontains"],
    }
    search_fields = ["acc_fingerprint", "bundle_key", "event__event_fp"]
    ordering_fields = ["id", "service_date", "amount", "tax_amount", "created_at"]
    ordering = ["-service_date", "-id"]


class BillingPeriodViewSet(OwnerWarehouseScopedQuerysetMixin, OwnerWarehouseSaveMixin, viewsets.ModelViewSet):
    queryset = BillingPeriod.objects.select_related("owner", "warehouse").all()
    serializer_class = BillingPeriodSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "status": ["exact", "in"],
        "start_date": ["exact", "gte", "lte"],
        "end_date": ["exact", "gte", "lte"],
    }
    search_fields = ["label"]
    ordering_fields = ["id", "label", "start_date", "end_date"]
    ordering = ["-start_date", "-id"]

    def _guard_status(self, period: BillingPeriod, allowed_statuses):
        if period.status in allowed_statuses:
            return None
        allowed = ", ".join(allowed_statuses)
        return Response(
            {"detail": f"该操作仅允许在账期状态 {allowed} 时执行，当前为 {period.status}。"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _preview_queryset(self, period: BillingPeriod):
        base_qs = BillingAccrual.objects.select_related("rule", "event").filter(
            owner_id=period.owner_id,
            warehouse_id=period.warehouse_id,
        )
        if period.status == PeriodStatus.OPEN:
            return base_qs.filter(
                period__isnull=True,
                status=AccrualStatus.OPEN,
                service_date__gte=period.start_date,
                service_date__lte=period.end_date,
            )
        return base_qs.filter(period=period)

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        period = self.get_object()
        qs = self._preview_queryset(period)
        accruals = list(qs)

        data = {
            "period": self.get_serializer(period).data,
            "scope": "open_unlocked" if period.status == PeriodStatus.OPEN else "period_locked",
            "accrual_count": len(accruals),
            "quantity_total": sum((Decimal(a.quantity) for a in accruals), Decimal("0.0000")),
            "subtotal": sum((Decimal(a.amount) for a in accruals), Decimal("0.00")),
            "tax_total": sum((Decimal(a.tax_amount) for a in accruals), Decimal("0.00")),
            "by_charge_type": list(
                qs.values("charge_type")
                .annotate(accrual_count=Count("id"), subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
                .order_by("charge_type")
            ),
            "by_status": list(
                qs.values("status")
                .annotate(accrual_count=Count("id"), subtotal=Sum("amount"))
                .order_by("status")
            ),
            "by_service_date": list(
                qs.values("service_date")
                .annotate(accrual_count=Count("id"), subtotal=Sum("amount"))
                .order_by("service_date")
            ),
        }
        return Response(data)

    @action(detail=True, methods=["post"], url_path="generate-metrics")
    @transaction.atomic
    def generate_metrics(self, request, pk=None):
        period = self.get_object()
        summary = generate_metrics_for_range(
            period.owner_id,
            period.warehouse_id,
            period.start_date,
            period.end_date,
            metric_types=request.data.get("metric_types"),
            overwrite=bool(request.data.get("overwrite", False)),
            allow_area_fallback=bool(request.data.get("allow_area_fallback", False)),
        )
        return Response({"period": self.get_serializer(period).data, "summary": summary})

    @action(detail=True, methods=["post"], url_path="accrue-storage")
    @transaction.atomic
    def accrue_storage(self, request, pk=None):
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.OPEN])
        if blocked is not None:
            return blocked

        service_date = period.start_date
        total_events = 0
        total_accruals = 0
        total_metrics_created = 0
        total_metrics_updated = 0
        while service_date <= period.end_date:
            metric_summary = generate_metrics_for_date(
                period.owner_id,
                period.warehouse_id,
                service_date,
                allow_area_fallback=bool(request.data.get("allow_area_fallback", False)),
            )
            total_metrics_created += metric_summary["created"]
            total_metrics_updated += metric_summary["updated"]
            ev1, acc1 = accrue_storage_for_date(
                period.owner_id,
                period.warehouse_id,
                service_date,
                by_user=request.user,
            )
            ev2, acc2 = accrue_metrics_for_date(
                period.owner_id,
                period.warehouse_id,
                service_date,
                by_user=request.user,
            )
            total_events += ev1 + ev2
            total_accruals += acc1 + acc2
            service_date += datetime.timedelta(days=1)

        return Response(
            {
                "period": self.get_serializer(period).data,
                "events_created": total_events,
                "accruals_created": total_accruals,
                "metrics_created": total_metrics_created,
                "metrics_updated": total_metrics_updated,
            }
        )

    @action(detail=True, methods=["post"], url_path="accrue-orders-posted")
    @transaction.atomic
    def accrue_orders_posted(self, request, pk=None):
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.OPEN])
        if blocked is not None:
            return blocked

        events_created, accruals_created = accrue_order_processing_from_posted(
            period.owner_id,
            period.warehouse_id,
            period.start_date,
            period.end_date,
            by_user=request.user,
        )
        return Response(
            {
                "period": self.get_serializer(period).data,
                "events_created": events_created,
                "accruals_created": accruals_created,
            }
        )

    @action(detail=True, methods=["post"], url_path="lock")
    @transaction.atomic
    def lock(self, request, pk=None):
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.OPEN])
        if blocked is not None:
            return blocked

        locked_period = lock_period(
            period.owner_id,
            period.warehouse_id,
            period.label,
            period.start_date,
            period.end_date,
        )
        return Response(self.get_serializer(locked_period).data)

    @action(detail=True, methods=["post"], url_path="invoice")
    @transaction.atomic
    def invoice(self, request, pk=None):
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.CLOSED])
        if blocked is not None:
            return blocked

        payload = BillingPeriodInvoiceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        invoice_no = payload.validated_data.get("invoice_no")
        if not invoice_no:
            seq = Bill.objects.filter(period__owner=period.owner, period__warehouse=period.warehouse).count() + 1
            invoice_no = f"INV-{period.label}-{period.owner_id}-{seq:04d}"

        bill = generate_invoice_for_period(
            period,
            invoice_no=invoice_no,
            issue_date=payload.validated_data.get("issue_date"),
            due_date=payload.validated_data.get("due_date"),
        )
        return Response(BillDetailSerializer(bill, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BillViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Bill.objects.select_related("owner", "warehouse", "period").prefetch_related("lines__accrual").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "period": ["exact"],
        "status": ["exact", "in"],
        "issue_date": ["exact", "gte", "lte"],
        "due_date": ["exact", "gte", "lte"],
    }
    search_fields = ["invoice_no", "memo", "period__label"]
    ordering_fields = ["id", "issue_date", "due_date", "subtotal", "tax_total", "total"]
    ordering = ["-issue_date", "-id"]

    def get_serializer_class(self):  # type: ignore[override]
        if self.action == "retrieve":
            return BillDetailSerializer
        return BillListSerializer

    def _xlsx_response(self, workbook: Workbook, filename: str) -> HttpResponse:
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="export")
    def export_list(self, request):
        qs = self.filter_queryset(self.get_queryset())

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Bills"
        sheet.append(
            [
                "Invoice No",
                "Status",
                "Owner",
                "Warehouse",
                "Period",
                "Issue Date",
                "Due Date",
                "Currency",
                "Subtotal",
                "Tax Total",
                "Total",
                "Line Count",
                "Memo",
            ]
        )

        for bill in qs:
            lines = list(bill.lines.all())
            sheet.append(
                [
                    bill.invoice_no,
                    bill.status,
                    getattr(bill.owner, "name", "") if bill.owner_id else "",
                    getattr(bill.warehouse, "name", "") if bill.warehouse_id else "",
                    getattr(bill.period, "label", "") if bill.period_id else "",
                    bill.issue_date.isoformat() if bill.issue_date else "",
                    bill.due_date.isoformat() if bill.due_date else "",
                    bill.currency,
                    Decimal(bill.subtotal),
                    Decimal(bill.tax_total),
                    Decimal(bill.total),
                    len(lines),
                    bill.memo or "",
                ]
            )

        return self._xlsx_response(
            workbook,
            f"billing-bills-{datetime.date.today().isoformat()}.xlsx",
        )

    @action(detail=True, methods=["get"], url_path="export")
    def export_detail(self, request, pk=None):
        bill = self.get_object()
        workbook = Workbook()

        summary_sheet = workbook.active
        summary_sheet.title = "Bill"
        summary_sheet.append(["Field", "Value"])
        summary_sheet.append(["Invoice No", bill.invoice_no])
        summary_sheet.append(["Status", bill.status])
        summary_sheet.append(["Owner", getattr(bill.owner, "name", "") if bill.owner_id else ""])
        summary_sheet.append(["Warehouse", getattr(bill.warehouse, "name", "") if bill.warehouse_id else ""])
        summary_sheet.append(["Period", getattr(bill.period, "label", "") if bill.period_id else ""])
        summary_sheet.append(["Issue Date", bill.issue_date.isoformat() if bill.issue_date else ""])
        summary_sheet.append(["Due Date", bill.due_date.isoformat() if bill.due_date else ""])
        summary_sheet.append(["Currency", bill.currency])
        summary_sheet.append(["Subtotal", Decimal(bill.subtotal)])
        summary_sheet.append(["Tax Total", Decimal(bill.tax_total)])
        summary_sheet.append(["Total", Decimal(bill.total)])
        summary_sheet.append(["Memo", bill.memo or ""])

        lines_sheet = workbook.create_sheet("Lines")
        lines_sheet.append(
            [
                "Service Date",
                "Charge Type",
                "Quantity",
                "Unit Price",
                "Amount",
                "Tax Amount",
                "Description",
                "Accrual Fingerprint",
            ]
        )
        for line in bill.lines.all():
            lines_sheet.append(
                [
                    line.service_date.isoformat() if line.service_date else "",
                    line.charge_type,
                    Decimal(line.quantity),
                    Decimal(line.unit_price),
                    Decimal(line.amount),
                    Decimal(line.tax_amount),
                    line.description or "",
                    getattr(line.accrual, "acc_fingerprint", ""),
                ]
            )

        invoice_token = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in bill.invoice_no or f"bill-{bill.id}")
        return self._xlsx_response(workbook, f"{invoice_token or f'bill-{bill.id}'}.xlsx")

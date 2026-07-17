from __future__ import annotations

import datetime
import io
from decimal import Decimal
from openpyxl import Workbook

from django.http import HttpResponse
from django.db import transaction
from django.db.models import Count, Q, QuerySet, Sum
from django.utils.dateparse import parse_date
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.locations.models import Warehouse

from .enums import AccrualStatus, BillStatus, PeriodStatus
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
    BillingAccrualDetailSerializer,
    BillingAccrualSerializer,
    BillingEventSerializer,
    BillingMetricGenerateSerializer,
    BillingMetricDailySerializer,
    BillingPeriodInvoiceSerializer,
    BillingPeriodSerializer,
    BillingRuleSerializer,
    BillingRuleTierSerializer,
    UnlockPeriodSerializer,
)
from .services import (
    accrue_metrics_for_date,
    accrue_order_processing_from_posted,
    accrue_storage_for_date,
    generate_metrics_for_date,
    generate_metrics_for_range,
    generate_invoice_for_period,
    lock_period,
    preview_lock_period,
    unlock_period,
)
from .services.dashboard import build_warehouse_overview_payload


def _require_billing_perm(request, perm_codename: str):
    """检查用户是否有指定的 billing 权限，无则抛 PermissionDenied。"""
    if not request.user.has_perm(f"billing.{perm_codename}"):
        raise PermissionDenied(f"需要 billing.{perm_codename} 权限。")


def _require_financial_export_perm(request):
    if request.user.is_superuser:
        return
    if not (
        request.user.has_perm("reports.export_operations")
        or request.user.has_perm("accounts.export_operational_reports")
    ):
        raise PermissionDenied("需要运营报表导出权限。")


def _explicit_billing_scope(user) -> AccessScope:
    """Return the tenant scope accepted for a state-changing billing request.

    Billing is a financial write surface.  In particular, a legacy ``owner``
    or ``warehouse`` field on ``User`` is only a migration aid and must never
    decide where a rule, metric or period is written.  Require a current
    ``UserRoleScope`` row for every non-superuser mutation.
    """

    scope = AccessScope.for_user(user)
    if not scope.is_valid or scope.source != "user_role_scope":
        raise PermissionDenied("计费写入必须使用有效的显式角色范围授权。")
    return scope


def _scope_id(value):
    """Return a model instance's primary key without trusting raw input."""

    if value is None:
        return None
    return getattr(value, "pk", value)


class BillingDataPermission(permissions.DjangoModelPermissions):
    """Role-aware read access plus Django model permissions for mutations."""

    perms_map = {
        **permissions.DjangoModelPermissions.perms_map,
        "GET": [],
        "HEAD": [],
        "OPTIONS": [],
    }

    # DRF maps every custom POST action to ``add_<view-model>`` by default.
    # Billing actions are semantically different: lock/unlock changes a
    # period, while invoice creation creates a bill.  Map them explicitly so
    # a correctly authorized scoped finance user is neither blocked nor given
    # a broader create permission than the operation needs.
    action_permissions = {
        "activate": "billing.change_billingrule",
        "deactivate": "billing.change_billingrule",
        "generate_metrics": "billing.change_billingperiod",
        "accrue_storage": "billing.change_billingperiod",
        "accrue_orders_posted": "billing.change_billingperiod",
        "lock": "billing.change_billingperiod",
        "unlock": "billing.change_billingperiod",
        "invoice": "billing.add_bill",
        "generate": "billing.change_billingperiod",
        "preview_lock": "billing.change_billingperiod",
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        scope = AccessScope.for_user(request.user)
        if request.method in permissions.SAFE_METHODS:
            return scope.is_valid and (
                request.user.has_perm("accounts.view_owner_financials")
                or request.user.has_perm("reports.view_warehouse_finance")
            )
        required = self.action_permissions.get(getattr(view, "action", ""))
        if required:
            return (
                scope.is_valid
                and scope.source == "user_role_scope"
                and request.user.has_perm(required)
            )
        return super().has_permission(request, view)


class OwnerWarehouseScopedQuerysetMixin:
    permission_classes = [BillingDataPermission]

    def scope_queryset(self, qs: QuerySet):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        return AccessScope.for_user(user).filter_queryset(
            qs,
            owner_field=getattr(self, "scope_owner_field", "owner_id"),
            warehouse_field=getattr(self, "scope_warehouse_field", "warehouse_id"),
        )

    def get_queryset(self):  # type: ignore[override]
        qs = super().get_queryset()  # type: ignore[misc]
        return self.scope_queryset(qs)


class OwnerWarehouseSaveMixin:
    """Validate the *final* serializer tenant target before persisting it.

    ``serializer.validated_data`` is authoritative here.  Mutating a payload
    with the legacy bindings stored on ``User`` both hides the requested
    target and permits unbound users to choose a tenant.  This mixin is used
    only by models that carry direct ``owner`` and ``warehouse`` fields.
    """

    def _final_serializer_scope_ids(self, serializer):
        instance = getattr(serializer, "instance", None)
        data = serializer.validated_data
        owner = data.get("owner", getattr(instance, "owner", None))
        warehouse = data.get("warehouse", getattr(instance, "warehouse", None))
        return _scope_id(owner), _scope_id(warehouse)

    def _validate_serializer_write_scope(self, serializer):
        owner_id, warehouse_id = self._final_serializer_scope_ids(serializer)
        self._validate_owner_warehouse_write_scope(owner_id, warehouse_id)

    def _validate_owner_warehouse_write_scope(self, owner_id, warehouse_id):
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return
        scope = _explicit_billing_scope(user)
        if not owner_id or not warehouse_id or not scope.allows(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        ):
            raise PermissionDenied("无权写入目标货主或仓库的计费数据。")

    def perform_create(self, serializer):
        self._validate_serializer_write_scope(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_serializer_write_scope(serializer)
        serializer.save()

    def perform_destroy(self, instance):
        self._validate_owner_warehouse_write_scope(
            getattr(instance, "owner_id", None),
            getattr(instance, "warehouse_id", None),
        )
        instance.delete()


class BillingWarehouseOverviewApi(OwnerWarehouseScopedQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _scope_dashboard_queryset(self, qs: QuerySet, *, scope: AccessScope):
        return scope.filter_queryset(
            qs,
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )

    def _resolve_scope_label(self, model_cls, pk):
        if not pk:
            return ""
        obj = model_cls.objects.filter(pk=pk).only("name").first()
        if obj is None:
            return ""
        return getattr(obj, "name", "")

    def get(self, request):
        owner_raw = (request.query_params.get("owner") or "").strip()
        warehouse_raw = (request.query_params.get("warehouse") or "").strip()
        charge_type = (request.query_params.get("charge_type") or "").strip()
        accrual_status = (request.query_params.get("status") or "").strip()
        date_from_raw = (request.query_params.get("date_from") or "").strip()
        date_to_raw = (request.query_params.get("date_to") or "").strip()
        recent_limit_raw = (request.query_params.get("recent_limit") or "").strip()
        # The client may choose presentation filters, never the authorization
        # mode.  The server resolves owner-vs-warehouse access from role scope.
        scope = AccessScope.for_user(request.user)
        if not scope.is_valid:
            raise PermissionDenied("No valid billing data scope.")
        if not request.user.is_superuser:
            if scope.warehouse_ids and not request.user.has_perm("reports.view_warehouse_finance"):
                raise PermissionDenied("No permission to view warehouse financial data.")
            if scope.owner_ids and not request.user.has_perm("accounts.view_owner_financials"):
                raise PermissionDenied("No permission to view owner financial data.")

        if owner_raw and not owner_raw.isdigit():
            return Response({"detail": "owner must be an integer id."}, status=status.HTTP_400_BAD_REQUEST)
        if warehouse_raw and not warehouse_raw.isdigit():
            return Response({"detail": "warehouse must be an integer id."}, status=status.HTTP_400_BAD_REQUEST)

        owner_id = int(owner_raw) if owner_raw else None
        warehouse_id = int(warehouse_raw) if warehouse_raw else None

        if scope.owner_ids and owner_id and not scope.allows(owner_id=owner_id):
            raise PermissionDenied("No access to other owners in billing dashboard.")
        if warehouse_id and not scope.allows(warehouse_id=warehouse_id):
            raise PermissionDenied("No access to other warehouses in billing dashboard.")

        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        if date_from_raw and date_from is None:
            return Response({"detail": "date_from must be YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if date_to_raw and date_to is None:
            return Response({"detail": "date_to must be YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if date_from and date_to and date_from > date_to:
            return Response({"detail": "date_from cannot be after date_to."}, status=status.HTTP_400_BAD_REQUEST)

        recent_limit = int(recent_limit_raw) if recent_limit_raw.isdigit() else 10
        recent_limit = max(1, min(recent_limit, 50))

        base_accrual_qs = self._scope_dashboard_queryset(
            BillingAccrual.objects.select_related("owner", "warehouse", "period", "rule", "event", "created_by")
            .filter(is_reversal=False)
            .exclude(status=AccrualStatus.VOID),
            scope=scope,
        )
        base_bill_qs = self._scope_dashboard_queryset(
            Bill.objects.select_related("owner", "warehouse", "period")
            .prefetch_related("lines")
            .exclude(status=BillStatus.VOID),
            scope=scope,
        )

        option_period_qs = self._scope_dashboard_queryset(
            BillingPeriod.objects.select_related("owner", "warehouse"),
            scope=scope,
        )
        option_accrual_qs = base_accrual_qs
        option_bill_qs = self._scope_dashboard_queryset(
            Bill.objects.select_related("owner", "warehouse").exclude(status=BillStatus.VOID),
            scope=scope,
        )

        if warehouse_id:
            base_accrual_qs = base_accrual_qs.filter(warehouse_id=warehouse_id)
            base_bill_qs = base_bill_qs.filter(warehouse_id=warehouse_id)
            option_period_qs = option_period_qs.filter(warehouse_id=warehouse_id)
            option_accrual_qs = option_accrual_qs.filter(warehouse_id=warehouse_id)
            option_bill_qs = option_bill_qs.filter(warehouse_id=warehouse_id)

        owner_options_map = {}
        for row in option_period_qs.values("owner_id", "owner__name").distinct():
            owner_options_map[row["owner_id"]] = {
                "id": row["owner_id"],
                "name": row["owner__name"] or f"Owner #{row['owner_id']}",
            }
        for row in option_accrual_qs.values("owner_id", "owner__name").distinct():
            owner_options_map[row["owner_id"]] = {
                "id": row["owner_id"],
                "name": row["owner__name"] or f"Owner #{row['owner_id']}",
            }
        for row in option_bill_qs.values("owner_id", "owner__name").distinct():
            owner_options_map[row["owner_id"]] = {
                "id": row["owner_id"],
                "name": row["owner__name"] or f"Owner #{row['owner_id']}",
            }
        owner_options = sorted(owner_options_map.values(), key=lambda item: (item["name"], item["id"]))

        if owner_id:
            base_accrual_qs = base_accrual_qs.filter(owner_id=owner_id)
            base_bill_qs = base_bill_qs.filter(owner_id=owner_id)

        if charge_type:
            base_accrual_qs = base_accrual_qs.filter(charge_type=charge_type)

        if accrual_status:
            base_accrual_qs = base_accrual_qs.filter(status=accrual_status)

        if date_from:
            base_accrual_qs = base_accrual_qs.filter(service_date__gte=date_from)
            base_bill_qs = base_bill_qs.filter(period__end_date__gte=date_from)

        if date_to:
            base_accrual_qs = base_accrual_qs.filter(service_date__lte=date_to)
            base_bill_qs = base_bill_qs.filter(period__start_date__lte=date_to)

        scope_owner_id = owner_id or (
            next(iter(scope.owner_ids)) if len(scope.owner_ids) == 1 else None
        )
        scope_owner_name = ""
        if scope_owner_id:
            scope_owner_name = owner_options_map.get(scope_owner_id, {}).get("name", "")
            if (
                not scope_owner_name
                and scope_owner_id == getattr(request.user, "owner_id", None)
                and getattr(request.user, "owner", None) is not None
            ):
                scope_owner_name = request.user.owner.name

        payload = build_warehouse_overview_payload(
            accrual_qs=base_accrual_qs,
            bill_qs=base_bill_qs,
            recent_limit=recent_limit,
        )
        payload["scope"] = {
            "owner": scope_owner_id,
            "owner_name": scope_owner_name,
            "warehouse": warehouse_id or (
                next(iter(scope.warehouse_ids)) if len(scope.warehouse_ids) == 1 else None
            ),
            "warehouse_name": self._resolve_scope_label(
                Warehouse,
                warehouse_id or (
                    next(iter(scope.warehouse_ids)) if len(scope.warehouse_ids) == 1 else None
                ),
            ),
            "date_from": date_from,
            "date_to": date_to,
            "charge_type": charge_type,
            "status": accrual_status,
        }
        payload["owner_options"] = owner_options
        return Response(payload)


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
        scope = AccessScope.for_user(user)
        scoped = scope.filter_queryset(
            qs, owner_field="owner_id", warehouse_field="warehouse_id"
        )
        # Global rate cards carry no tenant data. They are intentionally
        # readable so a scoped finance user can understand which fallback
        # rule applies, but `_validate_rule_write_scope` still rejects every
        # mutation of them.
        if request and request.method in permissions.SAFE_METHODS and scope.is_valid:
            return qs.filter(
                Q(owner__isnull=True, warehouse__isnull=True)
                | Q(pk__in=scoped.values("pk"))
            )
        return scoped

    def _validate_rule_write_scope(self, rule: BillingRule):
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return
        scope = _explicit_billing_scope(user)
        if not scope.allows(
            owner_id=rule.owner_id, warehouse_id=rule.warehouse_id
        ):
            raise PermissionDenied("无权修改通用规则或范围外规则。")

    def perform_update(self, serializer):
        # The inherited mixin validates the post-patch owner/warehouse pair,
        # rather than only the row that happened to be retrieved.
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        self._validate_rule_write_scope(instance)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        _require_billing_perm(request, "change_billingrule")
        rule = self.get_object()
        self._validate_rule_write_scope(rule)
        rule.active = True
        rule.save(update_fields=["active"])
        return Response(self.get_serializer(rule).data)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        _require_billing_perm(request, "change_billingrule")
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
        scope = AccessScope.for_user(user)
        scoped = scope.filter_queryset(
            qs,
            owner_field="rule__owner_id",
            warehouse_field="rule__warehouse_id",
        )
        if self.request.method in permissions.SAFE_METHODS and scope.is_valid:
            return qs.filter(
                Q(rule__owner__isnull=True, rule__warehouse__isnull=True)
                | Q(pk__in=scoped.values("pk"))
            )
        return scoped

    def _validate_rule_scope(self, rule):
        user = self.request.user
        if user.is_superuser:
            return
        scope = _explicit_billing_scope(user)
        if not scope.allows(owner_id=rule.owner_id, warehouse_id=rule.warehouse_id):
            raise PermissionDenied("无权操作通用规则或范围外规则的阶梯。")

    def perform_create(self, serializer):
        _require_billing_perm(self.request, "change_billingrule")
        rule = serializer.validated_data["rule"]
        self._validate_rule_scope(rule)
        serializer.save()

    def perform_update(self, serializer):
        _require_billing_perm(self.request, "change_billingrule")
        rule = serializer.validated_data.get("rule", serializer.instance.rule)
        self._validate_rule_scope(rule)
        serializer.save()

    def perform_destroy(self, instance):
        _require_billing_perm(self.request, "change_billingrule")
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

        if request.user.is_superuser:
            scope = AccessScope.for_user(request.user)
        else:
            scope = _explicit_billing_scope(request.user)

        # Defaults may only come from an unambiguous explicit role scope.
        # Never fall back to User.owner/User.warehouse, which may be stale or
        # deliberately populated for another application workflow.
        owner_id = data.get("owner") or (
            next(iter(scope.owner_ids)) if len(scope.owner_ids) == 1 else None
        )
        warehouse_id = data.get("warehouse") or (
            next(iter(scope.warehouse_ids)) if len(scope.warehouse_ids) == 1 else None
        )
        if not owner_id or not warehouse_id:
            return Response(
                {"detail": "必须提供 owner 和 warehouse，或由显式角色范围唯一确定。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not scope.allows(owner_id=owner_id, warehouse_id=warehouse_id):
            raise PermissionDenied("无权为范围外货主或仓库生成计费指标。")

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
        BillingAccrual.objects.select_related(
            "owner",
            "warehouse",
            "period",
            "rule",
            "event",
            "event__task",
            "created_by",
        )
        .prefetch_related("billline_set__bill")
        .all()
    )
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "period": ["exact", "isnull"],
        "charge_type": ["exact", "in"],
        "rule": ["exact"],
        "service_date": ["exact", "gte", "lte"],
        "status": ["exact", "in"],
        "event": ["exact"],
        "is_reversal": ["exact"],
        "reversal_of": ["exact"],
        "bundle_key": ["exact", "icontains"],
    }
    search_fields = ["acc_fingerprint", "bundle_key", "event__event_fp"]
    ordering_fields = ["id", "service_date", "amount", "tax_amount", "created_at"]
    ordering = ["-service_date", "-id"]

    def get_serializer_class(self):  # type: ignore[override]
        if self.action == "retrieve":
            return BillingAccrualDetailSerializer
        return BillingAccrualSerializer


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
        _require_billing_perm(request, "change_billingperiod")
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
        _require_billing_perm(request, "change_billingperiod")
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
        _require_billing_perm(request, "change_billingperiod")
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
        _require_billing_perm(request, "change_billingperiod")
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.OPEN])
        if blocked is not None:
            return blocked

        try:
            locked_period = lock_period(
                period.owner_id,
                period.warehouse_id,
                period.label,
                period.start_date,
                period.end_date,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(locked_period).data)

    @action(detail=True, methods=["post"], url_path="invoice")
    @transaction.atomic
    def invoice(self, request, pk=None):
        _require_billing_perm(request, "add_bill")
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.CLOSED])
        if blocked is not None:
            return blocked

        payload = BillingPeriodInvoiceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        invoice_no = payload.validated_data.get("invoice_no")
        if not invoice_no:
            seq = Bill.objects.filter(period__owner=period.owner, period__warehouse=period.warehouse).count() + 1
            invoice_no = f"INV-{period.label}-{period.owner_id}-{period.warehouse_id}-{seq:04d}"

        try:
            bill = generate_invoice_for_period(
                period,
                invoice_no=invoice_no,
                issue_date=payload.validated_data.get("issue_date"),
                due_date=payload.validated_data.get("due_date"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(BillDetailSerializer(bill, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="preview-lock")
    def preview_lock(self, request, pk=None):
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.OPEN])
        if blocked is not None:
            return blocked

        result = preview_lock_period(
            period.owner_id,
            period.warehouse_id,
            period.label,
            period.start_date,
            period.end_date,
        )
        return Response(result)

    @action(detail=True, methods=["post"], url_path="unlock")
    @transaction.atomic
    def unlock(self, request, pk=None):
        _require_billing_perm(request, "change_billingperiod")
        period = self.get_object()
        blocked = self._guard_status(period, [PeriodStatus.CLOSED, PeriodStatus.INVOICED])
        if blocked is not None:
            return blocked

        payload = UnlockPeriodSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            result = unlock_period(
                period,
                by_user=request.user,
                reason=payload.validated_data.get("reason", ""),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


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
        _require_financial_export_perm(request)
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

        response = self._xlsx_response(
            workbook,
            f"billing-bills-{datetime.date.today().isoformat()}.xlsx",
        )
        record_audit_event(
            action="EXPORT",
            module="billing.bill.list",
            request=request,
            metadata={"rows": qs.count()},
        )
        return response

    @action(detail=True, methods=["get"], url_path="export")
    def export_detail(self, request, pk=None):
        _require_financial_export_perm(request)
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

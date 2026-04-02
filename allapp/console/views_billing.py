from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.generic import TemplateView

from allapp.baseinfo.models import Owner
from allapp.billing.enums import AccrualStatus, ChargeType, PeriodStatus
from allapp.billing.models import Bill, BillingAccrual, BillingPeriod
from allapp.locations.models import Warehouse

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.0000")

CHARGE_TYPE_LABELS = dict(ChargeType.choices)
ACCRUAL_STATUS_LABELS = dict(AccrualStatus.choices)


def _decimal_or_zero(value, default=ZERO_MONEY):
    return default if value is None else value


def _build_query(**params) -> str:
    payload = {key: value for key, value in params.items() if value not in (None, "", [])}
    return urlencode(payload)


def _preview_queryset(period: BillingPeriod):
    qs = BillingAccrual.objects.select_related("rule", "event").filter(
        owner_id=period.owner_id,
        warehouse_id=period.warehouse_id,
    )
    if period.status == PeriodStatus.OPEN:
        return qs.filter(
            period__isnull=True,
            status=AccrualStatus.OPEN,
            service_date__gte=period.start_date,
            service_date__lte=period.end_date,
        )
    return qs.filter(period=period)


class BillingConsoleBaseView(LoginRequiredMixin, TemplateView):
    required_permissions: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        if not self._has_page_access():
            raise PermissionDenied("无权访问计费控制台页面。")
        return super().dispatch(request, *args, **kwargs)

    def _has_page_access(self) -> bool:
        user = self.request.user
        return bool(
            getattr(user, "is_superuser", False)
            or any(user.has_perm(permission) for permission in self.required_permissions)
        )

    def _user_can_view_bills(self) -> bool:
        user = self.request.user
        return bool(getattr(user, "is_superuser", False) or user.has_perm("billing.view_bill"))

    def _scoped_owner_id(self):
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return None
        return getattr(user, "owner_id", None)

    def _scoped_warehouse_id(self):
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return None
        return getattr(user, "warehouse_id", None)

    def _validate_scoped_param(self, param_name: str, scoped_value):
        raw_value = self.request.GET.get(param_name)
        if not raw_value:
            return None
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None
        if scoped_value and value != scoped_value:
            raise PermissionDenied("无权查看其他货主或仓库的计费数据。")
        return value

    def _scope_queryset(self, qs, *, owner_field: str = "owner_id", warehouse_field: str = "warehouse_id"):
        owner_id = self._scoped_owner_id()
        warehouse_id = self._scoped_warehouse_id()
        if owner_id:
            qs = qs.filter(**{owner_field: owner_id})
        if warehouse_id:
            qs = qs.filter(**{warehouse_field: warehouse_id})
        return qs

    def _owner_queryset(self):
        qs = Owner.objects.order_by("name", "id")
        owner_id = self._scoped_owner_id()
        if owner_id:
            qs = qs.filter(id=owner_id)
        return qs

    def _warehouse_queryset(self):
        qs = Warehouse.objects.order_by("code", "id")
        warehouse_id = self._scoped_warehouse_id()
        if warehouse_id:
            qs = qs.filter(id=warehouse_id)
        return qs


class BillingOverviewView(BillingConsoleBaseView):
    template_name = "console/billing/overview.html"
    required_permissions = (
        "billing.view_bill",
        "billing.view_billingperiod",
        "billing.view_billingaccrual",
    )

    def _charge_summary_rows(self, qs, *, current_bill: Bill | None, owner_id, warehouse_id, period_id):
        rows = []
        data = (
            qs.values("charge_type")
            .annotate(accrual_count=Count("id"), subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
            .order_by("charge_type")
        )
        for row in data:
            subtotal = _decimal_or_zero(row["subtotal"])
            tax_total = _decimal_or_zero(row["tax_total"])
            detail_url = ""
            if current_bill and self._user_can_view_bills():
                query = _build_query(
                    owner=owner_id,
                    warehouse=warehouse_id,
                    period=period_id,
                    charge_type=row["charge_type"],
                )
                base_url = reverse("console:billing_bill_detail", args=[current_bill.id])
                detail_url = (
                    f"{base_url}?{query}"
                    if query
                    else base_url
                )
            rows.append(
                {
                    "charge_type": row["charge_type"],
                    "label": CHARGE_TYPE_LABELS.get(row["charge_type"], row["charge_type"]),
                    "accrual_count": row["accrual_count"],
                    "subtotal": subtotal,
                    "tax_total": tax_total,
                    "total": subtotal + tax_total,
                    "detail_url": detail_url,
                }
            )
        rows.sort(key=lambda item: (-item["total"], item["charge_type"]))
        return rows

    def _status_summary_rows(self, qs):
        rows = []
        data = (
            qs.values("status")
            .annotate(accrual_count=Count("id"), subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
            .order_by("status")
        )
        for row in data:
            subtotal = _decimal_or_zero(row["subtotal"])
            tax_total = _decimal_or_zero(row["tax_total"])
            rows.append(
                {
                    "status": row["status"],
                    "label": ACCRUAL_STATUS_LABELS.get(row["status"], row["status"]),
                    "accrual_count": row["accrual_count"],
                    "subtotal": subtotal,
                    "tax_total": tax_total,
                    "total": subtotal + tax_total,
                }
            )
        return rows

    def _trend_rows(self, qs, *, current_bill: Bill | None, owner_id, warehouse_id, period_id):
        rows = []
        data = (
            qs.values("service_date")
            .annotate(accrual_count=Count("id"), subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
            .order_by("service_date")
        )
        for row in data:
            subtotal = _decimal_or_zero(row["subtotal"])
            tax_total = _decimal_or_zero(row["tax_total"])
            detail_url = ""
            if current_bill and self._user_can_view_bills():
                query = _build_query(
                    owner=owner_id,
                    warehouse=warehouse_id,
                    period=period_id,
                    date_from=row["service_date"].isoformat(),
                    date_to=row["service_date"].isoformat(),
                )
                base_url = reverse("console:billing_bill_detail", args=[current_bill.id])
                detail_url = (
                    f"{base_url}?{query}"
                    if query
                    else base_url
                )
            rows.append(
                {
                    "service_date": row["service_date"],
                    "accrual_count": row["accrual_count"],
                    "subtotal": subtotal,
                    "tax_total": tax_total,
                    "total": subtotal + tax_total,
                    "detail_url": detail_url,
                }
            )
        max_total = max((row["total"] for row in rows), default=ZERO_MONEY)
        for row in rows:
            row["bar_pct"] = 0 if max_total <= 0 else int((row["total"] / max_total) * 100)
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        selected_owner_id = self._validate_scoped_param("owner", self._scoped_owner_id())
        selected_warehouse_id = self._validate_scoped_param("warehouse", self._scoped_warehouse_id())
        selected_period_id = self._validate_scoped_param("period", None)

        periods = self._scope_queryset(BillingPeriod.objects.select_related("owner", "warehouse"))
        if selected_owner_id:
            periods = periods.filter(owner_id=selected_owner_id)
        if selected_warehouse_id:
            periods = periods.filter(warehouse_id=selected_warehouse_id)
        periods = periods.order_by("-start_date", "-id")

        selected_period = None
        if selected_period_id:
            selected_period = get_object_or_404(periods, pk=selected_period_id)
        else:
            selected_period = periods.first()

        if selected_period and not selected_owner_id:
            selected_owner_id = selected_period.owner_id
        if selected_period and not selected_warehouse_id:
            selected_warehouse_id = selected_period.warehouse_id

        charge_rows = []
        status_rows = []
        trend_rows = []
        recent_accruals = []
        current_bill = None
        summary = {
            "accrual_count": 0,
            "quantity_total": ZERO_QTY,
            "subtotal": ZERO_MONEY,
            "tax_total": ZERO_MONEY,
            "total": ZERO_MONEY,
        }

        if selected_period:
            preview_qs = _preview_queryset(selected_period)
            aggregates = preview_qs.aggregate(
                accrual_count=Count("id"),
                quantity_total=Sum("quantity"),
                subtotal=Sum("amount"),
                tax_total=Sum("tax_amount"),
            )
            summary = {
                "accrual_count": aggregates["accrual_count"] or 0,
                "quantity_total": _decimal_or_zero(aggregates["quantity_total"], ZERO_QTY),
                "subtotal": _decimal_or_zero(aggregates["subtotal"]),
                "tax_total": _decimal_or_zero(aggregates["tax_total"]),
            }
            summary["total"] = summary["subtotal"] + summary["tax_total"]

            if self._user_can_view_bills():
                current_bill = (
                    Bill.objects.select_related("period", "owner", "warehouse")
                    .filter(period=selected_period)
                    .order_by("-id")
                    .first()
                )
            charge_rows = self._charge_summary_rows(
                preview_qs,
                current_bill=current_bill,
                owner_id=selected_owner_id,
                warehouse_id=selected_warehouse_id,
                period_id=selected_period.id,
            )
            status_rows = self._status_summary_rows(preview_qs)
            trend_rows = self._trend_rows(
                preview_qs,
                current_bill=current_bill,
                owner_id=selected_owner_id,
                warehouse_id=selected_warehouse_id,
                period_id=selected_period.id,
            )
            recent_accruals = list(
                preview_qs.select_related("rule", "event")
                .order_by("-service_date", "-id")[:10]
            )

        detail_query = _build_query(
            owner=selected_owner_id,
            warehouse=selected_warehouse_id,
            period=selected_period.id if selected_period else None,
        )
        detail_url = ""
        if current_bill and self._user_can_view_bills():
            base_url = reverse("console:billing_bill_detail", args=[current_bill.id])
            detail_url = (
                f"{base_url}?{detail_query}"
                if detail_query
                else base_url
            )

        ctx.update(
            {
                "owners": self._owner_queryset(),
                "warehouses": self._warehouse_queryset(),
                "periods": periods,
                "selected_owner_id": selected_owner_id,
                "selected_warehouse_id": selected_warehouse_id,
                "selected_period": selected_period,
                "summary": summary,
                "charge_rows": charge_rows,
                "status_rows": status_rows,
                "trend_rows": trend_rows,
                "current_bill": current_bill,
                "recent_accruals": recent_accruals,
                "can_view_bill": self._user_can_view_bills(),
                "bill_detail_url": detail_url,
                "preview_scope_label": (
                    "开账账期内未锁定应计"
                    if selected_period and selected_period.status == PeriodStatus.OPEN
                    else "已锁定账期应计"
                ),
            }
        )
        return ctx


class BillingBillDetailView(BillingConsoleBaseView):
    template_name = "console/billing/bill_detail.html"
    required_permissions = ("billing.view_bill",)

    def _grouped_rows(self, qs):
        rows = []
        data = (
            qs.values("charge_type")
            .annotate(line_count=Count("id"), subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
            .order_by("charge_type")
        )
        for row in data:
            subtotal = _decimal_or_zero(row["subtotal"])
            tax_total = _decimal_or_zero(row["tax_total"])
            rows.append(
                {
                    "charge_type": row["charge_type"],
                    "label": CHARGE_TYPE_LABELS.get(row["charge_type"], row["charge_type"]),
                    "line_count": row["line_count"],
                    "subtotal": subtotal,
                    "tax_total": tax_total,
                    "total": subtotal + tax_total,
                }
            )
        rows.sort(key=lambda item: (-item["total"], item["charge_type"]))
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        bill = get_object_or_404(
            self._scope_queryset(Bill.objects.select_related("owner", "warehouse", "period")),
            pk=kwargs["pk"],
        )

        selected_charge_type = self.request.GET.get("charge_type") or ""
        if selected_charge_type not in CHARGE_TYPE_LABELS:
            selected_charge_type = ""

        date_from_raw = self.request.GET.get("date_from") or ""
        date_to_raw = self.request.GET.get("date_to") or ""
        keyword = (self.request.GET.get("q") or "").strip()
        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None

        lines_qs = bill.lines.select_related("accrual").order_by("service_date", "id")
        if selected_charge_type:
            lines_qs = lines_qs.filter(charge_type=selected_charge_type)
        if date_from:
            lines_qs = lines_qs.filter(service_date__gte=date_from)
        if date_to:
            lines_qs = lines_qs.filter(service_date__lte=date_to)
        if keyword:
            lines_qs = lines_qs.filter(
                Q(description__icontains=keyword)
                | Q(accrual__acc_fingerprint__icontains=keyword)
            )

        filtered_aggregates = lines_qs.aggregate(
            line_count=Count("id"),
            quantity_total=Sum("quantity"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        filtered_summary = {
            "line_count": filtered_aggregates["line_count"] or 0,
            "quantity_total": _decimal_or_zero(filtered_aggregates["quantity_total"], ZERO_QTY),
            "subtotal": _decimal_or_zero(filtered_aggregates["subtotal"]),
            "tax_total": _decimal_or_zero(filtered_aggregates["tax_total"]),
        }
        filtered_summary["total"] = filtered_summary["subtotal"] + filtered_summary["tax_total"]

        back_query = _build_query(
            owner=self.request.GET.get("owner") or bill.owner_id,
            warehouse=self.request.GET.get("warehouse") or bill.warehouse_id,
            period=self.request.GET.get("period") or bill.period_id,
        )

        ctx.update(
            {
                "bill": bill,
                "lines": list(lines_qs),
                "grouped_rows": self._grouped_rows(lines_qs),
                "filtered_summary": filtered_summary,
                "overall_line_count": bill.lines.count(),
                "selected_charge_type": selected_charge_type,
                "date_from": date_from_raw,
                "date_to": date_to_raw,
                "keyword": keyword,
                "charge_type_choices": ChargeType.choices,
                "back_query": back_query,
                "overview_url": (
                    f"{reverse('console:billing_overview')}?{back_query}"
                    if back_query
                    else reverse("console:billing_overview")
                ),
                "has_filters": any([selected_charge_type, date_from_raw, date_to_raw, keyword]),
            }
        )
        return ctx

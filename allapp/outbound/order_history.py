"""Strictly scoped, read-only outbound history and database aggregates."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Count,
    DecimalField,
    Exists,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authz import strict_order_queryset
from .history_products import history_product_q, product_ranking
from .models import OutboundOrder, OutboundOrderLine
from .order_filters import filter_orders
from .serializers import OutboundOrderReadSerializer


class HistoryParams(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    owner_id = serializers.IntegerField(required=False, min_value=1)
    warehouse_id = serializers.IntegerField(required=False, min_value=1)
    processing_mode = serializers.ChoiceField(
        required=False, choices=OutboundOrder.ProcessingMode.choices
    )
    outbound_type = serializers.ChoiceField(
        required=False, choices=OutboundOrder.OUTBOUND_TYPE_CHOICES
    )
    submit_status = serializers.ChoiceField(required=False, choices=OutboundOrder.SUBMIT_CHOICES)
    approval_status = serializers.ChoiceField(
        required=False, choices=OutboundOrder.APPROVAL_CHOICES
    )
    is_closed = serializers.BooleanField(required=False)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)
    top_n = serializers.IntegerField(required=False, min_value=1, max_value=50)

    def validate(self, attrs):
        now = timezone.now()
        today = timezone.localtime(now).date() if timezone.is_aware(now) else now.date()
        start, end = attrs.get("start_date"), attrs.get("end_date")
        if start is None and end is None:
            start = today if self.context.get("stats") else today - timedelta(days=29)
            end = today
        else:
            start, end = start or end, end or start
        if start > end:
            raise serializers.ValidationError("开始日期不能晚于结束日期。")
        if self.context.get("stats") and (end - start).days >= 366:
            raise serializers.ValidationError("单次统计范围不能超过366天。")
        return {**attrs, "start_date": start, "end_date": end}


class HistoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def annotated_orders(qs):
    lines = OutboundOrderLine.objects.filter(order_id=OuterRef("pk"), is_deleted=False).values(
        "order_id"
    )
    return qs.annotate(
        history_line_count=Coalesce(
            Subquery(lines.annotate(n=Count("pk")).values("n"), output_field=IntegerField()), 0
        ),
        history_total_qty=Coalesce(
            Subquery(lines.annotate(qty=Sum("base_qty")).values("qty")),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=24, decimal_places=3),
        ),
    )


def filtered_orders(qs, params):
    # Ordinary status/date filters are shared; history adds frozen product descriptions.
    shared = {
        k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items() if k != "search"
    }
    shared.update(
        date_from=params["start_date"].isoformat(), date_to=params["end_date"].isoformat()
    )
    qs = filter_orders(qs, shared)
    if params.get("processing_mode"):
        qs = qs.filter(processing_mode=params["processing_mode"])
    search = params.get("search", "").strip()
    if search:
        current_matches = filter_orders(qs, {"search": search}).order_by().values("pk")
        lines = OutboundOrderLine.objects.filter(order_id=OuterRef("pk"), is_deleted=False).filter(
            history_product_q(search)
        )
        qs = qs.alias(history_product_match=Exists(lines)).filter(
            Q(pk__in=current_matches)
            | Q(history_product_match=True)
            | Q(owner__name__icontains=search)
            | Q(owner__code__icontains=search)
        )
    return qs


def summary_row(order):
    return {
        "id": order.pk,
        "order_no": order.order_no,
        "biz_date": order.biz_date,
        "owner_id": order.owner_id,
        "owner_name": order.owner.name,
        "warehouse_id": order.warehouse_id,
        "warehouse_name": order.warehouse.name,
        "customer_name": order.customer.name if order.customer else "",
        "supplier_name": order.supplier.name if order.supplier else "",
        "contact": order.contact,
        "src_bill_no": order.src_bill_no,
        "processing_mode": order.processing_mode,
        "processing_mode_label": order.get_processing_mode_display(),
        "outbound_type": order.outbound_type,
        "outbound_type_label": order.get_outbound_type_display(),
        "submit_status": order.submit_status,
        "submit_status_name": order.get_submit_status_display(),
        "approval_status": order.approval_status,
        "approval_status_name": order.get_approval_status_display(),
        "is_closed": order.is_closed,
        "close_reason": order.close_reason,
        "line_count": order.history_line_count,
        "total_base_qty": str(order.history_total_qty),
    }


def aggregate_metrics():
    return {
        "order_count": Count("pk", distinct=True),
        "line_count": Sum("history_line_count"),
        "total_base_qty": Sum("history_total_qty"),
    }


def clean_metrics(row):
    return {
        **row,
        "line_count": row["line_count"] or 0,
        "total_base_qty": str(row["total_base_qty"] or 0),
    }


class OutboundHistoryViewSet(viewsets.GenericViewSet):
    """No write actions; every surface uses the same fail-closed order scope."""

    permission_classes = [IsAuthenticated]
    lookup_value_regex = r"\d+"
    pagination_class = HistoryPagination
    serializer_class = OutboundOrderReadSerializer

    def get_queryset(self):
        return strict_order_queryset(OutboundOrder.objects.all(), self.request.user)

    def params(self):
        raw = {key: value for key, value in self.request.query_params.items() if value != ""}
        serializer = HistoryParams(data=raw, context={"stats": self.action == "stats"})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def list(self, request):
        qs = annotated_orders(filtered_orders(self.get_queryset(), self.params()))
        qs = qs.select_related("owner", "warehouse", "customer", "supplier").order_by(
            "-biz_date", "-id"
        )
        page = self.paginate_queryset(qs)
        return self.get_paginated_response([summary_row(order) for order in page])

    def retrieve(self, request, pk=None):
        lines = (
            OutboundOrderLine.objects.filter(is_deleted=False)
            .select_related("product__brand", "base_uom", "aux_uom")
            .order_by("line_no", "id")
        )
        qs = (
            annotated_orders(self.get_queryset())
            .select_related(
                "owner",
                "warehouse",
                "customer",
                "supplier",
                "created_by",
                "priced_by",
                "approved_by_warehouse",
            )
            .prefetch_related(Prefetch("lines", queryset=lines))
        )
        order = get_object_or_404(qs, pk=pk)
        result = dict(self.get_serializer(order).data)
        result.update(summary_row(order))
        for row, line in zip(result["lines"], order.lines.all()):
            row["base_uom_name"] = line.base_uom.name if line.base_uom else ""
        return Response(result)

    @action(detail=False, methods=["get"], url_path="options")
    def history_options(self, request):
        qs = self.get_queryset().order_by()
        result = {}
        for key, field in (("owners", "owner"), ("warehouses", "warehouse")):
            rows = (
                qs.values(f"{field}_id", f"{field}__name")
                .distinct()
                .order_by(f"{field}__name", f"{field}_id")
            )
            result[key] = [
                {"id": row[f"{field}_id"], "name": row[f"{field}__name"]} for row in rows
            ]
        for key, choices in (
            ("processing_modes", OutboundOrder.ProcessingMode.choices),
            ("outbound_types", OutboundOrder.OUTBOUND_TYPE_CHOICES),
            ("submit_statuses", OutboundOrder.SUBMIT_CHOICES),
            ("approval_statuses", OutboundOrder.APPROVAL_CHOICES),
            ("closed_statuses", [("false", "未关闭"), ("true", "已关闭")]),
        ):
            result[key] = [{"value": value, "label": label} for value, label in choices]
        return Response(result)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        params = self.params()
        qs = annotated_orders(filtered_orders(self.get_queryset(), params)).order_by()
        result = {
            "period": {key: params[key].isoformat() for key in ("start_date", "end_date")},
            "summary": clean_metrics(qs.aggregate(**aggregate_metrics())),
            "product_rows": product_ranking(qs, params.get("top_n", 10)),
        }
        for key, field, choices in (
            ("submit_rows", "submit_status", OutboundOrder.SUBMIT_CHOICES),
            ("approval_rows", "approval_status", OutboundOrder.APPROVAL_CHOICES),
            ("closed_rows", "is_closed", [(False, "未关闭"), (True, "已关闭")]),
        ):
            counts = {
                row[field]: row["n"]
                for row in qs.values(field).annotate(n=Count("pk", distinct=True))
            }
            result[key] = [
                {"value": value, "label": label, "order_count": counts.get(value, 0)}
                for value, label in choices
            ]
        result["daily_rows"] = [
            clean_metrics({**row, "date": row["biz_date"].isoformat()})
            for row in qs.values("biz_date").annotate(**aggregate_metrics()).order_by("biz_date")
        ]
        result["owner_rows"] = [
            clean_metrics({**row, "owner_name": row["owner__name"]})
            for row in qs.values("owner_id", "owner__name")
            .annotate(**aggregate_metrics())
            .order_by("-order_count", "owner_id")
        ]
        return Response(result)

"""Shared order filtering for list views and owner exports."""

from django.db.models import BigIntegerField, Exists, OuterRef, Q
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from allapp.baseinfo.models import OwnerCustomerBinding
from allapp.products.identifier_lookup import product_search_q
from allapp.tasking.models import WmsTask

from .models import OutboundOrderLine


def filter_orders(qs, params):
    params = dict(params.items())
    for key in ("owner_id", "warehouse_id"):
        if params.get(key):
            params[key] = serializers.IntegerField(min_value=1).run_validation(params[key])
    q = (params.get("search") or "").strip()
    product_q = (params.get("product") or params.get("sku") or "").strip()
    owner_id = params.get("owner_id")
    warehouse_id = params.get("warehouse_id")
    submit_status = params.get("submit_status")
    approval_status = params.get("approval_status")
    outbound_type = params.get("outbound_type")
    delivery_method = params.get("delivery_method")
    source_no = params.get("src_bill_no")
    task_no = params.get("task_no")
    task_status = params.get("task_status")
    waybill_no = params.get("waybill_no")

    def matching_lines(keyword):
        return OutboundOrderLine.objects.filter(
            order_id=OuterRef("pk"),
            is_deleted=False,
        ).filter(product_search_q(keyword, product_field="product_id"))

    if q:
        qs = (
            qs.alias(
                matches_product=Exists(matching_lines(q)),
                matches_customer_code=Exists(
                    OwnerCustomerBinding.objects.filter(
                        owner_id=OuterRef("owner_id"),
                        customer_id=OuterRef("customer_id"),
                        code__icontains=q,
                    )
                ),
            )
            .filter(
                Q(order_no__icontains=q)
                | Q(src_bill_no__icontains=q)
                | Q(contact__icontains=q)
                | Q(contact_phone__icontains=q)
                | Q(customer__name__icontains=q)
                | Q(matches_customer_code=True)
                | Q(supplier__name__icontains=q)
                | Q(matches_product=True)
            )
            .distinct()
        )
    if product_q:
        qs = qs.filter(Exists(matching_lines(product_q)))
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if submit_status:
        qs = qs.filter(submit_status=submit_status)
    if approval_status:
        qs = qs.filter(approval_status=approval_status)
    if outbound_type:
        qs = qs.filter(outbound_type=outbound_type)
    if delivery_method:
        qs = qs.filter(delivery_method=delivery_method)
    if source_no:
        qs = qs.filter(src_bill_no__icontains=source_no)

    date_from = params.get("date_from") or params.get("biz_date_from")
    date_to = params.get("date_to") or params.get("biz_date_to")
    bounds = {}
    for key, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                parsed = parse_date(value)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None:
                raise ValidationError({key: "日期格式必须为有效的 YYYY-MM-DD。"})
            bounds[key] = parsed
    if len(bounds) == 2 and bounds["date_from"] > bounds["date_to"]:
        raise ValidationError("开始日期不能晚于结束日期。")
    if "date_from" in bounds:
        qs = qs.filter(biz_date__gte=bounds["date_from"])
    if "date_to" in bounds:
        qs = qs.filter(biz_date__lte=bounds["date_to"])

    closed = params.get("is_closed")
    if closed is not None:
        if closed.lower() not in {"1", "0", "true", "false"}:
            raise ValidationError({"is_closed": "必须为 true/false 或 1/0。"})
        qs = qs.filter(is_closed=closed.lower() in {"1", "true"})
    overdue = params.get("overdue")
    if overdue is not None:
        if overdue.lower() not in {"1", "0", "true", "false"}:
            raise ValidationError({"overdue": "必须为 true/false 或 1/0。"})
        overdue_q = Q(etd__lt=timezone.now(), is_closed=False) & ~Q(approval_status="CANCELLED")
        qs = qs.filter(overdue_q) if overdue.lower() in {"1", "true"} else qs.exclude(overdue_q)

    if task_no or task_status or waybill_no:
        task_qs = WmsTask.objects.filter(
            source_model__in=("outboundorder", "OutboundOrder"),
        )
        if task_no:
            task_qs = task_qs.filter(task_no__icontains=task_no)
        if task_status:
            task_qs = task_qs.filter(status=task_status)
        if waybill_no:
            task_qs = task_qs.filter(lines__dispatchlineextra__waybill_no__icontains=waybill_no)
        task_order_ids = task_qs.annotate(
            _source_order_id=Cast(
                "source_pk",
                output_field=BigIntegerField(),
            )
        ).values("_source_order_id")
        qs = qs.filter(pk__in=task_order_ids)

    return qs.distinct()

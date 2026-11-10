"""Paginated revenue-assurance evidence, including contract blocker records."""

from decimal import Decimal

from django.db.models import Exists, OuterRef, Sum
from rest_framework.exceptions import NotFound, ValidationError

from allapp.billing.models import Bill, BillingAccrual, BillingEvent, BillingJobRun, BillingPeriod
from allapp.billing.services.integrity import build_close_readiness

from .boss_activity_detail import fields


def assurance_details(access, section, start, end, params):
    try:
        page = max(1, int(params.get("page", 1)))
        size = min(100, max(1, int(params.get("page_size", 20))))
        period_id = int(params.get("period") or 0)
    except (ValueError, TypeError):
        raise ValidationError("分页及账期必须为整数。") from None
    offset = (page - 1) * size
    events = access.apply(BillingEvent.objects.filter(service_date__range=(start, end)))
    accruals = access.apply(BillingAccrual.objects.filter(service_date__range=(start, end)))
    periods = access.apply(BillingPeriod.objects.filter(start_date__lte=end, end_date__gte=start))
    notices = []
    extra = {}

    def render(row):
        return {
            "id": row.pk,
            "title": row.get_charge_type_display(),
            "fields": fields(
                服务日期=row.service_date,
                状态=row.get_pricing_status_display(),
                原因=row.pricing_reason,
            ),
            "target": {"kind": "billing", "id": row.pk},
        }

    if section == "unpriced_events":
        qs = events.filter(pricing_status__in=["PENDING", "UNPRICED"])
    elif section == "missing_accruals":
        qs = events.filter(pricing_status="ACCRUED").exclude(
            billingaccrual__status__in=["OPEN", "LOCKED", "INVOICED"]
        )
        notices.append(
            "本列表列出已有事件但缺应计的记录；服务合同缺失的任务、日指标请进入账期阻断明细查看。"
        )
        extra["related"] = {"label": "查看合同与账期阻断明细", "section": "close_blockers"}
    elif section == "approximate_sources":
        qs = accruals.filter(source_quality="APPROXIMATE").exclude(status="VOID")
        render = accrual_row
    elif section == "billing_job_failures":
        qs = access.apply(
            BillingJobRun.objects.filter(
                service_date__range=(start, end), status__in=["FAILED", "WARNING"]
            )
        )

        def render(row):
            return {
                "id": row.pk,
                "title": row.job_name,
                "fields": fields(日期=row.service_date, 状态=row.status, 说明=row.message),
            }

    elif section == "periods_pending_close":
        qs = periods.filter(status="OPEN", end_date__lt=end)
        render = period_row
    elif section == "late_arriving_charges":
        source = params.get("source", "events")
        if source not in {"events", "accruals"}:
            raise ValidationError("未知迟到费用来源。")
        related_periods = periods.filter(
            owner_id=OuterRef("owner_id"),
            warehouse_id=OuterRef("warehouse_id"),
            start_date__lte=OuterRef("service_date"),
            end_date__gte=OuterRef("service_date"),
        )
        if source == "events":
            qs = events.filter(Exists(related_periods.filter(closed_at__lt=OuterRef("created_at"))))
        else:
            qs = accruals.filter(status="OPEN").filter(
                Exists(related_periods.exclude(status="OPEN"))
            )
            render = accrual_row
        extra["sources"] = [
            {"value": "events", "label": "关账后新增事件"},
            {"value": "accruals", "label": "已关账期未锁定应计"},
        ]
    elif section == "accrual_invoice_variance":
        qs = periods

        def render(period):
            amounts = (
                accruals.filter(period=period)
                .exclude(status="VOID")
                .aggregate(amount=Sum("amount"), tax=Sum("tax_amount"))
            )
            bill = Bill.objects.filter(period=period).exclude(document_status="VOID").first()
            total = (amounts["amount"] or Decimal(0)) + (amounts["tax"] or Decimal(0))
            billed = (bill.subtotal + bill.tax_total) if bill else Decimal(0)
            return {
                "id": period.pk,
                "title": period.label,
                "fields": fields(
                    币种=period.currency, 应计含税=total, 账单含税=billed, 差额=total - billed
                ),
                "target": {"kind": "bill", "id": bill.pk} if bill else None,
            }

    elif section == "close_blockers":
        if period_id:
            period = periods.filter(pk=period_id).first()
            if not period:
                raise NotFound()
            code = params.get("blocker", "")
            readiness = build_close_readiness(
                owner_id=period.owner_id,
                warehouse_id=period.warehouse_id,
                start_date=period.start_date,
                end_date=period.end_date,
                for_invoice=period.status == "CLOSED",
                sample_offset=offset,
                sample_limit=size,
            )
            if code:
                selected = next((b for b in readiness["blockers"] if b["code"] == code), None)
                results = []
                if selected:
                    labels = {
                        "id": "记录编号",
                        "task_no": "任务号",
                        "task_type": "任务类型",
                        "posted_at": "过账时间",
                        "service_date": "服务日期",
                        "charge_type": "费用类型",
                        "calc_method": "计量方式",
                        "metric_type": "日指标",
                        "pricing_status": "计价状态",
                        "pricing_reason": "原因",
                        "amount": "金额",
                        "source_note": "来源说明",
                    }
                    results = [
                        {
                            "id": f"{code}:{offset + i}",
                            "title": selected["detail"],
                            "fields": [
                                {"label": labels.get(k, k), "value": v} for k, v in record.items()
                            ],
                        }
                        for i, record in enumerate(selected["samples"])
                    ]
                return {
                    "results": results,
                    "count": selected["count"] if selected else 0,
                    "page": page,
                    "page_size": size,
                    "notices": notices,
                }
            rows = [
                {
                    "id": b["code"],
                    "title": b["detail"],
                    "fields": fields(记录数=b["count"]),
                    "drill": {"section": section, "period": period_id, "blocker": b["code"]},
                }
                for b in readiness["blockers"]
            ]
            return {
                "results": rows[offset : offset + size],
                "count": len(rows),
                "page": page,
                "page_size": size,
                "notices": notices,
            }
        qs = periods

        def render(row):
            return {**period_row(row), "drill": {"section": section, "period": row.pk}}

        notices.append("先选择账期，再选择阻断类型，可逐页查看全部记录。")
    else:
        raise NotFound("未知收入保障栏目。")
    return {
        "count": qs.count(),
        "page": page,
        "page_size": size,
        "results": [render(row) for row in qs.order_by("id")[offset : offset + size]],
        "notices": notices,
        **extra,
    }


def accrual_row(row):
    return {
        "id": row.pk,
        "title": row.get_charge_type_display(),
        "fields": fields(
            服务日期=row.service_date,
            金额=row.amount,
            币种=row.currency,
            状态=row.get_status_display(),
            说明=row.source_note,
        ),
        "target": {"kind": "accrual", "id": row.pk},
    }


def period_row(row):
    return {
        "id": row.pk,
        "title": row.label,
        "fields": fields(
            开始=row.start_date, 截止=row.end_date, 状态=row.status, 币种=row.currency
        ),
    }

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


CALC_METHODS = {
    "PER_QTY_ABSDEL",
    "PER_QTY_BASE",
    "PER_TASK",
    "PER_LINE",
    "PER_DAY_ONHAND_BASE",
    "PER_ORDER",
    "PER_ORDER_LINE",
    "PER_PARCEL",
    "PER_PALLET_DAY",
    "PER_CBM_DAY",
    "PER_AREA_MONTH",
    "PERCENT_OF_ORDER_AMOUNT",
}


def backfill_integrity(apps, schema_editor):
    Event = apps.get_model("billing", "BillingEvent")
    Accrual = apps.get_model("billing", "BillingAccrual")
    Period = apps.get_model("billing", "BillingPeriod")

    unresolved = []
    for accrual in Accrual.objects.filter(
        status="LOCKED", period_id__isnull=True
    ).iterator():
        candidates = Period.objects.filter(
            owner_id=accrual.owner_id,
            warehouse_id=accrual.warehouse_id,
            currency=accrual.currency,
            status="CLOSED",
            start_date__lte=accrual.service_date,
            end_date__gte=accrual.service_date,
        )
        ids = list(candidates.values_list("id", flat=True)[:2])
        if len(ids) != 1:
            unresolved.append(accrual.id)
            continue
        Accrual.objects.filter(pk=accrual.pk).update(period_id=ids[0])
    if unresolved:
        raise RuntimeError(
            "Cannot safely attach LOCKED accruals to a unique CLOSED period: "
            + ",".join(map(str, unresolved[:100]))
        )

    now = timezone.now()
    for event in Event.objects.all().iterator():
        tokens = set(str(event.event_fp or "").split("|")) & CALC_METHODS
        calc_method = next(iter(tokens)) if len(tokens) == 1 else None
        accrual = (
            Accrual.objects.filter(event_id=event.id)
            .exclude(status="VOID")
            .order_by("-created_at", "-id")
            .first()
        )
        if accrual:
            Event.objects.filter(pk=event.pk).update(
                calc_method=calc_method,
                pricing_rule_id=accrual.rule_id,
                pricing_status="ACCRUED",
                pricing_reason="LEGACY_ACTIVE_ACCRUAL",
                pricing_detail={"migration": "0004", "accrual_id": accrual.id},
                priced_at=event.created_at or now,
            )
        else:
            Event.objects.filter(pk=event.pk).update(
                calc_method=calc_method,
                pricing_status="UNPRICED",
                pricing_reason=(
                    "LEGACY_NO_ACTIVE_ACCRUAL"
                    if calc_method
                    else "LEGACY_CALC_METHOD_UNRESOLVED"
                ),
                pricing_detail={"migration": "0004"},
                priced_at=event.created_at or now,
            )


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_allow_void_bill_reinvoice")]

    operations = [
        migrations.AddField(
            model_name="billingevent",
            name="calc_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PER_TASK", "按任务"),
                    ("PER_LINE", "按任务行"),
                    ("PER_QTY_BASE", "按基础数量"),
                    ("PER_QTY_ABSDEL", "按数量差额绝对值"),
                    ("PER_ORDER", "按订单"),
                    ("PER_ORDER_LINE", "按订单行"),
                    ("PER_PARCEL", "按包裹件数"),
                    ("PER_DAY_ONHAND_BASE", "按日在库(基础数)"),
                    ("PER_PALLET_DAY", "按托盘位/天"),
                    ("PER_CBM_DAY", "按体积CBM/天"),
                    ("PER_AREA_MONTH", "按面积㎡/月"),
                    ("PERCENT_OF_ORDER_AMOUNT", "按订单金额比例"),
                ],
                max_length=40,
                null=True,
                verbose_name="计量方式",
            ),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="bundle_key",
            field=models.CharField(
                blank=True, default="", max_length=40, verbose_name="打包分组键"
            ),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="metric",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="billing_events",
                to="billing.billingmetricdaily",
                verbose_name="来源计费指标",
            ),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="priced_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="计价完成时间"
            ),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="pricing_detail",
            field=models.JSONField(blank=True, default=dict, verbose_name="计价明细"),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="pricing_reason",
            field=models.CharField(
                blank=True, default="", max_length=80, verbose_name="计价原因"
            ),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="pricing_rule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="priced_events",
                to="billing.billingrule",
                verbose_name="实际计价规则",
            ),
        ),
        migrations.AddField(
            model_name="billingevent",
            name="pricing_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "待计价"),
                    ("ACCRUED", "已计费"),
                    ("NO_CHARGE", "无需收费"),
                    ("UNPRICED", "未定价"),
                ],
                db_index=True,
                default="PENDING",
                max_length=20,
                verbose_name="计价状态",
            ),
        ),
        migrations.AddField(
            model_name="billingaccrual",
            name="source_note",
            field=models.CharField(
                blank=True, default="", max_length=200, verbose_name="来源质量说明"
            ),
        ),
        migrations.AddField(
            model_name="billingaccrual",
            name="source_quality",
            field=models.CharField(
                choices=[("VERIFIED", "可信"), ("APPROXIMATE", "近似")],
                db_index=True,
                default="VERIFIED",
                max_length=20,
                verbose_name="来源质量",
            ),
        ),
        migrations.AddField(
            model_name="billingmetricdaily",
            name="source_quality",
            field=models.CharField(
                choices=[("VERIFIED", "可信"), ("APPROXIMATE", "近似")],
                db_index=True,
                default="VERIFIED",
                max_length=20,
                verbose_name="来源质量",
            ),
        ),
        migrations.AlterField(
            model_name="billingjobrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("RUNNING", "运行中"),
                    ("SUCCESS", "成功"),
                    ("FAILED", "失败"),
                    ("WARNING", "有风险"),
                    ("SKIPPED", "跳过"),
                ],
                default="RUNNING",
                max_length=20,
                verbose_name="执行状态",
            ),
        ),
        migrations.RunPython(backfill_integrity, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="billingevent",
            index=models.Index(
                fields=["owner", "warehouse", "service_date", "pricing_status"],
                name="ix_bevt_scope_price",
            ),
        ),
    ]

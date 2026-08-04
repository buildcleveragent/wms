from django.db import migrations, models
from django.db.models import Q


def reject_reason_preflight(apps, schema_editor):
    OutboundOrder = apps.get_model("outbound", "OutboundOrder")
    using = schema_editor.connection.alias
    invalid_ids = list(
        OutboundOrder._base_manager.using(using)
        .filter(approval_status="OWNER_REJECTED")
        .filter(Q(owner_reject_reason="") | Q(owner_reject_reason__isnull=True))
        .order_by("id")
        .values_list("id", flat=True)[:100]
    )
    if invalid_ids:
        raise RuntimeError(
            "无法建立退回原因约束；以下 OWNER_REJECTED 订单缺少原因："
            f"order_ids={invalid_ids}。请先人工补录。"
        )


class Migration(migrations.Migration):
    dependencies = [("outbound", "0005_outbound_order_idempotency")]

    operations = [
        migrations.AddField(
            model_name="outboundorder",
            name="owner_reject_reason",
            field=models.CharField(
                blank=True,
                default="",
                max_length=200,
                verbose_name="货主管理员退回原因",
            ),
        ),
        migrations.RunPython(reject_reason_preflight, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="outboundorder",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(approval_status="OWNER_REJECTED")
                    | ~Q(owner_reject_reason="")
                ),
                name="ox_owner_reject_reason",
            ),
        ),
    ]

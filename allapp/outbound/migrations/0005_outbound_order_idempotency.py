from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower, Trim


def normalize_source_numbers(apps, schema_editor):
    OutboundOrder = apps.get_model("outbound", "OutboundOrder")
    using = schema_editor.connection.alias
    orders = OutboundOrder._base_manager.using(using)

    duplicate_groups = list(
        orders
        .exclude(src_bill_no__isnull=True)
        .annotate(normalized_src=Lower(Trim("src_bill_no")))
        .exclude(normalized_src="")
        .values("owner_id", "normalized_src")
        .annotate(order_count=Count("id"))
        .filter(order_count__gt=1)
        .order_by("owner_id", "normalized_src")
    )
    if duplicate_groups:
        details = []
        for group in duplicate_groups:
            owner_id = group["owner_id"]
            normalized = group["normalized_src"]
            # Keep the comparison in SQL so IDs follow the database collation
            # (including its case/accent rules), exactly like the unique index.
            order_ids = list(
                orders
                .filter(owner_id=owner_id)
                .annotate(normalized_src=Lower(Trim("src_bill_no")))
                .filter(normalized_src=normalized)
                .order_by("id")
                .values_list("id", flat=True)
            )
            details.append(
                f"owner_id={owner_id}, src_bill_no={normalized!r}, order_ids={order_ids}"
            )
        raise RuntimeError(
            "无法建立货主源单号唯一约束；请先人工处理重复订单："
            + "; ".join(details)
        )

    for order_id, src_bill_no in (
        orders
        .exclude(src_bill_no__isnull=True)
        .values_list("id", "src_bill_no")
        .iterator()
    ):
        normalized = (src_bill_no or "").strip() or None
        if normalized != src_bill_no:
            orders.filter(pk=order_id).update(
                src_bill_no=normalized
            )


class Migration(migrations.Migration):
    dependencies = [
        ("outbound", "0004_outboundorder_assisted_history_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboundorder",
            name="idempotency_fingerprint",
            field=models.CharField(
                blank=True, default="", max_length=64, verbose_name="幂等请求指纹"
            ),
        ),
        migrations.AddField(
            model_name="outboundorder",
            name="idempotency_key",
            field=models.CharField(
                blank=True, max_length=64, null=True, verbose_name="幂等键"
            ),
        ),
        migrations.RunPython(normalize_source_numbers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="outboundorder",
            constraint=models.UniqueConstraint(
                fields=("owner", "created_by", "idempotency_key"),
                name="ux_out_owner_actor_idem",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundorder",
            constraint=models.UniqueConstraint(
                fields=("owner", "src_bill_no"),
                name="ux_out_owner_src_bill",
            ),
        ),
    ]

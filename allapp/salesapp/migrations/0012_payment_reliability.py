from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


def prepare_payment_reliability_data(apps, schema_editor):
    Payment = apps.get_model("salesapp", "SaleMiniPayment")
    Refund = apps.get_model("salesapp", "SaleMiniRefund")
    Mapping = apps.get_model("salesapp", "SaleMiniOrderMapping")

    negative_rows = {
        "mapping": list(
            Mapping.objects.filter(payable_amount__lt=0).values_list("id", flat=True)[
                :20
            ]
        ),
        "payment": list(
            Payment.objects.filter(amount__lt=0).values_list("id", flat=True)[:20]
        ),
        "refund": list(
            Refund.objects.filter(amount__lt=0).values_list("id", flat=True)[:20]
        ),
    }
    invalid_amounts = {
        name: ids for name, ids in negative_rows.items() if ids
    }
    if invalid_amounts:
        raise RuntimeError(f"存在负数支付金额，禁止继续迁移：{invalid_amounts}")

    duplicate_transactions = list(
        Payment.objects.exclude(transaction_id__isnull=True)
        .exclude(transaction_id="")
        .values("transaction_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .values_list("transaction_id", flat=True)[:20]
    )
    if duplicate_transactions:
        raise RuntimeError(
            "存在重复微信交易号，禁止继续迁移："
            + ", ".join(duplicate_transactions)
        )

    duplicate_refund_payments = list(
        Refund.objects.values("payment_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .values_list("payment_id", flat=True)[:20]
    )
    if duplicate_refund_payments:
        raise RuntimeError(
            "存在同一支付流水的重复全额退款，禁止继续迁移，payment_id="
            + ", ".join(str(value) for value in duplicate_refund_payments)
        )

    Payment.objects.filter(transaction_id="").update(transaction_id=None)

    now = timezone.now()
    for refund in Refund.objects.filter(idempotency_key__isnull=True).iterator():
        refund.idempotency_key = f"FULL_PAYMENT:{refund.payment_id}"
        if (
            refund.status in {"CREATED", "PROCESSING", "FAILED"}
            and not refund.requires_manual_action
            and refund.next_retry_at is None
        ):
            refund.next_retry_at = now
        refund.save(update_fields=["idempotency_key", "next_retry_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("salesapp", "0011_saleminirefund_idempotency_key_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleminipayment",
            name="last_error",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="saleminipayment",
            name="next_reconcile_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="saleminipayment",
            name="request_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="saleminipayment",
            name="requires_manual_action",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="saleminipayment",
            name="retry_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(
            prepare_payment_reliability_data,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="saleminipayment",
            name="channel",
            field=models.CharField(
                choices=[
                    ("WECHAT_JSAPI", "微信小程序支付"),
                    ("INTERNAL_ZERO", "内部零元结算"),
                ],
                default="WECHAT_JSAPI",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="saleminipayment",
            name="transaction_id",
            field=models.CharField(
                blank=True,
                default=None,
                max_length=128,
                null=True,
                unique=True,
            ),
        ),
        migrations.RemoveIndex(
            model_name="saleminipayment",
            name="salesapp_sa_transac_ae0013_idx",
        ),
        migrations.AddIndex(
            model_name="saleminipayment",
            index=models.Index(
                fields=["status", "next_reconcile_at"],
                name="salesapp_sa_status_2d5c52_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="saleminipayment",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="ck_sale_mini_payment_amount_nonneg",
            ),
        ),
        migrations.AddConstraint(
            model_name="saleminiordermapping",
            constraint=models.CheckConstraint(
                condition=models.Q(payable_amount__gte=0),
                name="ck_sale_mini_mapping_payable_nonneg",
            ),
        ),
        migrations.AddConstraint(
            model_name="saleminirefund",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="ck_sale_mini_refund_amount_nonneg",
            ),
        ),
    ]

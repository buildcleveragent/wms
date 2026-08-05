import django.db.models.deletion
from django.db import migrations, models


def refuse_unresolved_locked_accruals(apps, schema_editor):
    Accrual = apps.get_model("billing", "BillingAccrual")
    unresolved = list(
        Accrual.objects.filter(status="LOCKED", period_id__isnull=True).values_list(
            "id", flat=True
        )[:100]
    )
    if unresolved:
        raise RuntimeError(
            "LOCKED accruals without period remain unresolved: "
            + ",".join(map(str, unresolved))
        )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0004_billing_integrity"),
        ("inventory", "0007_snapshot_approximate_lineage"),
    ]

    operations = [
        migrations.RunPython(
            refuse_unresolved_locked_accruals, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="billingaccrual",
            name="period",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="billing.billingperiod",
                verbose_name="账期",
            ),
        ),
        migrations.AddConstraint(
            model_name="billingaccrual",
            constraint=models.CheckConstraint(
                condition=~models.Q(status="LOCKED") | models.Q(period__isnull=False),
                name="chk_locked_accrual_has_period",
            ),
        ),
    ]

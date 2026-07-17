from django.db import migrations, models


def deduplicate_fact_billing(apps, schema_editor):
    """Retain the newest copy before making the ETL idempotency key unique."""

    FactBilling = apps.get_model("reports", "FactBilling")
    keys = ("owner_id", "warehouse_id", "date_id", "fee_type", "dedup_key")
    duplicate_groups = (
        FactBilling.objects.values(*keys)
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
    )
    for group in duplicate_groups.iterator():
        filters = {key: group[key] for key in keys}
        rows = FactBilling.objects.filter(**filters).order_by("-id")
        keeper = rows.first()
        if keeper is not None:
            rows.exclude(pk=keeper.pk).delete()


class Migration(migrations.Migration):
    dependencies = [("reports", "0004_billing_fact_reversals")]

    operations = [
        migrations.RunPython(deduplicate_fact_billing, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="factbilling",
            constraint=models.UniqueConstraint(
                fields=("owner", "warehouse", "date", "fee_type", "dedup_key"),
                name="uq_factbilling_dedup",
            ),
        ),
    ]

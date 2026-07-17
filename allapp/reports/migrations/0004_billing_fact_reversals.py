from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0003_reporting_governance_fields")]

    operations = [
        migrations.RemoveConstraint(
            model_name="factbilling",
            name="chk_fee_amt_ge0",
        ),
        migrations.AlterField(
            model_name="factbilling",
            name="amount",
            field=models.DecimalField(decimal_places=2, max_digits=18),
        ),
        migrations.AlterField(
            model_name="factbilling",
            name="dedup_key",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AlterField(
            model_name="aggbillingdaily",
            name="amount",
            field=models.DecimalField(decimal_places=2, max_digits=18),
        ),
    ]

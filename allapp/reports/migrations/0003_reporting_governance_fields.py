from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0002_etljobrun_operations_permissions")]

    operations = [
        migrations.AlterField(
            model_name="warehousedim",
            name="owner_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="etljobrun",
            name="watermark",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="etljobrun",
            name="reconciliation",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

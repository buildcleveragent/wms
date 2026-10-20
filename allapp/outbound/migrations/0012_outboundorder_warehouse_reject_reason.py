from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("outbound", "0011_outboundorderline_is_priced")]

    operations = [
        migrations.AddField(
            model_name="outboundorder",
            name="warehouse_reject_reason",
            field=models.CharField("仓库退回原因", max_length=500, blank=True, default=""),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inbound", "0009_product_identity")]
    operations = [
        migrations.AddField(
            model_name="customerreturnreceipt",
            name="posting_hold_reason",
            field=models.CharField(blank=True, default="", max_length=240),
        )
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("outbound", "0010_owner_product_mapping_chinese_labels")]

    operations = [
        migrations.AddField(
            model_name="outboundorderline",
            name="is_priced",
            field=models.BooleanField(default=True, verbose_name="已计价"),
        ),
    ]

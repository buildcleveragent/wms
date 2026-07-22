from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("salesapp", "0008_alter_minicustomeraddress_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleproductconfig",
            name="enable_qty_rules",
            field=models.BooleanField(default=False, verbose_name="启用起购及递增限制"),
        ),
    ]

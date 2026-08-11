import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("locations", "0004_alter_location_zone_type")]

    operations = [
        migrations.AddField(
            model_name="warehouse",
            name="default_receive_location",
            field=models.ForeignKey(
                blank=True,
                help_text="商品批量导入自动收货使用的库位，必须属于本仓库且处于可用状态。",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="locations.location",
                verbose_name="默认收货库位",
            ),
        )
    ]

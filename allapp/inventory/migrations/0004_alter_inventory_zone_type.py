from django.db import migrations, models


ZONE_CHOICES = [
    (1, "拣选区"), (2, "存储区"), (3, "收货区"), (4, "发运区"),
    (5, "退货区"), (6, "整件区"), (7, "拆零区"), (8, "破损区"), (9, "其他"),
]


class Migration(migrations.Migration):
    dependencies = [("inventory", "0003_operations_report_indexes")]

    operations = [
        migrations.AlterField(
            model_name="inventorydetail",
            name="zone_type",
            field=models.PositiveSmallIntegerField(
                choices=ZONE_CHOICES, db_index=True, default=2, verbose_name="区域类型"
            ),
        ),
        migrations.AlterField(
            model_name="inventorytransaction",
            name="zone_type",
            field=models.PositiveSmallIntegerField(
                choices=ZONE_CHOICES, db_index=True, default=2, verbose_name="区域类型"
            ),
        ),
    ]

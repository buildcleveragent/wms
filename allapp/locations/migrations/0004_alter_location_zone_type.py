from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("locations", "0003_clarify_warehouse_subwarehouse_scope")]

    operations = [
        migrations.AlterField(
            model_name="location",
            name="zone_type",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, "拣选区"), (2, "存储区"), (3, "收货区"),
                    (4, "发运区"), (5, "退货区"), (6, "整件区"),
                    (7, "拆零区"), (8, "破损区"), (9, "其他"),
                ],
                db_index=True,
                default=2,
                verbose_name="区域类型",
            ),
        )
    ]

import django.core.validators
from django.db import migrations, models


def initialize_next_sku_sequence(apps, schema_editor):
    Owner = apps.get_model("baseinfo", "Owner")
    Product = apps.get_model("products", "Product")

    for owner in Owner._base_manager.only("id").iterator():
        historical_count = Product._base_manager.filter(owner_id=owner.pk).count()
        Owner._base_manager.filter(pk=owner.pk).update(
            next_sku_sequence=historical_count + 1
        )


class Migration(migrations.Migration):

    dependencies = [
        ("baseinfo", "0003_owner_allow_warehouse_assisted_outbound"),
        ("products", "0002_add_cross_owner_product_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="owner",
            name="next_sku_sequence",
            field=models.PositiveBigIntegerField(
                default=1,
                help_text="新建商品时使用该序号生成SKU，成功创建后自动加1。",
                validators=[django.core.validators.MinValueValidator(1)],
                verbose_name="下一个SKU序号",
            ),
        ),
        migrations.RunPython(
            initialize_next_sku_sequence,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

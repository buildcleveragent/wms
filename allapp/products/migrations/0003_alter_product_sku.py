from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("baseinfo", "0004_owner_next_sku_sequence"),
        ("products", "0002_add_cross_owner_product_permissions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="sku",
            field=models.CharField(
                blank=True,
                help_text="系统按“货主编码-序号”自动生成，货主内唯一",
                max_length=50,
                verbose_name="SKU编码",
            ),
        ),
    ]

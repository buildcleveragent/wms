from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0007_package_identifier_registry"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="carton_package",
            field=models.ForeignKey(
                blank=True,
                help_text="先创建商品包装层级，再将箱码与该商品的有效包装层级一次性绑定。",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="carton_barcode_products",
                to="products.productpackage",
                verbose_name="箱码对应包装层级",
            ),
        ),
    ]

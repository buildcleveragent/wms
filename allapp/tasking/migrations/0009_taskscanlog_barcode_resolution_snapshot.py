from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0008_product_carton_package"),
        ("tasking", "0008_reloclineextra_from_container_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskscanlog",
            name="matched_fields",
            field=models.JSONField(blank=True, default=list, verbose_name="命中标识字段"),
        ),
        migrations.AddField(
            model_name="taskscanlog",
            name="product_package",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="products.productpackage",
                verbose_name="解析包装层级",
            ),
        ),
        migrations.AddField(
            model_name="taskscanlog",
            name="uom_name",
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name="解析单位名称"),
        ),
    ]

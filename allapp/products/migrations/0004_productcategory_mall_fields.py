from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0003_alter_product_sku"),
    ]

    operations = [
        migrations.AddField(
            model_name="productcategory",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text="主要用于商城顶部大类图标；未上传时显示分类名称首字。",
                null=True,
                upload_to="product_categories/",
                verbose_name="分类图片",
            ),
        ),
        migrations.AddField(
            model_name="productcategory",
            name="sort_order",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                verbose_name="商城排序",
            ),
        ),
        migrations.AlterModelOptions(
            name="productcategory",
            options={
                "ordering": ["sort_order", "code"],
                "verbose_name": "商品分类",
                "verbose_name_plural": "商品分类",
            },
        ),
    ]

from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0012_gs1_lookup_cache_and_sku_format")]

    operations = [
        migrations.AlterField(
            model_name="productcategory",
            name="code",
            field=models.CharField(
                help_text="分类唯一编码", max_length=320, verbose_name="分类编码"
            ),
        ),
        migrations.AlterField(
            model_name="productuom",
            name="code",
            field=models.CharField(
                help_text="EA/PCS/CTN/PLT/KG/L 等",
                max_length=320,
                validators=[
                    RegexValidator(
                        "^[A-Za-z0-9_\\-\\*]+$",
                        "仅允许字母、数字、下划线、连字符、星号",
                    )
                ],
                verbose_name="单位编码",
            ),
        ),
    ]

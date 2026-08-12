from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0013_expand_category_uom_codes")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="purchase_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=None,
                max_digits=18,
                null=True,
                verbose_name="进价",
            ),
        ),
    ]

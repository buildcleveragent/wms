from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "products",
            "0010_remove_productbarcode_uniq_primary_product_barcode_scope_and_more",
        ),
    ]

    operations = [
        migrations.AddIndex(
            model_name="productidentifierregistry",
            index=models.Index(fields=["normalized_value"], name="prod_ident_norm_idx"),
        ),
        migrations.AddIndex(
            model_name="productbarcode",
            index=models.Index(fields=["normalized_value"], name="prod_barcode_norm_idx"),
        ),
        migrations.AddIndex(
            model_name="productexternalidentifier",
            index=models.Index(fields=["normalized_value"], name="prod_extident_norm_idx"),
        ),
    ]

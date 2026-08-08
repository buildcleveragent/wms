from collections import defaultdict

import django.db.models.deletion
from django.db import migrations, models


IDENTIFIER_FIELDS = (
    "code",
    "sku",
    "gtin",
    "unit_barcode",
    "carton_barcode",
    "external_code",
)


def _normalize(value):
    return str(value).strip().upper() if value not in (None, "") else ""


def backfill_identifier_registry(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    Registry = apps.get_model("products", "ProductIdentifierRegistry")

    claims = defaultdict(lambda: defaultdict(list))
    products = Product._base_manager.all().values(
        "id", "owner_id", *IDENTIFIER_FIELDS
    )
    for product in products.iterator(chunk_size=1000):
        for field in IDENTIFIER_FIELDS:
            value = _normalize(product[field])
            if value:
                claims[(product["owner_id"], value)][product["id"]].append(field)

    conflicts = []
    for (owner_id, value), product_claims in claims.items():
        if len(product_claims) > 1:
            detail = ", ".join(
                f"商品#{product_id}({','.join(fields)})"
                for product_id, fields in sorted(product_claims.items())
            )
            conflicts.append(f"货主#{owner_id} 标识“{value}”：{detail}")
    if conflicts:
        preview = "；".join(conflicts[:20])
        remainder = len(conflicts) - 20
        if remainder > 0:
            preview += f"；另有 {remainder} 项冲突"
        raise RuntimeError(
            "商品标识注册表回填失败，存在跨商品标识冲突：" + preview
        )

    rows = [
        Registry(
            owner_id=owner_id,
            product_id=next(iter(product_claims)),
            normalized_value=value,
        )
        for (owner_id, value), product_claims in claims.items()
    ]
    Registry.objects.bulk_create(rows, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [("products", "0005_alter_product_code_alter_product_external_code_and_more")]

    operations = [
        migrations.CreateModel(
            name="ProductIdentifierRegistry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "normalized_value",
                    models.CharField(max_length=50, verbose_name="标准化标识值"),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_identifier_registry",
                        to="baseinfo.owner",
                        verbose_name="货主",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identifier_registry",
                        to="products.product",
                        verbose_name="商品",
                    ),
                ),
            ],
            options={
                "verbose_name": "商品标识注册项",
                "verbose_name_plural": "商品标识注册项",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "normalized_value"),
                        name="uniq_owner_product_identifier",
                    )
                ],
            },
        ),
        migrations.RunPython(
            backfill_identifier_registry,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

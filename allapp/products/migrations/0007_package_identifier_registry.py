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


def backfill_package_identifiers(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    ProductPackage = apps.get_model("products", "ProductPackage")
    Registry = apps.get_model("products", "ProductIdentifierRegistry")

    claims = defaultdict(list)
    products = Product._base_manager.all().values(
        "id", "owner_id", *IDENTIFIER_FIELDS
    )
    for product in products.iterator(chunk_size=1000):
        seen_values = set()
        for field in IDENTIFIER_FIELDS:
            value = _normalize(product[field])
            if value and value not in seen_values:
                claims[(product["owner_id"], value)].append(
                    {
                        "kind": "product",
                        "product_id": product["id"],
                        "source": field,
                    }
                )
                seen_values.add(value)

    packages = ProductPackage._base_manager.all().values(
        "id", "product_id", "product__owner_id", "barcode", "is_deleted"
    )
    package_rows = []
    for package in packages.iterator(chunk_size=1000):
        value = _normalize(package["barcode"])
        if not value:
            continue
        claim = {
            "kind": "package",
            "product_id": package["product_id"],
            "package_id": package["id"],
            "source": "barcode",
            "is_deleted": package["is_deleted"],
        }
        claims[(package["product__owner_id"], value)].append(claim)
        package_rows.append((package, value))

    conflicts = []
    for (owner_id, value), value_claims in claims.items():
        package_claims = [claim for claim in value_claims if claim["kind"] == "package"]
        if not package_claims:
            continue
        if len(value_claims) == 1:
            continue
        details = []
        for claim in value_claims:
            if claim["kind"] == "package":
                deleted = ",已软删除" if claim["is_deleted"] else ""
                details.append(
                    f"包装#{claim['package_id']}(商品#{claim['product_id']},barcode{deleted})"
                )
            else:
                details.append(
                    f"商品#{claim['product_id']}({claim['source']})"
                )
        conflicts.append(
            f"货主#{owner_id} 标识“{value}”：{', '.join(details)}"
        )

    if conflicts:
        preview = "；".join(conflicts[:20])
        remainder = len(conflicts) - 20
        if remainder > 0:
            preview += f"；另有 {remainder} 项冲突"
        raise RuntimeError(
            "包装条码注册表回填失败，存在货主级标识冲突：" + preview
        )

    Registry.objects.bulk_create(
        [
            Registry(
                owner_id=package["product__owner_id"],
                product_id=package["product_id"],
                product_package_id=package["id"],
                normalized_value=value,
            )
            for package, value in package_rows
        ],
        batch_size=1000,
    )


def remove_package_identifiers(apps, schema_editor):
    Registry = apps.get_model("products", "ProductIdentifierRegistry")
    Registry.objects.filter(product_package__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [("products", "0006_productidentifierregistry")]

    operations = [
        migrations.AddField(
            model_name="productidentifierregistry",
            name="product_package",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="identifier_registry_entry",
                to="products.productpackage",
                verbose_name="商品包装层级",
            ),
        ),
        migrations.RunPython(
            backfill_package_identifiers,
            reverse_code=remove_package_identifiers,
        ),
    ]

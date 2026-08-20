from django.core.management.base import BaseCommand, CommandError

from allapp.products.models import (
    Product,
    ProductBarcode,
    ProductExternalIdentifier,
    ProductIdentifierRegistry,
    normalize_product_identifier,
)


class Command(BaseCommand):
    help = "审计商品稳定标识、主值投影和统一注册表的一致性。"

    def handle(self, *args, **options):
        errors = []
        for product in Product.all_objects.all().iterator():
            expected = {
                normalize_product_identifier(product.code),
                normalize_product_identifier(product.sku),
                *ProductBarcode.all_objects.filter(product=product).values_list(
                    "normalized_value", flat=True
                ),
                *ProductExternalIdentifier.all_objects.filter(product=product).values_list(
                    "normalized_value", flat=True
                ),
            } - {""}
            registered = set(
                ProductIdentifierRegistry.objects.filter(product=product).values_list(
                    "normalized_value", flat=True
                )
            )
            if expected != registered:
                missing = sorted(expected - registered)
                extra = sorted(registered - expected)
                errors.append(
                    f"商品 {product.pk}/{product.code}: " f"注册表缺少={missing} 多余={extra}"
                )
            projections = {
                "gtin": ("GTIN", product.gtin, None),
                "unit_barcode": ("UNIT", product.unit_barcode, None),
                "carton_barcode": (
                    "CARTON",
                    product.carton_barcode,
                    product.carton_package_id,
                ),
            }
            for field, (barcode_type, value, package_id) in projections.items():
                if (
                    value
                    and not ProductBarcode.all_objects.filter(
                        product=product,
                        barcode_type=barcode_type,
                        package_id=package_id,
                        normalized_value=normalize_product_identifier(value),
                        is_primary=True,
                    ).exists()
                ):
                    errors.append(
                        f"商品 {product.pk}/{product.code}: {field} 主值投影无对应主条码记录"
                    )
            if (
                product.external_code
                and not ProductExternalIdentifier.all_objects.filter(
                    product=product,
                    source_system="LEGACY",
                    normalized_value=normalize_product_identifier(product.external_code),
                    is_primary=True,
                ).exists()
            ):
                errors.append(
                    f"商品 {product.pk}/{product.code}: external_code 投影无 LEGACY 主记录"
                )
        if errors:
            raise CommandError("商品标识审计失败：\n" + "\n".join(errors[:100]))
        self.stdout.write(self.style.SUCCESS("商品标识历史及注册表审计通过。"))
